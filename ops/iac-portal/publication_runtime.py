"""Fixed publisher for the existing ITSM host. Never emit credential values."""
from __future__ import annotations

import base64
import datetime as dt
import grp
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path('/var/lib/cosmotech-iac')
SOURCE = Path(__file__).resolve().parent
GRAFANA = 'https://grafana.cosmotechsolutions.com.br'
PORTAL = 'https://kestra.cosmotechsolutions.com.br/iac/'
FOLDER = 'cosmotech-lab-v2'
PARAM = '/cosmotech/lab-v2/'


def run(args: list[str], value: dict | None = None) -> bytes:
    result = subprocess.run(args, input=json.dumps(value).encode() if value is not None else None,
                            capture_output=True, timeout=120, check=False)
    if result.returncode:
        raise RuntimeError('Command failed; output withheld')
    return result.stdout


def aws(service: str, operation: str, *args: str, value: dict | None = None) -> dict:
    command = ['aws', '--region', 'us-east-1', '--output', 'json', '--cli-connect-timeout', '5',
               '--cli-read-timeout', '20', service, operation, *args]
    if value is not None:
        command += ['--cli-input-json', 'file:///dev/stdin']
    return json.loads(run(command, value) or b'{}')


def parameter(name: str) -> dict | None:
    command = ['aws', '--region', 'us-east-1', '--output', 'json', 'ssm', 'get-parameter',
               '--name', PARAM + name, '--with-decryption']
    result = subprocess.run(command, capture_output=True, timeout=30)
    if result.returncode:
        if b'ParameterNotFound' in result.stderr:
            return None
        raise RuntimeError('Parameter read denied or unavailable')
    return json.loads(json.loads(result.stdout)['Parameter']['Value'])


def put_parameter(name: str, value: dict, *, overwrite: bool = False, public: bool = False) -> None:
    aws('ssm', 'put-parameter', value={'Name': PARAM + name, 'Type': 'String' if public else 'SecureString',
        'Value': json.dumps(value), 'Tier': 'Standard', 'Overwrite': overwrite})


def credentials(record: dict) -> tuple[str, str]:
    """Accept only unambiguous credential records; never guess or print passwords."""
    candidates = [record, record.get('runtime_credential_record', {}), record.get('runtime', {})]
    accounts = record.get('accounts', [])
    candidates += list(accounts.values()) if isinstance(accounts, dict) else accounts if isinstance(accounts, list) else []
    for candidate in candidates:
        if isinstance(candidate, dict) and isinstance(candidate.get('username'), str) and isinstance(candidate.get('password'), str):
            return candidate['username'], candidate['password']
    raise ValueError('Unsupported credential schema')


def api(path: str, auth: str, body: dict | None = None) -> object:
    request = urllib.request.Request(GRAFANA + path, data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization': auth, 'Content-Type': 'application/json'}, method='POST' if body is not None else 'GET')
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def publisher_token() -> str:
    previous = parameter('grafana-publisher')
    if previous and previous.get('expires_at', 0) > time.time() + 86400:
        api('/api/org', 'Bearer ' + previous['token'])
        return previous['token']
    record = json.loads(aws('secretsmanager', 'get-secret-value', '--secret-id',
                            'cosmotech/grafana/credentials')['SecretString'])
    username, password = credentials(record)
    admin = 'Basic ' + base64.b64encode((username + ':' + password).encode()).decode()
    found = api('/api/serviceaccounts/search?query=cosmotech-lab-v2-publisher&perpage=100&page=1', admin)
    accounts = [a for a in found['serviceAccounts'] if a['name'] == 'cosmotech-lab-v2-publisher']
    if len(accounts) > 1:
        raise ValueError('Ambiguous publisher account')
    account = accounts[0] if accounts else api('/api/serviceaccounts', admin,
                  {'name': 'cosmotech-lab-v2-publisher', 'role': 'Viewer', 'isDisabled': False})
    if account['role'] != 'Viewer' or account.get('isDisabled'):
        raise ValueError('Publisher must be Viewer with folder-scoped edit only')
    try:
        api('/api/folders/' + FOLDER, admin)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        api('/api/folders', admin, {'uid': FOLDER, 'title': 'CosmoTech Lab V2'})
    permissions = api('/api/folders/' + FOLDER + '/permissions', admin)
    items = []
    for permission in permissions:
        if permission.get('inherited'):
            continue
        if permission.get('userId') == account['id']:
            if permission['permission'] != 2:
                raise ValueError('Unexpected publisher folder permissions')
            continue
        target = next((key for key in ('userId', 'teamId', 'role') if permission.get(key)), None)
        if target:
            items.append({target: permission[target], 'permission': permission['permission']})
    items.append({'userId': account['id'], 'permission': 2})
    api('/api/folders/' + FOLDER + '/permissions', admin, {'items': items})
    token = api(f"/api/serviceaccounts/{account['id']}/tokens", admin,
                {'name': 'lab-v2-' + str(int(time.time())), 'secondsToLive': 604800})['key']
    put_parameter('grafana-publisher', {'token': token, 'expires_at': time.time() + 604800}, overwrite=previous is not None)
    return token


def publish_dashboard(token: str) -> None:
    dashboard = json.loads((SOURCE / 'v2-sqs-workers.json').read_text())
    if dashboard['uid'] != 'cosmotech-v2-sqs-workers':
        raise ValueError('Only the fixed Lab V2 dashboard is permitted')
    auth = 'Bearer ' + token
    try:
        previous = api('/api/dashboards/uid/' + dashboard['uid'], auth)
        if previous['meta'].get('folderUid') != FOLDER:
            raise ValueError('Existing dashboard is owned by another folder')
        dashboard.update(id=previous['dashboard']['id'], version=previous['dashboard']['version'])
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
    api('/api/dashboards/db', auth, {'dashboard': dashboard, 'folderUid': FOLDER, 'overwrite': True,
                                   'message': 'Lab V2 canonical GitHub/Terraform publication'})
    verified = api('/api/dashboards/uid/' + dashboard['uid'], auth)
    if verified['meta'].get('folderUid') != FOLDER or verified['dashboard']['uid'] != dashboard['uid']:
        raise ValueError('Dashboard verification failed')


def validate_real_data(token: str) -> dict:
    auth = 'Bearer ' + token
    sources = api('/api/datasources', auth)
    matching = [s for s in sources if s['type'] == 'prometheus' and
                (s['name'] == 'prometheus-aws-eks-istio-lab-dev' or s.get('isDefault'))]
    if len(matching) != 1:
        raise ValueError('Ambiguous Prometheus datasource')
    counts = {}
    for expression in ['cosmotech_sqs_visible', 'cosmotech_sqs_inflight', 'cosmotech_sqs_oldest_age_seconds',
                       'cosmotech_sqs_dlq_visible', 'cosmotech_worker_processing_seconds_count']:
        result = api('/api/datasources/proxy/uid/' + urllib.parse.quote(matching[0]['uid'], safe='') +
                     '/api/v1/query?' + urllib.parse.urlencode({'query': expression}), auth)
        samples = result.get('data', {}).get('result', [])
        if result.get('status') != 'success' or not samples or any(not math.isfinite(float(s['value'][1])) for s in samples):
            raise ValueError('Missing or invalid live metrics; unknown is not zero')
        counts[expression] = len(samples)
    freshness = api('/api/datasources/proxy/uid/' + matching[0]['uid'] + '/api/v1/query?' +
                    urllib.parse.urlencode({'query': 'time() - cosmotech_sqs_data_timestamp_seconds'}), auth)
    samples = freshness.get('data', {}).get('result', [])
    if not samples or any(not 0 <= float(s['value'][1]) <= 180 for s in samples):
        raise ValueError('Queue evidence is stale')
    return counts


def sync_reports() -> None:
    bucket = 'aws-serverless-lab-' + aws('sts', 'get-caller-identity')['Account'] + '-tfstate-use1'
    today = dt.datetime.now(dt.timezone.utc)
    previous = today.replace(day=1) - dt.timedelta(days=1)
    objects = []
    for month in {today.strftime('%Y-%m'), previous.strftime('%Y-%m')}:
        objects.extend(aws('s3api', 'list-objects-v2', '--bucket', bucket, '--prefix',
                           'lab-v2/iac-evidence/' + month + '/').get('Contents', []))
    for item in sorted(objects, key=lambda o: o['LastModified'])[-56:]:
        key = item['Key']
        if not key.endswith('.json.gpg'):
            continue
        name = Path(key).name.removesuffix('.gpg')
        target = ROOT / 'reports' / ('report-' + name)
        if target.exists():
            continue
        encrypted = ROOT / 'download.gpg'
        aws('s3api', 'get-object', '--bucket', bucket, '--key', key, str(encrypted))
        data = run(['gpg', '--homedir', str(ROOT / 'gnupg'), '--batch', '--decrypt', str(encrypted)])
        report = json.loads(data)
        if report.get('schema_version') != 1 or not isinstance(report.get('resources'), list):
            raise ValueError('Unsupported report; raw state is never served')
        temporary = target.with_suffix('.tmp')
        temporary.write_bytes(data)
        os.chown(temporary, 0, grp.getgrnam('www-data').gr_gid)
        temporary.chmod(0o640)
        temporary.replace(target)


def install() -> None:
    os.umask(0o077)
    for binary in ['aws', 'gpg', 'nginx', 'systemctl']:
        if shutil.which(binary) is None:
            raise RuntimeError('Required installed runtime tool missing')
    group = grp.getgrnam('www-data').gr_gid
    ROOT.mkdir(mode=0o750, exist_ok=True)
    os.chown(ROOT, 0, group)
    ROOT.chmod(0o750)
    (ROOT / 'reports').mkdir(mode=0o750, exist_ok=True)
    os.chown(ROOT / 'reports', 0, group)
    (ROOT / 'reports').chmod(0o750)
    home = ROOT / 'gnupg'
    home.mkdir(mode=0o700, exist_ok=True)
    gpg = ['gpg', '--homedir', str(home), '--batch', '--yes']
    if b'\nsec:' not in b'\n' + run(gpg + ['--list-secret-keys', '--with-colons']):
        run(gpg + ['--pinentry-mode', 'loopback', '--passphrase', '', '--quick-generate-key',
                   'CosmoTech Lab V2 Evidence', 'rsa2048', 'encr', '0'])
    public = run(gpg + ['--armor', '--export', 'CosmoTech Lab V2 Evidence']).decode()
    if not public.startswith('-----BEGIN PGP PUBLIC KEY BLOCK-----'):
        raise ValueError('No evidence recipient')
    put_parameter('evidence-public-key', {'public_key': public}, overwrite=True, public=True)
    auth = parameter('portal-auth')
    if auth is None:
        auth = {'username': 'iac-reader', 'password': secrets.token_urlsafe(32)}
        put_parameter('portal-auth', auth)
    digest = hashlib.sha256((auth['username'] + ':' + auth['password']).encode()).hexdigest()
    app = Path('/opt/cosmotech/iac-portal')
    app.mkdir(mode=0o755, parents=True, exist_ok=True)
    app.chmod(0o755)
    for name in ['portal_server.py', 'index.html']:
        shutil.copyfile(SOURCE / name, app / name)
        (app / name).chmod(0o644)
    (app / 'launch.py').write_text('from pathlib import Path\nimport os\nfrom portal_server import serve\nserve(Path("/var/lib/cosmotech-iac/reports"), Path("/opt/cosmotech/iac-portal/index.html"), os.environ["IAC_CREDENTIAL_SHA256"])\n')
    (app / 'launch.py').chmod(0o644)
    config = Path('/etc/cosmotech-iac-portal.conf')
    config.write_text('IAC_CREDENTIAL_SHA256=' + digest + '\n')
    config.chmod(0o600)
    unit = '''[Unit]\nDescription=CosmoTech read-only IaC portal\nAfter=network.target\n[Service]\nUser=www-data\nGroup=www-data\nEnvironmentFile=/etc/cosmotech-iac-portal.conf\nExecStart=/usr/bin/python3 /opt/cosmotech/iac-portal/launch.py\nRestart=on-failure\nNoNewPrivileges=true\nProtectSystem=strict\nProtectHome=true\nPrivateTmp=true\nCapabilityBoundingSet=\n[Install]\nWantedBy=multi-user.target\n'''
    Path('/etc/systemd/system/cosmotech-iac-portal.service').write_text(unit)
    Path('/etc/systemd/system/cosmotech-iac-reports.service').write_text('[Service]\nType=oneshot\nUMask=0077\nExecStart=/usr/bin/python3 /opt/cosmotech/lab-v2-source/publication_runtime.py sync-reports\n')
    Path('/etc/systemd/system/cosmotech-iac-reports.timer').write_text('[Timer]\nOnCalendar=hourly\nPersistent=true\n[Install]\nWantedBy=timers.target\n')
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', '--now', 'cosmotech-iac-portal.service', 'cosmotech-iac-reports.timer'])
    proxy = Path('/etc/nginx/conf.d/cosmotech-itsm-public.conf')
    original = proxy.read_text()
    marker = '    server_name kestra.cosmotechsolutions.com.br;'
    include = '    include /etc/nginx/cosmotech-iac-portal-locations.conf;'
    if original.count(marker) != 1:
        raise ValueError('Expected exactly one existing Kestra proxy server')
    Path('/etc/nginx/cosmotech-kestra-viewer-http-iac.conf').write_text('limit_req_zone $binary_remote_addr zone=iac_auth:1m rate=5r/s;\n')
    Path('/etc/nginx/cosmotech-iac-portal-locations.conf').write_text('location = /iac { return 308 /iac/; }\nlocation /iac/ { limit_req zone=iac_auth burst=10 nodelay; proxy_pass http://127.0.0.1:8091/; proxy_set_header Authorization $http_authorization; proxy_set_header Host $host; add_header Cache-Control "no-store" always; }\n')
    if include not in original:
        proxy.write_text(original.replace(marker, marker + '\n' + include))
    try:
        run(['nginx', '-t'])
    except RuntimeError:
        proxy.write_text(original)
        raise
    run(['systemctl', 'reload', 'nginx'])
    for name in ['lab_monitoring.py', 'sqs_canary_metrics.py']:
        target = Path('/opt/cosmotech/monitoring') / name
        shutil.copyfile(SOURCE / name, target)
        target.chmod(0o644)
    run(['systemctl', 'start', 'cosmotech-monitoring.service'])
    publish_dashboard(publisher_token())
    print(json.dumps({'portal_url': PORTAL, 'grafana_url': GRAFANA + '/d/cosmotech-v2-sqs-workers',
                      'recipient_parameter': PARAM + 'evidence-public-key', 'authenticated_live_validation': 'pending'}))


def validate() -> None:
    auth = parameter('portal-auth')
    if auth is None:
        raise ValueError('Portal not installed')
    try:
        urllib.request.urlopen(PORTAL, timeout=20)
    except urllib.error.HTTPError as error:
        if error.code != 401:
            raise
    else:
        raise ValueError('Unauthenticated portal access must be denied')
    request = urllib.request.Request(PORTAL, headers={'Authorization': 'Basic ' +
        base64.b64encode((auth['username'] + ':' + auth['password']).encode()).decode()})
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise ValueError('Authenticated portal failed')
    token = publisher_token()
    api('/api/dashboards/uid/cosmotech-v2-sqs-workers', 'Bearer ' + token)
    print(json.dumps({'portal_authenticated': True, 'grafana_authenticated': True,
                      'live_metric_series': validate_real_data(token)}))


if __name__ == '__main__':
    try:
        if ':assumed-role/' not in aws('sts', 'get-caller-identity').get('Arn', ''):
            raise RuntimeError('A temporary role session is required')
        mode = sys.argv[1] if len(sys.argv) == 2 else ''
        {'install': install, 'validate': validate, 'sync-reports': sync_reports}[mode]()
    except Exception as error:
        print(json.dumps({'publication_failed': True, 'error_type': type(error).__name__,
                          'details': 'Values withheld; verify IAM, runtime prerequisites and authenticated service access'}))
        raise SystemExit(1)
