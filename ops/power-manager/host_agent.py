"""Snapshot/restore existing application processes without changing their config."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time

ALLOWED_HOSTS={'i-0def4e806c49cf60e','i-0b33fdd51332e04e5','i-02878f35eed1572d0'}
UNITS=['docker','k3s','zabbix-server','zabbix-agent2','mariadb','postgresql','nginx']


def command(args,required=True):
    try:r=subprocess.run(args,capture_output=True,text=True,timeout=90)
    except FileNotFoundError:
        if required:raise ValueError('Required executable missing')
        return None
    if r.returncode:
        if required:raise ValueError('Host command failed: '+args[0])
        return None
    return r.stdout.strip()


def collect(host):
    result={'host':host,'observed_at':int(time.time()),'units':{},'containers':[],'warnings':[]}
    for unit in UNITS:
        if command(['systemctl','is-active','--quiet',unit],required=False) is not None:result['units'][unit]=True
    ids=command(['docker','ps','-aq'],required=False)
    if ids is None and result['units'].get('docker'):raise ValueError('Docker state unavailable; do not power off')
    if ids:
        for cid in ids.split():
            fmt='{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},"running":{{json .State.Running}},"restart":{{json .HostConfig.RestartPolicy}},"mounts":{{json .Mounts}}}'
            item=json.loads(command(['docker','inspect','--format',fmt,cid]));result['containers'].append(item)
            if item['running'] and item['restart']['Name']=='no':result['warnings'].append('Processo sem reinício automático será restaurado pelo ID original: '+item['name'])
    return result


def compare(baseline,current):
    differences=[];by={r['id']:r for r in current['containers']}
    for unit in baseline['units']:
        if not current['units'].get(unit):differences.append('Unidade não ativa: '+unit)
    for old in baseline['containers']:
        if not old['running']:continue
        now=by.get(old['id'])
        if not now or now['image']!=old['image'] or not now['running']:differences.append('Container não recuperado: '+old['name'])
        elif sorted(now['mounts'],key=lambda m:json.dumps(m,sort_keys=True))!=sorted(old['mounts'],key=lambda m:json.dumps(m,sort_keys=True)):differences.append('Mounts divergentes: '+old['name'])
    return differences


def restore(baseline,host):
    current=collect(host);started_unit=False
    for unit in baseline['units']:
        if unit not in UNITS:raise ValueError('Unit outside allowlist')
        if not current['units'].get(unit):
            command(['systemctl','start',unit]);started_unit=True
    if started_unit:current=collect(host)
    by={r['id']:r for r in current['containers']}
    def priority(item):return 0 if any(s in item['name'].lower() for s in ('database','postgres','mariadb','-db')) else 1
    for old in sorted(baseline['containers'],key=priority):
        if not old['running']:continue
        now=by.get(old['id'])
        if not now or now['image']!=old['image']:raise ValueError('Container/image drift; do not recreate or pull')
        if not re.fullmatch('[0-9a-f]{64}',old['id']):raise ValueError('Invalid container ID')
        if not now['running']:command(['docker','start',old['id']])
    deadline=time.time()+240
    while time.time()<deadline:
        differences=compare(baseline,collect(host))
        if not differences:return {'complete':True,'differences':[]}
        time.sleep(5)
    return {'complete':False,'differences':differences}


def main():
    parser=argparse.ArgumentParser()
    for arg in ['bucket','plan-id','operation-id']:parser.add_argument('--'+arg,required=True)
    parser.add_argument('--version',default='NONE');parser.add_argument('--mode',choices=['SNAPSHOT','SAFE_TEST','RESTORE','VERIFY'],required=True)
    args=parser.parse_args()
    if not all(re.fullmatch('[0-9a-f-]{36}',x) for x in [args.plan_id,args.operation_id]):raise ValueError('Invalid operation IDs')
    os.environ['AWS_DEFAULT_REGION']='us-east-1';os.environ['AWS_REGION']='us-east-1'
    arn=json.loads(command(['aws','--region','us-east-1','sts','get-caller-identity']))['Arn'];host=arn.rsplit('/',1)[-1]
    if host not in ALLOWED_HOSTS or not arn.startswith('arn:aws:sts::414053644145:assumed-role/'):raise ValueError('Expected non-root instance role')
    directory=Path('/var/lib/cosmotech-power')/args.operation_id;directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    key=f'plans/{args.plan_id}/hosts/{host}.json';path=directory/'host.json'
    if args.mode in ['SNAPSHOT','SAFE_TEST']:
        data=collect(host);path.write_text(json.dumps(data));path.chmod(0o600)
        uploaded=json.loads(command(['aws','s3api','put-object','--bucket',args.bucket,'--key',key,'--body',str(path),'--server-side-encryption','AES256']))
        result={'kind':'host','host':host,'reference':{'key':key,'version':uploaded['VersionId']},'warnings':data['warnings'],
                'safe_restore_validated':True,'containers':len(data['containers']),'mutated':False}
    else:
        if args.version=='NONE':raise ValueError('Immutable snapshot version required')
        command(['aws','s3api','get-object','--bucket',args.bucket,'--key',key,'--version-id',args.version,str(path)])
        baseline=json.loads(path.read_text())
        if baseline['host']!=host:raise ValueError('Snapshot belongs to another host')
        result=restore(baseline,host) if args.mode=='RESTORE' else {'complete':not compare(baseline,collect(host)),'differences':compare(baseline,collect(host))}
        result.update(kind='host',host=host)
    print(json.dumps(result))
    if result.get('complete') is False:raise SystemExit(2)

if __name__=='__main__':main()
