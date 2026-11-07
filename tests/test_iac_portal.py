import unittest
from src.iac_portal.report import build_report


class PortalTests(unittest.TestCase):
    def test_recursive_drift_and_secret_redaction(self):
        resource = {"address": "module.x.aws_sqs_queue.q", "type": "aws_sqs_queue", "mode": "managed", "values": {"arn": "arn:managed", "password": "DO-NOT-EXPORT"}}
        state = {"values": {"root_module": {"child_modules": [{"resources": [resource]}]}}}
        plan = {"resource_drift": [{"address": resource["address"], "change": {"actions": ["update"], "before": {"secret": "DO-NOT-EXPORT"}}}]}
        report = build_report(state, plan, {"complete": False, "resources": [{"arn": "arn:managed", "region": "us-east-1"}, {"arn": "arn:other", "region": "us-east-1"}]}, region="us-east-1")
        self.assertEqual(report["drift_count"], 1)
        self.assertEqual(report["unmanaged_candidates"], ["arn:other"])
        self.assertNotIn("DO-NOT-EXPORT", str(report))
        self.assertIsNone(report["last_apply"])

    def test_missing_inventory_is_unknown(self):
        with self.assertRaises(ValueError):
            build_report({}, {}, {}, region="us-east-1")

    def test_other_region_and_duplicates(self):
        report = build_report({}, {}, {"complete": False, "resources": [{"arn": "a", "region": "us-west-2"}]}, region="us-east-1")
        self.assertEqual(report["unmanaged_candidates"], [])
        self.assertFalse(report["inventory_complete"])
