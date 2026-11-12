#!/usr/bin/env python3
"""Verify pinned chart archive SHA256 and render locally. No helm install/apply."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import urllib.request

ROOT=Path(__file__).resolve().parents[2]

def render(destination):
    charts=json.loads((ROOT/'ops/dr/platform-lock.json').read_text())['charts']
    values=json.loads((ROOT/'ops/dr/platform-values.json').read_text())
    destination.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cosmotech-dr-charts-') as folder:
        folder=Path(folder)
        for chart in charts:
            archive=folder/(chart['release']+'.tgz')
            with urllib.request.urlopen(chart['archive_url'],timeout=60) as response:
                archive.write_bytes(response.read())
            if hashlib.sha256(archive.read_bytes()).hexdigest()!=chart['upstream_chart_digest']:
                raise ValueError('Chart digest mismatch: '+chart['release'])
            config=folder/(chart['release']+'.json')
            config.write_text(json.dumps(values[chart['release']]))
            rendered=subprocess.check_output(['helm','template',chart['release'],str(archive),
                '--namespace',chart['namespace'],'--kube-version','1.36.0','--include-crds','--skip-tests','-f',str(config)])
            (destination/(chart['release']+'.yaml')).write_bytes(rendered)
    print('PASS: pinned charts SHA256 verified and Helm templates rendered; no cluster contacted')

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    render(p.parse_args().output)
