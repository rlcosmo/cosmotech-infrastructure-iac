"""Read-only Lab table comparison; never persist order payloads or claim fencing."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json

TABLES = {
    'us-east-1': 'aws-eks-istio-lab-dev-orders-data',
    'us-east-2': 'cosmotech-dr-orders-restored',
}


def canonical(value: object) -> object:
    """Normalize native DynamoDB values without floats or unstable set ordering."""
    if isinstance(value, Decimal):
        return {'number': str(value.normalize())}
    if isinstance(value, bytes) or type(value).__name__ == 'Binary':
        return {'binary': base64.b64encode(bytes(value)).decode('ascii')}
    if isinstance(value, set):
        return {'set': sorted((canonical(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))}
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items()}
    if isinstance(value, list):
        return [canonical(v) for v in value]
    return value


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def collect(client, table: str) -> dict:
    """Client comes from a DynamoDB resource so items use native Python values."""
    region = client.meta.region_name
    if TABLES.get(region) != table:
        raise ValueError('Only the explicit Lab source and restored Ohio tables')
    started = datetime.now(timezone.utc).isoformat()
    rows = {}
    for page in client.get_paginator('scan').paginate(TableName=table, ConsistentRead=True):
        for item in page.get('Items', []):
            key = item.get('order_id')
            if not isinstance(key, str) or not key:
                raise ValueError('Unexpected primary key; do not omit unverifiable records')
            key_hash, row_hash = digest(key), digest(item)
            if key_hash in rows and rows[key_hash] != row_hash:
                raise ValueError('Record changed during scan; obtain a fenced snapshot')
            rows[key_hash] = row_hash
    return dict(region=region, table=table, started_at=started,
                finished_at=datetime.now(timezone.utc).isoformat(), rows=rows,
                count=len(rows), aggregate_sha256=digest(rows))


def compare(source: dict, destination: dict) -> dict:
    if source['region'] != 'us-east-1' or destination['region'] != 'us-east-2':
        raise ValueError('Wrong comparison direction')
    left, right = source['rows'], destination['rows']
    missing = len(left.keys() - right.keys())
    extra = len(right.keys() - left.keys())
    changed = sum(left[k] != right[k] for k in left.keys() & right.keys())
    return dict(equal=not (missing or extra or changed), source_count=len(left),
                destination_count=len(right), missing=missing, extra=extra, changed=changed,
                source_sha256=source['aggregate_sha256'], destination_sha256=destination['aggregate_sha256'],
                limitation='Consistent reads are not a table-wide snapshot or a writer fence; not platform RPO proof')
