#!/usr/bin/env python3
"""Synthetic-only E2E probe through a local authenticated DR operator tunnel.
Rejects arbitrary URLs and never targets any primary hostname. Records no payloads.
"""
import argparse
from datetime import datetime, timezone
import json
import time
import urllib.request
import uuid
import subprocess
from pathlib import Path
import urllib.error


def verify_target(kubeconfig):
    identity=json.loads(subprocess.check_output(['aws','sts','get-caller-identity','--region','us-east-2']))
    if identity['Account']!='414053644145' or ':assumed-role/' not in identity['Arn']:
        raise ValueError('Temporary non-root DR operator required')
    cluster=json.loads(subprocess.check_output(['aws','eks','describe-cluster','--name','cosmotech-dr','--region','us-east-2']))['cluster']
    if cluster['arn']!='arn:aws:eks:us-east-2:414053644145:cluster/cosmotech-dr':
        raise ValueError('Wrong cluster/account/region')
    config=json.loads(subprocess.check_output(['kubectl','--kubeconfig',str(kubeconfig),'config','view','--minify','-o','json']))
    if len(config['clusters'])!=1 or config['clusters'][0]['cluster']['server']!=cluster['endpoint']:
        raise ValueError('Kubeconfig does not target the verified destination cluster')


def probe(base='http://127.0.0.1:18080',timeout=120):
    if base != 'http://127.0.0.1:18080' or not 1 <= timeout <= 120:
        raise ValueError('Only the verified DR tunnel and bounded synthetic probe are allowed')
    # Called only after this process creates a tunnel to the verified DR context.
    key='dr-synthetic-'+str(uuid.uuid4())
    event={'order_id':key,'items':[{'sku':'LAB-SKU-001','quantity':1}]}
    req=urllib.request.Request(base+'/orders',data=json.dumps(event).encode(),
        headers={'Content-Type':'application/json','Host':'orders.dr-test.internal'})
    start=time.monotonic()
    with urllib.request.urlopen(req,timeout=5) as response:
        if response.status!=202:raise RuntimeError('Durable acceptance not verified')
        value=json.load(response)
        if value.get('order_id')!=key:raise RuntimeError('Unexpected acceptance ID')
    deadline=start+timeout
    while time.monotonic()<deadline:
        req=urllib.request.Request(base+'/orders/'+key,headers={'Host':'orders.dr-test.internal'})
        try:
            with urllib.request.urlopen(req,timeout=5) as response:result=json.load(response)
        except urllib.error.HTTPError as error:
            if error.code!=404:raise
            time.sleep(1);continue
        if result.get('status')=='COMPLETED':
            return dict(source='aws-e2e',observed_at=datetime.now(timezone.utc).isoformat(),
                        synthetic=True,order_completed=True,latency_seconds=round(time.monotonic()-start,3),
                        region='us-east-2',rto_seconds=None,rpo_seconds=None)
        if result.get('status')=='REJECTED':raise RuntimeError('Synthetic recovery order rejected; verify isolated inventory')
        time.sleep(1)
    raise TimeoutError('E2E recovery not proven; do not promote')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--kubeconfig',type=Path,required=True)
    args=p.parse_args();verify_target(args.kubeconfig)
    tunnel=subprocess.Popen(['kubectl','--kubeconfig',str(args.kubeconfig),'-n','istio-system',
        'port-forward','--address=127.0.0.1','svc/istio-ingress','18080:80'],
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    try:
        # First successful forwarding line binds the port; an existing tunnel fails.
        line=tunnel.stdout.readline()
        if not line.startswith('Forwarding from 127.0.0.1:18080'):
            raise RuntimeError('Could not establish own verified DR tunnel; no order sent')
        print(json.dumps(probe(),indent=2))
    finally:
        tunnel.terminate()
        try:tunnel.wait(timeout=5)
        except subprocess.TimeoutExpired:tunnel.kill();tunnel.wait()
