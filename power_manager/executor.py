"""Checkpointed power execution; callable only by the dedicated state machine.

No network write, data deletion, termination, release or Terraform command exists.
Every target and restore capacity originates in a versioned pre-operation snapshot.
"""
import copy
import json
import os
import time
from functools import lru_cache
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError
from .api import session, table, CONFIG
from .core import CLUSTER, digest

HOST = 'i-0b33fdd51332e04e5'
STANDALONE = {'i-0def4e806c49cf60e','i-0b33fdd51332e04e5','i-02878f35eed1572d0'}
SUSPEND = ['Terminate','Launch','AlarmNotification','ScheduledActions','AZRebalance','ReplaceUnhealthy','InstanceRefresh']


@lru_cache
def client(name):
    return session().client(name, region_name='us-east-1', config=CONFIG)


def items(service, operation, key, **kwargs):
    c=client(service)
    if c.can_paginate(operation):
        return [v for page in c.get_paginator(operation).paginate(**kwargs) for v in page.get(key,[])]
    return getattr(c,operation)(**kwargs).get(key,[])


def canonical_network():
    """Exclude target health/membership, counters and timestamps, not configuration."""
    e=client('ec2');l=client('elbv2')
    data={}
    for op,key,fields in [
        ('describe_addresses','Addresses',['AllocationId','PublicIp','AssociationId','NetworkInterfaceId','PrivateIpAddress']),
        ('describe_nat_gateways','NatGateways',['NatGatewayId','State','VpcId','SubnetId','NatGatewayAddresses']),
        ('describe_vpcs','Vpcs',['VpcId','CidrBlock','IsDefault']),
        ('describe_subnets','Subnets',['SubnetId','VpcId','CidrBlock','AvailabilityZone','MapPublicIpOnLaunch']),
        ('describe_route_tables','RouteTables',['RouteTableId','VpcId','Routes','Associations']),
        ('describe_security_groups','SecurityGroups',['GroupId','VpcId','IpPermissions','IpPermissionsEgress']),
        ('describe_internet_gateways','InternetGateways',['InternetGatewayId','Attachments'])]:
        rows=items('ec2',op,key)
        data[key]=[{f:r.get(f) for f in fields} for r in rows]
    data['LoadBalancers']=[]
    for lb in items('elbv2','describe_load_balancers','LoadBalancers'):
        arn=lb['LoadBalancerArn']
        data['LoadBalancers'].append({k:lb.get(k) for k in ['LoadBalancerArn','DNSName','CanonicalHostedZoneId','VpcId','Type','Scheme','AvailabilityZones','SecurityGroups']})
        data[arn+'/listeners']=items('elbv2','describe_listeners','Listeners',LoadBalancerArn=arn)
        data[arn+'/attributes']=l.describe_load_balancer_attributes(LoadBalancerArn=arn)['Attributes']
        for listener in data[arn+'/listeners']:
            data[listener['ListenerArn']+'/rules']=items('elbv2','describe_rules','Rules',ListenerArn=listener['ListenerArn']) if lb['Type']=='application' else []
    for tg in items('elbv2','describe_target_groups','TargetGroups'):
        data[tg['TargetGroupArn']]={'config':tg,'attributes':l.describe_target_group_attributes(TargetGroupArn=tg['TargetGroupArn'])['Attributes']}
    for zone in items('route53','list_hosted_zones','HostedZones'):
        data[zone['Id']]=items('route53','list_resource_record_sets','ResourceRecordSets',HostedZoneId=zone['Id'])
    # AWS collection ordering is not a configuration change.
    def normalize(value):
        if isinstance(value,dict):return {k:normalize(v) for k,v in value.items()}
        if isinstance(value,list):return sorted([normalize(v) for v in value],key=lambda v:json.dumps(v,sort_keys=True,default=str))
        return value
    return normalize(data)


def put_json(key,value):
    r=client('s3').put_object(Bucket=os.environ['SNAPSHOT_BUCKET'],Key=key,Body=json.dumps(value,default=str,sort_keys=True).encode(),ServerSideEncryption='AES256',IfNoneMatch='*')
    version=r.get('VersionId')
    if not version or version=='null':raise ValueError('Versioned storage required')
    return {'key':key,'version':version}


def get_json(reference):
    with client('s3').get_object(Bucket=os.environ['SNAPSHOT_BUCKET'],Key=reference['key'],VersionId=reference['version'])['Body'] as body:
        return json.load(body)


def save(op):
    previous=int(op.get('revision',0));op['revision']=previous+1;op['updated_at']=int(time.time())
    table().put_item(Item=op,ConditionExpression=Attr('revision').eq(previous))


def build_steps(snapshot,direction,source=None):
    if direction=='DOWN':
        return ['hosts','capture','snapshot','retain','freeze','agent:PREPARE','stop:itsm','queues','agent:QUIESCE','zero','wait_zero','stop:zabbix','stop:control','verify_off']
    return ['start:control','wait:control','host:control','restore_capacity','wait_nodes','start:itsm','wait:itsm','host:itsm','agent:RESUME','start:zabbix','wait:zabbix','host:zabbix','agent:VERIFY','unfreeze','verify_on']


def ec2_id(name):
    return {'control':HOST,'itsm':'i-0def4e806c49cf60e','zabbix':'i-02878f35eed1572d0'}[name]


def snapshot_for(op):
    return get_json(op['snapshot'])


def state_instance(instance):
    return client('ec2').describe_instances(InstanceIds=[instance])['Reservations'][0]['Instances'][0]


def agent_step(op,phase,snapshot):
    name='command_'+str(op['index'])
    if name not in op:
        r=client('ssm').send_command(InstanceIds=[HOST],DocumentName=os.environ['AGENT_DOCUMENT'],
          Parameters={'Phase':[phase],'PlanId':[snapshot['plan_id']],'OperationId':[op['id']],'SnapshotVersion':[snapshot['kubernetes']['version']]},
          TimeoutSeconds=600,Comment='Power Manager '+op['direction']+' '+phase)
        op[name]=r['Command']['CommandId'];save(op)
        return False
    try:
        r=client('ssm').get_command_invocation(CommandId=op[name],InstanceId=HOST)
    except client('ssm').exceptions.InvocationDoesNotExist:
        return False
    if r['Status'] in {'Pending','InProgress','Delayed'}:return False
    if r['Status']!='Success':raise ValueError('Agent '+phase+' failed; original snapshot preserved')
    return True


def execute_step(op,step,snapshot):
    ec2,eks,asg=client('ec2'),client('eks'),client('autoscaling')
    if step=='hosts':
        for iid in sorted(STANDALONE):
            target=next((r for r in op['baseline']['targets'] if r['type']=='EC2' and r['id']==iid),None)
            if not target or target['state']!='running':continue
            command_key='host_capture_'+iid
            if command_key not in op:
                sent=client('ssm').send_command(InstanceIds=[iid],DocumentName=os.environ['HOST_READ_DOCUMENT'],Parameters={'Mode':['SNAPSHOT'],'PlanId':[op['id']],'OperationId':[op['id']]},TimeoutSeconds=600,Comment='Capture application process recovery state')
                op[command_key]=sent['Command']['CommandId'];save(op);return False
            try:r=client('ssm').get_command_invocation(CommandId=op[command_key],InstanceId=iid)
            except client('ssm').exceptions.InvocationDoesNotExist:return False
            if r['Status'] in {'Pending','InProgress','Delayed'}:return False
            if r['Status']!='Success':raise ValueError('Host process snapshot failed; do not power off')
            report=json.loads(r['StandardOutputContent'].strip().splitlines()[-1])
            op['baseline'].setdefault('host_snapshots',{})[iid]=report['reference'];save(op)
        return True
    if step.startswith('host:'):
        iid=ec2_id(step.split(':')[1])
        source=table().get_item(Key={'pk':'OP#'+op['source_operation']},ConsistentRead=True)['Item']
        if iid not in source.get('stop_intents',[]):return True
        ref=snapshot.get('host_snapshots',{}).get(iid)
        if not ref:raise ValueError('Original host process snapshot missing')
        key='host_restore_'+iid
        if key not in op:
            sent=client('ssm').send_command(InstanceIds=[iid],DocumentName=os.environ['HOST_EXECUTE_DOCUMENT'],Parameters={'Mode':['RESTORE'],'PlanId':[snapshot['plan_id']],'OperationId':[op['id']],'SnapshotVersion':[ref['version']]},TimeoutSeconds=600,Comment='Restore only original application processes')
            op[key]=sent['Command']['CommandId'];save(op);return False
        try:r=client('ssm').get_command_invocation(CommandId=op[key],InstanceId=iid)
        except client('ssm').exceptions.InvocationDoesNotExist:return False
        if r['Status'] in {'Pending','InProgress','Delayed'}:return False
        if r['Status']!='Success':raise ValueError('Original host processes not recovered')
        return True
    if step=='capture':
        if 'capture_command' not in op:
            r=client('ssm').send_command(InstanceIds=[HOST],DocumentName=os.environ['READ_DOCUMENT'],Parameters={'Phase':['SNAPSHOT'],'PlanId':[op['id']],'OperationId':[op['id']]},TimeoutSeconds=600,Comment='Capture fresh immutable pre-shutdown state')
            op['capture_command']=r['Command']['CommandId'];save(op);return False
        try:r=client('ssm').get_command_invocation(CommandId=op['capture_command'],InstanceId=HOST)
        except client('ssm').exceptions.InvocationDoesNotExist:return False
        if r['Status'] in {'Pending','InProgress','Delayed'}:return False
        if r['Status']!='Success':raise ValueError('Fresh Kubernetes snapshot failed; no power action performed')
        report=json.loads(r['StandardOutputContent'].strip().splitlines()[-1])
        ref={'key':'plans/'+op['id']+'/kubernetes.json','version':report['version']}
        current=get_json(ref);approved=get_json(op['baseline']['kubernetes'])
        templates=lambda v:sorted([(r['namespace'],r['kind'],r['name'],r['template_hash']) for r in v['workloads']])
        if current['blockers'] or templates(current)!=templates(approved):raise ValueError('Kubernetes state drift since approved plan')
        if current['argo']!=approved['argo']:raise ValueError('GitOps revision drift since approved plan')
        op['baseline'].update(kubernetes=ref,plan_id=op['id'],approved_plan_id=op['plan_id']);save(op);return True
    if step=='snapshot':
        if 'snapshot' in op:return True
        # Fresh AWS snapshot is persisted before any external mutation.
        baseline=op['baseline'];baseline['schema_version']=1;baseline['network']=canonical_network();baseline['network_hash']=digest(baseline['network'])
        baseline['instances']=[state_instance(i) for i in STANDALONE]
        for target in baseline['targets']:
            if target['type']=='EC2':
                live=next(i for i in baseline['instances'] if i['InstanceId']==target['id'])
                if live['State']['Name']!=target['state']:raise ValueError('EC2 state drift since approved plan')
                if ec2.describe_instance_attribute(InstanceId=target['id'],Attribute='disableApiStop').get('DisableApiStop',{}).get('Value'):raise ValueError('Stop protection enabled')
        baseline['volumes']=items('ec2','describe_volumes','Volumes')
        baseline['nodegroups']=[];baseline['asgs']=[];baseline['workers']=[]
        for target in baseline['targets']:
            if target['type']=='MNG':
                ng=eks.describe_nodegroup(clusterName=CLUSTER,nodegroupName=target['id'])['nodegroup']
                if ng['scalingConfig']!=target['capacity']:raise ValueError('Capacity drift since approved plan')
                baseline['nodegroups'].append(ng)
                for group in ng['resources']['autoScalingGroups']:
                    g=asg.describe_auto_scaling_groups(AutoScalingGroupNames=[group['name']])['AutoScalingGroups'][0]
                    if any(p['ProcessName']=='Terminate' for p in g.get('SuspendedProcesses',[])):raise ValueError('ASG Terminate already suspended')
                    baseline['asgs'].append(g)
                    baseline['workers'].extend(state_instance(i['InstanceId']) for i in g['Instances'])
        ref=put_json('operations/'+op['id']+'/aws.json',baseline)
        op['snapshot']=ref;op.pop('baseline',None);save(op)
        table().put_item(Item={'pk':'CONTROL','state':'SHUTTING_DOWN','operation':op['id'],'snapshot':ref,'source_operation':op['id']})
        return True
    if step=='freeze':
        added={}
        for g in snapshot['asgs']:
            original={p['ProcessName'] for p in g.get('SuspendedProcesses',[])}
            added[g['AutoScalingGroupName']]=[p for p in SUSPEND if p not in original]
        op['suspended_by_operation']=added;save(op)
        for group,processes in added.items():
            if processes:asg.suspend_processes(AutoScalingGroupName=group,ScalingProcesses=processes)
        return True
    if step.startswith('agent:'):return agent_step(op,step.split(':')[1],snapshot)
    if step.startswith(('stop:','start:','wait:')):
        action,name=step.split(':');iid=ec2_id(name)
        target=next((r for r in snapshot['targets'] if r['type']=='EC2' and r['id']==iid),None)
        if not target or target['state']!='running':return True
        if iid not in STANDALONE:raise ValueError('EC2 outside allowlist')
        current=state_instance(iid)['State']['Name']
        if action=='stop':
            if current=='stopped':return True
            if current=='stopping':return False
            if current!='running':raise ValueError('Unexpected EC2 state')
            stopped=set(op.get('stop_intents',[]));stopped.add(iid);op['stop_intents']=sorted(stopped);save(op)
            ec2.stop_instances(InstanceIds=[iid]);return False
        source=table().get_item(Key={'pk':'OP#'+op['source_operation']},ConsistentRead=True)['Item']
        if iid not in source.get('stop_intents',[]):return True
        if action=='start':
            if current=='running':return True
            if current in {'pending','stopping'}:return False
            if current!='stopped':raise ValueError('EC2 cannot be restored')
            ec2.start_instances(InstanceIds=[iid]);return False
        if current!='running':return False
        online=items('ssm','describe_instance_information','InstanceInformationList',Filters=[{'Key':'InstanceIds','Values':[iid]}])
        return bool(online and online[0]['PingStatus']=='Online')
    if step=='queues':
        for queue in [r for r in snapshot['resources'] if r['type']=='SQS']:
            attrs=client('sqs').get_queue_attributes(QueueUrl=queue['id'],AttributeNames=['ApproximateNumberOfMessages','ApproximateNumberOfMessagesNotVisible'])['Attributes']
            if int(attrs.get('ApproximateNumberOfMessagesNotVisible',0)):op['empty_queue_samples']=0;save(op);return False
        op['empty_queue_samples']=int(op.get('empty_queue_samples',0))+1;save(op)
        return op['empty_queue_samples']>=2
    if step=='retain':
        for original in snapshot['workers']:
            current=state_instance(original['InstanceId'])
            if current['State']['Name']!='running':raise ValueError('Spot worker changed before retention; do not scale down')
            for mapping in current.get('BlockDeviceMappings',[]):
                if mapping['Ebs'].get('DeleteOnTermination'):
                    ec2.modify_instance_attribute(InstanceId=current['InstanceId'],BlockDeviceMappings=[{'DeviceName':mapping['DeviceName'],'Ebs':{'DeleteOnTermination':False}}])
        return True
    if step in {'zero','restore_capacity'}:
        if step=='restore_capacity':
            source=table().get_item(Key={'pk':'OP#'+op['source_operation']},ConsistentRead=True)['Item']
            for group,processes in source.get('suspended_by_operation',{}).items():
                if 'Launch' in processes:asg.resume_processes(AutoScalingGroupName=group,ScalingProcesses=['Launch'])
        for group in snapshot['nodegroups']:
            desired=copy.deepcopy(group['scalingConfig'])
            if step=='zero':desired.update(minSize=0,desiredSize=0)
            current=eks.describe_nodegroup(clusterName=CLUSTER,nodegroupName=group['nodegroupName'])['nodegroup']
            if current['status']!='ACTIVE':return False
            if step=='zero':
                for g in snapshot['asgs']:asg.resume_processes(AutoScalingGroupName=g['AutoScalingGroupName'],ScalingProcesses=['Terminate'])
            if current['scalingConfig']!=desired:
                eks.update_nodegroup_config(clusterName=CLUSTER,nodegroupName=group['nodegroupName'],scalingConfig=desired,clientRequestToken=op['id']+'-'+step)
                return False
        if step=='restore_capacity':
            source=table().get_item(Key={'pk':'OP#'+op['source_operation']},ConsistentRead=True)['Item']
            for group,processes in source.get('suspended_by_operation',{}).items():
                if 'Launch' in processes:asg.resume_processes(AutoScalingGroupName=group,ScalingProcesses=['Launch'])
        return True
    if step=='wait_zero':
        for group in snapshot['asgs']:
            g=asg.describe_auto_scaling_groups(AutoScalingGroupNames=[group['AutoScalingGroupName']])['AutoScalingGroups'][0]
            if g['Instances']:return False
        return True
    if step=='wait_nodes':
        for group in snapshot['nodegroups']:
            ng=eks.describe_nodegroup(clusterName=CLUSTER,nodegroupName=group['nodegroupName'])['nodegroup']
            if ng['status']!='ACTIVE' or ng['scalingConfig']!=group['scalingConfig']:return False
            for gname in group['resources']['autoScalingGroups']:
                g=asg.describe_auto_scaling_groups(AutoScalingGroupNames=[gname['name']])['AutoScalingGroups'][0]
                if len(g['Instances'])<group['scalingConfig']['desiredSize'] or any(i['HealthStatus']!='Healthy' or i['LifecycleState']!='InService' for i in g['Instances']):return False
        return True
    if step=='unfreeze':
        source=table().get_item(Key={'pk':'OP#'+op['source_operation']},ConsistentRead=True)['Item']
        for group,processes in source.get('suspended_by_operation',{}).items():
            if processes:asg.resume_processes(AutoScalingGroupName=group,ScalingProcesses=processes)
        return True
    if step in {'verify_off','verify_on'}:
        if digest(canonical_network())!=snapshot['network_hash']:raise ValueError('Protected network configuration differs; no automatic network repair permitted')
        existing={v['VolumeId'] for v in items('ec2','describe_volumes','Volumes')}
        if not {v['VolumeId'] for v in snapshot['volumes']}<=existing:raise ValueError('Original EBS volume missing')
        if step=='verify_off':
            if not execute_step(op,'wait_zero',snapshot):return False
            if any(state_instance(i)['State']['Name']!='stopped' for i in op.get('stop_intents',[])):return False
        return True
    raise ValueError('Unknown phase')


def release_lock(op):
    try:table().delete_item(Key={'pk':'LOCK'},ConditionExpression=Attr('operation').eq(op['id']))
    except table().meta.client.exceptions.ConditionalCheckFailedException:pass


def finish(op):
    table().put_item(Item={'pk':'CONTROL','state':op['state'],'operation':op['id'],'snapshot':op['snapshot'],'source_operation':op.get('source_operation',op['id'])})
    release_lock(op)


def fail(op,reason):
    mutated=bool(op.get('snapshot'))
    op['state']='RECOVERY_REQUIRED' if mutated else 'FAILED_NO_MUTATION';op['error']=reason;save(op)
    control={'pk':'CONTROL','state':'RECOVERY_REQUIRED' if mutated else 'ON','operation':op['id']}
    if op.get('snapshot'):control.update(snapshot=op['snapshot'],source_operation=op.get('source_operation',op['id']))
    table().put_item(Item=control)
    if not mutated:release_lock(op)
    return {'state':'failed','operation':op['id'],'error':reason}


def handler(event,context):
    if event.get('mode')=='safe_test':
        if event.get('failure'):return {'state':'failed','error':'Safe test failed'}
        return certify(event)
    op=table().get_item(Key={'pk':'OP#'+event['opid']},ConsistentRead=True)['Item']
    lock=table().get_item(Key={'pk':'LOCK'},ConsistentRead=True).get('Item',{})
    if op['state'] in {'OFF','ON'}:
        if lock.get('operation')==op['id']:finish(op)
        return {'state':'done','operation':op['id']}
    if op['state']=='RECOVERY_REQUIRED':
        if lock.get('operation')==op['id']:fail(op,op.get('error','Recovery required'))
        return {'state':'failed','operation':op['id']}
    if op['state']=='FAILED_NO_MUTATION':return {'state':'failed','operation':op['id']}
    if not op.get('confirmed') or lock.get('operation')!=op['id']:raise PermissionError('Operation is not confirmed or does not own the lock')
    try:
        if event.get('failure'):return fail(op,'State machine task failed; retry recovery with the preserved snapshot')
        if time.time()-int(op['started_at'])>3300:return fail(op,'Operation timeout; preserved snapshot requires recovery')
        index=int(op['index']);snapshot=snapshot_for(op) if op.get('snapshot') else None
        steps=build_steps(snapshot,op['direction']);step=steps[index]
        if op.get('phase')!=step:op['phase_started_at']=int(time.time())
        op['phase']=step;save(op)
        if not execute_step(op,step,snapshot):return {'state':'wait','operation':op['id'],'phase':step}
        op.setdefault('history',[]).append({'phase':step,'completed_at':int(time.time())})
        op['index']=index+1;save(op)
        if op['index']<len(steps):return {'state':'wait','operation':op['id'],'phase':step}
        op['state']='OFF' if op['direction']=='DOWN' else 'ON';save(op)
        finish(op)
        return {'state':'done','operation':op['id']}
    except ClientError as exc:
        return fail(op,'AWS_'+exc.response['Error']['Code'])
    except (ValueError,KeyError,TypeError) as exc:
        return fail(op,str(exc)[:400])


def safe_test():
    """Real IAM permission checks with EC2 DryRun only; never stops or starts."""
    identity=client('sts').get_caller_identity()['Arn']
    if ':assumed-role/cosmotech-power-worker/' not in identity:raise ValueError('Non-root worker role required')
    table().put_item(Item={'pk':'CERTIFICATION','agent_hash':os.environ['AGENT_HASH'],'complete':False,'checked_at':int(time.time())})
    before=canonical_network();states={i:state_instance(i)['State']['Name'] for i in STANDALONE}
    checks=[]
    def dry(method,**kwargs):
        try:getattr(client('ec2'),method)(**kwargs,DryRun=True)
        except ClientError as exc:
            if exc.response['Error']['Code']!='DryRunOperation':raise
        else:raise ValueError('EC2 did not confirm DryRun')
        checks.append(method)
    for iid in STANDALONE:
        client('ec2').describe_instance_attribute(InstanceId=iid,Attribute='disableApiStop')
        dry('stop_instances',InstanceIds=[iid]);dry('start_instances',InstanceIds=[iid])
    for group in items('eks','list_nodegroups','nodegroups',clusterName=CLUSTER):
        ng=client('eks').describe_nodegroup(clusterName=CLUSTER,nodegroupName=group)['nodegroup']
        for g in ng['resources']['autoScalingGroups']:
            for i in client('autoscaling').describe_auto_scaling_groups(AutoScalingGroupNames=[g['name']])['AutoScalingGroups'][0]['Instances']:
                instance=state_instance(i['InstanceId'])
                for mapping in instance.get('BlockDeviceMappings',[]):
                    if mapping['Ebs'].get('DeleteOnTermination'):
                        dry('modify_instance_attribute',InstanceId=instance['InstanceId'],BlockDeviceMappings=[{'DeviceName':mapping['DeviceName'],'Ebs':{'DeleteOnTermination':False}}])
    unchanged=digest(before)==digest(canonical_network()) and states=={i:state_instance(i)['State']['Name'] for i in STANDALONE}
    result={'pk':'CERTIFICATION','agent_hash':os.environ['AGENT_HASH'],'complete':unchanged,'aws_dry_run':True,
            'mutated':False,'checks':checks,'identity':identity,'checked_at':int(time.time())}
    return result


def certify(event):
    """Live read/dry-run certificate. Both execute documents run VERIFY only."""
    import uuid
    jobid=str(uuid.UUID(event['job_id']))
    pk='CERTJOB#'+jobid
    job=table().get_item(Key={'pk':pk},ConsistentRead=True).get('Item')
    if job is None:
        if table().get_item(Key={'pk':'LOCK'},ConsistentRead=True).get('Item'):raise ValueError('Power operation active; do not run certification')
        report=safe_test()
        commands={}
        command=client('ssm').send_command(InstanceIds=[HOST],DocumentName=os.environ['READ_DOCUMENT'],Parameters={'Phase':['SAFE_TEST'],'PlanId':[jobid],'OperationId':[jobid]},TimeoutSeconds=600,Comment='Safe DOWN and UP Kubernetes dry-run')
        commands['kube']=command['Command']['CommandId']
        for iid in sorted(STANDALONE):
            sent=client('ssm').send_command(InstanceIds=[iid],DocumentName=os.environ['HOST_READ_DOCUMENT'],Parameters={'Mode':['SAFE_TEST'],'PlanId':[jobid],'OperationId':[jobid]},TimeoutSeconds=600,Comment='Read-only process recovery validation')
            commands[iid]=sent['Command']['CommandId']
        job={'pk':pk,'id':jobid,'phase':'READ','report':report,'commands':commands,'expires':int(time.time())+86400}
        table().put_item(Item=job)
        return {'state':'wait','phase':'safe_read'}
    outputs={}
    for name,identifier in job['commands'].items():
        iid=HOST if name=='kube' else name
        try:r=client('ssm').get_command_invocation(CommandId=identifier,InstanceId=iid)
        except client('ssm').exceptions.InvocationDoesNotExist:return {'state':'wait','phase':'safe_'+job['phase']}
        if r['Status'] in ('Pending','InProgress','Delayed'):return {'state':'wait','phase':'safe_'+job['phase']}
        if r['Status']!='Success':raise ValueError('Safe validation failed: '+name+' '+r['Status'])
        outputs[name]=json.loads(r['StandardOutputContent'].strip().splitlines()[-1])
    if job['phase']=='READ':
        if outputs['kube'].get('blockers'):raise ValueError('Safe Kubernetes preflight: '+ '; '.join(outputs['kube']['blockers'])[:600])
        commands={}
        command=client('ssm').send_command(InstanceIds=[HOST],DocumentName=os.environ['AGENT_DOCUMENT'],Parameters={'Phase':['VERIFY'],'PlanId':[jobid],'OperationId':[jobid],'SnapshotVersion':[outputs['kube']['version']]},TimeoutSeconds=600,Comment='Verify current state against recovery snapshot; no power changes')
        commands['kube']=command['Command']['CommandId']
        for iid in sorted(STANDALONE):
            sent=client('ssm').send_command(InstanceIds=[iid],DocumentName=os.environ['HOST_EXECUTE_DOCUMENT'],Parameters={'Mode':['VERIFY'],'PlanId':[jobid],'OperationId':[jobid],'SnapshotVersion':[outputs[iid]['reference']['version']]},TimeoutSeconds=600,Comment='Verify original processes; do not start or stop')
            commands[iid]=sent['Command']['CommandId']
        job.update(phase='VERIFY',commands=commands);table().put_item(Item=job)
        return {'state':'wait','phase':'safe_verify'}
    if any(not value.get('complete') for value in outputs.values()):raise ValueError('Snapshot/health comparison did not pass')
    report=job['report'];report.update(complete=True,kubernetes_dry_run=True,host_recovery_validation=True,checked_at=int(time.time()),job_id=jobid)
    table().put_item(Item=report)
    job['phase']='COMPLETE';table().put_item(Item=job)
    return {'state':'done','certificate':report}
