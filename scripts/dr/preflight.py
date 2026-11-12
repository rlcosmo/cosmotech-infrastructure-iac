#!/usr/bin/env python3
"""Read-only destination readiness. Explicit AWS release is a required human fact."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess


def capacity_budget(quota: float, used_vcpus: int, max_nodes: int,
                    node_vcpus: int, recovery_host: bool, stateful_pool: bool = False) -> dict:
    """Conservatively reserve existing compute plus the whole proposed topology."""
    if max_nodes not in (1, 2, 3) or node_vcpus <= 0 or used_vcpus < 0:
        raise ValueError("Invalid capacity inputs")
    required = (max_nodes + int(recovery_host) + int(stateful_pool)) * node_vcpus
    if required + used_vcpus > quota:
        raise ValueError(f"Insufficient quota: {required} proposed + {used_vcpus} existing > {quota} vCPUs")
    return dict(vcpu_quota=quota, existing_vcpus_reserved=used_vcpus,
                proposed_vcpus=required, remaining_vcpus=quota-used_vcpus-required)


def inspect(release_reference, max_nodes=2, recovery_host=False, stateful_pool=False):
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
    instances=aws('ec2','describe-instances','--filters','Name=instance-state-name,Values=pending,running')
    types=[i['InstanceType'] for r in instances['Reservations'] for i in r['Instances']
           if i.get('InstanceLifecycle') != 'spot']
    metadata=aws('ec2','describe-instance-types','--instance-types',*sorted(set(types+['t3.large'])))
    cpus={i['InstanceType']:i['VCpuInfo']['DefaultVCpus'] for i in metadata['InstanceTypes']}
    # All on-demand families are reserved conservatively, including separate quota pools.
    budget=capacity_budget(standard,sum(cpus[t] for t in types),max_nodes,cpus['t3.large'],recovery_host,stateful_pool)
    return dict(observed_at=datetime.now(timezone.utc).isoformat(),account=identity['Account'],
        role=identity['Arn'],region='us-east-2',release_reference=release_reference,
        standard_version='1.36',reviewed_azs=sorted(locations),capacity=budget,
        blockers=['Verify backup opt-in/copy freshness, secure operator connectivity and reviewed budget; capacity alone does not prove recovery readiness'],
        safe_to_apply=False)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--aws-release-reference',required=True)
    p.add_argument('--max-nodes',type=int,default=2)
    p.add_argument('--recovery-host',action='store_true')
    p.add_argument('--stateful-pool',action='store_true')
    args=p.parse_args()
    print(json.dumps(inspect(args.aws_release_reference,args.max_nodes,args.recovery_host,args.stateful_pool),indent=2))
