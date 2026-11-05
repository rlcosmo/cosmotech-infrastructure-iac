"""Enroll active TLS agents and configure AWS/EKS/APM monitoring without logging credentials."""
from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

HOSTS = [
    ('cosmotech-zabbix-dev', '10.20.11.207'),
    ('cosmotech-itsm-dev', '10.20.11.150'),
    ('cosmotech-argocd-dev', '10.20.11.199'),
]
PROMETHEUS = 'https://prometheus-aws-eks-istio-lab-dev.cosmotechsolutions.com.br'


class Zabbix:
    def __init__(self) -> None:
        self.token = None
        self.protected = []
        value = json.loads(Path('/opt/cosmotech/zabbix/secrets/credentials.json').read_text())
        self.protected.append(value['password'])
        self.token = self.api('user.login', {'username': value['username'], 'password': value['password']})

    def api(self, method: str, params: dict | list):
        headers = {'Content-Type': 'application/json-rpc'}
        if self.token:
            headers['Authorization'] = 'Bearer '+self.token
        request = urllib.request.Request('http://127.0.0.1:8088/api_jsonrpc.php',
            data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            value = json.load(response)
        if 'error' in value:
            detail = json.dumps(value['error'])
            for secret in self.protected:
                detail = detail.replace(secret, '<redacted>')
            raise RuntimeError(method+': '+detail)
        return value['result']

    def group(self, name: str) -> str:
        found = self.api('hostgroup.get', {'filter': {'name': name}})
        return found[0]['groupid'] if found else self.api('hostgroup.create', {'name': name})['groupids'][0]

    def host(self, name: str, group: str, **extra) -> str:
        found = self.api('host.get', {'filter': {'host': name}, 'output': ['hostid']})
        value = {'host': name, 'name': name, 'groups': [{'groupid': group}],
            'tags': [{'tag': 'owner', 'value': 'cosmotech'}], **extra}
        if found:
            value['hostid'] = found[0]['hostid']
            self.api('host.update', value)
            return value['hostid']
        return self.api('host.create', value)['hostids'][0]

    def item(self, host: str, key: str, name: str, **extra) -> str:
        value = {'hostid': host, 'key_': key, 'name': name, 'delay': '5m', 'value_type': 0,
            'history': '7d', 'trends': '30d', 'tags': [{'tag': 'scope', 'value': 'cosmotech'}], **extra}
        for step in value.get('preprocessing', []):
            step.setdefault('error_handler', 0)
            step.setdefault('error_handler_params', '')
        found = self.api('item.get', {'hostids': [host], 'filter': {'key_': key}, 'output': ['itemid']})
        if found:
            value['itemid'] = found[0]['itemid']
            value.pop('hostid', None)
            self.api('item.update', value)
            return value['itemid']
        return self.api('item.create', value)['itemids'][0]

    def dependent(self, host: str, master: str, key: str, name: str, path: str, **extra) -> str:
        return self.item(host, key, name, type=18, master_itemid=master, delay='0',
            preprocessing=[{'type': 12, 'params': path}], **extra)

    def trigger(self, host: str, name: str, expression: str, severity: int = 3) -> None:
        found = self.api('trigger.get', {'hostids': [host], 'filter': {'description': name}, 'output': ['triggerid']})
        value = {'description': name, 'expression': expression, 'priority': severity,
            'tags': [{'tag': 'owner', 'value': 'cosmotech'}]}
        if found:
            self.api('trigger.update', {'triggerid': found[0]['triggerid'], **value})
        else:
            self.api('trigger.create', value)


def secret(name: str) -> dict:
    result = subprocess.run(['aws', '--region', 'us-east-1', 'secretsmanager', 'get-secret-value',
        '--secret-id', name, '--output', 'json'], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError('Agent enrollment secret unavailable; sensitive diagnostics omitted')
    return json.loads(json.loads(result.stdout)['SecretString'])


def enroll_agents(z: Zabbix, group: str) -> list[str]:
    templates = z.api('template.get', {'output': ['templateid', 'host'], 'filter': {
        'host': ['Linux by Zabbix agent active', 'Linux by Zabbix agent', 'Zabbix server health']}})
    ids = {t['host']: t['templateid'] for t in templates}
    result = []
    for hostname, ip in HOSTS:
        identity = secret('cosmotech/zabbix/agents/'+hostname)
        z.protected.append(identity['psk'])
        existing = z.api('host.get', {'filter': {'host': hostname}, 'output': ['hostid'],
            'selectInterfaces': ['interfaceid', 'type', 'main', 'useip', 'ip', 'dns', 'port'], 'selectParentTemplates': ['templateid']})
        if not existing and hostname == 'cosmotech-zabbix-dev':
            existing = z.api('host.get', {'filter': {'host': 'Zabbix server'}, 'output': ['hostid'],
                'selectInterfaces': ['interfaceid', 'type', 'main', 'useip', 'ip', 'dns', 'port'], 'selectParentTemplates': ['templateid']})
        linked = [{'templateid': ids['Linux by Zabbix agent active']}]
        if hostname == 'cosmotech-zabbix-dev':
            linked.append({'templateid': ids['Zabbix server health']})
        value = {'host': hostname, 'name': hostname, 'groups': [{'groupid': group}], 'templates': linked,
            'tls_connect': 2, 'tls_accept': 2, 'tls_psk_identity': identity['psk_identity'], 'tls_psk': identity['psk'],
            'tags': [{'tag': 'component', 'value': 'EC2'}, {'tag': 'owner', 'value': 'cosmotech'}]}
        if existing:
            hostid = existing[0]['hostid'];value['hostid'] = hostid
            if any(t['templateid'] == ids['Linux by Zabbix agent'] for t in existing[0]['parentTemplates']):
                value['templates_clear'] = [{'templateid': ids['Linux by Zabbix agent']}]
            value['interfaces'] = [{**interface, **({'useip': 1, 'ip': ip, 'dns': '', 'port': '10050'} if interface['type']=='1' else {})} for interface in existing[0]['interfaces']]
            z.api('host.update', value)
        else:
            value['interfaces'] = [{'type': 1, 'main': 1, 'useip': 1, 'ip': ip, 'dns': '', 'port': '10050'}]
            hostid = z.api('host.create', value)['hostids'][0]
        z.item(hostid, 'systemd.unit.info[zabbix-agent2.service,ActiveState]', 'Zabbix Agent 2 service state',
            type=7, value_type=1, trends='0', delay='1m')
        result.append(hostname)
    return result



def configure_optional_server_checks(z: Zabbix) -> None:
    """Match optional server-health items to features explicitly enabled in this Compose deployment."""
    config = json.loads(subprocess.check_output(['docker', 'compose', 'config', '--format', 'json'],
        cwd='/opt/cosmotech/zabbix', text=True))
    environment = config['services']['server'].get('environment', {})
    features = {
        'ZBX_STARTCONNECTORS': ['zabbix[connector_queue]', 'zabbix[process,connector manager,avg,busy]', 'zabbix[process,connector worker,avg,busy]'],
        'ZBX_STARTIPMIPOLLERS': ['zabbix[process,ipmi poller,avg,busy]', 'zabbix[process,ipmi manager,avg,busy]'],
        'ZBX_STARTJAVAPOLLERS': ['zabbix[process,java poller,avg,busy]'],
        'ZBX_STARTSNMPTRAPPER': ['zabbix[process,snmp trapper,avg,busy]'],
        'ZBX_STARTVMWARECOLLECTORS': ['zabbix[process,vmware collector,avg,busy]', 'zabbix[vmware,buffer,pused]'],
        'ZBX_STARTREPORTWRITERS': ['zabbix[process,report writer,avg,busy]', 'zabbix[process,report manager,avg,busy]'],
    }
    host = z.api('host.get', {'filter': {'host': 'cosmotech-zabbix-dev'}, 'output': ['hostid']})[0]['hostid']
    for setting, keys in features.items():
        status = 0 if int(environment.get(setting) or '0') > 0 else 1
        for item in z.api('item.get', {'hostids': [host], 'filter': {'key_': keys}, 'output': ['itemid','status']}):
            if int(item['status']) != status:
                z.api('item.update', {'itemid': item['itemid'], 'status': status})


def configure_aws(z: Zabbix, group: str) -> dict:
    name = 'cosmotech-aws-account';host = z.host(name, group)
    master = z.item(host, 'cosmotech.aws.metrics', 'AWS inventory and platform service metrics', type=19,
        value_type=4, trends='0', history='1d', url='http://10.20.11.150:8088/lab-metrics', timeout='10s')
    def metric(key: str, title: str, pattern: str, **extra):
        return z.item(host, key, title, type=18, master_itemid=master, delay='0',
            preprocessing=[{'type': 5, 'params': '(?m)^'+pattern+r'\s+([-+0-9.eE]+)$'+'\n'+r'\1'}], **extra)
    cost = metric('cosmotech.aws.cost.net', 'AWS month-to-date net cost', 'lab_aws_cost_mtd_net_usd', units='USD')
    for service in ['S3','ECR','DynamoDB','SQS','Lambda','ALB/NLB','NAT Gateway','VPC','Elastic IP','RDS',
                    'Backup vaults','SNS','Secrets Manager','KMS','ACM','CloudWatch Logs','CodePipeline','CodeBuild','WAF',
                    'EC2 sem nodes EKS','EBS sem volumes dos nodes EKS']:
        key = re.sub('[^a-z0-9]+', '.', service.lower()).strip('.')
        metric('cosmotech.aws.inventory.'+key, 'AWS inventory: '+service,
            'lab_aws_inventory_resources'+re.escape('{aws_service="'+service+'"}'), value_type=3)
    for status in ['ALARM','OK','INSUFFICIENT_DATA']:
        metric('cosmotech.aws.alarms.'+status.lower(), 'CloudWatch alarms: '+status,
            'lab_aws_cloudwatch_alarms'+re.escape('{state="'+status+'"}'), value_type=3)
    service_items = {}
    for state, label in [(0,'down'),(-1,'unknown'),(1,'up')]:
        js = "var lines=value.split('\\n'),count=0;for(var i=0;i<lines.length;i++){if(lines[i].indexOf('lab_ec2_application_service_status{')===0 && Number(lines[i].split(' ').pop())==="+str(state)+")count++;}return count;"
        service_items[label] = z.item(host, 'cosmotech.services.'+label, 'EC2 application services '+label,
            type=18, master_itemid=master, delay='0', value_type=3, preprocessing=[{'type': 21, 'params': js}])
    z.trigger(host, 'CosmoTech: application service is down', f'last(/{name}/cosmotech.services.down)>0')
    z.trigger(host, 'CosmoTech: application service probe is unknown', f'last(/{name}/cosmotech.services.unknown)>0', 2)
    z.trigger(host, 'CosmoTech: AWS/platform monitoring data missing', f'nodata(/{name}/cosmotech.aws.metrics,15m)=1')
    return {'AWS net cost': cost, 'EC2 services down': service_items['down']}


def configure_eks(z: Zabbix, group: str) -> dict:
    name = 'cosmotech-eks-cluster';host = z.host(name, group)
    queries = {
        'nodes_ready': 'sum(kube_node_status_condition{condition="Ready",status="true"})',
        'nodes_total': 'count(kube_node_info)',
        'pods_running': 'sum(kube_pod_status_phase{phase="Running"})',
        'pods_pending': 'sum(kube_pod_status_phase{phase="Pending"})',
        'pods_failed': 'sum(kube_pod_status_phase{phase="Failed"})',
        'deployments_unavailable': 'sum(kube_deployment_status_replicas_unavailable)',
        'api_ready': 'sum(kube_deployment_status_replicas_available{namespace="orders-api",deployment="orders-api"})',
        'worker_ready': 'sum(kube_deployment_status_replicas_available{namespace="orders-worker",deployment="orders-worker"})',
        'node_cpu_percent': '100 - 100*avg(rate(node_cpu_seconds_total{mode="idle"}[5m]))',
        'node_disk_max_percent': 'max(100*(1-node_filesystem_avail_bytes{fstype!~"tmpfs|overlay",mountpoint="/"}/node_filesystem_size_bytes{fstype!~"tmpfs|overlay",mountpoint="/"}))',
    }
    query = ' or '.join('label_replace(('+v+'),"check","'+k+'","","")' for k,v in queries.items())
    master = z.item(host, 'cosmotech.eks.metrics', 'EKS readiness, workload and node metrics', type=19,
        value_type=4, trends='0', history='1d', timeout='10s', verify_peer=1, verify_host=1,
        url=PROMETHEUS+'/api/v1/query?'+urllib.parse.urlencode({'query': query}))
    widgets = {}
    for key in queries:
        itemid = z.dependent(host, master, 'cosmotech.eks.'+key, 'EKS '+key.replace('_',' '),
            '$.data.result[?(@.metric.check=="'+key+'")].value[1].first()', units='%' if 'percent' in key else '')
        if key in ['nodes_ready','pods_running','api_ready','worker_ready']:
            widgets['EKS '+key] = itemid
    z.trigger(host, 'EKS: one or more nodes are not Ready', f'last(/{name}/cosmotech.eks.nodes_ready)<last(/{name}/cosmotech.eks.nodes_total)')
    z.trigger(host, 'EKS: unavailable deployment replicas', f'last(/{name}/cosmotech.eks.deployments_unavailable)>0', 2)
    z.trigger(host, 'EKS: API or worker has no available replica', f'last(/{name}/cosmotech.eks.api_ready)<1 or last(/{name}/cosmotech.eks.worker_ready)<1')
    z.trigger(host, 'EKS: node disk usage above 90 percent', f'last(/{name}/cosmotech.eks.node_disk_max_percent)>90')
    z.trigger(host, 'EKS: telemetry endpoint missing', f'nodata(/{name}/cosmotech.eks.metrics,15m)=1')
    return widgets


def configure_apm(z: Zabbix, group: str) -> dict:
    password = Path('/opt/cosmotech/zabbix/secrets/clickhouse_password').read_text().strip()
    z.protected.append(password);widgets = {}
    for service in ['orders-api','orders-worker']:
        name = 'cosmotech-apm-'+service;host = z.host(name, group)
        query = ("SELECT count() AS spans, countIf(StatusCode='Error') AS errors, "
            "if(count()=0,0,round(quantile(0.95)(Duration)/1000000,2)) AS p95_ms, "
            "round(100*countIf(StatusCode='Error')/greatest(count(),1),2) AS error_percent "
            "FROM zabbix.otel_traces WHERE ServiceName='"+service+"' AND Timestamp>now()-INTERVAL 15 MINUTE FORMAT JSON")
        master = z.item(host, 'cosmotech.apm.stats', service+' APM last 15 minutes', type=19,
            value_type=4, trends='0', history='1d', timeout='10s',
            url='http://clickhouse:8123/?'+urllib.parse.urlencode({'query': query}),
            authtype=1, username='zabbix', password=password)
        for metric, title, unit in [('spans','spans in 15m',''),('errors','error spans in 15m',''),('p95_ms','p95 latency','ms'),('error_percent','error percentage','%')]:
            itemid = z.dependent(host, master, 'cosmotech.apm.'+metric, service+' '+title, '$.data[0].'+metric, units=unit)
            if metric in ['spans','p95_ms']:
                widgets[service+' '+title] = itemid
        z.trigger(host, service+': APM received no spans in 15 minutes', f'last(/{name}/cosmotech.apm.spans)=0')
        z.trigger(host, service+': sustained APM error percentage above 20', f'count(/{name}/cosmotech.apm.error_percent,15m)>=3 and min(/{name}/cosmotech.apm.error_percent,15m)>20')
        z.trigger(host, service+': APM p95 latency above 5 seconds', f'last(/{name}/cosmotech.apm.p95_ms)>5000', 2)
        z.trigger(host, service+': APM storage query unavailable', f'nodata(/{name}/cosmotech.apm.stats,15m)=1')
    return widgets


def configure_web(z: Zabbix, group: str) -> None:
    host = z.host('cosmotech-platform-web', group)
    targets = {'Orders API': 'https://orders-api-dev.cosmotechsolutions.com.br/health/ready',
        'Orders Worker': 'https://orders-worker-dev.cosmotechsolutions.com.br/health/ready',
        'Grafana': 'https://grafana.cosmotechsolutions.com.br/', 'Argo CD': 'https://argocd.cosmotechsolutions.com.br/',
        'GLPI': 'https://glpi.cosmotechsolutions.com.br/', 'Kestra': 'https://kestra.cosmotechsolutions.com.br/ui/',
        'Zabbix': 'https://zabbix.cosmotechsolutions.com.br/', 'Prometheus': PROMETHEUS+'/-/ready',
        'Tempo': 'https://tempo.cosmotechsolutions.com.br/ready'}
    for name,url in targets.items():
        title = 'CosmoTech '+name
        value = {'hostid': host, 'name': title, 'delay': '5m', 'verify_peer': 1, 'verify_host': 1,
            'steps': [{'name': 'HTTPS ready', 'url': url, 'no': 1, 'timeout': '10s', 'status_codes': '200', 'follow_redirects': 1}]}
        found = z.api('httptest.get', {'hostids': [host], 'filter': {'name': title}, 'output': ['httptestid']})
        if found:
            value.pop('hostid', None)
            z.api('httptest.update', {'httptestid': found[0]['httptestid'], **value})
        else:
            z.api('httptest.create', value)
        z.trigger(host, title+': HTTPS unavailable', f'last(/cosmotech-platform-web/web.test.fail[{title}])<>0')


def dashboard(z: Zabbix, widgets: dict) -> str:
    name = 'CosmoTech | AWS EKS EC2 e APM'
    tiles = [{'type': 'item', 'name': title, 'x': (i%3)*24, 'y': (i//3)*4, 'width': 24, 'height': 4,
        'fields': [{'type': 4, 'name': 'itemid.0', 'value': itemid}]} for i,(title,itemid) in enumerate(widgets.items())]
    found = z.api('dashboard.get', {'filter': {'name': name}, 'output': ['dashboardid']})
    value = {'name': name, 'pages': [{'name': 'Resumo', 'widgets': tiles}]}
    if found:
        z.api('dashboard.update', {'dashboardid': found[0]['dashboardid'], **value})
        return found[0]['dashboardid']
    return z.api('dashboard.create', value)['dashboardids'][0]


def main() -> None:
    z = Zabbix()
    group = z.group('CosmoTech / Platform')
    agents = enroll_agents(z, group)
    configure_optional_server_checks(z)
    widgets = {**configure_aws(z, group), **configure_eks(z, group), **configure_apm(z, group)}
    configure_web(z, group)
    dashboard_id = dashboard(z, widgets)
    z.api('user.logout', [])
    subprocess.run(['docker','compose','exec','-T','server','zabbix_server','-R','config_cache_reload'],
        cwd='/opt/cosmotech/zabbix', check=True, capture_output=True)
    print(json.dumps({'agents': agents, 'aws_inventory': True, 'eks_monitoring': True,
        'apm_services': ['orders-api','orders-worker'], 'https_checks': 9, 'dashboard_id': dashboard_id}))


if __name__ == '__main__':
    main()
