#!/usr/bin/env python3
"""Export a quiescent Ohio active/staging journal; no source-region mutation."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from src.disaster_recovery.control import preparation_gate
from src.disaster_recovery.dynamo_outbox import DynamoOutbox
from src.disaster_recovery.reconciliation import snapshot
from src.disaster_recovery.runtime import aws_runtime


def main():
    p=argparse.ArgumentParser();p.add_argument('--table',required=True)
    p.add_argument('--snapshot-reference',required=True);p.add_argument('--fence-evidence',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if not re.fullmatch(r'cosmotech-dr-outbox(?:-restored(?:-[a-f0-9]{12})?)?',a.table):
        raise SystemExit('Only the isolated Ohio journal/staging table allowed')
    evidence=json.loads(a.fence_evidence.read_text())
    def fenced_read():
        preparation_gate(evidence)
        if evidence.get('writers')!=0 or evidence.get('checks',{}).get('fenced') is not True or not evidence.get('fence_reference'):
            raise ValueError('Independent quiescence/fencing evidence required')
    fenced_read()
    _,_,_,resource=aws_runtime()
    rows=DynamoOutbox(resource.Table(a.table)).records()
    document=snapshot(rows,snapshot_id=a.snapshot_reference,region='us-east-2',fenced=True)
    fenced_read()  # no snapshot claim if quiescence evidence expired during scan
    fd=os.open(a.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:json.dump(document,f,indent=2)
    print(json.dumps({'snapshot_written':True,'records':len(document['records']),'payloads_logged':False}))

if __name__=='__main__':main()
