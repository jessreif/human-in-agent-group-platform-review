"""INV-0003: reference fixtures stay faithful to the pinned demos."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import extract_demo_reference as extractor
import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "reference"
BEHAVIOR = json.loads((FIXTURES / "behavior_reference.json").read_text(encoding="utf-8"))
CONSTANTS = json.loads((FIXTURES / "demo_constants.json").read_text(encoding="utf-8"))


def _demo_lines(key: str) -> list[str]:
    path = ROOT / BEHAVIOR["demos"][key]["file"]
    return path.read_text(encoding="utf-8").splitlines()


def test_demo_hashes_match_fixture_and_pins() -> None:
    pins = (ROOT / "docs" / "baseline" / "SOURCE_CHECKSUMS.sha256").read_text(encoding="utf-8")
    for key, demo in BEHAVIOR["demos"].items():
        actual = hashlib.sha256((ROOT / demo["file"]).read_bytes()).hexdigest()
        assert actual == demo["sha256"], key
        assert f"{actual}  {demo['file']}" in pins
    for study, source in CONSTANTS["sources"].items():
        assert hashlib.sha256((ROOT / source["file"]).read_bytes()).hexdigest() == source["sha256"], study


def test_extracted_constants_have_not_drifted() -> None:
    expected = extractor.render(extractor.build())
    assert (FIXTURES / "demo_constants.json").read_text(encoding="utf-8") == expected


def test_behavior_ids_are_unique_and_anchored() -> None:
    ids = [b["id"] for b in BEHAVIOR["behaviors"]]
    assert len(ids) == len(set(ids))
    assert all(b["anchors"] for b in BEHAVIOR["behaviors"])
    assert {b["kind"] for b in BEHAVIOR["behaviors"]} <= {"behavior", "timing", "defect"}


@pytest.mark.parametrize(
    "behavior", BEHAVIOR["behaviors"], ids=[b["id"] for b in BEHAVIOR["behaviors"]]
)
def test_every_anchor_holds(behavior: dict[str, Any]) -> None:
    for anchor in behavior["anchors"]:
        lines = _demo_lines(anchor["demo"])
        if "absent" in anchor:
            assert all(anchor["absent"] not in line for line in lines), anchor
        else:
            assert anchor["contains"] in lines[anchor["line"] - 1], anchor


def _behavior(behavior_id: str) -> dict[str, Any]:
    return next(b for b in BEHAVIOR["behaviors"] if b["id"] == behavior_id)


def test_hand_values_agree_with_extracted_literals() -> None:
    tp, pe, shared = CONSTANTS["time_pressure"], CONSTANTS["peer_evaluation"], CONSTANTS["shared"]
    tp01 = _behavior("REF-TP-01")["value"]
    assert tp01["values"] == tp["PRESSURE_CONFIG"]["value"]
    assert tp01["collect_ms"] == tp["CHECKIN_COLLECT_MS"]["value"]
    assert tp01["at_active_s"] == tp["CHECKIN_AT"]["value"]
    # REF-PE-03's ACK_P layer was removed in the 2026-10-01 PE demo rewrite, so there
    # is no extracted constant left to cross-check; the fixture records it as absent.
    assert _behavior("REF-TIME-05")["value"]["timeout_ms"] == shared["AI_TIMEOUT_MS"]["value"]
    world = _behavior("REF-WORLD-01")["value"]
    assert world["crates"] == shared["N_CRATES"]["value"] == len(shared["CRATE_CONTENTS"]["value"])
    assert world["hideouts"] == len(shared["HIDEOUTS"]["value"])
    assert world["assigned"] == shared["N_ASSIGNED"]["value"]


def test_study_specific_surfaces_are_separated() -> None:
    assert "PRESSURE_CONFIG" not in CONSTANTS["peer_evaluation"]
    assert "EVAL_CONFIG" not in CONSTANTS["time_pressure"]
    tp_fields = set(CONSTANTS["time_pressure"]["inv_finish"]["embedded_data_fields"])
    pe_fields = set(CONSTANTS["peer_evaluation"]["inv_finish"]["embedded_data_fields"])
    assert {"inv_pressure_JT", "inv_pressure_PM", "inv_own_pressure"} <= tp_fields - pe_fields
    assert {"inv_peer_rating_JT", "inv_peer_rating_PM"} <= pe_fields - tp_fields
    assert "One minute left." not in json.dumps(CONSTANTS)  # a code behavior, anchored instead
