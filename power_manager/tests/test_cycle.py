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
