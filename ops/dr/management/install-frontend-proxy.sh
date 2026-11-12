#!/usr/bin/env bash
# Dedicated loopback proxy on the clean DR host, preserving the isolated Docker network.
set -Eeuo pipefail
umask 077
recovery_dir=/opt/cosmotech/dr-recovery
if ! command -v nginx >/dev/null; then
  apt-get update -qq > "$recovery_dir/proxy-package-install-private.log" 2>&1
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx >> "$recovery_dir/proxy-package-install-private.log" 2>&1
  systemctl stop nginx
  systemctl disable nginx >> "$recovery_dir/proxy-package-install-private.log" 2>&1
fi
python3 "$recovery_dir/render_frontend_proxy.py"
nginx -t -c "$recovery_dir/nginx-frontends.conf" > "$recovery_dir/proxy-config-validation.log" 2>&1
cat > /etc/systemd/system/cosmotech-dr-frontends.service <<'UNIT'
[Unit]
Description=CosmoTech isolated recovery frontend loopback proxy
After=docker.service network-online.target
Requires=docker.service
[Service]
Type=simple
ExecStartPre=/usr/bin/python3 /opt/cosmotech/dr-recovery/render_frontend_proxy.py
ExecStartPre=/usr/sbin/nginx -t -c /opt/cosmotech/dr-recovery/nginx-frontends.conf
ExecStart=/usr/sbin/nginx -c /opt/cosmotech/dr-recovery/nginx-frontends.conf -g "daemon off;"
ExecReload=/usr/sbin/nginx -c /opt/cosmotech/dr-recovery/nginx-frontends.conf -s reload
Restart=on-failure
RestartSec=10
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now cosmotech-dr-frontends.service
systemctl is-active cosmotech-dr-frontends.service
printf 'Recovery proxy active on 127.0.0.1 ports 8080/8081/8082; no inbound AWS rule added.\n'
