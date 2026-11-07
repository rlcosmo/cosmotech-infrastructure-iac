"""Initial IAM/SSM only, with temporary admin credentials. Never powers off resources."""
import argparse
import json
import secrets
import subprocess
from datetime import datetime, timedelta, timezone
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
            'lambda:ListFunctions', 'ecr:DescribeRepositories', 'logs:DescribeLogGroups', 'cloudwatch:DescribeAlarms',
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
              statement(['lambda:CreateFunction', 'lambda:GetFunction', 'lambda:GetFunctionConfiguration', 'lambda:UpdateFunctionCode', 'lambda:UpdateFunctionConfiguration', 'lambda:ListVersionsByFunction', 'lambda:ListTags', 'lambda:TagResource', 'lambda:UntagResource', 'lambda:PutFunctionConcurrency', 'lambda:GetFunctionConcurrency', 'lambda:AddPermission', 'lambda:GetPolicy', 'lambda:RemovePermission'], f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:cosmotech-power-api'),
              statement(['dynamodb:CreateTable', 'dynamodb:DescribeTable', 'dynamodb:DescribeContinuousBackups', 'dynamodb:UpdateContinuousBackups', 'dynamodb:UpdateTable', 'dynamodb:DescribeTimeToLive', 'dynamodb:ListTagsOfResource', 'dynamodb:TagResource', 'dynamodb:DescribeContributorInsights', 'dynamodb:DescribeKinesisStreamingDestination', 'dynamodb:DescribeTableReplicaAutoScaling', 'dynamodb:GetResourcePolicy'], f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/cosmotech-power-control'),
              statement(['logs:CreateLogGroup', 'logs:PutRetentionPolicy', 'logs:ListTagsLogGroup', 'logs:ListTagsForResource', 'logs:TagResource', 'logs:TagLogGroup'], f'arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/lambda/cosmotech-power-api:*'),
              statement(['logs:DescribeLogGroups'], '*'),
              statement(['apigateway:GET', 'apigateway:POST', 'apigateway:PATCH', 'apigateway:PUT'], ['arn:aws:apigateway:us-east-1::/apis', 'arn:aws:apigateway:us-east-1::/apis/*', 'arn:aws:apigateway:us-east-1::/tags/*']),
              statement(['s3:GetBucketLocation'], 'arn:aws:s3:::aws-serverless-lab-414053644145-tfstate-use1'),
              statement(['s3:ListBucket'], 'arn:aws:s3:::aws-serverless-lab-414053644145-tfstate-use1', Condition={'StringLike': {'s3:prefix': ['lab-v2/power-manager/*']}}),
              statement(['s3:GetObject', 's3:PutObject'], 'arn:aws:s3:::aws-serverless-lab-414053644145-tfstate-use1/lab-v2/power-manager/*'),
              statement(['s3:DeleteObject'], 'arn:aws:s3:::aws-serverless-lab-414053644145-tfstate-use1/lab-v2/power-manager/*.tflock')]
    # Explicitly forbid changes to the API that already existed before V2-004.
    return {'runtime': {'Version': '2012-10-17', 'Statement': runtime}, 'deploy': {'Version': '2012-10-17', 'Statement': deploy}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    config = Config(connect_timeout=5, read_timeout=15, retries={'mode': 'standard', 'total_max_attempts': 2})
    exported = json.loads(subprocess.check_output(['aws', '--profile', 'serverless-lab', 'configure', 'export-credentials', '--format', 'process']))
    assert exported['AccessKeyId'].startswith('ASIA') and exported.get('SessionToken'), 'Temporary session required'
    session = boto3.Session(aws_access_key_id=exported['AccessKeyId'], aws_secret_access_key=exported['SecretAccessKey'], aws_session_token=exported['SessionToken'], region_name=REGION)
    identity = session.client('sts', config=config).get_caller_identity()
    assert identity['Account'] == ACCOUNT
    iam = session.client('iam', config=config)
    policies = documents()
    api = session.client('apigatewayv2', config=config)
    existing_ids = [v['ApiId'] for page in api.get_paginator('get_apis').paginate() for v in page['Items'] if v['Name'] != 'cosmotech-power-api']
    if existing_ids:
        policies['deploy']['Statement'].append({'Effect': 'Deny', 'Action': ['apigateway:POST', 'apigateway:PUT', 'apigateway:PATCH', 'apigateway:DELETE'], 'Resource': [f'arn:aws:apigateway:us-east-1::/apis/{i}*' for i in existing_ids]})
    expiry = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    trusts = {
        'runtime': {'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]},
        'deploy': {'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Principal': {'Federated': f'arn:aws:iam::{ACCOUNT}:oidc-provider/token.actions.githubusercontent.com'},
                   'Action': 'sts:AssumeRoleWithWebIdentity', 'Condition': {'StringEquals': {'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
                   'token.actions.githubusercontent.com:sub': 'repo:rlcosmo@124368446/cosmotech-infrastructure-iac@1445869018:ref:refs/heads/main'},
                   'DateLessThan': {'aws:CurrentTime': expiry}}}]}}
    print(json.dumps({'identity': identity['Arn'], 'apply': args.apply, 'roles': ['cosmotech-power-runtime', 'cosmotech-power-deploy'],
                      'deploy_trust_expires': expiry, 'policies': policies, 'ssm_parameter': PARAMETER, 'existing_apis_denied': existing_ids}))
    if not args.apply:
        return
    for kind in ['runtime', 'deploy']:
        name = 'cosmotech-power-' + kind
        try:
            iam.get_role(RoleName=name)
        except iam.exceptions.NoSuchEntityException:
            iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trusts[kind]), Tags=[{'Key': 'Project', 'Value': 'cosmotech-power-manager'}])
        else:
            iam.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(trusts[kind]))
        iam.put_role_policy(RoleName=name, PolicyName='cosmotech-power-' + kind, PolicyDocument=json.dumps(policies[kind]))
    ssm = session.client('ssm', config=config)
    try:
        ssm.get_parameter(Name=PARAMETER, WithDecryption=False)
    except ssm.exceptions.ParameterNotFound:
        ssm.put_parameter(Name=PARAMETER, Value=secrets.token_hex(32), Type='SecureString', Overwrite=False, Tags=[{'Key': 'Project', 'Value': 'cosmotech-power-manager'}])
    subprocess.run(['gh', 'variable', 'set', 'POWER_MANAGER_DEPLOY_ROLE_ARN', '--repo', 'rlcosmo/cosmotech-infrastructure-iac', '--body', f'arn:aws:iam::{ACCOUNT}:role/cosmotech-power-deploy'], check=True)
    print(json.dumps({'bootstrap': 'IAM and SecureString configured; no power or network operations performed'}))

if __name__ == '__main__':
    main()
