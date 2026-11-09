#!/usr/bin/env python3
"""Reject destructive/primary/global/foreign changes in the standby root plan."""
import json
import sys

ALLOWED = {
    "aws_instance", "aws_security_group", "aws_iam_instance_profile", "aws_vpc", "aws_subnet", "aws_internet_gateway", "aws_route_table", "aws_route",
    "aws_route_table_association", "aws_eks_cluster", "aws_eks_node_group", "aws_eks_addon",
    "aws_eks_access_entry", "aws_eks_access_policy_association", "aws_eks_pod_identity_association",
    "aws_iam_role", "aws_iam_role_policy", "aws_iam_role_policy_attachment", "aws_launch_template",
    "aws_cloudwatch_log_group", "aws_s3_bucket", "aws_s3_bucket_versioning",
    "aws_s3_bucket_server_side_encryption_configuration", "aws_s3_bucket_public_access_block",
    "aws_s3_bucket_policy", "aws_kms_key", "aws_backup_vault", "aws_ecr_repository",
    "aws_sqs_queue", "aws_sqs_queue_redrive_allow_policy",
}

def check_plan(plan):
    for change in plan.get("resource_changes", []):
        if change.get("mode") == "data":
            continue
        if change["change"]["actions"] == ["no-op"]:
            continue
        if (change["type"] not in ALLOWED or "delete" in change["change"]["actions"]
                or not change["address"].startswith(("module.standby[0].", "module.standby_sqs[0]."))):
            raise ValueError("Plan contains out-of-scope/destructive resource: " + change["address"])
        after = change["change"].get("after") or {}
        region = after.get("region")
        if region and region != "us-east-2":
            raise ValueError("Wrong region")
        # New regional resources must never adopt a primary ID.
        serialized = json.dumps(after)
        if "aws-eks-istio-lab-dev-spot" in serialized or '"us-east-1"' in serialized:
            raise ValueError("Primary resource in standby plan")
    return True

if __name__ == "__main__":
    check_plan(json.load(open(sys.argv[1])))
    print("PASS: standby plan scope; no deletion/replacement or primary resource")
