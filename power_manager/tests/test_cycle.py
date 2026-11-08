import importlib.util
from pathlib import Path
from unittest.mock import Mock
import pytest

spec=importlib.util.spec_from_file_location('cycle',Path(__file__).resolve().parents[2]/'scripts/power_manager_cycle.py')
cycle=importlib.util.module_from_spec(spec);spec.loader.exec_module(cycle)


def test_partial_down_failure_still_recovers_and_reports_incident(monkeypatch):
    controller=Mock()
    controller.state.side_effect=[{'state':{'state':'ON'},'execution_enabled':True},{'state':{'state':'RECOVERY_REQUIRED'}},
        {'state':{'state':'ON'}},{'state':{'state':'ON'}}]
    controller.execute.side_effect=[RuntimeError('partial failure'),None]
    monkeypatch.setattr(cycle,'portal_alive',lambda:None)
    with pytest.raises(RuntimeError,match='partial failure'):cycle.cycle(controller)
    assert [c.args for c in controller.execute.call_args_list]==[('DOWN',),('UP',)]


def test_normal_cycle_restores_before_success(monkeypatch):
    controller=Mock()
    controller.state.side_effect=[{'state':{'state':'ON'},'execution_enabled':True},{'state':{'state':'OFF'}},
        {'state':{'state':'OFF'}},{'state':{'state':'ON'}},{'state':{'state':'ON'}}]
    monkeypatch.setattr(cycle,'portal_alive',lambda:None)
    cycle.cycle(controller)
    assert [c.args for c in controller.execute.call_args_list]==[('DOWN',),('UP',)]


def test_ssm_guard_filters_active_status_on_service_instead_of_scanning_history():
    controller=cycle.Controller.__new__(cycle.Controller);seen=[]
    class Pages:
        def paginate(self,**kwargs):
            seen.append(kwargs)
            return [{'Commands':[]}]
    class SSM:
        def get_paginator(self,name):assert name=='list_commands';return Pages()
    controller.ssm=SSM();controller.no_external_commands()
    assert seen==[{'Filters':[{'key':key,'value':value}]} for key,value in (('ExecutionStage','Executing'),('Status','Pending'))]


def test_active_external_command_blocks_before_any_power_request():
    controller=cycle.Controller.__new__(cycle.Controller)
    paginator=Mock();paginator.paginate.return_value=[{'Commands':[{'DocumentName':'deployment','CommandId':'active'}]}]
    controller.ssm=Mock();controller.ssm.get_paginator.return_value=paginator
    with pytest.raises(RuntimeError,match='Active SSM automation'):controller.no_external_commands()


def test_known_scheduler_is_deferred_to_backend_pause_and_drain():
    controller=cycle.Controller.__new__(cycle.Controller)
    commands=[{'DocumentName':'CosmoTech-Execute-Promotion-Stage','CommandId':'tick','Parameters':{'Stage':['tick']},'InstanceIds':['i-0def4e806c49cf60e']},
        {'DocumentName':'CosmoTech-Promotion-Argo-State','CommandId':'read'}]
    paginator=Mock();paginator.paginate.return_value=[{'Commands':commands}]
    controller.ssm=Mock();controller.ssm.get_paginator.return_value=paginator
    controller.no_external_commands()
