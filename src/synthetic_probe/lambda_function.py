"""Public-entry synthetic checks executed outside the application VPC every five minutes."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

TARGETS = {app: f'https://{app}-dev.cosmotechsolutions.com.br/diagnostics/ping'
           for app in ('orders-api', 'orders-worker')}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def handler(event: dict, context: Any) -> dict:
    """Probe only fixed read-only routes; log status/latency and trace correlation."""
    results = []
    client = urllib.request.build_opener(NoRedirect())
    for service, url in TARGETS.items():
        trace_id, parent_id = uuid.uuid4().hex, uuid.uuid4().hex[:16]
        request = urllib.request.Request(url, headers={
            'User-Agent': 'CosmoTechExternalProbe/1.0', 'X-Synthetic-Source': 'external-probe',
            'traceparent': f'00-{trace_id}-{parent_id}-01'})
        start = time.monotonic()
        status, error_type = 0, ''
        try:
            with client.open(request, timeout=5) as response:
                status = response.status
                response.read(4096)
        except urllib.error.HTTPError as error:
            status, error_type = error.code, type(error).__name__
        except (OSError, ValueError) as error:
            error_type = type(error).__name__
        value = {'event': 'synthetic_external_inbound', 'service': service, 'synthetic': True,
            'direction': 'public_ingress', 'status_code': status, 'trace_id': trace_id,
            'duration_ms': round((time.monotonic()-start)*1000, 2), 'interval_seconds': 300}
        if error_type:
            value['error_type'] = error_type
        print(json.dumps(value))
        results.append(value)
    if any(value['status_code'] != 200 for value in results):
        raise RuntimeError('One or more public application probes failed; see structured status logs')
    return {'synthetic': True, 'successful_checks': len(results)}
