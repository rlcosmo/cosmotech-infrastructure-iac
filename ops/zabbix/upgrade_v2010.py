"""Bounded, non-destructive Zabbix backup and isolated restore rehearsal."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path('/opt/cosmotech/zabbix')
SERVICES = ['telemetry','web','proxy','server','clickhouse','postgres']
TARGET = {
 'server':'zabbix/zabbix-server-pgsql@sha256:1bf249a89ec489fca1ac0a3a638ca9d5e667dba415c275fd79e6b55cb83e4cc6',
 'web':'zabbix/zabbix-web-nginx-pgsql@sha256:dacace5416e2447ff18e8a10214025cffaf09eb1ec5c3e6952aeeaf3eb163eda',
 'proxy':'zabbix/zabbix-proxy-sqlite3@sha256:85083a36eda0d382aaf4111d9d79d026a734d317dcd02a9b6a775115d60ea930',
 'clickhouse':'clickhouse/clickhouse-server@sha256:0805f9f529673d2a68d1fb50dfef90f30f1710bb1b62d4c3f1d1a99d37459ecc'}


def run(args, *, input=None, timeout=120, output=None):
    r=subprocess.run(args,cwd=ROOT,input=input,stdout=output or subprocess.PIPE,
                     stderr=subprocess.PIPE,timeout=timeout)
    if r.returncode:raise RuntimeError(f'{Path(args[0]).name} failed (output withheld)')
    return r.stdout


def compose(*args, **kw):return run(['docker','compose',*args],**kw)
def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''):h.update(part)
    return h.hexdigest()


def backup():
    os.umask(0o077)
    destination=ROOT/'backups'/('v2010-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    destination.mkdir(mode=0o700)
    metadata={'created_at':datetime.now(timezone.utc).isoformat(),'services':{},'archives':{},'original_volumes_preserved':True}
    for service in SERVICES:
        cid=compose('ps','-q',service).decode().strip()
        if not cid:raise RuntimeError('All existing services must be available before backup')
        obj=json.loads(run(['docker','inspect',cid]))[0]
        metadata['services'][service]={'image':obj['Config']['Image'],'mounts':[{'name':m['Name'],'destination':m['Destination']} for m in obj['Mounts'] if m['Type']=='volume']}
    with (destination/'postgres.dump').open('wb') as f:
        compose('exec','-T','postgres','pg_dump','-U','zabbix','-Fc','zabbix',output=f)
    run(['tar','-C',str(ROOT),'--exclude=./backups','-czf',str(destination/'configuration.tar.gz'),'.'])
    run(['tar','-C','/','-czf',str(destination/'agent-configuration.tar.gz'),'etc/zabbix'])
    try:
        compose('stop','--timeout','60',*SERVICES)
        volume_names=sorted({m['name'] for s in metadata['services'].values() for m in s['mounts']})
        for index,name in enumerate(volume_names):
            mount=json.loads(run(['docker','volume','inspect',name]))[0]['Mountpoint']
            archive='volume-'+str(index)+'.tar.gz'
            run(['tar','--numeric-owner','--use-compress-program=gzip -1','-cf',str(destination/archive),'-C',mount,'.'],timeout=900)
            run(['tar','-tf',str(destination/archive)],timeout=900)
            metadata['archives'][name]={'file':archive,'sha256':digest(destination/archive),'bytes':(destination/archive).stat().st_size}
        for file in ['postgres.dump','configuration.tar.gz','agent-configuration.tar.gz']:
            metadata[file]={'sha256':digest(destination/file),'bytes':(destination/file).stat().st_size}
        (destination/'manifest.json').write_text(json.dumps(metadata,indent=2)+'\n')
        os.sync()
    finally:
        # start reuses the original containers/volumes; never down, prune or recreate.
        compose('start',timeout=120)
    print(json.dumps({'backup':str(destination),'archives':len(metadata['archives']),
        'bytes':sum(x['bytes'] for x in metadata['archives'].values()),'cold_tar_verified':True,
        'original_containers_resumed':True}),flush=True)


def verify_archives(directory):
    directory=Path(directory).resolve()
    directory.relative_to(ROOT/'backups')
    m=json.loads((directory/'manifest.json').read_text())
    for entry in m['archives'].values():
        file=directory/entry['file']
        if file.parent!=directory or digest(file)!=entry['sha256']:raise ValueError('Backup integrity mismatch')
    for name in ['postgres.dump','configuration.tar.gz','agent-configuration.tar.gz']:
        if digest(directory/name)!=m[name]['sha256']:raise ValueError('Configuration backup integrity mismatch')
    return directory,m


def checkpoint(directory, old_clickhouse_image):
    """Fresh application data checkpoint; full cold volumes remain the rollback baseline."""
    from xml.sax.saxutils import escape
    directory=Path(directory)/('checkpoint-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    directory.mkdir(mode=0o700)
    with (directory/'postgres.dump').open('wb') as f:
        compose('exec','-T','postgres','pg_dump','-U','zabbix','-Fc','zabbix',output=f)
    password=(ROOT/'secrets/clickhouse_password').read_text().strip()
    config=('<config><host>localhost</host><port>9000</port><user>zabbix</user><password>'+escape(password)+'</password></config>').encode()
    client='umask 077; f=$(mktemp); cat > "$f"; clickhouse-client --config-file "$f" --query "$1"; rc=$?; rm -f "$f"; exit "$rc"'
    result={'postgres_dump_sha256':digest(directory/'postgres.dump'),'tables':{}}
    for table in ['otel_traces','otel_logs','otel_metrics_sum','otel_metrics_gauge','otel_metrics_histogram','otel_metrics_exponential_histogram']:
        with (directory/(table+'.native')).open('wb') as f:
            compose('exec','-T','clickhouse','sh','-c',client,'client','SELECT * FROM zabbix.'+table+' FORMAT Native',input=config,output=f)
        expected=int(compose('exec','-T','clickhouse','sh','-c',client,'client','SELECT count() FROM zabbix.'+table,input=config).strip())
        ddl=compose('exec','-T','clickhouse','sh','-c',client,'client','SHOW CREATE TABLE zabbix.'+table+' FORMAT TSVRaw',input=config)
        (directory/(table+'.sql')).write_bytes(ddl)
        # Empty Native exports contain no schema; the matching DDL is saved above.
        if (directory/(table+'.native')).stat().st_size==0:
            observed=0
        else:
            observed=int(run(['docker','run','--rm','--network','none','--memory','512m','--volume',str(directory)+':/checkpoint:ro','--entrypoint','clickhouse',old_clickhouse_image,'local','--query',"SELECT count() FROM file('/checkpoint/"+table+".native', Native)"]).strip())
        if expected!=observed:raise RuntimeError('Native checkpoint validation failed')
        result['tables'][table]={'rows':expected,'sha256':digest(directory/(table+'.native'))}
    (directory/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    os.sync()
    print(json.dumps({'fresh_checkpoint':str(directory),'native_tables_verified':len(result['tables'])}),flush=True)
    return directory


def verify_live_snapshot(snapshot):
    """Historical backup metadata is not proof that the off-host snapshot still exists."""
    import re
    ident=snapshot.get('SnapshotId','')
    if not isinstance(ident,str) or not re.fullmatch(r'snap-[0-9a-f]{8,17}',ident):
        raise ValueError('A valid snapshot identifier is required')
    response=json.loads(run(['aws','--region','us-east-1','ec2','describe-snapshots','--snapshot-ids',ident,'--output','json']))
    snapshots=response.get('Snapshots',[])
    if len(snapshots)!=1:
        raise ValueError('Required off-host snapshot is unavailable')
    live=snapshots[0]
    if (live.get('SnapshotId')!=ident or live.get('State')!='completed'
            or live.get('Encrypted') is not True or live.get('VolumeId')!=snapshot.get('VolumeId')):
        raise ValueError('Live off-host snapshot does not match the verified backup')


def apply(directory, source):
    import shutil
    directory,manifest=verify_archives(directory)
    if json.loads((directory/'rehearsal-ok.json').read_text()).get('rehearsal')!='passed':
        raise ValueError('A successful isolated restore rehearsal is required')
    snapshot=json.loads((directory/'snapshot-verified.json').read_text())
    if (snapshot.get('State')!='completed' or snapshot.get('Encrypted') is not True
            or snapshot.get('VolumeId')!='vol-0cf8b3eaf7cc6ea10'):
        raise ValueError('Completed encrypted off-host snapshot is required')
    verify_live_snapshot(snapshot)
    if shutil.disk_usage(ROOT).free<25*1024**3:
        raise ValueError('Reserve at least 25 GiB for non-destructive rollback copies')
    source=Path(source).resolve();source.relative_to('/opt/cosmotech')
    images=json.loads((source/'images.json').read_text())
    if any(images[k]['image']!=v for k,v in TARGET.items()):raise ValueError('Unreviewed image target')
    previous=json.loads(compose('config','--format','json'))
    if images['postgres']['image']!=previous['services']['postgres']['image']:
        raise ValueError('PostgreSQL upgrade is outside this operation')
    (directory/'before-apply-compose.json').write_text(json.dumps(previous))
    (directory/'before-apply-compose.json').chmod(0o600)
    for name in ['docker-compose.yml','images.json']:
        shutil.copyfile(source/name,ROOT/name)
    runtime=ROOT/'v2010';runtime.mkdir(mode=0o755,exist_ok=True)
    for file in (source/'v2010').iterdir():
        if file.is_file():shutil.copyfile(file,runtime/file.name);(runtime/file.name).chmod(0o644)
    run(['python3',str(runtime/'install_runtime.py')])
    compose('config','--quiet')
    compose('build','web','notifications',timeout=600)
    # The application collectors and every existing data volume remain in place.
    changed_database=False
    try:
        compose('stop','--timeout','60','telemetry','web','proxy','server')
        fresh=checkpoint(directory,manifest['services']['clickhouse']['image'])
        compose('stop','--timeout','60','clickhouse')
        changed_database=True
        compose('up','-d','postgres','clickhouse','notifications','server','proxy','web','telemetry',timeout=180)
        for _ in range(60):
            try:
                response=run(['curl','--silent','--show-error','--max-time','5','--header','Content-Type: application/json',
                    '--data','{"jsonrpc":"2.0","id":1,"method":"apiinfo.version","params":{}}',
                    'http://127.0.0.1:8088/api_jsonrpc.php'],timeout=10)
                if json.loads(response).get('result')=='8.0.0':break
            except (RuntimeError,ValueError,subprocess.TimeoutExpired):pass
            time.sleep(2)
        else:raise RuntimeError('Upgraded frontend did not become ready')
        for service,binary in [('server','zabbix_server'),('proxy','zabbix_proxy')]:
            version=compose('exec','-T',service,binary,'--version').decode().splitlines()[0]
            if '8.0.0' not in version or 'rc' in version:raise RuntimeError('Non-GA component')
        for name in ['cosmotech-zabbix-postgres','cosmotech-zabbix-clickhouse','cosmotech-zabbix-proxy']:
            service=next(k for k,v in manifest['services'].items() if any(m['name']==name for m in v['mounts']))
            current=json.loads(run(['docker','inspect',compose('ps','-q',service).decode().strip()]))[0]
            if name not in [m.get('Name') for m in current['Mounts']]:raise RuntimeError('Original data volume changed')
        result={'core_upgrade':'passed','version':'8.0.0','postgres_image_preserved':True,
                'original_named_volumes_preserved':True,'snapshot':snapshot['SnapshotId'],'fresh_checkpoint':str(fresh),
                'operational_certification':'pending full APM/bootstrap/webhook validation'}
        (directory/'applied.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result),flush=True)
    except Exception:
        if not changed_database:
            (ROOT/'docker-compose.yml').write_text(json.dumps(previous))
            compose('start',*SERVICES)
        # Never downgrade a changed database in place. Keep all originals for inspection.
        (directory/'apply-failed.json').write_text(json.dumps({'failed_at':datetime.now(timezone.utc).isoformat(),
            'data_volumes_preserved':True,'rollback':'restore archives into fresh directories; never erase current data'}))
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['backup','verify','apply']);parser.add_argument('--backup');parser.add_argument('--source')
    args=parser.parse_args()
    if args.mode=='backup':backup()
    elif args.mode=='apply':apply(args.backup,args.source)
    else:
        directory,_=verify_archives(args.backup);print(json.dumps({'backup_integrity_verified':True,'backup':str(directory)}))
