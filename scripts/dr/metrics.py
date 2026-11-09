#!/usr/bin/env python3
"""Render verified recovery timestamps as Prometheus text; never substitute zero."""
from datetime import datetime
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from src.disaster_recovery.evidence import recovery_metrics

def render(evidence):
    if (evidence.get('source') != 'aws-e2e' or evidence.get('region') != 'us-east-2'
            or not evidence.get('run_reference') or evidence.get('integrity_verified') is not True):
        raise ValueError('Real E2E/integrity evidence is required; simulations cannot publish AWS metrics')
    dates={key:datetime.fromisoformat(evidence[key]) for key in ('outage','observed','last_ack','recovered_commit')}
    dates['recovered']=datetime.fromisoformat(evidence['recovered']) if evidence.get('recovered') else None
    result=recovery_metrics(**dates)
    lines=[f'cosmotech_dr_evidence_observed_timestamp_seconds{{region="us-east-2"}} {dates["observed"].timestamp()}']
    for metric in ['rto_seconds','rpo_seconds','downtime_seconds']:
        if result[metric] is not None:
            lines.append(f'cosmotech_dr_{metric}{{region="us-east-2"}} {result[metric]}')
    return '\n'.join(lines)+'\n'

if __name__=='__main__': print(render(json.load(open(sys.argv[1]))),end='')
