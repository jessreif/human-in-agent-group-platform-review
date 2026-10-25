"""UX round 8 (2026-10-11) + BA-105 (2026-10-12): the open-ended follow-up stage.

TP sessions on manifests binding ``instruments.open_ended`` stop at
``open_ended`` between ``post_questionnaire`` and ``debrief`` — the first
question submits before the second, both persist through the generic
item_response machinery under the bound artifact id, and exports carry the
text. PE sessions keep the direct post_questionnaire -> debrief edge.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, update

from app.db import export as db_export
from app.db import schema as s
from app.domain.manifests.store import ContentStore

from . import audit_helpers as ah
from .conftest import MigratedDatabase


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _to_open_ended(
    db: MigratedDatabase, store: ContentStore, manifest_id: str, pid: str, batch: str
):
    """Drive a TP session to the open-ended stage: task -> (SQL jump)
    answers_locked -> proceed -> confidence -> proceed -> post_questionnaire ->
    all post items skipped -> open_ended. Requires a manifest that binds
    ``instruments.open_ended`` (BA-105) — unbound profiles skip the stage."""
    result = ah.drive_to_task(db, store, manifest_id, pid, batch)
    sid, writer = result.session_id, result.writer_id
    with db.runtime.begin() as conn:
        conn.execute(
            update(s.session).where(s.session.c.session_id == sid).values(stage="answers_locked")
        )
    ah.run(db, store, sid, writer, "proceed", {}, ah.stage(db, sid)[1])
    assert ah.stage(db, sid)[0] == "confidence"
    # Zero answered crates: confidence completes with nothing to rate.
    ah.run(db, store, sid, writer, "proceed", {}, ah.stage(db, sid)[1])
    assert ah.stage(db, sid)[0] == "post_questionnaire"
    # post_task.tp_demo@v5 items are required (skippable=false) — answer, never
    # skip, all 11 (tp_post_10 is the choice item; tp_post_11 slots after 09).
    for i in (*range(1, 10), 11):
        ah.run(
            db,
            store,
            sid,
            writer,
            "item_response",
            {"item_id": f"tp_post_{i:02d}", "value": 5},
            ah.stage(db, sid)[1],
        )
    ah.run(
        db,
        store,
        sid,
        writer,
        "item_response",
        {"item_id": "tp_post_10", "value": "All were human"},
        ah.stage(db, sid)[1],
    )
    assert ah.stage(db, sid)[0] == "open_ended"
    return result


@pytest.mark.integration
def test_open_ended_sequential_items_persist_and_advance(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _to_open_ended(migrated, store, "tp_ai_v13", "OE1", "qa-oe1")
    sid, writer = result.session_id, result.writer_id

    # Required items refuse the skip affordance server-side.
    out = ah.run(
        migrated,
        store,
        sid,
        writer,
        "item_skip",
        {"item_id": "crate_strategy"},
        ah.stage(migrated, sid)[1],
        accept=False,
    )
    assert not out.accepted and out.reason == "skip_not_permitted"
    # Undeclared items reject.
    out = ah.run(
        migrated,
        store,
        sid,
        writer,
        "item_response",
        {"item_id": "nope", "value": "x"},
        ah.stage(migrated, sid)[1],
        accept=False,
    )
    assert not out.accepted and out.reason == "unknown_item"
    # Empty / non-text / oversized responses reject; a seat is out of place.
    for bad in ("", "   ", 7, {"v": "x"}, "x" * 4001):
        out = ah.run(
            migrated,
            store,
            sid,
            writer,
            "item_response",
            {"item_id": "crate_strategy", "value": bad},
            ah.stage(migrated, sid)[1],
            accept=False,
        )
        assert not out.accepted and out.reason in ("invalid_open_response", "missing_value"), bad
    out = ah.run(
        migrated,
        store,
        sid,
        writer,
        "item_response",
        {"item_id": "crate_strategy", "value": "ok", "seat": "JT"},
        ah.stage(migrated, sid)[1],
        accept=False,
    )
    assert not out.accepted and out.reason == "unexpected_seat"
    # Proceed is legal only once every required item is resolved.
    out = ah.run(
        migrated, store, sid, writer, "proceed", {}, ah.stage(migrated, sid)[1], accept=False
    )
    assert not out.accepted and out.reason == "instrument_incomplete"

    # Q1 commits, stage holds for Q2.
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "item_response",
        {"item_id": "crate_strategy", "value": "I placed each crate by its file."},
        ah.stage(migrated, sid)[1],
    )
    assert ah.stage(migrated, sid)[0] == "open_ended"

    # Q2 commits and the stage advances into debrief.
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "item_response",
        {"item_id": "source_reliance", "value": "File first, then the chat."},
        ah.stage(migrated, sid)[1],
    )
    assert ah.stage(migrated, sid)[0] == "debrief"

    # Persistence: generic item_response rows under the bound artifact id.
    with migrated.runtime.connect() as conn:
        rows = (
            conn.execute(
                select(
                    s.item_response.c.item_id,
                    s.item_response.c.instrument_id,
                    s.item_response.c.instrument_version,
                    s.item_response.c.response,
                    s.item_response.c.status,
                ).where(s.item_response.c.session_id == sid)
            )
            .mappings()
            .all()
        )
        oe = {r["item_id"]: r for r in rows if r["instrument_id"] == "open_ended.tp_followup"}
    assert set(oe) == {"crate_strategy", "source_reliance"}
    assert all(r["instrument_version"] == 1 and r["status"] == "answered" for r in oe.values())
    assert oe["crate_strategy"]["response"]["value"] == "I placed each crate by its file."
    assert oe["source_reliance"]["response"]["value"] == "File first, then the chat."

    # The ledger shows the TP detour in order.
    with migrated.runtime.connect() as conn:
        trs = conn.execute(
            select(s.stage_transition.c.from_stage, s.stage_transition.c.to_stage).where(
                s.stage_transition.c.session_id == sid,
                s.stage_transition.c.from_stage.in_(("post_questionnaire", "open_ended")),
            )
        ).all()
    assert [(r.from_stage, r.to_stage) for r in trs] == [
        ("post_questionnaire", "open_ended"),
        ("open_ended", "debrief"),
    ]

    # Exports carry the free-text rows.
    snap = db_export.export_session(migrated.runtime, uuid.UUID(str(sid)))
    oe_rows = [r for r in snap["item_response"] if r["instrument_id"] == "open_ended.tp_followup"]
    assert len(oe_rows) == 2


@pytest.mark.integration
def test_pe_post_questionnaire_still_goes_straight_to_debrief(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = ah.drive_to_task(migrated, store, "pe_demo_v3", "OE2", "qa-oe2")
    sid, writer = result.session_id, result.writer_id
    with migrated.runtime.begin() as conn:
        conn.execute(
            update(s.session).where(s.session.c.session_id == sid).values(stage="answers_locked")
        )
    ah.run(migrated, store, sid, writer, "proceed", {}, ah.stage(migrated, sid)[1])
    assert ah.stage(migrated, sid)[0] == "confidence"
    ah.run(migrated, store, sid, writer, "proceed", {}, ah.stage(migrated, sid)[1])
    # PE: confidence -> peer_ratings -> post_questionnaire.
    assert ah.stage(migrated, sid)[0] == "peer_ratings"
    for seat in ("JT", "PM"):
        ah.run(
            migrated,
            store,
            sid,
            writer,
            "item_response",
            {"item_id": "peer_helpfulness", "value": 5, "seat": seat},
            ah.stage(migrated, sid)[1],
        )
    assert ah.stage(migrated, sid)[0] == "post_questionnaire"
    # Skipping every declared post-task item completes the instrument — PE's
    # stage graph has no open_ended, so the last skip lands on debrief.
    for i in range(1, 10):
        ah.run(
            migrated,
            store,
            sid,
            writer,
            "item_skip",
            {"item_id": f"pe_post_{i:02d}"},
            ah.stage(migrated, sid)[1],
        )
    assert ah.stage(migrated, sid)[0] == "debrief"
