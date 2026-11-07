"""Read tagging inventory with explicit partial coverage; never fetch secret values."""
import argparse
import json
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--region", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
# AWS CLI automatically paginates. This API does not return every untagged
# resource, so even a successful collection is explicitly incomplete.
result = subprocess.run(
    ["aws", "resourcegroupstaggingapi", "get-resources", "--region", args.region,
     "--output", "json", "--cli-connect-timeout", "5", "--cli-read-timeout", "30"],
    capture_output=True, check=False, timeout=180,
)
if result.returncode:
    raise SystemExit("Inventory unavailable; no report published")
response = json.loads(result.stdout)
report = {"complete": False, "coverage": ["tagging-api: tagged/previously-tagged supported resources only"],
          "resources": [{"arn": row["ResourceARN"], "region": args.region}
                        for row in response.get("ResourceTagMappingList", [])]}
Path(args.output).write_text(json.dumps(report))
