"""Fixed publisher for the existing ITSM host. Never emit credential values."""
from __future__ import annotations

import base64
import datetime as dt
import grp
import hashlib
import json
import math
import os
import pwd
import importlib.util
import re
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
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
STAGE = 'identity'
FAILURE = {}
REQUEST_DIRECTORY = Path('/run/cosmotech-lab-v2')


def run(args: list[str], value: dict | None = None) -> bytes:
    result = subprocess.run(args, input=json.dumps(value).encode() if value is not None else None,
                            capture_output=True, timeout=120, check=False)
    if result.returncode:
        FAILURE.update(binary=Path(args[0]).name, returncode=result.returncode)
        for marker, code in [(b'Error parsing parameter', 'parameter_input'),
                             (b'Parameter validation failed', 'parameter_schema'),
                             (b'Unknown options', 'unknown_option'),
                             (b'Unable to locate credentials', 'credentials_unavailable'),
                             (b'Could not connect', 'connection_failed'),
                             (b'No such file', 'input_file_missing'),
                             (b'No such device', 'input_device_unavailable'),
                             (b'Invalid JSON', 'invalid_json')]:
            if marker in result.stderr:
                FAILURE['cli_error'] = code
        if args[0] == 'aws' and len(args) > 10:
            FAILURE.update(aws_service=args[9], aws_operation=args[10])
        match = re.search(rb'\(([A-Za-z0-9]+)\) when calling the ([A-Za-z0-9]+) operation', result.stderr)
        if match:
            FAILURE.update(aws_error_code=match[1].decode(), aws_operation=match[2].decode())
        raise RuntimeError('Command failed; output withheld')
    return result.stdout


def aws(service: str, operation: str, *args: str, value: dict | None = None) -> dict:
    command = ['aws', '--region', 'us-east-1', '--output', 'json', '--cli-connect-timeout', '5',
               '--cli-read-timeout', '20', service, operation, *args]
    if value is not None:
        directory = REQUEST_DIRECTORY
        directory.mkdir(mode=0o700, exist_ok=True)
        directory.chmod(0o700)
        descriptor, path = tempfile.mkstemp(prefix='request-', suffix='.json', dir=directory)
        try:
            with os.fdopen(descriptor, 'w') as request:
                json.dump(value, request)
            return json.loads(run(command + ['--cli-input-json', 'file://' + path]) or b'{}')
        finally:
            Path(path).unlink(missing_ok=True)
    return json.loads(run(command) or b'{}')


def parameter(name: str) -> dict | None:
    command = ['aws', '--region', 'us-east-1', '--output', 'json', 'ssm', 'get-parameter',
               '--name', PARAM + name, '--with-decryption']
    result = subprocess.run(command, capture_output=True, timeout=30)
    if result.returncode:
        if b'ParameterNotFound' in result.stderr:
            return None
        match = re.search(rb'\(([A-Za-z0-9]+)\) when calling the ([A-Za-z0-9]+) operation', result.stderr)
        if match:
            FAILURE.update(binary='aws', aws_error_code=match[1].decode(), aws_operation=match[2].decode())
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
        folder = api('/api/folders/' + FOLDER, admin)
        if folder.get('title') != 'CosmoTech Lab V2':
            raise ValueError('Folder ownership mismatch')
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
            FAILURE.update(metric=expression, reason='missing_or_invalid_metric')
            raise ValueError('Missing or invalid live metrics; unknown is not zero')
        counts[expression] = len(samples)
    freshness = api('/api/datasources/proxy/uid/' + matching[0]['uid'] + '/api/v1/query?' +
                    urllib.parse.urlencode({'query': 'time() - cosmotech_sqs_data_timestamp_seconds'}), auth)
    samples = freshness.get('data', {}).get('result', [])
    if not samples or any(not 0 <= float(s['value'][1]) <= 180 for s in samples):
        FAILURE.update(reason='queue_evidence_stale_or_missing')
        ages = [float(s['value'][1]) for s in samples]
        if ages and all(math.isfinite(age) for age in ages):
            FAILURE['queue_data_max_age_seconds'] = round(max(ages))
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


def validate_alerts(token: str) -> dict:
    auth = 'Bearer ' + token
    sources = api('/api/datasources', auth)
    matching = [s for s in sources if s['type'] == 'prometheus' and
                (s['name'] == 'prometheus-aws-eks-istio-lab-dev' or s.get('isDefault'))]
    if len(matching) != 1:
        raise ValueError('Ambiguous Prometheus datasource')
    expected = {'CosmoTechSQSEvidenceUnavailable','CosmoTechSQSBacklog','CosmoTechSQSOldMessages',
                'CosmoTechSQSDLQ','CosmoTechWorkerFailures','CosmoTechWorkerRetries',
                'CosmoTechWorkerSlow','CosmoTechWorkerScrapeUnavailable'}
    response = api('/api/datasources/proxy/uid/' + matching[0]['uid'] + '/api/v1/rules?type=alert', auth)
    if response.get('status') != 'success':
        raise ValueError('Live alert rules unavailable')
    rules = [r for group in response.get('data', {}).get('groups', []) for r in group.get('rules', [])
             if r.get('name') in expected and r.get('type') == 'alerting']
    return {'expected': len(expected), 'loaded': len({r['name'] for r in rules}),
            'firing': sum(r.get('state') == 'firing' for r in rules),
            'unhealthy': sum(r.get('health') != 'ok' for r in rules)}


def install_direct_observer() -> None:
    username = 'cosmotech-iac-observer'
    try:
        account = pwd.getpwnam(username)
    except KeyError:
        run(['useradd', '--system', '--user-group', '--home-dir', '/var/lib/cosmotech-iac-observer',
             '--shell', '/usr/sbin/nologin', username])
        account = pwd.getpwnam(username)
    group = grp.getgrnam('www-data').gr_gid
    for path, mode in [('/var/lib/cosmotech-iac-observer',0o700),
                       ('/var/lib/cosmotech-iac/comparison',0o750),
                       ('/run/cosmotech-iac-observer',0o2770)]:
        directory = Path(path)
        directory.mkdir(parents=True,exist_ok=True)
        os.chown(directory,account.pw_uid,group)
        directory.chmod(mode)
    app = Path('/opt/cosmotech/iac-observer')
    app.mkdir(mode=0o755,exist_ok=True)
    app.chmod(0o755)
    for name in ['direct_observer.py','direct_inventory.py','direct_comparison.py','direct_sdk.py', 'gitops_bindings.py']:
        shutil.copyfile(SOURCE / name,app / name)
        (app / name).chmod(0o644)
    sdk_umask = os.umask(0o022)
    try:
        venv = app / 'venv'
        if not (venv / 'bin/python3').exists():
            if importlib.util.find_spec('ensurepip') is None:
                run(['apt-get','update'])
                run(['apt-get','install','-y','--no-install-recommends','python3-venv'])
            run(['/usr/bin/python3','-m','venv',str(venv)])
        run([str(venv / 'bin/python3'),'-m','pip','install','--disable-pip-version-check','--no-input','boto3==1.43.122','botocore==1.43.122','PyYAML==6.0.3'])
    finally:
        os.umask(sdk_umask)
    identity = aws('sts','get-caller-identity')
    role = 'arn:aws:iam::' + identity['Account'] + ':role/cosmotech-iac-observer'
    configuration = Path('/etc/cosmotech-iac-observer.conf')
    configuration.write_text('IAC_OBSERVER_ROLE=' + role + '\n')
    configuration.chmod(0o600)
    unit = '[Unit]\nDescription=Compare canonical Git IaC to AWS metadata without applying changes\nAfter=network-online.target\n[Service]\nUser=cosmotech-iac-observer\nGroup=www-data\nEnvironmentFile=/etc/cosmotech-iac-observer.conf\nEnvironment=HOME=/var/lib/cosmotech-iac-observer\nExecStart=/opt/cosmotech/iac-observer/venv/bin/python3 /opt/cosmotech/iac-observer/direct_observer.py --role-arn ${IAC_OBSERVER_ROLE}\nRestart=on-failure\nRestartSec=15\nUMask=0027\nRuntimeDirectory=cosmotech-iac-observer\nRuntimeDirectoryMode=2770\nRuntimeDirectoryPreserve=restart\nProtectSystem=strict\nProtectHome=true\nPrivateTmp=true\nReadWritePaths=/var/lib/cosmotech-iac-observer /var/lib/cosmotech-iac/comparison /run/cosmotech-iac-observer\n[Install]\nWantedBy=multi-user.target\n'
    Path('/etc/systemd/system/cosmotech-iac-observer.service').write_text(unit)
    run(['systemctl','daemon-reload'])
    run(['systemctl','enable','--now','cosmotech-iac-observer.service'])
    run(['systemctl','restart','cosmotech-iac-observer.service'])


def install() -> None:
    global STAGE
    STAGE = 'prerequisites'
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
    previous_recipient = parameter('evidence-public-key')
    STAGE = 'evidence-key'
    if b'\nsec:' not in b'\n' + run(gpg + ['--list-secret-keys', '--with-colons']):
        if previous_recipient is not None:
            raise ValueError('Restore the existing private key; automatic replacement is forbidden')
        run(gpg + ['--pinentry-mode', 'loopback', '--passphrase', '', '--quick-generate-key',
                   'CosmoTech Lab V2 Evidence', 'rsa2048', 'encr', '0'])
    public = run(gpg + ['--armor', '--export', 'CosmoTech Lab V2 Evidence']).decode()
    if not public.startswith('-----BEGIN PGP PUBLIC KEY BLOCK-----'):
        raise ValueError('No evidence recipient')
    if previous_recipient is not None and previous_recipient.get('public_key') != public:
        raise ValueError('Existing evidence recipient cannot be rotated implicitly')
    put_parameter('evidence-public-key', {'public_key': public}, overwrite=previous_recipient is not None, public=True)
    STAGE = 'portal-auth'
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
    unit = '''[Unit]\nDescription=CosmoTech read-only IaC portal\nAfter=network.target cosmotech-iac-observer.service\nWants=cosmotech-iac-observer.service\n[Service]\nUser=www-data\nGroup=www-data\nEnvironmentFile=/etc/cosmotech-iac-portal.conf\nExecStart=/usr/bin/python3 /opt/cosmotech/iac-portal/launch.py\nRestart=on-failure\nNoNewPrivileges=true\nProtectSystem=strict\nReadWritePaths=-/run/cosmotech-iac-observer\nProtectHome=true\nPrivateTmp=true\nCapabilityBoundingSet=\n[Install]\nWantedBy=multi-user.target\n'''
    Path('/etc/systemd/system/cosmotech-iac-portal.service').write_text(unit)
    Path('/etc/systemd/system/cosmotech-iac-reports.service').write_text('[Service]\nType=oneshot\nUMask=0077\nExecStart=/usr/bin/python3 /opt/cosmotech/lab-v2-source/publication_runtime.py sync-reports\n')
    Path('/etc/systemd/system/cosmotech-iac-reports.timer').write_text('[Timer]\nOnCalendar=hourly\nPersistent=true\n[Install]\nWantedBy=timers.target\n')
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', '--now', 'cosmotech-iac-portal.service'])
    run(['systemctl', 'disable', '--now', 'cosmotech-iac-reports.timer'])
    STAGE = 'nginx'
    proxy = Path('/etc/nginx/conf.d/cosmotech-itsm-public.conf')
    original = proxy.read_text()
    marker = '    server_name kestra.cosmotechsolutions.com.br;'
    include = '    include /etc/nginx/cosmotech-iac-portal-locations.conf;'
    if original.count(marker) != 1:
        raise ValueError('Expected exactly one existing Kestra proxy server')
    Path('/etc/nginx/cosmotech-iac-portal-http.conf').write_text('limit_req_zone $binary_remote_addr zone=iac_auth:1m rate=5r/s;\n')
    Path('/etc/nginx/cosmotech-iac-portal-locations.conf').write_text('location = /iac { return 308 /iac/; }\nlocation /iac/ { limit_req zone=iac_auth burst=10 nodelay; proxy_pass http://127.0.0.1:8091/; proxy_set_header Authorization $http_authorization; proxy_set_header Host $host; add_header Cache-Control "no-store" always; }\n')
    updated = original
    http_include = 'include /etc/nginx/cosmotech-iac-portal-http.conf;'
    if http_include not in updated:
        updated = http_include + '\n' + updated
    if include not in updated:
        updated = updated.replace(marker, marker + '\n' + include)
    temporary_proxy = proxy.with_suffix('.lab-v2.tmp')
    temporary_proxy.write_text(updated)
    temporary_proxy.chmod(proxy.stat().st_mode & 0o777)
    temporary_proxy.replace(proxy)
    try:
        run(['nginx', '-t'])
    except RuntimeError:
        proxy.write_text(original)
        raise
    run(['systemctl', 'reload', 'nginx'])
    install_direct_observer()
    run(['systemctl','restart','cosmotech-iac-portal.service'])
    STAGE = 'monitoring'
    for name in ['lab_monitoring.py', 'sqs_canary_metrics.py']:
        target = Path('/opt/cosmotech/monitoring') / name
        shutil.copyfile(SOURCE / name, target)
        target.chmod(0o644)
    run(['systemctl', 'start', 'cosmotech-monitoring.service'])
    STAGE = 'grafana'
    publish_dashboard(publisher_token())
    print(json.dumps({'portal_url': PORTAL, 'grafana_url': GRAFANA + '/d/cosmotech-v2-sqs-workers',
                      'recipient_parameter': PARAM + 'evidence-public-key', 'authenticated_live_validation': 'pending'}))


def validate() -> None:
    global STAGE
    STAGE = 'validate-portal'
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
    request = urllib.request.Request(PORTAL + 'api/comparison', headers=request.headers)
    comparison = {}
    for attempt in range(100):
        with urllib.request.urlopen(request, timeout=20) as response:
            comparison = json.load(response)
        if comparison.get('state') == 'ready':
            break
        if comparison.get('state') == 'unavailable':
            raise ValueError('Direct Git AWS observer failed')
        time.sleep(5)
    else:
        raise ValueError('Direct Git AWS observation not ready')
    if comparison.get('source',{}).get('repository') != 'https://github.com/rlcosmo/cosmotech-infrastructure-iac.git' or not comparison.get('source',{}).get('commit'):
        raise ValueError('Canonical Git revision unavailable')
    print(json.dumps({'git_aws_comparison_ready': True, 'git_commit': comparison['source']['commit'],
                      'compared_resources': len(comparison.get('resources',[])),
                      'detected_drift': comparison.get('drift_count')}),flush=True)
    token = publisher_token()
    STAGE = 'validate-grafana'
    api('/api/dashboards/uid/cosmotech-v2-sqs-workers', 'Bearer ' + token)
    print(json.dumps({'portal_authenticated': True, 'grafana_authenticated': True,
                      'portal_reports_available': 0}), flush=True)
    STAGE = 'validate-metrics'
    for attempt in range(4):
        try:
            metrics = validate_real_data(token)
            break
        except ValueError:
            if FAILURE.get('reason') != 'queue_evidence_stale_or_missing' or attempt == 3:
                raise
            time.sleep(30)
    print(json.dumps({'portal_authenticated': True, 'grafana_authenticated': True,
                      'portal_reports_available': 0,
                      'live_metric_series': metrics, 'live_alert_rules': validate_alerts(token)}))


if __name__ == '__main__':
    try:
        if ':assumed-role/' not in aws('sts', 'get-caller-identity').get('Arn', ''):
            raise RuntimeError('A temporary role session is required')
        mode = sys.argv[1] if len(sys.argv) == 2 else ''
        {'install': install, 'validate': validate, 'sync-reports': sync_reports}[mode]()
    except Exception as error:
        if isinstance(error, urllib.error.HTTPError):
            FAILURE['http_status'] = error.code
        codes = {'Ambiguous Prometheus datasource': 'ambiguous_prometheus',
                 'Portal not installed': 'portal_not_installed',
                 'Unauthenticated portal access must be denied': 'portal_auth_not_enforced',
                 'Authenticated portal failed': 'portal_authenticated_request_failed',
                 'Unsupported credential schema': 'credential_schema_unsupported',
                 'Queue evidence is stale': 'queue_evidence_stale_or_missing'}
        if str(error) in codes:
            FAILURE['reason'] = codes[str(error)]
        print(json.dumps({'publication_failed': True, 'error_type': type(error).__name__,
                          'stage': STAGE, 'failure_identifiers': FAILURE,
                          'runtime_tools': {name: shutil.which(name) is not None for name in ['aws','gpg','nginx','systemctl','docker']},
                          'prerequisite_paths': {name: Path(path).exists() for name, path in {
                              'host_proxy': '/etc/nginx/conf.d/cosmotech-itsm-public.conf',
                              'monitoring_directory': '/opt/cosmotech/monitoring',
                              'docker_proxy': '/opt/cosmotech/public-proxy'}.items()},
                          'details': 'Values withheld; verify IAM, runtime prerequisites and authenticated service access'}))
        raise SystemExit(1)
