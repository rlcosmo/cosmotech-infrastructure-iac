"""Deterministic cost comparison and observed recovery metrics."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

COMPONENTS = {"eks", "compute", "network_fixed", "storage", "dynamodb_replication",
              "image_replication", "secrets_replication", "inter_region_transfer", "observability"}


def compare_costs(candidates: list[dict], now: datetime) -> list[dict]:
    """Refuse partial/stale totals; supplied quotes are evidence, not live prices."""
    if not candidates or now.tzinfo is None:
        raise ValueError("Dated regional quotes are required")
    results, regions = [], set()
    for candidate in candidates:
        region = candidate["region"]
        quoted = datetime.fromisoformat(candidate["quoted_at"])
        if (region in regions or not candidate.get("compatible") or quoted.tzinfo is None
                or not timedelta(0) <= now - quoted <= timedelta(days=7)
                or set(candidate["monthly_usd"]) != COMPONENTS or not candidate.get("source")):
            raise ValueError("Incomplete, incompatible or stale comparison")
        regions.add(region)
        try:
            values = [Decimal(str(value)) for value in candidate["monthly_usd"].values()]
            if any(not value.is_finite() or value < 0 for value in values):
                raise ValueError("Invalid cost")
        except InvalidOperation as error:
            raise ValueError("Invalid cost") from error
        results.append({"region": region, "estimated_monthly_usd": sum(values, Decimal(0))})
    return sorted(results, key=lambda row: (row["estimated_monthly_usd"], row["region"]))


def recovery_metrics(*, outage: datetime, observed: datetime,
                     recovered: datetime | None, last_ack: datetime,
                     recovered_commit: datetime) -> dict:
    """RPO uses last acknowledged durable write; RTO ends at end-to-end recovery."""
    timestamps = [outage, observed, last_ack, recovered_commit] + ([recovered] if recovered else [])
    if (any(stamp.tzinfo is None for stamp in timestamps) or observed < outage
            or last_ack > observed or recovered_commit > last_ack
            or (recovered is not None and not outage <= recovered <= observed)):
        raise ValueError("Inconsistent recovery evidence")
    return {"available": recovered is not None,
            "rto_seconds": (recovered - outage).total_seconds() if recovered else None,
            "downtime_seconds": ((recovered or observed) - outage).total_seconds(),
            "rpo_seconds": (last_ack - recovered_commit).total_seconds()}
