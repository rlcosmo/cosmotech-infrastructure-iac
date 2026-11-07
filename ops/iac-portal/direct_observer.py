"""Autonomous Git -> Terraform plan -> AWS metadata comparison. Never applies AWS changes."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from direct_comparison import compare, flatten
from direct_inventory import inventory

REPOSITORY = 'https://github.com/rlcosmo/cosmotech-infrastructure-iac.git'
BRANCH = 'main'
DIRECTORY = 'terraform/environments/dev'
HOME = Path('/var/lib/cosmotech-iac-observer')
PUBLIC = Path('/var/lib/cosmotech-iac/comparison')
QUEUE = Path('/run/cosmotech-iac-observer')


class ObservationError(RuntimeError):
    def __init__(self, stage, diagnostics=None):
        self.stage, self.diagnostics = stage, diagnostics or {}
        super().__init__(stage)


def command(args, env, *, cwd=None, timeout=180, codes=(0,)):
    result = subprocess.run(args,cwd=cwd,env=env,capture_output=True,timeout=timeout)
    if result.returncode not in codes:
        diagnostics={'binary':Path(args[0]).name,'returncode':result.returncode}
        message=result.stderr.decode(errors='replace')+result.stdout.decode(errors='replace')
        diagnostics['denied_actions']=sorted(set(re.findall(r'not authorized to perform:?\s+([a-z0-9-]+:[A-Za-z0-9]+)',message)))
        diagnostics['aws_error_codes']=sorted(set(re.findall(r'(?:api error |An error occurred \()([A-Za-z0-9]+)',message)))
        raise ObservationError('command_failed',diagnostics)
    return result.stdout


def aws(env, region, service, operation, *args):
    return json.loads(command(['aws','--region',region,'--output','json','--cli-connect-timeout','5',
        '--cli-read-timeout','20',service,operation,*args],env,timeout=90) or b'{}')


def write_snapshot(value):
    PUBLIC.mkdir(mode=0o750,parents=True,exist_ok=True)
    target=PUBLIC/'current.json'
    temporary=PUBLIC/'current.tmp'
    temporary.write_text(json.dumps(value))
    temporary.chmod(0o640)
    temporary.replace(target)


def observe(role_arn):
    source={'repository':REPOSITORY,'branch':BRANCH,'directory':DIRECTORY,'commit':None}
    stage='identity'
    write_snapshot({'schema_version':2,'state':'checking','source':source,
                    'attempted_at':datetime.now(timezone.utc).isoformat(),'resources':[],'coverage':[]})
    try:
        env={k:v for k,v in os.environ.items() if k not in {'AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_SESSION_TOKEN','AWS_PROFILE','AWS_DEFAULT_PROFILE','TF_LOG','TF_LOG_PATH','GIT_CONFIG','GIT_CONFIG_GLOBAL'}}
        env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin'
        cache=HOME/'plugins'
        cache.mkdir(parents=True,exist_ok=True,mode=0o700)
        env['TF_PLUGIN_CACHE_DIR']=str(cache)
        env.update(AWS_CONFIG_FILE='/dev/null',AWS_SHARED_CREDENTIALS_FILE='/dev/null',AWS_MAX_ATTEMPTS='2',
                   AWS_RETRY_MODE='standard',TF_IN_AUTOMATION='1',GIT_TERMINAL_PROMPT='0',GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL='/dev/null')
        # The source is the host's temporary instance profile, never a user/root credential.
        host=aws(env,'us-east-1','sts','get-caller-identity')
        if ':assumed-role/cosmotech-itsm-dev-instance-role/' not in host['Arn']:
            raise ObservationError('unexpected_host_identity')
        credentials=aws(env,'us-east-1','sts','assume-role','--role-arn',role_arn,
                        '--role-session-name','IaCPortalObserver','--duration-seconds','3600')['Credentials']
        env.update(AWS_ACCESS_KEY_ID=credentials['AccessKeyId'],AWS_SECRET_ACCESS_KEY=credentials['SecretAccessKey'],AWS_SESSION_TOKEN=credentials['SessionToken'])
        identity=aws(env,'us-east-1','sts','get-caller-identity')
        if identity['Account']!=host['Account'] or ':assumed-role/cosmotech-iac-observer/' not in identity['Arn']:
            raise ObservationError('unexpected_observer_identity')
        stage='git'
        HOME.mkdir(mode=0o700,parents=True,exist_ok=True)
        # Raw plan/state/checkout are private and deleted after every attempt.
        with tempfile.TemporaryDirectory(prefix='comparison-',dir=HOME) as temporary:
            checkout=Path(temporary)/'source'
            command(['git','-c','core.hooksPath=/dev/null','clone','--depth=1','--single-branch','--branch',BRANCH,REPOSITORY,str(checkout)],env,timeout=180)
            source['commit']=command(['git','rev-parse','HEAD'],env,cwd=checkout).decode().strip()
            if not re.fullmatch('[a-f0-9]{40}',source['commit']):
                raise ObservationError('invalid_git_revision')
            stage='source_artifacts'
            # Artifact preparation is local code from the canonical reviewed main; no apply.
            command(['/usr/bin/python3','scripts/build_lambda_artifacts.py'],env,cwd=checkout)
            command(['/usr/bin/python3','scripts/build_lab_v2_bundle.py'],env,cwd=checkout)
            stage='terraform_init'
            stack=checkout/DIRECTORY
            command(['terraform','init','-input=false','-lockfile=readonly'],env,cwd=stack,timeout=300)
            stage='git_vs_aws_plan'
            plan=Path(temporary)/'comparison.plan'
            command(['terraform','plan','-input=false','-refresh=true','-lock=false','-detailed-exitcode','-out='+str(plan)],env,cwd=stack,timeout=600,codes=(0,2))
            payload=json.loads(command(['terraform','show','-json',str(plan)],env,cwd=stack,timeout=120))
            plans=[(DIRECTORY,payload)]
            # Evaluate the other active roots in the same canonical repository so
            # bootstrap resources are not falsely reported as absent from Git.
            admin='terraform/bootstrap/lab_v2_admin'
            if (checkout/admin).is_dir():
                stage='bootstrap_git_evaluation'
                trust=aws(env,'us-east-1','iam','get-role','--role-name','cosmotech-lab-v2-bootstrap')['Role']['AssumeRolePolicyDocument']
                expiry=trust['Statement'][0]['Condition']['DateLessThan']['aws:CurrentTime']
                live=flatten(payload.get('prior_state',{}).get('values',{}).get('root_module',{}))
                targets=[r['values']['id'] for r in live if r.get('type')=='aws_instance' and r.get('values',{}).get('tags',{}).get('Name')=='cosmotech-itsm-dev']
                if len(targets)!=1:raise ObservationError('bootstrap_target_unknown')
                command(['terraform','init','-input=false','-lockfile=readonly',
                         '-backend-config=bucket=aws-serverless-lab-'+identity['Account']+'-tfstate-use1'],env,cwd=checkout/admin,timeout=300)
                admin_plan=Path(temporary)/'bootstrap.plan'
                command(['terraform','plan','-input=false','-refresh=true','-lock=false','-detailed-exitcode',
                         '-var=expected_account_id='+identity['Account'],'-var=itsm_instance_id='+targets[0],
                         '-var=authorization_expires_at='+expiry,'-out='+str(admin_plan)],env,cwd=checkout/admin,timeout=300,codes=(0,2))
                plans.append((admin,json.loads(command(['terraform','show','-json',str(admin_plan)],env,cwd=checkout/admin))))
            backend='terraform/bootstrap/state_backend'
            if (checkout/backend).is_dir():
                stage='backend_git_evaluation'
                command(['terraform','init','-input=false','-lockfile=readonly'],env,cwd=checkout/backend,timeout=300)
                backend_plan=Path(temporary)/'backend.plan'
                command(['terraform','plan','-input=false','-refresh=true','-lock=false','-detailed-exitcode','-out='+str(backend_plan)],env,cwd=checkout/backend,timeout=300,codes=(0,2))
                plans.append((backend,json.loads(command(['terraform','show','-json',str(backend_plan)],env,cwd=checkout/backend))))
            combined={'planned_values':{'root_module':{'resources':[]}},'prior_state':{'values':{'root_module':{'resources':[]}}},'resource_changes':[]}
            for root,part in plans:
                if part.get('errored') or part.get('deferred_changes') or part.get('complete') is False:
                    raise ObservationError('incomplete_root_evaluation')
                for field,target in [('planned_values',combined['planned_values']['root_module']['resources'])]:
                    for row in flatten(part.get(field,{}).get('root_module',{})):
                        target.append({**row,'address':root+'::'+row['address']})
                for row in flatten(part.get('prior_state',{}).get('values',{}).get('root_module',{})):
                    combined['prior_state']['values']['root_module']['resources'].append({**row,'address':root+'::'+row['address']})
                combined['resource_changes'] += [{**row,'address':root+'::'+row['address']} for row in part.get('resource_changes',[])]
            source['directories']=[root for root,_ in plans]
            stage='aws_inventory'
            discovered=inventory(lambda region,service,operation,*args:aws(env,region,service,operation,*args),identity['Account'],identity['Arn'].split(':')[1])
            stage='comparison'
            report=compare(combined,discovered,source)
            write_snapshot(report)
            print(json.dumps({'state':'ready','commit':source['commit'],'resources':len(report['resources']),
                              'drift_count':report['drift_count'],'coverage_entries':len(report['coverage'])}),flush=True)
    except (ObservationError,OSError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired) as error:
        diagnostics=error.diagnostics if isinstance(error,ObservationError) else {'error_type':type(error).__name__}
        write_snapshot({'schema_version':2,'state':'unavailable','source':source,'resources':[],
                        'coverage':[],'failed_stage':stage,'diagnostics':diagnostics,
                        'attempted_at':datetime.now(timezone.utc).isoformat()})
        print(json.dumps({'state':'unavailable','stage':stage,**diagnostics}),flush=True)
        return False
    return True


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--role-arn',required=True)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    if not re.fullmatch(r'arn:aws:iam::[0-9]{12}:role/cosmotech-iac-observer',args.role_arn):
        raise SystemExit('Fixed observer role is required')
    os.umask(0o027)
    if args.once:
        raise SystemExit(0 if observe(args.role_arn) else 1)
    next_run=0
    last_start=0
    while True:
        request=QUEUE/'refresh.request'
        if time.monotonic()>=next_run or (request.exists() and time.monotonic()-last_start>=60):
            last_start=time.monotonic()
            request.unlink(missing_ok=True)
            observe(args.role_arn)
            next_run=time.monotonic()+300
        time.sleep(10)


if __name__=='__main__':main()
