#!/usr/bin/env python3
"""Offline cross-region consistency/replay compiler; sensitive output mode 0600."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from src.disaster_recovery.reconciliation import reconcile, replay_records


def main():
    p=argparse.ArgumentParser()
    for name in ['source','target','business','receipts','output']:
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--epoch',type=int,required=True);p.add_argument('--now-ms',type=int,required=True)
    p.add_argument('--receipt-inventory-complete',action='store_true')
    p.add_argument('--verify-target-online',action='store_true');p.add_argument('--fence-evidence',type=Path)
    a=p.parse_args()
    source=json.loads(a.source.read_text())
    target=json.loads(a.target.read_text())
    report=reconcile(source,target,json.loads(a.business.read_text()),
        json.loads(a.receipts.read_text()),receipt_inventory_complete=a.receipt_inventory_complete)
    rows=replay_records(source,report,epoch=a.epoch,now_ms=a.now_ms)
    evidence_source='offline-or-simulation';fingerprint=None;observed=None
    if a.verify_target_online:
        from src.disaster_recovery.control import preparation_gate
        from src.disaster_recovery.runtime import aws_runtime
        from src.disaster_recovery.reconciliation import snapshot
        from src.disaster_recovery.telemetry import journal_fingerprint
        import time
        status=json.loads((Path(__file__).resolve().parents[2]/'docs/dr/status.json').read_text())
        if status['aws_release_confirmed'] is not True or not a.fence_evidence:
            raise SystemExit('Recorded AWS release and verified quiescence required')
        fence=json.loads(a.fence_evidence.read_text());preparation_gate(fence)
        if fence.get('writers')!=0 or fence.get('checks',{}).get('fenced') is not True:
            raise SystemExit('Quiescent destination journal required')
        _,_,outbox,_=aws_runtime();actual=list(outbox.store.records())
        current=snapshot(actual,snapshot_id=target['snapshot_id'],region='us-east-2',fenced=True)
        if current!=target:raise SystemExit('Online journal differs from restored snapshot; no verified report')
        preparation_gate(fence)
        evidence_source='aws-verified-reconciliation';fingerprint=journal_fingerprint(actual);observed=int(time.time()*1000)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(a.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:json.dump({'report':report,'replay_records':rows,'evidence_source':evidence_source,'region':'us-east-2',
        'observed_at_ms':observed,'journal_fingerprint':fingerprint},f,indent=2)
    print(json.dumps({'report_written':True,'accepted':report['accepted_count'],
        'pending':len(rows),'recovered_journal':report['recovered_journal_count'],
        'receipt_coverage_verified':report['receipt_coverage_verified'],'safe_to_promote':False}))

if __name__=='__main__':main()
