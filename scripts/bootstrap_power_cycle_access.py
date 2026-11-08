"""Temporary IAM-only access for the explicitly owner-authorized real test.

The administrative AWS session only grants/revokes an expiring OIDC role policy;
it never invokes the power API or stops/scales resources. No persistent profiles
or long-lived credentials are changed. Revoke immediately after the test.
"""
import argparse
from datetime import datetime,timedelta,timezone
import json
import subprocess
import boto3
from botocore.config import Config

ROLE='cosmotech-power-deploy'
POLICY='cosmotech-power-cycle-temporary'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['grant','revoke']);args=parser.parse_args()
    credentials=json.loads(subprocess.check_output(['aws','--profile','serverless-lab','configure','export-credentials','--format','process']))
    assert credentials['AccessKeyId'].startswith('ASIA') and credentials.get('SessionToken')
    session=boto3.Session(profile_name='serverless-lab',aws_access_key_id=credentials['AccessKeyId'],aws_secret_access_key=credentials['SecretAccessKey'],aws_session_token=credentials['SessionToken'],region_name='us-east-1')
    config=Config(connect_timeout=5,read_timeout=15,retries={'mode':'standard','total_max_attempts':2})
    identity=session.client('sts',config=config).get_caller_identity()
    assert identity['Account']=='414053644145'
    iam=session.client('iam',config=config)
    if args.action=='revoke':
        try:iam.delete_role_policy(RoleName=ROLE,PolicyName=POLICY)
        except iam.exceptions.NoSuchEntityException:pass
        print(json.dumps({'temporary_access':'revoked','role':ROLE}));return
    expiry=(datetime.now(timezone.utc)+timedelta(hours=3)).isoformat()
    condition={'StringEquals':{'aws:RequestedRegion':'us-east-1'},'DateLessThan':{'aws:CurrentTime':expiry}}
    policy={'Version':'2012-10-17','Statement':[
        {'Effect':'Allow','Action':['lambda:InvokeFunction'],'Resource':'arn:aws:lambda:us-east-1:414053644145:function:cosmotech-power-api','Condition':condition},
        {'Effect':'Allow','Action':['ssm:ListCommands'],'Resource':'*','Condition':condition}]}
    iam.put_role_policy(RoleName=ROLE,PolicyName=POLICY,PolicyDocument=json.dumps(policy))
    print(json.dumps({'temporary_access':'granted','role':ROLE,'expires':expiry,'actions':['lambda:InvokeFunction on Power API only','ssm:ListCommands us-east-1']}))

if __name__=='__main__':main()
