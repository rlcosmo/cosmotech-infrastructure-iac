"""Apply only Worker controller-owned ConfigMap ignore fields, preserving existing rules.

Run on the Argo host with its existing KUBECONFIG. No API changes.
"""
import json
import subprocess

name='orders-worker-dev'
a=json.loads(subprocess.check_output(['kubectl','-n','argocd','get','application',name,'-o','json']))
rules=a['spec'].get('ignoreDifferences',[])
for resource,field in [('orders-worker-canary-control','/data/lease.json'),('orders-worker-stable-control','/data/mode')]:
 r={'group':'','kind':'ConfigMap','namespace':'orders-worker','name':resource,'jsonPointers':[field]}
 if r not in rules:rules.append(r)
patch={'spec':{'ignoreDifferences':rules}}
subprocess.run(['kubectl','-n','argocd','patch','application',name,'--type=merge','-p',json.dumps(patch),'--dry-run=server'],check=True,capture_output=True)
subprocess.run(['kubectl','-n','argocd','patch','application',name,'--type=merge','-p',json.dumps(patch)],check=True,capture_output=True)
print(json.dumps({'worker_control_fields_ignored':2,'existing_rules_preserved':True,'api_changed':False}))
