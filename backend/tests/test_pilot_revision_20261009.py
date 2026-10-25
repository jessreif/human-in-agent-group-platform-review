"""2026-10-09 pilot revision (owner + PhD student, after the first N=5 pilot).

tp_ai_v4 / tp_scripted_v6: consent v6 (~12 minutes, $3.00) + payment v4 (300 cents),
300 s active budget with the check-in still at active 60, scripted requests at 45/130/210,
the one-minute warning at 240, and two fixed arm-specific teammate remarks at 75/83.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.manifests.binding import BindError, bind
from app.domain.manifests.diff import paired_arm_diff
from app.domain.manifests.schema import Manifest
from app.domain.manifests.store import ContentStore
from app.timing.policy import resolve_policy

NEW = ("tp_ai_v4", "tp_scripted_v6")
REMARKS = {
    "high": [
        "Yikes this is a lot of info, I don't think I am going to finish on time",
        "i know right!",
    ],
    "low": [
        "Ok this seems manageable, I think I'll finish with time to spare",
        "me too! i think",
    ],
}


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


@pytest.mark.parametrize("manifest_id", NEW)
def test_revision_timing_and_payment(store: ContentStore, manifest_id: str) -> None:
    b = bind(store, manifest_id, mode="live")
    assert b.launch_eligible
    for arm in ("high", "low"):
        p = resolve_policy(b, arm)
        assert p.budget_ms == 300_000
        assert p.checkin is not None and p.checkin["at_active_ms"] == 60_000
        assert p.scripted_requests["times_ms"] == [45_000, 130_000, 210_000]
        assert [w.due_active_ms for w in p.warnings] == [240_000]
        assert p.warnings[0].low_time_until_ms == 300_000
        assert p.controls["pressure_remarks"]["schedule"] == [[75_000, "first"], [83_000, "second"]]
        assert p.manipulation["remark_lines"] == REMARKS[arm]
    pay = b.artifacts["outcomes.payment_policy"].artifact.body
    assert pay.base_cents == 300  # type: ignore[union-attr]
    text = b.artifacts["instruments.consent"].artifact.body.text  # type: ignore[union-attr]
    flat = " ".join(p for s in text.sections for p in s.paragraphs)
    assert (
        "We estimate the study will take approximately 12 minutes to complete, and you will "
        "be paid $3.00. To be compensated you need to reach the end of the study and click "
        "Submit. A payment code will appear on the final page of the study." in flat
    )
    assert "15 minutes" not in flat and "$4.00" not in flat


@pytest.mark.parametrize("manifest_id", NEW)
def test_remarks_are_the_only_new_arm_difference(store: ContentStore, manifest_id: str) -> None:
    paths = {d.path for d in paired_arm_diff(bind(store, manifest_id, mode="live"))}
    assert paths == {
        "manipulation.arm.arm",
        "manipulation.arm.teammate_values",
        "manipulation.arm.remark_lines",
    }


def test_ai_and_scripted_builds_differ_only_in_teammate_engine(store: ContentStore) -> None:
    ai = store.manifests["tp_ai_v4"].manifest.model_dump(mode="json")
    sc = store.manifests["tp_scripted_v6"].manifest.model_dump(mode="json")
    for body in (ai, sc):
        for key in ("manifest_id", "manifest_version", "title", "notes"):
            body.pop(key)
        body["teammates"].pop("social_generation")
        body["teammates"].pop("social_prompt")
    assert ai == sc


def test_predecessors_are_unchanged(store: ContentStore) -> None:
    """Old live pins keep their 360 s budget and no remarks (hash continuity)."""
    for mid in ("tp_ai_v3", "tp_scripted_v5"):
        m = store.manifests[mid].manifest
        assert m.timing.active_budget_s == 360
        assert m.manipulation.remarks is None  # type: ignore[union-attr]
        assert "remarks" not in m.model_dump(mode="json")["manipulation"]
        assert (
            "pressure_remarks" not in resolve_policy(bind(store, mid, mode="live"), "high").controls
        )


def _profile(store: ContentStore, mid: str) -> dict:  # type: ignore[type-arg]
    return json.loads((Path(store.root) / store.manifests[mid].path).read_text(encoding="utf-8"))


def test_schema_enforces_the_profile_budget(store: ContentStore) -> None:
    body = _profile(store, "tp_ai_v4")
    late = json.loads(json.dumps(body))
    late["timing"]["scripted_requests"]["times_active_s"] = [45, 130, 300]
    with pytest.raises(ValidationError, match="inside the active budget"):
        Manifest.model_validate_json(json.dumps(late))
    short = json.loads(json.dumps(body))
    short["channels"]["phases"][-1]["until_active_s"] = 360
    with pytest.raises(ValidationError, match="cover the whole active budget"):
        Manifest.model_validate_json(json.dumps(short))
    odd = json.loads(json.dumps(body))
    odd["timing"]["active_budget_s"] = 240
    with pytest.raises(ValidationError):
        Manifest.model_validate_json(json.dumps(odd))


def test_remarks_must_fit_the_budget_and_open_chat(store: ContentStore, tmp_path: Path) -> None:
    import shutil

    root = tmp_path / "content"
    shutil.copytree(Path(store.root), root)
    path = root / "scripts" / "remarks.tp_pressure.v1.json"
    d = json.loads(path.read_text(encoding="utf-8"))
    d["body"]["remarks"][1]["at_active_s"] = 310
    path.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(BindError, match="past budget"):
        bind(ContentStore(root), "tp_ai_v4", mode="preview")


def test_remark_wording_bypasses_the_generated_output_gate() -> None:
    """The verbatim lines carry '!' (the generated-text voice_format gate would reject
    them) — so they must stay fixed bound copy, never routed through generation. None
    of them trips the condition_leak classifier."""
    from app.services import engines

    for lines in REMARKS.values():
        for line in lines:
            assert "condition_leak" not in engines.validate_teammate_output(line)
    assert "voice_format" in engines.validate_teammate_output("i know right!")
