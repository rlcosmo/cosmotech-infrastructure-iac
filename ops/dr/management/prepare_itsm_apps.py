#!/usr/bin/env python3
"""Prepare isolated GLPI and Kestra webservers; no scheduler, executor, worker or Docker socket."""

import copy
import json
import re
import subprocess
import tarfile
from pathlib import Path

import yaml

ROOT = Path("/opt/cosmotech/dr-recovery")
COPY = Path("/mnt/dr-itsm")
COHORT = COPY / "opt/cosmotech/backups/logical/20261112T051322Z"


def run(arguments, *, capture=False):
    with (ROOT / "apps-preparation-private.log").open("ab") as log:
        result = subprocess.run(
            arguments,
            stdout=subprocess.PIPE if capture else log,
            stderr=log,
            check=False,
        )
    if result.returncode:
        raise RuntimeError("DR app preparation failed; inspect protected host log")
    return result.stdout.decode().strip() if capture else None


def env_values(text):
    result = {}
    for line in text.splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key] = value.strip().strip('"').strip("'")
    return result


def main():
    if not (ROOT / "itsm-database-restore-evidence.json").exists():
        raise ValueError("Successful logical database restore is required")
    values = env_values((ROOT / "runtime.env").read_text())
    with tarfile.open(COHORT / "reconstruction.tar.gz") as archive:

        def read(name):
            member = archive.getmember(name)
            if not member.isfile() or member.size > 2 * 1024 * 1024:
                raise ValueError("Unexpected reconstruction file")
            return archive.extractfile(member).read().decode()

        source_config = yaml.safe_load(read("itsm/application.yaml"))
        source_env = env_values(read("secrets/itsm/runtime.env"))
        automation_config = yaml.safe_load(read("itsm-automation/application.yaml"))
        automation_env = env_values(read("secrets/automation/kestra-oss/runtime.env"))
    credentials = {}
    for name, config, original_env, database, db_service, db_secret, port in [
        (
            "kestra",
            source_config,
            source_env,
            "kestra",
            "kestra-db",
            "KESTRA_DB_PASSWORD",
            8081,
        ),
        (
            "automation",
            automation_config,
            automation_env,
            "kestra_automation",
            "automation-db",
            "AUTOMATION_DB_PASSWORD",
            8082,
        ),
    ]:
        key = config["kestra"]["encryption"]["secret-key"]
        if key.startswith("${") and key.endswith("}"):
            key = original_env[key[2:-1]]
        prefix = "KESTRA_ADMIN_" if name == "kestra" else "AUTOMATION_"
        username = original_env[prefix + "USERNAME"]
        password = original_env[prefix + "PASSWORD"]
        target = {
            "datasources": {
                "postgres": {
                    "url": f"jdbc:postgresql://{db_service}:5432/{database}",
                    "driver-class-name": "org.postgresql.Driver",
                    "username": database,
                    "password": values[db_secret],
                }
            },
            "kestra": {
                "encryption": {"secret-key": key},
                "repository": {"type": "postgres"},
                "queue": {"type": "postgres"},
                "storage": {"type": "local", "local": {"base-path": "/app/storage"}},
                "server": {
                    "basic-auth": {"username": username, "password": password},
                    "url": f"http://127.0.0.1:{port}/",
                },
                "security": {
                    "instance-owner": {"username": username, "password": password}
                },
                "tasks": {"tmp-dir": {"path": "/tmp/kestra-wd/tmp"}},
            },
        }
        path = ROOT / (name + "-application.yaml")
        path.write_text(yaml.safe_dump(target))
        path.chmod(0o444)
        credentials[name] = {"username": username, "password": password, "port": port}
    credentials["glpi"] = {
        "username": "glpi",
        "password": source_env["GLPI_ADMIN_PASSWORD"],
        "port": 8080,
    }
    technical_credentials = COPY / "opt/cosmotech/secrets/automation/glpi.json"
    if technical_credentials.is_file():
        technical = json.loads(technical_credentials.read_text())
        credentials["glpi"].update(
            username=technical["username"], password=technical["password"]
        )
    path = ROOT / "application-credentials.json"
    path.write_text(json.dumps(credentials))
    path.chmod(0o600)
    compose = json.loads((ROOT / "compose.json").read_text())
    services = compose["services"]
    services["kestra"]["command"] = [
        "server",
        "webserver",
        "--config",
        "/etc/kestra/application.yaml",
    ]
    services["kestra"]["environment"]["JAVA_OPTS"] = "-Xms128m -Xmx768m"
    services["kestra"]["mem_limit"] = "1280m"
    engine = copy.deepcopy(services["kestra"])
    engine["volumes"] = [
        "automation_data:/app/storage",
        str(ROOT / "automation-application.yaml") + ":/etc/kestra/application.yaml:ro",
    ]
    engine["ports"] = ["127.0.0.1:8082:8080"]
    services["automation"] = engine
    compose["volumes"]["automation_data"] = {}
    sources = {
        "glpi_data": "cosmotech-glpi-data",
        "kestra_data": "cosmotech-kestra-data",
        "automation_data": "cosmotech-automation-kestra-storage",
    }
    for target_name, source_name in sources.items():
        volume = "cosmotech-dr-" + target_name.replace("_", "-")
        compose["volumes"][target_name] = {"name": volume}
        run(["docker", "volume", "create", volume])
        destination = Path(
            run(
                ["docker", "volume", "inspect", "--format", "{{.Mountpoint}}", volume],
                capture=True,
            )
        )
        marker = ROOT / (target_name + "-files-restored.json")
        if not marker.exists():
            if any(destination.iterdir()):
                raise RuntimeError(
                    "Nonempty unmarked application data volume; refuse overwrite"
                )
            original = COPY / "var/lib/docker/volumes" / source_name / "_data"
            run(["cp", "-a", str(original) + "/.", str(destination)])
            marker.write_text(
                json.dumps({"source_volume": source_name, "cohort": COHORT.name})
            )
        if target_name == "glpi_data":
            config = destination / "config/config_db.php"
            source = config.read_text()
            replacements = {
                "dbhost": "glpi-db",
                "dbuser": "glpi",
                "dbpassword": values["GLPI_DB_PASSWORD"],
                "dbdefault": "glpi",
            }
            for field, value in replacements.items():
                source, count = re.subn(
                    r"(public\s+\$" + field + r"\s*=\s*)[\'\"][^\n]*?[\'\"]\s*;",
                    lambda m, value=value: m.group(1) + repr(value) + ";",
                    source,
                )
                if count != 1:
                    raise ValueError(
                        "Unexpected restored GLPI database configuration format"
                    )
            config.write_text(source)
    services["glpi"]["volumes"].append(
        str(ROOT / "patches/Session.php") + ":/var/www/glpi/src/Session.php:ro"
    )
    compose["networks"]["recovery"]["internal"] = True
    for service in services.values():
        if any("/var/run/docker.sock" in v for v in service.get("volumes", [])):
            raise ValueError("Copied Docker socket access is forbidden")
    (ROOT / "compose.json").write_text(json.dumps(compose, indent=2))
    print(
        json.dumps(
            {
                "prepared": ["glpi", "kestra-webserver", "automation-webserver"],
                "network_internal": True,
                "execution_roles_started": False,
                "credentials_logged": False,
            }
        )
    )


if __name__ == "__main__":
    main()
