#!/usr/bin/env python3
"""Send a harmless OTLP marker and verify actual Tempo/Loki ingestion."""
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request


def request(url, data=None):
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as response:
        raw = response.read()
        return response.status, json.loads(raw) if raw else {}


def main():
    now = time.time_ns()
    trace_id, span_id = secrets.token_hex(16), secrets.token_hex(8)
    marker = "cosmotech-dr-otlp-" + secrets.token_hex(6)
    resource = {"attributes": [{"key": "service.name", "value": {"stringValue": "cosmotech-dr-validation"}}]}
    span = {"traceId": trace_id, "spanId": span_id, "name": marker, "kind": 1,
            "startTimeUnixNano": str(now), "endTimeUnixNano": str(now + 1000000), "status": {"code": 1}}
    record = {"timeUnixNano": str(now), "observedTimeUnixNano": str(now), "severityNumber": 9,
              "severityText": "INFO", "body": {"stringValue": marker}, "traceId": trace_id, "spanId": span_id}
    payloads = {
        "traces": {"resourceSpans": [{"resource": resource, "scopeSpans": [{"scope": {"name": "dr-live-validation"}, "spans": [span]}]}]},
        "logs": {"resourceLogs": [{"resource": resource, "scopeLogs": [{"scope": {"name": "dr-live-validation"}, "logRecords": [record]}]}]},
    }
    result = {"trace_id": trace_id, "marker": marker, "sent_at_ns": now}
    for signal, payload in payloads.items():
        code, response = request("http://otel-collector-opentelemetry-collector.observability.svc:4318/v1/" + signal, payload)
        if response.get("partialSuccess"):
            raise RuntimeError("OTLP receiver reported partial success: " + signal)
        result["otlp_" + signal + "_http"] = code
    query = urllib.parse.urlencode({"query": '{service_name="cosmotech-dr-validation"} |= "' + marker + '"', "start": str(now - 1000000000), "limit": "20"})
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            _, trace = request("http://tempo.observability.svc:3200/api/traces/" + trace_id)
            _, logs = request("http://loki.observability.svc:3100/loki/api/v1/query_range?" + query)
            if marker in json.dumps(trace) and marker in json.dumps(logs):
                result.update(tempo_marker_found=True, loki_marker_found=True, verified_at_ns=time.time_ns())
                print(json.dumps(result, sort_keys=True))
                return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(3)
    raise RuntimeError("Real OTLP marker could not be read from both Tempo and Loki")


if __name__ == "__main__":
    main()
