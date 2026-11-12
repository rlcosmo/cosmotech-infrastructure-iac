#!/usr/bin/env python3
"""Restore verified ITSM logical dumps into NEW, network-isolated Ohio databases."""

import copy
import json
import secrets
import subprocess
import time
from pathlib import Path

ROOT = Path("/opt/cosmotech/dr-recovery")
SOURCE = Path("/mnt/dr-itsm/opt/cosmotech/backups/logical/20261112T051322Z")


def command(arguments, *, source=None, capture=False):
    with (ROOT / "database-restore-private.log").open("ab") as log:
        result = subprocess.run(
            arguments,
            stdin=source,
            stdout=subprocess.PIPE if capture else log,
            stderr=log,
            check=False,
        )
    if result.returncode:
        raise RuntimeError("Recovery command failed; inspect the protected host log")
    return result.stdout.decode().strip() if capture else None


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    mount = json.loads(
        subprocess.check_output(
            ["findmnt", "-J", "--target", "/mnt/dr-itsm"], text=True
        )
    )["filesystems"][0]
    if "ro" not in mount["options"].split(",") or not (SOURCE / "COMPLETE").is_file():
        raise ValueError("Verified read-only recovery cohort is required")
    envfile = ROOT / "runtime.env"
    if not envfile.exists():
        values = {
            key: secrets.token_hex(32)
            for key in [
                "GLPI_DB_PASSWORD",
                "GLPI_DB_ROOT_PASSWORD",
                "KESTRA_DB_PASSWORD",
                "AUTOMATION_DB_PASSWORD",
            ]
        }
        values["KESTRA_DR_CONFIG"] = str(ROOT / "kestra-application.yaml")
        with envfile.open("x") as file:
            file.write(
                "".join(key + "=" + value + "\n" for key, value in values.items())
            )
        envfile.chmod(0o600)
    compose = json.loads((ROOT / "compose-source.json").read_text())
    pg = copy.deepcopy(compose["services"]["kestra-db"])
    pg["environment"] = {
        "POSTGRES_DB": "kestra_automation",
        "POSTGRES_USER": "kestra_automation",
        "POSTGRES_PASSWORD": "${AUTOMATION_DB_PASSWORD:?required}",
    }
    pg["volumes"] = ["automation_db:/var/lib/postgresql/data"]
    pg["mem_limit"] = "512m"
    compose["services"]["automation-db"] = pg
    compose["volumes"]["automation_db"] = {}
    for service in compose["services"].values():
        service["logging"] = {
            "driver": "local",
            "options": {"max-size": "10m", "max-file": "3"},
        }
    file = ROOT / "compose.json"
    file.write_text(json.dumps(compose, indent=2))
    file.chmod(0o600)
    cli = [
        "docker",
        "compose",
        "--project-name",
        "cosmotech-dr-itsm",
        "--env-file",
        str(envfile),
        "-f",
        str(file),
    ]
    services = ["glpi-db", "kestra-db", "automation-db"]
    command(cli + ["config", "--quiet"])
    command(cli + ["pull", *services])
    command(cli + ["up", "-d", *services])
    mysql = cli + [
        "exec",
        "-T",
        "glpi-db",
        "sh",
        "-c",
        'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" exec mariadb -uroot -N -B glpi "$@"',
        "mariadb-query",
    ]
    postgres = lambda service, database: (
        cli
        + [
            "exec",
            "-T",
            service,
            "psql",
            "-U",
            database,
            "-d",
            database,
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
        ]
    )
    for _ in range(60):
        try:
            command(mysql + ["-e", "SELECT 1"], capture=True)
            for service, database in [
                ("kestra-db", "kestra"),
                ("automation-db", "kestra_automation"),
            ]:
                command(postgres(service, database) + ["-c", "SELECT 1"], capture=True)
            break
        except RuntimeError:
            time.sleep(3)
    else:
        raise RuntimeError("New isolated database readiness timed out")
    report = {"cohort": SOURCE.name, "databases": []}
    marker = ROOT / "glpi-database-restored.json"
    if not marker.exists():
        count = command(
            mysql
            + [
                "-e",
                'SELECT COUNT(*) FROM information_schema.tables WHERE table_schema="glpi"',
            ],
            capture=True,
        )
        if count != "0":
            raise RuntimeError(
                "Nonempty unmarked target GLPI database; refuse overwrite"
            )
        with (SOURCE / "glpi.sql").open("rb") as dump:
            command(mysql, source=dump)
        marker.write_text(
            json.dumps({"source_sha256_verified": True, "cohort": SOURCE.name})
        )
    tables = command(
        mysql
        + [
            "-e",
            'SELECT COUNT(*) FROM information_schema.tables WHERE table_schema="glpi"',
        ],
        capture=True,
    )
    report["databases"].append({"database": "glpi", "tables": int(tables)})
    for service, database, filename in [
        ("kestra-db", "kestra", "kestra.dump"),
        ("automation-db", "kestra_automation", "kestra-automation.dump"),
    ]:
        marker = ROOT / (database + "-database-restored.json")
        if not marker.exists():
            count = command(
                postgres(service, database)
                + [
                    "-c",
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'",
                ],
                capture=True,
            )
            if count != "0":
                raise RuntimeError(
                    "Nonempty unmarked target PostgreSQL database; refuse overwrite"
                )
            with (SOURCE / filename).open("rb") as dump:
                command(
                    cli
                    + [
                        "exec",
                        "-T",
                        service,
                        "pg_restore",
                        "--no-owner",
                        "--no-privileges",
                        "--exit-on-error",
                        "-U",
                        database,
                        "-d",
                        database,
                    ],
                    source=dump,
                )
            marker.write_text(
                json.dumps({"source_sha256_verified": True, "cohort": SOURCE.name})
            )
        tables = command(
            postgres(service, database)
            + [
                "-c",
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'",
            ],
            capture=True,
        )
        report["databases"].append({"database": database, "tables": int(tables)})
    (ROOT / "itsm-database-restore-evidence.json").write_text(
        json.dumps(report, indent=2)
    )
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
