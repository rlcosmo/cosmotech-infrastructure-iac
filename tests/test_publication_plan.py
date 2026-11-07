import unittest
from src.publication.plan_gate import inspect_plan


class PlanGateTests(unittest.TestCase):
    def change(self, address, actions):
        return {"address": address, "mode": "managed", "change": {"actions": actions}}

    def test_waf_lambda_and_any_unrelated_address_block_apply(self):
        plan = {"resource_changes": [self.change("aws_ssm_document.lab_v2_publication", ["update"]),
                                     self.change("aws_wafv2_ip_set.observability_admin", ["update"]),
                                     self.change("module.producer_lambda.aws_lambda_function.this", ["update"])]}
        report = inspect_plan(plan)
        self.assertFalse(report["can_apply"])
        self.assertEqual(len(report["allowed"]), 1)
        self.assertEqual(len(report["excluded"]), 2)

    def test_replacement_deletion_import_and_self_modification_blocked(self):
        for actions in (["delete"], ["delete", "create"], ["create", "delete"]):
            self.assertFalse(inspect_plan({"resource_changes": [self.change("aws_iam_role.iac_evidence", actions)]})["can_apply"])
        plan = {"resource_changes": [self.change("aws_iam_role_policy.lab_v2_deploy", ["update"])]}
        self.assertFalse(inspect_plan(plan)["can_apply"])
        self.assertTrue(inspect_plan(plan, bootstrap=True)["can_apply"])
        resource = self.change("aws_iam_role.iac_evidence", ["create"])
        resource["change"]["importing"] = {"id": "existing"}
        self.assertFalse(inspect_plan({"resource_changes": [resource]})["can_apply"])
        resource["change"]["actions"] = ["no-op"]
        self.assertFalse(inspect_plan({"resource_changes": [resource]})["can_apply"])

    def test_noop_and_values_never_exported(self):
        plan = {"resource_changes": [self.change("aws_ssm_document.lab_v2_publication", ["update"])]}
        plan["resource_changes"][0]["change"]["after"] = {"password": "never-export"}
        report = inspect_plan(plan)
        self.assertTrue(report["can_apply"])
        self.assertNotIn("never-export", str(report))
        self.assertTrue(inspect_plan({"resource_changes": [self.change("aws_wafv2_ip_set.observability_admin", ["no-op"])]})["can_apply"])
        with self.assertRaises(ValueError):
            inspect_plan({"errored": True})
