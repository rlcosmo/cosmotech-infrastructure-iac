#!/usr/bin/env bash
# Clean recovery host only. Never start copied cron, agents or applications here.
set -Eeuo pipefail
umask 077
[[ -f /opt/cosmotech/dr-recovery/inspect_management_copy.py ]] || exit 1
install -d -m 0700 /opt/cosmotech/dr-recovery
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  sed -i -E 's@http://[^ /]*ec2.archive.ubuntu.com/ubuntu@https://archive.ubuntu.com/ubuntu@g; s@http://@https://@g' /etc/apt/sources.list.d/ubuntu.sources
  apt-get update -qq > /opt/cosmotech/dr-recovery/package-install.log 2>&1
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2 python3-yaml python3-bcrypt curl >> /opt/cosmotech/dr-recovery/package-install.log 2>&1
  systemctl enable --now docker >> /opt/cosmotech/dr-recovery/package-install.log 2>&1
fi
printf 'Clean Docker runtime installed; no application or copied automation started.\n'
