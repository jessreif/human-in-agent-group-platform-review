"""INV-0303/0307/0308: command idempotency, revisions, atomic event+outbox, jobs and leases on
real PostgreSQL, including concurrent callers."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from sqlalchemy import func, insert, select, text

from app.db import schema as s
from app.db.engine import make_engine
from app.db.tx import (
    Command,
    CommandConflictError,
    CommandResult,
    JobLeaseLostError,
    PersistenceUnavailableError,
    RejectionError,
    TxContext,
    claim_jobs,
    events_after,
    execute_command,
    finish_job,
    mark_dispatched,
    pending_outbox,
)
from app.services.sessions import create_session
from tests.integration.conftest import MigratedDatabase
from tests.wp02 import SEED, bound

pytestmark = pytest.mark.integration


@pytest.fixture
def session_id(migrated: MigratedDatabase) -> uuid.UUID:
    sid = create_session(
        migrated.runtime,
        bound=bound("tp_demo_v3"),
        batch_id="preview-wp03",
        research_pid=f"r-{uuid.uuid4().hex[:12]}",
        seed_hex=SEED,
        build_source_sha256="unset",
    )
    # WP06 (INV-0603): ``active_task`` jobs are claimable only while the session's open
    # interval is running and elapsed >= due_active_ms. Give the bare fixture session a
    # running clock already past the 300 s the tests schedule at.
    with migrated.runtime.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app.clock_interval"
                " (interval_id, session_id, kind, reason, active_ms_at_start)"
                " VALUES (:iid, :sid, 'running', 'task_start', 300000)"
            ),
            {"iid": uuid.uuid4(), "sid": sid},
        )
    return sid


def note(ctx: TxContext, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    ref = ctx.append_event("test.note", actor="participant", payload=dict(payload))
    return {"seq": ref.seq}


def cmd(session_id: uuid.UUID, revision: int, *, kind: str = "note", **payload: Any) -> Command:
    return Command(uuid.uuid4(), session_id, kind, revision, payload)


def _count(db: MigratedDatabase, table: Any, session_id: uuid.UUID) -> int:
    with db.runtime.connect() as conn:
        value: int = conn.execute(
            select(func.count()).select_from(table).where(table.c.session_id == session_id)
        ).scalar_one()
        return value


def _revision(db: MigratedDatabase, session_id: uuid.UUID) -> int:
    with db.runtime.connect() as conn:
        value: int = conn.execute(
            select(s.session.c.revision).where(s.session.c.session_id == session_id)
        ).scalar_one()
        return value


def test_command_commits_event_outbox_and_receipt_atomically(
    migrated: MigratedDatabase, session_id: uuid.UUID
) -> None:
    result = execute_command(migrated.runtime, cmd(session_id, 0, text="hi"), note)
    assert result.accepted and not result.replayed
    assert result.committed_revision == 1 and result.event_seqs == (1,)
    assert _count(migrated, s.event, session_id) == 1
    assert _count(migrated, s.outbox, session_id) == 1
    assert _count(migrated, s.command_receipt, session_id) == 1
    (event,) = events_after(migrated.runtime, session_id, 0)
    assert event["command_id"] == result.command_id and event["payload"] == {"text": "hi"}


def test_retry_returns_the_original_result_without_new_writes(
    migrated: MigratedDatabase, session_id: uuid.UUID
) -> None:
    command = cmd(session_id, 0, text="once")
    first = execute_command(migrated.runtime, command, note)
    again = execute_command(migrated.runtime, command, note)
    assert again.replayed and again.accepted
    assert (again.committed_revision, again.event_seqs, dict(again.result)) == (
        first.committed_revision,
        first.event_seqs,
        dict(first.result),
    )
    assert _count(migrated, s.event, session_id) == 1
    assert _revision(migrated, session_id) == 1


def test_command_id_reuse_is_a_conflict(migrated: MigratedDatabase, session_id: uuid.UUID) -> None:
    command = cmd(session_id, 0)
    execute_command(migrated.runtime, command, note)
    with pytest.raises(CommandConflictError):
        execute_command(
            migrated.runtime, Command(command.command_id, session_id, "other_kind", 1), note
        )
    other = create_session(
        migrated.runtime,
        bound=bound("pe_demo_v3"),
        batch_id="preview-wp03-pe",
        research_pid="r-other",
        seed_hex=SEED,
        build_source_sha256="unset",
    )
    with pytest.raises(CommandConflictError):
        execute_command(migrated.runtime, Command(command.command_id, other, "note", 0), note)


def test_stale_revision_is_rejected_recorded_and_replayable(
    migrated: MigratedDatabase, session_id: uuid.UUID
) -> None:
    stale = cmd(session_id, 5)
    result = execute_command(migrated.runtime, stale, note)
    assert not result.accepted and result.reason == "stale_revision"
    assert result.committed_revision == 0 and result.event_seqs == ()
    assert execute_command(migrated.runtime, stale, note).reason == "stale_revision"
    assert _count(migrated, s.event, session_id) == 0


def test_handler_rejection_rolls_back_only_its_writes(
    migrated: MigratedDatabase, session_id: uuid.UUID
) -> None:
    def half_then_reject(ctx: TxContext, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        ctx.append_event("test.partial", actor="participant")
        ctx.set_stage("consent")
        raise RejectionError("not_allowed_in_stage")

    result = execute_command(migrated.runtime, cmd(session_id, 0), half_then_reject)
    assert not result.accepted and result.reason == "not_allowed_in_stage"
    assert _count(migrated, s.event, session_id) == 0
    assert _count(migrated, s.stage_transition, session_id) == 0
    assert _revision(migrated, session_id) == 0
    ok = execute_command(migrated.runtime, cmd(session_id, 0), note)
    assert ok.accepted and ok.event_seqs == (1,)  # sequence has no gap after the rollback


def test_concurrent_duplicate_commands_yield_one_logical_write(
    migrated: MigratedDatabase, session_id: uuid.UUID
) -> None:
    command = cmd(session_id, 0, text="dup")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _: execute_command(migrated.runtime, command, note), range(8))
        )
    assert sum(not r.replayed for r in results) == 1
    assert {r.event_seqs for r in results} == {(1,)}
    assert _count(migrated, s.event, session_id) == 1


def test_concurrent_commands_at_one_revision_accept_exactly_one(
    migrated: MigratedDatabase, session_id: uuid.UUID
) -> None:
    commands = [cmd(session_id, 0, n=i) for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results: list[CommandResult] = list(
            pool.map(lambda c: execute_command(migrated.runtime, c, note), commands)
        )
    assert sum(r.accepted for r in results) == 1
    assert {r.reason for r in results if not r.accepted} == {"stale_revision"}
    seqs = [e["seq"] for e in events_after(migrated.runtime, session_id, 0)]
    assert seqs == [1]


def test_sequential_commands_have_monotonic_gapless_sequences(
    migrated: MigratedDatabase, session_id: uuid.UUID
) -> None:
    for revision in range(5):
        assert execute_command(migrated.runtime, cmd(session_id, revision), note).accepted
    seqs = [e["seq"] for e in events_after(migrated.runtime, session_id, 0)]
    assert seqs == [1, 2, 3, 4, 5]
    assert [e["seq"] for e in events_after(migrated.runtime, session_id, 3)] == [4, 5]


def test_due_transitions_advance_before_validation(
    migrated: MigratedDatabase, session_id: uuid.UUID
) -> None:
    def advance(ctx: TxContext) -> None:
        if ctx.stage == "entry":  # stand-in for a due deadline transition (WP06)
            ctx.set_stage("consent")
            ctx.append_event("stage.advanced", actor="scheduler")

    result = execute_command(migrated.runtime, cmd(session_id, 0), note, advance_due=advance)
    assert not result.accepted and result.reason == "stale_revision"
    assert result.committed_revision == 1  # the due transition itself committed
    assert [e["type"] for e in events_after(migrated.runtime, session_id, 0)] == ["stage.advanced"]
    ok = execute_command(migrated.runtime, cmd(session_id, 1), note, advance_due=advance)
    assert ok.accepted and ok.committed_revision == 2


def test_unavailable_persistence_reports_nothing_saved(session_id: uuid.UUID) -> None:
    dead = make_engine("postgresql://u:p@127.0.0.1:1/investigators_test_down")
    try:
        with pytest.raises(PersistenceUnavailableError):
            execute_command(dead, cmd(session_id, 0), note)
        with pytest.raises(PersistenceUnavailableError):
            claim_jobs(dead, worker_id="w")
    finally:
        dead.dispose()


def _schedule(key: str) -> Callable[[TxContext, Mapping[str, Any]], Mapping[str, Any]]:
    def handler(ctx: TxContext, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        job_id, created = ctx.schedule_job(
            key, kind="warning", claimable_at=ctx.now, due_active_ms=300_000
        )
        return {"job_id": str(job_id), "created": created}

    return handler


def test_logical_job_is_unique_and_claims_never_overlap(
    migrated: MigratedDatabase, session_id: uuid.UUID
) -> None:
    first = execute_command(
        migrated.runtime, cmd(session_id, 0), _schedule("warning.one_minute_left.v1")
    )
    second = execute_command(
        migrated.runtime, cmd(session_id, 1), _schedule("warning.one_minute_left.v1")
    )
    assert first.result["created"] is True and second.result["created"] is False
    assert first.result["job_id"] == second.result["job_id"]
    for i in range(6):
        execute_command(migrated.runtime, cmd(session_id, 2 + i), _schedule(f"job.{i}"))
    barrier = threading.Barrier(4)

    def worker(name: str) -> list[uuid.UUID]:
        barrier.wait()
        return [j.job_id for j in claim_jobs(migrated.runtime, worker_id=name, limit=3)]

    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = list(pool.map(worker, ["w1", "w2", "w3", "w4"]))
    flat = [job for batch in claimed for job in batch]
    assert len(flat) == len(set(flat)) == 7  # every job claimed exactly once


def test_job_finishes_once_and_lease_loss_is_detected(
    migrated: MigratedDatabase, session_id: uuid.UUID
) -> None:
    execute_command(migrated.runtime, cmd(session_id, 0), _schedule("warning.one_minute_left.v1"))
    (job,) = claim_jobs(migrated.runtime, worker_id="w1", lease_s=0.0)  # lease already expired
    (reclaimed,) = claim_jobs(migrated.runtime, worker_id="w2", lease_s=30.0)
    assert reclaimed.job_id == job.job_id and reclaimed.attempts == 2

    def commit_warning(ctx: TxContext, row: Mapping[str, Any]) -> str:
        ctx.append_event(
            "warning.committed", actor="system", payload={"key": row["logical_job_key"]}
        )
        return "committed"

    with pytest.raises(JobLeaseLostError):
        finish_job(migrated.runtime, job, worker_id="w1", handler=commit_warning)
    status, seqs = finish_job(migrated.runtime, reclaimed, worker_id="w2", handler=commit_warning)
    assert status == "committed" and len(seqs) == 1
    with pytest.raises(JobLeaseLostError):
        finish_job(migrated.runtime, reclaimed, worker_id="w2", handler=commit_warning)
    assert claim_jobs(migrated.runtime, worker_id="w3") == []
    types = [e["type"] for e in events_after(migrated.runtime, session_id, 0)]
    assert types.count("warning.committed") == 1
    assert _revision(migrated, session_id) == 2


def test_claim_jobs_never_claims_for_tombstoned_session(
    migrated: MigratedDatabase, session_id: uuid.UUID
) -> None:
    """BA-39: a scheduled-job row that survives an FK-blocked mutable delete is a
    purge work order — the claim query must never hand it to a worker."""
    execute_command(migrated.runtime, cmd(session_id, 0), _schedule("warning.one_minute_left.v1"))
    # Tombstone the session without deleting its rows — the job row survives as a
    # purge work order and must never reach a worker.
    with migrated.runtime.begin() as conn:
        conn.execute(
            insert(s.admin_audit).values(
                audit_id=uuid.uuid4(),
                session_id=session_id,
                actor="system",
                action="deletion_tombstone",
                payload={},
            )
        )
    assert claim_jobs(migrated.runtime, worker_id="w1") == []


def test_job_suppression_writes_no_event(migrated: MigratedDatabase, session_id: uuid.UUID) -> None:
    execute_command(migrated.runtime, cmd(session_id, 0), _schedule("warning.one_minute_left.v1"))
    (job,) = claim_jobs(migrated.runtime, worker_id="w1")
    status, seqs = finish_job(
        migrated.runtime, job, worker_id="w1", handler=lambda c, r: "suppressed"
    )
    assert (status, seqs) == ("suppressed", ())
    with migrated.runtime.connect() as conn:
        row = conn.execute(select(s.scheduled_job)).mappings().one()
    assert row["status"] == "suppressed" and row["finished_at"] is not None


def test_outbox_dispatch_is_recorded_once(
    migrated: MigratedDatabase, session_id: uuid.UUID
) -> None:
    for revision in range(3):
        execute_command(migrated.runtime, cmd(session_id, revision), note)
    pending = pending_outbox(migrated.runtime)
    assert [p["seq"] for p in pending] == [1, 2, 3]
    mark_dispatched(migrated.runtime, [p["outbox_id"] for p in pending[:2]])
    mark_dispatched(migrated.runtime, [p["outbox_id"] for p in pending[:2]])  # idempotent
    assert [p["seq"] for p in pending_outbox(migrated.runtime)] == [3]
    with migrated.runtime.connect() as conn:
        attempts = (
            conn.execute(text("SELECT attempts FROM app.outbox ORDER BY seq")).scalars().all()
        )
    assert attempts == [1, 1, 0]


def test_event_replay_is_scoped_to_one_session(
    migrated: MigratedDatabase, session_id: uuid.UUID
) -> None:
    other = create_session(
        migrated.runtime,
        bound=bound("pe_pilot_v4"),
        batch_id="preview-wp03-pe2",
        research_pid="r-scope",
        seed_hex=SEED,
        build_source_sha256="unset",
    )
    execute_command(migrated.runtime, cmd(session_id, 0, who="a"), note)
    execute_command(migrated.runtime, cmd(other, 0, who="b"), note)
    assert [e["payload"]["who"] for e in events_after(migrated.runtime, session_id, 0)] == ["a"]
    assert [e["payload"]["who"] for e in events_after(migrated.runtime, other, 0)] == ["b"]


def test_events_and_receipts_are_append_only_for_runtime(
    migrated: MigratedDatabase, session_id: uuid.UUID
) -> None:
    from sqlalchemy.exc import DBAPIError

    execute_command(migrated.runtime, cmd(session_id, 0), note)
    for sql in ("UPDATE app.event SET type = 'x'", "DELETE FROM app.command_receipt"):
        with pytest.raises(DBAPIError), migrated.runtime.begin() as conn:
            conn.execute(text(sql))
