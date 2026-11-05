"""Reconcile native AWS discovery and signal coverage without long-lived AWS keys."""
from __future__ import annotations

import json
import os
import subprocess
import urllib.parse
from pathlib import Path

from configure_monitoring import HOSTS, Zabbix

AWS_ROLE = 'arn:aws:iam::414053644145:role/cosmotech-zabbix-aws-monitoring'


def save_dashboard(z: Zabbix, name: str, pages: list[dict]) -> str:
    found = z.api('dashboard.get', {'filter': {'name': name}, 'output': ['dashboardid']})
    value = {'name': name, 'pages': pages}
    if found:
        z.api('dashboard.update', {'dashboardid': found[0]['dashboardid'], **value})
        return found[0]['dashboardid']
    return z.api('dashboard.create', value)['dashboardids'][0]


def tile(title: str, item: str, index: int) -> dict:
    return {'type': 'item', 'name': title, 'x': index % 3 * 24, 'y': index // 3 * 4,
            'width': 24, 'height': 4, 'fields': [{'type': 4, 'name': 'itemid.0', 'value': item}]}


def fix_backup_script(script: str) -> str:
    """Backup scripts include a canonical URI argument; their rc3 STS call omitted it."""
    return script.replace("AWS.request('GET', AWS.params.sts_region, 'sts', AWS.prepareParams(payload))",
                          "AWS.request('GET', '/', AWS.params.sts_region, 'sts', AWS.prepareParams(payload))")


def optional_preprocessing(steps: list[dict]) -> list[dict]:
    """Discard only absent/null optional fields; preserve transforms with fixed error semantics."""
    return [{**step, 'error_handler': 1, 'error_handler_params': ''}
            if index == 0 or str(step.get('error_handler', 0)) == '2' else dict(step)
            for index, step in enumerate(steps)]


def repair_aws_template_defaults(z: Zabbix, group: str) -> None:
    """Correct rc3 HTTP-only prototype interfaces, unique names and missing role parameter."""
    template = z.api('template.get', {'filter': {'host': 'AWS by HTTP'}, 'output': ['templateid']})[0]['templateid']
    rules = z.api('discoveryrule.get', {'templateids': [template], 'output': ['itemid', 'key_', 'type', 'parameters', 'params']})
    for rule in rules:
        prototypes = z.api('hostprototype.get', {'discoveryids': [rule['itemid']], 'output': ['hostid', 'host', 'name', 'custom_interfaces']})
        for prototype in prototypes:
            value = {'hostid': prototype['hostid'], 'custom_interfaces': 0,
                     'groupLinks': [{'groupid': group}]}
            if rule['key_'] == 'aws.ec2.discovery':
                value['name'] = 'EC2 - {#AWS.EC2.INSTANCE.NAME} [{#AWS.EC2.INSTANCE.ID}]'
            z.api('hostprototype.update', value)
        if rule['key_'] == 'aws.backup_vault.discovery':
            parameters = [{'name': p['name'], 'value': p['value']} for p in rule['parameters']]
            if not any(p['name'] == 'metadata_auth' for p in parameters):
                parameters.append({'name': 'metadata_auth', 'value': '{$AWS.ASSUME.ROLE.AUTH.METADATA}'})
            z.api('discoveryrule.update', {'itemid': rule['itemid'], 'parameters': parameters,
                                            'params': fix_backup_script(rule['params'])})
    backup = z.api('template.get', {'filter': {'host': 'AWS Backup Vault by HTTP'}, 'output': ['templateid']})
    if backup:
        for item in z.api('item.get', {'templateids': [backup[0]['templateid']], 'filter': {'type': 21},
                                      'output': ['itemid', 'parameters', 'params']}):
            parameters = [{'name': p['name'], 'value': p['value']} for p in item['parameters']]
            if not any(p['name'] == 'metadata_auth' for p in parameters):
                parameters.append({'name': 'metadata_auth', 'value': '{$AWS.ASSUME.ROLE.AUTH.METADATA}'})
            z.api('item.update', {'itemid': item['itemid'], 'parameters': parameters,
                                  'params': fix_backup_script(item['params'])})


    if backup:
        optional_keys = ['aws.backup_vault.state', 'aws.backup_vault.retention.min',
                         'aws.backup_vault.retention.max', 'aws.backup_vault.lock.time_left', 'aws.backup_vault.lock.date']
        optional = z.api('item.get', {'templateids': [backup[0]['templateid']],
                         'filter': {'key_': optional_keys}, 'output': ['itemid'], 'selectPreprocessing': 'extend'})
        for item in optional:
            steps = optional_preprocessing(item['preprocessing'])
            # An ordinary unlocked vault legitimately omits air-gapped/retention-lock fields.
            # Discard absent optional values; keep the independent master API-error trigger.
            z.api('item.update', {'itemid': item['itemid'], 'preprocessing': steps})


def native_discovery(z: Zabbix) -> str:
    group = z.group('CosmoTech / AWS Discovery')
    repair_aws_template_defaults(z, z.group('CosmoTech / AWS Resources'))
    template = z.api('template.get', {'filter': {'host': 'AWS by HTTP'}, 'output': ['templateid']})[0]['templateid']
    macros = {'{$AWS.AUTH_TYPE}': 'assume_role', '{$AWS.ASSUME.ROLE.AUTH.METADATA}': 'true',
              '{$AWS.ASSUME.ROLE.ARN}': AWS_ROLE, '{$AWS.DESCRIBE.REGION}': 'us-east-1',
              '{$AWS.STS.REGION}': 'us-east-1', '{$AWS.REQUEST.REGION}': 'us-east-1'}
    for service in ['EC2', 'ECS', 'ELB', 'LAMBDA', 'RDS', 'BACKUP_VAULT']:
        macros['{$AWS.'+service+'.LLD.FILTER.REGION.MATCHES}'] = '^us-east-1$'
    host = z.host('cosmotech-aws-discovery-us-east-1', group, templates=[{'templateid': template}],
                  macros=[{'macro': k, 'value': v} for k, v in macros.items()])
    masters = z.api('item.get', {'hostids': [host], 'filter': {'key_': 'aws.ec2.get'}, 'output': ['itemid']})
    z.api('item.update', {'itemid': masters[0]['itemid'], 'delay': '5m'})
    rules = z.api('discoveryrule.get', {'hostids': [host], 'output': ['itemid', 'key_', 'type']})
    for rule in rules:
        if rule['type'] != '18':
            z.api('discoveryrule.update', {'itemid': rule['itemid'], 'delay': '15m'})
    z.trigger(host, 'AWS: EC2 discovery data missing for 15 minutes',
              'nodata(/cosmotech-aws-discovery-us-east-1/aws.ec2.get,15m)=1')
    return host


def signal_coverage(z: Zabbix) -> dict[str, str]:
    password = Path('/opt/cosmotech/zabbix/secrets/clickhouse_password').read_text().strip()
    z.protected.append(password)
    widgets = {}
    group = z.group('CosmoTech / Platform')
    for service in ['orders-api', 'orders-worker']:
        name = 'cosmotech-apm-'+service
        host = z.host(name, group)
        predicate = "ServiceName='"+service+"' AND "
        recent = ' > now()-INTERVAL 15 MINUTE'
        metric_sql = ' UNION ALL '.join('SELECT count() AS n FROM zabbix.otel_metrics_'+kind+
                    ' WHERE '+predicate+'TimeUnix'+recent for kind in ['sum', 'gauge', 'histogram', 'exponential_histogram'])
        query = ("SELECT (SELECT count() FROM zabbix.otel_traces WHERE "+predicate+'Timestamp'+recent+
                 ') AS traces, (SELECT sum(n) FROM ('+metric_sql+')) AS metric_points, '+
                 '(SELECT count() FROM zabbix.otel_logs WHERE '+predicate+'Timestamp'+recent+
                 ') AS logs, (SELECT count() FROM zabbix.otel_logs WHERE '+predicate+'Timestamp'+recent+
                 " AND (SeverityNumber>=17 OR SeverityText IN ('ERROR','CRITICAL'))) AS log_errors FORMAT JSON")
        master = z.item(host, 'cosmotech.apm.signals', service+' traces, metrics and logs in 15m', type=19,
                        value_type=4, trends='0', history='1d', timeout='10s',
                        url='http://clickhouse:8123/?'+urllib.parse.urlencode({'query': query}),
                        authtype=1, username='zabbix', password=password)
        for key, title in [('traces', 'Traces / 15m'), ('metric_points', 'Metric points / 15m'),
                           ('logs', 'Logs / 15m'), ('log_errors', 'Error logs / 15m')]:
            widgets[service+' | '+title] = z.dependent(host, master, 'cosmotech.apm.signal.'+key,
                                                       title, '$.data[0].'+key, value_type=3)
        for key, signal in [('metric_points', 'metrics'), ('logs', 'logs')]:
            z.trigger(host, service+': APM '+signal+' missing for 15 minutes',
                      f'last(/{name}/cosmotech.apm.signal.{key})=0', 2)
        z.trigger(host, service+': APM log errors detected', f'last(/{name}/cosmotech.apm.signal.log_errors)>0', 2)
    return widgets


def aws_vault_configuration(name: str) -> dict:
    """Read optional configuration with a short-lived role, never persisting credentials."""
    result = subprocess.run(['aws', '--region', 'us-east-1', 'sts', 'assume-role',
                             '--role-arn', AWS_ROLE, '--role-session-name', 'zabbix-vault-reconciliation'],
                            capture_output=True, text=True, timeout=40, check=False)
    if result.returncode:
        raise RuntimeError('Vault reconciliation role unavailable; sensitive diagnostics omitted')
    credentials = json.loads(result.stdout)['Credentials']
    env = {**os.environ, 'AWS_ACCESS_KEY_ID': credentials['AccessKeyId'],
           'AWS_SECRET_ACCESS_KEY': credentials['SecretAccessKey'], 'AWS_SESSION_TOKEN': credentials['SessionToken']}
    result = subprocess.run(['aws', '--region', 'us-east-1', 'backup', 'describe-backup-vault',
                             '--backup-vault-name', name], env=env, capture_output=True, text=True,
                            timeout=40, check=False)
    if result.returncode:
        raise RuntimeError('Vault configuration cannot be read; collection errors are not suppressed')
    return json.loads(result.stdout)


def configure_optional_vault_items(z: Zabbix) -> None:
    """Enable lock/retention fields only when the AWS response provides them."""
    rows = z.api('host.get', {'output': ['hostid', 'host'], 'selectParentTemplates': ['name']})
    mapping = {'aws.backup_vault.retention.min': 'MinRetentionDays',
               'aws.backup_vault.retention.max': 'MaxRetentionDays',
               'aws.backup_vault.lock.time_left': 'LockDate',
               'aws.backup_vault.lock.date': 'LockDate'}
    for host in rows:
        if not any(t['name'] == 'AWS Backup Vault by HTTP' for t in host['parentTemplates']):
            continue
        data = aws_vault_configuration(host['host'])
        if 'error' in data or 'BackupVaultName' not in data:
            continue  # Never silence an authentication or collection failure.
        items = z.api('item.get', {'hostids': [host['hostid']], 'filter': {'key_': list(mapping)},
                                  'output': ['itemid', 'key_', 'status']})
        for item in items:
            status = 1 if data.get(mapping[item['key_']]) is None else 0
            if int(item['status']) != status:
                z.api('item.update', {'itemid': item['itemid'], 'status': status})


def dashboards(z: Zabbix, controller: str, signals: dict[str, str]) -> dict[str, str]:
    signal_tiles = [tile(title, item, i) for i, (title, item) in enumerate(signals.items())]
    apm_id = save_dashboard(z, 'CosmoTech | APM - Traces Metrics Logs', [
        {'name': 'Cobertura dos três sinais', 'widgets': signal_tiles}])
    cloud_tiles = []
    items = z.api('item.get', {'hostids': [controller], 'filter': {'key_': 'aws.ec2.count'}, 'output': ['itemid']})
    if items:
        cloud_tiles.append(tile('EC2 descobertas automaticamente', items[0]['itemid'], 0))
    cloud_tiles += [{'type': 'problems', 'name': 'Problemas de infraestrutura e aplicações', 'x': 0, 'y': 4,
                     'width': 72, 'height': 8, 'fields': []}]
    rows = z.api('host.get', {'output': ['hostid', 'host', 'name'], 'selectTags': ['tag', 'value'], 'selectParentTemplates': ['name']})
    metrics = []
    for host in rows:
        templates = {t['name'] for t in host['parentTemplates']}
        if not any(t.startswith('AWS ') for t in templates) or host['hostid'] == controller:
            continue
        for key in ['aws.ec2.cpu_utilization', 'aws.ec2.status_check_failed', 'aws.lambda.errors.sum', 'aws.elb.alb.http_5xx_count']:
            found = z.api('item.get', {'hostids': [host['hostid']], 'filter': {'key_': key}, 'output': ['itemid', 'name']})
            if found:
                metrics.append(tile(host['name']+' | '+found[0]['name'], found[0]['itemid'], len(metrics)))
    cloud_id = save_dashboard(z, 'CosmoTech | AWS - Descoberta e infraestrutura', [
        {'name': 'Disponibilidade e problemas', 'widgets': cloud_tiles},
        *([{'name': 'EC2 e serviços AWS', 'widgets': metrics[:30]}] if metrics else [])])
    os_pages = []
    for hostname, _ in HOSTS:
        host = z.api('host.get', {'filter': {'host': hostname}, 'output': ['hostid']})[0]['hostid']
        units = ['zabbix-agent2.service', 'k3s.service' if hostname == 'cosmotech-argocd-dev' else 'docker.service']
        for unit in units:
            key = 'systemd.unit.info['+unit+',ActiveState]'
            z.item(host, key, unit+' state', type=7, value_type=1, trends='0', delay='1m')
            z.trigger(host, hostname+': '+unit+' is not active',
                      f'last(/{hostname}/{key})<>"active"', 3)
        keys = ['system.cpu.util', 'vm.memory.size[pavailable]', 'system.uptime',
                *['systemd.unit.info['+unit+',ActiveState]' for unit in units]]
        items = z.api('item.get', {'hostids': [host], 'filter': {'key_': keys}, 'output': ['itemid', 'name']})
        disks = z.api('item.get', {'hostids': [host], 'search': {'key_': 'vfs.fs.dependent.size['},
                                 'output': ['itemid', 'name', 'key_']})
        items += [i for i in disks if ',pused]' in i['key_']][:6]
        os_pages.append({'name': hostname, 'widgets': [tile(i['name'], i['itemid'], n) for n, i in enumerate(items)]})
    os_id = save_dashboard(z, 'CosmoTech | EC2 - Sistema operacional e discos', os_pages)
    return {'apm': apm_id, 'aws': cloud_id, 'ec2_os': os_id}


def main() -> None:
    z = Zabbix()
    try:
        controller = native_discovery(z)
        signals = signal_coverage(z)
        configure_optional_vault_items(z)
        ids = dashboards(z, controller, signals)
        print(json.dumps({'aws_controller': controller, 'dashboards': ids, 'auth': 'short-lived instance-role AssumeRole'}))
    finally:
        z.api('user.logout', [])
    subprocess.run(['docker', 'compose', 'exec', '-T', 'server', 'zabbix_server', '-R', 'config_cache_reload'],
                   cwd='/opt/cosmotech/zabbix', check=True, capture_output=True)


if __name__ == '__main__':
    main()
