"""The persisted active-time ledger (INV-0601; spec §6.1).

``clock_interval`` rows are the only authority for active elapsed: a ``running`` interval
contributes ``(db_now - started_at)`` on top of its recorded ``active_ms_at_start``; a
``paused`` interval freezes elapsed at its own ``active_ms_at_start``. UTC timestamps are the
durable chronology; the check-in pause is a permitted ``paused`` interval with a reason, so
restarting either the API or the scheduler loses nothing and nothing is silently extended.

Pauses and resumes only ever run inside a session-locked transaction (``TxContext``), which is
what keeps the ledger transition atomic with the stage/job writes that cause it.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, insert, select, update
from sqlalchemy.engine import RowMapping

from app.db import schema as s


class ClockLedgerError(RuntimeError):
    pass


def speed_factor_for(session: Mapping[str, Any]) -> float:
    """The session's bound ``clock_speed_factor`` (BA-14; spec §11 QA overlays only —
    binding rejects any factor != 1 outside ``accelerated_qa`` mode). Missing or
    malformed values read as 1.0, i.e. faithful timing."""
    binding = session.get("binding")
    if isinstance(binding, Mapping):
        try:
            factor = float(binding.get("clock_speed_factor") or 1.0)
        except (TypeError, ValueError):
            return 1.0
        return factor if factor > 0 else 1.0
    return 1.0


def _speed_factor(conn: Connection, session_id: uuid.UUID) -> float:
    binding = conn.execute(
        select(s.session.c.binding).where(s.session.c.session_id == session_id)
    ).scalar_one_or_none()
    return speed_factor_for({"binding": binding})


def open_interval(conn: Connection, session_id: uuid.UUID) -> RowMapping | None:
    row = (
        conn.execute(
            select(s.clock_interval).where(
                s.clock_interval.c.session_id == session_id,
                s.clock_interval.c.ended_at.is_(None),
            )
        )
        .mappings()
        .one_or_none()
    )
    return row


def active_ms(conn: Connection, session_id: uuid.UUID, now: datetime, *, default: int = 0) -> int:
    """Active elapsed ms at ``now`` (the DB clock value the caller already fetched).

    A ``running`` interval contributes ``(now - started_at) * clock_speed_factor`` on top
    of its ``active_ms_at_start`` — under an accelerated QA overlay (BA-14) the same wall
    span produces ``factor`` times the active elapsed; a ``paused`` interval stays frozen
    either way. With no open interval — before the task clock starts or after it is
    closed for good — the last *closed* interval still bounds elapsed with the same
    factor applied, so pause/close/restart all compute on one basis."""
    interval = open_interval(conn, session_id)
    if interval is not None:
        base = int(interval["active_ms_at_start"])
        if interval["kind"] == "running":
            started: datetime = interval["started_at"]
            factor = _speed_factor(conn, session_id)
            return base + max(0, round((now - started).total_seconds() * 1000 * factor))
        return base  # paused: elapsed frozen at pause start
    last = (
        conn.execute(
            select(s.clock_interval)
            .where(s.clock_interval.c.session_id == session_id)
            .order_by(s.clock_interval.c.started_at.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    if last is None:
        return default
    base = int(last["active_ms_at_start"])
    if last["kind"] != "running":
        return base
    ended: datetime = last["ended_at"]
    started_last: datetime = last["started_at"]
    factor = _speed_factor(conn, session_id)
    return base + max(0, round((ended - started_last).total_seconds() * 1000 * factor))


def is_running(conn: Connection, session_id: uuid.UUID) -> bool:
    interval = open_interval(conn, session_id)
    return interval is not None and interval["kind"] == "running"


def _close_open(conn: Connection, session_id: uuid.UUID, now: datetime) -> RowMapping:
    interval = open_interval(conn, session_id)
    if interval is None:
        raise ClockLedgerError(f"session {session_id} has no open clock interval")
    conn.execute(
        update(s.clock_interval)
        .where(s.clock_interval.c.interval_id == interval["interval_id"])
        .values(ended_at=now)
    )
    return interval


def _open(conn: Connection, session_id: uuid.UUID, kind: str, reason: str, active: int) -> None:
    conn.execute(
        insert(s.clock_interval).values(
            interval_id=uuid.uuid4(),
            session_id=session_id,
            kind=kind,
            reason=reason,
            active_ms_at_start=active,
        )
    )


def pause(conn: Connection, session_id: uuid.UUID, *, reason: str, now: datetime) -> int:
    """Freeze the active clock (check-in). Returns the frozen active elapsed.

    Idempotent on an already-paused clock: a second pause request is a no-op so the same
    logical pause can never double-close the running interval."""
    interval = open_interval(conn, session_id)
    if interval is None:
        raise ClockLedgerError(f"session {session_id}: cannot pause a closed clock")
    if interval["kind"] == "paused":
        return int(interval["active_ms_at_start"])
    active = active_ms(conn, session_id, now)
    _close_open(conn, session_id, now)
    _open(conn, session_id, "paused", reason, active)
    return active


def resume(conn: Connection, session_id: uuid.UUID, *, reason: str, now: datetime) -> int:
    """Restart the active clock after a pause; returns the unchanged active elapsed."""
    interval = open_interval(conn, session_id)
    if interval is None:
        raise ClockLedgerError(f"session {session_id}: cannot resume a closed clock")
    if interval["kind"] == "running":
        return int(interval["active_ms_at_start"])
    active = int(interval["active_ms_at_start"])
    _close_open(conn, session_id, now)
    _open(conn, session_id, "running", reason, active)
    return active


def close(conn: Connection, session_id: uuid.UUID, now: datetime) -> int | None:
    """Close whatever interval is open (deadline/terminal). No new interval opens; the clock
    is done. Returns the final active elapsed, or None if nothing was open."""
    interval = open_interval(conn, session_id)
    if interval is None:
        return None
    active = active_ms(conn, session_id, now)
    _close_open(conn, session_id, now)
    return active


def wall_due_estimate(
    active_now_ms: int, due_active_ms: int, now: datetime, *, factor: float = 1.0
) -> datetime:
    """The earliest wall instant an active-due job could fire *if the clock stays running*.
    Only a claim-priority hint — pauses legitimately push real due-ness later, so the claim
    query and the commit-time check are what actually enforce the active axis. ``factor``
    is the session's ``clock_speed_factor`` (BA-14): under acceleration the wall wait is
    the active gap divided by the factor."""
    from datetime import timedelta

    f = factor if factor > 0 else 1.0
    return now + timedelta(milliseconds=max(0.0, (due_active_ms - active_now_ms) / f))
