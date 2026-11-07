"""Metadata only. No secret values, SQS bodies or persistent configurations."""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from .core import REGION, CLUSTER
from .costs import estimate


def discover(session):
    from botocore.config import Config
    config = Config(connect_timeout=3, read_timeout=8, retries={'mode': 'standard', 'total_max_attempts': 2})
    names = ['ec2', 'eks', 'autoscaling', 'elbv2', 'efs', 'rds', 'sqs', 's3', 'dynamodb', 'lambda',
             'apigatewayv2', 'apigateway', 'ecr', 'cloudwatch', 'logs', 'secretsmanager', 'acm', 'route53', 'iam', 'events', 'ssm', 'stepfunctions', 'scheduler', 'backup', 'resourcegroupstaggingapi']
    clients = {n: session.client(n, region_name=REGION, config=config) for n in names}
    resources, errors, raw = [], [], {}

    def read(service, operation, key, **kwargs):
        client = clients[service]
        if client.can_paginate(operation):
            return [x for page in client.get_paginator(operation).paginate(**kwargs) for x in page.get(key, [])]
        return getattr(client, operation)(**kwargs).get(key, [])

    def add(typ, rid, name=None, state='preserved', classification='PERSISTENTES', action='none', **extra):
        resources.append({'id': rid, 'name': name or rid, 'type': typ, 'region': REGION,
                          'state': state, 'classification': classification, 'action': action,
                          'dependencies': [], 'cost_on_usd_hour': None, 'cost_off_usd_hour': None,
                          'reason': 'Preservado; sem escrita pelo Power Manager' if action == 'none' else 'Capacidade recuperável pelo snapshot', **extra})

    def compute():
        ngs = read('eks', 'list_nodegroups', 'nodegroups', clusterName=CLUSTER)
        managed = set()
        for ng in ngs:
            v = clients['eks'].describe_nodegroup(clusterName=CLUSTER, nodegroupName=ng)['nodegroup']
            approved=ng=='aws-eks-istio-lab-dev-spot-named'
            if approved:managed.update(g['name'] for g in v['resources']['autoScalingGroups'])
            add('MNG', ng, state=v['status'], classification='LAB' if approved else 'PROTECTED', action='scale' if approved else 'none', capacity=v['scalingConfig'], spot=v['capacityType'] == 'SPOT', dependencies=[CLUSTER,*[g['name'] for g in v['resources']['autoScalingGroups']],*v['subnets']])
        add('EKS', CLUSTER, classification='ALWAYS-ON', state=clients['eks'].describe_cluster(name=CLUSTER)['cluster']['status'])
        for g in read('autoscaling', 'describe_auto_scaling_groups', 'AutoScalingGroups'):
            tags = {t['Key']: t['Value'] for t in g.get('Tags', [])}
            in_lab = g['AutoScalingGroupName'] in managed
            add('ASG', g['AutoScalingGroupName'], state=str(g['DesiredCapacity']), classification='LAB' if in_lab else 'PROTECTED',
                action='none' if in_lab else 'none', managed_by_eks=in_lab,
                capacity={k: g[k] for k in ('MinSize', 'MaxSize', 'DesiredCapacity')}, suspended=g.get('SuspendedProcesses', []))
        addresses = read('ec2', 'describe_addresses', 'Addresses')
        for page in read('ec2', 'describe_instances', 'Reservations'):
            for i in page['Instances']:
                if i['State']['Name'] == 'terminated':
                    continue
                tags = {t['Key']: t['Value'] for t in i.get('Tags', [])}
                managed_node = 'aws:autoscaling:groupName' in tags
                lab = i['InstanceId'] in {'i-0def4e806c49cf60e','i-0b33fdd51332e04e5','i-02878f35eed1572d0'} and tags.get('Name') in {'cosmotech-itsm-dev', 'cosmotech-argocd-dev', 'cosmotech-zabbix-dev'}
                elastic = any(a.get('InstanceId') == i['InstanceId'] for a in addresses)
                stop_protected = clients['ec2'].describe_instance_attribute(InstanceId=i['InstanceId'],Attribute='disableApiStop').get('DisableApiStop',{}).get('Value',False) if lab else False
                safe = not stop_protected and lab and not managed_node and i['RootDeviceType'] == 'ebs' and (not i.get('PublicIpAddress') or elastic)
                add('EC2', i['InstanceId'], tags.get('Name'), i['State']['Name'], 'LAB' if safe or (managed_node and tags.get('aws:autoscaling:groupName') in managed) else 'PROTECTED',
                    'stop' if safe else 'managed' if managed_node and tags.get('aws:autoscaling:groupName') in managed else 'none', public_ip=bool(i.get('PublicIpAddress')), elastic_ip=elastic,
                    capacity_type=i.get('InstanceLifecycle', 'on-demand'), instance_type=i['InstanceType'], availability_zone=i['Placement']['AvailabilityZone'],stop_protected=stop_protected,
                    dependencies=[i['VpcId'],i['SubnetId'],*[g['GroupId'] for g in i.get('SecurityGroups',[])],*[v['Ebs']['VolumeId'] for v in i.get('BlockDeviceMappings',[])]],
                    control_host=tags.get('Name') == 'cosmotech-argocd-dev', managed_node=managed_node,
                    volumes=[{'id': v['Ebs']['VolumeId'], 'delete_on_termination': v['Ebs'].get('DeleteOnTermination', False)} for v in i.get('BlockDeviceMappings', [])])
        for a in addresses:
            add('EIP', a['AllocationId'], state='allocated', classification='PROTECTED', public_address=a.get('PublicIp'))

    jobs = [('compute', compute)]
    generic = [
        ('ec2', 'describe_volumes', 'Volumes', 'EBS', 'VolumeId', 'State', 'PERSISTENTES'),
        ('ec2', 'describe_snapshots', 'Snapshots', 'SNAPSHOT', 'SnapshotId', 'State', 'PERSISTENTES'),
        ('ec2', 'describe_nat_gateways', 'NatGateways', 'NAT', 'NatGatewayId', 'State', 'PROTECTED'),
        ('ec2', 'describe_vpcs', 'Vpcs', 'VPC', 'VpcId', 'State', 'PROTECTED'),
        ('ec2', 'describe_subnets', 'Subnets', 'SUBNET', 'SubnetId', 'State', 'PROTECTED'),
        ('ec2', 'describe_route_tables', 'RouteTables', 'ROUTE', 'RouteTableId', None, 'PROTECTED'),
        ('ec2', 'describe_security_groups', 'SecurityGroups', 'SG', 'GroupId', None, 'PROTECTED'),
        ('ec2', 'describe_internet_gateways', 'InternetGateways', 'IGW', 'InternetGatewayId', None, 'PROTECTED'),
        ('elbv2', 'describe_load_balancers', 'LoadBalancers', 'LB', 'LoadBalancerArn', None, 'PROTECTED'),
        ('efs', 'describe_file_systems', 'FileSystems', 'EFS', 'FileSystemId', 'LifeCycleState', 'PERSISTENTES'),
        ('rds', 'describe_db_instances', 'DBInstances', 'RDS', 'DBInstanceIdentifier', 'DBInstanceStatus', 'NÃO DESLIGÁVEIS'),
        ('lambda', 'list_functions', 'Functions', 'LAMBDA', 'FunctionName', 'State', 'ALWAYS-ON'),
        ('apigatewayv2', 'get_apis', 'Items', 'API', 'ApiId', None, 'ALWAYS-ON'),
        ('apigateway', 'get_rest_apis', 'items', 'REST_API', 'id', None, 'ALWAYS-ON'),
        ('ecr', 'describe_repositories', 'repositories', 'ECR', 'repositoryName', None, 'PERSISTENTES'),
        ('logs', 'describe_log_groups', 'logGroups', 'LOGS', 'logGroupName', None, 'PERSISTENTES'),
        ('cloudwatch', 'describe_alarms', 'MetricAlarms', 'ALARM', 'AlarmName', 'StateValue', 'ALWAYS-ON'),
        ('secretsmanager', 'list_secrets', 'SecretList', 'SECRET', 'Name', None, 'PERSISTENTES'),
        ('acm', 'list_certificates', 'CertificateSummaryList', 'CERTIFICATE', 'CertificateArn', 'Status', 'PROTECTED'),
        ('events', 'list_rules', 'Rules', 'AUTOMATION', 'Name', 'State', 'PROTECTED'),
        ('stepfunctions','list_state_machines','stateMachines','STEP_FUNCTIONS','stateMachineArn',None,'ALWAYS-ON'),
        ('scheduler','list_schedules','Schedules','SCHEDULE','Name','State','PROTECTED'),
        ('ssm','describe_parameters','Parameters','SSM','Name',None,'PERSISTENTES'),
        ('ssm','list_documents','DocumentIdentifiers','SSM_DOCUMENT','Name',None,'PROTECTED'),
        ('backup','list_backup_vaults','BackupVaultList','BACKUP_VAULT','BackupVaultName',None,'PERSISTENTES'),
        ('backup','list_backup_plans','BackupPlansList','BACKUP_PLAN','BackupPlanId',None,'PERSISTENTES'),
        ('cloudwatch','list_dashboards','DashboardEntries','DASHBOARD','DashboardName',None,'ALWAYS-ON'),
    ]
    def generic_job(spec):
        service, op, key, typ, idkey, statekey, classification = spec
        kwargs = {'OwnerIds': ['self']} if typ == 'SNAPSHOT' else {'Filters':[{'Key':'Owner','Values':['Self']}]} if typ=='SSM_DOCUMENT' else {}
        values = read(service, op, key, **kwargs)
        raw[typ] = values
        for v in values:
            add(({'application': 'ALB', 'network': 'NLB'}.get(v.get('Type'), 'LB')) if typ == 'LB' else typ, v[idkey], name=v.get('Name') or v.get('LoadBalancerName') or v.get('FunctionName') or v[idkey], state=v.get(statekey, 'preserved'), classification='ALWAYS-ON' if typ in {'SSM','SSM_DOCUMENT'} and ('/power-manager/' in v[idkey] or v[idkey].startswith('CosmoTech-Power-')) else classification,
                size_gib=v.get('Size'), volume_type=v.get('VolumeType'), endpoint=v.get('DNSName'),
                dependencies=[v[k] for k in ['VpcId','SubnetId'] if v.get(k)]+[a['SubnetId'] for a in v.get('AvailabilityZones',[]) if a.get('SubnetId')])
    jobs.extend((s[3], lambda spec=s: generic_job(spec)) for s in generic)
    def queues():
        for url in read('sqs', 'list_queues', 'QueueUrls'):
            attrs = clients['sqs'].get_queue_attributes(QueueUrl=url, AttributeNames=['ApproximateNumberOfMessages', 'ApproximateNumberOfMessagesNotVisible', 'RedrivePolicy','MessageRetentionPeriod'])['Attributes']
            add('SQS', url, in_flight=int(attrs.get('ApproximateNumberOfMessagesNotVisible', 0)), messages=int(attrs.get('ApproximateNumberOfMessages', 0)), has_dlq='RedrivePolicy' in attrs,retention_seconds=int(attrs.get('MessageRetentionPeriod',0)))
    def tables():
        for name in read('dynamodb', 'list_tables', 'TableNames'):
            add('DYNAMODB', name, classification='ALWAYS-ON' if name.startswith('cosmotech-power-') else 'PERSISTENTES')
    def buckets():
        for b in read('s3', 'list_buckets', 'Buckets'):
            location = clients['s3'].get_bucket_location(Bucket=b['Name']).get('LocationConstraint') or 'us-east-1'
            if location == REGION:
                add('S3', b['Name'],classification='ALWAYS-ON' if b['Name'].startswith('cosmotech-power-snapshots-') else 'PERSISTENTES')
    # IAM/DNS are global metadata and never mutated by this regional controller.
    def globals_read():
        for r in read('iam', 'list_roles', 'Roles'):
            add('IAM', r['RoleName'], classification='PROTECTED', region='global')
        for z in read('route53', 'list_hosted_zones', 'HostedZones'):
            add('DNS', z['Id'], z['Name'], classification='PROTECTED', region='global')
    jobs.extend([('SQS', queues), ('DYNAMODB', tables), ('S3', buckets), ('global metadata', globals_read)])
    def run(job):
        name, fn = job
        try:
            fn()
        except Exception as exc:
            # Explicit coverage gap, never an empty-success inventory.
            code=getattr(exc,'response',{}).get('Error',{}).get('Code',type(exc).__name__)
            errors.append(f'{name}: {code}')
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(run, jobs))
    def tagged():
        for r in read('resourcegroupstaggingapi','get_resources','ResourceTagMappingList'):
            arn=r['ResourceARN']
            if any(arn==v['id'] or arn.endswith('/'+v['id']) or arn.endswith(':'+v['id']) for v in resources):continue
            tags={t['Key']:t['Value'] for t in r.get('Tags',[])}
            add('OTHER:'+arn.split(':')[2],arn,tags.get('Name'),state='identificado por tags; estado não consultado',classification='PROTECTED',reason='Tipo fora dos mecanismos aprovados; preservado até análise de dependências')
    run(('tagged resource coverage',tagged))
    resources.sort(key=lambda r: (r['type'], r['id']))
    return {'observed_at': datetime.now(timezone.utc).isoformat(), 'region': REGION, 'cluster': CLUSTER,
            'resources': resources, 'errors': errors, 'complete': not errors, 'coverage':{'regional_services':names,'untagged_unknown_services':'Recursos sem tags de serviços não cobertos podem não aparecer; recursos desconhecidos nunca são desligados'},
            'preflight': {'recovery_verified': None, 'mode': 'per-operation', 'message': 'Inventário isolado não libera execução; use o plano autenticado com os testes seguros.'},
            'costs': estimate(resources)}
