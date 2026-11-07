"""AWS metadata only. Explicit coverage; inventory failure never means zero resources."""
from concurrent.futures import ThreadPoolExecutor
import json


def inventory(call, account, partition='aws'):
    assets, coverage = [], []
    regions = call('us-east-1', 'ec2', 'describe-regions')['Regions']
    regions = sorted(r['RegionName'] for r in regions if r.get('OptInStatus') != 'not-opted-in')

    def regional(region):
        found, observed = [], []
        def add(kind, identifier, name=None, arn=None, parents=(), platform=False):
            if not identifier:
                return
            found.append({'kind':kind,'id':str(identifier),'name':str(name or identifier),
                          'arn':arn,'region':region,'parents':list(parents),'platform':platform})
        specs = [
            ('AWS::EC2::Instance','ec2','describe-instances','Reservations','InstanceId'),
            ('AWS::EC2::Volume','ec2','describe-volumes','Volumes','VolumeId'),
            ('AWS::EC2::Snapshot','ec2','describe-snapshots','Snapshots','SnapshotId'),
            ('AWS::EC2::VPC','ec2','describe-vpcs','Vpcs','VpcId'),
            ('AWS::EC2::Subnet','ec2','describe-subnets','Subnets','SubnetId'),
            ('AWS::EC2::SecurityGroup','ec2','describe-security-groups','SecurityGroups','GroupId'),
            ('AWS::EC2::NatGateway','ec2','describe-nat-gateways','NatGateways','NatGatewayId'),
            ('AWS::EFS::FileSystem','efs','describe-file-systems','FileSystems','FileSystemId'),
            ('AWS::AutoScaling::AutoScalingGroup','autoscaling','describe-auto-scaling-groups','AutoScalingGroups','AutoScalingGroupName'),
            ('AWS::FSx::FileSystem','fsx','describe-file-systems','FileSystems','FileSystemId'),
            ('AWS::RDS::DBInstance','rds','describe-db-instances','DBInstances','DBInstanceIdentifier'),
            ('AWS::ElasticLoadBalancingV2::LoadBalancer','elbv2','describe-load-balancers','LoadBalancers','LoadBalancerArn'),
            ('AWS::ECR::Repository','ecr','describe-repositories','repositories','repositoryArn'),
            ('AWS::Lambda::Function','lambda','list-functions','Functions','FunctionArn'),
            ('AWS::DynamoDB::Table','dynamodb','list-tables','TableNames',None),
        ]
        for kind, service, operation, key, idkey in specs:
            try:
                options = ['--owner-ids', 'self'] if operation == 'describe-snapshots' else []
                result = call(region, service, operation, *options)
                rows = result[key]
                if operation == 'describe-instances':
                    rows = [i for reservation in rows for i in reservation['Instances'] if i.get('State',{}).get('Name') != 'terminated']
                for row in rows:
                    if isinstance(row,str):
                        add(kind,row,arn=f'arn:{partition}:dynamodb:{region}:{account}:table/{row}')
                        continue
                    tags = {t.get('Key'):t.get('Value') for t in row.get('Tags',[]) if isinstance(t,dict)}
                    parents = []
                    if kind == 'AWS::EC2::Instance':
                        parents += [tags[k] for k in ('aws:autoscaling:groupName','eks:nodegroup-name') if tags.get(k)]
                    if kind == 'AWS::EC2::Volume':
                        parents += [a['InstanceId'] for a in row.get('Attachments',[]) if a.get('InstanceId')]
                    identifier = row[idkey]
                    arn = next((row[k] for k in ('Arn','ARN','DBInstanceArn','LoadBalancerArn','repositoryArn','FunctionArn','ResourceARN','FileSystemArn','AutoScalingGroupARN') if row.get(k)), None)
                    if not arn and kind.startswith('AWS::EC2::'):
                        suffix={'Instance':'instance','Volume':'volume','Snapshot':'snapshot','VPC':'vpc','Subnet':'subnet','SecurityGroup':'security-group','NatGateway':'natgateway'}[kind.split('::')[-1]]
                        arn=f'arn:{partition}:ec2:{region}:{account}:{suffix}/{identifier}'
                    add(kind,identifier,tags.get('Name') or row.get('DBInstanceIdentifier') or row.get('FunctionName') or row.get('repositoryName'),arn,parents,
                        kind == 'AWS::EC2::Instance' and bool(tags.get('aws:autoscaling:groupName') or tags.get('eks:nodegroup-name')))
                observed.append({'region':region,'kind':kind,'status':'partial' if result.get('NextToken') else 'observed','source':service})
            except (RuntimeError,KeyError,ValueError,TypeError):
                observed.append({'region':region,'kind':kind,'status':'unavailable','source':service})
        for kind,service,operation,key in [('AWS::EKS::Cluster','eks','list-clusters','clusters'),('AWS::SQS::Queue','sqs','list-queues','QueueUrls')]:
            try:
                for name in call(region,service,operation).get(key,[]):
                    if service=='eks':
                        arn=f'arn:{partition}:eks:{region}:{account}:cluster/{name}'
                        add(kind,name,name,arn)
                        try:
                            for group in call(region,'eks','list-nodegroups','--cluster-name',name)['nodegroups']:
                                data=call(region,'eks','describe-nodegroup','--cluster-name',name,'--nodegroup-name',group)['nodegroup']
                                add('AWS::EKS::Nodegroup',name+':'+group,group,data['nodegroupArn'],[arn])
                                for asg in data.get('resources',{}).get('autoScalingGroups',[]):
                                    add('AWS::AutoScaling::AutoScalingGroup',asg['name'],asg['name'],parents=[data['nodegroupArn']],platform=True)
                            observed.append({'region':region,'kind':'AWS::EKS::Nodegroup','status':'observed','source':'eks'})
                        except (RuntimeError,KeyError,ValueError):
                            observed.append({'region':region,'kind':'AWS::EKS::Nodegroup','status':'unavailable','source':'eks'})
                    else:
                        qname=name.rsplit('/',1)[-1]
                        add(kind,name,qname,f'arn:{partition}:sqs:{region}:{account}:{qname}')
                observed.append({'region':region,'kind':kind,'status':'observed','source':service})
            except (RuntimeError,KeyError,ValueError,TypeError):
                observed.append({'region':region,'kind':kind,'status':'unavailable','source':service})
        # Optional broad discovery; never enable recorders, indexes or paid services.
        try:
            recorders=call(region,'configservice','describe-configuration-recorder-status').get('ConfigurationRecordersStatus',[])
            if not any(r.get('recording') for r in recorders):
                raise RuntimeError('Recorder unavailable')
            response=call(region,'configservice','select-resource-config','--expression',
                'SELECT arn, resourceId, resourceName, resourceType, awsRegion, relationships')
            for raw in response.get('Results',[]):
                row=json.loads(raw)
                add(row['resourceType'],row['resourceId'],row.get('resourceName'),row.get('arn'),
                    [r['resourceId'] for r in row.get('relationships',[]) if r.get('resourceId')])
            observed.append({'region':region,'kind':'AWS Config recorded types','status':'partial','source':'configservice'})
        except (RuntimeError,KeyError,ValueError,TypeError):
            observed.append({'region':region,'kind':'AWS Config recorded types','status':'unavailable','source':'configservice'})
        try:
            response=call(region,'resource-explorer-2','search','--query-string','region:'+region)
            for row in response.get('Resources',[]):
                if row.get('OwningAccountId')==account:
                    add(row['ResourceType'],row['Arn'],arn=row['Arn'])
            observed.append({'region':region,'kind':'Resource Explorer indexed types','status':'partial','source':'resource-explorer-2'})
        except (RuntimeError,KeyError,ValueError,TypeError):
            observed.append({'region':region,'kind':'Resource Explorer indexed types','status':'unavailable','source':'resource-explorer-2'})
        return found,observed

    with ThreadPoolExecutor(max_workers=4) as pool:
        for found, observed in pool.map(regional, regions):
            assets.extend(found);coverage.extend(observed)
    try:
        for row in call('us-east-1','s3api','list-buckets').get('Buckets',[]):
            name=row['Name']
            try:
                region=call('us-east-1','s3api','get-bucket-location','--bucket',name).get('LocationConstraint') or 'us-east-1'
                if region=='EU':region='eu-west-1'
                assets.append({'kind':'AWS::S3::Bucket','id':name,'name':name,'arn':f'arn:{partition}:s3:::{name}','region':region,'parents':[],'platform':False})
            except (RuntimeError,ValueError,KeyError):
                coverage.append({'region':'unknown','kind':'AWS::S3::Bucket location','status':'unavailable','source':'s3api'})
        coverage.append({'region':'all','kind':'AWS::S3::Bucket','status':'observed','source':'s3api'})
    except (RuntimeError,ValueError,KeyError):
        coverage.append({'region':'all','kind':'AWS::S3::Bucket','status':'unavailable','source':'s3api'})
    for kind,operation,key in [('AWS::IAM::Role','list-roles','Roles'),('AWS::IAM::InstanceProfile','list-instance-profiles','InstanceProfiles'),('AWS::IAM::Policy','list-policies','Policies')]:
        try:
            options=['--scope','Local'] if operation=='list-policies' else []
            for row in call('us-east-1','iam',operation,*options).get(key,[]):
                assets.append({'kind':kind,'id':row['Arn'],'arn':row['Arn'],
                               'name':row.get('RoleName') or row.get('InstanceProfileName') or row.get('PolicyName'),
                               'region':'global','parents':[],'platform':False})
            coverage.append({'region':'global','kind':kind,'status':'observed','source':'iam'})
        except (RuntimeError,KeyError,ValueError):
            coverage.append({'region':'global','kind':kind,'status':'unavailable','source':'iam'})
    unique={}; by_arn={}
    for asset in assets:
        key=(asset['kind'],asset['region'],asset['id'])
        existing=by_arn.get(asset.get('arn'),key)
        if existing in unique:
            prior=unique[existing]
            if asset.get('platform'):
                prior.update(platform=True,parents=asset.get('parents',[]))
            if not prior.get('arn') and asset.get('arn'):
                prior['arn']=asset['arn']
        else:
            unique[existing]=asset
        if asset.get('arn'):
            by_arn[asset['arn']]=existing
    return {'resources':list(unique.values()),'coverage':coverage,'regions':regions,'complete':False}
