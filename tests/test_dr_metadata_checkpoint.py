"""A recovery signature must never authorize new critical application content."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('checkpoint', Path(__file__).parents[1] / 'ops/promotion/reconcile_metadata_checkpoint.py')
checkpoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checkpoint)


class CheckpointScopeTests(unittest.TestCase):
    def test_reviewed_metadata_is_accepted(self):
        checkpoint.validate_paths(['disaster-recovery/platform-bootstrap.json',
                                   'gitops/bootstrap/observability-v2-application.json'])

    def test_every_critical_application_authority_path_is_rejected(self):
        paths = ['gitops/apps/orders-api/dev/deployment.json',
                 'gitops/apps/orders-worker/dev/configmap.json',
                 'gitops/releases/orders-api-dev.json',
                 'gitops/releases/orders-worker-dev.json',
                 'gitops/environments/dev/release-policy.json',
                 'gitops/policies/history-retention/orders-api-dev.json',
                 'gitops/bootstrap/orders-project.yaml',
                 'gitops/bootstrap/argocd/values.yaml',
                 '.github/workflows/unreviewed.yml', 'scripts/new-publisher.py',
                 'docs/../gitops/apps/orders-api/dev/deployment.json']
        for path in paths:
            with self.subTest(path=path), self.assertRaises(PermissionError):
                checkpoint.validate_paths([path])
        with self.assertRaises(ValueError):
            checkpoint.validate_paths([])
