#!/usr/bin/env python3
"""Build deterministic Lambda ZIP files from the first-party orders package."""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
DIST = ROOT / "dist"
ARTIFACTS = {
    "orders-producer.zip": "producer",
    "orders-consumer.zip": "consumer",
}


def source_files(function: str) -> list[Path]:
    package = SOURCE / "orders"
    roots = (package, package / "shared", package / function)
    files = {package / "__init__.py"}
    for root in roots[1:]:
        files.update(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)
    return sorted(files)


def build() -> None:
    DIST.mkdir(exist_ok=True)
    for artifact, function in ARTIFACTS.items():
        files = source_files(function)
        if not files:
            raise RuntimeError(f"No Python source files found for {function} under {SOURCE}")
        destination = DIST / artifact
        temporary = destination.with_suffix(".zip.tmp")
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in files:
                info = zipfile.ZipInfo(path.relative_to(SOURCE).as_posix())
                info.date_time = (2020, 1, 1, 0, 0, 0)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, path.read_bytes())
        temporary.replace(destination)
        print(f"Created {destination.relative_to(ROOT)} ({destination.stat().st_size} bytes)")


    destination = DIST / "external-http-probe.zip"
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        info = zipfile.ZipInfo("lambda_function.py")
        info.date_time = (2020, 1, 1, 0, 0, 0)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(info, (SOURCE / "synthetic_probe/lambda_function.py").read_bytes())
    print(f"Created {destination.relative_to(ROOT)} ({destination.stat().st_size} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        build()
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
