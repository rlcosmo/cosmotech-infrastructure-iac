import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from src.disaster_recovery.evidence import COMPONENTS, compare_costs, recovery_metrics


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 7, tzinfo=timezone.utc)

    def quote(self, region, value):
        return {"region": region, "compatible": True, "quoted_at": self.now.isoformat(),
                "source": "test-fixture-only", "monthly_usd": dict.fromkeys(COMPONENTS, value)}

    def test_total_cost_includes_every_component(self):
        result = compare_costs([self.quote("us-west-2", "2.1"), self.quote("us-east-2", "1.1")], self.now)
        self.assertEqual(result[0]["region"], "us-east-2")
        self.assertEqual(result[0]["estimated_monthly_usd"], Decimal("9.9"))

    def test_partial_stale_and_invalid_costs_are_rejected(self):
        for value in ("NaN", "-1", "bad"):
            with self.assertRaises(ValueError):
                compare_costs([self.quote("us-east-2", value)], self.now)
        quote = self.quote("us-east-2", "1")
        quote["monthly_usd"].pop("inter_region_transfer")
        with self.assertRaises(ValueError):
            compare_costs([quote], self.now)
        with self.assertRaises(ValueError):
            compare_costs([self.quote("us-east-2", "1")], self.now + timedelta(days=8))

    def test_ongoing_outage_does_not_claim_recovery(self):
        result = recovery_metrics(outage=self.now, observed=self.now + timedelta(seconds=60),
                                  recovered=None, last_ack=self.now, recovered_commit=self.now - timedelta(seconds=5))
        self.assertEqual(result, {"available": False, "rto_seconds": None, "downtime_seconds": 60, "rpo_seconds": 5})

    def test_recovery_and_invalid_timeline(self):
        result = recovery_metrics(outage=self.now, observed=self.now + timedelta(seconds=90),
                                  recovered=self.now + timedelta(seconds=30), last_ack=self.now, recovered_commit=self.now)
        self.assertEqual(result["rto_seconds"], 30)
        with self.assertRaises(ValueError):
            recovery_metrics(outage=self.now, observed=self.now, recovered=self.now - timedelta(seconds=1),
                             last_ack=self.now, recovered_commit=self.now)
