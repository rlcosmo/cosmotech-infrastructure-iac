"""Restore K3s/Argo backup files offline; never start a second controller."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ROOT = Path('/opt/cosmotech/backups/argocd')
REQUIRED = [
    'etc/rancher/k3s/config.yaml',
    'etc/rancher/k3s/k3s.yaml',
    'etc/rancher/k3s/audit.yaml',
    'var/lib/rancher/k3s/server/token',
    'var/lib/rancher/k3s/server/cred/encryption-config.json',
]


def main() -> None:
    os.umask(0o077)
    captures = sorted(p.parent for p in ROOT.glob('*/state.db')
                      if (p.parent / 'SHA256SUMS').is_file())
    if not captures:
        raise SystemExit('No completed configuration capture is available')
    source = captures[-1]
    for line in (source / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        if name not in {'state.db', 'config.tar.gz'}:
            raise SystemExit('Unexpected checksum filename')
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != digest:
            raise SystemExit('Source checksum validation failed')
    target = ROOT / 'restore-tests' / datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    target.mkdir(parents=True, exist_ok=False, mode=0o700)
    shutil.copyfile(source / 'state.db', target / 'state.db')
    with tarfile.open(source / 'config.tar.gz') as archive:
        for name in REQUIRED:
            member = archive.getmember(name)
            if not member.isfile():
                raise SystemExit('Refusing a non-regular configuration entry')
            stream = archive.extractfile(member)
            if stream is None:
                raise SystemExit('Missing configuration entry')
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            destination.write_bytes(stream.read())
    uri = 'file:' + str(target / 'state.db') + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise SystemExit('Restored SQLite integrity failed')
        rows = db.execute('SELECT name,value FROM kine WHERE deleted=0 AND id IN '
                          '(SELECT max(id) FROM kine GROUP BY name)').fetchall()
    names = [name for name, _ in rows]
    for suffix in ['/orders-api-dev', '/orders-dev', '/argocd-secret',
                   '/cosmotech-gitops', '/cosmotech-eks']:
        if not any(name.endswith(suffix) for name in names):
            raise SystemExit('Required Argo configuration record is absent: ' + suffix)
    if not any('/sh.helm.release.v1.argocd.' in name for name in names):
        raise SystemExit('Helm reconstruction metadata absent')
    encryption = json.loads((target / REQUIRED[-1]).read_text())
    keys = {}
    for resource in encryption['resources']:
        for provider in resource['providers']:
            for key in provider.get('aescbc', {}).get('keys', []):
                keys[key['name']] = base64.b64decode(key['secret'])
    decrypted = 0
    for name, value in rows:
        if '/secrets/argocd/' not in name:
            continue
        payload = bytes(value)
        if not payload.startswith(b'k8s:enc:aescbc:v1:'):
            raise SystemExit('Unexpected secret encryption provider; no plaintext is printed')
        header, encrypted = payload[len(b'k8s:enc:aescbc:v1:'):].split(b':', 1)
        decryptor = Cipher(algorithms.AES(keys[header.decode()]), modes.CBC(encrypted[:16])).decryptor()
        padded = decryptor.update(encrypted[16:]) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()
        if not plaintext.startswith((b'k8s\x00', b'{')):
            raise SystemExit('Secret reconstruction format verification failed')
        decrypted += 1
    if decrypted == 0:
        raise SystemExit('No encrypted Argo secrets were validated')
    result = {'result': 'passed', 'source_capture': source.name,
              'sqlite_integrity': 'ok', 'configuration_files': len(REQUIRED),
              'helm_application_project_repository_cluster': 'present',
              'encrypted_argocd_secrets_recoverable': decrypted,
              'controllers_started': 0, 'live_data_modified': False,
              'scope': 'Offline configuration/database/secret recovery; full EC2 boot not exercised'}
    (target / 'evidence.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
