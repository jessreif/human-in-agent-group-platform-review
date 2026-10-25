"""Shared helpers for the BA audit test files (test_audit_ba*.py).

Mirrors the drive-to-task scaffolding from ``test_wp06_engines.py`` so the audit
remediation tests exercise the same durable machinery — active time is driven by
setting the open running interval's ``active_ms_at_start``, and job ``claimable_at``
is reset alongside it because that column is only a wall-time estimate.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy import func, insert, select, text, update

from app.db import schema as s
from app.db.tx import Command, CommandResult, TxContext, execute_command
from app.domain.manifests.binding import bind
from app.domain.manifests.store import ContentStore
from app.services import activetime, lifecycle, scheduler
from app.services.entry import EntryResult, enter
from app.services.sessions import register_manifest

from .conftest import MigratedDatabase

BUILD_SHA = "ab" * 32
PREVIEW_KEY = b"k" * 32


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def seed_batch(db: MigratedDatabase, store: ContentStore, manifest_id: str, batch_id: str) -> None:
    """Register the manifest and open a QA batch for it (as the migrator/superuser)."""
    bound = bind(store, manifest_id, mode="qa")
    with db.superuser.begin() as conn:
        register_manifest(conn, bound)
        conn.execute(
            insert(s.batch).values(
                batch_id=batch_id,
                study_key=bound.manifest.study_key,
                manifest_id=bound.manifest_id,
                mode="qa",
                enrollment_open=True,
            )
        )


def query(pid: str, batch: str) -> dict[str, str]:
    return {"PROLIFIC_PID": pid, "STUDY_ID": "s", "SESSION_ID": "x", "batch": batch}


def cmd(session_id: uuid.UUID, kind: str, payload: dict[str, Any], revision: int) -> Command:
    return Command(
        command_id=uuid.uuid4(),
        session_id=session_id,
        kind=kind,
        expected_revision=revision,
        payload=payload,
    )


def _advance(store: ContentStore):
    def advance(ctx: TxContext) -> None:
        bound = lifecycle.bound_for_session(ctx.conn, store, ctx.session)
        scheduler.advance_due(ctx, bound)

    return advance


def run(
    db: MigratedDatabase,
    store: ContentStore,
    sid: uuid.UUID,
    writer: uuid.UUID,
    kind: str,
    payload: dict[str, Any],
    revision: int,
    *,
    accept: bool = True,
) -> CommandResult:
    out = execute_command(
        db.runtime,
        cmd(sid, kind, payload, revision),
        lifecycle.handler_for(kind, store=store, behavior_sha=BUILD_SHA, writer=writer),
        advance_due=_advance(store),
    )
    if accept:
        assert out.accepted, (kind, out.reason)
    return out


def stage(db: MigratedDatabase, sid: uuid.UUID) -> tuple[str, int]:
    with db.runtime.connect() as conn:
        row = conn.execute(
            select(s.session.c.stage, s.session.c.revision).where(s.session.c.session_id == sid)
        ).one()
        return row.stage, row.revision


def drive_to_task(
    db: MigratedDatabase,
    store: ContentStore,
    manifest_id: str,
    pid: str,
    batch: str,
) -> EntryResult:
    seed_batch(db, store, manifest_id, batch)
    result = enter(
        db.runtime,
        store,
        query=query(pid, batch),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    sid, writer = result.session_id, result.writer_id
    # BA-37: the recorded text version must be the manifest's bound consent key.
    consent_key = bind(store, manifest_id, mode="qa").artifacts["instruments.consent"].key
    run(
        db,
        store,
        sid,
        writer,
        "consent",
        {"action": "consented", "text_version": consent_key},
        1,
    )
    run(db, store, sid, writer, "training_step", {"step": 1}, stage(db, sid)[1])
    # BA-95: completion requires a committed practice share first.
    run(
        db,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "share", "record_id": "PRACTICE-K3-04"},
        stage(db, sid)[1],
    )
    run(
        db,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "pick_location", "crate": 3, "location": "The Sawmill"},
        stage(db, sid)[1],
    )

    run(
        db,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "ask", "text": "anything on crate 3?"},
        stage(db, sid)[1],
    )
    run(
        db,
        store,
        sid,
        writer,
        "training_step",
        {
            "step": "comprehension",
            "complete": True,
            "answers": dict(lifecycle.COMPREHENSION_KEY),
        },
        stage(db, sid)[1],
    )
    drive_connecting(db, store, sid, writer)
    return result


def drive_connecting(
    db: MigratedDatabase, store: ContentStore, sid: uuid.UUID, writer: uuid.UUID
) -> None:
    """PM joins at the wall-clock connecting job; task opens when the hold job commits."""
    force_wall_due(db, sid, "connecting")
    tick(db, store)
    run(db, store, sid, writer, "ready", {}, stage(db, sid)[1])
    assert stage(db, sid)[0] == "ready"
    force_wall_due(db, sid, "task_open")
    tick(db, store)
    assert stage(db, sid)[0] in ("task", "solo_review")


def set_active(db: MigratedDatabase, sid: uuid.UUID, active_ms: int) -> None:
    """Jump the session's open running interval to ``active_ms`` elapsed."""
    with db.runtime.begin() as conn:
        conn.execute(
            text(
                "UPDATE app.clock_interval "
                "SET active_ms_at_start = :ms, started_at = clock_timestamp() "
                " WHERE session_id = :sid AND ended_at IS NULL AND kind = 'running'"
            ),
            {"ms": active_ms, "sid": sid},
        )
        conn.execute(
            text(
                "UPDATE app.scheduled_job SET claimable_at = clock_timestamp() "
                " WHERE session_id = :sid AND status IN ('pending') "
                " AND clock_basis = 'active_task'"
            ),
            {"sid": sid},
        )


def active(db: MigratedDatabase, sid: uuid.UUID) -> int:
    with db.runtime.connect() as conn:
        return activetime.active_ms(
            conn, sid, conn.execute(text("SELECT clock_timestamp()")).scalar_one()
        )


def tick(
    db: MigratedDatabase,
    store: ContentStore,
    *,
    worker: str = "w1",
    n: int = 1,
    engine: Any = None,
) -> int:
    total = 0
    for _ in range(n):
        committed = scheduler.tick(
            engine or db.runtime, store, worker_id=worker, provider=None, limit=25
        )
        if committed == 0:
            break
        total += committed
    return total


def force_wall_due(db: MigratedDatabase, sid: uuid.UUID, kind: str) -> None:
    with db.runtime.begin() as conn:
        conn.execute(
            update(s.scheduled_job)
            .where(
                s.scheduled_job.c.session_id == sid,
                s.scheduled_job.c.kind == kind,
                s.scheduled_job.c.status == "pending",
            )
            .values(due_wall_at=func.now(), claimable_at=func.now())
        )


def jobs(db: MigratedDatabase, sid: uuid.UUID) -> list[Mapping[str, Any]]:
    with db.runtime.connect() as conn:
        return (
            conn.execute(select(s.scheduled_job).where(s.scheduled_job.c.session_id == sid))
            .mappings()
            .all()
        )


def job(db: MigratedDatabase, sid: uuid.UUID, kind: str) -> Mapping[str, Any] | None:
    return next((j for j in jobs(db, sid) if j["kind"] == kind), None)


def messages(db: MigratedDatabase, sid: uuid.UUID) -> list[Mapping[str, Any]]:
    with db.runtime.connect() as conn:
        return conn.execute(select(s.message).where(s.message.c.session_id == sid)).mappings().all()


def events(db: MigratedDatabase, sid: uuid.UUID, type_: str) -> list[Mapping[str, Any]]:
    with db.runtime.connect() as conn:
        return (
            conn.execute(
                select(s.event).where(s.event.c.session_id == sid, s.event.c.type == type_)
            )
            .mappings()
            .all()
        )
