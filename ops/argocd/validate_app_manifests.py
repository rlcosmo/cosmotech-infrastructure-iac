"""Require an HPA and a routable HTTPS health endpoint for each DEV application."""
import json
from pathlib import Path

WORKER_HTTP_PATHS = frozenset({'/health/live', '/health/ready', '/diagnostics/ping'})


def validate_worker_routes(routes: list[dict]) -> None:
    """Publish only exact read-only health and diagnostic endpoints for the worker."""
    assert len(routes) == len(WORKER_HTTP_PATHS), 'Worker route inventory must be exact and unique'
    assert {route['path'] for route in routes} == WORKER_HTTP_PATHS, 'Unexpected worker public route'
    assert all(route['pathType'] == 'Exact' for route in routes), 'Worker routes must use exact matching'


def main() -> None:
    applications = sorted(Path('gitops/apps').glob('*/dev'))
    if not applications:
        raise SystemExit('Application inventory is empty')
    for folder in applications:
        app = folder.parent.name
        deployment, hpa, service, ingress = (
            json.loads((folder / f'{kind}.json').read_text())
            for kind in ('deployment', 'hpa', 'service', 'ingress')
        )
        for resource in (deployment, hpa, service, ingress):
            assert resource['metadata']['namespace'] == app, f'{app}: namespace mismatch'
        assert 'replicas' not in deployment['spec'], f'{app}: HPA must own replicas'
        assert hpa['apiVersion'] == 'autoscaling/v2'
        spec = hpa['spec']
        assert spec['scaleTargetRef'] == {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'name': app}
        assert 1 <= spec['minReplicas'] <= spec['maxReplicas'] <= 4
        container = next(c for c in deployment['spec']['template']['spec']['containers'] if c['name'] == app)
        assert container['resources']['requests']['cpu']
        metric = spec['metrics'][0]['containerResource']
        assert metric['container'] == app and metric['target']['averageUtilization'] == 70
        assert spec['behavior']['scaleDown']['stabilizationWindowSeconds'] >= 300
        assert service['spec']['selector'] == deployment['spec']['selector']['matchLabels']
        assert service['spec']['type'] == 'ClusterIP'
        assert 'clusterIP' not in service['spec']
        port = service['spec']['ports'][0]
        assert any(p['name'] == port['targetPort'] for p in container['ports'])
        assert container['readinessProbe']['httpGet']['path'] == '/health/ready'
        assert ingress['spec']['ingressClassName'] == 'alb'
        assert ingress['metadata']['annotations']['alb.ingress.kubernetes.io/group.name'] == 'observability'
        assert ingress['metadata']['annotations']['alb.ingress.kubernetes.io/listen-ports'] == '[{"HTTPS":443}]'
        rule = ingress['spec']['rules'][0]
        assert rule['host'] == f'{app}-dev.cosmotechsolutions.com.br'
        if app == 'orders-worker':
            validate_worker_routes(rule['http']['paths'])
        for route in rule['http']['paths']:
            assert route['backend']['service'] == {'name': app, 'port': {'number': port['port']}}
        print(f'{app}: HPA, Service, probes and HTTPS host validated')


if __name__ == '__main__':
    main()
