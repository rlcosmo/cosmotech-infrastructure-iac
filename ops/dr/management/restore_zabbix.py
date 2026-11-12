#!/usr/bin/env python3
"""Restore Zabbix SQL and recover ClickHouse on a verified independent filesystem copy."""

import copy
import gzip
import json
import secrets
import shutil
import subprocess
import time
from pathlib import Path

import yaml

ROOT = Path("/opt/cosmotech/dr-recovery")
DIR = ROOT / "zabbix"
WORK = Path("/mnt/dr-zabbix-work")
SOURCE = Path("/mnt/dr-zabbix/opt/cosmotech/zabbix/backups/config-2026-11-12.sql.gz")


def run(args, *, source=None, capture=False):
    with (DIR / "restore-private.log").open("ab") as log:
        r = subprocess.run(
            args,
            stdin=source,
            stdout=subprocess.PIPE if capture else log,
            stderr=log,
            check=False,
        )
    if r.returncode:
        raise RuntimeError("Zabbix recovery command failed; inspect protected host log")
    return r.stdout.decode().strip() if capture else None


def main():
    evidence = json.loads(
        (ROOT / "zabbix-working-filesystem-verified.json").read_text()
    )
    mount = json.loads(
        subprocess.check_output(["findmnt", "-J", "--target", str(WORK)], text=True)
    )["filesystems"][0]
    if mount["target"] != str(WORK) or "rw" not in mount["options"].split(","):
        raise ValueError(
            "Verified working filesystem must be mounted; refuse an empty bind directory"
        )
    data = WORK / "var/lib/docker/volumes/cosmotech-zabbix-clickhouse/_data"
    if not data.is_dir() or evidence.get("offline_filesystem_check") != "passed":
        raise ValueError("Validated recovered ClickHouse data is required")
    DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    secret_dir = DIR / "secrets"
    secret_dir.mkdir(mode=0o700, exist_ok=True)
    for name in ["postgres_password", "clickhouse_password"]:
        path = secret_dir / name
        if not path.exists():
            with path.open("x") as stream:
                stream.write(secrets.token_hex(32) + "\n")
        path.chmod(
            0o444
        )  # protected host parent; readable only inside assigned secret mounts
    original = yaml.safe_load((ROOT / "zabbix-compose-source.yaml").read_text())
    images = json.loads((ROOT / "zabbix-images-source.json").read_text())
    services = {
        name: copy.deepcopy(original["services"][name])
        for name in ["postgres", "clickhouse", "server", "web"]
    }
    for name, service in services.items():
        service["restart"] = "no"
        service["networks"] = ["backend"]
        service.pop("ports", None)
        service["logging"] = {
            "driver": "local",
            "options": {"max-size": "10m", "max-file": "3"},
        }
        service.pop("build", None)
        service.pop("pull_policy", None)
        if name == "web":
            service["image"] = images["web"]["image"]
    services["postgres"]["mem_limit"] = "768m"
    services["clickhouse"]["mem_limit"] = "2g"
    services["clickhouse"]["volumes"] = [
        {
            "type": "bind",
            "source": str(data),
            "target": "/var/lib/clickhouse",
            "bind": {"create_host_path": False},
        },
        str(DIR / "clickhouse-safety.xml")
        + ":/etc/clickhouse-server/config.d/zz-dr-safety.xml:ro",
    ]
    services["server"]["mem_limit"] = "512m"
    services["server"]["environment"]["ZBX_HOUSEKEEPINGFREQUENCY"] = "0"
    services["web"]["mem_limit"] = "512m"
    services["web"]["environment"]["ZBX_SERVER_NAME"] = "CosmoTech DR Ohio - Zabbix 8.0"
    services["web"]["depends_on"] = {
        "postgres": {"condition": "service_healthy"},
        "server": {"condition": "service_started"},
    }
    (DIR / "clickhouse-safety.xml").write_text(
        "<clickhouse><max_server_memory_usage>1342177280</max_server_memory_usage><mark_cache_size>67108864</mark_cache_size><uncompressed_cache_size>33554432</uncompressed_cache_size></clickhouse>\n"
    )
    (DIR / "clickhouse-safety.xml").chmod(0o444)
    compose = {
        "name": "cosmotech-dr-zabbix",
        "services": services,
        "networks": {"backend": {"internal": True}},
        "volumes": {"postgres": {"name": "cosmotech-dr-zabbix-postgres"}},
        "secrets": {
            n: {"file": str(secret_dir / n)}
            for n in ["postgres_password", "clickhouse_password"]
        },
    }
    file = DIR / "compose.json"
    file.write_text(json.dumps(compose, indent=2))
    file.chmod(0o600)
    cli = [
        "docker",
        "compose",
        "--project-name",
        "cosmotech-dr-zabbix",
        "-f",
        str(file),
    ]
    run(cli + ["config", "--quiet"])
    run(cli + ["pull", "postgres", "clickhouse", "server", "web"])
    run(cli + ["up", "-d", "postgres"])
    psql = cli + [
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "zabbix",
        "-d",
        "zabbix",
        "-At",
        "-v",
        "ON_ERROR_STOP=1",
    ]
    for _ in range(60):
        try:
            run(psql + ["-c", "SELECT 1"], capture=True)
            break
        except RuntimeError:
            time.sleep(3)
    else:
        raise RuntimeError("New isolated Zabbix PostgreSQL did not become ready")
    marker = DIR / "database-restored.json"
    if not marker.exists():
        count = run(
            psql
            + [
                "-c",
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'",
            ],
            capture=True,
        )
        if count != "0":
            raise RuntimeError("Nonempty unmarked Zabbix target; refuse overwrite")
        sql = DIR / "restore.sql"
        if not sql.exists():
            with gzip.open(SOURCE, "rb") as source, sql.open("xb") as destination:
                shutil.copyfileobj(source, destination)
            sql.chmod(0o600)
        with sql.open("rb") as source:
            run(psql, source=source)
        marker.write_text(
            json.dumps({"source": SOURCE.name, "working_volume": evidence["volume"]})
        )
    events = int(run(psql + ["-c", "SELECT COUNT(*) FROM events"], capture=True))
    expected_events = 0
    inside_events = False
    with gzip.open(SOURCE, "rt") as dump:
        for line in dump:
            if line.startswith("COPY public.events "):
                inside_events = True
                continue
            if inside_events:
                if line.rstrip() == "\\.":
                    inside_events = False
                else:
                    expected_events += 1
    if events != expected_events:
        raise RuntimeError("Restored event count differs from verified logical dump")
    # Fence copied collection/notifications before the server or web frontend starts.
    run(
        psql
        + [
            "-c",
            "UPDATE actions SET status=1; UPDATE media_type SET status=1; UPDATE hosts SET status=1 WHERE status=0;",
        ]
    )
    active_actions = int(
        run(psql + ["-c", "SELECT COUNT(*) FROM actions WHERE status=0"], capture=True)
    )
    active_hosts = int(
        run(psql + ["-c", "SELECT COUNT(*) FROM hosts WHERE status=0"], capture=True)
    )
    if active_actions or active_hosts:
        raise RuntimeError("Copied actions or hosts are not fenced")
    run(cli + ["up", "-d", "clickhouse", "server", "web"])
    report = {
        "logical_source": SOURCE.name,
        "events": events,
        "expected_dump_events": expected_events,
        "active_copied_actions": active_actions,
        "active_copied_hosts": active_hosts,
        "working_volume": evidence["volume"],
        "internal_network": True,
        "runtime_started": True,
    }
    (DIR / "database-restore-evidence.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
