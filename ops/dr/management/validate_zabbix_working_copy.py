#!/usr/bin/env python3
"""Replay and check ext4 only on an independent snapshot copy; preserve both original mounts."""

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path("/opt/cosmotech/dr-recovery")
MOUNT = Path("/mnt/dr-zabbix-work")
ORIGINALS = {"vol0184f553ed58cf951", "vol08e83f8bac7a9f1e7"}


def run(arguments, *, capture=False):
    with (ROOT / "working-filesystem-private.log").open("ab") as log:
        r = subprocess.run(
            arguments,
            stdout=subprocess.PIPE if capture else log,
            stderr=log,
            check=False,
        )
    if r.returncode:
        raise RuntimeError(
            "Working-copy filesystem validation failed; inspect protected log"
        )
    return r.stdout.decode().strip() if capture else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--volume", required=True)
    args = parser.parse_args()
    if (
        not re.fullmatch(r"vol-[a-f0-9]{17}", args.volume)
        or args.volume.replace("-", "") in ORIGINALS
    ):
        raise ValueError("Independent recorded working-copy volume required")
    devices = json.loads(
        run(
            ["lsblk", "--tree", "-J", "-o", "PATH,TYPE,FSTYPE,SERIAL,MOUNTPOINT"],
            capture=True,
        )
    )["blockdevices"]
    preserved = []
    for disk in devices:
        if disk.get("serial") in ORIGINALS:
            if run(["blockdev", "--getro", disk["path"]], capture=True) != "1":
                raise RuntimeError("Original restored copy is not kernel read-only")
            preserved.append(disk["serial"])
    if set(preserved) != ORIGINALS:
        raise ValueError("Both preserved original copies must remain attached")
    matches = [d for d in devices if d.get("serial") == args.volume.replace("-", "")]
    if len(matches) != 1:
        raise ValueError("Exact working-copy NVMe serial is required")
    disk = matches[0]
    parts = [
        p
        for p in disk.get("children", [])
        if p["path"].endswith("p1") and p.get("fstype") == "ext4"
    ]
    if len(parts) != 1:
        raise ValueError("Expected recorded ext4 root partition")
    partition = parts[0]["path"]
    MOUNT.mkdir(parents=True, exist_ok=True)
    marker = ROOT / "zabbix-working-filesystem-verified.json"
    if marker.exists():
        evidence = json.loads(marker.read_text())
        if evidence["volume"] != args.volume:
            raise ValueError("Existing evidence belongs to another working copy")
        if parts[0].get("mountpoint") not in [None, str(MOUNT)]:
            raise ValueError("Working copy mounted at an unexpected location")
        if not parts[0].get("mountpoint"):
            run(
                [
                    "mount",
                    "-t",
                    "ext4",
                    "-o",
                    "rw,nosuid,nodev,noexec",
                    partition,
                    str(MOUNT),
                ]
            )
        print(json.dumps(evidence, sort_keys=True))
        return
    # No application may consume the working disk before the initial consistency check.
    ids = run(["docker", "ps", "-q"], capture=True).splitlines()
    for container in ids:
        mounts = json.loads(
            run(
                ["docker", "inspect", "--format", "{{json .Mounts}}", container],
                capture=True,
            )
        )
        if any(m.get("Source", "").startswith(str(MOUNT)) for m in mounts):
            raise RuntimeError(
                "Working disk already in use; refuse unmount or filesystem check"
            )
    if parts[0].get("mountpoint") not in [None, str(MOUNT)]:
        raise ValueError("Unexpected working-copy mountpoint")
    if not parts[0].get("mountpoint"):
        run(
            [
                "mount",
                "-t",
                "ext4",
                "-o",
                "rw,nosuid,nodev,noexec",
                partition,
                str(MOUNT),
            ]
        )
    # Mount replays the crash-consistent journal, then a clean unmount permits an offline read-only check.
    run(["umount", str(MOUNT)])
    run(["e2fsck", "-f", "-n", partition])
    run(["mount", "-t", "ext4", "-o", "rw,nosuid,nodev,noexec", partition, str(MOUNT)])
    MOUNT.chmod(0o700)
    data = MOUNT / "var/lib/docker/volumes/cosmotech-zabbix-clickhouse/_data"
    size = int(run(["du", "-sb", str(data)], capture=True).split()[0])
    evidence = {
        "volume": args.volume,
        "snapshot": "snap-0e89b80997e9a7349",
        "mount": str(MOUNT),
        "original_copies_read_only": len(preserved),
        "offline_filesystem_check": "passed",
        "clickhouse_directory_bytes": size,
        "directory_read_errors": 0,
        "copied_os_booted": False,
    }
    marker.write_text(json.dumps(evidence, indent=2))
    marker.chmod(0o600)
    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    main()
