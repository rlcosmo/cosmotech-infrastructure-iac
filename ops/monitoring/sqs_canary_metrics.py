"""Read CloudWatch SQS gauges through an injectable API; never touch messages."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime, timedelta


def collect(call: Callable[..., dict], queue_name: str, dlq_name: str, now: datetime) -> str:
    """Missing/stale points emit success=0 and omit gauges instead of inventing zero."""
    names = [
        ("cosmotech_sqs_visible", queue_name, "ApproximateNumberOfMessagesVisible"),
        ("cosmotech_sqs_oldest_age_seconds", queue_name, "ApproximateAgeOfOldestMessage"),
        ("cosmotech_sqs_dlq_visible", dlq_name, "ApproximateNumberOfMessagesVisible"),
        ("cosmotech_sqs_inflight", queue_name, "ApproximateNumberOfMessagesNotVisible"),
        ("cosmotech_sqs_delayed", queue_name, "ApproximateNumberOfMessagesDelayed"),
    ]
    values, timestamps = {}, []
    try:
        for output, queue, metric in names:
            response = call(Namespace="AWS/SQS", MetricName=metric,
                            Dimensions=[{"Name": "QueueName", "Value": queue}],
                            StartTime=now - timedelta(minutes=10), EndTime=now,
                            Period=60, Statistics=["Maximum"])
            points = response.get("Datapoints", [])
            if not points:
                raise ValueError("No queue data")
            point = max(points, key=lambda p: p["Timestamp"])
            stamp = point["Timestamp"]
            if isinstance(stamp, str):
                stamp = datetime.fromisoformat(stamp)
            value = point["Maximum"]
            if (not 0 <= (now - stamp).total_seconds() <= 180
                    or type(value) not in {int, float} or not math.isfinite(value) or value < 0):
                raise ValueError("Invalid queue data")
            timestamps.append(stamp.timestamp())
            values[output] = value
    except (KeyError, ValueError, TypeError, OSError, RuntimeError):
        return "cosmotech_sqs_collection_success 0\n"
    lines = ["cosmotech_sqs_collection_success 1",
             f"cosmotech_sqs_data_timestamp_seconds {min(timestamps)}"]
    lines += [f"{name} {value}" for name, value in values.items()]
    # SQS event counters are sparse on idle queues. Missing is unknown, never zero;
    # it must not suppress the fresh backlog/age gauges used for release safety.
    for output, metric in (("sent", "NumberOfMessagesSent"),
                           ("received", "NumberOfMessagesReceived"),
                           ("deleted", "NumberOfMessagesDeleted")):
        try:
            response = call(Namespace="AWS/SQS", MetricName=metric,
                            Dimensions=[{"Name": "QueueName", "Value": queue_name}],
                            StartTime=now - timedelta(minutes=10), EndTime=now,
                            Period=60, Statistics=["Sum"])
            point = max(response.get("Datapoints", []), key=lambda p: p["Timestamp"])
            stamp = point["Timestamp"]
            if isinstance(stamp, str):
                stamp = datetime.fromisoformat(stamp)
            value = point["Sum"]
            if (not 0 <= (now - stamp).total_seconds() <= 180
                    or type(value) not in {int, float} or not math.isfinite(value) or value < 0):
                raise ValueError("Invalid counter sample")
            lines.append(f"cosmotech_sqs_{output}_per_second {value / 60}")
            lines.append(f'cosmotech_sqs_counter_available{{metric="{output}"}} 1')
        except (KeyError, ValueError, TypeError, OSError, RuntimeError):
            lines.append(f'cosmotech_sqs_counter_available{{metric="{output}"}} 0')
    return "\n".join(lines) + "\n"
