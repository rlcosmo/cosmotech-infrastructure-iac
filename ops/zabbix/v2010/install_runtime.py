"""Install only the new service's files/credentials; never rotate Zabbix identities."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from bootstrap import ROOT, credentials

os.umask(0o077)
source=Path(__file__).resolve().parent
runtime=ROOT/'v2010';runtime.mkdir(mode=0o755,exist_ok=True)
for name in ['Dockerfile','Dockerfile.web','notifications.py','index.html','nginx-location.conf','bootstrap.py','fault_lab.py']:
    if (source/name).resolve()!=(runtime/name).resolve():shutil.copyfile(source/name,runtime/name)
    (runtime/name).chmod(0o644)
value=credentials()
data=ROOT/'notifications';data.mkdir(mode=0o750,exist_ok=True);os.chown(data,10001,10001)
record=ROOT/'secrets/credentials.json'
if record.exists():
    current=json.loads(record.read_text());current['notifications_reader']={'username':value['username'],'password':value['password'],'url':'https://zabbix.cosmotechsolutions.com.br/lab-notifications/'}
    record.write_text(json.dumps(current));record.chmod(0o600)
    result=subprocess.run(['aws','--region','us-east-1','ssm','put-parameter','--name','/cosmotech/zabbix/credentials','--type','SecureString','--overwrite','--value','file://'+str(record)],capture_output=True,text=True,timeout=30)
    if result.returncode:raise RuntimeError('Credential catalog update failed; values withheld')
print(json.dumps({'notification_runtime_installed':True,'existing_zabbix_identity_preserved':True,'credential_values_printed':False}))
