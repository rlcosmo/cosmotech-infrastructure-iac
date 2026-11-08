"""Initial IAM/SSM only, with temporary admin credentials. Never powers off resources."""
import argparse
import json
import secrets
import subprocess
import boto3
from botocore.config import Config

ACCOUNT = '414053644145'
REGION = 'us-east-1'
PARAMETER = '/cosmotech/power-manager/proxy-signing-key'


def statement(actions, resource, **kwargs):
    return {'Effect': 'Allow', 'Action': actions, 'Resource': resource, **kwargs}


def documents():
    read = ['ec2:DescribeInstances', 'ec2:DescribeAddresses', 'ec2:DescribeVolumes', 'ec2:DescribeSnapshots',
            'ec2:DescribeNatGateways', 'ec2:DescribeVpcs', 'ec2:DescribeSubnets', 'ec2:DescribeRouteTables',
            'ec2:DescribeSecurityGroups', 'ec2:DescribeInternetGateways', 'autoscaling:DescribeAutoScalingGroups',
            'elasticloadbalancing:DescribeLoadBalancers', 'elasticfilesystem:DescribeFileSystems', 'rds:DescribeDBInstances',
            'states:ListStateMachines','scheduler:ListSchedules','ssm:DescribeParameters','ssm:ListDocuments','tag:GetResources','backup:ListBackupVaults','backup:ListBackupPlans','cloudwatch:ListDashboards','lambda:ListFunctions', 'ecr:DescribeRepositories', 'logs:DescribeLogGroups', 'cloudwatch:DescribeAlarms',
            'secretsmanager:ListSecrets', 'acm:ListCertificates', 'events:ListRules', 'sqs:ListQueues', 'dynamodb:ListTables', 's3:ListAllMyBuckets']
    runtime = [statement(read, '*', Condition={'StringEquals': {'aws:RequestedRegion': REGION}}),
               statement(['iam:ListRoles', 'route53:ListHostedZones'], '*'),
               statement(['s3:GetBucketLocation'], 'arn:aws:s3:::*'),
               statement(['apigateway:GET'], ['arn:aws:apigateway:us-east-1::/apis', 'arn:aws:apigateway:us-east-1::/restapis']),
               statement(['eks:ListNodegroups', 'eks:DescribeCluster', 'eks:DescribeNodegroup'], [f'arn:aws:eks:{REGION}:{ACCOUNT}:cluster/aws-eks-istio-lab-dev', f'arn:aws:eks:{REGION}:{ACCOUNT}:nodegroup/aws-eks-istio-lab-dev/*/*']),
               statement(['sqs:GetQueueAttributes'], f'arn:aws:sqs:{REGION}:{ACCOUNT}:*'),
               statement(['ssm:GetParameter'], f'arn:aws:ssm:{REGION}:{ACCOUNT}:parameter{PARAMETER}'),
               statement(['dynamodb:GetItem', 'dynamodb:PutItem'], f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/cosmotech-power-control'),
               statement(['logs:CreateLogStream', 'logs:PutLogEvents'], f'arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/lambda/cosmotech-power-api:*')]
    # Current planning runtime has no Start/Stop/Scale, SSM execution, network or IAM writes.
    deploy = [statement(['sts:GetCallerIdentity'], '*'),
              statement(['iam:GetRole', 'iam:PassRole'], f'arn:aws:iam::{ACCOUNT}:role/cosmotech-power-runtime'),
              statement(['lambda:CreateFunction', 'lambda:GetFunction', 'lambda:GetFunctionConfiguration', 'lambda:GetFunctionCodeSigningConfig', 'lambda:UpdateFunctionCode', 'lambda:UpdateFunctionConfiguration', 'lambda:ListVersionsByFunction', 'lambda:ListTags', 'lambda:TagResource', 'lambda:UntagResource', 'lambda:PutFunctionConcurrency', 'lambda:GetFunctionConcurrency', 'lambda:AddPermission', 'lambda:GetPolicy', 'lambda:RemovePermission'], f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:cosmotech-power-api'),
              statement(['dynamodb:CreateTable', 'dynamodb:DescribeTable', 'dynamodb:DescribeContinuousBackups', 'dynamodb:UpdateContinuousBackups', 'dynamodb:UpdateTable', 'dynamodb:UpdateTimeToLive', 'dynamodb:DescribeTimeToLive', 'dynamodb:ListTagsOfResource', 'dynamodb:TagResource', 'dynamodb:DescribeContributorInsights', 'dynamodb:DescribeKinesisStreamingDestination', 'dynamodb:DescribeTableReplicaAutoScaling', 'dynamodb:GetResourcePolicy'], f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/cosmotech-power-control'),
              statement(['logs:CreateLogGroup', 'logs:PutRetentionPolicy', 'logs:ListTagsLogGroup', 'logs:ListTagsForResource', 'logs:TagResource', 'logs:TagLogGroup'], f'arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/lambda/cosmotech-power-api:*'),
              statement(['logs:ListTagsForResource', 'logs:TagResource'], f'arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/lambda/cosmotech-power-api'),
              statement(['logs:DescribeLogGroups'], '*'),
              statement(['apigateway:GET', 'apigateway:POST', 'apigateway:PATCH', 'apigateway:PUT'], ['arn:aws:apigateway:us-east-1::/apis', 'arn:aws:apigateway:us-east-1::/apis/*', 'arn:aws:apigateway:us-east-1::/tags/*']),
              statement(['s3:GetBucketLocation'], 'arn:aws:s3:::aws-serverless-lab-414053644145-tfstate-use1'),
              statement(['s3:ListBucket'], 'arn:aws:s3:::aws-serverless-lab-414053644145-tfstate-use1', Condition={'StringLike': {'s3:prefix': ['lab-v2/power-manager/*']}}),
              statement(['s3:GetObject', 's3:PutObject'], 'arn:aws:s3:::aws-serverless-lab-414053644145-tfstate-use1/lab-v2/power-manager/*'),
              statement(['s3:DeleteObject'], 'arn:aws:s3:::aws-serverless-lab-414053644145-tfstate-use1/lab-v2/power-manager/*.tflock')]
    # Explicitly forbid changes to the API that already existed before V2-004.
    return {'runtime': {'Version': '2012-10-17', 'Statement': runtime}, 'deploy': {'Version': '2012-10-17', 'Statement': deploy}}


def extend_execution_policies(policies, groups):
    bucket=f'arn:aws:s3:::cosmotech-power-snapshots-{ACCOUNT}-us-east-1'
    table_arn=f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/cosmotech-power-control'
    host=f'arn:aws:ec2:{REGION}:{ACCOUNT}:instance/i-0b33fdd51332e04e5'
    state_machine=f'arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:cosmotech-power-operations'
    executions=f'arn:aws:states:{REGION}:{ACCOUNT}:execution:cosmotech-power-operations:*'
    ssm_read=f'arn:aws:ssm:{REGION}:{ACCOUNT}:document/CosmoTech-Power-Read'
    host_read=f'arn:aws:ssm:{REGION}:{ACCOUNT}:document/CosmoTech-Power-Host-Read'
    host_execute=f'arn:aws:ssm:{REGION}:{ACCOUNT}:document/CosmoTech-Power-Host-Execute'
    hosts=[f'arn:aws:ec2:{REGION}:{ACCOUNT}:instance/{i}' for i in ['i-0def4e806c49cf60e','i-0b33fdd51332e04e5','i-02878f35eed1572d0']]
    ssm_execute=f'arn:aws:ssm:{REGION}:{ACCOUNT}:document/CosmoTech-Power-Execute'
    attributes=statement(['ec2:DescribeInstanceAttribute'],[f'arn:aws:ec2:{REGION}:{ACCOUNT}:instance/{i}' for i in ['i-0def4e806c49cf60e','i-0b33fdd51332e04e5','i-02878f35eed1572d0']])
    own_table=statement(['dynamodb:GetItem','dynamodb:PutItem','dynamodb:UpdateItem','dynamodb:DeleteItem'],table_arn)
    policies['runtime']['Statement'].extend([
        attributes,own_table,statement(['s3:GetObject','s3:GetObjectVersion'],bucket+'/*'),
        statement(['ssm:SendCommand'],[ssm_read,host_read,*hosts]),statement(['ssm:GetCommandInvocation'],'*',Condition={'StringEquals':{'aws:RequestedRegion':REGION}}),
        statement(['states:StartExecution'],state_machine),statement(['states:DescribeExecution'],executions)])
    worker=[attributes,
      statement(['ec2:DescribeInstances','ec2:DescribeInstanceStatus','ec2:DescribeVolumes','ec2:DescribeAddresses','ec2:DescribeNatGateways','ec2:DescribeVpcs','ec2:DescribeSubnets','ec2:DescribeRouteTables','ec2:DescribeSecurityGroups','ec2:DescribeInternetGateways',
       'elasticloadbalancing:DescribeLoadBalancers','elasticloadbalancing:DescribeLoadBalancerAttributes','elasticloadbalancing:DescribeListeners','elasticloadbalancing:DescribeRules','elasticloadbalancing:DescribeTargetGroups','elasticloadbalancing:DescribeTargetGroupAttributes','autoscaling:DescribeAutoScalingGroups','autoscaling:DescribeLifecycleHooks','ssm:DescribeInstanceInformation','ssm:GetCommandInvocation'],'*',Condition={'StringEquals':{'aws:RequestedRegion':REGION}}),
      statement(['eks:DescribeCluster','eks:ListNodegroups'],f'arn:aws:eks:{REGION}:{ACCOUNT}:cluster/aws-eks-istio-lab-dev'),
      statement(['eks:DescribeNodegroup','eks:UpdateNodegroupConfig'],f'arn:aws:eks:{REGION}:{ACCOUNT}:nodegroup/aws-eks-istio-lab-dev/aws-eks-istio-lab-dev-spot-named/*'),
      statement(['ec2:StartInstances','ec2:StopInstances'],[f'arn:aws:ec2:{REGION}:{ACCOUNT}:instance/{i}' for i in ['i-0def4e806c49cf60e','i-0b33fdd51332e04e5','i-02878f35eed1572d0']]),
      statement(['autoscaling:SuspendProcesses','autoscaling:ResumeProcesses'],[g['AutoScalingGroupARN'] for g in groups]),
      statement(['ec2:ModifyInstanceAttribute'],f'arn:aws:ec2:{REGION}:{ACCOUNT}:instance/*',Condition={'StringEquals':{'ec2:Attribute':'BlockDeviceMappings','ec2:ResourceTag/aws:autoscaling:groupName':[g['AutoScalingGroupName'] for g in groups]}}),
      statement(['ec2:ModifyInstanceAttribute'],f'arn:aws:ec2:{REGION}:{ACCOUNT}:volume/*',Condition={'StringEquals':{'ec2:ResourceTag/eks:cluster-name':'aws-eks-istio-lab-dev','ec2:ResourceTag/eks:nodegroup-name':'aws-eks-istio-lab-dev-spot-named'}}),
      statement(['s3:GetObject','s3:GetObjectVersion','s3:PutObject'],bucket+'/*'),own_table,
      statement(['route53:ListHostedZones','route53:ListResourceRecordSets'],'*'),
      statement(['sqs:GetQueueAttributes'],f'arn:aws:sqs:{REGION}:{ACCOUNT}:*'),
      statement(['dynamodb:DescribeTable'],f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/*'),
      statement(['ssm:SendCommand'],[ssm_read,ssm_execute,host_read,host_execute,*hosts]),
      statement(['logs:CreateLogStream','logs:PutLogEvents'],f'arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/lambda/cosmotech-power-worker:*'),
      {'Effect':'Deny','Action':['ec2:TerminateInstances','ec2:DeleteVolume','ec2:DeleteSnapshot','ec2:ReleaseAddress','ec2:AssociateAddress','ec2:DisassociateAddress','ec2:DeleteNatGateway','ec2:CreateNatGateway','ec2:CreateRoute','ec2:ReplaceRoute','ec2:DeleteRoute','ec2:ModifyNetworkInterfaceAttribute','ec2:AuthorizeSecurityGroupIngress','ec2:RevokeSecurityGroupIngress','ec2:AuthorizeSecurityGroupEgress','ec2:RevokeSecurityGroupEgress','elasticloadbalancing:Create*','elasticloadbalancing:Delete*','elasticloadbalancing:Modify*','elasticloadbalancing:Set*','route53:Change*','iam:*','s3:DeleteObject','s3:DeleteObjectVersion','dynamodb:DeleteTable'],'Resource':'*'},
      {'Effect':'Deny','Action':['ec2:ModifyInstanceAttribute'],'Resource':f'arn:aws:ec2:{REGION}:{ACCOUNT}:instance/*','Condition':{'StringNotEquals':{'ec2:Attribute':'BlockDeviceMappings'}}}]
    policies['worker']={'Version':'2012-10-17','Statement':worker}
    policies['orchestrator']={'Version':'2012-10-17','Statement':[statement(['lambda:InvokeFunction'],f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:cosmotech-power-worker')]}
    # Publisher cannot edit IAM; these roles are provisioned by this initial bootstrap only.
    policies['deploy']['Statement'].extend([
      statement(['iam:GetRole','iam:PassRole'],[f'arn:aws:iam::{ACCOUNT}:role/cosmotech-power-worker',f'arn:aws:iam::{ACCOUNT}:role/cosmotech-power-orchestrator']),
      statement(['lambda:CreateFunction','lambda:GetFunction','lambda:GetFunctionConfiguration','lambda:GetFunctionCodeSigningConfig','lambda:UpdateFunctionCode','lambda:UpdateFunctionConfiguration','lambda:ListVersionsByFunction','lambda:ListTags','lambda:TagResource','lambda:UntagResource','lambda:PutFunctionConcurrency','lambda:GetFunctionConcurrency','lambda:InvokeFunction'],f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:cosmotech-power-worker'),
      statement(['logs:CreateLogGroup','logs:PutRetentionPolicy','logs:ListTagsLogGroup','logs:ListTagsForResource','logs:TagResource','logs:TagLogGroup'],[f'arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/lambda/cosmotech-power-worker',f'arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/lambda/cosmotech-power-worker:*']),
      statement(['states:StartExecution'],state_machine),statement(['states:DescribeExecution','states:GetExecutionHistory'],executions),
      statement(['states:ValidateStateMachineDefinition'],'*',Condition={'StringEquals':{'aws:RequestedRegion':REGION}}),
      statement(['states:CreateStateMachine','states:DescribeStateMachine','states:ListStateMachineVersions','states:UpdateStateMachine','states:TagResource','states:UntagResource','states:ListTagsForResource'],state_machine),
      statement(['ssm:CreateDocument','ssm:GetDocument','ssm:DescribeDocument','ssm:DescribeDocumentPermission','ssm:UpdateDocument','ssm:UpdateDocumentDefaultVersion','ssm:ListDocumentVersions','ssm:ListTagsForResource','ssm:AddTagsToResource'],[ssm_read,ssm_execute,host_read,host_execute]),
      statement(['s3:CreateBucket','s3:GetBucket*','s3:ListBucket','s3:PutBucketVersioning','s3:PutEncryptionConfiguration','s3:PutBucketPublicAccessBlock','s3:PutBucketPolicy','s3:PutBucketTagging','s3:PutBucketOwnershipControls','s3:GetEncryptionConfiguration','s3:GetAccelerateConfiguration','s3:GetLifecycleConfiguration','s3:GetReplicationConfiguration','s3:TagResource','s3:UntagResource','s3:ListTagsForResource'],bucket),
      statement(['dynamodb:GetItem'],table_arn)])
    return policies


def publisher_trust():
    # Trust is durable; credentials minted by STS remain short-lived. A calendar
    # cutoff here would make routine CI require another administrative bootstrap.
    return {'Version':'2012-10-17','Statement':[{
        'Effect':'Allow','Principal':{'Federated':f'arn:aws:iam::{ACCOUNT}:oidc-provider/token.actions.githubusercontent.com'},
        'Action':'sts:AssumeRoleWithWebIdentity','Condition':{'StringEquals':{
            'token.actions.githubusercontent.com:aud':'sts.amazonaws.com',
            'token.actions.githubusercontent.com:sub':'repo:rlcosmo@124368446/cosmotech-infrastructure-iac@1445869018:ref:refs/heads/main'}}}]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    config = Config(connect_timeout=5, read_timeout=15, retries={'mode': 'standard', 'total_max_attempts': 2})
    exported = json.loads(subprocess.check_output(['aws', '--profile', 'serverless-lab', 'configure', 'export-credentials', '--format', 'process']))
    assert exported['AccessKeyId'].startswith('ASIA') and exported.get('SessionToken'), 'Temporary session required'
    session = boto3.Session(profile_name='serverless-lab',aws_access_key_id=exported['AccessKeyId'], aws_secret_access_key=exported['SecretAccessKey'], aws_session_token=exported['SessionToken'], region_name=REGION)
    identity = session.client('sts', config=config).get_caller_identity()
    assert identity['Account'] == ACCOUNT
    iam = session.client('iam', config=config)
    policies = documents()
    ng=session.client('eks',config=config).describe_nodegroup(clusterName='aws-eks-istio-lab-dev',nodegroupName='aws-eks-istio-lab-dev-spot-named')['nodegroup']
    group_names=[v['name'] for v in ng['resources']['autoScalingGroups']]
    groups=session.client('autoscaling',config=config).describe_auto_scaling_groups(AutoScalingGroupNames=group_names)['AutoScalingGroups']
    assert len(groups)==1, 'Review changed group topology before granting capacity permissions'
    policies=extend_execution_policies(policies,groups)
    api = session.client('apigatewayv2', config=config)
    existing_ids = [v['ApiId'] for page in api.get_paginator('get_apis').paginate() for v in page['Items'] if v['Name'] != 'cosmotech-power-api']
    if existing_ids:
        policies['deploy']['Statement'].append({'Effect': 'Deny', 'Action': ['apigateway:POST', 'apigateway:PUT', 'apigateway:PATCH', 'apigateway:DELETE'], 'Resource': [f'arn:aws:apigateway:us-east-1::/apis/{i}*' for i in existing_ids]})
    power_ids = [v['ApiId'] for page in api.get_paginator('get_apis').paginate() for v in page['Items'] if v['Name'] == 'cosmotech-power-api']
    if power_ids:
        policies['deploy']['Statement'].append(statement(['apigateway:TagResource'], [f'arn:aws:apigateway:us-east-1::/apis/{i}*' for i in power_ids]))
    trusts = {
        'runtime': {'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]},
        'deploy': publisher_trust()}
    trusts['worker']=trusts['runtime']
    trusts['orchestrator']={'Version':'2012-10-17','Statement':[{'Effect':'Allow','Principal':{'Service':'states.amazonaws.com'},'Action':'sts:AssumeRole','Condition':{'StringEquals':{'aws:SourceAccount':ACCOUNT},'ArnLike':{'aws:SourceArn':f'arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:cosmotech-power-operations'}}}]}
    for policy in policies.values():
        assert len(json.dumps(policy,separators=(',',':'))) < 10240, 'Inline IAM policy exceeds role quota'
    print(json.dumps({'identity': identity['Arn'], 'apply': args.apply, 'roles': ['cosmotech-power-'+v for v in policies],
                      'deploy_trust': 'immutable repository/main + STS audience; short-lived STS credentials', 'policies': policies, 'ssm_parameter': PARAMETER, 'existing_apis_denied': existing_ids}))
    if not args.apply:
        return
    for kind in ['runtime', 'deploy', 'worker', 'orchestrator']:
        name = 'cosmotech-power-' + kind
        try:
            iam.get_role(RoleName=name)
        except iam.exceptions.NoSuchEntityException:
            iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trusts[kind]), Tags=[{'Key': 'Project', 'Value': 'cosmotech-power-manager'}])
        else:
            iam.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(trusts[kind]))
        iam.put_role_policy(RoleName=name, PolicyName='cosmotech-power-' + kind, PolicyDocument=json.dumps(policies[kind]))
    bucket=f'arn:aws:s3:::cosmotech-power-snapshots-{ACCOUNT}-us-east-1'
    host_policy={'Version':'2012-10-17','Statement':[statement(['s3:GetObject','s3:GetObjectVersion','s3:PutObject'],[bucket+'/plans/*',bucket+'/operations/*'])]}
    iam.put_role_policy(RoleName='cosmotech-argocd-dev-host',PolicyName='cosmotech-power-snapshots',PolicyDocument=json.dumps(host_policy))
    for profile,iid in [('cosmotech-itsm-dev-instance-profile','i-0def4e806c49cf60e'),('cosmotech-zabbix-dev','i-02878f35eed1572d0')]:
        roles=iam.get_instance_profile(InstanceProfileName=profile)['InstanceProfile']['Roles']
        assert len(roles)==1
        policy={'Version':'2012-10-17','Statement':[statement(['s3:GetObject','s3:GetObjectVersion','s3:PutObject'],bucket+'/plans/*/hosts/'+iid+'.json')]}
        if iid=='i-0def4e806c49cf60e':policy['Statement'].append(statement(['ssm:ListCommands'],'*',Condition={'StringEquals':{'aws:RequestedRegion':REGION}}))
        iam.put_role_policy(RoleName=roles[0]['RoleName'],PolicyName='cosmotech-power-host-snapshots',PolicyDocument=json.dumps(policy))
    ssm = session.client('ssm', config=config)
    try:
        ssm.get_parameter(Name=PARAMETER, WithDecryption=False)
    except ssm.exceptions.ParameterNotFound:
        ssm.put_parameter(Name=PARAMETER, Value=secrets.token_hex(32), Type='SecureString', Overwrite=False, Tags=[{'Key': 'Project', 'Value': 'cosmotech-power-manager'}])
    subprocess.run(['gh', 'variable', 'set', 'POWER_MANAGER_DEPLOY_ROLE_ARN', '--repo', 'rlcosmo/cosmotech-infrastructure-iac', '--body', f'arn:aws:iam::{ACCOUNT}:role/cosmotech-power-deploy'], check=True)
    print(json.dumps({'bootstrap': 'IAM and SecureString configured; no power or network operations performed'}))

if __name__ == '__main__':
    main()
