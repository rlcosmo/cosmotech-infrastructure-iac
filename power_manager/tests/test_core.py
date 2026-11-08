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


def test_iam_admin_route_is_not_selectable_by_public_http_body():
    from power_manager import api
    event={'source':'cosmotech.power.iam-admin','run_id':'123-1','authorization':'OWNER_AUTHORIZED_CONTROLLED_CYCLE',
        'method':'POST','path':'/api/operations','payload':{},'requestContext':{'http':{'method':'POST'}},'rawPath':'/api/operations'}
    with patch.object(api,'route') as route:
        assert api.handler(event,None)['statusCode']==401
        route.assert_not_called()


def test_iam_admin_route_preserves_audit_actor_without_impersonating_visitor():
    from power_manager import api
    event={'source':'cosmotech.power.iam-admin','run_id':'123-1','authorization':'OWNER_AUTHORIZED_CONTROLLED_CYCLE',
        'method':'POST','path':'/api/plan','payload':{'direction':'DOWN'}}
    with patch.object(api,'route',return_value={'statusCode':200}) as route:
        assert api.handler(event,None)['statusCode']==200
        assert route.call_args.args[1:] == ('github-actions:123-1',{'direction':'DOWN'})
    event['path']='/api/arbitrary'
    assert api.handler(event,None)['statusCode']==401


def test_backend_correlation_survives_errors_without_logging_sensitive_exception(capsys):
    import json
    from power_manager import api
    identifier='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    event={'headers':{'X-Power-Correlation-Id':identifier}}
    with patch.object(api,'handle_request',side_effect=RuntimeError('secret-token-value')):
        result=api.handler(event,None)
    body=json.loads(result['body']);logs=capsys.readouterr().out
    assert result['statusCode']==500 and body['error_code']=='INTERNAL_ERROR'
    assert result['headers']['x-correlation-id']==identifier==body['correlation_id']
    assert identifier in logs
    assert 'secret-token-value' not in logs+result['body']
    assert api.CORRELATION.get() is None


def test_aws_access_denial_is_correlated_and_does_not_expose_raw_message(capsys):
    import json
    from botocore.exceptions import ClientError
    from power_manager import api
    identifier='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
    error=ClientError({'Error':{'Code':'AccessDeniedException','Message':'Authorization: Bearer never-log-this'},'ResponseMetadata':{'RequestId':'aws-request'}},'GetParameter')
    with patch.object(api,'authenticate',side_effect=error):
        result=api.handler({'headers':{'x-power-correlation-id':identifier}},None)
    body=json.loads(result['body']);logs=capsys.readouterr().out
    assert result['statusCode']==503 and body['error_code']=='AccessDeniedException'
    assert body['correlation_id']==identifier
    assert 'never-log-this' not in logs+result['body']
    assert 'GetParameter' in logs


def test_publisher_trust_rejects_fork_branch_and_wrong_audience_without_calendar_expiry():
    import importlib.util
    from pathlib import Path
    path=Path(__file__).resolve().parents[2]/'scripts/bootstrap_power_manager.py'
    spec=importlib.util.spec_from_file_location('bootstrap_power',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    trust=module.publisher_trust()['Statement']
    assert len(trust)==1 and trust[0]['Action']=='sts:AssumeRoleWithWebIdentity'
    condition=trust[0]['Condition']
    assert set(condition)=={'StringEquals'}
    required=condition['StringEquals']
    assert required['token.actions.githubusercontent.com:aud']=='sts.amazonaws.com'
    subject=required['token.actions.githubusercontent.com:sub']
    assert subject=='repo:rlcosmo@124368446/cosmotech-infrastructure-iac@1445869018:ref:refs/heads/main'
    assert '*' not in subject and ':pull_request' not in subject
