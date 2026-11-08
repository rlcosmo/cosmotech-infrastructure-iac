"""Snapshot/restore existing application processes without changing their config."""
import argparse
import base64
import copy
import hashlib
import urllib.request
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


ITSM='i-0def4e806c49cf60e'
FLOW={'namespace':'cosmotech.operations','id':'promotion_reconcile'}
FLOW_PATH='/api/v1/main/flows/'+FLOW['namespace']+'/'+FLOW['id']


def kestra_request(path, *, method='GET', payload=None):
    allowed={FLOW_PATH,'/api/v1/main/flows/disable/by-ids','/api/v1/main/flows/enable/by-ids'}
    if path not in allowed and not (method=='GET' and re.fullmatch(re.escape(FLOW_PATH)+r'\?revision=[0-9]{1,9}',path)):
        raise ValueError('Unreviewed Kestra operation')
    credential=Path('/opt/cosmotech/secrets/itsm/runtime.env')
    info=credential.lstat()
    if info.st_uid!=0 or info.st_mode&0o022 or credential.is_symlink():raise ValueError('Unprotected host credential source')
    values=dict(line.split('=',1) for line in credential.read_text().splitlines() if '=' in line and not line.startswith('#'))
    token=base64.b64encode((values['KESTRA_ADMIN_USERNAME']+':'+values['KESTRA_ADMIN_PASSWORD']).encode()).decode()
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    request=urllib.request.Request('https://kestra.cosmotechsolutions.com.br'+path,method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization':'Basic '+token,'Content-Type':'application/json'})
    with urllib.request.build_opener(NoRedirect).open(request,timeout=20) as response:
        raw=response.read(2_000_000)
        return json.loads(raw) if raw else {}


def automation_state():
    flow=kestra_request(FLOW_PATH)
    if any(flow.get(k)!=v for k,v in FLOW.items()):raise ValueError('Unexpected scheduler identity')
    return {**FLOW,'disabled':bool(flow.get('disabled',False)),
        'spec_hash':flow_hash(flow),'hash_schema':2,'revision':flow['revision']}


def flow_hash(flow, *, legacy=False):
    ignored={'disabled','revision','source'}
    if not legacy:ignored.add('updated')
    return hashlib.sha256(json.dumps({k:v for k,v in flow.items() if k not in ignored},sort_keys=True).encode()).hexdigest()


def normalize_legacy_snapshot(baseline,host):
    original=baseline.get('automation')
    if host!=ITSM or not original or original.get('hash_schema')==2:return baseline
    # Recover an immutable v1 snapshot using the authoritative revision history.
    # Never accept the current configuration merely because its timestamp changed.
    current=kestra_request(FLOW_PATH);revision=int(current['revision'])
    for number in range(revision,max(0,revision-8),-1):
        historical=kestra_request(FLOW_PATH+'?revision='+str(number))
        if any(historical.get(k)!=v for k,v in FLOW.items()):raise ValueError('Unexpected historical scheduler identity')
        if flow_hash(historical,legacy=True)==original['spec_hash']:
            result=copy.deepcopy(baseline)
            result['automation'].update(spec_hash=flow_hash(historical),hash_schema=2,revision=number)
            return result
    raise ValueError('Original scheduler revision not proven; refuse recovery overwrite')


def automation(baseline, host, *, resume=False):
    if host!=ITSM:raise ValueError('Scheduler operation outside ITSM host')
    original=baseline.get('automation')
    if not original:raise ValueError('Original scheduler snapshot missing')
    current=automation_state()
    if current['spec_hash']!=original['spec_hash']:raise ValueError('Scheduler configuration drift; refuse overwrite')
    disabled=original['disabled'] if resume else True
    if current['disabled']!=disabled:
        action='disable' if disabled else 'enable'
        kestra_request('/api/v1/main/flows/'+action+'/by-ids',method='POST',payload=[FLOW])
    restored=automation_state()
    if restored['disabled']!=disabled or restored['spec_hash']!=original['spec_hash']:raise ValueError('Scheduler state change not verified')
    if not resume:
        deadline=time.monotonic()+360;empty_samples=0
        while time.monotonic()<deadline:
            active=[]
            for key,value in [('ExecutionStage','Executing'),('Status','Pending')]:
                data=json.loads(command(['aws','ssm','list-commands','--region','us-east-1','--filters','key='+key+',value='+value,'--output','json']))
                active.extend(c for c in data.get('Commands',[]) if c['DocumentName'] not in {'CosmoTech-Power-Read','CosmoTech-Power-Execute','CosmoTech-Power-Host-Read','CosmoTech-Power-Host-Execute'})
            empty_samples=0 if active else empty_samples+1
            if empty_samples>=2:break
            time.sleep(5)
        else:raise ValueError('External automation did not drain; do not stop compute')
    return {'complete':True,'scheduler_disabled':disabled,'resumed':resume,'drained':not resume}


def collect(host):
    result={'host':host,'observed_at':int(time.time()),'units':{},'containers':[],'warnings':[]}
    if host==ITSM:result['automation']=automation_state()
    for unit in UNITS:
        if command(['systemctl','is-active','--quiet',unit],required=False) is not None:result['units'][unit]=True
    ids=command(['docker','ps','-aq'],required=False)
    if ids is None and result['units'].get('docker'):raise ValueError('Docker state unavailable; do not power off')
    if ids:
        for cid in ids.split():
            fmt='{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},"image_ref":{{json .Config.Image}},"running":{{json .State.Running}},"restart":{{json .HostConfig.RestartPolicy}},"mounts":{{json .Mounts}}}'
            item=json.loads(command(['docker','inspect','--format',fmt,cid]))
            item['ephemeral']=item.get('image_ref','').startswith('cosmotech-automation-runner:') and item['restart']['Name']=='no'
            result['containers'].append(item)
            if item.get('ephemeral'):result['warnings'].append('Runner efêmero será drenado; não reiniciar tarefa pelo ID: '+item['name'])
            elif item['running'] and item['restart']['Name']=='no':result['warnings'].append('Processo sem reinício automático será restaurado pelo ID original: '+item['name'])
    return result


def compare(baseline,current):
    differences=[];by={r['id']:r for r in current['containers']}
    if baseline.get('automation',{}).get('spec_hash')!=current.get('automation',{}).get('spec_hash'):differences.append('Scheduler configuration drift')
    for unit in baseline['units']:
        if not current['units'].get(unit):differences.append('Unidade não ativa: '+unit)
    for old in baseline['containers']:
        if not old['running'] or old.get('ephemeral'):continue
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
        if not old['running'] or old.get('ephemeral'):continue
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
    parser.add_argument('--version',default='NONE');parser.add_argument('--mode',choices=['SNAPSHOT','SAFE_TEST','RESTORE','VERIFY','PAUSE_AUTOMATION','RESUME_AUTOMATION'],required=True)
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
        baseline=normalize_legacy_snapshot(baseline,host)
        if args.mode in ('PAUSE_AUTOMATION','RESUME_AUTOMATION'):
            result=automation(baseline,host,resume=args.mode=='RESUME_AUTOMATION')
        else:result=restore(baseline,host) if args.mode=='RESTORE' else {'complete':not compare(baseline,collect(host)),'differences':compare(baseline,collect(host))}
        result.update(kind='host',host=host)
    print(json.dumps(result))
    if result.get('complete') is False:raise SystemExit(2)

if __name__=='__main__':main()
