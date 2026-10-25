"""Session completion and abandonment: the ``/complete`` handler and the idle sweep
(audit H2).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any

from sqlalchemy import exists, func, insert, select, update
from sqlalchemy.engine import Engine

from app.db import schema as s
from app.db.tx import RejectionError, TxContext, db_now, lock_session
from app.domain.manifests.store import ContentStore
from app.services import (
    activetime,
    engines,
    scheduler,
)
from app.services.enrollment import close_if_complete
from app.services.lifecycle_core import enforce_rate_limit, transition
from app.services.outcomes import (  # explicit re-exports: historical import path
    entitlement_for,
    record_terminal_outcome,
)
from app.services.session_binding import (  # explicit re-exports: historical import path
    bound_for_session,
)
from app.settings import ConnectCompletion

# ----- completion (INV-0408) -----------------------------------------------------------------


def completion_code(session_id: uuid.UUID) -> str:
    """Internal deterministic receipt identifier, not the displayed completion code."""
    digest = hashlib.sha256(f"investigators.completion.v1:{session_id}".encode()).hexdigest()
    return f"INV-{digest[:8].upper()}"


def completion_handler(
    *,
    store: ContentStore,
    writer: uuid.UUID | None = None,
    connect_completion: Mapping[str, ConnectCompletion] | None = None,
    prolific_code: str | None = None,
    bonus_payment_enabled: bool = True,
) -> Callable[[TxContext, Mapping[str, Any]], dict[str, Any]]:
    """The ``complete`` command: reconcile to ``completion`` and persist the durable outcome.

    Requires the data-use choice and a recorded consent (spec §5.2/§8.1); writes the
    ``completion_receipt`` and the ``payment_entitlement`` rows in the same transaction so a
    finished session always carries its compensation decision. Entitlements are ``pending``
    only for live sessions whose bound payment policy is activatable; preview/QA and
    non-activatable policies record ``ineligible`` explicitly (never silently absent).
    """

    def handler(ctx: TxContext, _p: Mapping[str, Any]) -> dict[str, Any]:
        """The ``complete`` command: only from ``data_use_choice``, under the rate limit.
        Transitions to ``completion``, writes the completion receipt with the platform
        handoff and code, persists the base entitlement, and closes the group quota when
        the last cell fills."""
        if ctx.stage != "data_use_choice":
            raise RejectionError("wrong_stage")
        holder = ctx.session["writer_id"]
        if holder is not None and holder != writer:
            raise RejectionError("writer_lease_held")
        bound = bound_for_session(ctx.conn, store, ctx.session)
        enforce_rate_limit(ctx, bound)
        consented = ctx.conn.execute(
            select(func.count())
            .select_from(s.consent_event)
            .where(
                s.consent_event.c.session_id == ctx.session_id,
                s.consent_event.c.action == "consented",
            )
        ).scalar_one()
        choice_rows = ctx.conn.execute(
            select(func.count())
            .select_from(s.data_use_choice)
            .where(s.data_use_choice.c.session_id == ctx.session_id)
        ).scalar_one()
        if not consented:
            raise RejectionError("consent_missing")
        platform = ctx.conn.execute(
            select(s.platform_link.c.platform).where(s.platform_link.c.session_id == ctx.session_id)
        ).scalar_one_or_none()
        receipt_code = completion_code(ctx.session_id)
        display_code = prolific_code or "PLACEHOLDER-CODE"
        handoff: dict[str, Any] = {
            "code_version": "study_completion.v1",
            "completion_code": display_code,
            "platform": platform,
            "receipt_code": receipt_code,
        }
        if platform == "connect":
            configured = (connect_completion or {}).get(str(ctx.session["batch_id"]))
            if configured is None:
                raise RejectionError("connect_completion_not_configured")
            handoff = {
                "code_version": "connect_completion.v1",
                "completion_code": display_code,
                "completion_url": configured.url or None,
                "platform": "connect",
                "receipt_code": receipt_code,
            }
        elif platform == "prolific" and prolific_code:
            # The configured code is shown verbatim — participants copy/paste it
            # back on Prolific; no redirect URL is served by design.
            handoff = {
                "code_version": "prolific_completion.v1",
                "completion_code": prolific_code,
                "platform": "prolific",
                "receipt_code": receipt_code,
            }
        transition(ctx, bound, "completion", data_use_choice_recorded=choice_rows > 0)
        seq = ctx.append_event("session_completed", actor="system")
        ctx.conn.execute(
            insert(s.completion_receipt).values(
                session_id=ctx.session_id,
                outcome="completed",
                reconciled={
                    "consent_verified": True,
                    "data_use_choice_recorded": True,
                    "final_stage": "completion",
                },
                # Persist the selected handoff so reloads ignore later config changes.
                handoff=handoff,
                final_event_seq=seq.seq,
            )
        )
        # Signed previews can run on a live server. Eligibility belongs to the
        # enrolled session, never to the server's APP_MODE.
        entitlement = entitlement_for(
            ctx,
            bound,
            str(ctx.session["mode"]),
            store=store,
            bonus_payment_enabled=bonus_payment_enabled,
        )

        close_if_complete(ctx.conn, ctx.session.get("batch_group_id"), ctx.session_id)
        return {
            "stage": "completion",
            "entitlement": entitlement,
            "completion_code": handoff["completion_code"],
            "completion_url": handoff.get("completion_url"),
            "completion_platform": platform,
        }

    return handler


# Ops constant (spec §8.1 terminal dispositions): a session with no committed event and
# no command receipt for 30 minutes of wall time is abandoned. Wall clock — never the
# paused active clock — so a participant idle through a check-in pause is still caught.
ABANDONMENT_IDLE_S = 30 * 60


def sweep_abandoned(
    engine: Engine,
    store: ContentStore,
    *,
    idle_window_s: int = ABANDONMENT_IDLE_S,
    limit: int = 200,
    bonus_payment_enabled: bool = True,
) -> int:
    """Abandon sessions idle past ``idle_window_s`` of wall time (BA-08; spec §8.1).

    For every non-terminal, non-completed session whose last committed event and last
    command receipt are both older than the window, the sweep — in one transaction under
    the session lock, re-verifying idleness after locking — transitions to ``abandoned``
    with terminal reason ``heartbeat_timeout``, appends the ``session_abandoned`` audit
    event, closes the open clock interval, expires open request episodes (mirroring the
    deadline cleanup), resolves outstanding jobs, and persists the completion_receipt +
    base payment_entitlement. The participant record (events, messages, answers) is
    preserved untouched; no manipulation exposure is fabricated. Returns the count of
    sessions abandoned this pass.
    """
    with engine.connect() as conn:
        now = db_now(conn)
    cutoff = now - timedelta(seconds=idle_window_s)
    recent_events = exists(
        select(s.event.c.session_id).where(
            s.event.c.session_id == s.session.c.session_id,
            s.event.c.server_time > cutoff,
        )
    )
    recent_receipts = exists(
        select(s.command_receipt.c.session_id).where(
            s.command_receipt.c.session_id == s.session.c.session_id,
            s.command_receipt.c.received_at > cutoff,
        )
    )
    with engine.connect() as conn:
        candidates: list[uuid.UUID] = list(
            conn.execute(
                select(s.session.c.session_id)
                .where(
                    s.session.c.stage.not_in(("completion", *s.TERMINAL_STAGES)),
                    s.session.c.created_at < cutoff,
                    ~recent_events,
                    ~recent_receipts,
                )
                .limit(limit)
            )
            .scalars()
            .all()
        )
    abandoned = 0
    for session_id in candidates:
        with engine.begin() as conn:
            row = lock_session(conn, session_id)
            if row["stage"] == "completion" or row["stage"] in s.TERMINAL_STAGES:
                continue
            # Re-verify idleness under the lock: a command committed after the candidate
            # scan must not be raced by the sweep.
            recent = conn.execute(
                select(func.count())
                .select_from(s.event)
                .where(
                    s.event.c.session_id == session_id,
                    s.event.c.server_time > cutoff,
                )
            ).scalar_one()
            recent += conn.execute(
                select(func.count())
                .select_from(s.command_receipt)
                .where(
                    s.command_receipt.c.session_id == session_id,
                    s.command_receipt.c.received_at > cutoff,
                )
            ).scalar_one()
            if recent:
                continue
            ctx = TxContext(conn, row, now=now)
            try:
                bound = bound_for_session(conn, store, row)
            except RejectionError:
                # Artifact-pin drift is an operator incident, never a guess: leave the
                # session for inspection rather than abandoning on a corrupt binding.
                continue
            transition(ctx, bound, "abandoned", reason="heartbeat_timeout")
            seq = ctx.append_event(
                "session_abandoned",
                actor="system",
                payload={"idle_window_s": idle_window_s},
            )
            final = activetime.close(conn, session_id, now)
            # Open episodes can never be answered now — expire them like the deadline
            # path so agent memory holds no false "outstanding" state.
            engines.expire_open_episodes(
                conn,
                session_id,
                seq=seq.seq,
                at=now,
                at_active_ms=final,
                reason="session_abandoned",
            )
            # Active-time jobs can never become due again once the clock is closed;
            # wall jobs (generations, reveal timers) are cancelled outright.
            scheduler.sweep_active_jobs(ctx, final_active_ms=0 if final is None else final)
            conn.execute(
                update(s.scheduled_job)
                .where(
                    s.scheduled_job.c.session_id == session_id,
                    s.scheduled_job.c.status.in_(("pending", "claimed")),
                    s.scheduled_job.c.clock_basis == "wall",
                )
                .values(
                    status="cancelled",
                    lease_owner=None,
                    lease_expires_at=None,
                    outcome_reason="session_abandoned",
                    finished_at=now,
                    finished_revision=ctx.revision + (1 if ctx.dirty else 0),
                )
            )
            record_terminal_outcome(
                ctx,
                bound,
                outcome="abandoned",
                final_event_seq=seq.seq,
                reconciled={
                    "reason": "heartbeat_timeout",
                    "idle_window_s": idle_window_s,
                    "last_active_ms": final,
                    "abandoned_from_stage": row["stage"],
                },
                store=store,
                bonus_payment_enabled=bonus_payment_enabled,
            )
            ctx._flush_session()
            abandoned += 1
    return abandoned
