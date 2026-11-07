"""Reject any destroy/replacement, any foreign resource, any network change."""
import json
import sys
ALLOWED = {'aws_dynamodb_table': {'control'}, 'aws_cloudwatch_log_group': {'api', 'worker'},
           'aws_lambda_function': {'api','worker'}, 'aws_apigatewayv2_api': {'power'},
           'aws_apigatewayv2_integration': {'api'}, 'aws_apigatewayv2_route': {'api','detail'},
           'aws_apigatewayv2_stage': {'api'}, 'aws_lambda_permission': {'api'},
           'aws_s3_bucket': {'snapshots'}, 'aws_s3_bucket_versioning': {'snapshots'},
           'aws_s3_bucket_server_side_encryption_configuration': {'snapshots'},
           'aws_s3_bucket_public_access_block': {'snapshots'}, 'aws_s3_bucket_policy': {'snapshots'},
           'aws_sfn_state_machine': {'operations'}, 'aws_ssm_document': {'agent','host'}}

def check(value):
    changes = []
    for r in value.get('resource_changes', []):
        if r.get('mode') == 'data':
            continue
        ops = r['change']['actions']
        if ops == ['no-op']:
            continue
        if r['type'] not in ALLOWED or r['name'] not in ALLOWED[r['type']] or 'delete' in ops:
            raise ValueError('Out-of-scope or destructive change: ' + r['address'])
        changes.append({'address': r['address'], 'actions': ops})
    return changes

def main():
    with open(sys.argv[1]) as stream:
        print(json.dumps({'approved_scope': check(json.load(stream))}))

if __name__ == '__main__':
    main()
