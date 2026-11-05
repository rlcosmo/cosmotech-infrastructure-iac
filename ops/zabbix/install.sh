#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
install -d -m 0700 /opt/cosmotech/zabbix /opt/cosmotech/zabbix/secrets /opt/cosmotech/zabbix/backups
if ! command -v docker >/dev/null; then
  sed -i -E 's@http://[^ /]*ec2.archive.ubuntu.com/ubuntu@https://archive.ubuntu.com/ubuntu@g; s@http://@https://@g' /etc/apt/sources.list.d/ubuntu.sources
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2 python3-bcrypt unzip curl > /opt/cosmotech/zabbix/package-install.log 2>&1
  systemctl enable --now docker
fi
if ! command -v aws >/dev/null; then
  curl --fail --silent --show-error --location https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/zabbix-awscliv2.zip
  unzip -q /tmp/zabbix-awscliv2.zip -d /tmp/zabbix-awscli
  /tmp/zabbix-awscli/aws/install >/dev/null
  rm -rf /tmp/zabbix-awscliv2.zip /tmp/zabbix-awscli
fi
install -m 0600 "$source_dir/docker-compose.yml" /opt/cosmotech/zabbix/docker-compose.yml
install -m 0644 "$source_dir/telemetry-collector.yaml" /opt/cosmotech/zabbix/telemetry-collector.yaml
install -m 0600 "$source_dir/images.json" /opt/cosmotech/zabbix/images.json
python3 - <<'PY'
import json,secrets
from pathlib import Path
root=Path('/opt/cosmotech/zabbix/secrets')
for name in ('postgres_password','clickhouse_password'):
 p=root/name
 if not p.exists():p.write_text(secrets.token_hex(32)+'\n')
 # File secrets are bind-mounted; non-root Zabbix UIDs must read only their assigned mounts.
 # The host parent directory remains root-only (0700).
 p.chmod(0o444)
p=root/'clickhouse_password_otel'
p.write_text((root/'clickhouse_password').read_text().strip());p.chmod(0o444)
p=root/'credentials.json'
if not p.exists():p.write_text(json.dumps({'username':'Admin','password':secrets.token_urlsafe(36),'url':'https://zabbix.cosmotechsolutions.com.br'}));p.chmod(0o600)
PY
cd /opt/cosmotech/zabbix
docker compose config --quiet
docker compose pull --quiet > /opt/cosmotech/zabbix/image-pull.log 2>&1
docker compose up -d > /opt/cosmotech/zabbix/startup.log 2>&1
for attempt in $(seq 1 90); do
  if docker compose exec -T postgres psql -U zabbix -d zabbix -Atc "SELECT 1 FROM users WHERE username='Admin'" 2>/dev/null | grep -qx 1; then break; fi
  sleep 5
done
python3 "$source_dir/initialize_telemetry.py"
python3 "$source_dir/configure.py"
aws ssm put-parameter --name /cosmotech/zabbix/credentials --type SecureString --overwrite \
  --value file:///opt/cosmotech/zabbix/secrets/credentials.json >/dev/null
# Logical configuration backup; EC2/EBS backup provides crash-consistent telemetry recovery.
cat > /etc/cron.d/cosmotech-zabbix-backup <<'CRON'
17 3 * * * root cd /opt/cosmotech/zabbix && umask 077 && docker compose exec -T postgres pg_dump -U zabbix zabbix | gzip > /opt/cosmotech/zabbix/backups/config-$(date +\%F).sql.gz
37 3 * * * root find /opt/cosmotech/zabbix/backups -name 'config-*.sql.gz' -mtime +7 -delete
CRON
docker compose exec -T server /usr/sbin/zabbix_server --version | head -2
printf 'Zabbix stack initialized; credentials stored in SSM SecureString.\n'
