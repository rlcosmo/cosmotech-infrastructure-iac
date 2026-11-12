#!/usr/bin/env python3
"""Compare actual Lab table records; save only private row hashes, never payloads."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.disaster_recovery.table_integrity import TABLES, collect, compare


def main() -> None:
    import boto3
    from botocore.config import Config
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-profile', default='cosmotech-lab-operator')
    parser.add_argument('--destination-profile', default='cosmotech-dr-provisioner')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    snapshots = []
    config = Config(connect_timeout=5, read_timeout=20,
                    retries={'mode': 'standard', 'total_max_attempts': 2})
    for profile, region in [(args.source_profile, 'us-east-1'), (args.destination_profile, 'us-east-2')]:
        session = boto3.Session(profile_name=profile, region_name=region)
        identity = session.client('sts', config=config).get_caller_identity()
        if identity['Account'] != '414053644145' or ':assumed-role/cosmotech-' not in identity['Arn']:
            raise ValueError('Authorized temporary Lab role required')
        client = session.resource('dynamodb', config=config).meta.client
        snapshots.append(collect(client, TABLES[region]))
    result = compare(*snapshots)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump({'comparison': result, 'snapshots': snapshots}, output, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
