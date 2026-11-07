"""Bounded metadata inventory, including untagged resources in named services."""
from __future__ import annotations

from collections.abc import Callable


def collect(call: Callable[..., dict], region: str) -> dict:
    """CLI call must paginate; per-service denial remains explicit unknown coverage."""
    resources, coverage = [], []
    specifications = (
        ("tagging", "resourcegroupstaggingapi", "get-resources", "ResourceTagMappingList", "ResourceARN"),
        ("ecr", "ecr", "describe-repositories", "repositories", "repositoryArn"),
        ("lambda", "lambda", "list-functions", "Functions", "FunctionArn"),
        ("load-balancers", "elbv2", "describe-load-balancers", "LoadBalancers", "LoadBalancerArn"),
        ("rds", "rds", "describe-db-instances", "DBInstances", "DBInstanceArn"),
        ("secrets-metadata", "secretsmanager", "list-secrets", "SecretList", "ARN"),
    )
    for label, service, operation, key, arn_key in specifications:
        try:
            response = call(service, operation)
            rows = response[key]
            if not isinstance(rows, list):
                raise ValueError("Invalid resource collection")
            found = [{"arn": row[arn_key], "region": region} for row in rows]
            if not all(isinstance(row["arn"], str) and row["arn"].startswith("arn:") for row in found):
                raise ValueError("Invalid resource identifier")
            resources.extend(found)
            coverage.append({"service": label, "status": "partial" if label == "tagging" else "observed"})
        except (RuntimeError, OSError, ValueError, KeyError, TypeError):
            coverage.append({"service": label, "status": "unavailable"})
    # SQS has no list-ARN operation; inspect metadata only, with a hard request cap.
    try:
        urls = call("sqs", "list-queues", "--max-results", "1000").get("QueueUrls", [])
        found = []
        for url in urls[:200]:
            attributes = call("sqs", "get-queue-attributes", "--queue-url", url,
                              "--attribute-names", "QueueArn")["Attributes"]
            found.append({"arn": attributes["QueueArn"], "region": region})
        resources.extend(found)
        coverage.append({"service": "sqs", "status": "observed" if len(urls) <= 200 else "partial"})
    except (RuntimeError, OSError, ValueError, KeyError, TypeError):
        coverage.append({"service": "sqs", "status": "unavailable"})
    try:
        identity = call("sts", "get-caller-identity")
        account = identity["Account"]
        partition = identity["Arn"].split(":")[1]
        if not account.isdigit() or len(account) != 12:
            raise ValueError("Invalid account")
    except (RuntimeError, OSError, ValueError, KeyError, TypeError):
        account, partition = None, None
    for service, operation, key, resource_type in (
        ("dynamodb", "list-tables", "TableNames", "table/"),
        ("eks", "list-clusters", "clusters", "cluster/"),
    ):
        try:
            if account is None:
                raise ValueError("Identity unavailable")
            names = call(service, operation)[key]
            resources.extend({"arn": f"arn:{partition}:{service}:{region}:{account}:{resource_type}{name}",
                              "region": region} for name in names)
            coverage.append({"service": service, "status": "observed"})
        except (RuntimeError, OSError, ValueError, KeyError, TypeError):
            coverage.append({"service": service, "status": "unavailable"})
    try:
        if account is None:
            raise ValueError("Identity unavailable")
        instances = call("ec2", "describe-instances")["Reservations"]
        resources.extend({"arn": f"arn:{partition}:ec2:{region}:{account}:instance/{instance['InstanceId']}",
                          "region": region} for reservation in instances for instance in reservation["Instances"])
        coverage.append({"service": "ec2-instances", "status": "observed"})
    except (RuntimeError, OSError, ValueError, KeyError, TypeError):
        coverage.append({"service": "ec2-instances", "status": "unavailable"})
    unique = {row["arn"]: row for row in resources}
    return {"complete": False, "coverage": coverage,
            "resources": [unique[key] for key in sorted(unique)]}
