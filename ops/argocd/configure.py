"""Configure renewable EKS access and protect bootstrap secrets without printing them."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path('/opt/cosmotech/argocd')


def command(args: list[str]) -> dict:
    return json.loads(subprocess.check_output(args))


def apply(value: dict) -> None:
    subprocess.run(['kubectl', 'apply', '-f', '-'], input=json.dumps(value).encode(),
                   check=True, stdout=subprocess.DEVNULL)


def main() -> None:
    os.umask(0o077)
    cluster = command(['aws', 'eks', 'describe-cluster', '--name', 'aws-eks-istio-lab-dev'])['cluster']
    config = {
        'execProviderConfig': {
            'command': 'argocd-k8s-auth',
            'args': ['aws', '--cluster-name', cluster['name']],
            'apiVersion': 'client.authentication.k8s.io/v1beta1',
            'env': {'AWS_REGION': 'us-east-1'},
        },
        'tlsClientConfig': {'insecure': False, 'caData': cluster['certificateAuthority']['data']},
    }
    apply({'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {
        'name': 'cosmotech-eks', 'namespace': 'argocd',
        'labels': {'argocd.argoproj.io/secret-type': 'cluster'}}, 'stringData': {
            'name': cluster['name'], 'server': cluster['endpoint'], 'namespaces': 'orders-api',
            'clusterResources': 'false', 'config': json.dumps(config)}})
    initial = command(['kubectl', '-n', 'argocd', 'get', 'secret',
                       'argocd-initial-admin-secret', '-o', 'json'])
    password = base64.b64decode(initial['data']['password']).decode()
    secret = ROOT / 'admin.json'
    secret.write_text(json.dumps({'username': 'admin', 'password': password,
                                 'url': 'https://argocd.cosmotechsolutions.com.br'}))
    subprocess.run(['aws', 'secretsmanager', 'put-secret-value', '--secret-id',
                    'cosmotech/argocd/admin', '--secret-string', 'file://' + str(secret)],
                   check=True, stdout=subprocess.DEVNULL)
    request = urllib.request.Request('http://127.0.0.1:30080/api/v1/session',
        data=json.dumps({'username': 'admin', 'password': password}).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=15) as response:
        assert json.load(response).get('token'), 'Authenticated session was not created'
    secret.unlink()
    key = ROOT / 'github-deploy-key'
    if not key.exists():
        restored = subprocess.run(['aws', 'secretsmanager', 'get-secret-value', '--secret-id',
            'cosmotech/argocd/github-deploy-key'], capture_output=True, text=True)
        if restored.returncode == 0:
            key.write_text(json.loads(restored.stdout)['SecretString'])
            key.chmod(0o600)
            public = subprocess.check_output(['ssh-keygen', '-y', '-f', str(key)], text=True)
            key.with_suffix('.pub').write_text(public)
        elif 'ResourceNotFoundException' in restored.stderr:
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C',
                            'cosmotech-argocd-readonly', '-f', str(key)], check=True)
        else:
            raise SystemExit('Cannot restore existing deploy key; refusing key replacement')
    subprocess.run(['aws', 'secretsmanager', 'put-secret-value', '--secret-id',
                    'cosmotech/argocd/github-deploy-key', '--secret-string', 'file://' + str(key)],
                   check=True, stdout=subprocess.DEVNULL)
    print('Argo CD authenticated login passed; credentials stored in Secrets Manager.')
    print('Read-only GitHub deploy public key:', key.with_suffix('.pub').read_text().strip())


if __name__ == '__main__':
    main()
