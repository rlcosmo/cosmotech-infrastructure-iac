"""One idempotent administrator entrypoint; temporary auth only, no secret output."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from publication.admin_bootstrap import authorization_deadline, check_admin_plan, validate_rbac, validate_temporary

IAC = 'rlcosmo/cosmotech-infrastructure-iac'
RBAC = 'rlcosmo/cosmotech-kubernetes-rbac'


def export_session(profile: str, config: str, shared: str) -> dict:
    environment = {k: v for k, v in os.environ.items() if k not in {'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN', 'AWS_PROFILE', 'AWS_DEFAULT_PROFILE'}}
    environment.update(AWS_CONFIG_FILE=config, AWS_SHARED_CREDENTIALS_FILE=shared)
    response = subprocess.run(['aws', '--profile', profile, 'configure', 'export-credentials', '--format', 'process'], env=environment, capture_output=True, timeout=30)
    if response.returncode:
        raise ValueError('AUTHENTICATION_REQUIRED: aws login --profile ' + profile)
    credentials = json.loads(response.stdout)
    validate_temporary(credentials, dt.datetime.now(dt.timezone.utc))
    return credentials


def command(arguments: list[str], *, env: dict | None = None, value: object = None, timeout: int = 180) -> bytes:
    response = subprocess.run(arguments, env=env, input=json.dumps(value).encode() if value is not None else None,
                              capture_output=True, timeout=timeout)
    if response.returncode:
        message = response.stderr.decode(errors='replace')
        action = re.search(r'not authorized to perform:?\s+([a-z0-9-]+:[A-Za-z0-9]+)', message)
        raise RuntimeError('Permission required: ' + action[1] if action else 'Command failed: ' + ' '.join(arguments[:3]) + '; raw output withheld')
    return response.stdout


def aws(env: dict, service: str, operation: str, *options: str) -> dict:
    return json.loads(command(['aws', '--region', 'us-east-1', '--output', 'json', service, operation, *options], env=env) or b'{}')


def check_kubernetes_authority(env: dict, verb: str, resource: str) -> None:
    arguments = ['kubectl', '--request-timeout=20s', 'auth', 'can-i', verb,
                 resource, '-n', 'observability']
    response = subprocess.run(arguments, env=env, capture_output=True, timeout=90)
    if response.returncode == 0 and response.stdout.strip() == b'yes':
        return
    error = response.stderr.decode(errors='replace')
    if 'Unauthorized' in error or 'must be logged in' in error:
        raise RuntimeError('EKS authentication failed for the explicit temporary bootstrap identity; '
                           'the ambient kubeconfig/profile is not used. Verify the IAM principal EKS mapping')
    if response.stdout.strip() == b'no' or 'Forbidden' in error:
        raise RuntimeError(f'Kubernetes permission required: {verb} {resource} in observability')
    raise RuntimeError(f'Kubernetes authorization check failed: {verb} {resource} in observability; '
                       'check API connectivity and the exec credential provider; raw output withheld')


def gh_json(*arguments: str) -> object:
    return json.loads(command(['gh', *arguments]))


def verify_checkout(path: Path, repository: str) -> str:
    if command(['git', '-C', str(path), 'status', '--porcelain']).strip():
        raise ValueError('Refusing an uncommitted checkout')
    head = command(['git', '-C', str(path), 'rev-parse', 'HEAD']).decode().strip()
    remote = gh_json('api', f'repos/{repository}/commits/main')['sha']
    if head != remote:
        raise ValueError('Checkout must be the reviewed current main: ' + repository)
    return head


def json_documents(payload: bytes) -> list[dict]:
    decoder, text, objects = json.JSONDecoder(), payload.decode(), []
    while text.strip():
        obj, end = decoder.raw_decode(text.lstrip())
        text = text.lstrip()[end:]
        objects.extend(obj['items'] if obj.get('kind') == 'List' else [obj])
    return objects


def official_run(repository: str, workflow: str, inputs: dict, request_id: str) -> dict:
    command(['gh', 'workflow', 'run', workflow, '--repo', repository, '--ref', 'main',
             *[argument for key, value in {**inputs, 'request_id': request_id}.items() for argument in ('-f', key + '=' + value)]])
    run_id = None
    for attempt in range(360):
        if run_id is None:
            runs = gh_json('run', 'list', '--repo', repository, '--workflow', workflow, '--branch', 'main', '--event', 'workflow_dispatch',
                           '--limit', '30', '--json', 'databaseId,displayTitle')
            matching = [r for r in runs if request_id in r['displayTitle']]
            if len(matching) > 1:
                raise ValueError('Ambiguous workflow correlation')
            run_id = matching[0]['databaseId'] if matching else None
        if run_id:
            status = gh_json('run', 'view', str(run_id), '--repo', repository, '--json', 'status,conclusion,url')
            if status['status'] == 'completed':
                print(json.dumps({'pipeline': workflow, **status}), flush=True)
                if status['conclusion'] != 'success':
                    raise RuntimeError('Official pipeline failed; no denied-action retry: ' + status['url'])
                return status
        if attempt % 12 == 0:
            print(json.dumps({'pipeline': workflow, 'status': 'waiting', 'run_id': run_id}), flush=True)
        time.sleep(5)
    raise RuntimeError('Official workflow exceeded its bounded wait')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='serverless-lab')
    parser.add_argument('--rbac-checkout', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--credential-process', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--source-config', default=str(Path.home() / '.aws/config'), help=argparse.SUPPRESS)
    parser.add_argument('--source-credentials', default=str(Path.home() / '.aws/credentials'), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', args.profile):
        raise ValueError('Invalid profile name')
    credentials = export_session(args.profile, args.source_config, args.source_credentials)
    if args.credential_process:
        if sys.stdout.isatty():
            raise ValueError('Credential process output is only for the SDK private pipe')
        sys.stdout.write(json.dumps(credentials))
        return
    if args.rbac_checkout is None:
        raise ValueError('--rbac-checkout must reference the canonical reviewed repository')
    rbac = args.rbac_checkout.resolve()
    revisions = {'iac': verify_checkout(ROOT, IAC), 'rbac': verify_checkout(rbac, RBAC)}
    expected = re.search(r'aws-serverless-lab-([0-9]{12})-tfstate', (ROOT / 'terraform/environments/dev/versions.tf').read_text())[1]
    with tempfile.TemporaryDirectory(prefix='lab-v2-admin-') as directory:
        work = Path(directory)
        os.chmod(work, 0o700)
        # Metadata-only SDK profile. No access key/token is written to this file.
        provider_config = work / 'config'
        process = shlex.join([sys.executable, str(Path(__file__).resolve()), '--credential-process', '--profile', args.profile,
                              '--source-config', args.source_config, '--source-credentials', args.source_credentials])
        provider_config.write_text('[profile lab-v2-admin-session]\nregion = us-east-1\ncredential_process = ' + process + '\n')
        provider_config.chmod(0o600)
        env = {k: v for k, v in os.environ.items() if k not in {'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN', 'AWS_DEFAULT_PROFILE', 'TF_LOG', 'TF_LOG_PATH'}}
        env.update(AWS_CONFIG_FILE=str(provider_config), AWS_SHARED_CREDENTIALS_FILE='/dev/null', AWS_PROFILE='lab-v2-admin-session', AWS_SDK_LOAD_CONFIG='1', AWS_MAX_ATTEMPTS='1', AWS_RETRY_MODE='standard')
        caller = aws(env, 'sts', 'get-caller-identity')
        if caller['Account'] != expected:
            raise ValueError('Wrong AWS account; no bootstrap actions permitted')
        # A new administrative session must be able to read the existing role.
        # One failure stops here; no use of a previously denied maintenance role.
        aws(env, 'iam', 'get-role', '--role-name', 'cosmotech-lab-maintenance')
        cluster = aws(env, 'eks', 'describe-cluster', '--name', 'aws-eks-istio-lab-dev')['cluster']
        kubeconfig = work / 'kubeconfig'
        kubeconfig.write_text(json.dumps({'apiVersion':'v1','kind':'Config','clusters':[{'name':'lab','cluster':{'server':cluster['endpoint'],'certificate-authority-data':cluster['certificateAuthority']['data']}}],
            'users':[{'name':'administrator','user':{'exec':{'apiVersion':'client.authentication.k8s.io/v1beta1','command':'aws','args':['eks','get-token','--region','us-east-1','--cluster-name',cluster['name']]}}}],
            'contexts':[{'name':'lab','context':{'cluster':'lab','user':'administrator'}}],'current-context':'lab'}))
        kubeconfig.chmod(0o600)
        env['KUBECONFIG'] = str(kubeconfig)
        for resource in ['roles.rbac.authorization.k8s.io', 'rolebindings.rbac.authorization.k8s.io']:
            for verb in ['get', 'create', 'patch', 'update']:
                check_kubernetes_authority(env, verb, resource)
        manifests = []
        for filename in ['lab-v2-observability-reader.yaml', 'lab-v2-bootstrap-authority.json']:
            data = command(['kubectl','--request-timeout=20s','apply','--dry-run=client','-f',str(rbac/'ops/kubernetes'/filename),'-o','json'],env=env)
            manifests.extend(json_documents(data))
        validate_rbac(manifests)
        instance = gh_json('api', f'repos/{IAC}/actions/variables/LAB_V2_ITSM_INSTANCE_ID')['value']
        if not re.fullmatch(r'i-[a-f0-9]{17}', instance):
            raise ValueError('Invalid existing ITSM target')
        target = aws(env, 'ec2', 'describe-instances', '--instance-ids', instance)
        instances = [i for reservation in target.get('Reservations', []) for i in reservation['Instances']]
        if len(instances) != 1 or {t['Key']: t['Value'] for t in instances[0].get('Tags', [])}.get('Name') != 'cosmotech-itsm-dev' or instances[0]['State']['Name'] != 'running':
            raise ValueError('ITSM variable does not match the reviewed live target')
        stack = ROOT / 'terraform/bootstrap/lab_v2_admin' 
        command(['terraform','-chdir='+str(stack),'init','-input=false','-backend-config=bucket=aws-serverless-lab-'+expected+'-tfstate-use1'],env=env)
        outputs = json.loads(command(['terraform','-chdir='+str(stack),'output','-json'],env=env))
        expiry = authorization_deadline(outputs.get('authorization_expires_at',{}).get('value'),dt.datetime.now(dt.timezone.utc))
        plan, plan_json = work/'admin.plan', work/'admin.json'
        command(['terraform','-chdir='+str(stack),'plan','-input=false','-refresh=true','-out='+str(plan),
                 '-var=expected_account_id='+expected,'-var=itsm_instance_id='+instance,'-var=authorization_expires_at='+expiry],env=env)
        raw = command(['terraform','-chdir='+str(stack),'show','-json',str(plan)],env=env)
        changes = check_admin_plan(json.loads(raw))
        print(json.dumps({'phase':'administrative-plan','changes':changes,'authorization_expires_at':expiry,'revisions':revisions,'apply_requested':args.apply}),flush=True)
        if not args.apply:
            return
        validate_temporary(export_session(args.profile, args.source_config, args.source_credentials), dt.datetime.now(dt.timezone.utc))
        command(['terraform','-chdir='+str(stack),'apply','-input=false',str(plan)],env=env,timeout=600)
        command(['kubectl','--request-timeout=20s','apply','--dry-run=server','-f','-'],env=env,value={'apiVersion':'v1','kind':'List','items':manifests})
        command(['kubectl','--request-timeout=20s','apply','-f','-'],env=env,value={'apiVersion':'v1','kind':'List','items':manifests})
        output = json.loads(command(['terraform','-chdir='+str(stack),'output','-json'],env=env))
        values = {
          (IAC,'LAB_V2_BOOTSTRAP_ROLE_ARN'):output['bootstrap_role_arn']['value'],
          (RBAC,'LAB_V2_RBAC_BOOTSTRAP_ROLE_ARN'):output['rbac_role_arn']['value'],
          (IAC,'LAB_V2_BOOTSTRAP_EXPIRES_AT'):expiry,
          (IAC,'IAC_EVIDENCE_ROLE_ARN'):f'arn:aws:iam::{expected}:role/cosmotech-iac-evidence',
          (IAC,'LAB_V2_DEPLOY_ROLE_ARN'):f'arn:aws:iam::{expected}:role/cosmotech-lab-v2-deploy'}
        for (repository,name),value in values.items():
            command(['gh','variable','set',name,'--repo',repository,'--body',value])
        request = 'lab-v2-admin-' + uuid.uuid4().hex[:16]
        official_run(RBAC,'lab-v2-rbac.yml',{},request+'-rbac')
        official_run(IAC,'lab-v2-publication.yml',{'phase':'bootstrap-apply'},request+'-publish')
        public = json.loads(aws(env,'ssm','get-parameter','--name','/cosmotech/lab-v2/evidence-public-key')['Parameter']['Value'])['public_key']
        if not public.startswith('-----BEGIN PGP PUBLIC KEY BLOCK-----') or 'PRIVATE KEY' in public:
            raise ValueError('Only an approved public recipient can be configured')
        command(['gh','variable','set','IAC_EVIDENCE_GPG_PUBLIC_KEY','--repo',IAC,'--body',public])
        official_run(IAC,'iac-evidence.yml',{},request+'-evidence')
        print(json.dumps({'status':'official_pipelines_passed','portal':'https://kestra.cosmotechsolutions.com.br/iac/','grafana':'https://grafana.cosmotechsolutions.com.br/d/cosmotech-v2-sqs-workers','secret_values_printed':False}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status':'blocked','error':str(error)}),file=sys.stderr)
        raise SystemExit(1)
