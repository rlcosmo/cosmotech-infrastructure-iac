"""Project sensitive Terraform JSON into a minimal portal report.

Raw state/plan must remain private. Inventory must include explicit coverage;
missing inventory is never interpreted as an empty AWS account.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def resources(module: dict[str, Any]) -> list[dict[str, Any]]:
    result = list(module.get("resources", []))
    for child in module.get("child_modules", []):
        result.extend(resources(child))
    return result


def build_report(state: dict, plan: dict, inventory: dict, *, region: str,
                 last_apply: str | None = None) -> dict:
    """Return an allowlisted view without values, outputs, tags or plan payloads."""
    if not region or not isinstance(inventory.get("complete"), bool):
        raise ValueError("Region and explicit inventory coverage are required")
    if plan.get("errored") is True:
        raise ValueError("Failed Terraform plan cannot establish drift status")
    managed = resources(state.get("values", {}).get("root_module", {}))
    arns = {r.get("values", {}).get("arn") for r in managed if r.get("mode") == "managed"}
    arns.discard(None)
    changes = {c["address"]: c.get("change", {}).get("actions", [])
               for c in plan.get("resource_drift", [])
               if c.get("change", {}).get("actions") != ["no-op"]}
    rows = [{"address": r["address"], "type": r["type"], "region": region,
             "status": "drift" if r["address"] in changes else "observed",
             "actions": changes.get(r["address"], [])}
            for r in managed if r.get("mode") == "managed"]
    # Inventory is scoped to the requested region by the collector. Candidates
    # may belong to another Terraform state; they must never be auto-imported.
    candidates = sorted({r["arn"] for r in inventory.get("resources", [])
                         if r.get("region") == region and r.get("arn") not in arns
                         and r.get("arn")})
    return {"schema_version": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
            "region": region, "last_apply": last_apply,
            "last_plan": plan.get("timestamp"), "inventory_complete": inventory["complete"],
            "inventory_coverage": inventory.get("coverage", []),
            "resources": rows, "unmanaged_candidates": candidates,
            "drift_count": len(changes)}
