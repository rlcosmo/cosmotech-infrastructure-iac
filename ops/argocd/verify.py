"""Check the public authenticated Argo CD API without exposing credentials."""
from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

BASE = 'https://argocd.cosmotechsolutions.com.br'


def main() -> None:
    result = json.loads(subprocess.check_output(['aws', 'secretsmanager', 'get-secret-value',
        '--secret-id', 'cosmotech/argocd/admin', '--region', 'us-east-1']))
    credentials = json.loads(result['SecretString'])
    request = urllib.request.Request(BASE + '/api/v1/session',
        data=json.dumps({'username': credentials['username'], 'password': credentials['password']}).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        token = json.load(response)['token']
    evidence = {'https_authenticated_login': True}
    try:
        urllib.request.urlopen(BASE + '/api/v1/applications', timeout=15)
    except urllib.error.HTTPError as error:
        evidence['anonymous_api_status'] = error.code
    for name in ['clusters', 'repositories']:
        req = urllib.request.Request(BASE + '/api/v1/' + name,
                                     headers={'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(req, timeout=20) as response:
            data = json.load(response)
        evidence[name] = [item.get('connectionState', {}).get('status', 'Unknown')
                          for item in data.get('items', [])]
    req = urllib.request.Request(BASE + '/api/v1/applications/orders-api-dev',
                                 headers={'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(req, timeout=20) as response:
        app = json.load(response)
    status = app.get('status', {})
    evidence['application'] = {'sync': status.get('sync', {}).get('status'),
                              'health': status.get('health', {}).get('status'),
                              'conditions': status.get('conditions', [])}
    path = Path('/opt/cosmotech/backups/argocd/validation.json')
    path.write_text(json.dumps(evidence, indent=2) + '\n')
    path.chmod(0o600)
    print(json.dumps(evidence))
    if evidence['anonymous_api_status'] != 401:
        raise SystemExit('Anonymous API protection failed')


if __name__ == '__main__':
    main()
