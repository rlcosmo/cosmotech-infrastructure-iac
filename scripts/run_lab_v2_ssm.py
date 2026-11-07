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
    if response['Status'] in ('Success', 'Failed', 'Cancelled', 'TimedOut', 'Cancelling'):
        for line in response.get('StandardOutputContent', '').splitlines():
            try:
                evidence = json.loads(line)
            except ValueError:
                continue
            if not isinstance(evidence, dict):
                continue
            safe = {}
            if evidence.get('stage') in ['identity','prerequisites','evidence-key','portal-auth','nginx','monitoring','grafana','validate-portal','validate-grafana','validate-metrics']:
                safe['stage'] = evidence['stage']
            identifiers = evidence.get('failure_identifiers', {})
            if isinstance(identifiers, dict):
                safe['failure_identifiers'] = {key: value for key, value in identifiers.items()
                    if key in ['binary','aws_error_code','aws_operation','aws_service','cli_error','reason','metric'] and isinstance(value, str)
                    and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value)}
                if type(identifiers.get('http_status')) is int and 100 <= identifiers['http_status'] <= 599:
                    safe['failure_identifiers']['http_status'] = identifiers['http_status']
                if type(identifiers.get('queue_data_max_age_seconds')) is int:
                    safe['failure_identifiers']['queue_data_max_age_seconds'] = identifiers['queue_data_max_age_seconds']
            for name in ['portal_authenticated', 'grafana_authenticated']:
                if type(evidence.get(name)) is bool:
                    safe[name] = evidence[name]
            metrics = evidence.get('live_metric_series', {})
            if isinstance(metrics, dict):
                safe['live_metric_series'] = {key: value for key, value in metrics.items()
                    if key in ['cosmotech_sqs_visible','cosmotech_sqs_inflight','cosmotech_sqs_oldest_age_seconds',
                               'cosmotech_sqs_dlq_visible','cosmotech_worker_processing_seconds_count']
                    and type(value) is int and value >= 0}
            alerts = evidence.get('live_alert_rules', {})
            if isinstance(alerts, dict):
                safe['live_alert_rules'] = {key: value for key, value in alerts.items()
                    if key in ['expected','loaded','firing','unhealthy'] and type(value) is int and value >= 0}
            for field, names in {'runtime_tools': ['aws','gpg','nginx','systemctl','docker'],
                                 'prerequisite_paths': ['host_proxy','monitoring_directory','docker_proxy']}.items():
                value = evidence.get(field, {})
                if isinstance(value, dict):
                    safe[field] = {name: value[name] for name in names if type(value.get(name)) is bool}
            if safe:
                print(json.dumps(safe), flush=True)
        if response['Status'] == 'Success':
            print(json.dumps({'command_id': command, 'mode': args.mode, 'status': 'Success'}))
            break
        raise SystemExit('Fixed publisher failed; inspect only its sanitized runtime evidence')
    time.sleep(5)
else:
    raise SystemExit('SSM publisher did not complete within its bounded execution window')
