"""Cached AWS inventory, billing and web health metrics using the installed AWS CLI."""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import decimal
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REGION = "us-east-1"
STATE_DIR = Path(os.environ.get("LAB_MONITORING_STATE", "/var/lib/cosmotech-monitoring"))
TOOLS = {
    "Zabbix": ("https://zabbix.cosmotechsolutions.com.br/", "/", 200),
    "Grafana": ("https://grafana.cosmotechsolutions.com.br/", "/api/health", 200),
    "Prometheus": ("https://prometheus-aws-eks-istio-lab-dev.cosmotechsolutions.com.br/", "/-/ready", 200),
    "Alertmanager": ("https://alertmanager.cosmotechsolutions.com.br/", "/-/ready", 200),
    "Tempo": ("https://tempo.cosmotechsolutions.com.br/", "/ready", 200),
    "Argo CD": ("https://argocd.cosmotechsolutions.com.br/", "/healthz", 200),
    "GLPI": ("https://glpi.cosmotechsolutions.com.br/", "/", 200),
    "Kestra": ("https://kestra.cosmotechsolutions.com.br/ui/", "/api/v1/main/flows/search", 401),
}


def aws(service: str, operation: str, *args: str) -> dict:
    """Use instance-profile credentials; never include raw AWS errors in metrics/logs."""
    result = subprocess.run(
        [os.environ.get("LAB_AWS_CLI", "aws"), service, operation, "--region", REGION, "--output", "json",
         "--cli-connect-timeout", "5", "--cli-read-timeout", "15", *args],
        capture_output=True, timeout=50, check=False,
        env={**os.environ, "AWS_MAX_ATTEMPTS": "2", "AWS_RETRY_MODE": "standard"},
    )
    if result.returncode:
        raise RuntimeError(f"{service}:{operation} failed")
    return json.loads(result.stdout)


def metric(name: str, value: object, **labels: str) -> str:
    """Render numeric Prometheus samples with escaped labels."""
    label_text = ",".join(f"{key}={json.dumps(str(val))}" for key, val in sorted(labels.items()))
    return f"lab_{name}" + ("{" + label_text + "}" if labels else "") + f" {value}"


def cost_samples(response: dict) -> list[str]:
    """Keep account gross, applied credits and net separate; never infer credit balance."""
    gross = decimal.Decimal(0)
    credits = decimal.Decimal(0)
    net = decimal.Decimal(0)
    services: dict[str, decimal.Decimal] = {}
    daily_costs: dict[str, decimal.Decimal] = {}
    for day in response["ResultsByTime"]:
        daily = decimal.Decimal(0)
        for group in day.get("Groups", []):
            service, record_type = group["Keys"]
            cost = group["Metrics"]["UnblendedCost"]
            if cost["Unit"] != "USD":
                raise ValueError("Unexpected billing currency")
            amount = decimal.Decimal(cost["Amount"])
            net += amount
            if record_type == "Credit":
                credits -= amount
            if record_type not in ("Credit", "Refund"):
                gross += amount
                daily += amount
                services[service] = services.get(service, decimal.Decimal(0)) + amount
        date = day["TimePeriod"]["Start"]
        daily_costs[date] = daily_costs.get(date, decimal.Decimal(0)) + daily
    lines = [metric("aws_cost_daily_usd", amount, date=date) for date, amount in sorted(daily_costs.items())]
    lines += [metric("aws_cost_mtd_gross_usd", gross),
              metric("aws_cost_mtd_credits_applied_usd", credits),
              metric("aws_cost_mtd_net_usd", net),
              metric("aws_cost_estimated", int(any(d.get("Estimated", False) for d in response["ResultsByTime"])))]
    lines += [metric("aws_cost_service_mtd_usd", value, aws_service=service) for service, value in sorted(services.items())]
    return lines


def collect_costs() -> list[str]:
    now = dt.datetime.now(dt.UTC).date()
    response = aws("ce", "get-cost-and-usage", "--time-period",
                   f"Start={now.replace(day=1)},End={now + dt.timedelta(days=1)}",
                   "--granularity", "DAILY", "--metrics", "UnblendedCost", "--group-by",
                   "Type=DIMENSION,Key=SERVICE", "Type=DIMENSION,Key=RECORD_TYPE")
    return cost_samples(response)


def collect_credits() -> list[str]:
    state = aws("freetier", "get-account-plan-state")
    remaining = state.get("accountPlanRemainingCredits")
    if not remaining or remaining["unit"] != "USD":
        raise ValueError("Free Tier credit balance unavailable")
    lines = [metric("aws_free_tier_credit_remaining_usd", remaining["amount"]),
             metric("aws_account_plan_info", 1, plan=state["accountPlanType"], status=state["accountPlanStatus"])]
    if state.get("accountPlanExpirationDate"):
        expiration = dt.datetime.fromisoformat(state["accountPlanExpirationDate"])
        lines.append(metric("aws_free_tier_credit_expiration_timestamp_seconds", expiration.timestamp()))
    return lines


def is_eks_node(instance: dict) -> bool:
    tags = {tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])}
    return any(key.startswith(("eks:", "kubernetes.io/cluster/")) for key in tags) or "eks" in tags.get("Name", "").lower()


def collect_servers() -> list[str]:
    response = aws("ec2", "describe-instances")
    all_instances = [i for reservation in response["Reservations"] for i in reservation["Instances"]]
    instances = [i for i in all_instances if not is_eks_node(i) and i["State"]["Name"] != "terminated"]
    statuses = {s["InstanceId"]: s for s in aws("ec2", "describe-instance-status", "--include-all-instances")["InstanceStatuses"]}
    lines = []
    queries = []
    query_names = {}
    for index, instance in enumerate(instances):
        iid = instance["InstanceId"]
        name = next((t["Value"] for t in instance.get("Tags", []) if t["Key"] == "Name"), iid)
        labels = {"server": name, "instance_id": iid}
        status = statuses.get(iid, {})
        healthy = instance["State"]["Name"] == "running" and all(status.get(k, {}).get("Status") == "ok" for k in ("InstanceStatus", "SystemStatus"))
        lines += [metric("aws_server_up", int(healthy), **labels),
                  metric("aws_server_info", 1, state=instance["State"]["Name"], instance_type=instance["InstanceType"], **labels)]
        if instance["State"]["Name"] == "running":
            key = f"cpu{index}"
            query_names[key] = labels
            queries.append({"Id": key, "MetricStat": {"Metric": {"Namespace": "AWS/EC2", "MetricName": "CPUUtilization", "Dimensions": [{"Name": "InstanceId", "Value": iid}]}, "Period": 300, "Stat": "Average"}, "ReturnData": True})
    if queries:
        end = dt.datetime.now(dt.UTC)
        data = aws("cloudwatch", "get-metric-data", "--metric-data-queries", json.dumps(queries),
                   "--start-time", (end - dt.timedelta(minutes=20)).isoformat(), "--end-time", end.isoformat(), "--scan-by", "TimestampDescending")
        for result in data["MetricDataResults"]:
            if result.get("Values"):
                lines.append(metric("aws_server_cpu_percent", result["Values"][0], **query_names[result["Id"]]))
    return lines



def host_samples(payload: dict, instance_id: str, server: str, probe_up: int) -> list[str]:
    """An unreachable probe is UNKNOWN, never a cached green process status."""
    labels = {"instance_id": instance_id, "server": server}
    lines = [metric("ec2_host_probe_up", probe_up, **labels),
             metric("ec2_host_last_success_timestamp_seconds", payload.get("observed_at", 0), **labels)]
    for item in payload.get("services", []):
        fields = {**labels, "app_service": str(item["service"]), **{k: str(item[k]) for k in ("application", "manager", "repair")}}
        status = item["status"] if probe_up else -1
        if status not in {-1, 0, 1}:
            raise ValueError("Invalid application service status")
        lines += [metric("ec2_application_service_status", status, **fields),
                  metric("ec2_application_service_info", 1, state=item.get("state", "unknown") if probe_up else "probe unavailable", **fields)]
    for item in payload.get("filesystems", []):
        fields = {**labels, **{k: str(item[k]) for k in ("mountpoint", "device", "filesystem")}}
        for field in ("size_bytes", "used_bytes", "available_bytes", "used_percent"):
            lines.append(metric("ec2_filesystem_" + field, item[field], **fields))
    return lines


def collect_application_hosts() -> list[str]:
    instances = [i for r in aws("ec2", "describe-instances")["Reservations"] for i in r["Instances"]
                 if not is_eks_node(i) and i["State"]["Name"] != "terminated"]
    path = STATE_DIR / "host-snapshots.json"
    try:
        snapshots = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        snapshots = {}

    def read(instance: dict) -> tuple[str, str, dict, int]:
        iid = instance["InstanceId"]
        name = next((t["Value"] for t in instance.get("Tags", []) if t["Key"] == "Name"), iid)
        previous = snapshots.get(iid, {})
        if instance["State"]["Name"] != "running":
            return iid, name, previous, 0
        try:
            command_id = aws("ssm", "send-command", "--document-name", "CosmoTech-Read-Application-Health",
                             "--document-version", "$LATEST", "--instance-ids", iid)["Command"]["CommandId"]
            for _ in range(15):
                time.sleep(2)
                try:
                    value = aws("ssm", "get-command-invocation", "--command-id", command_id, "--instance-id", iid)
                except RuntimeError:
                    continue
                if value["Status"] == "Success":
                    payload = json.loads(value["StandardOutputContent"])
                    if not payload.get("services") or not payload.get("filesystems"):
                        raise ValueError("Application/disk probe inventory is empty")
                    return iid, name, payload, 1
                if value["Status"] in {"Failed", "TimedOut", "Cancelled"}:
                    break
        except (RuntimeError, ValueError, KeyError, OSError, subprocess.SubprocessError):
            pass
        return iid, name, previous, 0

    lines = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for iid, name, payload, up in pool.map(read, instances):
            if up:
                snapshots[iid] = payload
            lines += host_samples(payload, iid, name, up)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshots))
    temporary.replace(path)
    return lines


def collect_codex_quota() -> list[str]:
    value = json.loads(aws("ssm", "get-parameter", "--name", "/cosmotech/monitoring/codex-usage")["Parameter"]["Value"])
    lines = [metric("codex_quota_observed_timestamp_seconds", value["observed_at"]),
             metric("codex_absolute_token_balance_available", int(value.get("token_balance_available", False)))]
    for bucket, windows in value["buckets"].items():
        for window, info in windows.items():
            used = float(info["usedPercent"])
            if not 0 <= used <= 100:
                raise ValueError("Invalid quota percentage")
            labels = {"bucket": bucket, "window": window}
            lines += [metric("codex_quota_remaining_percent", 100 - used, **labels),
                      metric("codex_quota_reset_timestamp_seconds", info["resetsAt"], **labels),
                      metric("codex_quota_window_minutes", info["windowDurationMins"], **labels)]
    return lines

def collect_inventory() -> list[str]:
    """Inventory is regional (us-east-1), except S3/IAM which are account-wide."""
    specs = [
        ("S3", "s3api", "list-buckets", "Buckets"),
        ("ECR", "ecr", "describe-repositories", "repositories"),
        ("DynamoDB", "dynamodb", "list-tables", "TableNames"),
        ("SQS", "sqs", "list-queues", "QueueUrls"),
        ("Lambda", "lambda", "list-functions", "Functions"),
        ("ALB/NLB", "elbv2", "describe-load-balancers", "LoadBalancers"),
        ("NAT Gateway", "ec2", "describe-nat-gateways", "NatGateways"),
        ("VPC", "ec2", "describe-vpcs", "Vpcs"),
        ("Elastic IP", "ec2", "describe-addresses", "Addresses"),
        ("RDS", "rds", "describe-db-instances", "DBInstances"),
        ("Backup vaults", "backup", "list-backup-vaults", "BackupVaultList"),
        ("SNS", "sns", "list-topics", "Topics"),
        ("Secrets Manager", "secretsmanager", "list-secrets", "SecretList"),
        ("KMS", "kms", "list-keys", "Keys"),
        ("ACM", "acm", "list-certificates", "CertificateSummaryList"),
        ("CloudWatch Logs", "logs", "describe-log-groups", "logGroups"),
        ("CodePipeline", "codepipeline", "list-pipelines", "pipelines"),
        ("CodeBuild", "codebuild", "list-projects", "projects"),
        ("WAF", "wafv2", "list-web-acls", "WebACLs"),
    ]
    lines = []
    for name, service, operation, key in specs:
        try:
            args = ("--scope", "REGIONAL") if service == "wafv2" else ()
            values = aws(service, operation, *args).get(key, [])
            lines += [metric("aws_inventory_resources", len(values), aws_service=name), metric("aws_inventory_api_up", 1, aws_service=name)]
            for value in values:
                if isinstance(value, str):
                    resource = value.rsplit("/", 1)[-1]
                else:
                    fields = ("Name", "repositoryName", "FunctionName", "LoadBalancerName", "NatGatewayId", "VpcId", "PublicIp", "DBInstanceIdentifier", "BackupVaultName", "TopicArn", "KeyId", "CertificateArn", "logGroupName", "name")
                    resource = str(next((value[field] for field in fields if field in value), "unknown"))
                lines.append(metric("aws_resource_info", 1, aws_service=name, resource=resource))
        except (RuntimeError, subprocess.TimeoutExpired, ValueError):
            lines.append(metric("aws_inventory_api_up", 0, aws_service=name))
    instances = [i for r in aws("ec2", "describe-instances")["Reservations"] for i in r["Instances"]]
    excluded_ids = {i["InstanceId"] for i in instances if is_eks_node(i)}
    servers = [i for i in instances if i["InstanceId"] not in excluded_ids and i["State"]["Name"] != "terminated"]
    volumes = [v for v in aws("ec2", "describe-volumes")["Volumes"] if not any(a["InstanceId"] in excluded_ids for a in v.get("Attachments", []))]
    lines += [metric("aws_inventory_resources", len(servers), aws_service="EC2 sem nodes EKS"),
              metric("aws_inventory_resources", len(volumes), aws_service="EBS sem volumes dos nodes EKS"),
              metric("aws_ebs_capacity_gib", sum(v["Size"] for v in volumes))]
    alarms = aws("cloudwatch", "describe-alarms")["MetricAlarms"]
    for state in ("OK", "ALARM", "INSUFFICIENT_DATA"):
        lines.append(metric("aws_cloudwatch_alarms", sum(a["StateValue"] == state for a in alarms), state=state))
    return lines



ZABBIX_COMMAND_CENTER_SECRET = "cosmotech/grafana/command-center"
ZABBIX_COMMAND_CENTER_URL = "https://zabbix.cosmotechsolutions.com.br/api_jsonrpc.php"
ZABBIX_SEVERITIES = {2: "Warning", 3: "Average", 4: "High", 5: "Disaster"}


def zabbix_request(token: str, method: str, params: dict) -> list:
    """Call only the read methods granted to the dedicated service account."""
    if method not in {"problem.get", "trigger.get"}:
        raise ValueError("Command Center API method is not read-only")
    request = urllib.request.Request(ZABBIX_COMMAND_CENTER_URL,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"Content-Type": "application/json-rpc", "Authorization": "Bearer "+token})
    with urllib.request.urlopen(request, timeout=15) as response:
        value = json.load(response)
    if "error" in value or not isinstance(value.get("result"), list):
        raise RuntimeError("Zabbix read API failed; sensitive diagnostics omitted")
    return value["result"]


def zabbix_problem_samples(problems: list[dict], triggers: list[dict]) -> list[str]:
    """Export each unresolved, unsuppressed monitored event once, including acknowledged events."""
    monitored = {t["triggerid"]: t for t in triggers}
    counts = {severity: 0 for severity in ZABBIX_SEVERITIES}
    lines, seen = [], set()
    for problem in problems:
        severity = int(problem["severity"])
        event = str(problem["eventid"])
        if (severity not in counts or event in seen or str(problem.get("r_eventid", "0")) != "0"
                or str(problem.get("suppressed", "0")) == "1" or problem["objectid"] not in monitored):
            continue
        seen.add(event)
        hosts = monitored[problem["objectid"]].get("hosts", [])
        names = sorted({host.get("name", host.get("host", "unknown")) for host in hosts})
        counts[severity] += 1
        labels = {"event_id": event, "host": "; ".join(names)[:240] or "unknown",
                  "problem": str(problem["name"])[:350], "severity": str(severity),
                  "severity_name": ZABBIX_SEVERITIES[severity],
                  "acknowledged": str(int(str(problem.get("acknowledged", "0")) == "1"))}
        lines += [metric("zabbix_problem_active", 1, **labels),
                  metric("zabbix_problem_started_timestamp_seconds", int(problem["clock"]), **labels)]
    lines += [metric("zabbix_problem_count", count, severity=str(severity), severity_name=ZABBIX_SEVERITIES[severity])
              for severity, count in counts.items()]
    return lines


def collect_zabbix_problems() -> list[str]:
    """Read the API token from Secrets Manager in memory; never write credentials to cache."""
    value = aws("secretsmanager", "get-secret-value", "--secret-id", ZABBIX_COMMAND_CENTER_SECRET)
    credentials = json.loads(value["SecretString"])
    if credentials.get("api_url") != ZABBIX_COMMAND_CENTER_URL or credentials["token_expires_at"] <= time.time():
        raise ValueError("Zabbix Command Center credentials expired or invalid")
    problems = zabbix_request(credentials["api_token"], "problem.get", {
        "output": ["eventid", "objectid", "name", "clock", "severity", "acknowledged", "suppressed", "r_eventid"],
        "source": 0, "object": 0, "recent": False, "suppressed": False,
        "severities": [2, 3, 4, 5], "sortfield": ["eventid"], "sortorder": "DESC", "limit": 1001})
    if len(problems) > 1000:
        raise RuntimeError("Zabbix Command Center event limit reached; refusing a partial snapshot")
    triggers = zabbix_request(credentials["api_token"], "trigger.get", {
        "triggerids": sorted({p["objectid"] for p in problems}), "output": ["triggerid"],
        "selectHosts": ["host", "name"], "monitored": True}) if problems else []
    return zabbix_problem_samples(problems, triggers) + [
        metric("zabbix_token_expiration_timestamp_seconds", credentials["token_expires_at"])]


def collect_web() -> list[str]:
    def probe(item: tuple) -> list[str]:
        name, (url, path, expected) = item
        origin = urllib.parse.urlsplit(url)
        target = f"{origin.scheme}://{origin.netloc}{path}"
        start = time.monotonic()
        code = 0
        try:
            with urllib.request.urlopen(target, timeout=10) as response:
                code = response.status
        except urllib.error.HTTPError as error:
            code = error.code
        except (OSError, urllib.error.URLError):
            pass
        labels = {"tool": name, "url": url}
        return [metric("tool_up", int(code == expected), **labels),
                metric("tool_http_status", code, **labels),
                metric("tool_response_seconds", time.monotonic() - start, **labels)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=7) as executor:
        return [sample for result in executor.map(probe, TOOLS.items()) for sample in result]


def collect_sqs() -> list[str]:
    """Read only CloudWatch through the existing role; no queue message APIs."""
    from sqs_canary_metrics import collect as collect_queue_metrics

    def call(**query: object) -> dict:
        return aws("cloudwatch", "get-metric-statistics",
                   "--namespace", str(query["Namespace"]),
                   "--metric-name", str(query["MetricName"]),
                   "--dimensions", json.dumps(query["Dimensions"]),
                   "--start-time", query["StartTime"].isoformat(),
                   "--end-time", query["EndTime"].isoformat(),
                   "--period", str(query["Period"]),
                   "--statistics", str(query["Statistics"][0]))

    def queue_attributes(queue_name: str) -> dict:
        # Reuse the current account/region and the existing ITSM GetQueueAttributes
        # grant. No GetQueueUrl permission, message receive or payload read.
        account = aws("sts", "get-caller-identity")["Account"]
        if not re.fullmatch(r"[0-9]{12}", account) or not re.fullmatch(r"[A-Za-z0-9_-]{1,75}(?:\.fifo)?", queue_name):
            raise ValueError("Invalid queue identity")
        url = f"https://sqs.{REGION}.amazonaws.com/{account}/{queue_name}"
        return aws("sqs", "get-queue-attributes", "--queue-url", url,
                   "--attribute-names", "ApproximateNumberOfMessages")

    result = collect_queue_metrics(call,
        os.environ.get("LAB_SQS_QUEUE_NAME", "aws-eks-istio-lab-dev-orders-eks"),
        os.environ.get("LAB_SQS_DLQ_NAME", "aws-eks-istio-lab-dev-orders-eks-dlq"),
        dt.datetime.now(dt.timezone.utc), queue_attributes=queue_attributes)
    if not result.startswith("cosmotech_sqs_collection_success 1\n"):
        raise RuntimeError("SQS gauges unavailable")
    return result.strip().splitlines()


def collect() -> None:
    STATE_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
    path = STATE_DIR / "cache.json"
    try:
        cache = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        cache = {}
    for data in cache.values():
        data["samples"] = [re.sub(r"(?<=[{,])service=", "aws_service=", sample) for sample in data.get("samples", [])]
    now = time.time()
    # Persist successful snapshots; a failure is visible and never becomes a false zero.
    groups = {"costs": (21600, collect_costs), "credits": (3600, collect_credits),
              "servers": (300, collect_servers), "inventory": (900, collect_inventory),
              "web": (45, collect_web), "sqs": (60, collect_sqs), "application_hosts": (60, collect_application_hosts),
              "codex": (60, collect_codex_quota), "zabbix": (45, collect_zabbix_problems)}
    for name, (ttl, collector) in groups.items():
        previous = cache.get(name, {})
        if now - previous.get("attempt", 0) < (ttl if previous.get("ok") else 300):
            continue
        try:
            cache[name] = {"ok": 1, "attempt": now, "success": time.time(), "samples": collector()}
        except (RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired, OSError):
            cache[name] = {**previous, "ok": 0, "attempt": now}
        print(json.dumps({"component": "lab-monitoring", "group": name, "ok": cache[name]["ok"]}), flush=True)
    samples = [metric("exporter_last_run_timestamp_seconds", time.time())]
    for name, data in cache.items():
        samples += data.get("samples", [])
        samples += [metric("collection_success", data.get("ok", 0), group=name),
                    metric("collection_last_success_timestamp_seconds", data.get("success", 0), group=name)]
    for filename, contents in [("cache.json", json.dumps(cache)), ("lab.prom", "\n".join(samples) + "\n")]:
        temporary = STATE_DIR / (filename + ".tmp")
        temporary.write_text(contents)
        temporary.chmod(0o644)
        temporary.replace(STATE_DIR / filename)


if __name__ == "__main__":
    collect()
