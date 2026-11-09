"""Reject trust/key and namespace escalation while bootstrapping observability."""
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('bootstrap',ROOT/'scripts/configure_v2_observability_argocd.py')
bootstrap=importlib.util.module_from_spec(spec);spec.loader.exec_module(bootstrap)

class BootstrapTests(unittest.TestCase):
    def test_only_pinned_public_signer_is_added(self):
        key=(ROOT/'ops/argocd/observability-github-signing-key.asc').read_text()
        self.assertEqual(bootstrap.public_key_patch(key),{'data':{'B5690EEEBB952194':key}})
        for bad in ['-----BEGIN PGP PRIVATE KEY BLOCK-----','not a key',key.replace('PUBLIC KEY','PRIVATE KEY')]:
            with self.assertRaises(ValueError):bootstrap.public_key_patch(bad)

    def test_only_existing_namespaces_plus_observability_are_managed(self):
        for old in ['orders-api,orders-worker','orders-api,orders-worker,observability']:
            self.assertEqual(bootstrap.namespace_patch(old),{'stringData':{'namespaces':'orders-api,orders-worker,observability'}})
        for old in ['', '*','orders-api','orders-api,orders-worker,default']:
            with self.assertRaises(ValueError):bootstrap.namespace_patch(old)
