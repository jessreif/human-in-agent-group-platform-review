"""2026-10-10 ask-routing revision against disposable PostgreSQL.

Live-log review of the v8 fielding showed asks dying at the classifier —
multi-crate text ("where is crate 3, 4 and 6?") and bare-location shapes
("7 sawmill?") resolved at most one crate or none and fell through to social
generation, which answered with polite acknowledgements that hid the miss.
This revision:

- fans a multi-crate ask out to one request per named crate (cap 4) — the
  clarifier alternative would cost a pressured participant 10-20 s of a
  5-minute task;
- delivers ONE card per asked crate on a broadcast ask — the newest record
  across both seats' holdings, attributed to its holder (the two-card pair
  used to arrive newest-first, burying the decisive entry);
- routes intel-flavoured text naming no crate to the deterministic
  "name the crate" invite instead of a generated acknowledgement;
- keeps the typed practice ask as *optional* practice (UX_Feedback_v9,
  2026-10-12): invalid text is still rejected with ``practice_ask_invalid``
  and a valid ask still returns the canned card, but completion gates on the
  practice share alone — the ``practice_ask_required`` gate is retired.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy import select

from app.db import schema as s
from app.domain.manifests.store import ContentStore
from app.services import lifecycle
from tests.integration.test_o_decisions import (
    _deliver_all,
    _file_membership,
    _messages,
    _requests,
    _say,
    _seat_crates,
)
from tests.integration.test_wp06_engines import _drive_to_task, _jobs, _run, _stage

from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _jobs_for(db: MigratedDatabase, sid: uuid.UUID) -> list[Mapping[str, Any]]:
    return [dict(j) for j in _jobs(db, sid)]


# ----- multi-crate fan-out ------------------------------------------------------


def test_multi_crate_ask_fans_out_one_request_per_crate(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_nl_v2", "R1", "b-r1")
    sid, writer = result.session_id, result.writer_id
    out = _say(migrated, store, sid, writer, "where is crate 2, 4 and 6?")
    request_ids = {uuid.UUID(r) for r in out.result["request_ids"]}
    assert len(request_ids) == 3
    reqs = [r for r in _requests(migrated, sid) if r["request_id"] in request_ids]
    assert {r["crate"] for r in reqs} == {2, 4, 6}
    assert all(r["kind"] == "free_text" for r in reqs)


def test_bare_location_shape_ask_fans_out(migrated: MigratedDatabase, store: ContentStore) -> None:
    """The observed lost message (913b38af): location guesses with a trailing
    '?' name every crate but used to produce only acknowledgements."""
    result = _drive_to_task(migrated, store, "tp_demo_nl_v2", "R2", "b-r2")
    sid, writer = result.session_id, result.writer_id
    out = _say(migrated, store, sid, writer, "3 cannery. 4 blackwell, 6 ridgeline, 7 sawmill?")
    request_ids = {uuid.UUID(r) for r in out.result["request_ids"]}
    reqs = [r for r in _requests(migrated, sid) if r["request_id"] in request_ids]
    assert {r["crate"] for r in reqs} == {3, 4, 6, 7}


def test_fan_out_is_capped_at_four_crates(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_nl_v2", "R3", "b-r3")
    sid, writer = result.session_id, result.writer_id
    out = _say(migrated, store, sid, writer, "what do you have on 1, 2, 3, 4, 5 and 6?")
    request_ids = {uuid.UUID(r) for r in out.result["request_ids"]}
    assert len(request_ids) == 4


def test_addressed_multi_crate_ask_stays_on_the_named_seat(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_nl_v2", "R4", "b-r4")
    sid, writer = result.session_id, result.writer_id
    out = _say(migrated, store, sid, writer, "jt, what about crates 3 and 5?")
    request_ids = {uuid.UUID(r) for r in out.result["request_ids"]}
    reqs = [r for r in _requests(migrated, sid) if r["request_id"] in request_ids]
    assert {r["crate"] for r in reqs} == {3, 5}
    assert {r["responder"] for r in reqs} == {"JT"}


def test_fanned_out_asks_deliver_cards(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_nl_v2", "R5", "b-r5")
    sid, writer = result.session_id, result.writer_id
    crates = _seat_crates(migrated, sid)
    held = sorted(c for c, h in crates.items() if h - {"participant"})
    targets = held[:2]
    out = _say(migrated, store, sid, writer, f"anything on crates {targets[0]} and {targets[1]}?")
    request_ids = {uuid.UUID(r) for r in out.result["request_ids"]}
    _deliver_all(migrated, store, sid)
    cards = [m for m in _messages(migrated, sid) if m["request_id"] in request_ids]
    assert len(cards) == len(targets)
    assert all(m["record_id"] is not None for m in cards)
    added = {r["record_id"] for r in _file_membership(migrated, sid)}
    assert {m["record_id"] for m in cards} <= added


# ----- unresolved intel asks -----------------------------------------------------


def test_crateless_intel_ask_gets_the_invite_not_an_acknowledgement(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """ "any updates?" names no crate — both seats reply with the deterministic
    asks_intel_nl line (generation=None, never the model's 'noting it')."""
    result = _drive_to_task(migrated, store, "tp_demo_nl_v2", "R6", "b-r6")
    sid, writer = result.session_id, result.writer_id
    out = _say(migrated, store, sid, writer, "any updates?")
    request_ids = {str(r) for r in out.result["request_ids"]}
    jobs = [j for j in _jobs_for(migrated, sid) if j["payload"].get("request_id") in request_ids]
    assert jobs
    # No generate job exists for these requests — the deterministic line is
    # scheduled verbatim, never the model's "noting it".
    assert {j["kind"] for j in jobs} == {"deliver"}
    assert all(j["payload"].get("verbatim") for j in jobs)
    assert {j["payload"].get("grounded") for j in jobs} == {"asks_intel_nl"}
    _deliver_all(migrated, store, sid)
    replies = [m for m in _messages(migrated, sid) if str(m["request_id"]) in request_ids]
    assert {m["actor"] for m in replies} == {"JT", "PM"}
    assert all("crate" in m["body"].lower() for m in replies)
    with migrated.runtime.connect() as conn:
        calls = conn.execute(select(s.model_call).where(s.model_call.c.session_id == sid)).all()
    assert not calls


# ----- practice ask gate ---------------------------------------------------------


def _drive_to_training(
    db: MigratedDatabase, store: ContentStore, manifest_id: str, pid: str, batch: str
) -> tuple[uuid.UUID, uuid.UUID]:
    """Entry + consent + instructions, stopping inside the training stage."""
    from tests.integration.audit_helpers import run as ah_run
    from tests.integration.test_wp09_experience import _preview_entry
    from tests.integration.test_wp09_experience import _stage as _st

    result = _preview_entry(db, store, manifest_id, "high", nonce_seed=batch)
    sid, writer = result.session_id, result.writer_id
    with db.runtime.connect() as conn:
        sess = conn.execute(select(s.session).where(s.session.c.session_id == sid)).mappings().one()
        bound = lifecycle.bound_for_session(conn, store, dict(sess))
    ah_run(
        db,
        store,
        sid,
        writer,
        "consent",
        {
            "action": "consented",
            "text_version": bound.artifacts["instruments.consent"].key,
        },
        _st(db, sid)[1],
    )
    ah_run(db, store, sid, writer, "training_step", {"step": "instructions"}, _st(db, sid)[1])
    return sid, writer


def test_practice_share_alone_unlocks_completion_on_nl_only(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """UX_Feedback_v9 (2026-10-12): the typed ask is optional — a committed
    practice share plus a zero-miss comprehension pass completes training on
    nl_only builds with no ask at all."""
    sid, writer = _drive_to_training(migrated, store, "tp_demo_nl_v2", "R7", "b7")
    _run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "share", "record_id": "PRACTICE-K3-04"},
        _stage(migrated, sid)[1],
    )
    _run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "pick_location", "crate": 3, "location": "The Sawmill"},
        _stage(migrated, sid)[1],
    )
    done = _run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {
            "step": "comprehension",
            "complete": True,
            "answers": dict(lifecycle.COMPREHENSION_KEY),
        },
        _stage(migrated, sid)[1],
    )
    assert done.result["stage"] == "scripted_connecting"


def test_optional_practice_ask_still_validated_and_answered(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """An attempted ask keeps its contract: non-ask text is rejected
    (``practice_ask_invalid``) and a real ask returns the generic-labelled
    canned card."""
    sid, writer = _drive_to_training(migrated, store, "tp_demo_nl_v2", "R7B", "b7b")
    invalid = _run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "ask", "text": "hello"},
        _stage(migrated, sid)[1],
        accept=False,
    )
    assert not invalid.accepted and invalid.reason == "practice_ask_invalid"
    asked = _run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "ask", "text": "anything on crate 3?"},
        _stage(migrated, sid)[1],
    )
    assert asked.accepted
    card = asked.result["card"]
    # UX v7: the practice card is labelled generically — a seat initial in
    # practice would leak that the teammate names are scripted fixtures.
    assert card["crate"] == 3 and card["shared_by"] == "Teammate"


def test_practice_share_alone_unlocks_completion_on_structured(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """Structured-mode profiles keep the request picker — completion gates on
    the share alone there as well."""
    sid, writer = _drive_to_training(migrated, store, "tp_demo_v6", "R8", "b8")
    _run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "share", "record_id": "PRACTICE-K3-04"},
        _stage(migrated, sid)[1],
    )
    _run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "pick_location", "crate": 3, "location": "The Sawmill"},
        _stage(migrated, sid)[1],
    )
    done = _run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {
            "step": "comprehension",
            "complete": True,
            "answers": dict(lifecycle.COMPREHENSION_KEY),
        },
        _stage(migrated, sid)[1],
    )
    assert done.result["stage"] == "scripted_connecting"
