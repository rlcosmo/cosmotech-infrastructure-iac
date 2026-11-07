"""Distinguish EKS authentication failures from missing namespace permissions."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('bootstrap_entrypoint', Path(__file__).parents[1] / 'scripts/bootstrap_lab_v2_admin.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class KubernetesAuthorityTests(unittest.TestCase):
    def check(self, result):
        with patch.object(bootstrap.subprocess, 'run', return_value=result) as run:
            bootstrap.check_kubernetes_authority({'AWS_PROFILE': 'temporary'}, 'create', 'roles.rbac.authorization.k8s.io')
            self.assertIn('can-i', run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs['env']['AWS_PROFILE'], 'temporary')

    def test_yes_is_authorized(self):
        self.check(subprocess.CompletedProcess([], 0, b'yes\n', b''))

    def test_no_names_the_missing_permission(self):
        with self.assertRaisesRegex(RuntimeError, 'permission required: create roles.*observability'):
            self.check(subprocess.CompletedProcess([], 1, b'no\n', b''))

    def test_unauthorized_does_not_claim_missing_rbac(self):
        with self.assertRaisesRegex(RuntimeError, 'EKS authentication failed'):
            self.check(subprocess.CompletedProcess([], 1, b'', b'error: You must be logged in to the server (Unauthorized)'))

    def test_unexpected_output_is_not_authority_or_disclosed(self):
        with self.assertRaisesRegex(RuntimeError, 'raw output withheld'):
            self.check(subprocess.CompletedProcess([], 0, b'unexpected-private-output', b''))

class KubernetesSessionTests(unittest.TestCase):
    def test_static_profile_only_obtains_temporary_session(self):
        import datetime as dt
        import json
        identity = {'Account': '123456789012', 'Arn': 'arn:aws:iam::123456789012:user/serverless-lab-admin', 'UserId': 'test'}
        credentials = {'AccessKeyId': 'ASIA-test', 'SecretAccessKey': 'test-only', 'SessionToken': 'test-only',
                       'Expiration': (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat()}
        with patch.object(bootstrap, 'command', side_effect=[json.dumps(identity).encode(), json.dumps({'Credentials': credentials}).encode()]) as call, patch.object(bootstrap, 'aws', return_value=identity), patch('builtins.print'):
            env = bootstrap.kubernetes_session('serverless-lab-static', '/source/config', '/source/credentials', identity['Account'])
        self.assertEqual(env['AWS_ACCESS_KEY_ID'], 'ASIA-test')
        self.assertNotIn('AWS_PROFILE', env)
        self.assertEqual(call.call_args_list[1].args[0][4], 'get-session-token')

    def test_wrong_source_stops_before_session_request(self):
        import json
        with patch.object(bootstrap, 'command', return_value=json.dumps({'Account':'123456789012', 'Arn':'arn:aws:iam::123456789012:root'}).encode()) as call:
            with self.assertRaisesRegex(ValueError, 'Unexpected Kubernetes'):
                bootstrap.kubernetes_session('source', '/config', '/credentials', '123456789012')
            self.assertEqual(call.call_count, 1)
