"""Reject any destroy/replacement, any foreign resource, any network change."""
import json
import sys
ALLOWED = {'aws_dynamodb_table': {'control'}, 'aws_cloudwatch_log_group': {'api'},
           'aws_lambda_function': {'api'}, 'aws_apigatewayv2_api': {'power'},
           'aws_apigatewayv2_integration': {'api'}, 'aws_apigatewayv2_route': {'api'},
           'aws_apigatewayv2_stage': {'api'}, 'aws_lambda_permission': {'api'}}

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
