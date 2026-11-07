"""Review only addresses/actions; require a successfully generated saved plan."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from publication.plan_gate import inspect_plan

parser = argparse.ArgumentParser()
parser.add_argument("plan", type=Path)
parser.add_argument("--bootstrap", action="store_true")
args = parser.parse_args()
report = inspect_plan(json.loads(args.plan.read_text()), bootstrap=args.bootstrap)
print(json.dumps(report))
raise SystemExit(0 if report["can_apply"] else 1)
