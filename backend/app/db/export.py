"""Consistent-snapshot exports and replay reconstruction (INV-0309, spec §8.3, §9.3).

- :func:`export_session` reads every session-owned table inside one ``REPEATABLE READ``
  transaction, so the export is a consistent snapshot of committed data even while the session
  continues to accept commands (spec §9.3: "generated from committed data at a consistent
  snapshot"). The ``identity`` schema is never part of a general research export (spec §8.1);
  payment reconciliation uses :func:`export_payment_linkage`, which is the only export that may
  join ``identity.platform_link`` — callers must run it as ``investigators_compensation``.
- :func:`reconstruct_session` rebuilds the session's required state (stage, revision, next
  event sequence, terminal reason, open clock interval, pending jobs) purely from committed
  records, proving that replay does not depend on any transient queue or in-memory state.
  A mismatch against the stored ``app.session`` row is a bug and raises
  :class:`ReplayMismatchError`.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Connection, Engine

from app.db import schema as s


class ReplayMismatchError(RuntimeError):
    pass


SESSION_TABLES = tuple(
    table.name
    for table in s.metadata.sorted_tables
    if table.schema == s.APP and "session_id" in table.c and not table.c.session_id.nullable
)
"""Every app table whose rows are owned by exactly one session (non-null session_id)."""

EXPORT_EXCLUDED_TABLES = frozenset({"participant_display"})
"""Session-owned tables that are identity-adjacent rather than research data: stored
server-side for display purposes only and omitted from every research export
(UX round 2 — initials must never leave in a shared bundle)."""


def export_session(engine: Engine, session_id: uuid.UUID) -> dict[str, list[dict[str, Any]]]:
    """Committed rows for one session across all session-owned tables, one snapshot.

    Identity rows are deliberately excluded; a research export must never contain platform
    identifiers (spec §8.1, §11), and ``EXPORT_EXCLUDED_TABLES`` (participant display
    initials) are never included either."""
    out: dict[str, list[dict[str, Any]]] = {}
    with engine.connect() as conn:
        conn = conn.execution_options(isolation_level="REPEATABLE READ")
        for name in SESSION_TABLES:
            if name in EXPORT_EXCLUDED_TABLES:
                continue
            table = s.metadata.tables[f"{s.APP}.{name}"]
            rows = (
                conn.execute(select(table).where(table.c.session_id == session_id)).mappings().all()
            )
            out[name] = [dict(row) for row in rows]
    return out


def export_payment_linkage(
    engine: Engine, session_id: uuid.UUID
) -> dict[str, list[dict[str, Any]]]:
    """The compensation view: platform linkage plus payment/completion state only.

    Must be executed as ``investigators_compensation`` — it is the only export allowed to read
    ``identity.platform_link``, and it returns nothing else session-owned (spec §9: the payment
    export is restricted to linkage, entitlement and outcome)."""
    tables = (
        s.metadata.tables[f"{s.IDENTITY}.platform_link"],
        s.payment_entitlement,
        s.completion_receipt,
        s.result,
    )
    out: dict[str, list[dict[str, Any]]] = {}
    with engine.connect() as conn:
        conn = conn.execution_options(isolation_level="REPEATABLE READ")
        for table in tables:
            rows = (
                conn.execute(select(table).where(table.c.session_id == session_id)).mappings().all()
            )
            out[table.name] = [dict(row) for row in rows]
    return out


def reconstruct_session(conn: Connection, session_id: uuid.UUID) -> dict[str, Any]:
    """Derive the session's required state from committed records only (INV-0309).

    Rebuilt, per spec §8.3 ("replay reconstructs required state from committed records"):
    stage and terminal reason from ``stage_transition``; the event cursor from ``event``; the
    open clock interval from ``clock_interval``; pending jobs from ``scheduled_job``. The
    revision is the maximum revision stamped on any committed record — every command receipt,
    stage transition and event carries the revision its transaction committed — so a mutation
    that bumps the revision without leaving such a record is exactly what this check exists to
    catch (it raises rather than silently disagreeing)."""
    session = (
        conn.execute(select(s.session).where(s.session.c.session_id == session_id))
        .mappings()
        .one_or_none()
    )
    if session is None:
        raise ReplayMismatchError(f"session {session_id} does not exist")

    last_transition = (
        conn.execute(
            select(s.stage_transition)
            .where(s.stage_transition.c.session_id == session_id)
            .order_by(s.stage_transition.c.revision.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    next_seq: int = conn.execute(
        select(func.coalesce(func.max(s.event.c.seq), 0) + 1).where(
            s.event.c.session_id == session_id
        )
    ).scalar_one()
    open_interval = conn.execute(
        select(s.clock_interval.c.interval_id).where(
            s.clock_interval.c.session_id == session_id,
            s.clock_interval.c.ended_at.is_(None),
        )
    ).all()
    pending_jobs = conn.execute(
        select(func.count())
        .select_from(s.scheduled_job)
        .where(
            s.scheduled_job.c.session_id == session_id,
            s.scheduled_job.c.status.in_(("pending", "claimed")),
        )
    ).scalar_one()
    receipt_rev: int = conn.execute(
        select(func.coalesce(func.max(s.command_receipt.c.committed_revision), 0)).where(
            s.command_receipt.c.session_id == session_id
        )
    ).scalar_one()
    event_rev: int = conn.execute(
        select(func.coalesce(func.max(s.event.c.revision), 0)).where(
            s.event.c.session_id == session_id
        )
    ).scalar_one()
    transition_rev = last_transition["revision"] if last_transition else 0
    job_rev: int = conn.execute(
        select(func.coalesce(func.max(s.scheduled_job.c.finished_revision), 0)).where(
            s.scheduled_job.c.session_id == session_id
        )
    ).scalar_one()
    revision = max(receipt_rev, event_rev, transition_rev, job_rev)
    rebuilt = {
        "stage": last_transition["to_stage"] if last_transition else "entry",
        "terminal_reason": last_transition["reason"]
        if last_transition and last_transition["to_stage"] in s.TERMINAL_STAGES
        else None,
        "next_event_seq": next_seq,
        "revision": revision,
        "open_clock_intervals": len(open_interval),
        "pending_jobs": pending_jobs,
    }
    mismatches = {
        key: (rebuilt[key], session[key])
        for key in ("stage", "terminal_reason", "next_event_seq", "revision")
        if rebuilt[key] != session[key]
    }
    if mismatches:
        raise ReplayMismatchError(f"session {session_id}: committed records disagree {mismatches}")
    return rebuilt
