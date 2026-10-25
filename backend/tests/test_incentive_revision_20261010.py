"""2026-10-10 incentive-compatibility revision (owner direction).

tp_ai_v7 / tp_scripted_v9: consent v7 offers '$3.00 plus a $0.50 bonus for
correctly locating your assigned crates' while payment v5 pays a flat 350 cents
to every completer — university IRB classifies an offered incentive paid out to
everyone as low-risk non-deception, so the bonus frame is copy only and
bonus.type stays none. Instructions (Case background), the task answer bar, and
the debrief (non-deception disclosure) carry the matching sentences; the
training/debrief pointer artifacts version to v3 so the offer is provable from
the bound manifest alone. Pilot incentive frame, not a pre-registered
condition — identical across all four cells.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain.manifests.binding import bind
from app.domain.manifests.store import ContentStore
from app.timing.policy import resolve_policy

NEW = ("tp_ai_v7", "tp_scripted_v9")
BONUS_SENTENCE = (
    "you will be paid $3.00 plus a $0.50 bonus for correctly locating your assigned crates"
)


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


@pytest.mark.parametrize("manifest_id", NEW)
def test_incentive_consent_and_payment(store: ContentStore, manifest_id: str) -> None:
    b = bind(store, manifest_id, mode="live")
    assert b.launch_eligible
    pay = b.artifacts["outcomes.payment_policy"].artifact.body
    assert pay.base_cents == 350  # type: ignore[union-attr]
    assert pay.bonus.type == "none"  # type: ignore[union-attr]
    text = b.artifacts["instruments.consent"].artifact.body.text  # type: ignore[union-attr]
    flat = " ".join(p for s in text.sections for p in s.paragraphs)
    assert BONUS_SENTENCE in flat
    assert "$3.50" not in flat  # the offer is described as 3.00 + 0.50, never flat
    # The bound material pointers carry the offer in the manifest provenance.
    assert b.artifacts["instruments.consent"].key == "consent.reference@v7"
    assert b.artifacts["instruments.training"].key == "training.reference@v3"
    assert b.artifacts["instruments.debrief"].key == "debrief.tp_reference@v3"


@pytest.mark.parametrize("manifest_id", NEW)
def test_only_the_consent_paragraph_differs_from_v6(store: ContentStore, manifest_id: str) -> None:
    text = bind(store, manifest_id, mode="live").artifacts["instruments.consent"].artifact.body.text  # type: ignore[union-attr]
    v6 = json.loads((Path(store.root) / "instruments/consent.reference.v6.json").read_text())[
        "body"
    ]["text"]
    new_paras = [p for s in text.sections for p in s.paragraphs]
    old_paras = [p for s in v6["sections"] for p in s["paragraphs"]]
    diff = [n for n, o in zip(new_paras, old_paras, strict=True) if n != o]
    assert len(diff) == 1 and BONUS_SENTENCE in diff[0]
    assert text.heading == v6["heading"] and text.subheading == v6["subheading"]


def test_ai_and_scripted_builds_differ_only_in_teammate_engine(
    store: ContentStore,
) -> None:
    ai = store.manifests["tp_ai_v7"].manifest.model_dump(mode="json")
    sc = store.manifests["tp_scripted_v9"].manifest.model_dump(mode="json")
    for body in (ai, sc):
        for key in ("manifest_id", "manifest_version", "title", "notes"):
            body.pop(key)
        body["teammates"].pop("social_generation")
        body["teammates"].pop("social_prompt")
    assert ai == sc


def test_remarks_and_timing_carry_over_unchanged(store: ContentStore) -> None:
    for mid in NEW:
        p = resolve_policy(bind(store, mid, mode="live"), "high")
        assert p.budget_ms == 300_000
        assert p.scripted_requests["times_ms"] == [45_000, 130_000, 210_000]
        assert p.controls["pressure_remarks"]["schedule"] == [
            [75_000, "first"],
            [83_000, "second"],
        ]


def test_predecessors_are_unchanged(store: ContentStore) -> None:
    """Old live pins keep consent v6 / payment v4 (hash continuity)."""
    for mid in ("tp_ai_v6", "tp_scripted_v8"):
        b = bind(store, mid, mode="live")
        assert b.artifacts["instruments.consent"].key == "consent.reference@v6"
        pay = b.artifacts["outcomes.payment_policy"].artifact.body
        assert pay.base_cents == 300  # type: ignore[union-attr]
