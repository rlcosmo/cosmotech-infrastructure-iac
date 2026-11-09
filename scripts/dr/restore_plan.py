#!/usr/bin/env python3
"""Compile destination-only restore commands from an immutable backup manifest.
No commands execute. Obtain restore metadata from the destination vault first.
"""
import argparse
import json
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from src.disaster_recovery.control import canonical_hash


def compile_plan(manifest):
    if manifest.get('account')!='414053644145' or manifest.get('region')!='us-east-2':
        raise ValueError('Wrong restore scope')
    role=manifest.get('restore_role_arn','')
    if not re.fullmatch(r'arn:aws:iam::414053644145:role/cosmotech-dr-[\w+=,.@-]+',role):
        raise ValueError('Require a dedicated non-root DR restore role')
    commands=[]
    for point in manifest['recovery_points']:
        arn=point['arn']
        if not re.fullmatch(r'arn:aws:(backup|ec2):us-east-2:414053644145:(recovery-point:[A-Za-z0-9-]+|snapshot/snap-[a-f0-9]+)',arn):
            raise ValueError('Only already copied destination recovery points')
        if point.get('state')!='COMPLETED' or not point.get('integrity_reference'):
            raise ValueError('Recovery point not independently verified')
        metadata=point['restore_metadata']
        if not isinstance(metadata,dict) or any(not isinstance(v,str) or 'us-east-1' in v for v in metadata.values()):
            raise ValueError('Restore metadata must be destination-only string values')
        if point['type']=='DynamoDB':
            if metadata.get('targetTableName')!='cosmotech-dr-orders-restored':
                raise ValueError('Restore into a NEW isolated table only')
        elif point['type']=='EBS':
            if (metadata.get('availabilityZone') not in {'us-east-2a','us-east-2b','us-east-2c'}
                    or metadata.get('volumeType')!='gp3' or str(metadata.get('encrypted')).lower()!='true'):
                raise ValueError('Encrypted gp3 destination volume in reviewed AZ required')
        else:raise ValueError('Unsupported restore type')
        commands.append(['aws','backup','start-restore-job','--region','us-east-2',
            '--recovery-point-arn',arn,'--iam-role-arn',role,'--resource-type',point['type'],
            '--metadata',json.dumps(metadata,sort_keys=True),
            '--idempotency-token',canonical_hash(point)[:50]])
    if not commands:raise ValueError('No verified backups; never create empty data as a substitute')
    return {'mode':'plan-only','manifest_hash':canonical_hash(manifest),'commands':commands,
            'post_restore':['wait-for-completed','verify-counts-and-checksums','set-table-deletion-protection-and-PITR','retain-restored-volumes','bind-static-PV/PVC-by-new-volume-ID','do-not-activate-writers']}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('manifest',type=Path)
    print(json.dumps(compile_plan(json.loads(p.parse_args().manifest.read_text())),indent=2))
