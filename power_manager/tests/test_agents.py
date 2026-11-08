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
