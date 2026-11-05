"""Read application process readiness and persistent filesystem use; never return environment data."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path


def command(args: list[str]) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=10)


def docker_services(project: str, application: str, names: list[str], directory: str) -> list[dict]:
    try:
        lines = command(['docker', 'ps', '--all', '--filter', 'label=com.docker.compose.project=' + project,
                         '--format', '{{.ID}} {{.Label "com.docker.compose.service"}}']).splitlines()
        ids = {line.split()[1]: line.split()[0] for line in lines if len(line.split()) == 2}
    except (OSError, subprocess.SubprocessError):
        ids = None
    result = []
    for name in names:
        status, state = -1, 'unknown'
        if ids is not None:
            if name not in ids:
                status, state = 0, 'missing'
            else:
                try:
                    value = json.loads(command(['docker', 'inspect', '--format', '{{json .State}}', ids[name]]))
                    state = value.get('Status', 'unknown')
                    health = value.get('Health', {}).get('Status')
                    if health and health != 'healthy':
                        state += '/' + health
                    status = int(value.get('Running', False) and health in {None, 'healthy'})
                except (OSError, ValueError, subprocess.SubprocessError):
                    pass
        result.append({'application': application, 'service': name, 'manager': 'docker compose',
                       'status': status, 'state': state, 'repair': f'cd {directory} && docker compose up -d {name}'})
    return result


def argo_services() -> list[dict]:
    result = []
    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'k3s'], check=False).returncode == 0
    result.append({'application': 'Argo CD', 'service': 'k3s (plataforma Argo)', 'manager': 'systemd',
                   'status': int(active), 'state': 'active' if active else 'inactive', 'repair': 'systemctl start k3s'})
    expected = [('deployment', 'argocd-server'), ('deployment', 'argocd-repo-server'),
                ('statefulset', 'argocd-application-controller')]
    try:
        env = {**os.environ, 'KUBECONFIG': '/etc/rancher/k3s/k3s.yaml'}
        data = json.loads(subprocess.check_output(['kubectl', '--request-timeout=8s', '-n', 'argocd', 'get',
            'deployment,statefulset', '-o', 'json'], env=env, text=True, stderr=subprocess.DEVNULL, timeout=10))
        objects = {value['metadata']['name']: value for value in data['items']}
    except (OSError, ValueError, subprocess.SubprocessError):
        objects = None
    for kind, name in expected:
        status, state = (0, 'k3s inactive') if not active else (-1, 'unknown')
        if objects is not None:
            value = objects.get(name)
            if value is None:
                status, state = 0, 'missing'
            else:
                desired = value['spec'].get('replicas', 1)
                ready = value.get('status', {}).get('readyReplicas', 0)
                status, state = int(desired > 0 and ready >= desired), f'{ready}/{desired} ready'
        result.append({'application': 'Argo CD', 'service': name, 'manager': 'kubernetes', 'status': status,
                       'state': state, 'repair': f'kubectl -n argocd rollout restart {kind}/{name}'})
    return result


def filesystems() -> list[dict]:
    result = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, right = line.split(' - ', 1)
        fs_type, device, *_ = right.split()
        if fs_type not in {'ext4', 'ext3', 'xfs', 'btrfs', 'vfat', 'zfs'}:
            continue
        mount = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), left.split()[4])
        if mount.startswith(('/var/lib/docker/', '/run/')):
            continue
        stat = os.statvfs(mount)
        total = stat.f_blocks * stat.f_frsize
        used = (stat.f_blocks - stat.f_bfree) * stat.f_frsize
        available = stat.f_bavail * stat.f_frsize
        if total:
            result.append({'mountpoint': mount, 'device': device, 'filesystem': fs_type,
                           'size_bytes': total, 'used_bytes': used, 'available_bytes': available,
                           'used_percent': used / (used + available) * 100 if used + available else 0})
    return result


def main() -> None:
    services = []
    if Path('/opt/cosmotech/zabbix/docker-compose.yml').exists():
        services += docker_services('cosmotech-zabbix', 'Zabbix', ['server', 'web', 'proxy', 'postgres', 'clickhouse', 'telemetry'], '/opt/cosmotech/zabbix')
    if Path('/opt/cosmotech/itsm/application.yaml').exists():
        services += docker_services('cosmotech-itsm', 'GLPI / Kestra', ['glpi', 'glpi-db', 'kestra', 'kestra-db'], '/opt/cosmotech/itsm')
        services += docker_services('cosmotech-automation', 'Kestra Automation', ['engine', 'database'], '/opt/cosmotech/itsm-automation')
    if Path('/etc/rancher/k3s/k3s.yaml').exists():
        services += argo_services()
    print(json.dumps({'observed_at': time.time(), 'services': services, 'filesystems': filesystems()}))


if __name__ == '__main__':
    main()
