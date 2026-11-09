#!/usr/bin/env python3
"""Generate an auditable offline plan; never apply or change DNS."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.disaster_recovery.control import promotion_plan

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    print(json.dumps(promotion_plan(json.loads(args.evidence.read_text())), indent=2))
