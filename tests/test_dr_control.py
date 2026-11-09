import copy
import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from src.disaster_recovery.control import REQUIRED, promotion_plan, replay_events, guard_chaos_target

spec = importlib.util.spec_from_file_location('dr_plan', Path(__file__).parents[1] / 'scripts/dr/check_plan.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class RecoveryGateTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 8, tzinfo=timezone.utc)
        self.evidence = dict(account='414053644145', source_region='us-east-1', target_region='us-east-2',
            environment='dr-test', observed_at=self.now.isoformat(),
            expires_at=(self.now + timedelta(minutes=10)).isoformat(), account_released=True,
            aws_release_reference='MOCK-NOT-REAL', inflight_operations=0, writers=0,
            checks=dict.fromkeys(REQUIRED, True), fence_reference='isolated-fixture',
            snapshot_version='immutable-fixture', change_reference='fixture-change', epoch=1,
            protected_primary_hash='a'*64)

    def test_passive_plan_does_not_modify_dns_or_activate(self):
        result = promotion_plan(self.evidence, self.now)
        self.assertEqual(result['mode'], 'plan-only')
        self.assertFalse(result['dns_mutation'])
        self.assertNotIn('activate', result['steps'])

    def test_each_unverified_recovery_dependency_blocks(self):
        for name in REQUIRED:
            with self.subTest(name=name):
                e = copy.deepcopy(self.evidence)
                e['checks'][name] = False
                with self.assertRaises(ValueError): promotion_plan(e, self.now)

    def test_hold_expiry_future_writer_and_wrong_target_block(self):
        mutations = [('account_released', False), ('writers', 1), ('inflight_operations', 1),
            ('target_region','us-east-1'), ('environment','dev'), ('epoch',0),
            ('fence_reference',''), ('snapshot_version',''), ('protected_primary_hash',''),
            ('expires_at', self.now.isoformat()),
            ('observed_at',(self.now+timedelta(minutes=1)).isoformat())]
        for key, value in mutations:
            e = {**self.evidence, key:value}
            with self.subTest(key=key), self.assertRaises(ValueError): promotion_plan(e, self.now)

    def test_changed_evidence_changes_audit_hash(self):
        a = promotion_plan(self.evidence, self.now)
        b = promotion_plan({**self.evidence, 'epoch':2}, self.now)
        self.assertNotEqual(a['evidence_hash'], b['evidence_hash'])

    def event(self, key='one'):
        return dict(event_type='order.created', version=2, order_id=key, order={'order_id':key,'items':[{'sku':'LAB-SKU-001','quantity':1}]}, created_at='2026-11-08T00:00:00Z')

    def test_replay_deduplicates_and_skips_completed_ids(self):
        a, b = self.event(), self.event('two')
        self.assertEqual(replay_events([a,a,b], {'two'}), [a])

    def test_conflicting_ids_and_invalid_records_abort(self):
        a = self.event()
        b = {**a, 'order':{**a['order'], 'items':['changed']}}
        with self.assertRaises(ValueError): replay_events([a,b], set())
        for e in [self.event('INVENTORY#LAB'), {**a, 'order':{}}, {**a, 'created_at':''}]:
            with self.assertRaises(ValueError): replay_events([e], set())

    def test_chaos_fails_closed_on_wrong_region_primary_cluster_and_no_telemetry(self):
        t = dict(account='414053644145', region='us-east-2', cluster='cosmotech-dr',
                 namespace='dr-test',environment='dr-test',duration_seconds=120,
                 kind='pod-loss',telemetry_ready=True,synthetic_only=True)
        guard_chaos_target(t)
        for key, value in [('region','us-east-1'),('cluster','aws-eks-istio-lab-dev'),
            ('namespace','observability'),('duration_seconds',121),('telemetry_ready',False),('synthetic_only',False)]:
            with self.subTest(key=key), self.assertRaises(ValueError): guard_chaos_target({**t,key:value})

    def test_plan_scope_rejects_network_primary_deletion_and_foreign_modules(self):
        good = {'address':'module.standby[0].aws_vpc.standby','type':'aws_vpc',
                'change':{'actions':['create'],'after':{'region':'us-east-2'}}}
        self.assertTrue(checker.check_plan({'resource_changes':[good]}))
        for change in [
            {**good,'type':'aws_route53_record'},
            {**good,'address':'aws_vpc.primary'},
            {**good,'change':{'actions':['delete','create'],'after':{}}},
            {**good,'change':{'actions':['create'],'after':{'region':'us-east-1'}}},
        ]:
            with self.assertRaises(ValueError): checker.check_plan({'resource_changes':[change]})


class RestorePlanTests(unittest.TestCase):
    def test_new_destination_only_idempotent_restore(self):
        s=importlib.util.spec_from_file_location('restore',Path(__file__).parents[1]/'scripts/dr/restore_plan.py')
        mod=importlib.util.module_from_spec(s);s.loader.exec_module(mod)
        point=dict(arn='arn:aws:backup:us-east-2:414053644145:recovery-point:fixture-123',
            state='COMPLETED',integrity_reference='fixture-only',type='DynamoDB',
            restore_metadata={'targetTableName':'cosmotech-dr-orders-restored'})
        fixture=dict(account='414053644145',region='us-east-2',restore_role_arn='arn:aws:iam::414053644145:role/cosmotech-dr-restore',recovery_points=[point])
        self.assertEqual(mod.compile_plan(fixture),mod.compile_plan(fixture))
        with self.assertRaises(ValueError): mod.compile_plan({**fixture,'region':'us-east-1'})
        with self.assertRaises(ValueError): mod.compile_plan({**fixture,'recovery_points':[dict(point,restore_metadata={'targetTableName':'aws-eks-istio-lab-dev-orders-data'})]})
        with self.assertRaises(ValueError): mod.compile_plan({**fixture,'recovery_points':[]})

class RealMetricGateTests(unittest.TestCase):
    def test_simulations_cannot_publish_real_metrics(self):
        s=importlib.util.spec_from_file_location('metrics',Path(__file__).parents[1]/'scripts/dr/metrics.py')
        m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
        with self.assertRaises(ValueError):m.render({'source':'simulation'})
        evidence=dict(source='aws-e2e',region='us-east-2',run_reference='fixture-only',integrity_verified=True,
            outage='2026-11-08T00:00:00Z',observed='2026-11-08T00:01:00Z',last_ack='2026-11-08T00:00:00Z',recovered_commit='2026-11-07T23:59:55Z',recovered=None)
        text=m.render(evidence)
        self.assertNotIn('cosmotech_dr_rto_seconds',text)
        self.assertIn('cosmotech_dr_rpo_seconds{region="us-east-2"} 5.0',text)

class TargetProbeTests(unittest.TestCase):
    def test_wrong_kubeconfig_is_rejected_before_order_or_tunnel(self):
        from unittest.mock import patch
        s=importlib.util.spec_from_file_location('e2e',Path(__file__).parents[1]/'scripts/dr/probe_e2e.py')
        m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
        import json
        replies=[{'Account':'414053644145','Arn':'arn:aws:sts::414053644145:assumed-role/dr/operator'},
            {'cluster':{'arn':'arn:aws:eks:us-east-2:414053644145:cluster/cosmotech-dr','endpoint':'https://dr.example'}},
            {'clusters':[{'cluster':{'server':'https://primary.example'}}]}]
        with patch.object(m.subprocess,'check_output',side_effect=[json.dumps(r).encode() for r in replies]):
            with self.assertRaises(ValueError):m.verify_target(Path('/tmp/fixture-kubeconfig'))

class ChaosModelTests(unittest.TestCase):
    def test_faults_delay_recovery_and_commit_without_ack_is_idempotent(self):
        s=importlib.util.spec_from_file_location('chaos',Path(__file__).parents[1]/'scripts/dr/simulate_chaos.py')
        m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
        results=[m.simulate(kind) for kind in ['pod-loss','node-loss','az-loss','dependency-outage']]
        self.assertEqual([r['fault_blocked_model_ticks'] for r in results],[1,2,3,4])
        for result in results:
            self.assertEqual(result['orders_recovered'],10)
            self.assertEqual(result['lost_ack_redeliveries'],1)
            self.assertEqual(result['duplicate_effects'],0)
            self.assertIsNone(result['measured_aws_rto_seconds'])

class QuoteEvidenceTests(unittest.TestCase):
    def test_real_quote_totals_and_compatibility_are_reproducible(self):
        import json
        from decimal import Decimal
        from src.disaster_recovery.evidence import compare_costs
        root=Path(__file__).parents[1]
        record=json.loads((root/'docs/dr/cost-comparison.json').read_text())
        now=datetime.fromisoformat(record['quoted_at'])
        ranking=compare_costs(record['candidates'],now)
        self.assertEqual(ranking[0]['region'],'us-east-2')
        for region in record['candidates']:
            self.assertEqual(sum(Decimal(v) for v in region['monthly_usd'].values()).quantize(Decimal('.01')),Decimal(region['standby_total_usd']))
        compatibility=json.loads((root/'docs/dr/regional-evidence-2026-11-08.json').read_text())
        for region in compatibility['candidates']:
            self.assertIn('1.36',region['eks'])
            self.assertTrue(region['compute'][0]['sku'])

if __name__ == '__main__': unittest.main()
