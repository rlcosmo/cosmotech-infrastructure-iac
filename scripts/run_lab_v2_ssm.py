"""Invoke only the fixed Terraform-managed publisher and print safe status."""
import argparse
import json
import re
import subprocess
import time

parser = argparse.ArgumentParser()
parser.add_argument('--instance-id', required=True)
parser.add_argument('--document-version', required=True)
parser.add_argument('--document-sha256', required=True)
parser.add_argument('--mode', choices=('install', 'validate'), required=True)
args = parser.parse_args()
if not re.fullmatch(r'[1-9][0-9]*', args.document_version) or not re.fullmatch(r'[a-fA-F0-9]{64}', args.document_sha256):
    raise SystemExit('An immutable numeric document version and SHA256 are required')


def aws(*arguments):
    response = subprocess.run(['aws', '--region', 'us-east-1', '--output', 'json', *arguments], capture_output=True, timeout=40)
    if response.returncode:
        raise RuntimeError('SSM request failed; response withheld')
    return json.loads(response.stdout)


command = aws('ssm', 'send-command', '--document-name', 'CosmoTech-Publish-Lab-V2',
              '--document-version', args.document_version, '--document-hash', args.document_sha256, '--document-hash-type', 'Sha256', '--instance-ids', args.instance_id,
              '--parameters', json.dumps({'Mode': [args.mode]}))['Command']['CommandId']
print(json.dumps({'command_id': command, 'mode': args.mode}), flush=True)
for _ in range(130):
    try:
        response = aws('ssm', 'get-command-invocation', '--command-id', command, '--instance-id', args.instance_id)
    except RuntimeError:
        time.sleep(5)
        continue
    if response['Status'] == 'Success':
        # Runtime emits only explicitly allowlisted evidence, never raw command logs.
        print(json.dumps({'command_id': command, 'mode': args.mode, 'status': 'Success'}))
        break
    if response['Status'] in ('Failed', 'Cancelled', 'TimedOut', 'Cancelling'):
        raise SystemExit('Fixed publisher failed; inspect only its sanitized runtime evidence')
    time.sleep(5)
else:
    raise SystemExit('SSM publisher did not complete within its bounded execution window')
