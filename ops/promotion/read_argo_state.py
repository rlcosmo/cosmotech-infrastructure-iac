"""Emit bounded, credential-free Argo state from the fixed management host."""
import json
import subprocess
import sys

application=sys.argv[1]
if application not in {'orders-api','orders-worker'}:
    raise ValueError('Unsupported application')
def read(kind,name):
    return json.loads(subprocess.check_output(['kubectl','-n','argocd','get',kind,name,'-o','json']))
app=read('application',application+'-dev')
project=read('appproject','orders-dev')
print(json.dumps({'application':{'name':app['metadata']['name'],'spec':app['spec'],
      'status':{k:app.get('status',{}).get(k) for k in ['sync','health','conditions']}},
      'project':project['spec']}))
