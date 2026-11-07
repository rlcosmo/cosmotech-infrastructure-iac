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
