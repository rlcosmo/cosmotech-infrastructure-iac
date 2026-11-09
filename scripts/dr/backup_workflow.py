#!/usr/bin/env python3
"""Prepare or execute ONLY isolated restore intents after recorded AWS clearance."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from src.disaster_recovery.backup import execute_restore_plan
from src.disaster_recovery.control import preparation_gate


def main():
    p=argparse.ArgumentParser();p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--evidence',type=Path);p.add_argument('--execute',action='store_true')
    p.add_argument('--restore-role-arn');p.add_argument('--output',type=Path)
    a=p.parse_args();plan=json.loads(a.plan.read_text())
    if not a.execute:
        print(json.dumps({'mode':'plan-only','intents':len(plan['intents']),'aws_calls':0}));return
    status=json.loads((Path(__file__).resolve().parents[2]/'docs/dr/status.json').read_text())
    if status['aws_release_confirmed'] is not True:
        raise SystemExit('AWS release is not recorded; no restore allowed')
    if not a.evidence or not a.output or not a.restore_role_arn:
        raise SystemExit('Fresh preparation evidence, scoped restore role and private output required')
    evidence=json.loads(a.evidence.read_text());preparation_gate(evidence,datetime.now(timezone.utc))
    from decimal import Decimal
    ceiling=Decimal(str(evidence.get('monthly_budget_usd',0)))
    estimate=Decimal(str(evidence.get('estimated_monthly_cost_usd',0)))
    if not (ceiling.is_finite() and estimate.is_finite() and 0<estimate<=ceiling):
        raise SystemExit('Explicit reviewed cost ceiling/estimate required')
    import boto3
    from botocore.config import Config
    session=boto3.Session(region_name='us-east-2')
    config=Config(connect_timeout=3,read_timeout=15,retries={'mode':'standard','total_max_attempts':2})
    identity=session.client('sts',config=config).get_caller_identity()
    if identity['Account']!='414053644145' or ':assumed-role/cosmotech-dr-' not in identity['Arn']:
        raise SystemExit('Temporary non-root DR operator required')
    # Reserve audit file before any write; retries must consult saved job IDs and
    # the same idempotency token, never silently create a fresh recovery intent.
    fd=os.open(a.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as audit:
        json.dump({'state':'STARTING','manifest_hash':plan['manifest_hash']},audit);audit.flush();os.fsync(audit.fileno())
        def checkpoint(jobs):
            audit.seek(0);json.dump({'state':'PARTIAL','manifest_hash':plan['manifest_hash'],'jobs':jobs},audit);audit.truncate();audit.flush();os.fsync(audit.fileno())
        jobs=execute_restore_plan(session.client('backup',config=config),plan,
            release_gate=lambda:preparation_gate(evidence,datetime.now(timezone.utc)) is None,role_arn=a.restore_role_arn,checkpoint=checkpoint)
        audit.seek(0);json.dump({'state':'STARTED','manifest_hash':plan['manifest_hash'],'jobs':jobs},audit);audit.truncate();audit.flush();os.fsync(audit.fileno())
    print(json.dumps({'restore_jobs_started':len(jobs),'audit_written':True}))

if __name__=='__main__':main()
