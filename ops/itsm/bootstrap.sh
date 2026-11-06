#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR=/opt/cosmotech/itsm
SECRETS_DIR=/opt/cosmotech/secrets/itsm
IDENTITY_MARKER=/var/lib/cosmotech-itsm-identities-initialized
ENV_FILE="$SECRETS_DIR/runtime.env"
APP_CONFIG="$DATA_DIR/application.yaml"
COMPOSE=(docker compose --project-name cosmotech-itsm --env-file "$ENV_FILE" --file "$SCRIPT_DIR/docker-compose.yml")

install -d -o root -g root -m 0750 "$DATA_DIR" "$DATA_DIR/tmp" "$SECRETS_DIR"
chmod 0700 "$DATA_DIR/tmp" "$SECRETS_DIR"

if [ ! -s "$ENV_FILE" ]; then
  glpi_db_password="$(openssl rand -base64 48 | tr -d '\n')"
  glpi_db_root_password="$(openssl rand -base64 48 | tr -d '\n')"
  kestra_db_password="$(openssl rand -base64 48 | tr -d '\n')"
  glpi_admin_password="$(openssl rand -base64 48 | tr -d '\n')"
  kestra_admin_password="$(openssl rand -base64 48 | tr -d '\n')"
  kestra_encryption_key="$(openssl rand -base64 32 | tr -d '\n')"
  docker_gid="$(getent group docker | cut -d: -f3)"
  {
    printf 'GLPI_DB_PASSWORD=%s\n' "$glpi_db_password"
    printf 'GLPI_DB_ROOT_PASSWORD=%s\n' "$glpi_db_root_password"
    printf 'KESTRA_DB_PASSWORD=%s\n' "$kestra_db_password"
    printf 'GLPI_ADMIN_PASSWORD=%s\n' "$glpi_admin_password"
    printf 'KESTRA_ADMIN_PASSWORD=%s\n' "$kestra_admin_password"
    printf 'KESTRA_ADMIN_USERNAME=%s\n' 'admin@cosmotech.example'
    printf 'KESTRA_ENCRYPTION_SECRET_KEY=%s\n' "$kestra_encryption_key"
    printf 'DOCKER_GID=%s\n' "$docker_gid"
  } > "$ENV_FILE"
  unset glpi_db_password glpi_db_root_password kestra_db_password glpi_admin_password kestra_admin_password kestra_encryption_key docker_gid
fi
chmod 0600 "$ENV_FILE"

set -a
# The generated values are base64 strings, so sourcing this protected file is shell-safe.
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [ ! -s "$APP_CONFIG" ]; then
  envsubst < "$SCRIPT_DIR/application.yaml.template" > "$APP_CONFIG"
  chown root:docker "$APP_CONFIG"
  chmod 0440 "$APP_CONFIG"
fi

bash "$SCRIPT_DIR/prepare-glpi-session-fix.sh"
"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" up -d

for _ in $(seq 1 90); do
  if curl --silent --fail --max-time 3 http://127.0.0.1:8080/ >/dev/null; then
    break
  fi
  sleep 10
done
curl --silent --fail --max-time 5 http://127.0.0.1:8080/ >/dev/null

# GLPI's installer creates well-known sample users. Rotate the administrator
# password and disable the unused sample accounts before declaring bootstrap done.
if [ ! -e "$IDENTITY_MARKER" ] && [ ! -e /var/lib/cosmotech-itsm-apps-ready ]; then
  "${COMPOSE[@]}" exec -T glpi php bin/console user:reset_password --no-interaction --password "$GLPI_ADMIN_PASSWORD" glpi >/dev/null
  for username in tech normal post-only; do
    "${COMPOSE[@]}" exec -T glpi php bin/console user:disable --no-interaction "$username" >/dev/null
  done
  # Only initial provisioning may write bootstrap administrator credentials.
  aws ssm put-parameter --name /cosmotech/itsm/glpi/admin-password --type SecureString --overwrite --value "$GLPI_ADMIN_PASSWORD" >/dev/null
  aws ssm put-parameter --name /cosmotech/itsm/kestra/admin-password --type SecureString --overwrite --value "$KESTRA_ADMIN_PASSWORD" >/dev/null
fi
touch "$IDENTITY_MARKER"

unset GLPI_DB_PASSWORD GLPI_DB_ROOT_PASSWORD KESTRA_DB_PASSWORD GLPI_ADMIN_PASSWORD KESTRA_ADMIN_PASSWORD KESTRA_ENCRYPTION_SECRET_KEY
bash "$SCRIPT_DIR/install-public-proxy.sh"
touch /var/lib/cosmotech-itsm-apps-ready
printf '%s\n' 'CosmoTech ITSM stack is ready; credentials were written to encrypted SSM Parameter Store.'
