"""Fail-closed operational plan and reversible capacity state machine."""
import hashlib
import json

REGION = 'us-east-1'
CLUSTER = 'aws-eks-istio-lab-dev'
PROTECTED = {'ALB', 'NLB', 'NAT', 'EIP', 'VPC', 'SUBNET', 'ROUTE', 'SG', 'DNS', 'IGW'}
PHRASES = {'DOWN': 'DESLIGAR LABORATÓRIO', 'UP': 'LIGAR LABORATÓRIO'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()


def plan(inventory, direction, snapshot=None):
    if direction not in PHRASES:
        raise ValueError('Direção inválida')
    blockers = list(inventory.get('errors', []))
    if direction == 'DOWN':
        blockers.extend(inventory.get('preflight', {}).get('blockers', ['Pré-flight Kubernetes/GitOps ainda não validado']))
        for resource in inventory['resources']:
            if resource.get('action') == 'stop' and resource.get('public_ip') and not resource.get('elastic_ip'):
                blockers.append(f"IP público efêmero: {resource['id']}")
        if not inventory.get('preflight', {}).get('recovery_verified'):
            blockers.append('Recuperação independente do EKS ainda não validada')
    elif not snapshot:
        blockers.append('Nenhum snapshot de desligamento registrado; não é seguro adivinhar capacidades anteriores')
    return {'direction': direction, 'confirmation': PHRASES[direction], 'blockers': sorted(set(blockers)),
            'ready': not blockers, 'inventory_hash': digest(inventory['resources']),
            'targets': [r for r in inventory['resources'] if r.get('action') in ('stop', 'scale')],
            'protected': [r for r in inventory['resources'] if r['classification'] != 'LAB'],
            'recovery': ['Iniciar EC2 de controle', 'Restaurar capacidades registradas de EKS/ASG',
                         'Restaurar workloads e reconciliação GitOps', 'Comparar volumes, rede, filas e saúde com snapshot']}


def actions(snapshot, direction):
    """Generate only exact snapshot-owned actions; no network API exists here."""
    targets = snapshot['targets']
    if direction == 'DOWN':
        result = [{'kind': 'quiesce', 'snapshot': snapshot['key']}]
        for target in targets:
            if target['type'] == 'MNG':
                result.append({'kind': 'mng', 'id': target['id'], 'capacity': {**target['capacity'], 'minSize': 0, 'desiredSize': 0}})
            elif target['type'] == 'ASG':
                result.append({'kind': 'asg', 'id': target['id'], 'capacity': {'MinSize': 0, 'MaxSize': target['capacity']['MaxSize'], 'DesiredCapacity': 0}})
        # Control host is last. Instance IDs/capacity are always from the immutable snapshot.
        result.extend({'kind': 'stop', 'id': t['id']} for t in sorted(targets, key=lambda t: bool(t.get('control_host')))
                      if t['type'] == 'EC2' and t['state'] == 'running')
        return result + [{'kind': 'verify_off', 'snapshot': snapshot['key']}]
    if direction != 'UP':
        raise ValueError('Direção inválida')
    result = [{'kind': 'start', 'id': t['id']} for t in sorted(targets, key=lambda t: not t.get('control_host'))
              if t['type'] == 'EC2' and t['state'] == 'running']
    result.extend({'kind': 'mng' if t['type'] == 'MNG' else 'asg', 'id': t['id'], 'capacity': t['capacity']}
                  for t in targets if t['type'] in ('MNG', 'ASG'))
    return result + [{'kind': 'restore', 'snapshot': snapshot['key']}, {'kind': 'verify_on', 'snapshot': snapshot['key']}]


def assert_action(action, allowed):
    kind = action['kind']
    if kind not in {'mng', 'asg', 'stop', 'start', 'quiesce', 'restore', 'verify_on', 'verify_off'}:
        raise ValueError('Ação não autorizada')
    if kind in {'mng', 'asg', 'stop', 'start'} and action['id'] not in allowed:
        raise ValueError('Recurso fora da allowlist')
    if kind == 'mng' and action['capacity']['maxSize'] < 1:
        raise ValueError('EKS exige maxSize >= 1')
