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
