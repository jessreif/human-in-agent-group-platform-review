"""v15 mixed arm (2026-10-17) against disposable PostgreSQL: tp_ai_ovsome_thin_v9
allocates ``high`` and ``mixed`` cells; under ``mixed`` the check-in reveals one
heavy-workload teammate (6) and one light (2), and each remark line is picked by
the *speaker's* level from the recorded assigned_values."""

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

V15 = "tp_ai_ovsome_thin_v9"

REMARK1 = {
    "high": "Yikes this workload is heavy, I don't think I am going to finish on time",
    "low": "Ok this workload seems light, I think I'll finish with time to spare",
}
REMARK2_MIXED = {
    "high": "ugh lucky, mine's heavy, don't think I'll finish",
    "low": "mine's actually pretty light, think I'll finish early",
}
REMARK2_HIGH = "i know right!"


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _arm(db: MigratedDatabase, sid: uuid.UUID) -> str:
    with db.runtime.connect() as conn:
        return str(
            conn.execute(select(s.session.c.arm).where(s.session.c.session_id == sid)).scalar_one()
        )


def _assigned(db: MigratedDatabase, sid: uuid.UUID) -> dict[str, int]:
    with db.runtime.connect() as conn:
        row = conn.execute(
            select(s.manipulation_exposure.c.assigned_values).where(
                s.manipulation_exposure.c.session_id == sid,
                s.manipulation_exposure.c.kind == "pressure_checkin",
            )
        ).first()
    assert row is not None
    return dict(row[0])


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


def _drive_through_checkin(
    db: MigratedDatabase, store: ContentStore, sid: uuid.UUID, writer: uuid.UUID
) -> None:
    ah.set_active(db, sid, 60_000)
    ah.tick(db, store)
    assert ah.stage(db, sid)[0] == "checkin_collect"
    ah.run(db, store, sid, writer, "pressure_response", {"value": 4}, ah.stage(db, sid)[1])
    ah.force_wall_due(db, sid, "checkin_reveal")
    ah.tick(db, store)
    assert ah.stage(db, sid)[0] == "checkin_reveal"
    ah.run(db, store, sid, writer, "proceed", {}, ah.stage(db, sid)[1])
    assert ah.stage(db, sid)[0] == "task"


def _drive_in_seeded_batch(db: MigratedDatabase, store: ContentStore, pid: str, batch: str) -> Any:
    """drive_to_task without re-seeding — for >1 session in the same batch."""
    result = ah.enter(
        db.runtime,
        store,
        query=ah.query(pid, batch),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=ah.BUILD_SHA,
    )
    sid, writer = result.session_id, result.writer_id
    consent_key = ah.bind(store, V15, mode="qa").artifacts["instruments.consent"].key
    ah.run(
        db,
        store,
        sid,
        writer,
        "consent",
        {"action": "consented", "text_version": consent_key},
        1,
    )
    ah.run(db, store, sid, writer, "training_step", {"step": 1}, ah.stage(db, sid)[1])
    ah.run(
        db,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "share", "record_id": "PRACTICE-K3-04"},
        ah.stage(db, sid)[1],
    )
    ah.run(
        db,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "pick_location", "crate": 3, "location": "The Sawmill"},
        ah.stage(db, sid)[1],
    )
    ah.run(
        db,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "ask", "text": "anything on crate 3?"},
        ah.stage(db, sid)[1],
    )
    ah.run(
        db,
        store,
        sid,
        writer,
        "training_step",
        {
            "step": "comprehension",
            "complete": True,
            "answers": dict(ah.lifecycle.COMPREHENSION_KEY),
        },
        ah.stage(db, sid)[1],
    )
    ah.drive_connecting(db, store, sid, writer)
    return result


def test_v15_arms_assign_and_remark_by_seat_level(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """Two sessions in one batch cover both v15 cells (permuted block of 2)."""
    ah.seed_batch(migrated, store, V15, "b-v15-mixed")
    results = [_drive_in_seeded_batch(migrated, store, f"V15-{i}", "b-v15-mixed") for i in range(2)]
    by_arm = {_arm(migrated, r.session_id): r for r in results}
    assert set(by_arm) == {"high", "mixed"}, set(by_arm)

    for arm, result in by_arm.items():
        sid, writer = result.session_id, result.writer_id
        _drive_through_checkin(migrated, store, sid, writer)
        assigned = _assigned(migrated, sid)
        if arm == "high":
            assert sorted(assigned.values()) == [6, 7]
        else:
            assert sorted(assigned.values()) == [2, 6]

        ah.set_active(migrated, sid, 75_500)
        ah.tick(migrated, store, n=4)
        ah.set_active(migrated, sid, 83_500)
        ah.tick(migrated, store, n=4)
        delivered = sorted(
            _events(migrated, sid, "pressure_remark_delivered"), key=lambda r: r.payload["index"]
        )
        assert [r.payload["index"] for r in delivered] == [0, 1]
        seats = [r.payload["seat"] for r in delivered]
        assert set(seats) == {"JT", "PM"}

        expected = (
            [
                REMARK1["high"],
                REMARK2_HIGH,
            ]
            if arm == "high"
            else [
                REMARK1["high" if assigned[seats[0]] == 6 else "low"],
                REMARK2_MIXED["high" if assigned[seats[1]] == 6 else "low"],
            ]
        )
        lines = [(a, b) for a, b in _team_lines(migrated, sid) if b in expected]
        assert lines == [(seats[0], expected[0]), (seats[1], expected[1])]
