"""Secure the new Zabbix installation and register its APM proxy without logging credentials."""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from pathlib import Path

import bcrypt


def sql(statement: str) -> str:
    return subprocess.check_output(['docker', 'compose', 'exec', '-T', 'postgres', 'psql', '-U', 'zabbix', '-d', 'zabbix', '-At', '-v', 'ON_ERROR_STOP=1'], input=statement, text=True)


def main() -> None:
    credentials = json.loads(Path('/opt/cosmotech/zabbix/secrets/credentials.json').read_text())
    password_hash = bcrypt.hashpw(credentials['password'].encode(), bcrypt.gensalt(rounds=12)).decode()
    sql("UPDATE users SET passwd='" + password_hash + "' WHERE username='Admin';\n")
    sql("UPDATE usrgrp SET users_status=1 WHERE name='Guests';\n")
    sql("UPDATE role_rule SET value_int=1 WHERE name='api.access' AND roleid IN (SELECT roleid FROM role WHERE type=3);\n")
    # Only this installation's bootstrap administrator uses API configuration.
    auth = None

    def api(method: str, params: dict | list, authenticated: bool = True):
        headers = {'Content-Type': 'application/json-rpc'}
        if auth and authenticated:
            headers['Authorization'] = 'Bearer ' + auth
        request = urllib.request.Request('http://127.0.0.1:8088/api_jsonrpc.php', data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=10) as response:
            value = json.load(response)
        if 'error' in value:
            raise RuntimeError(method + ': ' + json.dumps(value['error']))
        return value['result']

    for attempt in range(60):
        try:
            auth = api('user.login', {'username': credentials['username'], 'password': credentials['password']}, False)
            break
        except (OSError, ValueError, RuntimeError):
            if attempt == 59:
                raise
            time.sleep(5)
    version = api('apiinfo.version', {}, False)
    if not version.startswith('8.0'):
        raise RuntimeError('Expected Zabbix 8.0 pre-release')
    proxies = api('proxy.get', {'output': ['proxyid', 'name'], 'filter': {'name': 'aws-eks-istio-lab-dev-apm'}})
    if not proxies:
        api('proxy.create', {'name': 'aws-eks-istio-lab-dev-apm', 'operating_mode': 0})
    api('user.logout', [])
    print(json.dumps({'api_version': version, 'admin_password_rotated': True, 'guest_disabled': True, 'apm_proxy': 'aws-eks-istio-lab-dev-apm'}))


if __name__ == '__main__':
    main()
