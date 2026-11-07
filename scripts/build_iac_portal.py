"""Build local private portal evidence; do not upload raw state or plan."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from iac_portal.report import build_report

parser = argparse.ArgumentParser()
for name in ("state", "plan", "inventory", "region", "output"):
    parser.add_argument(f"--{name}", required=True)
args = parser.parse_args()
inputs = [json.loads(Path(getattr(args, key)).read_text()) for key in ("state", "plan", "inventory")]
Path(args.output).write_text(json.dumps(build_report(*inputs, region=args.region), indent=2) + "\n")
