"""Pure controls for the administrator bootstrap; no network or credential storage."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import re
import json

ADMIN_ADDRESSES = {
    'aws_iam_role.bootstrap["infrastructure"]', 'aws_iam_role.bootstrap["rbac"]',
    'aws_iam_role_policy.infrastructure', 'aws_iam_role_policy.rbac', 'aws_eks_access_entry.rbac',
}
ROLE_NAMES = {'cosmotech-lab-v2-observability-reader', 'cosmotech-lab-v2-rbac-bootstrap'}


def validate_temporary(credentials: dict, now: datetime) -> None:
    if (not credentials.get('AccessKeyId', '').startswith('ASIA') or not credentials.get('SessionToken')
            or not credentials.get('SecretAccessKey') or not credentials.get('Expiration')):
        raise ValueError('A refreshable temporary login/role session is required; static keys are rejected')
    expires = datetime.fromisoformat(credentials['Expiration'].replace('Z', '+00:00'))
    if expires.tzinfo is None or expires <= now + timedelta(minutes=2):
        raise ValueError('Temporary session is expired or too close to expiry')


def authorization_deadline(previous: str | None, now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError('UTC-aware time required')
    if previous:
        deadline = datetime.fromisoformat(previous.replace('Z', '+00:00'))
        if deadline.tzinfo is None or deadline > now + timedelta(hours=4):
            raise ValueError('Invalid prior authorization window')
        if deadline >= now + timedelta(hours=1):
            return previous
    return (now + timedelta(hours=2)).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def check_admin_plan(plan: dict) -> list[dict]:
    if plan.get('errored') or plan.get('deferred_changes'):
        raise ValueError('Failed/deferred administrative plan')
    result = []
    for resource in plan.get('resource_changes', []):
        if resource.get('mode') == 'data':
            continue
        change = resource['change']
        if change.get('importing'):
            raise ValueError('Implicit adoption/import is forbidden')
        if change['actions'] == ['no-op']:
            continue
        if resource['address'] not in ADMIN_ADDRESSES or change['actions'] not in (['create'], ['update']):
            raise ValueError('Administrative plan contains replacement/deletion or unrelated resources')
        if resource['type'] == 'aws_iam_role':
            after = change['after']
            kind = 'infrastructure' if resource['address'].endswith('["infrastructure"]') else 'rbac'
            expected = 'cosmotech-lab-v2-bootstrap' if kind == 'infrastructure' else 'cosmotech-lab-v2-rbac-bootstrap'
            trust = json.loads(after['assume_role_policy'])['Statement']
            if after['name'] != expected or after['max_session_duration'] > 3600 or len(trust) != 1:
                raise ValueError('Unexpected administrative role or lifetime')
            conditions = trust[0]['Condition']
            suffix = 'cosmotech-infrastructure-iac@1445869018' if kind == 'infrastructure' else 'cosmotech-kubernetes-rbac@1445869219'
            if conditions['StringEquals'] != {'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com', 'token.actions.githubusercontent.com:sub': 'repo:rlcosmo@124368446/' + suffix + ':ref:refs/heads/main'}:
                raise ValueError('OIDC audience/repository/branch must be exact')
            deadline = datetime.fromisoformat(conditions['DateLessThan']['aws:CurrentTime'].replace('Z', '+00:00'))
            remaining = deadline - datetime.now(timezone.utc)
            if not timedelta(minutes=30) <= remaining <= timedelta(hours=4):
                raise ValueError('Administrative authorization must be bounded and current')
        if resource['type'] == 'aws_eks_access_entry':
            after = change['after']
            if after['cluster_name'] != 'aws-eks-istio-lab-dev' or after['type'] != 'STANDARD' or after['kubernetes_groups'] != ['cosmotech-lab-v2-rbac-bootstrap']:
                raise ValueError('Unexpected EKS principal mapping')
        if resource['type'] == 'aws_iam_role_policy':
            policy = json.loads(change['after']['policy'])
            for statement in policy['Statement']:
                actions = statement['Action']
                if isinstance(actions, str):
                    actions = [actions]
                if '*' in actions:
                    raise ValueError('Unrestricted action grants are forbidden')
                for action in actions:
                    mutation = action.split(':', 1)[1].startswith(('Create', 'Put', 'Update', 'Tag', 'Delete', 'Send', 'Add', 'Attach', 'Pass', 'Set'))
                    allowed = {'iam:CreateRole', 'iam:TagRole', 'iam:PutRolePolicy', 's3:PutObject', 's3:DeleteObject', 'ssm:CreateDocument', 'ssm:UpdateDocument', 'ssm:UpdateDocumentDefaultVersion', 'ssm:AddTagsToResource', 'ssm:SendCommand'}
                    if mutation and action not in allowed:
                        raise ValueError('Unreviewed administrative action')
                    if mutation:
                        if not statement.get('Condition', {}).get('DateLessThan', {}).get('aws:CurrentTime'):
                            raise ValueError('Every bootstrap mutation must expire')
        result.append({'address': resource['address'], 'actions': change['actions']})
    return result


def validate_rbac(objects: list[dict]) -> None:
    if len(objects) != 4:
        raise ValueError('Exactly two namespaced Roles and two RoleBindings required')
    seen = set()
    for obj in objects:
        metadata = obj.get('metadata', {})
        name = metadata.get('name')
        if metadata.get('namespace') != 'observability' or name not in ROLE_NAMES:
            raise ValueError('Unexpected RBAC namespace/name')
        key = (obj['kind'], name)
        if key in seen:
            raise ValueError('Duplicate RBAC object')
        seen.add(key)
        if obj['kind'] == 'Role':
            for rule in obj['rules']:
                groups, resources, verbs = set(rule['apiGroups']), set(rule['resources']), set(rule['verbs'])
                if groups == {''}:
                    valid = resources <= {'configmaps'} and verbs <= {'get', 'list', 'watch'}
                elif groups == {'monitoring.coreos.com'}:
                    valid = resources <= {'podmonitors', 'servicemonitors', 'prometheusrules'} and verbs <= {'get', 'list', 'watch'}
                elif groups == {'rbac.authorization.k8s.io'} and name == 'cosmotech-lab-v2-rbac-bootstrap':
                    valid = resources <= {'roles', 'rolebindings'} and verbs <= {'get', 'list', 'watch', 'create', 'patch', 'update'}
                else:
                    valid = False
                if not valid or rule.get('nonResourceURLs'):
                    raise ValueError('RBAC exceeds the reviewed read/bounded-management scope')
        elif obj['kind'] == 'RoleBinding':
            group = 'cosmotech-maintenance-readers' if name.endswith('observability-reader') else name
            if obj['roleRef'] != {'kind': 'Role', 'name': name, 'apiGroup': 'rbac.authorization.k8s.io'} or obj['subjects'] != [{'kind': 'Group', 'name': group, 'apiGroup': 'rbac.authorization.k8s.io'}]:
                raise ValueError('Unexpected binding or principal')
        else:
            raise ValueError('Cluster-scoped RBAC is forbidden')
