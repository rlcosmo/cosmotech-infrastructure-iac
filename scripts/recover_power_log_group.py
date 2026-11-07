"""Recover only our verified log group after provider post-create read failure."""
import json
import subprocess

ADDRESS = 'aws_cloudwatch_log_group.api'
NAME = '/aws/lambda/cosmotech-power-api'
ARN = 'arn:aws:logs:us-east-1:414053644145:log-group:' + NAME


def read(*args):
    return json.loads(subprocess.check_output(args))


def main():
    state = read('terraform', '-chdir=power_manager/terraform', 'state', 'pull')
    for r in state.get('resources', []):
        if r.get('mode') != 'managed' or r['type'] not in {'aws_cloudwatch_log_group','aws_lambda_function'} or r['name'] != 'api' or r.get('module'):
            continue
        for instance in r.get('instances', []):
            if instance.get('status') != 'tainted':
                continue
            attrs = instance['attributes']
            if r['type'] == 'aws_lambda_function':
                if attrs.get('id') != 'cosmotech-power-api' or attrs.get('function_name') != 'cosmotech-power-api':
                    raise ValueError('Refuse recovery: unexpected Lambda')
                function = read('aws', '--region', 'us-east-1', 'lambda', 'get-function', '--function-name', 'cosmotech-power-api')
                config = function['Configuration']
                if config['Role'] != 'arn:aws:iam::414053644145:role/cosmotech-power-runtime' or config['Runtime'] != 'python3.12' or function.get('Tags',{}).get('Project') != 'cosmotech-power-manager':
                    raise ValueError('Refuse recovery: unexpected function ownership or execution role')
                subprocess.run(['terraform', '-chdir=power_manager/terraform', 'untaint', 'aws_lambda_function.api'], check=True)
                print('Recovered verified Power Manager Lambda state; no function deleted or recreated')
                continue
            if attrs.get('id') != NAME or attrs.get('name') != NAME:
                raise ValueError('Refuse recovery: unexpected log group')
            groups = read('aws', '--region', 'us-east-1', 'logs', 'describe-log-groups', '--log-group-name-prefix', NAME)['logGroups']
            found = [g for g in groups if g['logGroupName'] == NAME]
            tags = read('aws', '--region', 'us-east-1', 'logs', 'list-tags-for-resource', '--resource-arn', ARN)['tags']
            if len(found) != 1 or tags.get('Project') != 'cosmotech-power-manager' or tags.get('ManagedBy') != 'terraform':
                raise ValueError('Refuse recovery: ownership evidence absent')
            subprocess.run(['terraform', '-chdir=power_manager/terraform', 'untaint', ADDRESS], check=True)
            print('Recovered verified Power Manager log group state; no AWS resource deleted or recreated')

if __name__ == '__main__':
    main()
