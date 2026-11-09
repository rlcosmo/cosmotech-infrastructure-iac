#!/usr/bin/env python3
"""Destination-only image copy, disabled without --execute + recovery evidence.

Requires skopeo and temporary ECR credentials already in a private auth file.
Never accepts a primary destination, mutable image digest or static AWS key.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from src.disaster_recovery.control import preparation_gate


def commands(lock):
    if lock.get('region')!='us-east-2' or lock.get('account')!='414053644145':
        raise ValueError('Wrong image destination')
    result=[]
    for image in lock['images'].values():
        if not re.fullmatch(r'414053644145\.dkr\.ecr\.us-east-2\.amazonaws\.com/aws-eks-istio-lab-dev-orders-(api|worker)@sha256:[a-f0-9]{64}',image):
            raise ValueError('Only reviewed immutable Orders images are allowed')
        source=image.replace('.us-east-2.', '.us-east-1.')
        digest=image.split('@sha256:')[1]
        target=image.split('@')[0]+':dr-'+digest
        # --all + --preserve-digests ensures manifest-list/architecture digests stay identical.
        result.append(['skopeo','copy','--all','--preserve-digests','docker://'+source,'docker://'+target])
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('lock',type=Path);p.add_argument('--evidence',type=Path)
    p.add_argument('--execute',action='store_true');args=p.parse_args()
    work=commands(json.loads(args.lock.read_text()))
    if args.execute:
        if not args.evidence:raise SystemExit('Verified release/recovery evidence is required')
        preparation_gate(json.loads(args.evidence.read_text()),datetime.now(timezone.utc))
        identity=json.loads(subprocess.check_output(['aws','sts','get-caller-identity','--region','us-east-2']))
        if identity['Account']!='414053644145' or ':assumed-role/' not in identity['Arn']:
            raise SystemExit('Use an authorized temporary non-root image-copy role')
        for command in work:subprocess.run(command,check=True)
    else:
        print(json.dumps({'mode':'plan-only','commands':work},indent=2))
