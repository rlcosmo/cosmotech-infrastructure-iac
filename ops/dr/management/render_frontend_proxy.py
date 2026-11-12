#!/usr/bin/env python3
"""Publish isolated Docker frontends through host loopback only, without container egress."""

import ipaddress
import json
import subprocess
from pathlib import Path

ROOT = Path("/opt/cosmotech/dr-recovery")


def main():
    servers = []
    targets = [
        (
            "cosmotech-dr-itsm-" + app + "-1",
            port,
            upstream,
            "cosmotech-dr-itsm_recovery",
            True,
        )
        for app, port, upstream in [
            ("glpi", 8080, 80),
            ("kestra", 8081, 8080),
            ("automation", 8082, 8080),
        ]
    ]
    targets.append(
        ("cosmotech-dr-zabbix-web-1", 8088, 8080, "cosmotech-dr-zabbix_backend", False)
    )
    for name, local_port, container_port, expected_network, required in targets:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .NetworkSettings.Networks}}",
                name,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode and not required:
            continue
        result.check_returncode()
        networks = json.loads(result.stdout)
        network = networks.get(expected_network)
        if not required and (not network or not network.get("IPAddress")):
            continue
        if not network:
            raise ValueError("Expected isolated DR network attachment")
        address = ipaddress.ip_address(network["IPAddress"])
        if address.version != 4 or not address.is_private:
            raise ValueError("Expected private container IPv4 address")
        servers.append(
            f"""server {{
    listen 127.0.0.1:{local_port};
    location / {{
      proxy_pass http://{address}:{container_port};
      proxy_http_version 1.1;
      proxy_set_header Host $http_host;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection $connection_upgrade;
      proxy_buffering off;
      proxy_read_timeout 90s;
    }}
  }}"""
        )
    config = """worker_processes 1;
pid /run/cosmotech-dr-frontends.pid;
error_log /opt/cosmotech/dr-recovery/nginx-error-private.log warn;
events {{ worker_connections 256; }}
http {{
  access_log off;
  server_tokens off;
  client_max_body_size 20m;
  map $http_upgrade $connection_upgrade {{ default upgrade; '' close; }}
  {}
}}
""".format("\n  ".join(servers))
    target = ROOT / "nginx-frontends.conf"
    target.write_text(config)
    target.chmod(0o600)
    print(
        f"Rendered {len(servers)} loopback-only upstreams; container network isolation preserved"
    )


if __name__ == "__main__":
    main()
