"""Read paginated AWS resource metadata; never retrieve secret values or messages."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from iac_portal.inventory import collect


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    def call(service: str, operation: str, *options: str) -> dict:
        response = subprocess.run(
            ["aws", service, operation, "--region", args.region, "--output", "json",
             "--cli-connect-timeout", "5", "--cli-read-timeout", "15", *options],
            capture_output=True, check=False, timeout=90,
            env={**os.environ, "AWS_MAX_ATTEMPTS": "2", "AWS_RETRY_MODE": "standard"})
        if response.returncode:
            raise RuntimeError("Inventory operation unavailable")
        return json.loads(response.stdout)

    report = collect(call, args.region)
    if all(entry["status"] == "unavailable" for entry in report["coverage"]):
        raise SystemExit("Inventory unavailable; no report published")
    Path(args.output).write_text(json.dumps(report))


if __name__ == "__main__":
    main()
