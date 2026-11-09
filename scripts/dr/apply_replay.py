#!/usr/bin/env python3
"""Conditional destination journal import/replay. OFF by default and account-gated."""
import argparse
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from src.disaster_recovery.control import promotion_plan
from src.disaster_recovery.outbox import bucket
from src.disaster_recovery.reconciliation import verify_snapshot, immutable, replay_records


def apply(store, source, report, *, epoch, now_ms, still_fenced, business_records=None):
    if report.get('business_dataset_consistent') is not True:
        raise ValueError('Full business/inventory/checkpoint integrity required before replay execution')
    if not report['receipt_coverage_verified']:
        raise ValueError('Complete independent receipt inventory required before automated replay')
    from src.disaster_recovery.integrity import fingerprint
    if business_records is None or fingerprint(business_records)[1]!=report.get('business_dataset_digest'):
        raise ValueError('Business dataset changed since reconciliation; no replay write')
    rows=verify_snapshot(source)
    # Preflight ALL existing journal rows before performing any import.
    for existing in store.records():
        key=existing['order_id']
        if key not in rows or immutable(existing)!=immutable(rows[key]):
            raise ValueError('Current target journal diverged; no import started')
    pending=replay_records(source,report,epoch=epoch,now_ms=now_ms)
    count=0
    # Restore all immutable acceptance records, including completed business orders;
    # conditional import never overwrites target journal metadata/leases.
    for key,row in rows.items():
        if not still_fenced():raise ValueError('Fence evidence expired; stop replay')
        proposed={**row,'epoch':epoch,'attempts':0,'state':'SENT'}
        if key in report['pending_ids']:
            proposed.update(state='PENDING',dispatch_bucket=bucket(key),due_at=now_ms)
        existing=store.put_if_absent(proposed)
        if immutable(existing)!=immutable(row):raise ValueError('Target journal diverged; no overwrite')
    for row in pending:
        if not still_fenced():raise ValueError('Fence evidence expired; stop replay')
        store.schedule_replay(row,epoch=epoch,now=now_ms);count+=1
    return {'journal_records_verified':len(rows),'replay_scheduled':count,'business_table_mutations':0}


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True);p.add_argument('--epoch',type=int,required=True)
    p.add_argument('--evidence',type=Path);p.add_argument('--execute',action='store_true')
    a=p.parse_args();source=json.loads(a.source.read_text());report=json.loads(a.report.read_text())
    if 'report' in report:report=report['report']
    verify_snapshot(source)
    if not a.execute:
        print(json.dumps({'mode':'plan-only','accepted':len(source['records']),'pending':len(report['pending_ids']),'aws_calls':0}));return
    status=json.loads((Path(__file__).resolve().parents[2]/'docs/dr/status.json').read_text())
    if status['aws_release_confirmed'] is not True or not a.evidence:
        raise SystemExit('Account release and independent fencing evidence required; no write')
    evidence=json.loads(a.evidence.read_text());promotion_plan(evidence)
    if evidence['epoch']!=a.epoch:raise SystemExit('Replay epoch does not match fence evidence')
    from src.disaster_recovery.runtime import aws_runtime
    _,_,outbox,resource=aws_runtime()
    from src.disaster_recovery.dynamo_outbox import DynamoOutbox
    business=list(DynamoOutbox(resource.Table('cosmotech-dr-orders-restored')).records())
    def live():promotion_plan(evidence);return True
    print(json.dumps(apply(outbox.store,source,report,epoch=a.epoch,now_ms=int(time.time()*1000),still_fenced=live,business_records=business)))

if __name__=='__main__':main()
