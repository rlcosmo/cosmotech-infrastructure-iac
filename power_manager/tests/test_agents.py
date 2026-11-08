import importlib.util
from pathlib import Path
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[2]
def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'ops/power-manager'/f'{name}.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def test_load_balancer_controller_is_gone_before_producer_quiesce():
    agent=module('agent').Agent.__new__(module('agent').Agent)
    events=[]
    agent.scale=lambda r,n,**kw:events.append(('scale',r['name'],n))
    agent.wait_workload=lambda r,n,**kw:events.append(('terminated',r['name'],n))
    baseline={'blockers':[],'controllers':[], 'workloads':[
        {'namespace':'kube-system','kind':'deployments','name':'aws-load-balancer-controller'},
        {'namespace':'kube-system','kind':'deployments','name':'cluster-autoscaler'},
        {'namespace':'orders-api','kind':'deployments','name':'orders-api'}]}
    agent.prepare(baseline)
    assert events.index(('terminated','aws-load-balancer-controller',0))<events.index(('scale','orders-api',0))


def test_host_restore_starts_only_original_running_container(monkeypatch):
    mod=module('host_agent');calls=[]
    old={'host':'host','units':{},'containers':[
        {'id':'a'*64,'name':'/database','image':'image1','running':True,'mounts':[]},
        {'id':'b'*64,'name':'/restore-test','image':'image2','running':False,'mounts':[]}]}
    stopped={'host':'host','units':{},'containers':[{**r,'running':False} for r in old['containers']]}
    samples=iter([stopped,old])
    monkeypatch.setattr(mod,'collect',lambda _:next(samples))
    monkeypatch.setattr(mod,'command',lambda args,**kw:calls.append(args))
    assert mod.restore(old,'host')['complete']
    assert calls==[['docker','start','a'*64]]


def test_host_restore_refuses_missing_or_replaced_image(monkeypatch):
    import pytest
    mod=module('host_agent')
    baseline={'units':{},'containers':[{'id':'a'*64,'name':'/database','image':'original','running':True,'mounts':[]}]}
    monkeypatch.setattr(mod,'collect',lambda _:{'units':{},'containers':[{'id':'a'*64,'image':'different','running':False}]})
    command=Mock();monkeypatch.setattr(mod,'command',command)
    with pytest.raises(ValueError,match='drift'):mod.restore(baseline,'host')
    command.assert_not_called()


def test_host_restore_refreshes_containers_after_starting_docker(monkeypatch):
    mod=module('host_agent');calls=[]
    original={'units':{'docker':True},'containers':[{'id':'a'*64,'name':'/database','image':'image','running':True,'mounts':[]}]}
    unavailable={'units':{},'containers':[]}
    stopped={'units':{'docker':True},'containers':[{**original['containers'][0],'running':False}]}
    samples=iter([unavailable,stopped,original])
    monkeypatch.setattr(mod,'collect',lambda _:next(samples))
    monkeypatch.setattr(mod,'command',lambda args,**kw:calls.append(args))
    assert mod.restore(original,'host')['complete']
    assert calls==[['systemctl','start','docker'],['docker','start','a'*64]]


def test_argo_restore_accepts_unchanged_automatic_application():
    mod=module('agent')
    app={'name':'orders-api-dev','spec_hash':'automatic-enabled','revision':'a'*40,'remote_revision':'a'*40}
    mod.Agent.check_argo_restore({'argo':[app]},{'argo':[dict(app)]})


def test_argo_restore_blocks_new_git_revision_and_application():
    import pytest
    mod=module('agent')
    app={'name':'orders-api-dev','spec_hash':'automatic-enabled','revision':'a'*40,'remote_revision':'a'*40}
    with pytest.raises(ValueError,match='keep Argo paused'):
        mod.Agent.check_argo_restore({'argo':[app]},{'argo':[{**app,'remote_revision':'b'*40}]})
    with pytest.raises(ValueError,match='inventory drift'):
        mod.Agent.check_argo_restore({'argo':[app]},{'argo':[app,{**app,'name':'new'}]})


def test_prepare_waits_for_local_argo_before_workload_mutation():
    agent=module('agent').Agent.__new__(module('agent').Agent);events=[]
    agent.scale=lambda r,n,**kw:events.append(('scale',r['name'],kw.get('local',False)))
    agent.wait_workload=lambda r,n,**kw:events.append(('wait',r['name'],kw.get('local',False)))
    agent.prepare({'blockers':[],'controllers':[{'name':'argocd-application-controller'}],
        'workloads':[{'name':'orders-api','namespace':'orders-api'}]})
    assert events==[('scale','argocd-application-controller',True),('wait','argocd-application-controller',True),('scale','orders-api',False)]


def test_git_revision_rejects_untrusted_repository_before_running_commands():
    import pytest
    agent=module('agent').Agent.__new__(module('agent').Agent)
    with pytest.raises(ValueError,match='Unreviewed GitOps repository'):
        agent.git_revision({'repoURL':'ext::arbitrary-command','targetRevision':'main'})


def test_verify_rejects_unrestored_replica_count_even_when_zero_is_healthy():
    agent=module('agent').Agent.__new__(module('agent').Agent)
    baseline={'nodes':[{'ready':True}],'workloads':[{'namespace':'orders-api','kind':'deployments','name':'orders-api','template_hash':'same','replicas':3,'ready':3}],
        'daemonsets':[],'argo':[],'pvs':[],'endpoints':{}}
    current={**baseline,'observed_at':1,'blockers':[],'workloads':[{**baseline['workloads'][0],'replicas':0,'ready':0}]}
    agent.collect=lambda:current
    result=agent.verify_once(baseline)
    assert result['complete'] is False
    assert result['differences']==['Workload não recuperado: orders-api/orders-api']


def test_passive_recovery_claim_is_preserved_without_authorizing_workloads():
    agent=module('agent').Agent
    claim={'spec':{'volumeName':'recovery'},'status':{'phase':'Bound'}}
    volume={'spec':{'persistentVolumeReclaimPolicy':'Retain'}}
    assert agent.passive_retained_claim(claim,{'recovery':volume})
    assert not agent.passive_retained_claim(claim,{})
    assert not agent.passive_retained_claim(claim,{'recovery':{'spec':{'persistentVolumeReclaimPolicy':'Delete'}}})
    assert not agent.passive_retained_claim(claim,{'recovery':{'spec':{'persistentVolumeReclaimPolicy':'Retain','hostPath':{}}}})


def test_collect_pvs_are_not_overwritten_by_pod_template_volumes(monkeypatch):
    mod=module('agent');agent=mod.Agent.__new__(mod.Agent)
    monkeypatch.setattr(mod,'NAMESPACES',('kube-system',));monkeypatch.setattr(mod,'ENDPOINTS',{})
    pv={'metadata':{'name':'durable'},'spec':{'persistentVolumeReclaimPolicy':'Retain','csi':{'volumeHandle':'vol-original'}}}
    deployment={'metadata':{'name':'coredns'},'spec':{'replicas':1,'template':{'spec':{'volumes':[{'name':'scratch','emptyDir':{}}]}}},'status':{'readyReplicas':1}}
    def kube(args,**kw):
        resource=args[args.index('get')+1]
        if '--all-namespaces' in args:return {'items':[]}
        if resource=='pv':return {'items':[pv]}
        if resource=='nodes':return {'items':[{'metadata':{'name':'node'},'status':{'conditions':[{'type':'Ready','status':'True'}]}}]}
        if resource=='deployments' and not kw.get('local'):return {'items':[deployment]}
        return {'items':[]}
    agent.kube=kube
    snapshot=agent.collect()
    assert snapshot['pvs']==[{'name':'durable','spec':pv['spec'],'phase':None}]
    assert snapshot['blockers']==[]


def test_host_mount_comparison_ignores_order_but_detects_storage_changes():
    import copy
    mod=module('host_agent')
    mounts=[{'Type':'volume','Source':'/data/db','Destination':'/var/lib/db','RW':True},
        {'Type':'bind','Source':'/config','Destination':'/etc/app','RW':False}]
    baseline={'units':{},'containers':[{'id':'id','name':'database','running':True,'image':'same','mounts':mounts}]}
    current=copy.deepcopy(baseline);current['containers'][0]['mounts'].reverse()
    assert mod.compare(baseline,current)==[]
    current['containers'][0]['mounts'][0]['Source']='/different-volume'
    assert mod.compare(baseline,current)==['Mounts divergentes: database']


def test_scheduler_pause_preserves_snapshot_and_drains_without_killing_jobs(monkeypatch):
    mod=module('host_agent');old={**mod.FLOW,'disabled':False,'spec_hash':'same'}
    samples=iter([old,{**old,'disabled':True}])
    monkeypatch.setattr(mod,'automation_state',lambda:next(samples))
    request=Mock();monkeypatch.setattr(mod,'kestra_request',request)
    monkeypatch.setattr(mod,'command',lambda args: '{"Commands":[]}')
    monkeypatch.setattr(mod.time,'sleep',lambda _:None)
    assert mod.automation({'automation':old},mod.ITSM)['drained']
    assert old['disabled'] is False
    request.assert_called_once_with('/api/v1/main/flows/disable/by-ids',method='POST',payload=[mod.FLOW])


def test_scheduler_resume_retains_original_disabled_state(monkeypatch):
    mod=module('host_agent');old={**mod.FLOW,'disabled':True,'spec_hash':'same'}
    monkeypatch.setattr(mod,'automation_state',lambda:dict(old));request=Mock();monkeypatch.setattr(mod,'kestra_request',request)
    assert mod.automation({'automation':old},mod.ITSM,resume=True)['scheduler_disabled'] is True
    request.assert_not_called()


def test_scheduler_drift_blocks_without_overwriting_configuration(monkeypatch):
    import pytest
    mod=module('host_agent');old={**mod.FLOW,'disabled':False,'spec_hash':'old'}
    monkeypatch.setattr(mod,'automation_state',lambda:{**old,'spec_hash':'new'})
    request=Mock();monkeypatch.setattr(mod,'kestra_request',request)
    with pytest.raises(ValueError,match='configuration drift'):mod.automation({'automation':old},mod.ITSM,resume=True)
    request.assert_not_called()


def test_ephemeral_kestra_runner_is_not_restarted_as_a_service():
    mod=module('host_agent');old={'units':{},'containers':[{'id':'job','name':'finished-task','running':True,'ephemeral':True}]}
    assert mod.compare(old,{'units':{},'containers':[]})==[]


def test_scheduler_hash_ignores_audit_timestamp_but_keeps_tasks():
    mod=module('host_agent')
    old={**mod.FLOW,'revision':1,'updated':'old','disabled':False,'tasks':[{'id':'reconcile'}]}
    current={**old,'revision':2,'updated':'new','disabled':True}
    assert mod.flow_hash(old)==mod.flow_hash(current)
    assert mod.flow_hash(old,legacy=True)!=mod.flow_hash(current,legacy=True)
    assert mod.flow_hash(old)!=mod.flow_hash({**current,'tasks':[{'id':'changed'}]})


def test_legacy_scheduler_snapshot_requires_matching_historical_revision(monkeypatch):
    import copy,pytest
    mod=module('host_agent')
    old={**mod.FLOW,'revision':1,'updated':'old','disabled':False,'tasks':[{'id':'reconcile'}]}
    current={**old,'revision':2,'updated':'new','disabled':True}
    baseline={'host':mod.ITSM,'automation':{**mod.FLOW,'disabled':False,'spec_hash':mod.flow_hash(old,legacy=True)}}
    saved=copy.deepcopy(baseline)
    monkeypatch.setattr(mod,'kestra_request',lambda path:old if path.endswith('?revision=1') else current)
    upgraded=mod.normalize_legacy_snapshot(baseline,mod.ITSM)
    assert baseline==saved
    assert upgraded['automation']['spec_hash']==mod.flow_hash(old)
    assert upgraded['automation']['disabled'] is False
    monkeypatch.setattr(mod,'kestra_request',lambda path:current)
    with pytest.raises(ValueError,match='not proven'):mod.normalize_legacy_snapshot(baseline,mod.ITSM)


def test_itsm_restore_recovers_containers_before_polling_warming_api(monkeypatch):
    import urllib.error
    mod=module('host_agent');events=[]
    original={'units':{},'containers':[{'id':'a'*64,'name':'/kestra','image':'same','running':True,'mounts':[]}]}
    stopped={'units':{},'containers':[{**original['containers'][0],'running':False}]}
    count=0
    def collect(host,**kwargs):
        nonlocal count
        if kwargs.get('include_automation') is False:
            events.append('process-state');return stopped
        count+=1;events.append('api-state')
        if count==1:raise urllib.error.HTTPError('https://kestra.example',502,'warming',{},None)
        return original
    monkeypatch.setattr(mod,'collect',collect)
    monkeypatch.setattr(mod,'command',lambda args,**kw:events.append(args))
    monkeypatch.setattr(mod.time,'sleep',lambda _:None)
    assert mod.restore(original,mod.ITSM)['complete']
    assert events[:3]==['process-state',['docker','start','a'*64],'api-state']
    assert count==2
