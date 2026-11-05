"""Provision a read-only Command Center identity and publish credentials only to Secrets Manager."""
from __future__ import annotations

import json
import secrets
import subprocess
import time

from configure_monitoring import Zabbix

SECRET = 'cosmotech/grafana/command-center'
USERNAME = 'svc_grafana_command_center'
API_URL = 'https://zabbix.cosmotechsolutions.com.br/api_jsonrpc.php'


def aws_secret(operation: str, value: dict | None = None):
    args = ['aws', '--region', 'us-east-1', 'secretsmanager', operation, '--secret-id', SECRET, '--output', 'json']
    if value is not None:
        args += ['--secret-string', 'file:///dev/stdin']
    result = subprocess.run(args, input=json.dumps(value) if value is not None else None,
                            capture_output=True, text=True, timeout=40, check=False)
    if result.returncode:
        if operation == 'get-secret-value' and 'ResourceNotFoundException' in result.stderr:
            return None
        raise RuntimeError('Command Center secret operation failed; sensitive diagnostics omitted')
    return json.loads(result.stdout)


def main() -> None:
    z = Zabbix()
    try:
        prior = aws_secret('get-secret-value')
        previous = json.loads(prior['SecretString']) if prior else {}
        password = previous.get('password') or secrets.token_urlsafe(36)
        z.protected.append(password)
        rules = {'ui.default_access': 0, 'ui': [{'name': 'monitoring.problems', 'status': 1}],
                 'actions.default_access': 0, 'modules.default_access': 0,
                 'api.access': 1, 'api.mode': 1,
                 'api': ['problem.get', 'trigger.get']}
        found = z.api('role.get', {'filter': {'name': 'CosmoTech Command Center read only'}, 'output': ['roleid']})
        if found:
            role = found[0]['roleid'];z.api('role.update', {'roleid': role, 'rules': rules})
        else:
            role = z.api('role.create', {'name': 'CosmoTech Command Center read only', 'type': 1, 'rules': rules})['roleids'][0]
        groups = z.api('hostgroup.get', {'search': {'name': 'CosmoTech /'}, 'output': ['groupid']})
        rights = [{'id': g['groupid'], 'permission': 2} for g in groups]
        found = z.api('usergroup.get', {'filter': {'name': 'CosmoTech Command Center readers'}, 'output': ['usrgrpid']})
        if found:
            group = found[0]['usrgrpid'];z.api('usergroup.update', {'usrgrpid': group, 'hostgroup_rights': rights})
        else:
            group = z.api('usergroup.create', {'name': 'CosmoTech Command Center readers', 'gui_access': 1,
                                              'hostgroup_rights': rights})['usrgrpids'][0]
        found = z.api('user.get', {'filter': {'username': USERNAME}, 'output': ['userid']})
        user = {'roleid': role, 'usrgrps': [{'usrgrpid': group}]}
        if found:
            userid = found[0]['userid']
            if not previous.get('password'):
                user['passwd'] = password
            z.api('user.update', {'userid': userid, **user})
        else:
            userid = z.api('user.create', {'username': USERNAME, 'passwd': password, **user})['userids'][0]
        if previous.get('api_token') and previous.get('token_expires_at', 0) > time.time()+30*86400:
            token, expiry = previous['api_token'], previous['token_expires_at']
        else:
            expiry = int(time.time())+180*86400
            found = z.api('token.get', {'userids': [userid], 'filter': {'name': 'Grafana Command Center'}, 'output': ['tokenid']})
            if found:
                tokenid = found[0]['tokenid'];z.api('token.update', {'tokenid': tokenid, 'expires_at': expiry})
            else:
                tokenid = z.api('token.create', {'name': 'Grafana Command Center', 'userid': userid,
                                                'expires_at': expiry})['tokenids'][0]
            token = z.api('token.generate', [tokenid])[0]['token']
        z.protected.append(token)
        aws_secret('put-secret-value', {'username': USERNAME, 'password': password, 'api_token': token,
                    'api_url': API_URL, 'token_expires_at': expiry, 'role': 'read-only',
                    'allowed_methods': ['problem.get', 'trigger.get']})
        print(json.dumps({'username': USERNAME, 'permissions': 'read-only', 'secret': SECRET,
                          'token_expires_at': expiry, 'host_groups': len(groups)}))
    finally:
        z.api('user.logout', [])


if __name__ == '__main__':
    main()
