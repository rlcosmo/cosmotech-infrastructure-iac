"""Render reviewed Argo GPG enforcement outside the mutable delivery repository."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

import yaml


def render(project: dict, public_key: str, fingerprint: str) -> list[dict]:
    if not re.fullmatch(r'(?:[A-F0-9]{40}|[A-F0-9]{64})', fingerprint):
        raise ValueError('Full dedicated key fingerprint required')
    if 'PRIVATE KEY' in public_key or not public_key.startswith('-----BEGIN PGP PUBLIC KEY BLOCK-----'):
        raise ValueError('Only an exported public key is permitted')
    # Inspect in an isolated keyring; never import into the operator keyring.
    with tempfile.TemporaryDirectory() as keyring:
        result = subprocess.run(['gpg', '--homedir', keyring, '--batch', '--with-colons',
                                 '--import-options', 'show-only', '--import'],
                                input=public_key, text=True, capture_output=True, check=False)
    records = [row.split(':') for row in result.stdout.splitlines()]
    primaries = [row[4] for row in records if row[0] == 'pub']
    fingerprints = [row[9] for row in records if row[0] == 'fpr']
    if result.returncode or len(primaries) != 1 or fingerprint not in fingerprints:
        raise ValueError('Public key does not match the exclusive authority')
    project = json.loads(json.dumps(project))
    if project.get('kind') != 'AppProject' or project.get('metadata', {}).get('name') != 'orders-dev':
        raise ValueError('Unexpected delivery project')
    project['spec']['signatureKeys'] = [{'keyID': fingerprint[-16:]}]
    keyring = {'apiVersion': 'v1', 'kind': 'ConfigMap',
               'metadata': {'name': 'argocd-gpg-keys-cm', 'namespace': 'argocd',
                            'labels': {'app.kubernetes.io/part-of': 'argocd'}},
               'data': {fingerprint[-16:]: public_key}}
    return [keyring, project]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--public-key', type=Path, required=True)
    parser.add_argument('--fingerprint', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    resources = render(yaml.safe_load(args.project.read_text()), args.public_key.read_text(), args.fingerprint)
    args.output.write_text(yaml.safe_dump_all(resources, sort_keys=False))


if __name__ == '__main__':
    main()
