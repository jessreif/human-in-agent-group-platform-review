#!/usr/bin/env python3
"""Validate an assistant-release evidence bundle against its pinned panel.

Panel resolution (BA-108 — panels are sealed, never edited in place):

* ``--panel PATH`` selects the panel file explicitly.
* Otherwise the panel is resolved from the evidence: a sibling
  ``<evidence-stem>.panel.json`` (archived alongside the bundle at collection
  or sealing time) is preferred; falling back to
  ``release/assistant-panel-<vN>.json`` derived from ``panel_version``.
* When the evidence carries ``panel_sha256`` (collector emits it), the resolved
  panel file must hash to exactly that value — a wrong or mutated panel fails
  before any row checks run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.assistant_release import validate_evidence  # noqa: E402


def _resolve_panel(evidence_path: Path, evidence: dict) -> Path:
    version = str(evidence.get("panel_version") or "")
    suffix = version.rsplit("_", 1)[-1]  # assistant_release_panel_v1 -> v1
    sibling = evidence_path.with_name(evidence_path.name.removesuffix(".json") + ".panel.json")
    for candidate in (
        sibling,
        ROOT / "release" / f"assistant-panel-{suffix}.json",
    ):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"no panel for {version or '<missing panel_version>'}: pass --panel")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--panel", type=Path, default=None)
    args = parser.parse_args()
    evidence = json.loads(args.evidence.read_text())
    try:
        panel_path = args.panel or _resolve_panel(args.evidence, evidence)
    except FileNotFoundError as exc:
        print(json.dumps({"passed": False, "errors": [str(exc)]}, indent=2))
        return 1
    errors: list[str] = []
    pinned_sha = evidence.get("panel_sha256")
    actual_sha = hashlib.sha256(panel_path.read_bytes()).hexdigest()
    if isinstance(pinned_sha, str) and pinned_sha != actual_sha:
        errors.append(
            f"panel_sha256: evidence pins {pinned_sha}, resolved panel "
            f"{panel_path.name} hashes {actual_sha}"
        )
    panel = json.loads(panel_path.read_text())
    errors.extend(validate_evidence(panel, evidence))
    print(
        json.dumps(
            {"passed": not errors, "panel": str(panel_path), "errors": errors},
            indent=2,
        )
    )
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
