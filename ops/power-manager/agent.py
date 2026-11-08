"""Fixed SSM power agent. Never deletes data, Services, Ingress, secrets or network.

AWS identity is the existing EC2 instance role. OS execution is supplied by SSM.
All workload changes are Scale subresources in the explicitly approved namespaces.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import shlex
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request
import urllib.error

CLUSTER = 'aws-eks-istio-lab-dev'
NAMESPACES = ('istio-system','kube-system','kyverno','loki','observability','orders-api','orders-worker')
APPROVED = {
 'istio-system': {'deployments': ['istio-ingressgateway','istiod']},
 'kube-system': {'deployments': ['aws-load-balancer-controller','cluster-autoscaler','coredns','ebs-csi-controller','metrics-server','snapshot-controller']},
 'kyverno': {'deployments': ['kyverno-admission-controller','kyverno-background-controller','kyverno-cleanup-controller','kyverno-reports-controller']},
 'loki': {'deployments': ['loki-gateway'], 'statefulsets':['loki']},
 'observability': {'deployments':['otel-collector-opentelemetry-collector','prometheus-grafana','prometheus-kube-prometheus-operator','prometheus-kube-state-metrics'], 'statefulsets':['alertmanager-prometheus-kube-prometheus-alertmanager','prometheus-prometheus-kube-prometheus-prometheus','tempo']},
 'orders-api': {'deployments':['orders-api']},
 'orders-worker': {'deployments':['orders-worker','orders-worker-canary']},
}
ENDPOINTS = {'argocd':'https://argocd.cosmotechsolutions.com.br/healthz',
 'kestra':'https://kestra.cosmotechsolutions.com.br/', 'glpi':'https://glpi.cosmotechsolutions.com.br/',
 'grafana':'https://grafana.cosmotechsolutions.com.br/api/health',
 'orders_api':'https://orders-api-dev.cosmotechsolutions.com.br/health/ready',
 'orders_worker':'https://orders-worker-dev.cosmotechsolutions.com.br/health/ready',
 'prometheus':'https://prometheus.cosmotechsolutions.com.br/-/ready',
 'tempo':'https://tempo.cosmotechsolutions.com.br/ready',
 'zabbix':'https://zabbix.cosmotechsolutions.com.br/'}


def run(args, *, text=False):
    result = subprocess.run(args, capture_output=True, timeout=90, check=False)
    if result.returncode:
        raise RuntimeError('COMMAND_FAILED:' + args[0] + ':' + str(result.returncode))
    value = result.stdout.decode()
    return value if text else json.loads(value or '{}')


def hash_value(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class Agent:
    def __init__(self, bucket, plan_id, operation_id, version):
        self.bucket, self.plan_id, self.operation_id, self.version = bucket, plan_id, operation_id, version
        self.directory = Path('/var/lib/cosmotech-power') / operation_id
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.kubeconfig = str(self.directory / 'kubeconfig')
        os.environ['AWS_DEFAULT_REGION'] = 'us-east-1'
        os.environ['AWS_REGION'] = 'us-east-1'
        identity = run(['aws','sts','get-caller-identity'])['Arn']
        if not identity.startswith('arn:aws:sts::414053644145:assumed-role/cosmotech-argocd-dev-host/'):
            raise ValueError('Unexpected AWS recovery identity')
        run(['aws','eks','update-kubeconfig','--name',CLUSTER,'--region','us-east-1','--kubeconfig',self.kubeconfig], text=True)
        os.chmod(self.kubeconfig,0o600)

    def kube(self, args, local=False, text=False):
        path = '/etc/rancher/k3s/k3s.yaml' if local else self.kubeconfig
        return run(['kubectl','--kubeconfig',path,'--request-timeout=20s',*args], text=text)

    def write(self, key, value):
        path = self.directory / 'transfer.json'
        path.write_text(json.dumps(value, sort_keys=True));path.chmod(0o600)
        return run(['aws','s3api','put-object','--bucket',self.bucket,'--key',key,'--body',str(path),'--server-side-encryption','AES256'])

    def load(self):
        if not self.version or self.version == 'NONE':
            raise ValueError('Immutable snapshot version required')
        path = self.directory / 'baseline.json'
        run(['aws','s3api','get-object','--bucket',self.bucket,'--key',f'plans/{self.plan_id}/kubernetes.json','--version-id',self.version,str(path)])
        path.chmod(0o600)
        return json.loads(path.read_text())

    def git_revision(self, source):
        # Reuse the host's existing deploy key and pinned SSH host keys. Never
        # clone, checkout, execute repository code, or accept arbitrary URLs.
        if source.get('repoURL') != 'ssh://git@ssh.github.com:443/rlcosmo/cosmotech-gitops.git':
            raise ValueError('Unreviewed GitOps repository')
        target=source.get('targetRevision','')
        if re.fullmatch(r'[0-9a-f]{40}',target):return target
        if target!='main':raise ValueError('Unreviewed GitOps branch')
        hosts=self.kube(['-n','argocd','get','configmap','argocd-ssh-known-hosts-cm','-o','json'],local=True)
        pinned=hosts.get('data',{}).get('ssh_known_hosts','')
        if not pinned.strip():raise ValueError('Missing pinned GitOps SSH host keys')
        with tempfile.TemporaryDirectory(dir=self.directory) as folder:
            path=Path(folder)/'known_hosts';path.write_text(pinned);path.chmod(0o600)
            env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GIT_CONFIG_NOSYSTEM='1')
            env['GIT_SSH_COMMAND']='ssh -i /opt/cosmotech/argocd/github-deploy-key -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile='+shlex.quote(str(path))
            result=subprocess.run(['git','-c','core.hooksPath=/dev/null','ls-remote','--exit-code','--',source['repoURL'],'refs/heads/main'],env=env,capture_output=True,timeout=30,check=False)
            lines=result.stdout.decode().strip().splitlines()
            if result.returncode or len(lines)!=1:raise ValueError('Cannot verify GitOps remote revision')
            revision,ref=lines[0].split()
            if ref!='refs/heads/main' or not re.fullmatch(r'[0-9a-f]{40}',revision):raise ValueError('Invalid GitOps remote revision')
            return revision

    @staticmethod
    def check_argo_restore(baseline, current):
        apps={a['name']:a for a in current['argo']}
        if set(apps)!={a['name'] for a in baseline['argo']}:raise ValueError('Argo Application inventory drift')
        for app in baseline['argo']:
            found=apps[app['name']]
            if found['spec_hash']!=app['spec_hash']:raise ValueError('Argo Application drift')
            if found.get('remote_revision')!=app['revision']:
                raise ValueError('GitOps remote changed; keep Argo paused pending reviewed recovery')

    @staticmethod
    def passive_retained_claim(resource, volumes):
        volume=volumes.get(resource.get('spec',{}).get('volumeName'),{})
        spec=volume.get('spec',{})
        return (resource.get('status',{}).get('phase')=='Bound' and
            spec.get('persistentVolumeReclaimPolicy')=='Retain' and
            'local' not in spec and 'hostPath' not in spec)

    def collect(self):
        data = {'observed_at':int(time.time()), 'workloads':[], 'pvs':[], 'pvcs':[], 'hpa':[], 'pdb':[], 'nodes':[], 'daemonsets':[], 'argo':[], 'controllers':[], 'endpoints':{}, 'blockers':[]}
        data['namespaces']=[r['metadata']['name'] for r in self.kube(['get','namespaces','-o','json'])['items']]
        volumes=self.kube(['get','pv','-o','json'])['items']
        volume_by_name={r['metadata']['name']:r for r in volumes}
        data['storageclasses']=self.kube(['get','storageclasses','-o','json'])['items']
        data['outside_scope']=[]
        for kind in ('deployments','statefulsets','daemonsets','pods','jobs','cronjobs','pvc'):
            for resource in self.kube(['get',kind,'--all-namespaces','-o','json']).get('items',[]):
                ns=resource['metadata']['namespace']
                if ns in NAMESPACES:continue
                item={'namespace':ns,'kind':kind,'name':resource['metadata']['name']}
                if kind=='pvc':item.update(volume=resource.get('spec',{}).get('volumeName'),phase=resource.get('status',{}).get('phase'),classification='PERSISTENTES')
                data['outside_scope'].append(item)
                # Empty namespaces are harmless; any workload or persisted data
                # outside the approved mutation scope requires dependency review.
                terminal=kind in ('pods','jobs') and (resource.get('status',{}).get('phase') in ('Succeeded','Failed') or bool(resource.get('status',{}).get('completionTime')))
                persistent=kind=='pvc' and self.passive_retained_claim(resource,volume_by_name)
                if not terminal and not persistent:data['blockers'].append('Recurso fora do escopo operacional: '+ns+'/'+kind+'/'+item['name'])
        daemonsets={'kube-system':{'aws-node','ebs-csi-node','ebs-csi-node-windows','eks-pod-identity-agent','kube-proxy'},'observability':{'alloy','prometheus-prometheus-node-exporter'}}
        for ns in NAMESPACES:
            for r in self.kube(['-n',ns,'get','daemonsets','-o','json']).get('items',[]):
                name=r['metadata']['name'];data['daemonsets'].append({'namespace':ns,'name':name,'template_hash':hash_value(r['spec'].get('template',{}))})
                if name not in daemonsets.get(ns,set()):data['blockers'].append('DaemonSet fora do escopo conhecido: '+ns+'/'+name)
            for kind in ('deployments','statefulsets'):
                for r in self.kube(['-n',ns,'get',kind,'-o','json']).get('items',[]):
                    name = r['metadata']['name']
                    if name not in APPROVED[ns].get(kind,[]):
                        data['blockers'].append('Workload fora da allowlist RBAC: '+ns+'/'+name)
                    if r.get('status',{}).get('readyReplicas',0)<r['spec'].get('replicas',1):
                        data['blockers'].append('Workload não saudável: '+ns+'/'+name)
                    retention = r['spec'].get('persistentVolumeClaimRetentionPolicy',{})
                    if retention.get('whenScaled','Retain') != 'Retain':
                        data['blockers'].append('PVC whenScaled não é Retain: '+ns+'/'+name)
                    template = r['spec'].get('template',{})
                    volumes = template.get('spec',{}).get('volumes',[])
                    if any('hostPath' in v for v in volumes):
                        data['blockers'].append('Workload usa hostPath: '+ns+'/'+name)
                    data['workloads'].append({'namespace':ns,'kind':kind,'name':name,'replicas':r['spec'].get('replicas',1),
                         'ready':r.get('status',{}).get('readyReplicas',0),'template_hash':hash_value(template),
                         'images':[v['image'] for v in template.get('spec',{}).get('containers',[])], 'retention':retention})
            for kind,key in [('pvc','pvcs'),('hpa','hpa'),('pdb','pdb')]:
                for r in self.kube(['-n',ns,'get',kind,'-o','json']).get('items',[]):
                    if kind=='pvc':
                        data[key].append({'namespace':ns,'name':r['metadata']['name'],'volume':r['spec'].get('volumeName'),'phase':r.get('status',{}).get('phase')})
                        if r.get('status',{}).get('phase') != 'Bound':data['blockers'].append('PVC não Bound: '+ns+'/'+r['metadata']['name'])
                    else:data[key].append({'namespace':ns,'name':r['metadata']['name'],'spec':r['spec']})
        for r in volumes:
            data['pvs'].append({'name':r['metadata']['name'],'spec':r['spec'],'phase':r.get('status',{}).get('phase')})
            if r['spec'].get('persistentVolumeReclaimPolicy') != 'Retain' or 'local' in r['spec'] or 'hostPath' in r['spec']:
                data['blockers'].append('Volume sem retenção segura: '+r['metadata']['name'])
        for r in self.kube(['get','nodes','-o','json'])['items']:
            data['nodes'].append({'name':r['metadata']['name'],'ready':any(c['type']=='Ready' and c['status']=='True' for c in r['status']['conditions'])})
        if not data['nodes'] or not all(n['ready'] for n in data['nodes']):data['blockers'].append('Nós não estão Ready')
        apps = self.kube(['-n','argocd','get','applications','-o','json'], local=True)
        for app in apps.get('items',[]):
            spec = app['spec']
            revision=app.get('status',{}).get('sync',{}).get('revision')
            remote=self.git_revision(spec.get('source',{}))
            if remote!=revision:data['blockers'].append('GitOps remoto diverge da revisão aplicada: '+app['metadata']['name'])
            data['argo'].append({'name':app['metadata']['name'],'spec_hash':hash_value(spec),'revision':revision,'remote_revision':remote,'sync':app.get('status',{}).get('sync',{}).get('status')})
        for kind in ('deployments','statefulsets'):
            for r in self.kube(['-n','argocd','get',kind,'-o','json'],local=True).get('items',[]):
                if any(k in r['metadata']['name'] for k in ('application-controller','applicationset-controller')):
                    data['controllers'].append({'kind':kind,'name':r['metadata']['name'],'replicas':r['spec'].get('replicas',1)})
        for name,url in ENDPOINTS.items():
            try:
                with urllib.request.urlopen(url,timeout=8) as r:data['endpoints'][name]=r.status
            except urllib.error.HTTPError as e:data['endpoints'][name]=e.code
            except (TimeoutError,urllib.error.URLError):data['endpoints'][name]=0
        return data

    def scale(self, r, replicas, *, local=False, dry_run=False):
        ns='argocd' if local else r['namespace']
        args=['-n',ns,'scale',r['kind']+'/'+r['name'],'--replicas='+str(replicas)]
        if dry_run:args.append('--dry-run=server')
        return self.kube(args,local=local,text=True)

    def snapshot(self, safe_test=False):
        data=self.collect()
        if safe_test:
            for r in data['workloads']:
                self.scale(r,0,dry_run=True)
                self.scale(r,r['replicas'],dry_run=True)
            data['safe_scale_test']={'down':True,'up':True,'workloads':len(data['workloads']),'mutated':False}
        result=self.write(f'plans/{self.plan_id}/kubernetes.json',data)
        return {'phase':'SNAPSHOT','blockers':data['blockers'],'version':result.get('VersionId'),'workloads':len(data['workloads']),'safe_test':safe_test}

    def wait_workload(self,r,replicas,timeout=120,*,local=False):
        deadline=time.time()+timeout
        ns='argocd' if local else r['namespace']
        while True:
            live=self.kube(['-n',ns,'get',r['kind'],r['name'],'-o','json'],local=local)
            if replicas==0:
                selector=','.join(k+'='+v for k,v in live['spec']['selector']['matchLabels'].items())
                pods=self.kube(['-n',ns,'get','pods','-l',selector,'-o','json'],local=local)['items']
                if all(p.get('status',{}).get('phase') in ('Succeeded','Failed') for p in pods):return
            elif live.get('status',{}).get('readyReplicas',0)>=replicas:return
            if time.time()>=deadline:raise ValueError('Workload did not reach requested state: '+ns+'/'+r['name'])
            time.sleep(3)

    def prepare(self, baseline):
        if baseline['blockers']:raise ValueError('Preflight blockers')
        for r in baseline['controllers']:
            self.scale(r,0,local=True)
            self.wait_workload(r,0,local=True)
        # Pause controllers that could restore replicas before changing owned workloads.
        for r in baseline['workloads']:
            if 'autoscaler' in r['name'] or 'operator' in r['name'] or r['name']=='aws-load-balancer-controller' or (r['namespace']=='orders-api'):
                self.scale(r,0)
                if r['name']=='aws-load-balancer-controller':self.wait_workload(r,0)
        return {'phase':'PREPARE','complete':True}

    def quiesce(self, baseline):
        # System bootstrap Deployments retain desired replicas. They restart naturally
        # when workers return, avoiding a dependency on a disabled admission webhook.
        targets=[r for r in baseline['workloads'] if r['namespace']!='kube-system']
        targets.sort(key=lambda r: r['namespace']=='kyverno')
        for r in targets:self.scale(r,0)
        deadline=time.time()+300
        while time.time()<deadline:
            remaining=[]
            for ns in NAMESPACES:
                if ns=='kube-system':continue
                for p in self.kube(['-n',ns,'get','pods','-o','json'])['items']:
                    owners=p['metadata'].get('ownerReferences',[])
                    if any(o['kind']=='DaemonSet' for o in owners):continue
                    if p.get('status',{}).get('phase') not in ('Succeeded','Failed'):remaining.append(ns+'/'+p['metadata']['name'])
            if not remaining:return {'phase':'QUIESCE','complete':True}
            time.sleep(5)
        raise ValueError('Pods ainda ativos após quiescência; não terminar nós')

    def resume(self, baseline):
        deadline=time.time()+240
        while True:
            current=self.collect()
            if sum(n['ready'] for n in current['nodes'])>=len(baseline['nodes']):break
            if time.time()>=deadline:raise ValueError('Ready nodes not restored')
            time.sleep(5)
        by={(r['namespace'],r['kind'],r['name']):r for r in current['workloads']}
        for r in baseline['workloads']:
            found=by.get((r['namespace'],r['kind'],r['name']))
            if not found or found['template_hash']!=r['template_hash']:raise ValueError('Template drift: '+r['namespace']+'/'+r['name'])
        priority=lambda r: 0 if r['namespace']=='kyverno' else 1 if r['name']=='istiod' else 2 if r['kind']=='statefulsets' else 3
        for r in sorted(baseline['workloads'],key=priority):
            if 'autoscaler' in r['name'] or r['name']=='aws-load-balancer-controller':continue
            self.scale(r,r['replicas'])
            if r['namespace']=='kyverno' or r['name']=='istiod':
                self.wait_workload(r,r['replicas'])
        for r in baseline['workloads']:
            if r['name']=='aws-load-balancer-controller':self.scale(r,r['replicas'])
        # Argo CD resumes only with the unchanged Application spec. No Service,
        # Ingress or network sync is issued by the power agent.
        self.check_argo_restore(baseline,current)
        for r in baseline['controllers']:self.scale(r,r['replicas'],local=True)
        for app in baseline['argo']:
            self.kube(['-n','argocd','annotate','application',app['name'],'argocd.argoproj.io/refresh=hard','--overwrite'],local=True,text=True)
        for r in baseline['workloads']:
            if 'autoscaler' in r['name']:self.scale(r,r['replicas'])
        return {'phase':'RESUME','complete':True}

    def verify_once(self, baseline):
        current=self.collect();differences=list(current['blockers'])
        if sum(n['ready'] for n in current['nodes'])>=len(baseline['nodes']):
            differences=[d for d in differences if d!='Nós não estão Ready']
        by={(r['namespace'],r['kind'],r['name']):r for r in current['workloads']}
        for r in baseline['workloads']:
            found=by.get((r['namespace'],r['kind'],r['name']))
            if not found or found['template_hash']!=r['template_hash'] or found['replicas']!=r['replicas'] or found['ready']<r['replicas']:
                differences.append('Workload não recuperado: '+r['namespace']+'/'+r['name'])
        if current['daemonsets']!=baseline['daemonsets']:differences.append('Configuração de DaemonSets divergente')
        for app in baseline['argo']:
            found=next((a for a in current['argo'] if a['name']==app['name']),None)
            if not found or found['spec_hash']!=app['spec_hash'] or found['revision']!=app['revision'] or found['sync']!=app['sync']:differences.append('GitOps pendente ou divergente: '+app['name'])
        old_pv={r['name']:r['spec'].get('csi',{}).get('volumeHandle') for r in baseline['pvs']}
        new_pv={r['name']:r['spec'].get('csi',{}).get('volumeHandle') for r in current['pvs']}
        if old_pv!=new_pv:differences.append('Volumes persistentes divergentes')
        for name,status in baseline['endpoints'].items():
            if status and current['endpoints'].get(name)!=status:differences.append('Endpoint divergente: '+name)
        result={'phase':'VERIFY','complete':not differences,'differences':differences,'observed_at':current['observed_at']}
        return result

    def verify(self,baseline):
        deadline=time.time()+300
        while True:
            result=self.verify_once(baseline)
            if result['complete'] or time.time()>=deadline:return result
            time.sleep(5)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--bucket',required=True);parser.add_argument('--plan-id',required=True);parser.add_argument('--operation-id',required=True)
    parser.add_argument('--version',default='NONE');parser.add_argument('--phase',choices=['SNAPSHOT','SAFE_TEST','PREPARE','QUIESCE','RESUME','VERIFY'],required=True)
    args=parser.parse_args()
    # Documents enforce these patterns too; reject direct or malformed invocation.
    import re
    if not all(re.fullmatch(r'[0-9a-f-]{36}',v) for v in [args.plan_id,args.operation_id]):raise ValueError('Invalid identifiers')
    agent=Agent(args.bucket,args.plan_id,args.operation_id,args.version)
    if args.phase in ('SNAPSHOT','SAFE_TEST'):result=agent.snapshot(safe_test=args.phase=='SAFE_TEST')
    else:
        baseline=agent.load();result=getattr(agent,args.phase.lower())(baseline)
        agent.write(f'operations/{args.operation_id}/{args.phase}.json',result)
    print(json.dumps(result,sort_keys=True))
    if result.get('complete') is False:raise SystemExit(2)

if __name__=='__main__':
    main()
