#!/usr/bin/env bash
# Only loopback synthetic endpoints and the dedicated v2010 lab-agent container.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$root/ops/zabbix/v2010/fault_lab.py" "$@"
