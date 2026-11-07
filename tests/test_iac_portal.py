import unittest
from src.iac_portal.report import build_report


class PortalTests(unittest.TestCase):
    def test_recursive_drift_and_secret_redaction(self):
        resource = {"address": "module.x.aws_sqs_queue.q", "type": "aws_sqs_queue", "mode": "managed", "values": {"arn": "arn:managed", "password": "DO-NOT-EXPORT"}}
        state = {"values": {"root_module": {"child_modules": [{"resources": [resource]}]}}}
        plan = {"timestamp": "2026-11-07T11:00:00Z", "resource_drift": [{"address": resource["address"], "change": {"actions": ["update"], "before": {"secret": "DO-NOT-EXPORT"}}}]}
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


    def test_missing_plan_is_unknown_and_global_region_is_preserved(self):
        state = {"values": {"root_module": {"resources": [{"address": "aws_iam_role.reader", "type": "aws_iam_role", "mode": "managed", "values": {"arn": "arn:aws:iam::123456789012:role/reader"}}]}}}
        report = build_report(state, {}, {"complete": False}, region="us-east-1")
        self.assertFalse(report["drift_evaluated"])
        self.assertIsNone(report["drift_count"])
        self.assertEqual(report["resources"][0]["region"], "global")
        self.assertEqual(report["resources"][0]["status"], "unknown")
        with self.assertRaises(ValueError):
            build_report(state, {"errored": True}, {"complete": False}, region="us-east-1")
