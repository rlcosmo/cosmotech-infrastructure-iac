#!/usr/bin/env python3
"""Restore a trusted metadata-only GitOps HEAD without promoting an application.

Run only on the existing protected publisher host. The signing key and GitHub App
credential stay there. A separately reviewed exact HEAD and a cryptographically
verified baseline are mandatory. No key/project policy, application bundle or
release receipt may change. Push is fast-forward only; a race fails closed.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
from pathlib import Path

PROTECTED = (
    "gitops/apps/",
    "gitops/environments/",
    "gitops/policies/",
    "gitops/releases/orders-api-dev.json",
    "gitops/releases/orders-worker-dev.json",
    "gitops/bootstrap/orders-application.yaml",
    "gitops/bootstrap/orders-project.yaml",
    "gitops/bootstrap/orders-worker-application.json",
    "gitops/bootstrap/argocd/",
)
ALLOWED_PREFIXES = ("disaster-recovery/", "docs/", "tests/")
ALLOWED_FILES = frozenset(
    {
        "README.md",
        ".github/workflows/deliver-dev.yml",
        ".github/workflows/dr-validation.yml",
        "gitops/bootstrap/observability-v2-application.json",
        "gitops/bootstrap/observability-v2-project.json",
        "gitops/releases/v2-sqs-workers-source.json",
        "gitops/observability/v2-sqs-workers/alerts.json",
        "gitops/observability/v2-sqs-workers/dashboard.json",
    }
)
ROOT = Path("/var/lib/cosmotech-promotion/authority")
ORIGIN = "https://github.com/rlcosmo/cosmotech-gitops.git"


def validate_paths(paths: list[str]) -> None:
    if not paths:
        raise ValueError("No independently reviewed metadata changes to attest")
    for path in paths:
        if (
            ".." in Path(path).parts
            or path.startswith(PROTECTED)
            or not (path in ALLOWED_FILES or path.startswith(ALLOWED_PREFIXES))
        ):
            raise PermissionError(
                "Checkpoint would attest an unapproved application/security change"
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--head", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not all(re.fullmatch("[a-f0-9]{40}", v) for v in [args.head, args.baseline]):
        raise ValueError("Exact immutable revisions are required")
    config_path = Path("/etc/cosmotech/promotion/gate.json")
    info = config_path.lstat()
    if config_path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
        raise PermissionError("Publisher configuration is not protected")
    configuration = json.loads(config_path.read_text())
    # Import existing trusted adapters; do not run code from the delivery repository.
    from automation.github_access import GitHubApp
    from automation.gitops_publisher import verify_commit
    from automation.live_evidence import client

    credentials = json.loads(
        client("secretsmanager").get_secret_value(SecretId="cosmotech/github/app")[
            "SecretString"
        ]
    )
    app = GitHubApp(credentials, "cosmotech-gitops", write=args.execute)
    environment = app.git_environment(dict(os.environ, GNUPGHOME=str(ROOT / "gnupg")))
    repository = ROOT / "gitops"

    def git(*command: str) -> str:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", *command],
            cwd=repository,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(
                "Checkpoint Git operation failed; no blind retry or force push"
            )
        return result.stdout.strip()

    with (repository / ".git/cosmotech-publisher.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if git("remote", "get-url", "origin") != ORIGIN or git("status", "--porcelain"):
            raise PermissionError("Unexpected or dirty native publisher checkout")
        git("fetch", "--quiet", "origin", "main")
        if git("rev-parse", "origin/main") != args.head:
            raise PermissionError("Reviewed GitOps HEAD moved; review again")
        fingerprint = configuration["gpg_fingerprint"]
        verify_commit(repository, args.baseline, fingerprint, env=environment)
        git("merge-base", "--is-ancestor", args.baseline, args.head)
        paths = git("diff", "--name-only", args.baseline, args.head).splitlines()
        validate_paths(paths)
        tree = git("rev-parse", args.head + "^{tree}")
        evidence = {
            "mode": "plan",
            "parent": args.head,
            "verified_baseline": args.baseline,
            "tree": tree,
            "protected_application_changes": 0,
            "metadata_paths": paths,
            "change_reference": "COSMOTECH-V2-003-20261112",
        }
        if args.execute:
            revision = git(
                "-c",
                "user.name=CosmoTech Promotion",
                "-c",
                "user.email=promotion@localhost",
                "commit-tree",
                tree,
                "-p",
                args.head,
                "-S" + fingerprint,
                "-m",
                "chore(dr): attest reviewed metadata with unchanged approved applications",
            )
            verify_commit(repository, revision, fingerprint, env=environment)
            if git("rev-parse", revision + "^{tree}") != tree:
                raise PermissionError("Checkpoint tree changed unexpectedly")
            evidence.update(mode="signed-before-push", revision=revision)
            audit = ROOT / ("dr-metadata-checkpoint-" + args.head + ".json")
            with audit.open("x") as stream:
                json.dump(evidence, stream, indent=2)
            audit.chmod(0o600)
            git("push", "--quiet", "origin", revision + ":refs/heads/main")
            if git("ls-remote", "origin", "refs/heads/main").split()[0] != revision:
                raise RuntimeError(
                    "Push outcome ambiguous; reconcile saved audit before retry"
                )
            evidence["mode"] = "published"
            audit.write_text(json.dumps(evidence, indent=2))
        print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    main()
