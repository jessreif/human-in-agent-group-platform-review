"""2026-10-10 comprehension-check + eligibility-phrasing revision (owner direction).

tp_ai_v8 / tp_scripted_v10: consent v8 softens the bonus offer to 'you will be
paid $3.00 and will be eligible to receive a bonus of up to $0.50 based on your
performance on the task', and training v4 adds a five-item true/false
comprehension gate on the last practice screen before 'Connecting you to your
team' — the server holds the answer key, wrong answers come back on
``result.missed`` for highlighting, every attempt is recorded, and completion
requires a zero-miss event (still gated on the committed BA-95 practice share).
payment.s5_base_only@v5 and debrief.tp_reference@v3 are unchanged — the bonus
is still paid to every completer and the debrief still discloses it. Minted for
preview/QA first per owner direction; no new fielding group until lab testing.
"""

from __future__ import annotations

import pytest

from app.domain.manifests.binding import bind
from app.domain.manifests.store import ContentStore

NEW = ("tp_ai_v8", "tp_scripted_v10")
BONUS_SENTENCE = (
    "you will be paid $3.00 and will be eligible to receive a bonus of up to "
    "$0.50 based on your performance on the task"
)
OLD_OFFER = "$3.00 plus a $0.50 bonus"


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


@pytest.mark.parametrize("manifest_id", NEW)
def test_consent_v8_eligibility_offer(store: ContentStore, manifest_id: str) -> None:
    b = bind(store, manifest_id, mode="live")
    assert b.launch_eligible
    pay = b.artifacts["outcomes.payment_policy"].artifact.body
    assert pay.base_cents == 350  # type: ignore[union-attr]
    assert pay.bonus.type == "none"  # type: ignore[union-attr]
    text = b.artifacts["instruments.consent"].artifact.body.text  # type: ignore[union-attr]
    flat = " ".join(p for s in text.sections for p in s.paragraphs)
    assert BONUS_SENTENCE in flat
    assert OLD_OFFER not in flat
    assert "$3.50" not in flat  # the offer is described as 3.00 + bonus, never flat
    assert b.artifacts["instruments.consent"].key == "consent.reference@v8"
    assert b.artifacts["instruments.training"].key == "training.reference@v4"
    assert b.artifacts["instruments.debrief"].key == "debrief.tp_reference@v3"


@pytest.mark.parametrize("manifest_id", NEW)
def test_training_v4_records_the_comprehension_gate(store: ContentStore, manifest_id: str) -> None:
    assert manifest_id  # binding check lives in the consent test above
    art = store.artifacts["training.reference@v4"]
    joined = " ".join(getattr(art, "notes", None) or getattr(art.artifact, "notes", []))
    assert "comprehension" in joined.lower()


def test_predecessors_still_bind_their_pins(store: ContentStore) -> None:
    for mid, ckey, tkey in (
        ("tp_ai_v7", "consent.reference@v7", "training.reference@v3"),
        ("tp_scripted_v9", "consent.reference@v7", "training.reference@v3"),
    ):
        b = bind(store, mid, mode="live")
        assert b.artifacts["instruments.consent"].key == ckey
        assert b.artifacts["instruments.training"].key == tkey
