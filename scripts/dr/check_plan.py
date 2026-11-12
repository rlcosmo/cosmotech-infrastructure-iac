#!/usr/bin/env python3
"""Reject destructive/primary/global/foreign changes in the standby root plan."""
import argparse
from collections import Counter
import json
import math

ALLOWED = {
    "aws_acm_certificate", "aws_volume_attachment",
    "aws_ebs_volume", "aws_instance", "aws_security_group", "aws_iam_instance_profile", "aws_vpc", "aws_subnet", "aws_internet_gateway", "aws_route_table", "aws_route",
    "aws_route_table_association", "aws_eks_cluster", "aws_eks_node_group", "aws_eks_addon",
    "aws_eks_access_entry", "aws_eks_access_policy_association", "aws_eks_pod_identity_association",
    "aws_iam_role", "aws_iam_role_policy", "aws_iam_role_policy_attachment", "aws_launch_template",
    "aws_cloudwatch_log_group", "aws_s3_bucket", "aws_s3_bucket_versioning",
    "aws_s3_bucket_server_side_encryption_configuration", "aws_s3_bucket_public_access_block",
    "aws_s3_bucket_policy", "aws_kms_key", "aws_backup_vault", "aws_ecr_repository",
    "aws_dynamodb_table", "aws_backup_plan", "aws_backup_selection", "aws_sqs_queue", "aws_sqs_queue_redrive_allow_policy",
}

REQUIRED_COUNTS = {
    "aws_vpc": 1, "aws_subnet": 3, "aws_eks_cluster": 1, "aws_eks_node_group": 1,
    "aws_eks_addon": 5, "aws_sqs_queue": 2, "aws_dynamodb_table": 1,
    "aws_ecr_repository": 3, "aws_s3_bucket": 1, "aws_kms_key": 1,
    "aws_backup_vault": 1, "aws_iam_role": 1,
}


def check_plan(plan, *, require_provisioning=False):
    if plan.get('errored') or plan.get('deferred_changes') or plan.get('complete') is False:
        raise ValueError('Incomplete plan cannot establish provisioning scope')
    for change in plan.get("resource_changes", []):
        if change.get("mode") == "data":
            continue
        if change["change"]["actions"] == ["no-op"]:
            continue
        if (change["type"] not in ALLOWED or "delete" in change["change"]["actions"]
                or not change["address"].startswith(("module.standby[0].", "module.standby_sqs[0]."))):
            raise ValueError("Plan contains out-of-scope/destructive resource: " + change["address"])
        if change["type"] == "aws_acm_certificate":
            certificate = change["change"].get("after") or {}
            if (change["change"]["actions"] != ["create"]
                    or certificate.get("domain_name") != "*.cosmotechsolutions.com.br"
                    or certificate.get("validation_method") != "DNS"
                    or certificate.get("options", [{}])[0].get("export") != "DISABLED"
                    or set(certificate.get("subject_alternative_names", [])) - {"*.cosmotechsolutions.com.br"}):
                raise ValueError("Only the existing lab wildcard may be requested in Ohio")
        if change["type"] == "aws_ebs_volume":
            before = change["change"].get("before") or {}
            after = change["change"].get("after") or {}
            changed = {key for key in before.keys() | after.keys()
                       if before.get(key) != after.get(key)}
            if (change["change"]["actions"] != ["update"] or not before.get("id")
                    or changed - {"tags", "tags_all"}
                    or after.get("tags_all", {}).get("Environment") != "dr"
                    or after.get("tags_all", {}).get("Project") != "CosmoTech"):
                raise ValueError("Only metadata updates on imported DR volumes are permitted")
        if change["type"] == "aws_volume_attachment":
            variables = plan.get("variables", {})
            volumes = variables.get("restored_volumes", {}).get("value", {})
            allowed = {v["volume_id"] for name, v in volumes.items()
                       if name in {"itsm_root", "zabbix_root"}
                       and v.get("availability_zone") == "us-east-2b"}
            after = change["change"].get("after") or {}
            if (change["change"]["actions"] != ["create"]
                    or variables.get("enable_recovery_host", {}).get("value") is not True
                    or after.get("volume_id") not in allowed
                    or not change["address"].startswith("module.standby[0].aws_volume_attachment.recovery_data[")):
                raise ValueError("Only verified management data copies may attach to the clean recovery host")
        after = change["change"].get("after") or {}
        region = after.get("region")
        if region and region != "us-east-2":
            raise ValueError("Wrong region")
        # New regional resources must never adopt a primary ID.
        serialized = json.dumps(after)
        if "aws-eks-istio-lab-dev-spot" in serialized or '"us-east-1"' in serialized:
            raise ValueError("Primary resource in standby plan")
    if require_provisioning:
        changes = [r for r in plan.get('resource_changes', []) if r.get('mode', 'managed') == 'managed']
        if not any(r['change']['actions'] != ['no-op'] for r in changes):
            raise ValueError('Empty/no-op plan is not provisioning evidence')
        counts = Counter(r['type'] for r in changes if r['address'].startswith(
            ('module.standby[0].', 'module.standby_sqs[0].')))
        if any(counts[kind] < minimum for kind, minimum in REQUIRED_COUNTS.items()):
            raise ValueError('Expected full standby resource graph is missing')
        readiness = plan.get('output_changes', {}).get('readiness', {}).get('after') or {}
        if (readiness.get('provisioning_enabled') is not True or readiness.get('mode') != 'passive'
                or readiness.get('selected_region') != 'us-east-2'
                or readiness.get('state_key') != 'aws-serverless-lab/dr/terraform.tfstate'
                or type(readiness.get('financial_ceiling_usd')) not in (int, float)
                or not math.isfinite(readiness['financial_ceiling_usd'])
                or readiness['financial_ceiling_usd'] <= 0):
            raise ValueError('Enabled isolated Ohio configuration with a positive budget is required')
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('plan')
    parser.add_argument('--require-provisioning', action='store_true')
    args = parser.parse_args()
    with open(args.plan) as source:
        check_plan(json.load(source), require_provisioning=args.require_provisioning)
    print("PASS: standby plan scope; no deletion/replacement or primary resource")
