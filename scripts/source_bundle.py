#!/usr/bin/env python3
"""Source-bundle checksum (plan §2.2, INV-0006, INV-0105).

There is no Git tree, so builds and releases are identified by a SHA-256 over the declared
build source roots. The aggregate is the SHA-256 of a manifest of ``<sha256>  <path>`` lines
sorted by path; generated and dependency directories are excluded.

    python3 scripts/source_bundle.py                 # prints the aggregate checksum
    python3 scripts/source_bundle.py --manifest FILE # also writes the per-file manifest
    python3 scripts/source_bundle.py --behavior      # participant-facing-scoped digest

``--behavior`` digests only the subset of source that defines what a participant-facing
session does or records: participant API/lifecycle/engine/timing code, content, manifests,
prompts, providers, measurement/coding (result-defining), and the participant UI. The
compatibility key pins this digest — not the whole-repo aggregate — so dashboard, export,
admin and test churn cannot split an analytic partition. The full aggregate remains the
release identity (``build_source_sha256``), still stamped and exported for audit.

``OPS_PATHS`` is a closed denylist, deliberately maintained by hand: a new file defaults to
behavior scope unless it is explicitly classified here, so forgetting to classify a new
participant-path file fails *closed* (churns the digest) rather than silently exempting it.

Runs on the host's Python 3.9.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOTS = ("backend", "frontend", "content", "docker-compose.yml")
# Ops/observational paths: excluded from --behavior only (still in the full aggregate).
# Each prefix is repo-relative; a file is ops-scoped when any prefix matches.
OPS_PATHS = (
    "backend/tests/",
    "backend/harness/",
    "backend/app/api/admin.py",
    "backend/app/api/health.py",
    # app/export is itemised, not blanket: aggregates.py is imported by
    # measurement/fidelity (behavior) and defines compatibility-key grouping.
    "backend/app/export/__init__.py",
    "backend/app/export/csvsafe.py",
    "backend/app/export/historical.py",
    "backend/app/export/legacy.py",
    "backend/app/export/restricted.py",
    "backend/app/export/semantic.py",
    "backend/app/export/study.py",
    # app/db/export.py stays behavior: it holds consistent-snapshot export AND
    # replay reconstruction (reconstruct_session), both session-semantics-bearing.
    "backend/app/services/adminauth.py",
    "backend/app/services/adminops.py",
    "backend/app/services/adminviews.py",
    "backend/app/services/assistant_metrics.py",
    "backend/app/services/assistant_monitor.py",
    "backend/app/services/assistant_release.py",
    "backend/app/services/dashboard.py",
    "backend/app/services/dashboard_analysis.py",
    "backend/app/services/dashboard_exports.py",
    "backend/app/services/dashboard_ops.py",
    "backend/app/services/pooling_policy.py",
    "backend/app/services/retention.py",
    "frontend/src/admin/",
    "frontend/e2e/",
    "frontend/tests/",
)
OPS_NAME_SUFFIXES = (".test.ts", ".test.tsx", ".test.py")
EXCLUDED_DIRS = {
    ".venv",
    "node_modules",
    "dist",
    "coverage",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    # Generated evidence/docs trees must never shift the build stamp — e.g. a
    # relative --out once wrote sim runs to backend/docs/ inside the bundle.
    "docs",
    "evidence",
}
EXCLUDED_NAMES = {".env"}


def _is_ops(rel: str, name: str) -> bool:
    return rel.startswith(OPS_PATHS) or name.endswith(OPS_NAME_SUFFIXES)


def iter_files(root: Path = ROOT, *, behavior_only: bool = False):
    for entry in BUILD_ROOTS:
        path = root / entry
        if path.is_file():
            if not (behavior_only and _is_ops(entry, path.name)):
                yield path
            continue
        if not path.is_dir():
            continue
        for candidate in sorted(path.rglob("*")):
            rel = candidate.relative_to(root)
            if any(part in EXCLUDED_DIRS for part in rel.parts):
                continue
            if (
                candidate.is_file()
                and candidate.name not in EXCLUDED_NAMES
                and not (behavior_only and _is_ops(rel.as_posix(), candidate.name))
            ):
                yield candidate


def manifest(root: Path = ROOT, *, behavior_only: bool = False) -> list[str]:
    lines = []
    for path in iter_files(root, behavior_only=behavior_only):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(root).as_posix()}")
    return sorted(lines, key=lambda line: line.split("  ", 1)[1])


def aggregate(lines: list[str]) -> str:
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, help="write the per-file manifest here")
    parser.add_argument(
        "--behavior",
        action="store_true",
        help="digest only participant-facing source (excludes OPS_PATHS)",
    )
    args = parser.parse_args(argv)
    lines = manifest(behavior_only=args.behavior)
    if args.manifest:
        args.manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(aggregate(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
