"""Install a pinned native Agent 2 with per-host TLS PSK fetched through its instance profile."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import subprocess
import urllib.request
from pathlib import Path

PACKAGE_URL = 'https://repo.zabbix.com/zabbix/8.0/stable/ubuntu/pool/main/z/zabbix/zabbix-agent2_8.0.0-1%2Bubuntu24.04_amd64.deb'
PACKAGE_SHA256 = '1ff8566528c3ee39cc11721877f776cb409493ac11468750f405e873b770fd60'
HOSTS = {'cosmotech-itsm-dev','cosmotech-argocd-dev','cosmotech-zabbix-dev'}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--hostname', choices=sorted(HOSTS), required=True)
    args = parser.parse_args()
    os.umask(0o077)
    upgraded = False
    result = subprocess.run(['dpkg-query','-W','-f=${Version}','zabbix-agent2'],capture_output=True,text=True,check=False)
    if result.returncode or result.stdout != '2:8.0.0-1+ubuntu24.04':
        directory = Path('/var/cache/cosmotech-zabbix');directory.mkdir(parents=True,exist_ok=True)
        package = directory/'agent2.deb'
        with urllib.request.urlopen(PACKAGE_URL,timeout=60) as response:
            package.write_bytes(response.read())
        if hashlib.sha256(package.read_bytes()).hexdigest() != PACKAGE_SHA256:
            raise RuntimeError('Pinned Agent 2 package integrity verification failed')
        env = {**os.environ,'DEBIAN_FRONTEND':'noninteractive'}
        with (directory/'install.log').open('a') as log:
            subprocess.run(['apt-get','update','-qq'],env=env,stdout=log,stderr=log,check=True)
            subprocess.run(['apt-get','install','-y','-qq','-o','Dpkg::Options::=--force-confold',str(package)],env=env,stdout=log,stderr=log,check=True)
        upgraded = True
    name = 'cosmotech/zabbix/agents/'+args.hostname
    result = subprocess.run(['aws','--region','us-east-1','secretsmanager','get-secret-value','--secret-id',name,'--output','json'],capture_output=True,text=True,check=False)
    if result.returncode:
        raise RuntimeError('Cannot fetch this host agent identity; sensitive diagnostics omitted')
    identity = json.loads(json.loads(result.stdout)['SecretString'])
    if identity['hostname'] != args.hostname or len(identity['psk']) != 64:
        raise ValueError('Invalid host TLS identity')
    root = Path('/etc/zabbix');root.mkdir(exist_ok=True)
    key = root/'cosmotech-agent2.psk';key.write_text(identity['psk']+'\n')
    zabbix = pwd.getpwnam('zabbix');os.chown(key,0,zabbix.pw_gid);key.chmod(0o640)
    config = '\n'.join(['LogType=system','ListenIP=127.0.0.1','Server=127.0.0.1',
        'ServerActive=10.20.11.207:10051','Hostname='+args.hostname,'RefreshActiveChecks=60',
        'BufferSend=5','HeartbeatFrequency=60','Timeout=10','TLSConnect=psk','TLSAccept=psk',
        'TLSPSKIdentity='+identity['psk_identity'],'TLSPSKFile='+str(key),''])
    path = root/'zabbix_agent2.conf'
    if path.exists():
        current = path.read_text()
        if 'Hostname='+args.hostname not in current:
            raise ValueError('Existing agent identity differs; preserving configuration')
        config = current
    changed = not path.exists() or path.read_text()!=config
    if changed:
        if path.exists() and not path.with_suffix('.before-cosmotech').exists():
            path.with_suffix('.before-cosmotech').write_bytes(path.read_bytes())
        path.write_text(config);path.chmod(0o640);os.chown(path,0,zabbix.pw_gid)
    subprocess.run(['zabbix_agent2','-T','-c',str(path)],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['systemctl','enable','zabbix-agent2'],check=True,capture_output=True)
    subprocess.run(['systemctl','restart' if changed or upgraded else 'start','zabbix-agent2'],check=True)
    subprocess.run(['systemctl','is-active','--quiet','zabbix-agent2'],check=True)
    print(json.dumps({'hostname':args.hostname,'agent':'8.0.0','active':True,'transport':'TLS-PSK','server':'10.20.11.207:10051'}))


if __name__ == '__main__':
    main()
