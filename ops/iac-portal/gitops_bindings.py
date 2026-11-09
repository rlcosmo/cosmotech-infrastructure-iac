"""Read-only PV/PVC binding to plain manifests in the canonical GitOps checkout."""
import re
from pathlib import Path

REPOSITORY='https://github.com/rlcosmo/cosmotech-gitops'


def declared_claims(checkout):
    import json
    checkout=Path(checkout).resolve();claims=set();total=0
    def documents(path):
        nonlocal total
        if path.is_symlink() or not path.is_file() or path.suffix not in {'.yaml','.yml','.json'}:return []
        if not path.resolve().is_relative_to(checkout):raise ValueError('Manifest outside checkout')
        size=path.stat().st_size;total+=size
        if size>262144 or total>8388608:raise ValueError('GitOps manifest size budget exceeded')
        if path.suffix=='.json':return [json.loads(path.read_text())]
        import yaml
        return list(yaml.safe_load_all(path.read_text()))
    # A PVC name alone cannot establish the destination cluster. Resolve plain
    # manifests through canonical Argo Applications, not every file in a monorepo.
    for app_path in sorted((checkout/'gitops/bootstrap').glob('*')):
        for app in documents(app_path):
            if not isinstance(app,dict) or app.get('kind')!='Application':continue
            spec=app.get('spec') or {};source=spec.get('source') or {};dest=spec.get('destination') or {}
            if source.get('repoURL') not in {REPOSITORY,REPOSITORY+'.git','ssh://git@ssh.github.com:443/rlcosmo/cosmotech-gitops.git'}:continue
            if source.get('targetRevision')!='main' or source.get('helm') or source.get('plugin'):continue
            endpoint=dest.get('server','')
            if not endpoint.startswith('https://') or endpoint=='https://kubernetes.default.svc':continue
            relative=source.get('path','');directory=checkout/relative
            if not relative.startswith('gitops/apps/') or '..' in Path(relative).parts or not directory.resolve().is_relative_to(checkout):continue
            # Helm/Kustomize renderings need an independent evaluated manifest;
            # raw YAML underneath an overlay must not be treated as deployed.
            if any((directory/name).exists() for name in ['Chart.yaml','kustomization.yaml','kustomization.yml','Kustomization']):continue
            for path in sorted(directory.glob('*')):
                for doc in documents(path):
                    if not isinstance(doc,dict) or doc.get('kind')!='PersistentVolumeClaim':continue
                    meta=doc.get('metadata') or {};namespace=meta.get('namespace') or dest.get('namespace')
                    if namespace and meta.get('name'):claims.add((endpoint,namespace,meta['name']))
    return claims


def bindings(snapshot,claims,*,region,commit,cluster):
    if not re.fullmatch('[a-f0-9]{40}',commit):raise ValueError('Pinned GitOps revision required')
    pvcs={(c['metadata']['namespace'],c['metadata']['name']):c for c in snapshot['pvcs']}
    found=[]
    for pv in snapshot['pvs']:
        spec=pv.get('spec') or {};ref=spec.get('claimRef') or {};csi=spec.get('csi') or {}
        key=(ref.get('namespace'),ref.get('name'));claim=pvcs.get(key)
        if (snapshot.get('endpoint'),*key) not in claims or not claim or csi.get('driver')!='ebs.csi.aws.com':continue
        if pv.get('status',{}).get('phase')!='Bound' or claim.get('status',{}).get('phase')!='Bound':continue
        if not ref.get('uid') or ref['uid']!=claim['metadata'].get('uid'):continue
        if claim.get('spec',{}).get('volumeName')!=pv['metadata']['name']:continue
        handle=csi.get('volumeHandle','')
        if not re.fullmatch('vol-[0-9a-f]+',handle):continue
        found.append(dict(region=region,volume_id=handle,pv=pv['metadata']['name'],pvc=key[1],
                          namespace=key[0],repository=REPOSITORY,commit=commit,cluster=cluster,verified=True))
    return found
