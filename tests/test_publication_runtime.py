import importlib.util
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('publication_runtime', ROOT / 'ops/iac-portal/publication_runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class RuntimeTests(unittest.TestCase):
    def test_private_cli_payload_is_removed_even_when_command_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / 'requests'
            def failed(args):
                request = Path(args[-1].removeprefix('file://'))
                self.assertEqual(request.stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(request.read_text()), {'Value': 'test-only-secret'})
                self.assertNotIn('test-only-secret', str(args))
                raise RuntimeError('test failure')
            with patch.object(runtime, 'REQUEST_DIRECTORY', private), patch.object(runtime, 'run', side_effect=failed):
                with self.assertRaises(RuntimeError):
                    runtime.aws('ssm', 'put-parameter', value={'Value': 'test-only-secret'})
            self.assertEqual(list(private.iterdir()), [])

    def test_credential_schema_never_prints_secret_on_failure(self):
        self.assertEqual(runtime.credentials({'runtime_credential_record': {'username': 'test', 'password': 'test-only'}}), ('test', 'test-only'))
        with self.assertRaises(ValueError) as caught:
            runtime.credentials({'unknown': {'password': 'must-not-appear'}})
        self.assertNotIn('must-not-appear', str(caught.exception))

    def test_sparse_or_stale_data_cannot_become_zero_or_success(self):
        def api(path, auth):
            if path == '/api/datasources':
                return [{'uid': 'test', 'name': 'prometheus-aws-eks-istio-lab-dev', 'type': 'prometheus'}]
            return {'status': 'success', 'data': {'result': []}}
        with patch.object(runtime, 'api', side_effect=api):
            with self.assertRaises(ValueError):
                runtime.validate_real_data('test-token')
        def stale(path, auth):
            if path == '/api/datasources':
                return api(path, auth)
            value = '181' if 'timestamp' in path else '0'
            return {'status': 'success', 'data': {'result': [{'value': [0, value]}]}}
        with patch.object(runtime, 'api', side_effect=stale):
            with self.assertRaises(ValueError):
                runtime.validate_real_data('test-token')

    def test_fresh_measured_zero_is_distinct_from_absence(self):
        def api(path, auth):
            if path == '/api/datasources':
                return [{'uid': 'test', 'name': 'prometheus-aws-eks-istio-lab-dev', 'type': 'prometheus'}]
            return {'status': 'success', 'data': {'result': [{'value': [0, '0']} ]}}
        with patch.object(runtime, 'api', side_effect=api):
            result = runtime.validate_real_data('test-token')
        self.assertEqual(len(result), 5)

    def test_existing_dashboard_in_another_folder_is_not_overwritten(self):
        with patch.object(runtime, 'api', return_value={'meta': {'folderUid': 'another-owner'}}) as api:
            with self.assertRaises(ValueError):
                runtime.publish_dashboard('test-token')
            self.assertEqual(api.call_count, 1)

    def test_evidence_policy_no_mutation_payload_read_or_replication(self):
        policy = json.loads((ROOT / 'ops/iac-portal/evidence-policy.json.tftpl').read_text())
        forbidden = {'secretsmanager:GetSecretValue', 'ssm:GetParametersByPath', 'dynamodb:Scan', 'dynamodb:Query',
                     'dynamodb:ReadDataForReplication', 'dynamodb:ReplicateSettings', 'iam:PassRole', 'sts:AssumeRole'}
        for statement in policy['Statement']:
            actions = statement['Action']
            self.assertFalse(forbidden.intersection(actions))
            self.assertNotIn('*', actions)
            self.assertTrue(all(action.split(':')[1].startswith(('Get', 'List', 'Describe')) or action == 'apigateway:GET' for action in actions))
            if 'dynamodb:GetItem' in actions:
                self.assertEqual(statement['Condition']['Null'], {'dynamodb:LeadingKeys': 'false'})
        # Inline policy aggregate limit is 10,240 chars; leave room for encrypted PutObject grant.
        self.assertLess(len(json.dumps(policy, separators=(',', ':'))), 9500)
