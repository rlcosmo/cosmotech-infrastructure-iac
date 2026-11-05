"""Publish owner-supplied JSON on stdin to catalogued Secrets Manager names without logging values."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[1] / 'ops/credential-catalog/catalog.json'


def aws(operation: str, *args: str, value: dict | None = None) -> dict:
    command = ['aws', '--region', 'us-east-1', 'secretsmanager', operation, *args, '--output', 'json']
    if value is not None:
        command += ['--secret-string', 'file:///dev/stdin']
    result = subprocess.run(command, input=json.dumps(value) if value is not None else None,
                            capture_output=True, text=True, check=False)
    if result.returncode:
        if operation == 'get-secret-value' and 'ResourceNotFoundException' in result.stderr:
            return {}
        raise RuntimeError(f'Secrets Manager {operation} failed; values omitted from diagnostics')
    return json.loads(result.stdout)


def publish(values: dict, catalog: dict, call: Callable = aws) -> list[dict]:
    if not isinstance(values, dict) or not set(values) <= set(catalog.values()):
        raise ValueError('Only explicitly catalogued secret names are accepted')
    if any(not isinstance(value, dict) or not value for value in values.values()):
        raise ValueError('Each secret requires a nonempty JSON object')
    results = []
    for name, value in sorted(values.items()):
        current = call('get-secret-value', '--secret-id', name)
        if current and json.loads(current['SecretString']) == value:
            results.append({'secret_id': name, 'status': 'unchanged'})
            continue
        result = call('put-secret-value', '--secret-id', name, value=value)
        results.append({'secret_id': name, 'status': 'stored', 'version_id': result['VersionId']})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=CATALOG)
    args = parser.parse_args()
    values = json.load(sys.stdin)
    result = publish(values, json.loads(args.catalog.read_text()))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
