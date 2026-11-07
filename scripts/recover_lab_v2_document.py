"""Clear only a post-create read failure after verifying the fixed private document."""
import json
import subprocess

STACK = 'terraform/environments/dev'
ADDRESS = 'aws_ssm_document.lab_v2_publication'
NAME = 'CosmoTech-Publish-Lab-V2'


def verify(attributes, document, permissions):
    if (attributes.get('id') != NAME or attributes.get('name') != NAME
            or attributes.get('document_type') != 'Command'
            or document.get('Name') != NAME or document.get('DocumentType') != 'Command'
            or document.get('Status') != 'Active'
            or permissions.get('AccountIds') or permissions.get('AccountSharingInfoList')):
        raise ValueError('Fixed document identity, readiness or private ownership differs')
    if json.loads(attributes['content']) != json.loads(document['Content']):
        raise ValueError('Live document differs from the content recorded at creation')


def run(args):
    result = subprocess.run(args, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError('Document recovery verification failed; private output withheld')
    return result.stdout


def main():
    state = json.loads(run(['terraform', '-chdir='+STACK, 'state', 'pull']))
    matches = [i for r in state.get('resources', [])
               if not r.get('module') and r['type'] == 'aws_ssm_document' and r['name'] == 'lab_v2_publication'
               for i in r.get('instances', []) if i.get('status') == 'tainted']
    if not matches:
        return
    if len(matches) != 1:
        raise ValueError('Ambiguous fixed document state')
    prefix = ['aws', '--region', 'us-east-1', '--output', 'json', 'ssm']
    document = json.loads(run(prefix + ['get-document', '--name', NAME, '--document-version', '$LATEST']))
    permissions = json.loads(run(prefix + ['describe-document-permission', '--name', NAME, '--permission-type', 'Share']))
    verify(matches[0]['attributes'], document, permissions)
    run(['terraform', '-chdir='+STACK, 'untaint', '-lock-timeout=30s', ADDRESS])
    print(json.dumps({'recovered': ADDRESS, 'live_content_matches_created_state': True,
                      'document_active_and_private': True, 'infrastructure_changes': False}))


if __name__ == '__main__':
    main()
