"""Copy existing protected host runtime credentials to fixed Secrets Manager destinations."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def aws(operation: str, name: str, value: dict | None = None) -> dict:
    args = ['aws', '--region', 'us-east-1', 'secretsmanager', operation, '--secret-id', name, '--output', 'json']
    if value is not None:
        args += ['--secret-string', 'file:///dev/stdin']
    result = subprocess.run(args, input=json.dumps(value) if value is not None else None,
                            text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f'Credential export {operation} failed; sensitive diagnostics omitted')
    return json.loads(result.stdout)


def environment(path: str) -> dict:
    return dict(line.split('=', 1) for line in Path(path).read_text().splitlines()
                if '=' in line and not line.startswith('#'))


def publish(name: str, value: dict) -> None:
    result = aws('put-secret-value', name, value)
    print(json.dumps({'secret_id': name, 'version_id': result['VersionId'], 'exported': True}))


def export_itsm() -> None:
    source = '/opt/cosmotech/secrets/itsm/runtime.env'
    values = environment(source)
    publish('cosmotech/itsm/runtime', {'source': source, 'source_type': 'existing_bootstrap_runtime',
        'databases': {
            'glpi': {'username': 'glpi', 'password': values['GLPI_DB_PASSWORD']},
            'glpi_root': {'username': 'root', 'password': values['GLPI_DB_ROOT_PASSWORD']},
            'kestra': {'username': 'kestra', 'password': values['KESTRA_DB_PASSWORD']}},
        'bootstrap_accounts': {
            'glpi': {'username': 'glpi', 'password': values['GLPI_ADMIN_PASSWORD']},
            'kestra': {'username': values['KESTRA_ADMIN_USERNAME'], 'password': values['KESTRA_ADMIN_PASSWORD']}},
        'encryption_keys': {'kestra': values['KESTRA_ENCRYPTION_SECRET_KEY']}})
    source = '/opt/cosmotech/secrets/automation/kestra-oss/runtime.env'
    values = environment(source)
    publish('cosmotech/kestra/automation-runtime', {'source': source,
        'account': {'username': values['AUTOMATION_USERNAME'], 'password': values['AUTOMATION_PASSWORD']},
        'database': {'username': 'kestra_automation', 'password': values['AUTOMATION_DB_PASSWORD']},
        'encryption_key': values['AUTOMATION_ENCRYPTION_KEY']})


def export_zabbix() -> None:
    name = 'cosmotech/zabbix/credentials'
    value = json.loads(aws('get-secret-value', name)['SecretString'])
    root = Path('/opt/cosmotech/zabbix/secrets')
    value['runtime_databases'] = {service: {'username': 'zabbix', 'database': 'zabbix',
        'password': (root / filename).read_text().strip()}
        for service, filename in [('postgres', 'postgres_password'), ('clickhouse', 'clickhouse_password')]}
    publish(name, value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', choices=['itsm', 'zabbix'], required=True)
    args = parser.parse_args()
    if args.profile == 'itsm':
        export_itsm()
    else:
        export_zabbix()


if __name__ == '__main__':
    main()
