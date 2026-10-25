"""Transactional core: commands, events, outbox and durable jobs (INV-0303, INV-0307).

Lock ordering (always, everywhere): the ``app.session`` row first (``SELECT ... FOR UPDATE``),
then any job row, then other session-owned rows. Job *claiming* takes only job rows with
``SKIP LOCKED`` and never waits, so it cannot deadlock with command or job-finish transactions.

A command (spec §6.2, §8.2) runs in ONE transaction:

1. lock the session;
2. if the ``command_id`` already has a receipt, return the stored result unchanged (HTTP retry
   idempotency); a receipt for another session or a different kind is a conflict;
3. advance due deadline transitions (hook supplied by WP06), bumping the revision if they
   changed state;
4. reject a stale ``expected_revision``;
5. run the handler inside a savepoint (a handler rejection rolls back only its own writes);
6. append events + outbox rows atomically, bump the revision, write the receipt, commit.

Nothing is reported as saved unless the commit succeeded: persistence failures raise
:class:`PersistenceUnavailableError` (HTTP 503) and write nothing. Serialization failures and
deadlocks are retried a bounded number of times with the same command.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Float, cast, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError, IntegrityError, InterfaceError, OperationalError
from sqlalchemy.sql.elements import ColumnElement

from app.db import schema as s

RETRYABLE_SQLSTATES = frozenset({"40001", "40P01"})  # serialization_failure, deadlock_detected


class PersistenceUnavailableError(RuntimeError):
    """The database could not durably record the operation. Maps to HTTP 503."""


class SessionNotFoundError(LookupError):
    pass


class CommandConflictError(ValueError):
    """The command_id was already used for another session or another command kind."""


class RejectionError(Exception):
    """Raised by handlers to reject a command with a stable reason code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class JobLeaseLostError(RuntimeError):
    """The job is no longer claimed by this worker (lease expired and reclaimed, or done)."""


@dataclass(frozen=True)
class Command:
    command_id: uuid.UUID
    session_id: uuid.UUID
    kind: str
    expected_revision: int
    payload: Mapping[str, Any] = field(default_factory=dict)
    client_monotonic_ms: int | None = None


@dataclass(frozen=True)
class EventRef:
    seq: int
    event_id: uuid.UUID
    type: str


@dataclass(frozen=True)
class CommandResult:
    command_id: uuid.UUID
    session_id: uuid.UUID
    accepted: bool
    reason: str | None
    committed_revision: int
    result: Mapping[str, Any]
    event_seqs: tuple[int, ...]
    replayed: bool


def db_now(conn: Connection) -> datetime:
    """Database clock (spec §6.1: chronology from the database, not client clocks)."""
    value: datetime = conn.execute(select(func.clock_timestamp())).scalar_one()
    return value


class TxContext:
    """Mutation context for one locked session inside one transaction."""

    def __init__(
        self,
        conn: Connection,
        session_row: Mapping[str, Any],
        *,
        now: datetime,
        command_id: uuid.UUID | None = None,
        job_id: uuid.UUID | None = None,
    ) -> None:
        self.conn = conn
        self.session_id: uuid.UUID = session_row["session_id"]
        self.stage: str = session_row["stage"]
        self.revision: int = session_row["revision"]
        self.next_event_seq: int = session_row["next_event_seq"]
        self.terminal_reason: str | None = session_row["terminal_reason"]
        self.session: Mapping[str, Any] = session_row
        self.now = now
        self.command_id = command_id
        self.job_id = job_id
        self.events: list[EventRef] = []
        self.dirty = False

    # ----- events ------------------------------------------------------------------------------
    def append_event(
        self,
        type_: str,
        *,
        actor: str,
        payload: Mapping[str, Any] | None = None,
        parent_event_id: uuid.UUID | None = None,
        active_ms: int | None = None,
    ) -> EventRef:
        seq = self.next_event_seq
        event_id = uuid.uuid4()
        self.conn.execute(
            insert(s.event).values(
                session_id=self.session_id,
                seq=seq,
                event_id=event_id,
                stage=self.stage,
                actor=actor,
                type=type_,
                command_id=self.command_id,
                job_id=self.job_id,
                parent_event_id=parent_event_id,
                revision=self.revision + 1,
                server_time=self.now,
                active_ms=active_ms,
                payload=dict(payload or {}),
            )
        )
        self.conn.execute(insert(s.outbox).values(session_id=self.session_id, seq=seq))
        self.next_event_seq = seq + 1
        self.dirty = True
        ref = EventRef(seq, event_id, type_)
        self.events.append(ref)
        return ref

    # ----- stage -------------------------------------------------------------------------------
    def set_stage(self, to_stage: str, *, reason: str | None = None) -> None:
        if to_stage not in s.STAGES:
            raise ValueError(f"unknown stage {to_stage!r}")
        if to_stage in s.TERMINAL_STAGES and not reason:
            raise ValueError("terminal stages need a reason")
        self.conn.execute(
            insert(s.stage_transition).values(
                transition_id=uuid.uuid4(),
                session_id=self.session_id,
                from_stage=self.stage,
                to_stage=to_stage,
                revision=self.revision + 1,
                reason=reason,
                occurred_at=self.now,
            )
        )
        if to_stage in s.TERMINAL_STAGES:
            self.terminal_reason = reason
        self.stage = to_stage
        self.dirty = True

    # ----- jobs --------------------------------------------------------------------------------
    def schedule_job(
        self,
        logical_job_key: str,
        *,
        kind: str,
        claimable_at: datetime,
        due_active_ms: int | None = None,
        due_wall_at: datetime | None = None,
        payload: Mapping[str, Any] | None = None,
        precondition: Mapping[str, Any] | None = None,
        parent_event_id: uuid.UUID | None = None,
    ) -> tuple[uuid.UUID, bool]:
        """Insert a job once per logical key. Returns ``(job_id, created)``; a duplicate logical
        job returns the existing id and ``created=False`` (at-least-once callers are safe)."""
        if (due_active_ms is None) == (due_wall_at is None):
            raise ValueError("exactly one of due_active_ms / due_wall_at")
        stmt = (
            pg_insert(s.scheduled_job)
            .values(
                job_id=uuid.uuid4(),
                session_id=self.session_id,
                logical_job_key=logical_job_key,
                kind=kind,
                clock_basis="active_task" if due_active_ms is not None else "wall",
                due_active_ms=due_active_ms,
                due_wall_at=due_wall_at,
                claimable_at=claimable_at,
                parent_event_id=parent_event_id,
                payload=dict(payload or {}),
                precondition=dict(precondition or {}),
            )
            .on_conflict_do_nothing(index_elements=["session_id", "logical_job_key"])
            .returning(s.scheduled_job.c.job_id)
        )
        created = self.conn.execute(stmt).scalar_one_or_none()
        if created is not None:
            self.dirty = True
            return created, True
        existing: uuid.UUID = self.conn.execute(
            select(s.scheduled_job.c.job_id).where(
                s.scheduled_job.c.session_id == self.session_id,
                s.scheduled_job.c.logical_job_key == logical_job_key,
            )
        ).scalar_one()
        return existing, False

    # ----- persistence of the session row --------------------------------------------------------
    def _flush_session(self) -> None:
        if not self.dirty:
            return
        self.revision += 1
        values: dict[str, Any] = {
            "revision": self.revision,
            "next_event_seq": self.next_event_seq,
            "terminal_reason": self.terminal_reason,
        }
        if self.stage != self.session["stage"]:
            values["stage"] = self.stage
            values["stage_entered_at"] = self.now
        self.conn.execute(
            update(s.session).where(s.session.c.session_id == self.session_id).values(**values)
        )
        self.session = {**self.session, **values}
        self.dirty = False


Handler = Callable[[TxContext, Mapping[str, Any]], Mapping[str, Any] | None]
DueAdvancer = Callable[[TxContext], None]
JobHandler = Callable[[TxContext, Mapping[str, Any]], "str | Deferred"]


def lock_session(conn: Connection, session_id: uuid.UUID) -> Mapping[str, Any]:
    row = (
        conn.execute(
            select(s.session).where(s.session.c.session_id == session_id).with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        raise SessionNotFoundError(str(session_id))
    return dict(row)


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(exc.orig, "sqlstate", None)


def _receipt(conn: Connection, command_id: uuid.UUID) -> Mapping[str, Any] | None:
    row = (
        conn.execute(select(s.command_receipt).where(s.command_receipt.c.command_id == command_id))
        .mappings()
        .first()
    )
    return None if row is None else dict(row)


def _from_receipt(row: Mapping[str, Any], *, replayed: bool) -> CommandResult:
    result = dict(row["result"])
    return CommandResult(
        command_id=row["command_id"],
        session_id=row["session_id"],
        accepted=row["outcome"] == "accepted",
        reason=row["reason"],
        committed_revision=row["committed_revision"],
        result=result.get("result", {}),
        event_seqs=tuple(result.get("event_seqs", ())),
        replayed=replayed,
    )


def _run_command_once(
    engine: Engine,
    command: Command,
    handler: Handler,
    advance_due: DueAdvancer | None,
) -> CommandResult:
    with engine.begin() as conn:
        conn.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
        row = lock_session(conn, command.session_id)
        existing = _receipt(conn, command.command_id)
        if existing is not None:
            if existing["session_id"] != command.session_id or existing["kind"] != command.kind:
                raise CommandConflictError("command_id already used for a different command")
            return _from_receipt(existing, replayed=True)

        ctx = TxContext(conn, row, now=db_now(conn), command_id=command.command_id)
        if advance_due is not None:
            advance_due(ctx)
            ctx._flush_session()

        accepted = False
        reason: str | None = None
        result: Mapping[str, Any] = {}
        if command.expected_revision != ctx.revision:
            reason = "stale_revision"
        else:
            savepoint = conn.begin_nested()
            mark = (ctx.stage, ctx.next_event_seq, ctx.terminal_reason, len(ctx.events), ctx.dirty)
            try:
                result = handler(ctx, command.payload) or {}
                savepoint.commit()
                accepted = True
            except RejectionError as rejection:
                savepoint.rollback()
                ctx.stage, ctx.next_event_seq, ctx.terminal_reason = mark[0], mark[1], mark[2]
                del ctx.events[mark[3] :]
                ctx.dirty = mark[4]
                reason = rejection.reason
            if accepted:
                ctx.dirty = True  # an accepted command always advances the revision
            ctx._flush_session()

        stored = {"result": dict(result), "event_seqs": [e.seq for e in ctx.events]}
        conn.execute(
            insert(s.command_receipt).values(
                command_id=command.command_id,
                session_id=command.session_id,
                kind=command.kind,
                expected_revision=command.expected_revision,
                outcome="accepted" if accepted else "rejected",
                reason=reason,
                committed_revision=ctx.revision,
                result=stored,
                client_monotonic_ms=command.client_monotonic_ms,
                received_at=ctx.now,
            )
        )
    return CommandResult(
        command_id=command.command_id,
        session_id=command.session_id,
        accepted=accepted,
        reason=reason,
        committed_revision=ctx.revision,
        result=dict(result),
        event_seqs=tuple(e.seq for e in ctx.events),
        replayed=False,
    )


def execute_command(
    engine: Engine,
    command: Command,
    handler: Handler,
    *,
    advance_due: DueAdvancer | None = None,
    max_attempts: int = 3,
) -> CommandResult:
    for attempt in range(1, max_attempts + 1):
        try:
            return _run_command_once(engine, command, handler, advance_due)
        except IntegrityError:
            # A concurrent transaction on ANOTHER session inserted the same command_id first.
            with engine.connect() as conn:
                existing = _receipt(conn, command.command_id)
            if existing is not None and existing["session_id"] != command.session_id:
                raise CommandConflictError("command_id already used for another session") from None
            raise  # a genuine constraint violation from the handler: nothing was committed
        except (OperationalError, InterfaceError) as exc:
            if _sqlstate(exc) in RETRYABLE_SQLSTATES and attempt < max_attempts:
                continue
            raise PersistenceUnavailableError("database unavailable; nothing was saved") from None
    raise PersistenceUnavailableError("command retries exhausted")  # pragma: no cover


# ----- jobs ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimedJob:
    job_id: uuid.UUID
    session_id: uuid.UUID
    logical_job_key: str
    kind: str
    attempts: int
    lease_expires_at: datetime
    row: Mapping[str, Any]


@dataclass(frozen=True)
class Deferred:
    """A claimed job that is not (yet) due — e.g. its session clock paused between the claim
    and the commit — is returned to ``pending`` with this wall-time re-claim hint instead of
    being failed or forced early (spec §6.4: pauses push due-ness, never pull it early)."""

    claimable_at: datetime


def due_clause(now: datetime) -> ColumnElement[bool]:
    """Per-job due-ness on its declared clock basis.

    ``wall`` jobs are due at ``due_wall_at``. ``active_task`` jobs are due only while the
    session's open interval is ``running`` and its active elapsed reaches ``due_active_ms`` —
    a paused clock can never satisfy the clause, which is exactly how the check-in suspends
    every pending agent delivery on the same axis (spec §6.1/§6.4). The wall-side due
    instant divides the remaining active ms by the session's bound ``clock_speed_factor``
    (BA-14) so an accelerated QA clock fires ``factor`` times sooner in wall time; missing
    or malformed binding factors read as 1.0 (faithful timing).

    Requires ``app.scheduled_job`` to be joined to ``app.session`` (``session_id``)."""
    j, ci = s.scheduled_job, s.clock_interval
    factor = func.coalesce(cast(s.session.c.binding.op("->>")("clock_speed_factor"), Float), 1.0)
    remaining_s = (j.c.due_active_ms - ci.c.active_ms_at_start) / (1000.0 * factor)
    active_due = ci.c.started_at + func.make_interval(0, 0, 0, 0, 0, 0, remaining_s)
    return (j.c.claimable_at <= now) & (
        ((j.c.clock_basis == "wall") & (j.c.due_wall_at <= now))
        | ((j.c.clock_basis == "active_task") & (ci.c.kind == "running") & (active_due <= now))
    )


def claim_jobs(
    engine: Engine,
    *,
    worker_id: str,
    lease_s: float = 30.0,
    limit: int = 10,
    kinds: Sequence[str] | None = None,
    defer_provider_generation: bool = False,
) -> list[ClaimedJob]:
    """Claim due jobs (pending, or claimed with an expired lease) without ever waiting.

    ``kinds`` restricts the claim to those job kinds. ``defer_provider_generation``
    excludes ``generate`` jobs whose payload does not opt out of provider calls —
    used by the worker's dispatch pass so a due timing job is never stuck behind a
    claimed-but-blocking generation (BA-45)."""
    j, ci = s.scheduled_job, s.clock_interval
    try:
        with engine.begin() as conn:
            now = db_now(conn)
            query = (
                select(j)
                .select_from(
                    j.join(
                        ci,
                        (ci.c.session_id == j.c.session_id) & ci.c.ended_at.is_(None),
                        isouter=True,
                    ).join(s.session, s.session.c.session_id == j.c.session_id)
                )
                .where(
                    (j.c.status == "pending")
                    | ((j.c.status == "claimed") & (j.c.lease_expires_at < now)),
                    due_clause(now),
                    # BA-39: never run jobs for a tombstoned session — surviving
                    # job rows are a purge work order, not runnable work.
                    ~select(s.admin_audit.c.session_id)
                    .where(
                        s.admin_audit.c.session_id == j.c.session_id,
                        s.admin_audit.c.action == "deletion_tombstone",
                    )
                    .exists(),
                )
                .order_by(j.c.claimable_at, j.c.job_id)
                .limit(limit)
                .with_for_update(of=j, skip_locked=True)
            )
            if kinds is not None:
                query = query.where(j.c.kind.in_(kinds))
            if defer_provider_generation:
                # ``qa_answer`` (AI_QA_ENABLED) always needs the provider —
                # the deterministic fallback is written by its dispatch, not
                # by skipping the call here.
                query = query.where(
                    ~(
                        (
                            (j.c.kind == "generate")
                            & (
                                func.coalesce(j.c.payload.op("->>")("deterministic_only"), "false")
                                != "true"
                            )
                        )
                        | (j.c.kind == "qa_answer")
                    )
                )
            rows = conn.execute(query).mappings().all()
            expires = now + timedelta(seconds=lease_s)
            claimed = []
            for row in rows:
                conn.execute(
                    update(j)
                    .where(j.c.job_id == row["job_id"])
                    .values(
                        status="claimed",
                        lease_owner=worker_id,
                        lease_expires_at=expires,
                        attempts=j.c.attempts + 1,
                    )
                )
                claimed.append(
                    ClaimedJob(
                        row["job_id"],
                        row["session_id"],
                        row["logical_job_key"],
                        row["kind"],
                        row["attempts"] + 1,
                        expires,
                        dict(row),
                    )
                )
            return claimed
    except (OperationalError, InterfaceError):
        raise PersistenceUnavailableError("database unavailable; no job claimed") from None


def extend_job_lease(
    engine: Engine, job: ClaimedJob, *, worker_id: str, lease_s: float
) -> ClaimedJob:
    """Re-lease a job this worker still owns for a longer blocking phase (BA-45).

    Provider generation can outlast the short dispatch lease the batch was claimed
    under; without extension another worker can re-claim mid-generation and the
    original finish fails lease verification. The extension is authorized by the
    same ``status='claimed' AND lease_owner=worker_id`` check ``finish_job`` uses —
    a job already lost cannot be extended.
    """
    try:
        with engine.begin() as conn:
            now = db_now(conn)
            new_expiry = now + timedelta(seconds=lease_s)
            result = conn.execute(
                update(s.scheduled_job)
                .where(
                    s.scheduled_job.c.job_id == job.job_id,
                    s.scheduled_job.c.status == "claimed",
                    s.scheduled_job.c.lease_owner == worker_id,
                )
                .values(lease_expires_at=new_expiry)
            )
            if result.rowcount != 1:
                raise JobLeaseLostError(str(job.job_id))
        return replace(job, lease_expires_at=new_expiry)
    except (OperationalError, InterfaceError):
        raise PersistenceUnavailableError("database unavailable; lease not extended") from None


def finish_job(
    engine: Engine, job: ClaimedJob, *, worker_id: str, handler: JobHandler
) -> tuple[str, tuple[int, ...]]:
    """Run ``handler`` under the session lock and record the job outcome exactly once.

    The handler returns the terminal status (``committed``, ``suppressed``, ``failed``,
    ``cancelled`` or ``expired``); it performs its own commit-time authorization (stage,
    deadline, recipient, revision) with the session locked (spec §6.4). Returning a
    :class:`Deferred` instead releases the job back to ``pending`` with a re-claim hint —
    used when the commit-time check shows the job is not due on its own clock."""
    j = s.scheduled_job
    try:
        with engine.begin() as conn:
            row = lock_session(conn, job.session_id)
            current = (
                conn.execute(select(j).where(j.c.job_id == job.job_id).with_for_update())
                .mappings()
                .one()
            )
            if current["status"] != "claimed" or current["lease_owner"] != worker_id:
                raise JobLeaseLostError(str(job.job_id))
            ctx = TxContext(conn, row, now=db_now(conn), job_id=job.job_id)
            outcome = handler(ctx, dict(current))
            if isinstance(outcome, Deferred):
                conn.execute(
                    update(j)
                    .where(j.c.job_id == job.job_id)
                    .values(
                        status="pending",
                        claimable_at=outcome.claimable_at,
                        lease_owner=None,
                        lease_expires_at=None,
                    )
                )
                ctx._flush_session()
                return "deferred", tuple(e.seq for e in ctx.events)
            status = outcome
            if status not in s.JOB_STATUSES or status in ("pending", "claimed"):
                raise ValueError(f"job handler returned non-terminal status {status!r}")
            conn.execute(
                update(j)
                .where(j.c.job_id == job.job_id)
                .values(
                    status=status,
                    lease_owner=None,
                    lease_expires_at=None,
                    finished_at=ctx.now,
                    finished_revision=ctx.revision + (1 if ctx.dirty else 0),
                )
            )
            ctx._flush_session()
            return status, tuple(e.seq for e in ctx.events)
    except (OperationalError, InterfaceError):
        raise PersistenceUnavailableError(
            "database unavailable; job outcome not recorded"
        ) from None


# ----- replay and outbox ---------------------------------------------------------------------


def events_after(
    engine: Engine, session_id: uuid.UUID, after_seq: int, *, limit: int = 500
) -> list[Mapping[str, Any]]:
    """Committed events of ONE session after a cursor, in sequence order (replay/SSE)."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(s.event)
            .where(s.event.c.session_id == session_id, s.event.c.seq > after_seq)
            .order_by(s.event.c.seq)
            .limit(limit)
        ).mappings()
        return [dict(r) for r in rows]


def pending_outbox(engine: Engine, *, limit: int = 100) -> list[Mapping[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(s.outbox)
            .where(s.outbox.c.dispatched_at.is_(None))
            .order_by(s.outbox.c.outbox_id)
            .limit(limit)
        ).mappings()
        return [dict(r) for r in rows]


def mark_dispatched(engine: Engine, outbox_ids: Sequence[int]) -> None:
    if not outbox_ids:
        return
    with engine.begin() as conn:
        conn.execute(
            update(s.outbox)
            .where(s.outbox.c.outbox_id.in_(list(outbox_ids)), s.outbox.c.dispatched_at.is_(None))
            .values(dispatched_at=db_now(conn), attempts=s.outbox.c.attempts + 1)
        )
