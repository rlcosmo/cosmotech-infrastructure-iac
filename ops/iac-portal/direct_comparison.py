"""Compare evaluated Git configuration to refreshed Terraform and discovered AWS assets."""
from collections import Counter
from datetime import datetime, timezone

KINDS = {'aws_instance':'AWS::EC2::Instance','aws_ebs_volume':'AWS::EC2::Volume',
 'aws_ebs_snapshot':'AWS::EC2::Snapshot','aws_vpc':'AWS::EC2::VPC','aws_subnet':'AWS::EC2::Subnet',
 'aws_security_group':'AWS::EC2::SecurityGroup','aws_nat_gateway':'AWS::EC2::NatGateway',
 'aws_eks_cluster':'AWS::EKS::Cluster','aws_eks_node_group':'AWS::EKS::Nodegroup',
 'aws_autoscaling_group':'AWS::AutoScaling::AutoScalingGroup','aws_s3_bucket':'AWS::S3::Bucket',
 'aws_efs_file_system':'AWS::EFS::FileSystem','aws_fsx_lustre_file_system':'AWS::FSx::FileSystem',
 'aws_fsx_windows_file_system':'AWS::FSx::FileSystem','aws_db_instance':'AWS::RDS::DBInstance',
 'aws_lb':'AWS::ElasticLoadBalancingV2::LoadBalancer','aws_ecr_repository':'AWS::ECR::Repository',
 'aws_lambda_function':'AWS::Lambda::Function','aws_dynamodb_table':'AWS::DynamoDB::Table','aws_sqs_queue':'AWS::SQS::Queue'}


def flatten(module):
    return list(module.get('resources',[])) + [r for child in module.get('child_modules',[]) for r in flatten(child)]


def ids(values):
    return {str(values[k]) for k in ['id','arn']
            if isinstance(values.get(k),(str,int)) and str(values[k])}


def changed_paths(before, after, sensitive_before=None, sensitive_after=None, prefix=''):
    # Report field names only; never before/after values, credentials or user_data.
    if sensitive_before is True or sensitive_after is True:
        return [prefix or 'sensitive_attribute'] if before != after else []
    if isinstance(before,dict) and isinstance(after,dict):
        result=[]
        for key in sorted(set(before)|set(after)):
            if key in ['user_data','user_data_base64','content','policy','assume_role_policy','password','secret','token']:
                if before.get(key)!=after.get(key): result.append((prefix+'.' if prefix else '')+key)
                continue
            result += changed_paths(before.get(key),after.get(key),
                sensitive_before.get(key) if isinstance(sensitive_before,dict) else None,
                sensitive_after.get(key) if isinstance(sensitive_after,dict) else None,
                (prefix+'.' if prefix else '')+key)
        return result[:30]
    return [prefix] if before != after else []


def compare(plan, discovered, source, region='us-east-1'):
    if not isinstance(plan.get('planned_values'),dict):
        raise ValueError('Git configuration was not evaluated')
    if plan.get('errored') or plan.get('deferred_changes') or plan.get('complete') is False:
        raise ValueError('Incomplete Git evaluation cannot establish comparison')
    rows=[]; owned=set();typed=set(); inherited_ids=set(); seen=set(); declared_untracked=[]
    changes=[r for r in plan.get('resource_changes',[]) if r.get('mode','managed')=='managed']
    desired={r['address']:r for r in flatten(plan.get('planned_values',{}).get('root_module',{})) if r.get('mode')=='managed'}
    current={r['address']:r for r in flatten(plan.get('prior_state',{}).get('values',{}).get('root_module',{})) if r.get('mode')=='managed'}
    by_address={c['address']:c for c in changes}
    for address in sorted(set(desired)|set(current)|set(by_address)):
        change=by_address.get(address,{})
        detail=change.get('change',{})
        resource=desired.get(address) or current.get(address) or change
        before=detail.get('before') or current.get(address,{}).get('values',{}) or {}
        after=detail.get('after') or desired.get(address,{}).get('values',{}) or {}
        actions=detail.get('actions',['no-op'])
        kind=KINDS.get(resource['type'],resource['type'])
        status='in_sync'
        if actions==['delete'] or address not in desired and address in current and not after:
            status='extra'
        elif actions==['create']:
            status='missing'
        elif actions!=['no-op']:
            status='different'
        values=before or after
        arn=values.get('arn')
        resource_region=arn.split(':')[3] or 'global' if isinstance(arn,str) and arn.startswith('arn:') else region
        identity=values.get('id') or arn or address
        # Names do not establish ownership: use provider ID/ARN, not guessed tag matches.
        identifiers=ids(before)|ids(after)
        for identifier in identifiers:
            if identifier.startswith('arn:'):owned.add(identifier)
            typed.add((kind,resource_region,identifier))
        if status!='extra':
            inherited_ids |= {(resource_region,i) for i in identifiers}
            if before and resource['type']=='aws_autoscaling_group':
                inherited_ids.add((resource_region,before.get('name','')))
            if resource['type']=='aws_eks_node_group':
                inherited_ids |= {(resource_region,i) for i in {after.get('node_group_name'),before.get('node_group_name')}-{None}}
        for block in ('root_block_device','ebs_block_device'):
            for entry in before.get(block,[]) or []:
                if isinstance(entry,dict) and entry.get('volume_id'):
                    typed.add(('AWS::EC2::Volume',resource_region,entry['volume_id']))
        row={'address':address,'kind':kind,'region':resource_region,'id':str(identity),
             'name':str(values.get('name') or values.get('bucket') or values.get('node_group_name') or
                        (values.get('tags') or {}).get('Name') or identity),
             'status':status,'origin':'git_terraform','management':'terraform','reason':'Evaluated canonical Terraform configuration and refreshed state','changed_fields':changed_paths(before,after,detail.get('before_sensitive'),detail.get('after_sensitive')) if status=='different' else []}
        if status=='missing' and not before:
            natural=next((after[k] for k in ['bucket','name','function_name','node_group_name'] if isinstance(after.get(k),str)),None)
            if natural:
                declared_untracked.append((row,natural,kind,resource_region))
        rows.append(row)
        seen.add((row['kind'],row['region'],row['id']))
    # A root without an accessible state can still declare a known natural ID.
    # Presence is confirmed by inventory; configuration is explicitly unverified, never falsely missing.
    for row,natural,kind,rregion in declared_untracked:
        matches=[a for a in discovered.get('resources',[]) if a['kind']==kind and (a['region']==rregion or kind=='AWS::S3::Bucket') and (a['id']==natural or a.get('name')==natural)]
        if len(matches)==1:
            row['status']='declared_untracked'
            row['id']=matches[0]['id']
            row['region']=matches[0]['region']
            if matches[0].get('arn'):owned.add(matches[0]['arn'])
            typed.add((kind,matches[0]['region'],matches[0]['id']))
    roots_with_presence={r['address'].split('::')[0] for r in rows if r['status']=='declared_untracked' and '::' in r['address']}
    known_natural_addresses={r['address'] for r,_,_,_ in declared_untracked}
    for row in rows:
        if row['status']=='missing' and '::' in row['address'] and row['address'].split('::')[0] in roots_with_presence and row['address'] not in known_natural_addresses:
            row['status']='declared_untracked'
    pending=[]
    for asset in discovered.get('resources',[]):
        key=(asset['kind'],asset['region'],asset['id'])
        if asset.get('arn') in owned or key in typed or key in seen:
            continue
        pending.append(asset)
    # EKS-managed ASG -> EC2 instances are derived resources only when the actual parent is in Git.
    while True:
        derived=[a for a in pending if a.get('platform') and inherited_ids.intersection((a['region'],p) for p in a.get('parents',[]))]
        if not derived:break
        for asset in derived:
            rows.append({**{k:asset.get(k) for k in ['kind','id','name','region']},'address':None,
                         'status':'service_managed','origin':'aws_inventory','management':'aws_managed',
                         'reason':'Derived from a Terraform-owned parent in the same region','parents':asset.get('parents',[]),'changed_fields':[]})
            inherited_ids |= {(asset['region'],i) for i in {asset['id'],asset.get('arn'),asset.get('name')}-{None}}
            seen.add((asset['kind'],asset['region'],asset['id']))
            pending.remove(asset)
    for asset in pending:
        key=(asset['kind'],asset['region'],asset['id'])
        if key in seen:continue
        seen.add(key)
        rows.append({**{k:asset.get(k) for k in ['kind','id','name','region']},'address':None,
                     'origin':'aws_inventory','changed_fields':[],**govern(asset,discovered)})
    counts=Counter(row['status'] for row in rows)
    regional={r:dict(Counter(row['status'] for row in rows if row['region']==r)) for r in sorted({row['region'] for row in rows})}
    return {'schema_version':2,'source':source,'observed_at':datetime.now(timezone.utc).isoformat(),
            'state':'ready','resources':rows,'summary':dict(counts),'regional_summary':regional,
            'management_summary':dict(Counter(row['management'] for row in rows)),
            'orphan_candidate_count':sum(row.get('orphan_candidate',False) for row in rows),
            'drift_count':sum(counts[k] for k in ['extra','missing','different']),
            'coverage':discovered.get('coverage',[]),'regions':discovered.get('regions',[]),
            'inventory_complete':False,'coverage_note':'Recursos gerenciados: avaliação Terraform. Descoberta AWS: tipos cobertos pelas APIs, Config e Resource Explorer; cobertura global não presumida.'}


def govern(asset, discovered):
    """Explain ownership, without equating a tag or missing parent with deletion safety."""
    kind=asset['kind'];region=asset['region'];identifier=asset['id']
    meta=asset.get('metadata') or {};tags=meta.get('tags') or {}
    base={'status':'extra','management':'unmanaged','orphan_candidate':False,
          'reason':'No matching evaluated Terraform owner; external ownership remains unverified'}
    if kind=='AWS::EC2::Snapshot':
        if tags.get('aws:dlm:lifecycle-policy-id') or tags.get('aws:backup:source-resource'):
            return {**base,'status':'service_managed','management':'aws_managed',
                    'reason':'AWS system tag identifies an automated recovery artifact; retained in inventory, not Terraform drift'}
        # Manual snapshots are not orphaned just because their source volume no longer exists.
        return {**base,'reason':'Snapshot has no verified automation owner; source volume absence does not prove an orphan'}
    if kind=='AWS::EC2::Volume':
        csi=tags.get('ebs.csi.aws.com/cluster')=='true' or tags.get('CSIVolumeName') or tags.get('kubernetes.io/created-for/pvc/name')
        # A binding is supplied only by a verified read-only Kubernetes/GitOps collector.
        # Match physical volume ID + region, not Name/PVC-name alone.
        bindings=[b for b in discovered.get('gitops_bindings',[]) if b.get('volume_id')==identifier and b.get('region')==region
                  and b.get('verified') is True and b.get('repository')=='https://github.com/rlcosmo/cosmotech-gitops'
                  and isinstance(b.get('commit'),str) and len(b['commit'])==40 and all(c in '0123456789abcdef' for c in b['commit'])
                  and b.get('pv') and b.get('pvc') and b.get('namespace')]
        if len(bindings)==1:
            return {**base,'status':'service_managed','management':'gitops',
                    'reason':'Physical volume matched to verified PV/PVC and canonical GitOps revision'}
        if csi:
            return {**base,'status':'needs_review','management':'unknown',
                    'reason':'CSI/PVC ownership marker found; Kubernetes and GitOps binding not verified. Retained/detached PVC is not an orphan'}
        complete=any(c.get('region')==region and c.get('kind')==kind and c.get('status')=='observed' for c in discovered.get('coverage',[]))
        if meta.get('state')=='available' and meta.get('attachments')==[] and complete:
            return {**base,'orphan_candidate':True,
                    'reason':'Unattached unmanaged volume candidate; retention, backups and Kubernetes must be reviewed before any action'}
    return base
