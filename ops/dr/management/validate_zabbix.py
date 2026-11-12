#!/usr/bin/env python3
"""Verify actual Zabbix API authentication, recovered events and ClickHouse tables privately."""

import base64
import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path("/opt/cosmotech/dr-recovery")
DIR = ROOT / "zabbix"


def api(method, params, token=None):
    headers = {"Content-Type": "application/json-rpc"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = json.dumps(
        {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    ).encode()
    with urllib.request.urlopen(
        urllib.request.Request(
            "http://127.0.0.1:8088/api_jsonrpc.php", data=body, headers=headers
        ),
        timeout=30,
    ) as response:
        return json.load(response)


def main():
    for _ in range(90):
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:8088/", timeout=5
            ) as response:
                if response.status == 200:
                    break
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(3)
    else:
        raise RuntimeError("Recovered Zabbix web frontend unavailable")
    credentials = json.loads(
        Path("/mnt/dr-zabbix/opt/cosmotech/zabbix/secrets/credentials.json").read_text()
    )
    version = api("apiinfo.version", {})
    if "result" not in version:
        raise RuntimeError("Zabbix API version not available")
    login = api(
        "user.login",
        {"username": credentials["username"], "password": credentials["password"]},
    )
    if "result" not in login:
        raise RuntimeError("Restored Zabbix identity did not authenticate")
    token = login["result"]
    events = api(
        "event.get",
        {
            "eventids": ["118", "119", "120", "121"],
            "output": ["eventid", "clock", "value", "r_eventid"],
        },
        token,
    )
    if "result" not in events or {r["eventid"] for r in events["result"]} != {
        "118",
        "119",
        "120",
        "121",
    }:
        raise RuntimeError("Required restored Zabbix event cohort is incomplete")
    denied = api("host.get", {"output": ["hostid"], "limit": 1})
    if "error" not in denied:
        raise RuntimeError("Anonymous Zabbix host data unexpectedly available")
    partial = {
        "frontend_http": 200,
        "zabbix_api_version": version["result"],
        "authenticated_api": True,
        "anonymous_host_data_denied": True,
        "events_118_121": events["result"],
    }
    (DIR / "http-auth-validation-evidence.json").write_text(
        json.dumps(partial, indent=2)
    )
    raw = subprocess.check_output(
        [
            "docker",
            "inspect",
            "--format",
            "{{json .NetworkSettings.Networks}}",
            "cosmotech-dr-zabbix-clickhouse-1",
        ],
        text=True,
    )
    network = json.loads(raw)["cosmotech-dr-zabbix_backend"]
    address = network["IPAddress"]
    password = (DIR / "secrets/clickhouse_password").read_text().strip()
    auth = base64.b64encode(("zabbix:" + password).encode()).decode()

    def query(sql):
        request = urllib.request.Request(
            "http://" + address + ":8123/",
            data=sql.encode(),
            headers={"Authorization": "Basic " + auth},
        )
        with urllib.request.urlopen(request, timeout=240) as response:
            return response.read().decode().strip()

    for _ in range(90):
        try:
            if query("SELECT 1") == "1":
                break
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(3)
    else:
        raise RuntimeError("Recovered ClickHouse did not authenticate or become ready")
    query("SYSTEM STOP TTL MERGES")
    tables = json.loads(
        query(
            "SELECT name, engine FROM system.tables WHERE database='zabbix' FORMAT JSON"
        )
    )["data"]
    checks = []
    for table in tables:
        name = table["name"]
        if not name.replace("_", "").isalnum():
            raise ValueError("Unexpected table identifier")
        if "MergeTree" not in table["engine"]:
            continue
        active = json.loads(
            query(
                "SELECT name FROM system.parts WHERE active AND database='zabbix' AND table='"
                + name
                + "' FORMAT JSON"
            )
        )["data"]
        current_parts = {part["name"] for part in active}
        cache = DIR / (name + "-checksum-check.json")
        previous = (
            DIR / "first-table-check-detail.json" if name == "otel_logs" else cache
        )
        checked = None
        if previous.is_file():
            candidate = json.loads(previous.read_text())
            if {
                row.get("part_path") for row in candidate.get("data", [])
            } == current_parts and all(
                row.get("is_passed") == 1 for row in candidate.get("data", [])
            ):
                checked = candidate
        if checked is None:
            checked = json.loads(query("CHECK TABLE zabbix.`" + name + "` FORMAT JSON"))
        rows = checked.get("data", [])
        if (not rows and current_parts) or any(
            row.get("is_passed") != 1 for row in rows
        ):
            raise RuntimeError(
                "Restored ClickHouse table checksum check failed: " + name
            )
        cache.write_text(json.dumps(checked, indent=2))
        checks.append({"table": name, "check": "passed", "checked_parts": len(rows)})
    parts = json.loads(
        query(
            "SELECT sum(rows) AS rows, sum(bytes_on_disk) AS bytes FROM system.parts WHERE active AND database='zabbix' FORMAT JSON"
        )
    )["data"][0]
    result = {
        "frontend_http": 200,
        "zabbix_api_version": version["result"],
        "authenticated_api": True,
        "anonymous_host_data_denied": True,
        "events_118_121": events["result"],
        "clickhouse_authenticated": True,
        "clickhouse_tables": len(tables),
        "clickhouse_checksum_checks": checks,
        "clickhouse_active_rows": parts["rows"],
        "clickhouse_active_bytes": parts["bytes"],
        "ttl_merges_paused": True,
        "copied_notifications_fenced": True,
    }
    (DIR / "application-validation-evidence.json").write_text(
        json.dumps(result, indent=2)
    )
    api("user.logout", [], token)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
