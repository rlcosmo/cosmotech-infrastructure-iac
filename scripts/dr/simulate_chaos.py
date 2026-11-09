#!/usr/bin/env python3
"""Deterministic process-independent model of recovery gates; NOT real AWS RTO."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.disaster_recovery.control import guard_chaos_target, replay_events


def simulate(kind):
    target = dict(account="414053644145", region="us-east-2", cluster="cosmotech-dr",
                  namespace="dr-test", environment="dr-test", synthetic_only=True,
                  duration_seconds=120, kind=kind, telemetry_ready=True)
    guard_chaos_target(target)
    events = [{"event_type":"order.created", "version":2, "order_id":f"synthetic-{n}",
               "order":{"order_id":f"synthetic-{n}", "items":[{"sku":"LAB-SKU-001","quantity":1}]},
               "created_at":"2026-11-08T23:00:00Z"} for n in range(10)]
    journal = events + events[:3]
    completed = set(e['order_id'] for e in events[:4])
    pending = replay_events(journal, completed)
    # Abstract recovery model: a failed pod, node, AZ or dependency blocks the
    # single synthetic consumer until its own recovery checkpoint. These ticks
    # are model transitions, never wall-clock seconds or AWS performance claims.
    fault_until={'pod-loss':1,'node-loss':2,'az-loss':3,'dependency-outage':4}[kind]
    queue=list(pending)
    effects={key:1 for key in completed}
    retries=0;duplicates=0;tick=0;crashed_after_commit=False
    while queue and tick<120:
        tick+=1
        if tick<=fault_until:
            retries+=1
            continue
        event=queue[0];key=event['order_id']
        if key in completed:
            duplicates+=1
            queue.pop(0)
            continue
        effects[key]=effects.get(key,0)+1
        completed.add(key)  # model atomic effect + completion transaction
        if not crashed_after_commit:
            crashed_after_commit=True  # lost ACK, same event delivered again
            continue
        queue.pop(0)
    assert not queue and len(effects)==10 and all(v==1 for v in effects.values())
    assert duplicates==1 and retries==fault_until
    return {"scenario":kind, "execution":"simulation-only", "duplicate_effects":0,
            "orders_recovered":10,"fault_blocked_model_ticks":retries,
            "recovery_model_ticks":tick,"lost_ack_redeliveries":duplicates,
            "measured_aws_rto_seconds":None, "measured_aws_rpo_seconds":None}


if __name__ == '__main__':
    print(json.dumps([simulate(k) for k in ('pod-loss','node-loss','az-loss','dependency-outage')], indent=2))
