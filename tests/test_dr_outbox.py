import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest

from src.disaster_recovery.memory_outbox import MemoryOutbox
from src.disaster_recovery.outbox import Outbox, LostLease, make_record
from src.disaster_recovery.reconciliation import snapshot, reconcile, replay_records
from src.disaster_recovery.runtime import accept_request, activation


def order(key='dr-one'):
    return {'order_id':key,'items':[{'sku':'LAB-SKU-001','quantity':1}]}


class Queue:
    def __init__(self):self.bodies=[];self.available=True;self.uncertain=False
    def send_message(self,MessageBody):
        if not self.available:raise ConnectionError('SQS outage')
        self.bodies.append(MessageBody)
        if self.uncertain:raise TimeoutError('Sent but response lost')
        return {'MessageId':str(len(self.bodies))}


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.now=1000.0;self.store=MemoryOutbox();self.box=Outbox(self.store,clock=lambda:self.now);self.q=Queue()

    def test_accept_while_sqs_down_persists_before_202_then_eventually_publishes(self):
        self.q.available=False
        status,value=accept_request(self.box,order(),active=True)
        self.assertEqual(status,202);self.assertEqual(len(self.store.records()),1)
        with self.assertRaises(ConnectionError):self.box.publish('dr-one',self.q,epoch=1)
        self.assertEqual(self.store.records()[0]['state'],'PENDING')
        self.now+=61;self.q.available=True
        self.assertEqual(self.box.publish('dr-one',self.q,epoch=1),'sent')
        self.assertEqual(self.store.records()[0]['state'],'SENT')

    def test_no_202_when_journal_down_and_no_write_when_paused(self):
        self.store.available=False
        self.assertEqual(accept_request(self.box,order(),active=True)[0],503)
        self.store.available=True
        self.assertEqual(accept_request(self.box,order(),active=False)[0],503)
        self.assertEqual(self.store.records(),[])

    def test_uncertain_journal_commit_retry_returns_original_event(self):
        original=self.store.put_if_absent
        def uncertain(r):original(r);raise TimeoutError('Committed response lost')
        self.store.put_if_absent=uncertain
        self.assertEqual(accept_request(self.box,order(),active=True)[0],503)
        self.store.put_if_absent=original
        self.now+=1
        self.assertEqual(accept_request(self.box,order(),active=True)[0],202)
        self.assertEqual(self.store.records()[0]['accepted_at'],1000000)
        self.assertEqual(len(self.store.records()),1)

    def test_concurrent_accepts_one_immutable_record(self):
        with ThreadPoolExecutor(max_workers=12) as pool:
            records=list(pool.map(lambda _:self.box.accept(order()),range(50)))
        self.assertEqual(len(self.store.records()),1)
        self.assertEqual(len({r['event_json'] for r in records}),1)

    def test_conflicting_order_returns_409_without_mutation(self):
        first=self.box.accept(order());other=order();other['items'][0]['quantity']=2
        self.assertEqual(accept_request(self.box,other,active=True)[0],409)
        self.assertEqual(self.store.records()[0],first)

    def test_sqs_unknown_send_may_duplicate_but_stable_event_and_id_survive(self):
        self.box.accept(order());self.q.uncertain=True
        with self.assertRaises(TimeoutError):self.box.publish('dr-one',self.q,epoch=1)
        self.now+=61;self.q.uncertain=False
        self.box.publish('dr-one',self.q,epoch=2)
        self.assertEqual(self.q.bodies[0],self.q.bodies[1])
        effects={}
        for body in self.q.bodies:
            event=json.loads(body)
            # Model the consumer's atomic business transaction keyed by order ID.
            effects.setdefault(event['order_id'],event['order'])
        self.assertEqual(len(effects),1)

    def test_claim_prevents_overlapping_publishers_and_stale_owner(self):
        self.box.accept(order());r=self.store.claim('dr-one','old',1,1000000,1060000)
        self.assertIsNotNone(r)
        self.assertEqual(self.box.publish('dr-one',self.q,epoch=1),'not_claimed')
        self.now+=61;self.store.claim('dr-one','new',2,1061000,1121000)
        with self.assertRaises(LostLease):self.store.sent('dr-one','old',1,1061000)
        self.assertEqual(self.store.items['dr-one']['lease_token'],'new')

    def test_sweep_requires_live_activation_and_validated_body(self):
        self.box.accept(order())
        self.assertEqual(self.box.sweep(self.q,epoch=1),0)
        self.assertEqual(self.box.sweep(self.q,epoch=1,lease_live=lambda:True),1)
        self.assertEqual(self.box.sweep(self.q,epoch=1,lease_live=lambda:True),0)

    def test_invalid_or_oversize_payloads_never_acknowledged(self):
        for value in [None,{},order('INVENTORY#LAB'),{'order_id':'a','items':[]},
                      {'order_id':'a','items':[{'sku':'b','quantity':True}]}]:
            self.assertEqual(accept_request(self.box,value,active=True)[0],400)
        self.assertEqual(self.store.records(),[])

    def test_activation_missing_expired_wrong_region_and_epoch(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'control.json'
            self.assertIsNone(activation(p,now=1000))
            for data in [dict(mode='run',region='us-east-1',epoch=1,expires_at=1100),
                         dict(mode='run',region='us-east-2',epoch=True,expires_at=1100),
                         dict(mode='run',region='us-east-2',epoch=1,expires_at=999)]:
                p.write_text(json.dumps(data));self.assertIsNone(activation(p,now=1000))
            p.write_text(json.dumps(dict(mode='run',region='us-east-2',epoch=1,expires_at=1100)))
            self.assertEqual(activation(p,now=1000),1)


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.records=[make_record(order('dr-'+str(i)),1000+i) for i in range(4)]
        self.source=snapshot(self.records,snapshot_id='immutable-source-version',region='us-east-1',fenced=True)
        self.target=snapshot(self.records,snapshot_id='immutable-target-version',region='us-east-2',fenced=True)
        self.receipts=[{'order_id':r['order_id'],'payload_hash':r['payload_hash']} for r in self.records]

    def test_pending_replay_skips_verified_terminal_orders_and_preserves_created_at(self):
        done={**order('dr-0'),'status':'COMPLETED'}
        report=reconcile(self.source,self.target,[done],self.receipts,receipt_inventory_complete=True)
        self.assertEqual(report['completed_count'],1)
        rows=replay_records(self.source,report,epoch=2,now_ms=2000000)
        self.assertEqual(len(rows),3)
        self.assertEqual(rows[0]['event_json'],self.records[1]['event_json'])
        self.assertFalse(report['safe_to_promote'])

    def test_missing_region_copy_does_not_claim_zero_message_loss(self):
        target=snapshot(self.records[:2],snapshot_id='lagged-copy',region='us-east-2',fenced=True)
        report=reconcile(self.source,target,[],self.receipts,receipt_inventory_complete=True)
        self.assertEqual(len(report['missing_journal_ids']),2)
        self.assertEqual(report['unrecovered_ack_count'],2)
        self.assertIsNone(report['message_loss_count'])

    def test_receipts_required_and_unknown_ack_blocks_replay(self):
        report=reconcile(self.source,self.target,[],[])
        self.assertFalse(report['receipt_coverage_verified']);self.assertIsNone(report['message_loss_count'])
        report=reconcile(self.source,self.target,[],self.receipts+[{'order_id':'lost','payload_hash':'f'*64}])
        with self.assertRaises(ValueError):replay_records(self.source,report,epoch=2,now_ms=0)

    def test_corruption_and_business_divergence_block(self):
        tampered=copy.deepcopy(self.target);tampered['records'][0]['accepted_at']+=1
        with self.assertRaises(ValueError):reconcile(self.source,tampered,[],self.receipts)
        wrong={**order('dr-0'),'status':'COMPLETED','items':[{'sku':'OTHER','quantity':1}]}
        with self.assertRaises(ValueError):reconcile(self.source,self.target,[wrong],self.receipts)

    def test_region_loss_simulation_can_restore_and_reconcile_reverse_failback(self):
        restored=snapshot(self.source['records'],snapshot_id='restored-version',region='us-east-2',fenced=True)
        report=reconcile(self.source,restored,[],self.receipts,receipt_inventory_complete=True)
        self.assertEqual(report['missing_journal_ids'],[])
        # Reverse comparison is OFFLINE; no operation changes the primary.
        back=reconcile(restored,self.source,[],self.receipts,receipt_inventory_complete=True)
        self.assertEqual(back['accepted_count'],4)

    def test_export_requires_fence_and_rejects_duplicate_ids(self):
        with self.assertRaises(ValueError):snapshot(self.records,snapshot_id='v',region='us-east-1',fenced=False)
        with self.assertRaises(ValueError):snapshot(self.records+self.records[:1],snapshot_id='v',region='us-east-1',fenced=True)

class TelemetryTests(unittest.TestCase):
    def test_backlog_and_unknown_loss_never_filled_with_zero(self):
        from src.disaster_recovery.telemetry import render_outbox
        record=make_record(order(),1000)
        text=render_outbox([record],now_ms=1030000)
        self.assertIn('outbox_pending{region="us-east-2"} 1',text)
        self.assertIn('oldest_pending_age_seconds{region="us-east-2"} 30.0',text)
        self.assertNotIn('journal_loss_count_verified',text)
        report=dict(missing_journal_ids=['unknown'],pending_ids=['pending'],unrecovered_ack_count=1,
                    receipt_coverage_verified=False,message_loss_count=None)
        text=render_outbox([record],now_ms=1030000,report=report)
        self.assertIn('unrecovered_acknowledgements{region="us-east-2"} 1',text)
        self.assertNotIn('journal_loss_count_verified',text)

class ExtendedChaosTests(unittest.TestCase):
    def test_sqs_region_az_node_and_dependency_models(self):
        from src.disaster_recovery.chaos import simulate_outbox
        for name in ['sqs-outage','ambiguous-send','queue-loss','region-loss-completed-copy',
                     'az-client-isolation','node-loss-after-send','dependency-outage']:
            with self.subTest(name=name):
                report=simulate_outbox(name)
                self.assertFalse(report['recovery_blocked'])
                self.assertEqual(report['recovered'],10)
                self.assertEqual(report['duplicate_business_effects'],0)
                self.assertIsNone(report['measured_aws_rto_seconds'])

    def test_incomplete_region_copy_and_corruption_block_instead_of_claiming_zero_loss(self):
        from src.disaster_recovery.chaos import simulate_outbox
        lagged=simulate_outbox('region-loss-lagged-copy')
        self.assertTrue(lagged['recovery_blocked']);self.assertEqual(lagged['known_unrecovered_acknowledgements'],2)
        self.assertTrue(simulate_outbox('corrupted-backup')['recovery_blocked'])

class VerifiedReportTests(unittest.TestCase):
    def test_offline_stale_or_changed_journal_cannot_emit_verified_recovery_metrics(self):
        from src.disaster_recovery.telemetry import verified_report,journal_fingerprint
        rows=[make_record(order(),1000)]
        envelope=dict(evidence_source='aws-verified-reconciliation',observed_at_ms=1000000,region='us-east-2',
            journal_fingerprint=journal_fingerprint(rows),report={'fixture':'no actual AWS certification'})
        self.assertIsNotNone(verified_report(envelope,rows,1000001))
        self.assertIsNone(verified_report({**envelope,'evidence_source':'simulation'},rows,1000001))
        self.assertIsNone(verified_report(envelope,rows,2000000))
        self.assertIsNone(verified_report(envelope,rows+[make_record(order('another'),1000)],1000001))
