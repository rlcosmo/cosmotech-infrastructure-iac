"""Bootstrap infrastructure-only Argo trust; preserve application promotion authority."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import tempfile

FINGERPRINT = '968479A1AFF927E37D1A566BB5690EEEBB952194'


def public_key_patch(key: str) -> dict:
    if not key.startswith('-----BEGIN PGP PUBLIC KEY BLOCK-----') or 'PRIVATE KEY' in key:
        raise ValueError('Only the pinned public merge-signing key is accepted')
    with tempfile.TemporaryDirectory() as home:
        result = subprocess.run(['gpg','--homedir',home,'--batch','--with-colons',
            '--import-options','show-only','--import'], input=key, text=True,
            capture_output=True, check=False, timeout=10)
    records = [line.split(':') for line in result.stdout.splitlines()]
    fingerprints = [r[9] for r in records if r[0] == 'fpr']
    if result.returncode or fingerprints != [FINGERPRINT] or sum(r[0] == 'pub' for r in records) != 1:
        raise ValueError('Unexpected GitHub public-key fingerprint')
    return {'data': {FINGERPRINT[-16:]: key}}


def namespace_patch(current: str) -> dict:
    namespaces = current.split(',')
    if set(namespaces) != {'orders-api','orders-worker'} and set(namespaces) != {'orders-api','orders-worker','observability'}:
        raise ValueError('Unexpected cluster namespace boundary; do not widen blindly')
    return {'stringData': {'namespaces': 'orders-api,orders-worker,observability'}}


def main() -> None:
    # Run only on the existing management host with its local K3s admin identity.
    kubectl = ['k3s','kubectl','-n','argocd']
    def read(*args):
        return subprocess.check_output(kubectl + list(args), timeout=20)
    before = json.loads(read('get','appproject','orders-dev','-o','json'))['spec']
    if before.get('signatureKeys') != [{'keyID':'6C140B9EE5CA76A6'}]:
        raise ValueError('Existing application promotion authority must remain enforced')
    # Fetch only this non-credential field, never the cluster token/config.
    namespaces = base64.b64decode(read('get','secret','cosmotech-eks','-o',
        'jsonpath={.data.namespaces}')).decode()
    key = (Path(__file__).resolve().parents[1] / 'ops/argocd/observability-github-signing-key.asc').read_text()
    patches = [('configmap','argocd-gpg-keys-cm',public_key_patch(key)),
               ('secret','cosmotech-eks',namespace_patch(namespaces))]
    for kind,name,patch in patches:
        subprocess.run(kubectl + ['patch',kind,name,'--type=merge','-p',json.dumps(patch)],
                       check=True, timeout=20, stdout=subprocess.DEVNULL)
    after = json.loads(read('get','appproject','orders-dev','-o','json'))['spec']
    if before != after:
        raise RuntimeError('Application promotion project changed unexpectedly')
    print(json.dumps({'observability_namespace_managed':True,
        'public_merge_signer':FINGERPRINT,'application_promotion_project_preserved':True}))


if __name__ == '__main__':
    main()
