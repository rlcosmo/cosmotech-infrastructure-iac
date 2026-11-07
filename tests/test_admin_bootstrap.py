import copy
import datetime as dt
import json
import unittest
from src.publication.admin_bootstrap import authorization_deadline, check_admin_plan, validate_rbac, validate_temporary


class AdminBootstrapTests(unittest.TestCase):
    def test_static_or_expired_credentials_are_never_used(self):
        now = dt.datetime.now(dt.timezone.utc)
        value = {'AccessKeyId': 'AKIA-test-only', 'SessionToken': 'test', 'SecretAccessKey': 'test', 'Expiration': (now + dt.timedelta(minutes=30)).isoformat()}
        with self.assertRaises(ValueError): validate_temporary(value, now)
        value['AccessKeyId'] = 'ASIA-test-only'
        validate_temporary(value, now)
        value['Expiration'] = (now - dt.timedelta(seconds=1)).isoformat()
        with self.assertRaises(ValueError): validate_temporary(value, now)

    def test_deadline_idempotence_and_no_indefinite_extension(self):
        now = dt.datetime(2026, 11, 7, tzinfo=dt.timezone.utc)
        value = authorization_deadline(None, now)
        self.assertEqual(authorization_deadline(value, now + dt.timedelta(minutes=5)), value)
        with self.assertRaises(ValueError): authorization_deadline('2099-01-01T00:00:00Z', now)

    def test_unrelated_replacements_and_imports_blocked(self):
        for address, actions in [('aws_wafv2_ip_set.other', ['update']), ('aws_iam_role.bootstrap["rbac"]', ['delete','create'])]:
            with self.assertRaises(ValueError): check_admin_plan({'resource_changes':[{'address':address,'mode':'managed','type':'aws_iam_role','change':{'actions':actions}}]})
        with self.assertRaises(ValueError): check_admin_plan({'resource_changes':[{'address':'aws_iam_role.bootstrap["rbac"]','mode':'managed','type':'aws_iam_role','change':{'actions':['no-op'],'importing':{'id':'foreign'}}}]})

    def test_no_permanent_or_unreviewed_mutation_grants(self):
        policy = {'Version':'2012-10-17','Statement':[{'Effect':'Allow','Action':['iam:PutRolePolicy'],'Resource':'reviewed-role'}]}
        resource = {'address':'aws_iam_role_policy.infrastructure','type':'aws_iam_role_policy','change':{'actions':['create'],'after':{'policy':json.dumps(policy)}}}
        with self.assertRaises(ValueError): check_admin_plan({'resource_changes':[resource]})
        policy['Statement'][0]['Condition'] = {'DateLessThan':{'aws:CurrentTime':'2099-01-01T00:00:00Z'}}
        policy['Statement'][0]['Action'] = ['iam:AttachRolePolicy']
        resource['change']['after']['policy'] = json.dumps(policy)
        with self.assertRaises(ValueError): check_admin_plan({'resource_changes':[resource]})

    def test_rbac_rejects_secrets_or_wrong_namespace(self):
        def role(name): return {'kind':'Role','metadata':{'name':name,'namespace':'observability'},'rules':[{'apiGroups':[''],'resources':['configmaps'],'verbs':['get','list','watch']}]}
        def binding(name, group): return {'kind':'RoleBinding','metadata':{'name':name,'namespace':'observability'},'roleRef':{'kind':'Role','name':name,'apiGroup':'rbac.authorization.k8s.io'},'subjects':[{'kind':'Group','name':group,'apiGroup':'rbac.authorization.k8s.io'}]}
        names = ['cosmotech-lab-v2-observability-reader','cosmotech-lab-v2-rbac-bootstrap']
        objects = [role(n) for n in names] + [binding(names[0], 'cosmotech-maintenance-readers'), binding(names[1], names[1])]
        validate_rbac(objects)
        bad = copy.deepcopy(objects); bad[0]['rules'][0]['resources'] = ['secrets']
        with self.assertRaises(ValueError): validate_rbac(bad)
        bad = copy.deepcopy(objects); bad[1]['metadata']['namespace'] = 'kube-system'
        with self.assertRaises(ValueError): validate_rbac(bad)
