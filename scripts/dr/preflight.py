#!/usr/bin/env python3
"""Read-only destination readiness. Explicit AWS release is a required human fact."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess


def inspect(release_reference):
    if not release_reference or len(release_reference)<10:
        raise ValueError('Official account release reference is required; ACTIVE plan/credits are insufficient')
    def aws(service,operation,*args):
        r=subprocess.run(['aws',service,operation,'--region','us-east-2','--cli-connect-timeout','5',
                          '--cli-read-timeout','20',*args],capture_output=True,text=True)
        if r.returncode:raise RuntimeError(service+':'+operation+' failed; halt provisioning (do not widen IAM blindly)')
        return json.loads(r.stdout)
    identity=aws('sts','get-caller-identity')
    if identity['Account']!='414053644145' or ':assumed-role/' not in identity['Arn']:
        raise ValueError('Use an authorized temporary non-root role in account 414053644145')
    versions=aws('eks','describe-cluster-versions')
    supported=[v['clusterVersion'] for v in versions['clusterVersions'] if v['status']=='STANDARD_SUPPORT']
    if '1.36' not in supported:raise ValueError('Pinned EKS version no longer in standard support; update compatibility/cost review')
    offerings=aws('ec2','describe-instance-type-offerings','--location-type','availability-zone','--filters','Name=instance-type,Values=t3.large')
    locations={v['Location'] for v in offerings['InstanceTypeOfferings']}
    if not {'us-east-2a','us-east-2b','us-east-2c'}.issubset(locations):raise ValueError('Missing reviewed AZ/type offering')
    quotas=aws('service-quotas','list-service-quotas','--service-code','ec2')
    standard=next((v['Value'] for v in quotas['Quotas'] if v['QuotaCode']=='L-1216C47A'),0)
    if standard<6:raise ValueError('Need at least six regional standard On-Demand vCPUs; verify current usage too')
    return dict(observed_at=datetime.now(timezone.utc).isoformat(),account=identity['Account'],
        role=identity['Arn'],region='us-east-2',release_reference=release_reference,
        standard_version='1.36',reviewed_azs=sorted(locations),vcpu_quota=standard,
        blockers=['Manually verify spare quota after current usage, backup opt-in/copy freshness, secure operator connectivity and reviewed budget'],
        safe_to_apply=False)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--aws-release-reference',required=True)
    print(json.dumps(inspect(p.parse_args().aws_release_reference),indent=2))
