#!/usr/bin/env python3
"""Emit the WP09 material/exposure snapshot (INV-0909).

For every profile x arm combination (8 total) this writes a checkable JSON record of
what a participant session actually binds and serves:

- the bound manifest + bundle hashes (the pinned publication identity);
- every bound artifact slot -> key/sha256/kind/status;
- the declared instruments (ordered item ids, response scale, applies_to);
- the bound consent/training/debrief material references;
- for PE profiles, the resolved exposure surfaces for that arm — the exact copy the
  participant projection serves, with empty control surfaces shown as ``""``;
- for TP profiles, the check-in policy + the scripted teammate-value mapping the arm
  assigns;
- the manipulation-event vocabulary that stage will produce (``pressure_checkin``
  receipt vs the PE per-surface ``exposure_committed`` receipts).

Run from ``backend/`` (the content store resolves relative to the repo root)::

    cd backend && uv run python ../scripts/material_snapshot.py [--write]

Without ``--write`` the snapshot is printed to stdout; with ``--write`` it is written to
``docs/wp09/material_snapshot.json`` (the file the audit trail diffs).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# The app package lives under backend/ — put it on sys.path so the script also runs
# when invoked as ``uv run python ../scripts/material_snapshot.py`` from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.domain.manifests.binding import BoundManifest, bind  # noqa: E402
from app.domain.manifests.store import ContentStore
from app.services import engines
from app.services.lifecycle import INSTRUMENT_SLOTS
from app.timing.policy import resolve_policy

PROFILES = ("tp_demo_v1", "pe_demo_v1", "tp_review_first_v2", "pe_pilot_v2")
# What each study's manipulation writes durably — the auditable exposure vocabulary.
MANIPULATION_EVENTS = {
    "pressure_checkin": [
        "manipulation_exposure:pressure_checkin",
        "checkin_open",
        "checkin_response",
        "checkin_reveal",
    ],
    "evaluation_exposure": [
        "manipulation_exposure:pe_intro",
        "manipulation_exposure:pe_callout",
        "manipulation_exposure:pe_strip",
        "manipulation_exposure:pe_system_message",
        "manipulation_exposure:peer_intro",
        "exposure_committed",
    ],
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _artifact_entry(stored: Any) -> dict[str, Any]:
    return {
        "key": stored.key,
        "sha256": stored.sha256,
        "kind": getattr(stored.artifact, "kind", None),
        "status": getattr(stored.artifact, "status", None),
    }


def _instrument_detail(bound: BoundManifest, slot: str) -> dict[str, Any] | None:
    stored = bound.artifacts.get(slot)
    if stored is None:
        return None
    body = stored.artifact.body
    entry: dict[str, Any] = _artifact_entry(stored)
    entry["applies_to"] = getattr(body, "applies_to", None)
    if hasattr(body, "items"):
        entry["items"] = [i.item_id for i in body.items]
        entry["response"] = body.response.model_dump(mode="json")
    return entry


def combination_snapshot(store: ContentStore, manifest_id: str, arm: str) -> dict[str, Any]:
    bound = bind(store, manifest_id, mode="preview")
    policy = resolve_policy(bound, arm)
    manifest = bound.manifest
    texts = engines.pe_surface_texts(bound, arm)
    declared_surfaces = list(getattr(manifest.manipulation, "surfaces", []) or [])

    snap: dict[str, Any] = {
        "manifest_id": manifest_id,
        "arm": arm,
        "manifest_sha256": bound.manifest_sha256,
        "bundle_sha256": bound.bundle_sha256,
        "binding_sha256": bound.binding_sha256,
        "study_key": manifest.study_key,
        "profile_kind": manifest.profile_kind,
        "material_status": manifest.material_status,
        "launch_eligible": bound.launch_eligible,
        "timing": {
            "budget_ms": policy.budget_ms,
            "checkin": policy.checkin,
            "phases": [
                {"name": p.name, "from_ms": p.from_ms, "until_ms": p.until_ms,
                 "enabled": sorted(p.enabled)}
                for p in policy.phases
            ],
            "scripted_requests_active_ms": policy.scripted_requests["times_ms"],
        },
        "manipulation": {
            "kind": policy.manipulation["kind"],
            "events": MANIPULATION_EVENTS.get(str(policy.manipulation["kind"]), []),
        },
        "instruments": {
            stage: _instrument_detail(bound, slot)
            for stage, slot in sorted(INSTRUMENT_SLOTS.items())
        },
        "materials": {
            stage: _artifact_entry(bound.artifacts[slot])
            for stage, slot in {
                "consent": "instruments.consent",
                "training": "instruments.training",
                "debrief": "instruments.debrief",
            }.items()
            if slot in bound.artifacts
        },
        "artifacts": {
            slot: _artifact_entry(stored) for slot, stored in sorted(bound.artifacts.items())
        },
    }
    if policy.manipulation["kind"] == "pressure_checkin":
        snap["manipulation"]["teammate_values"] = list(
            policy.manipulation["teammate_values"]
        )
    else:
        # The exact copy the participant projection serves this arm; empty surfaces are
        # deliberately "" (control arm) — and are never committed or rendered.
        snap["exposure"] = {
            "declared_surfaces": declared_surfaces,
            "served": {s: texts.get(s, "") for s in declared_surfaces},
        }
    return snap


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write docs/wp09/material_snapshot.json")
    args = parser.parse_args(argv)

    store = ContentStore(repo_root() / "content")
    combos = [
        combination_snapshot(store, manifest_id, arm)
        for manifest_id in PROFILES
        for arm in store.manifests[manifest_id].manifest.arms
    ]
    doc = {
        "schema": "investigators.material_snapshot/1",
        "requirement": "INV-0909",
        "profiles": list(PROFILES),
        "combinations": combos,
    }
    text = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.write:
        out = repo_root() / "docs" / "wp09" / "material_snapshot.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
