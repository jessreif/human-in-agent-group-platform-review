"""Shared command machinery: stage gates, guarded transitions, channel/rate/pending-cap
enforcement and participant-message persistence (audit H2 split of lifecycle.py).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from sqlalchemy import func, insert, select

from app.db import schema as s
from app.db.tx import RejectionError, TxContext
from app.domain import stage_graph as sg
from app.domain.manifests.binding import BoundManifest
from app.services import (
    activetime,
)
from app.services.session_binding import (  # explicit re-exports: historical import path
    variant_for_manifest,
)
from app.timing.policy import resolve_policy

INSTRUMENT_SLOTS = {
    "confidence": "instruments.confidence",
    "peer_ratings": "instruments.peer_ratings",
    "post_questionnaire": "instruments.post_task",
    "open_ended": "instruments.open_ended",
}


# Stages driven through the generic item_response/proceed machinery: bound
# artifact instruments plus the artifact-bound open_ended stage.
INSTRUMENT_STAGES = frozenset((*INSTRUMENT_SLOTS, "open_ended"))

# Which stages accept which command kinds (spec §8.2 command list).
COMMAND_STAGES: dict[str, frozenset[str]] = {
    "consent": frozenset({"consent"}),
    "set_initials": frozenset({"instructions"}),
    "training_step": frozenset({"instructions", "training"}),
    "ready": frozenset({"scripted_connecting"}),
    "answer_update": frozenset({"solo_review", "task"}),
    "assistant_request": frozenset({"solo_review", "task"}),
    "teammate_request": frozenset({"task"}),
    "record_share": frozenset({"solo_review", "task"}),
    "social_reply": frozenset({"task"}),
    "greeting": frozenset({"task"}),
    "pressure_response": frozenset({"checkin_collect"}),
    "pressure_skip": frozenset({"checkin_collect"}),
    "qa_ask": frozenset({"ai_qa"}),
    "item_response": INSTRUMENT_STAGES,
    "item_skip": INSTRUMENT_STAGES,
    "debrief_ack": frozenset({"debrief"}),
    "data_use_choice": frozenset({"data_use_choice"}),
    # Single-target participant click-throughs: reveal resume, task-close continue, and the
    # instrument stages (an explicit proceed is legal only once the stage is complete — the
    # zero-answered-crates confidence case has no item whose commit would advance it).
    # (task -> checkin_collect / answers_locked and checkin_wait -> checkin_reveal are
    # scheduler-driven deadline transitions, not commands.)
    "proceed": frozenset({"checkin_reveal", "answers_locked", "ai_qa", *INSTRUMENT_STAGES}),
    "withdraw": frozenset(set(s.STAGES) - {"completion", *s.TERMINAL_STAGES}),
    "resume": frozenset(set(s.STAGES) - {"completion", *s.TERMINAL_STAGES}),
    "takeover": frozenset(set(s.STAGES) - {"completion", *s.TERMINAL_STAGES}),
}


def gate(ctx: TxContext, kind: str) -> None:
    if ctx.stage not in COMMAND_STAGES.get(kind, frozenset()):
        raise RejectionError("wrong_stage")
    if ctx.stage in s.TERMINAL_STAGES:
        raise RejectionError("session_terminal")


def transition(
    ctx: TxContext,
    bound: BoundManifest,
    to_stage: str,
    *,
    reason: str | None = None,
    consent_action: str | None = None,
    data_use_choice_recorded: bool = False,
) -> None:
    sg.check_transition(
        variant_for_manifest(bound.manifest),
        ctx.stage,
        to_stage,
        terminal_reason=reason,
        consent_action=consent_action,
        arm_assigned=ctx.session["arm"] is not None,
        data_use_choice_recorded=data_use_choice_recorded,
    )
    ctx.set_stage(to_stage, reason=reason)


def channel_open(ctx: TxContext, bound: BoundManifest, channel: str) -> bool:
    """Resolved-policy channel gate (INV-0903): commands for a channel the current phase
    disables — e.g. ``sharing``/``assistant_prompting`` during review-first solo review —
    are rejected server-side, not just hidden in the UI."""
    policy = resolve_policy(bound, ctx.session["arm"])
    active = activetime.active_ms(ctx.conn, ctx.session_id, ctx.now)
    return channel in policy.channels_open(active)


def resolved_policy(ctx: TxContext, bound: BoundManifest) -> Any:
    """Resolve the session's policy; pre-allocation commands (arm not yet assigned) fall
    back to the first arm — concurrency and rate-limit surfaces are arm-invariant."""
    arm = ctx.session["arm"] or bound.manifest.arms[0]
    return resolve_policy(bound, arm)


def enforce_pending_cap(ctx: TxContext, policy: Any, *, responder: str, limit_field: str) -> None:
    """INV-0608 concurrency caps: open request episodes per responder seat (or the
    assistant) are bounded by the resolved policy. Over-cap requests are rejected with the
    declared ``over_cap`` semantics (``reject_visible``: a durable rejected command receipt
    the participant can see), inside the command transaction so the count cannot race."""
    concurrency = policy.controls.get("concurrency") or {}
    limit = concurrency.get(limit_field)
    if limit is None:
        return
    pending = select(s.request_episode.c.request_id).where(
        s.request_episode.c.session_id == ctx.session_id,
        s.request_episode.c.status == "pending",
    )
    open_count = ctx.conn.execute(
        select(func.count())
        .select_from(s.request)
        .where(
            s.request.c.session_id == ctx.session_id,
            s.request.c.responder == responder,
            s.request.c.request_id.in_(pending),
        )
    ).scalar_one()
    if int(open_count) >= int(limit):
        raise RejectionError("over_cap")


def enforce_rate_limit(ctx: TxContext, bound: BoundManifest) -> None:
    """INV-0608 rate limits: participant operational commands are bounded per rolling
    60-second window by the resolved policy. An over-limit command is rejected
    (429-class ``rate_limited``) and still leaves a durable rejected receipt, so the
    count is self-consistent under retries."""
    policy = resolved_policy(ctx, bound)
    rate_limits = policy.controls.get("rate_limits") or {}
    limit = rate_limits.get("operational_commands_per_minute")
    if limit is None:
        return
    window_start = ctx.now - timedelta(seconds=60)
    count = ctx.conn.execute(
        select(func.count())
        .select_from(s.command_receipt)
        .where(
            s.command_receipt.c.session_id == ctx.session_id,
            s.command_receipt.c.received_at >= window_start,
        )
    ).scalar_one()
    if int(count) >= int(limit):
        raise RejectionError("rate_limited")


def participant_message(
    ctx: TxContext,
    *,
    body: str,
    channel: str = "team",
    parent_message_id: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
    crate: int | None = None,
    active_ms: int | None = None,
    payload_extra: Mapping[str, Any] | None = None,
) -> tuple[uuid.UUID, Any]:
    """The participant's own message: durable row + event, just like agent deliveries.
    Structured asks (crate pickers) surface in the channel through this path too —
    INV-0604/UX: an accepted request must be visible to the participant, not only to
    the responder."""
    mid = uuid.uuid4()
    seq = ctx.append_event(
        "message_accepted",
        actor="participant",
        active_ms=active_ms,
        payload={
            "message_id": str(mid),
            "channel": channel,
            "body": body,
            "crate": crate,
            "request_id": None if request_id is None else str(request_id),
            **dict(payload_extra or {}),
        },
    )
    ctx.conn.execute(
        insert(s.message).values(
            session_id=ctx.session_id,
            message_id=mid,
            actor="participant",
            channel=channel,
            parent_message_id=parent_message_id,
            request_id=request_id,
            crate=crate,
            body=body,
            status="delivered",
            accepted_seq=seq.seq,
            committed_seq=seq.seq,
            created_at=ctx.now,
            delivered_at=ctx.now,
        )
    )
    return mid, seq
