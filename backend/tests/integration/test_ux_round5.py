"""UX round 5 (UX_Feedback_v5.pdf) backend regression tests — disposable PostgreSQL.

Items 1-2: a teammate's structured share committed the ``file_membership`` row
but never emitted the ``record_added_to_file`` event (the participant allowlist
entry existed, unused). The only projected signal was ``message_delivered`` —
so a live client rendered the chat card before any workbench refresh and fell
back to "Attached record <id>" until the next stage transition (observed:
bare record ids pre-check-in, correct cards after; missing case-file entries).
The fix emits ``record_added_to_file`` carrying the full record projection in
the same commit as the share.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import schema as s
from app.domain.manifests.store import ContentStore
from app.services.projection import project_event
from tests.integration.test_wp06_engines import (
    _drive_to_task,
    _events,
    _job,
    _run,
    _set_active,
    _stage,
    _tick,
)

from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _delivered_share(db: MigratedDatabase, store: ContentStore) -> tuple:
    """Drive a session to task and deliver one structured teammate share."""
    result = _drive_to_task(db, store, "tp_demo_v3", "UXR5A", "b-uxr5")
    sid, writer = result.session_id, result.writer_id
    asked = None
    for c in range(1, 9):
        out = _run(
            db,
            store,
            sid,
            writer,
            "teammate_request",
            {"crate": c},
            _stage(db, sid)[1],
            accept=False,
        )
        if out.accepted:
            asked = c
            break
    assert asked is not None
    deliver = _job(db, sid, "deliver")
    assert deliver is not None
    _set_active(db, sid, int(deliver["due_active_ms"]) + 50)
    _tick(db, store)
    return sid, asked


def test_teammate_share_emits_record_added_to_file(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    sid, crate = _delivered_share(migrated, store)

    shares = [
        m
        for m in _events(migrated, sid, "message_delivered")
        if m["payload"].get("record_id") is not None
    ]
    assert len(shares) == 1
    share_seq = shares[0]["seq"]
    record_id = shares[0]["payload"]["record_id"]

    added = _events(migrated, sid, "record_added_to_file")
    assert len(added) == 1, "the membership change must be emitted, not just committed"
    ev = added[0]
    # Ordering: the event rides the same commit as the message, so a client
    # applying events in seq order sees it immediately after the card.
    assert ev["seq"] > share_seq
    payload = ev["payload"]
    assert payload["record_id"] == record_id
    assert payload["crate"] == crate
    assert payload["source"] == "added_from_team"
    assert payload["shared_by"] in ("JT", "PM")
    # The full card body must ride the event so the client can apply it without
    # a fetch round-trip.
    assert payload["body"] and payload["stamp"] and payload["event_type"]


def test_record_added_to_file_reaches_the_participant_projection(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    sid, _crate = _delivered_share(migrated, store)
    ev = _events(migrated, sid, "record_added_to_file")[0]
    projected = project_event(ev)
    assert projected is not None
    for key in ("record_id", "crate", "stamp", "body", "event_type", "source", "shared_by"):
        assert key in projected["payload"], f"projection dropped {key}"
    # Ordering on the wire mirrors the ledger.
    with migrated.runtime.connect() as conn:
        msg_seq = conn.execute(
            select(s.event.c.seq).where(
                s.event.c.session_id == sid,
                s.event.c.type == "message_delivered",
                s.event.c.payload["record_id"].astext == ev["payload"]["record_id"],
            )
        ).scalar_one()
    assert projected["seq"] > msg_seq
