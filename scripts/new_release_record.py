#!/usr/bin/env python3
"""Create and seal release evidence records (INV-0006).

    python3 scripts/new_release_record.py create [--date YYYYMMDD]
    python3 scripts/new_release_record.py seal docs/releases/<release_id>

Runs on the host's Python 3.9. Creates files only under docs/releases/.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_bundle  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RELEASES = ROOT / "docs" / "releases"
GATES = [f"G{n}" for n in range(1, 11)] + ["SEM"]
STATUSES = {"not_run", "in_progress", "pass", "fail", "blocked"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tool(cmd: List[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout.strip()
    except OSError:
        return "unavailable"


def create(date: str) -> Path:
    lines = source_bundle.manifest()
    source_sha = source_bundle.aggregate(lines)
    release_id = f"inv-{date}-{source_sha[:12]}"
    target = RELEASES / release_id
    if target.exists():
        raise SystemExit(f"{target} already exists; releases are immutable")
    (target / "evidence").mkdir(parents=True)
    (target / "SOURCE_MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    shutil.copyfile(ROOT / "docs" / "baseline" / "SOURCE_CHECKSUMS.sha256", target / "INPUTS.sha256")
    release = {
        "release_id": release_id,
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source_sha256": source_sha,
        "design_spec_sha256": _sha(ROOT / "docs" / "DESIGN_SPEC.md"),
        "plan_sha256": _sha(ROOT / "docs" / "IMPLEMENTATION_PLAN.md"),
        "selected_profiles": "unresolved",
        "decision_versions": "unresolved (docs/DECISIONS.md)",
    }
    (target / "RELEASE.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    images: Dict[str, str] = {}
    for image in ("investigators-backend:local", "investigators-ui:local"):
        images[image] = _tool(["docker", "image", "inspect", "--format", "{{.Id}}", image]) or "not built"
    build = {
        "images": images,
        "tools": {
            "uv": _tool(["uv", "--version"]),
            "node": _tool(["node", "-v"]),
            "npm": _tool(["npm", "-v"]),
            "docker": _tool(["docker", "--version"]),
        },
        "base_images": "see docker-compose.yml build args (digest-pinned)",
    }
    (target / "build.json").write_text(json.dumps(build, indent=2) + "\n", encoding="utf-8")
    gates = {gate: {"status": "not_run", "evidence": [], "reviewers": []} for gate in GATES}
    (target / "gates.json").write_text(json.dumps(gates, indent=2) + "\n", encoding="utf-8")
    (target / "reviews.md").write_text(
        f"# Reviews for {release_id}\n\n"
        "| Reviewer (name, role) | UTC date | Gates / tasks | Evidence examined (path, SHA-256) | Decision |\n"
        "|---|---|---|---|---|\n",
        encoding="utf-8",
    )
    return target


def seal(target: Path) -> str:
    gates = json.loads((target / "gates.json").read_text(encoding="utf-8"))
    problems = []
    for gate, record in gates.items():
        if record["status"] not in STATUSES:
            problems.append(f"{gate}: invalid status {record['status']}")
        if record["status"] == "pass" and (not record["evidence"] or not record["reviewers"]):
            problems.append(f"{gate}: pass without evidence and reviewer")
        for path in record["evidence"]:
            if not (target / path).is_file():
                problems.append(f"{gate}: missing evidence file {path}")
    if problems:
        raise SystemExit("refusing to seal:\n  " + "\n  ".join(problems))
    files = sorted(p for p in target.rglob("*") if p.is_file() and p.name != "SEAL.sha256")
    seal_lines = [f"{_sha(p)}  {p.relative_to(target).as_posix()}" for p in files]
    (target / "SEAL.sha256").write_text("\n".join(seal_lines) + "\n", encoding="utf-8")
    for path in [target, *target.rglob("*")]:
        mode = os.stat(path).st_mode
        os.chmod(path, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    return hashlib.sha256((target / "SEAL.sha256").read_bytes()).hexdigest()


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("create")
    make.add_argument("--date", default=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d"))
    close = sub.add_parser("seal")
    close.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    if args.command == "create":
        print(create(args.date))
    else:
        print(f"sealed; SEAL.sha256 digest {seal(args.path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
