#!/usr/bin/env python3
"""Recompute source-bundle fingerprints and compare them to docs/FINGERPRINTS.json.

    python3 scripts/verify_fingerprints.py
    python3 scripts/verify_fingerprints.py --write

``--write`` records the hashes of the tree you just hashed. Commit that file
only after you mean to. The production working-tree hashes from 2026-10-21
are stored beside them and are not recomputed from this tree.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "FINGERPRINTS.json"
REFERENCE = ROOT / "docs" / "baseline" / "reference" / "investigators_time_pressure_demo.html"
REFERENCE_SHA = "7ced90c5e0c6f45882e9c1c54650fd0c23b137d744b3c9a138ea49d53d6b9825"
PRODUCTION_FULL = "96c87fb557e3e30d0ad45d5636aed61d9c29bdd9795af714fcfb82a5a6680968"
PRODUCTION_BEHAVIOR = "60e2c664d5a97c8656df5ce3e773c05fece5cac8f7a54c1128b87e797f6188e7"


def _bundle(behavior: bool) -> str:
    cmd = [sys.executable, str(ROOT / "scripts" / "source_bundle.py")]
    if behavior:
        cmd.append("--behavior")
    out = subprocess.check_output(cmd, cwd=ROOT, text=True).strip()
    return out.splitlines()[-1].strip()


def _file_sha(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def current() -> dict[str, str]:
    return {
        "snapshot_full": _bundle(False),
        "snapshot_behavior": _bundle(True),
        "frozen_reference": _file_sha(REFERENCE),
        "production_full_2026_10_21": PRODUCTION_FULL,
        "production_behavior_2026_10_21": PRODUCTION_BEHAVIOR,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    got = current()
    if got["frozen_reference"] != REFERENCE_SHA:
        print("frozen reference hash mismatch", file=sys.stderr)
        return 1
    if args.write or not DOC.is_file():
        DOC.write_text(json.dumps(got, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {DOC}")
        return 0
    expected = json.loads(DOC.read_text(encoding="utf-8"))
    bad = [key for key, value in got.items() if expected.get(key) != value]
    if bad:
        for key in bad:
            print(f"{key}: recorded {expected.get(key)} computed {got[key]}")
        return 1
    print("fingerprints match docs/FINGERPRINTS.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
