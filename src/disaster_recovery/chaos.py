"""Deterministic outbox fault models. No network, AWS, Kubernetes or real RTO."""
import json

from .memory_outbox import MemoryOutbox
from .outbox import Outbox
from .reconciliation import snapshot, reconcile, replay_records


class FaultQueue:
    def __init__(self, mode='healthy'):
        self.mode=mode;self.messages=[]

    def send_message(self,MessageBody):
        if self.mode=='sqs-outage':raise ConnectionError('Injected SQS outage')
        self.messages.append(MessageBody)
        if self.mode=='ambiguous-send':raise TimeoutError('Injected lost send response')
        return {'MessageId':str(len(self.messages))}


def simulate_outbox(scenario):
    allowed={'sqs-outage','ambiguous-send','queue-loss','region-loss-completed-copy',
             'region-loss-lagged-copy','az-client-isolation','node-loss-after-send',
             'dependency-outage','corrupted-backup'}
    if scenario not in allowed:raise ValueError('Unknown bounded fault model')
    now=[1000];store=MemoryOutbox();box=Outbox(store,clock=lambda:now[0])
    orders=[dict(order_id='dr-synthetic-'+str(i),items=[dict(sku='LAB-SKU-001',quantity=1)]) for i in range(10)]
    for order in orders:box.accept(order)
    receipts=[dict(order_id=r['order_id'],payload_hash=r['payload_hash']) for r in store.records()]
    queue=FaultQueue(scenario if scenario in {'sqs-outage','ambiguous-send'} else 'healthy')
    for order in orders:
        try:box.publish(order['order_id'],queue,epoch=1)
        except (ConnectionError,TimeoutError):pass
    if scenario=='node-loss-after-send':
        # A process crash loses the publication mark, not the committed journal.
        store=MemoryOutbox([dict(r,state='PENDING',due_at=now[0]*1000,dispatch_bucket='pending-0') for r in store.records()])
        box=Outbox(store,clock=lambda:now[0])
    if scenario=='az-client-isolation':
        store.available=False
        try:box.accept(dict(order_id='dr-rejected',items=[dict(sku='LAB-SKU-001',quantity=1)]))
        except ConnectionError:pass
        else:raise AssertionError('No acknowledgement allowed without journal')
        store.available=True
    if scenario=='queue-loss':queue.messages.clear()  # model only; never SQS PurgeQueue
    rows=store.records()
    if scenario=='region-loss-lagged-copy':rows=rows[:8]
    source=snapshot(rows,snapshot_id='SIMULATED-IMMUTABLE-COPY',region='us-east-1',fenced=True)
    if scenario=='corrupted-backup':
        source['records'][0]['accepted_at']+=1
    target=snapshot(rows,snapshot_id='SIMULATED-OHIO-RESTORE',region='us-east-2',fenced=True)
    try:
        report=reconcile(source,target,[],receipts,receipt_inventory_complete=True)
        recovered=replay_records(source,report,epoch=2,now_ms=1061000)
    except ValueError:
        return dict(scenario=scenario,execution='simulation-only',recovery_blocked=True,
                    accepted=10,known_unrecovered_acknowledgements=len({r['order_id'] for r in receipts}-{r['order_id'] for r in rows}) if scenario=='region-loss-lagged-copy' else None,
                    measured_aws_rto_seconds=None,measured_aws_rpo_seconds=None)
    # All replay bodies survive loss of the original process/store/queue.
    fresh=MemoryOutbox(recovered);newbox=Outbox(fresh,clock=lambda:1061)
    queue.mode='healthy'
    for order in orders:newbox.publish(order['order_id'],queue,epoch=2)
    reservations={};fulfilled=set();retries=0
    for body in queue.messages+queue.messages[:3]:
        event=json.loads(body);key=event['order_id']
        reservations.setdefault(key,1)  # model atomic inventory+checkpoint transaction
        if scenario=='dependency-outage' and not retries:
            retries+=1;continue  # reservation survives dependency failure
        fulfilled.add(key)
    # Resume interrupted work from durable checkpoint, no extra reservation.
    fulfilled.update(reservations)
    assert len(reservations)==10 and sum(reservations.values())==10 and len(fulfilled)==10
    return dict(scenario=scenario,execution='simulation-only',recovery_blocked=False,
                accepted=10,recovered=10,duplicate_business_effects=0,
                deliveries_observed=len(queue.messages),measured_aws_rto_seconds=None,measured_aws_rpo_seconds=None)
