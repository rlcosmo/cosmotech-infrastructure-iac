#!/usr/bin/env python3
"""On the clean Ohio host only: mount exact restored copies read-only and verify dumps."""

import argparse
import gzip
import hashlib
import json
import subprocess
from pathlib import Path


def mount_copy(volume_id, destination):
    devices = json.loads(
        subprocess.check_output(
            ["lsblk", "--tree", "-J", "-o", "PATH,TYPE,FSTYPE,SERIAL,MOUNTPOINT"],
            text=True,
        )
    )["blockdevices"]
    expected = volume_id.replace("-", "")
    matches = [
        d for d in devices if d["type"] == "disk" and d.get("serial") == expected
    ]
    if len(matches) != 1 or matches[0].get("mountpoint"):
        raise ValueError("Exact unmounted restored EBS disk required")
    disk = matches[0]
    partitions = [
        p
        for p in disk.get("children", [])
        if p["path"].endswith("p1") and p.get("fstype") == "ext4"
    ]
    if len(partitions) != 1 or partitions[0].get("mountpoint") not in (
        None,
        destination,
    ):
        raise ValueError("Expected ext4 root partition from the recorded copy")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run(["blockdev", "--setro", disk["path"]], check=True)
    if (
        subprocess.run(["mountpoint", "-q", str(destination)], check=False).returncode
        != 0
    ):
        subprocess.run(
            [
                "mount",
                "-t",
                "ext4",
                "-o",
                "ro,noload",
                partitions[0]["path"],
                str(destination),
            ],
            check=True,
        )
    result = json.loads(
        subprocess.check_output(
            ["findmnt", "-J", "--target", str(destination)], text=True
        )
    )["filesystems"][0]
    options = set(result["options"].split(","))
    if "ro" not in options or not {"noload", "norecovery"}.intersection(options):
        raise RuntimeError(
            "Read-only mount without journal recovery was not established"
        )
    return {
        "volume": volume_id,
        "device": disk["path"],
        "mount": str(destination),
        "options": result["options"],
    }


def verify_logical(root):
    complete = sorted((root / "opt/cosmotech/backups/logical").glob("*/COMPLETE"))
    if not complete:
        raise RuntimeError("No completed logical backup on the restored ITSM copy")
    latest = complete[-1].parent
    checks = []
    for line in (latest / "SHA256SUMS").read_text().splitlines():
        digest, filename = line.split(maxsplit=1)
        filename = filename.lstrip("*")
        if Path(filename).name != filename:
            raise ValueError("Only local backup filenames are accepted")
        path = latest / filename
        actual = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
        if actual != digest:
            raise RuntimeError("Logical backup checksum mismatch")
        checks.append(
            {"filename": filename, "bytes": path.stat().st_size, "sha256": actual}
        )
    names = {c["filename"] for c in checks}
    if not {"glpi.sql", "kestra.dump", "reconstruction.tar.gz"}.issubset(names):
        raise RuntimeError("Incomplete logical recovery cohort")
    return {
        "completed_backups": len(complete),
        "selected": latest.name,
        "checksums": checks,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--itsm-volume", required=True, choices=["vol-0184f553ed58cf951"]
    )
    parser.add_argument(
        "--zabbix-volume", required=True, choices=["vol-08e83f8bac7a9f1e7"]
    )
    args = parser.parse_args()
    result = {
        "mounts": [
            mount_copy(args.itsm_volume, "/mnt/dr-itsm"),
            mount_copy(args.zabbix_volume, "/mnt/dr-zabbix"),
        ]
    }
    result["itsm"] = verify_logical(Path("/mnt/dr-itsm"))
    backups = sorted(
        Path("/mnt/dr-zabbix/opt/cosmotech/zabbix/backups").glob("config-*.sql.gz")
    )
    if not backups:
        raise RuntimeError("No Zabbix logical SQL dump on restored copy")
    latest = backups[-1]
    count = 0
    with gzip.open(latest, "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            count += len(chunk)
    result["zabbix"] = {
        "logical_backups": len(backups),
        "selected": latest.name,
        "uncompressed_bytes": count,
    }
    for component, root in [
        ("itsm", Path("/mnt/dr-itsm")),
        ("zabbix", Path("/mnt/dr-zabbix")),
    ]:
        result[component]["docker_volume_names"] = sorted(
            p.name for p in (root / "var/lib/docker/volumes").iterdir() if p.is_dir()
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
