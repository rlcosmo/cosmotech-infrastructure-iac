"""Delay the Orders Istio startup probe without changing application releases.

Hot-reloads injector configuration; affects only newly injected Orders pods.
Istio1.31.3 native-sidecar template is checked before a resourceVersion CAS update.
"""
import argparse
import json
import subprocess
from pathlib import Path

OLD = '          initialDelaySeconds: 0\n          periodSeconds: 1\n          timeoutSeconds: 3\n          failureThreshold: {{ .Values.global.proxy.startupProbe.failureThreshold }}'
PREVIOUS = '          initialDelaySeconds: {{ if or (eq (index .ObjectMeta.Labels "app") "orders-api") (eq (index .ObjectMeta.Labels "app") "orders-worker") }}5{{ else }}0{{ end }}\n          periodSeconds: 1\n          timeoutSeconds: 3\n          failureThreshold: {{ .Values.global.proxy.startupProbe.failureThreshold }}'
NEW = PREVIOUS.replace('(eq (index .ObjectMeta.Labels \"app\") \"orders-worker\")', '(eq (index .ObjectMeta.Labels \"app\") \"orders-worker\") (eq (index .ObjectMeta.Labels \"app\") \"orders-worker-canary\")')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path, required=True)
    args = parser.parse_args()
    command = ['kubectl', '-n', 'istio-system']
    current = json.loads(subprocess.check_output(command + ['get', 'configmap', 'istio-sidecar-injector', '-o', 'json']))
    if current['metadata']['labels'].get('app.kubernetes.io/version') != '1.31.3':
        raise SystemExit('Review injection template for this installed Istio version')
    source = current['data']['config']
    if NEW in source:
        print('Orders5s startup delay already installed; no mutation')
        return
    original=PREVIOUS if PREVIOUS in source else OLD
    if source.count(original) != 1:
        raise SystemExit('Unexpected injection template; do not patch')
    args.backup.parent.mkdir(parents=True, exist_ok=True)
    with args.backup.open('x') as file:
        file.write(json.dumps(current))
    args.backup.chmod(0o600)
    current['data']['config'] = source.replace(original, NEW)
    payload = json.dumps(current).encode()
    subprocess.run(command + ['replace', '--dry-run=server', '-f', '-'], input=payload, check=True)
    if args.apply:
        subprocess.run(command + ['replace', '-f', '-'], input=payload, check=True)
    print(json.dumps({'applied': args.apply, 'orders_startup_delay_seconds': 5,
                      'other_workloads_unchanged': True, 'probes_retained': True,
                      'application_rollout': False}))


if __name__ == '__main__':
    main()
