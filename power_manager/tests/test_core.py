import copy
import hashlib
import hmac
from unittest.mock import patch
import pytest
from power_manager.core import plan, actions, assert_action
from power_manager.api import authenticate


def fixture():
    return {'key': 'snapshots/1.json', 'targets': [
        {'id': 'nodes', 'type': 'MNG', 'capacity': {'minSize': 1, 'maxSize': 3, 'desiredSize': 3}},
        {'id': 'control', 'type': 'EC2', 'state': 'running', 'control_host': True},
        {'id': 'app', 'type': 'EC2', 'state': 'running'},
        {'id': 'already-stopped', 'type': 'EC2', 'state': 'stopped'}]}


def test_both_directions_and_exact_restore():
    snapshot = fixture()
    before = copy.deepcopy(snapshot)
    down, up = actions(snapshot, 'DOWN'), actions(snapshot, 'UP')
    assert down[0]['kind'] == 'quiesce'
    assert down[1]['capacity'] == {'minSize': 0, 'maxSize': 3, 'desiredSize': 0}
    assert [a['id'] for a in down if a['kind'] == 'stop'] == ['app', 'control']
    assert [a['id'] for a in up if a['kind'] == 'start'] == ['control', 'app']
    assert next(a['capacity'] for a in up if a['kind'] == 'mng') == snapshot['targets'][0]['capacity']
    assert up[-1]['kind'] == 'verify_on'
    assert snapshot == before
    for a in down + up:
        assert_action(a, {'nodes', 'app', 'control'})


@pytest.mark.parametrize('kind', ['delete_load_balancer', 'release_address', 'modify_route', 'destroy', 'delete_volume'])
def test_network_and_data_actions_impossible(kind):
    with pytest.raises(ValueError):
        assert_action({'kind': kind}, set())


def test_unknown_resource_rejected():
    with pytest.raises(ValueError):
        assert_action({'kind': 'stop', 'id': 'other-account-instance'}, {'app'})


def test_mng_zero_max_rejected():
    with pytest.raises(ValueError):
        assert_action({'kind': 'mng', 'id': 'nodes', 'capacity': {'maxSize': 0}}, {'nodes'})


def test_plans_fail_closed_and_up_requires_snapshot():
    inventory = {'resources': [], 'errors': []}
    assert not plan(inventory, 'DOWN')['ready']
    assert not plan(inventory, 'UP')['ready']
    inventory['errors'] = ['AccessDenied']
    assert 'AccessDenied' in plan(inventory, 'DOWN')['blockers']


def test_public_ip_not_preserved_blocks():
    inventory = {'resources': [{'id': 'x', 'classification': 'LAB', 'action': 'stop', 'public_ip': True}],
                 'preflight': {'blockers': [], 'recovery_verified': True}}
    assert not plan(inventory, 'DOWN')['ready']


def test_signed_api_and_replay_expiry():
    event = {'rawPath': '/api/status', 'requestContext': {'http': {'method': 'GET'}}, 'headers': {}}
    content = '\n'.join(('1000', 'GET', '/api/status', hashlib.sha256(b'').hexdigest(), 'owner'))
    signature = hmac.new(b'test-key', content.encode(), hashlib.sha256).hexdigest()
    event['headers'] = {'x-power-timestamp': '1000', 'x-power-actor': 'owner', 'x-power-signature': signature}
    with patch('power_manager.api.signing_secret', return_value='test-key'):
        assert authenticate(event, now=1000) == ('owner', {})
        with pytest.raises(PermissionError):
            authenticate(event, now=1061)
        event['rawPath'] = '/api/operations'
        with pytest.raises(PermissionError):
            authenticate(event, now=1000)
