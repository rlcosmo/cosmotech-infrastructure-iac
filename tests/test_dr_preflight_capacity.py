"""The five-vCPU quota must constrain the complete proposed topology."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "dr_preflight", Path(__file__).parents[1] / "scripts/dr/preflight.py"
)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class CapacityBudgetTests(unittest.TestCase):
    def test_two_nodes_fit_observed_quota(self):
        self.assertEqual(preflight.capacity_budget(5, 0, 2, 2, False)["remaining_vcpus"], 1)

    def test_rejects_topologies_above_available_quota(self):
        for used, nodes, host in [(0, 3, False), (2, 2, False), (0, 2, True)]:
            with self.subTest(used=used, nodes=nodes, host=host):
                with self.assertRaisesRegex(ValueError, "Insufficient quota"):
                    preflight.capacity_budget(5, used, nodes, 2, host)

    def test_full_topology_requires_six_vcpus(self):
        self.assertEqual(preflight.capacity_budget(6, 0, 2, 2, True)["proposed_vcpus"], 6)


if __name__ == "__main__":
    unittest.main()
