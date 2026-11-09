#!/usr/bin/env python3
"""Build/validate the primary rollout KIT in a disposable checkout, never deploy."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[2]
SOURCE='8cf95ca042ee90a8c5697603029d0bb8be4eca62'
FILES=['control.py','outbox.py','dynamo_outbox.py','reconciliation.py','integrity.py']


def prepare(checkout):
    checkout=checkout.resolve()
    if '.dr-fixtures' not in checkout.parts and not str(checkout).startswith('/tmp/dr-api-integration-'):
        raise ValueError('Disposable integration checkout required; no active application workspace')
    sha=subprocess.check_output(['git','-C',str(checkout),'rev-parse','HEAD'],text=True).strip()
    if sha!=SOURCE:raise ValueError('Reviewed API release SHA required; rebase/review patch for a newer source')
    if subprocess.check_output(['git','-C',str(checkout),'status','--porcelain'],text=True).strip():
        raise ValueError('Integration checkout must be pristine')
    patch=ROOT/'ops/dr/integrations/primary-outbox.patch'
    subprocess.run(['git','-C',str(checkout),'apply','--check',str(patch)],check=True)
    subprocess.run(['git','-C',str(checkout),'apply',str(patch)],check=True)
    dest=checkout/'src/orders/shared/dr_outbox';dest.mkdir()
    (dest/'__init__.py').write_text('"""Vendored reviewed DR outbox core; source hashes in integration-manifest.json."""\n')
    hashes={}
    for name in FILES:
        src=ROOT/'src/disaster_recovery'/name;shutil.copyfile(src,dest/name)
        hashes[name]=hashlib.sha256(src.read_bytes()).hexdigest()
    manifest={'api_source':SOURCE,'core_sha256':hashes,'deployment_performed':False,'activation_default':False}
    (dest/'integration-manifest.json').write_text(json.dumps(manifest,indent=2))
    return manifest


def main():
    p=argparse.ArgumentParser();p.add_argument('--checkout',type=Path,required=True)
    print(json.dumps(prepare(p.parse_args().checkout),indent=2))

if __name__=='__main__':main()
