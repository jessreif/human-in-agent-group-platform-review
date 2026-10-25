"""2026-10-09 pilot revision against disposable PostgreSQL: the 300 s task, the pre-check-in
request at 45 s, and the two arm-specific teammate remarks at active 75/83 — delivered as
ordinary team-chat lines by a coin-drawn seat pair, identically in both teammate builds."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.db import schema as s
from app.domain.manifests.store import ContentStore

from . import audit_helpers as ah
from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration

LINES = {
    "high": (
        "Yikes this is a lot of info, I don't think I am going to finish on time",
        "i know right!",
    ),
    "low": (
        "Ok this seems manageable, I think I'll finish with time to spare",
        "me too! i think",
    ),
}


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _team_lines(db: MigratedDatabase, sid: uuid.UUID) -> list[Any]:
    with db.runtime.connect() as conn:
        return list(
            conn.execute(
                select(s.message.c.actor, s.message.c.body)
                .where(
                    s.message.c.session_id == sid,
                    s.message.c.channel == "team",
                    s.message.c.actor.in_(("JT", "PM")),
                )
                .order_by(s.message.c.accepted_seq)
            ).all()
        )


def _events(db: MigratedDatabase, sid: uuid.UUID, type_: str) -> list[Any]:
    with db.runtime.connect() as conn:
        return list(
            conn.execute(
                select(s.event.c.payload, s.event.c.active_ms).where(
                    s.event.c.session_id == sid, s.event.c.type == type_
                )
            ).all()
        )


def _arm(db: MigratedDatabase, sid: uuid.UUID) -> str:
    with db.runtime.connect() as conn:
        return str(
            conn.execute(select(s.session.c.arm).where(s.session.c.session_id == sid)).scalar_one()
        )


@pytest.mark.parametrize("manifest_id", ["tp_ai_v4", "tp_scripted_v6"])
def test_remarks_requests_and_budget(
    migrated: MigratedDatabase, store: ContentStore, manifest_id: str
) -> None:
    result = ah.drive_to_task(migrated, store, manifest_id, f"PR-{manifest_id}", f"b-{manifest_id}")
    sid, writer = result.session_id, result.writer_id
    arm = _arm(migrated, sid)
    first_line, second_line = LINES[arm]

    # The first scripted request lands before the check-in.
    ah.set_active(migrated, sid, 46_000)
    ah.tick(migrated, store, n=4)
    assert len(_events(migrated, sid, "scripted_request_delivered")) == 1
    assert ah.stage(migrated, sid)[0] == "task"

    # Check-in at 60; nothing remark-shaped can arrive while it holds the clock.
    ah.set_active(migrated, sid, 60_000)
    ah.tick(migrated, store)
    assert ah.stage(migrated, sid)[0] == "checkin_collect"
    ah.run(
        migrated, store, sid, writer, "pressure_response", {"value": 4}, ah.stage(migrated, sid)[1]
    )
    ah.force_wall_due(migrated, sid, "checkin_reveal")
    ah.tick(migrated, store)
    assert ah.stage(migrated, sid)[0] == "checkin_reveal"
    ah.run(migrated, store, sid, writer, "proceed", {}, ah.stage(migrated, sid)[1])
    assert ah.stage(migrated, sid)[0] == "task"

    ah.set_active(migrated, sid, 74_000)
    ah.tick(migrated, store, n=4)
    assert not _events(migrated, sid, "pressure_remark_delivered")

    ah.set_active(migrated, sid, 75_500)
    ah.tick(migrated, store, n=4)
    ah.set_active(migrated, sid, 83_500)
    ah.tick(migrated, store, n=4)
    delivered = sorted(
        _events(migrated, sid, "pressure_remark_delivered"), key=lambda r: r.payload["index"]
    )
    assert [r.payload["index"] for r in delivered] == [0, 1]
    seats = [r.payload["seat"] for r in delivered]
    assert set(seats) == {"JT", "PM"}  # first + the other seat
    assert 75_000 <= delivered[0].active_ms < 83_000 <= delivered[1].active_ms
    lines = [(a, b) for a, b in _team_lines(migrated, sid) if b in (first_line, second_line)]
    assert lines == [(seats[0], first_line), (seats[1], second_line)]
    # The other arm's wording never appears.
    other = LINES["low" if arm == "high" else "high"]
    assert not [b for _, b in _team_lines(migrated, sid) if b in other]

    # The seat coin is a logged draw.
    with migrated.runtime.connect() as conn:
        keys = list(
            conn.execute(
                select(s.draw_log.c.draw_key).where(
                    s.draw_log.c.session_id == sid, s.draw_log.c.stream == "jobs"
                )
            ).scalars()
        )
    assert any("pressure_remarks" in k and "first_seat" in k for k in keys), keys

    # The task freezes at the 300 s budget.
    ah.set_active(migrated, sid, 300_000)
    ah.tick(migrated, store, n=6)
    assert ah.stage(migrated, sid)[0] == "answers_locked"
