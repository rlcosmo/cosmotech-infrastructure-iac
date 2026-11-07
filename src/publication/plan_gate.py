"""Fail closed before applying a saved Lab V2 plan; never log resource values."""
from __future__ import annotations

PUBLICATION = frozenset({"aws_ssm_document.lab_v2_publication"})
BOOTSTRAP = PUBLICATION | {
    "aws_iam_role.iac_evidence", "aws_iam_role_policy.iac_evidence",
    "aws_iam_role.iac_observer", "aws_iam_role_policy.iac_observer",
    "aws_iam_role.lab_v2_deploy", "aws_iam_role_policy.lab_v2_deploy",
    "aws_iam_role_policy.lab_v2_runtime", "aws_iam_role_policy.lab_v2_maintenance",
}



def inspect_plan(plan: dict, *, bootstrap: bool = False) -> dict:
    """Reject deletion, replacement, import, deferral and every unrelated address."""
    if plan.get("errored") or plan.get("deferred_changes"):
        raise ValueError("Failed or deferred plan cannot be applied")
    allowed = BOOTSTRAP if bootstrap else PUBLICATION
    accepted, excluded = [], []
    for resource in plan.get("resource_changes", []):
        if resource.get("mode") == "data":
            continue
        change = resource["change"]
        actions = change["actions"]
        row = {"address": resource["address"], "actions": actions}
        if change.get("importing"):
            excluded.append(row)
            continue
        if actions == ["no-op"]:
            continue
        if (resource["address"] not in allowed or actions not in (["create"], ["update"])
                or change.get("importing") or change.get("before_sensitive") is True):
            excluded.append(row)
        else:
            accepted.append(row)
    return {"allowed": accepted, "excluded": excluded, "can_apply": not excluded}
