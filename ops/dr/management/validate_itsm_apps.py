#!/usr/bin/env python3
"""Start isolated recovery web frontends after fencing local GLPI cron and mail."""

import base64
import http.cookiejar
import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path("/opt/cosmotech/dr-recovery")
CLI = [
    "docker",
    "compose",
    "--project-name",
    "cosmotech-dr-itsm",
    "--env-file",
    str(ROOT / "runtime.env"),
    "-f",
    str(ROOT / "compose.json"),
    "--profile",
    "recovery-apps",
]


def command(args, *, capture=False):
    with (ROOT / "apps-validation-private.log").open("ab") as log:
        r = subprocess.run(
            args, stdout=subprocess.PIPE if capture else log, stderr=log, check=False
        )
    if r.returncode:
        raise RuntimeError("Protected regional app validation command failed")
    return r.stdout.decode().strip() if capture else None


def main():
    compose = json.loads((ROOT / "compose.json").read_text())
    for name in ["kestra", "automation"]:
        if compose["services"][name]["command"][:2] != ["server", "webserver"]:
            raise ValueError("Only webserver roles may start before execution fencing")
    if compose["networks"]["recovery"]["internal"] is not True:
        raise ValueError("An isolated internal application network is mandatory")
    mysql = CLI + [
        "exec",
        "-T",
        "glpi-db",
        "sh",
        "-c",
        'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" exec mariadb -uroot -N -B glpi "$@"',
        "query",
    ]
    command(
        mysql
        + [
            "-e",
            "UPDATE glpi_crontasks SET state=0; UPDATE glpi_configs SET value='0' WHERE name IN ('use_notifications','notifications_mailing','notifications_ajax');",
        ]
    )
    active_cron = command(
        mysql + ["-e", "SELECT COUNT(*) FROM glpi_crontasks WHERE state<>0"],
        capture=True,
    )
    if active_cron != "0":
        raise RuntimeError("Copied GLPI cron was not fenced")
    command(CLI + ["up", "-d", "glpi", "kestra", "automation"])
    creds = json.loads((ROOT / "application-credentials.json").read_text())
    result = {
        "internal_network": True,
        "active_glpi_cron": 0,
        "kestra_execution_roles": False,
        "apps": {},
    }
    for name in ["glpi", "kestra", "automation"]:
        url = "http://127.0.0.1:" + str(creds[name]["port"])
        for _ in range(90):
            try:
                with urllib.request.urlopen(url + "/", timeout=5) as response:
                    code = response.status
                if code == 200:
                    break
            except (urllib.error.URLError, TimeoutError):
                pass
            time.sleep(3)
        else:
            raise RuntimeError("Regional web frontend did not become ready: " + name)
        result["apps"][name] = {"http_status": code}
        if name != "glpi":
            path = url + "/api/v1/main/flows/search"
            try:
                urllib.request.urlopen(path, timeout=10)
                raise RuntimeError(
                    "Kestra flow API unexpectedly permits anonymous access"
                )
            except urllib.error.HTTPError as error:
                result["apps"][name]["anonymous_api_http"] = error.code
                if error.code != 401:
                    raise RuntimeError(
                        "Unexpected anonymous Kestra API status"
                    ) from None
            auth = base64.b64encode(
                (creds[name]["username"] + ":" + creds[name]["password"]).encode()
            ).decode()
            with urllib.request.urlopen(
                urllib.request.Request(
                    path, headers={"Authorization": "Basic " + auth}
                ),
                timeout=15,
            ) as response:
                data = json.load(response)
                result["apps"][name].update(
                    authenticated_api_http=response.status, flows=data.get("total")
                )

    class Inputs(HTMLParser):
        def __init__(self):
            super().__init__()
            self.values = {}

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "input" and a.get("type") == "hidden" and a.get("name"):
                self.values[a["name"]] = a.get("value", "")

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    with opener.open("http://127.0.0.1:8080/", timeout=15) as response:
        html = response.read().decode()
    parser = Inputs()
    parser.feed(html)
    # Submit the actual login fields. This pinned GLPI login form does not emit a
    # hidden CSRF field before authentication; preserve one whenever present.
    form = {
        **parser.values,
        "login_name": creds["glpi"]["username"],
        "login_password": creds["glpi"]["password"],
        "submit": "Post",
    }
    request = urllib.request.Request(
        "http://127.0.0.1:8080/front/login.php",
        data=urllib.parse.urlencode(form).encode(),
        headers={
            "Origin": "http://127.0.0.1:8080",
            "Referer": "http://127.0.0.1:8080/",
        },
    )
    with opener.open(request, timeout=20) as response:
        html = response.read().decode()
        authenticated = "logout.php" in html and "/front/central.php" in response.url
    if not authenticated:
        raise RuntimeError("Recovered GLPI authentication not verified")
    result["apps"]["glpi"]["authenticated"] = True
    (ROOT / "itsm-app-validation-evidence.json").write_text(
        json.dumps(result, indent=2)
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
