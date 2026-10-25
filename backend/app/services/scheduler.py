"""Durable job scheduling and world-engine job handlers (WP06; spec §6.1-6.6).

Job taxonomy on two declared clock bases (spec §6.3 ``clock_basis``):

- ``active_task`` jobs — every task-surface delivery and every active-time transition:
  ``scripted_request``, ``pressure_remark``, ``crosstalk``, ``warning``, ``greeting_prompt``,
  ``deliver``
  (all response classes), ``checkin`` and ``deadline``. They can only become due while the
  session's clock interval is ``running`` (see ``tx.due_clause``), which is precisely how
  the check-in pause suspends agent output on the same axis.
- ``wall`` jobs — things that are wall-time by definition: ``generate`` (model calls),
  ``checkin_reveal`` (the 3.5 s collection presentation) and ``checkin_inactivity`` (the
  inactivity cap).

``schedule_task_jobs`` runs inside the ``ready`` command transaction so a session's whole
schedule exists before the participant ever sees the task. Commit-time authorization is the
guard rail: every handler revalidates stage/deadline/recipient/due-ness under the session
lock, and ``advance_due`` runs due transition jobs inline inside ordinary commands so a
lagging scheduler can never let an answer slip past the 360 s freeze.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import Connection, func, insert, select, update
from sqlalchemy.engine import Engine

from app.db import schema as s
from app.db.tx import Deferred, TxContext
from app.domain.manifests.artifacts import (
    CheckinCopyArtifact,
    RequestScriptArtifact,
    VoiceBankArtifact,
)
from app.domain.manifests.binding import BoundManifest
from app.domain.manifests.store import ContentStore
from app.domain.rng import DrawLog, RandomStreams
from app.domain.schedules import (
    plan_job_draws,
    plan_rating_seats,
)
from app.flags import DEFAULT_FLAGS, FeatureFlags
from app.measurement import scoring
from app.providers.base import (
    Message,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    TextProvider,
)
from app.providers.local import MODEL_PIN_PARAM
from app.services import (
    activetime,
    claims,
    engines,
    output_claims,
    prompts,
    retrieval,
    worlds,
)
from app.services.outcomes import record_terminal_outcome
from app.services.session_binding import bound_for_session, variant_for_manifest
from app.services.sessions import record_draws
from app.timing import warnings as warn
from app.timing.mechanisms import Generation, SampledComponents, plan_schedule
from app.timing.policy import ResolvedPolicy, resolve_policy, time_pressure_for_arm

logger = logging.getLogger(__name__)

TASK_STAGES = frozenset({"solo_review", "task"})
CHECKIN_STAGES = frozenset({"checkin_collect", "checkin_wait", "checkin_reveal"})
OPEN_TASK_STAGES = TASK_STAGES | CHECKIN_STAGES
# Transition jobs also run inline inside ordinary commands (advance_due).
TRANSITION_KINDS = frozenset({"checkin", "deadline", "connecting", "task_open", "solo_review_end"})
GREETING_PROMPT_ACTIVE_MS = 2000
DEFERRED_POLL_MS = 30000
# INV-0701 scripted connecting presentation (spec §5.2): the shared demo constants —
# identical in both demos (S2/S3), so they are reference constants like
# GREETING_PROMPT_ACTIVE_MS rather than per-profile policy. PM's join lands at
# CONNECT_PM_JOIN_WALL_MS of wall time; pressing Start holds CONNECT_TASK_OPEN_WALL_MS of
# "waiting for teammates" before the task opens (the active clock starts at task-open).
CONNECT_PM_JOIN_WALL_MS = 10_600
CONNECT_TASK_OPEN_WALL_MS = 1_800
# INV-0702: the PE demo's fixed crosstalk relation — the other seat's voice line lands
# 28 s of active time after the scripted request it is parented to (REF-PE-04).
CROSSTALK_AFTER_REQUEST_MS = 28_000


class JobPayloadError(RuntimeError):
    pass


def _streams(ctx: TxContext, log: DrawLog | None = None) -> RandomStreams:
    return RandomStreams(ctx.session["seed_hex"], log or DrawLog())


def _policy(ctx: TxContext, bound: BoundManifest) -> ResolvedPolicy:
    return resolve_policy(bound, ctx.session["arm"])


def _active(ctx: TxContext) -> int:
    return activetime.active_ms(ctx.conn, ctx.session_id, ctx.now)


def _transition(
    ctx: TxContext, bound: BoundManifest, to_stage: str, *, reason: str | None = None
) -> None:
    from app.domain import stage_graph as sg

    sg.check_transition(
        variant_for_manifest(bound.manifest),
        ctx.stage,
        to_stage,
        terminal_reason=reason,
    )
    ctx.set_stage(to_stage, reason=reason)


# ----- scheduling -------------------------------------------------------------------------


def _schedule_active(
    ctx: TxContext,
    logical_key: str,
    *,
    kind: str,
    due_active_ms: int,
    payload: Mapping[str, Any] | None = None,
    active_now: int = 0,
    parent_event_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, bool]:
    return ctx.schedule_job(
        logical_key,
        kind=kind,
        due_active_ms=due_active_ms,
        claimable_at=activetime.wall_due_estimate(
            active_now,
            due_active_ms,
            ctx.now,
            factor=activetime.speed_factor_for(ctx.session),
        ),
        payload=payload,
        parent_event_id=parent_event_id,
    )


def _schedule_wall(
    ctx: TxContext,
    logical_key: str,
    *,
    kind: str,
    due_wall_at: datetime,
    payload: Mapping[str, Any] | None = None,
) -> tuple[uuid.UUID, bool]:
    return ctx.schedule_job(
        logical_key,
        kind=kind,
        due_wall_at=due_wall_at,
        claimable_at=due_wall_at,
        payload=payload,
    )


def schedule_task_jobs(ctx: TxContext, bound: BoundManifest, *, time_pressure: bool = True) -> None:
    """Persist the session's entire active-time schedule when the task clock starts.

    Scripted-request plans come from the persisted world snapshot — replay never re-derives
    them — while timing draws are sampled here once on the ``jobs`` stream and recorded.

    ``time_pressure`` is the TIME_PRESSURE_ENABLED deployment flag: when 0 the
    pressure manipulation jobs (check-in, warning, teammate pressure_remark) are
    never scheduled, so no pop-up, warning, or urgency line can exist — the
    deadline job still runs, since the task budget is not part of the
    manipulation.
    """
    time_pressure = time_pressure_for_arm(ctx.session.get("arm"), enabled=time_pressure)
    policy = _policy(ctx, bound)
    content = worlds.snapshot_content(ctx.conn, ctx.session_id)
    for plan in content["scripted_requests"]:
        _schedule_active(
            ctx,
            f"scripted_request.{plan['index']}",
            kind="scripted_request",
            due_active_ms=int(plan["at_active_ms"]),
            payload={
                "seat": plan["seat"],
                "crate": plan["crate"],
                "index": plan["index"],
                "target": plan["target"],
            },
        )
    _schedule_active(
        ctx,
        "greeting_prompt",
        kind="greeting_prompt",
        due_active_ms=GREETING_PROMPT_ACTIVE_MS,
    )
    remarks = policy.controls.get("pressure_remarks")
    if remarks is not None and time_pressure:
        # Fair-coin seat order, drawn once per session on the logged ``jobs`` stream:
        # ``first`` speaks the first remark, the other seat answers.
        log = DrawLog()
        first = "JT" if _streams(ctx, log).coin("jobs", "pressure_remarks", "first_seat") else "PM"
        record_draws(ctx.conn, ctx.session_id, log.records)
        other = "PM" if first == "JT" else "JT"
        # Entries are [at_ms, speaker] or [at_ms, speaker, speaker_level] —
        # the level pin (v15 mixed) is honored at dispatch, not here.
        for index, entry in enumerate(remarks["schedule"]):
            at_ms, speaker = int(entry[0]), str(entry[1])
            _schedule_active(
                ctx,
                f"pressure_remark.{index}",
                kind="pressure_remark",
                due_active_ms=at_ms,
                payload={"index": index, "seat": first if speaker == "first" else other},
            )
    if time_pressure:
        for plan in policy.warnings:
            _schedule_active(
                ctx,
                plan.logical_job_key,
                kind="warning",
                due_active_ms=plan.due_active_ms,
                payload={"plan": plan.as_dict()},
            )
    _schedule_active(ctx, "deadline", kind="deadline", due_active_ms=policy.budget_ms)
    if policy.checkin is not None and time_pressure:
        _schedule_active(
            ctx,
            "checkin",
            kind="checkin",
            due_active_ms=int(policy.checkin["at_active_ms"]),
            payload={"checkin": dict(policy.checkin)},
        )
    if not time_pressure:
        # The check-in is also the solo_review -> task gate in review-first
        # flows (checkin.at_active_s is pinned to the phase boundary), so a
        # suppressed check-in leaves review-first sessions stranded. The
        # neutral solo_review_end job ends the review window at the same
        # active-time boundary without any manipulation surface.
        solo = next((p for p in policy.phases if p.name == "solo_review"), None)
        if solo is not None:
            _schedule_active(
                ctx,
                "solo_review_end",
                kind="solo_review_end",
                due_active_ms=int(solo.until_ms),
            )
    ctx.append_event("task_schedule_committed", actor="scheduler")


def schedule_response(
    ctx: TxContext,
    bound: BoundManifest,
    *,
    request_key: str,
    response_class: str,
    seat: str,
    grant_ms: int,
    payload: Mapping[str, Any],
    generation: Generation | None,
    body_chars: int | None = None,
    parent_event_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Sample this job's timing components once, compute the declared schedule and persist the
    ``deliver`` job due at ``eligible_ms``. Returns the persisted plan (spec §6.3/§6.5).
    """
    policy = _policy(ctx, bound)
    cls = policy.class_policy(response_class, seat)
    logical = f"deliver.{request_key}"
    log = DrawLog()
    streams = _streams(ctx, log)
    draws = plan_job_draws(cls, streams, logical_job_key=logical)
    record_draws(ctx.conn, ctx.session_id, log.records)
    plan = plan_schedule(cls, grant_ms, draws.components, generation, body_chars=body_chars)
    draw_payload = {
        name: {
            "seconds": d.seconds,
            "raw_seconds": d.raw_seconds,
            "attempts": d.attempts,
            "truncated": d.truncated,
            "method": d.method,
        }
        for name, d in draws.draws.items()
    }
    plan_payload = {
        "mechanism": plan.mechanism,
        "grant_ms": plan.grant_ms,
        "typing_ms": plan.typing_ms,
        "scheduled_ms": plan.scheduled_ms,
        "effective_ready_ms": plan.effective_ready_ms,
        "eligible_ms": plan.eligible_ms,
        "readiness_overrun_ms": plan.readiness_overrun_ms,
        "fallback_reason": plan.fallback_reason,
    }
    _schedule_active(
        ctx,
        logical,
        kind="deliver",
        due_active_ms=plan.eligible_ms,
        payload={
            **dict(payload),
            "response_class": response_class,
            "seat": seat,
            "grant_ms": grant_ms,
            "components": {
                "delay_ms": draws.components.delay_ms,
                "platform_ms": draws.components.platform_ms,
                "think_ms": draws.components.think_ms,
                "floor_ms": draws.components.floor_ms,
            },
            "draws": draw_payload,
            "plan": plan_payload,
        },
        active_now=_active(ctx),
        parent_event_id=parent_event_id,
    )
    return plan_payload


# ----- commit-time guards ------------------------------------------------------------------


def _not_due(ctx: TxContext, job: Mapping[str, Any]) -> Deferred | None:
    """Return a Deferred if the job is not due on its own clock under the lock."""
    if job["clock_basis"] != "active_task":
        if job["due_wall_at"] is not None and job["due_wall_at"] > ctx.now:
            return Deferred(job["due_wall_at"])
        return None
    due = int(job["due_active_ms"])
    if not activetime.is_running(ctx.conn, ctx.session_id):
        # Paused or closed clock: nothing active-basis can be due right now.
        return Deferred(ctx.now + timedelta(milliseconds=DEFERRED_POLL_MS))
    if _active(ctx) < due:
        return Deferred(
            activetime.wall_due_estimate(
                _active(ctx),
                due,
                ctx.now,
                factor=activetime.speed_factor_for(ctx.session),
            )
        )
    return None


def sweep_active_jobs(ctx: TxContext, *, final_active_ms: int) -> None:
    """Resolve every still-open ``active_task`` job once the task clock is closed for good.

    A closed clock can never satisfy the active due-clause, so without this sweep late
    warnings and deliveries would linger ``pending`` forever. Suppressing them here is the
    durable record of spec §6.2 "suppress all late task output" — each row keeps its full
    plan/payload for the export."""
    j = s.scheduled_job
    rows = (
        ctx.conn.execute(
            select(j.c.job_id, j.c.kind, j.c.logical_job_key).where(
                j.c.session_id == ctx.session_id,
                j.c.status.in_(("pending", "claimed")),
                j.c.clock_basis == "active_task",
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        ctx.conn.execute(
            update(j)
            .where(j.c.job_id == row["job_id"])
            .values(
                status="suppressed",
                lease_owner=None,
                lease_expires_at=None,
                outcome_reason="task_closed",
                finished_at=ctx.now,
                finished_revision=ctx.revision + (1 if ctx.dirty else 0),
            )
        )
        if row["kind"] == "warning":
            ctx.append_event(
                "warning_suppressed",
                actor="system",
                payload={"job": row["logical_job_key"], "reason": "task_closed"},
            )
    if rows:
        ctx.append_event(
            "schedule_swept",
            actor="system",
            active_ms=final_active_ms,
            payload={
                "suppressed": len(rows),
                "jobs": [row["logical_job_key"] for row in rows],
            },
        )


def _task_surface_guard(
    ctx: TxContext, bound: BoundManifest, job: Mapping[str, Any]
) -> str | Deferred | None:
    """Commit-time authorization shared by every task-surface delivery (spec §6.4):
    stage, deadline, due-ness and prior delivery are rechecked under the session lock.
    """
    not_due = _not_due(ctx, job)
    if not_due is not None:
        return not_due
    if ctx.stage in s.TERMINAL_STAGES or ctx.stage == "completion":
        return "cancelled"
    if ctx.stage not in OPEN_TASK_STAGES:
        return "suppressed"  # answers locked / post-task: late task output stays auditable
    if _active(ctx) >= _policy(ctx, bound).budget_ms:
        return "suppressed"
    if ctx.stage == "solo_review":
        # Channels are closed during solo review: delivery waits for the task phase. It is
        # still on the active axis — defer rather than suppress.
        return Deferred(ctx.now + timedelta(milliseconds=DEFERRED_POLL_MS))
    return None


# ----- individual job handlers --------------------------------------------------------------


def _checkin(ctx: TxContext, bound: BoundManifest, job: Mapping[str, Any]) -> str | Deferred:
    """Open the pressure check-in overlay (``checkin_collect``) once per session; a
    redelivered job after the cycle completed is a no-op rather than a second overlay."""
    if ctx.stage in CHECKIN_STAGES:
        return "cancelled"  # already inside a check-in (e.g. command-path ran it first)
    if ctx.stage not in TASK_STAGES:
        return "suppressed" if ctx.stage in ("confidence", "peer_ratings") else "cancelled"
    not_due = _not_due(ctx, job)
    if not_due is not None:
        return not_due
    policy = _policy(ctx, bound)
    if policy.checkin is None:
        return "cancelled"
    # One pressure_checkin per session (policy carries a single check-in): a
    # retried or redelivered job arriving after the cycle completed — stage is
    # back at "task" — must not open a second overlay.
    if (
        ctx.conn.execute(
            select(s.manipulation_exposure.c.exposure_id)
            .where(
                s.manipulation_exposure.c.session_id == ctx.session_id,
                s.manipulation_exposure.c.kind == "pressure_checkin",
            )
            .limit(1)
        ).first()
        is not None
    ):
        return "cancelled"
    if activetime.open_interval(ctx.conn, ctx.session_id) is None:
        return "cancelled"  # no active clock running: pre-task or already closed
    active = activetime.pause(ctx.conn, ctx.session_id, reason="pressure_checkin", now=ctx.now)
    _transition(ctx, bound, "checkin_collect")
    log = DrawLog()
    seats = plan_rating_seats(policy, _streams(ctx, log))
    record_draws(ctx.conn, ctx.session_id, log.records)
    copy_art = bound.artifacts.get("manipulation.copy_ref")
    copy = (
        copy_art.artifact.body
        if copy_art is not None and isinstance(copy_art.artifact, CheckinCopyArtifact)
        else None
    )
    seq = ctx.append_event(
        "checkin_open",
        actor="system",
        active_ms=active,
        payload={
            "at_active_ms": active,
            "assigned_values": dict(seats.values),
            "copy": None if copy is None else copy.model_dump(mode="json"),
        },
    )
    ctx.conn.execute(
        insert(s.manipulation_exposure).values(
            session_id=ctx.session_id,
            exposure_id=uuid.uuid4(),
            kind="pressure_checkin",
            artifact_id=copy_art.key.rsplit("@v", 1)[0] if copy_art else None,
            artifact_version=int(copy_art.key.rsplit("@v", 1)[1]) if copy_art else None,
            due_active_ms=int(job["due_active_ms"]),
            committed_seq=seq.seq,
            assigned_values=dict(seats.values),
            status="committed",
        )
    )
    cap_ms = int(policy.checkin["inactivity_cap_wall_ms"])
    _schedule_wall(
        ctx,
        "checkin_inactivity",
        kind="checkin_inactivity",
        due_wall_at=ctx.now + timedelta(milliseconds=cap_ms),
        payload={"outcome": policy.checkin["inactivity_outcome"]},
    )
    return "committed"


def _solo_review_end(
    ctx: TxContext, bound: BoundManifest, job: Mapping[str, Any]
) -> str | Deferred:
    """End the review window under TIME_PRESSURE_ENABLED=0 — the flag-on
    flow exits ``solo_review`` through the check-in loop; with the check-in
    suppressed this bare transition preserves the review window and opens the
    team task at the same active-time boundary."""
    if ctx.stage != "solo_review":
        return "cancelled"
    not_due = _not_due(ctx, job)
    if not_due is not None:
        return not_due
    _transition(ctx, bound, "task")
    ctx.append_event(
        "solo_review_ended",
        actor="system",
        active_ms=_active(ctx),
        payload={"reason": "time_pressure_disabled"},
    )
    ctx.append_event("stage_proceeded", actor="system", payload={"to": "task"})
    return "committed"


def _checkin_reveal(ctx: TxContext, bound: BoundManifest, _job: Mapping[str, Any]) -> str:
    if ctx.stage != "checkin_wait":
        return "cancelled"
    _transition(ctx, bound, "checkin_reveal")
    exposure = (
        ctx.conn.execute(
            select(s.manipulation_exposure)
            .where(
                s.manipulation_exposure.c.session_id == ctx.session_id,
                s.manipulation_exposure.c.kind == "pressure_checkin",
                s.manipulation_exposure.c.rendered_seq.is_(None),
            )
            .order_by(s.manipulation_exposure.c.scheduled_at.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    seq = ctx.append_event(
        "checkin_reveal",
        actor="system",
        payload={
            "assigned_values": (None if exposure is None else dict(exposure["assigned_values"])),
            # INV-0902: the persistent strip/reveal shows the participant's own committed
            # rating alongside the teammate chips (demo parity); null when skipped.
            "own_rating": None if exposure is None else exposure["own_rating"],
        },
    )
    if exposure is not None:
        ctx.conn.execute(
            update(s.manipulation_exposure)
            .where(s.manipulation_exposure.c.exposure_id == exposure["exposure_id"])
            .values(rendered_seq=seq.seq, status="rendered")
        )
    return "committed"


def _checkin_inactivity(
    ctx: TxContext,
    bound: BoundManifest,
    job: Mapping[str, Any],
    *,
    store: ContentStore | None = None,
    bonus_payment: bool = True,
) -> str:
    if ctx.stage not in CHECKIN_STAGES:
        return "cancelled"
    outcome = str(job["payload"].get("outcome", "technical_stop/checkin_incomplete"))
    reason = outcome.rsplit("/", 1)[-1]
    _transition(ctx, bound, "technical_stop", reason=reason)
    seq = ctx.append_event(
        "technical_stop",
        actor="system",
        payload={"outcome": outcome, "stage": "checkin"},
    )
    ctx.conn.execute(
        insert(s.result).values(
            session_id=ctx.session_id,
            scoring_version="unscored.v0",
            status="technical_stop",
            correct_total=0,
            correct_teamdep=0,
            correct_ownfile=0,
            counts={},
            finalized_seq=seq.seq,
        )
    )
    final = activetime.close(ctx.conn, ctx.session_id, ctx.now)
    # AB-19: a terminal stop can never be answered again — expire open episodes
    # explicitly like the deadline/abandoned paths instead of leaving them pending.
    engines.expire_open_episodes(
        ctx.conn,
        ctx.session_id,
        seq=seq.seq,
        at=ctx.now,
        at_active_ms=final,
        reason="session_abandoned",
    )
    sweep_active_jobs(ctx, final_active_ms=0 if final is None else final)
    # Spec §8.1: the technical-stop disposition gets its durable completion_receipt and
    # base payment_entitlement (pinned policy) in the same transaction as the stop.
    record_terminal_outcome(
        ctx,
        bound,
        outcome="technical_stop",
        final_event_seq=seq.seq,
        store=store,
        bonus_payment_enabled=bonus_payment,
        reconciled={"reason": reason, "outcome_label": outcome},
    )
    return "committed"


def _deadline(ctx: TxContext, bound: BoundManifest, job: Mapping[str, Any]) -> str | Deferred:
    """Task deadline: atomically freeze the latest server-accepted answer for each
    answered crate (spec §6.2), record ``task_deadline`` and move to
    ``answers_locked``. Never due while a check-in has paused the active clock."""
    if ctx.stage not in OPEN_TASK_STAGES:
        return "cancelled"
    if ctx.stage in CHECKIN_STAGES:
        # A check-in pauses the active clock below the budget, so the deadline can never be
        # due there; the resume re-arms it on the same axis.
        return Deferred(ctx.now + timedelta(milliseconds=DEFERRED_POLL_MS))
    not_due = _not_due(ctx, job)
    if not_due is not None:
        return not_due
    # Atomically freeze the latest server-accepted answer for each answered crate (spec §6.2).
    active = _active(ctx)
    seq = ctx.append_event("task_deadline", actor="system", active_ms=active)
    latest = (
        ctx.conn.execute(
            select(
                s.answer_revision.c.crate,
                func.max(s.answer_revision.c.revision).label("revision"),
            )
            .where(s.answer_revision.c.session_id == ctx.session_id)
            .group_by(s.answer_revision.c.crate)
        )
        .mappings()
        .all()
    )
    for row in latest:
        location: Any = ctx.conn.execute(
            select(s.answer_revision.c.location_id).where(
                s.answer_revision.c.session_id == ctx.session_id,
                s.answer_revision.c.crate == row["crate"],
                s.answer_revision.c.revision == row["revision"],
            )
        ).scalar_one()
        ctx.conn.execute(
            insert(s.answer_final).values(
                session_id=ctx.session_id,
                crate=row["crate"],
                revision=row["revision"],
                location_id=location,
                finalized_seq=seq.seq,
                finalized_at=ctx.now,
            )
        )
    final = activetime.close(ctx.conn, ctx.session_id, ctx.now)
    _transition(ctx, bound, "answers_locked")
    # Persist the scored result in the same transaction as the freeze: the
    # dashboard's analytic cohort keys on ``result.status='scored'`` under the
    # pinned scoring version, so a completed task must always carry this row.
    score = scoring.score_session(ctx.conn, ctx.session_id)
    ctx.conn.execute(
        insert(s.result).values(
            session_id=ctx.session_id,
            scoring_version=score.scoring_version,
            status="scored",
            correct_total=score.correct_total.numerator or 0,
            correct_teamdep=score.correct_teamdep.numerator or 0,
            correct_ownfile=score.correct_ownfile.numerator or 0,
            counts=score.as_dict(),
            finalized_seq=seq.seq,
            finalized_at=ctx.now,
        )
    )
    ctx.append_event(
        "answers_frozen",
        actor="system",
        payload={"crates": [int(r["crate"]) for r in latest]},
        parent_event_id=seq.event_id,
    )
    # INV-0704: episodes still open at the freeze expire explicitly — they can never be
    # answered now, and leaving them open would keep false "outstanding" state in agent
    # memory after the task closes.
    engines.expire_open_episodes(
        ctx.conn,
        ctx.session_id,
        seq=seq.seq,
        at=ctx.now,
        at_active_ms=active,
        reason="deadline",
    )
    sweep_active_jobs(ctx, final_active_ms=0 if final is None else final)
    return "committed"


def _warning(ctx: TxContext, _bound: BoundManifest, job: Mapping[str, Any]) -> str | Deferred:
    """Deliver the bound one-minute-left warning (time-pressure arms only; suppressed
    under ``nopress`` by ``dispatch``)."""
    plan = warn.WarningPlan(
        artifact_key=str(job["payload"]["plan"]["artifact"]),
        artifact_sha256=str(job["payload"]["plan"]["artifact_sha256"]),
        logical_job_key=str(job["payload"]["plan"]["logical_job_key"]),
        text=str(job["payload"]["plan"]["text"]),
        sender=str(job["payload"]["plan"]["sender"]),
        clock_basis=str(job["payload"]["plan"]["clock_basis"]),
        due_active_ms=int(job["payload"]["plan"]["due_active_ms"]),
        low_time_from_ms=int(job["payload"]["plan"]["low_time_ms"][0]),
        low_time_until_ms=int(job["payload"]["plan"]["low_time_ms"][1]),
        when_task_closed=str(job["payload"]["plan"]["when_task_closed"]),
        exposure_record=str(job["payload"]["plan"]["exposure_record"]),
        receipts=tuple(job["payload"]["plan"]["receipts"]),
    )
    committed = ctx.conn.execute(
        select(func.count())
        .select_from(s.manipulation_exposure)
        .where(
            s.manipulation_exposure.c.session_id == ctx.session_id,
            s.manipulation_exposure.c.kind == "one_minute_warning",
            s.manipulation_exposure.c.committed_seq.is_not(None),
        )
    ).scalar_one()
    decision = warn.decide_warning_commit(
        plan,
        active_ms=_active(ctx),
        task_closed=ctx.stage not in OPEN_TASK_STAGES,
        already_committed=committed > 0,
    )
    if decision is warn.WarningDecision.ALREADY_COMMITTED:
        return "cancelled"
    if decision is warn.WarningDecision.SUPPRESS_TASK_CLOSED:
        ctx.append_event(
            "warning_suppressed",
            actor="system",
            payload={"job": job["logical_job_key"]},
        )
        return "suppressed"
    if decision is warn.WarningDecision.NOT_DUE:
        return _not_due(ctx, job) or Deferred(ctx.now + timedelta(milliseconds=DEFERRED_POLL_MS))
    mid, seq = engines.deliver_message(
        ctx,
        actor="system",
        channel="system",
        body=plan.text,
        scheduled_for_active_ms=plan.due_active_ms,
        active_ms=_active(ctx),
    )
    ctx.conn.execute(
        insert(s.manipulation_exposure).values(
            session_id=ctx.session_id,
            exposure_id=uuid.uuid4(),
            kind="one_minute_warning",
            artifact_id=plan.artifact_key.rsplit("@v", 1)[0],
            artifact_version=int(plan.artifact_key.rsplit("@v", 1)[1]),
            due_active_ms=plan.due_active_ms,
            committed_seq=seq.seq,
            status="committed",
        )
    )
    ctx.append_event(
        "warning_committed",
        actor="system",
        payload={
            "job": job["logical_job_key"],
            "message_id": str(mid),
            "low_time": True,
        },
    )
    return "committed"


def _greeting_prompt(
    ctx: TxContext, bound: BoundManifest, job: Mapping[str, Any]
) -> str | Deferred:
    guard = _task_surface_guard(ctx, bound, job)
    if guard is not None:
        return guard
    stored = bound.artifacts.get("teammates.voice_bank")
    options = ["Hi!", "Hello!", "Hey!"]
    if stored is not None and isinstance(stored.artifact, VoiceBankArtifact):
        options = list(stored.artifact.body.seats["JT"].greetings) or options
    engines.deliver_message(
        ctx,
        actor="system",
        channel="system",
        body="Let your teammates know you are here.",
        voice_kind=None,
        active_ms=_active(ctx),
    )
    ctx.append_event(
        "greeting_prompt",
        actor="system",
        payload={"options": options},
        # The client anchors its greeting-button window on this stamp: solo review
        # defers the prompt to channel open, so task-start anchoring hid the
        # buttons entirely in review-first sessions (UX round 6, item 4).
        active_ms=_active(ctx),
    )
    return "committed"


def _pressure_remark(
    ctx: TxContext, bound: BoundManifest, job: Mapping[str, Any]
) -> str | Deferred:
    """Deliver one bound arm-specific remark as an ordinary teammate team-chat line.

    The text is fixed approved copy for the session's arm (``manipulation.remark_lines``),
    identical across the AI and scripted builds — it is never generated, so the
    generated-output gate does not apply; the delivery itself is the exposure record."""
    guard = _task_surface_guard(ctx, bound, job)
    if guard is not None:
        return guard
    policy = _policy(ctx, bound)
    lines = policy.manipulation.get("remark_lines") or []
    index, seat = int(job["payload"]["index"]), str(job["payload"]["seat"])
    if index >= len(lines):
        return "failed"
    line = lines[index]
    if not isinstance(line, str):
        # ``mixed`` arm (v15): remark text is keyed by the speaker seat's
        # scripted workload level — read the level from the check-in's recorded
        # assigned_values (the seat with the max value is the high seat). The
        # exposure row always exists by then: remarks ride the active clock
        # past the check-in's pause.
        exposure = (
            ctx.conn.execute(
                select(s.manipulation_exposure.c.assigned_values)
                .where(
                    s.manipulation_exposure.c.session_id == ctx.session_id,
                    s.manipulation_exposure.c.kind == "pressure_checkin",
                )
                .limit(1)
            )
            .mappings()
            .first()
        )
        if exposure is None:
            return "failed"
        assigned = dict(exposure["assigned_values"])
        if len(set(assigned.values())) != 2:
            return "failed"
        # Owner direction 2026-10-18: under ``mixed`` the exchange is always
        # heavy-first / light-second — the scheduled seat coin is ignored and
        # the remark's position ("first"/"second") resolves BOTH the speaking
        # seat (whoever holds that level) and the line, so the heavy teammate
        # leads regardless of which seat drew the high value.
        schedule = policy.controls.get("pressure_remarks", {}).get("schedule") or []
        entry = schedule[index] if index < len(schedule) else None
        pinned = entry[2] if entry is not None and len(entry) > 2 else None
        speaker_tag = entry[1] if entry is not None else "first"
        level = (
            pinned if pinned in ("high", "low") else ("high" if speaker_tag == "first" else "low")
        )
        seat = (
            max(assigned, key=assigned.get) if level == "high" else min(assigned, key=assigned.get)
        )
        line = line[level]
    _mid, ev = engines.deliver_message(
        ctx,
        actor=seat,
        channel="team",
        body=line,
        active_ms=_active(ctx),
    )
    ctx.append_event(
        "pressure_remark_delivered",
        actor="scheduler",
        active_ms=_active(ctx),
        payload={"index": index, "seat": seat, "message_seq": ev.seq},
    )
    return "committed"


def _scripted_request(
    ctx: TxContext, bound: BoundManifest, job: Mapping[str, Any]
) -> str | Deferred:
    """Deliver one scripted teammate request: deterministic, replayable phrasing via a
    keyed draw per (seat, index), a session-wide request permutation, and the request
    episode the participant may answer."""
    guard = _task_surface_guard(ctx, bound, job)
    if guard is not None:
        return guard
    payload = job["payload"]
    seat, crate, index = payload["seat"], int(payload["crate"]), int(payload["index"])
    script = bound.artifacts.get("timing.scripted_requests.script")
    template = "Requesting intel on Crate {n}."
    if script is not None and isinstance(script.artifact, RequestScriptArtifact):
        templates = script.artifact.body.templates
        if script.artifact.body.selection == "uniform_per_request" and len(templates) > 1:
            # UX round 4, item 10: vary the scripted request's phrasing — a keyed
            # ``jobs`` draw per (seat, index) keeps the pick deterministic and
            # replayable, and the draw is logged like every other pool draw.
            log = DrawLog()
            idx = int(
                _streams(ctx, log).uniform("jobs", "scripted_request", seat, str(index))
                * len(templates)
            )
            record_draws(ctx.conn, ctx.session_id, log.records)
            template = templates[idx]
        elif script.artifact.body.selection == "uniform_without_replacement":
            # One session-wide permutation, not a seat-specific draw. Request indices
            # stay unique across seats and retries; old pinned scripts keep their policy.
            log = DrawLog()
            order = _streams(ctx, log).shuffle(
                "jobs", templates, "scripted_request_templates", script.key
            )
            record_draws(ctx.conn, ctx.session_id, log.records)
            template = order[index]
        else:
            template = templates[0]
    body = template.replace("{n}", str(crate))
    request_id = uuid.uuid4()
    seq = ctx.append_event(
        "scripted_request_delivered",
        actor=seat,
        active_ms=_active(ctx),
        payload={"request_id": str(request_id), "crate": crate, "index": index},
    )
    ctx.conn.execute(
        insert(s.request).values(
            session_id=ctx.session_id,
            request_id=request_id,
            requester=seat,
            responder="participant",
            crate=crate,
            target="any_useful",
            kind="structured",
            job_id=job["job_id"],
            accepted_seq=seq.seq,
            accepted_active_ms=_active(ctx),
            accepted_at=ctx.now,
        )
    )
    ctx.conn.execute(
        insert(s.request_episode).values(
            session_id=ctx.session_id,
            episode_id=uuid.uuid4(),
            request_id=request_id,
            status="pending",
            opened_at=ctx.now,
        )
    )
    # INV-0704: the requesting seat's outstanding ask is part of its auditable memory.
    engines.note_request(
        ctx.conn,
        ctx.session_id,
        request_id=request_id,
        requester=seat,
        responder="participant",
        crate=crate,
        at_active_ms=_active(ctx),
    )
    # fk_message_request is enforced at insert, so the request row must precede the
    # message that announces it.
    mid, _ = engines.deliver_message(
        ctx,
        actor=seat,
        channel="team",
        body=body,
        crate=crate,
        request_id=request_id,
        scheduled_for_active_ms=int(job["due_active_ms"]),
        active_ms=_active(ctx),
    )
    teammates = _policy(ctx, bound).teammates
    if teammates.get("crosstalk") is not None:
        other = "PM" if seat == "JT" else "JT"
        _schedule_active(
            ctx,
            f"crosstalk.{index}",
            kind="deliver",
            due_active_ms=int(job["due_active_ms"]) + CROSSTALK_AFTER_REQUEST_MS,
            payload={
                "response_class": "crosstalk",
                "seat": other,
                "crate": crate,
                "request_id": str(request_id),
                "parent_message_id": str(mid),
                "request_seq": seq.seq,
                "grant_ms": _active(ctx),
            },
            active_now=_active(ctx),
        )
    return "committed"


def _acknowledgment(ctx: TxContext, bound: BoundManifest, job: Mapping[str, Any]) -> str | Deferred:
    guard = _task_surface_guard(ctx, bound, job)
    if guard is not None:
        return guard
    p = job["payload"]
    log = DrawLog()
    draw = engines.voice_line(
        ctx.conn, _streams(ctx, log), bound, p["seat"], "thanks", ctx.session_id
    )
    record_draws(ctx.conn, ctx.session_id, log.records)
    body, repeat_reason = _team_line_or_substitute(
        ctx, bound, p["seat"], draw.text if draw else "thanks", ""
    )
    engines.deliver_message(
        ctx,
        actor=p["seat"],
        channel="team",
        body=body,
        crate=p.get("crate"),
        request_id=uuid.UUID(p["request_id"]) if p.get("request_id") else None,
        parent_message_id=(
            uuid.UUID(p["parent_message_id"]) if p.get("parent_message_id") else None
        ),
        voice_kind="thanks",
        voice_draw=draw,
        active_ms=_active(ctx),
        repeat_substituted=repeat_reason is not None,
        repeat_substituted_reason=repeat_reason,
    )
    return "committed"


def _crosstalk(ctx: TxContext, bound: BoundManifest, job: Mapping[str, Any]) -> str | Deferred:
    guard = _task_surface_guard(ctx, bound, job)
    if guard is not None:
        return guard
    p = job["payload"]
    answered = (
        ctx.conn.execute(
            select(func.count())
            .select_from(s.share)
            .where(
                s.share.c.session_id == ctx.session_id,
                s.share.c.sharer == "participant",
                s.share.c.delivered_seq > int(p["request_seq"]),
                s.record.c.crate == int(p["crate"]),
            )
            .select_from(
                s.share.join(
                    s.record,
                    (s.record.c.session_id == s.share.c.session_id)
                    & (s.record.c.record_id == s.share.c.record_id),
                )
            )
        ).scalar_one()
        > 0
    )
    kind: Literal["after_share", "no_news"] = "after_share" if answered else "no_news"
    log = DrawLog()
    draw = engines.voice_line(ctx.conn, _streams(ctx, log), bound, p["seat"], kind, ctx.session_id)
    record_draws(ctx.conn, ctx.session_id, log.records)
    body = (draw.text if draw else "noted").replace("{n}", str(p["crate"])).replace("{you}", "you")
    body, repeat_reason = _team_line_or_substitute(ctx, bound, p["seat"], body, "")
    engines.deliver_message(
        ctx,
        actor=p["seat"],
        channel="team",
        body=body,
        crate=int(p["crate"]),
        parent_message_id=(
            uuid.UUID(p["parent_message_id"]) if p.get("parent_message_id") else None
        ),
        voice_kind=kind,
        voice_draw=draw,
        active_ms=_active(ctx),
        repeat_substituted=repeat_reason is not None,
        repeat_substituted_reason=repeat_reason,
    )
    return "committed"


def _deliver_structured(
    ctx: TxContext, bound: BoundManifest, job: Mapping[str, Any]
) -> str | Deferred:
    """Deliver a teammate's structured share: a short header message plus the record as
    its own card (rendered like the participant's shares), and the record joins the
    participant's file on delivery."""
    guard = _task_surface_guard(ctx, bound, job)
    if guard is not None:
        return guard
    p = job["payload"]
    request_id = uuid.UUID(p["request_id"])
    manifest = ctx.conn.execute(
        select(s.knowledge_snapshot.c.record_manifest).where(
            s.knowledge_snapshot.c.session_id == ctx.session_id,
            s.knowledge_snapshot.c.snapshot_id == uuid.UUID(p["snapshot_id"]),
        )
    ).scalar_one_or_none()
    if manifest is None:
        return "failed"
    selected = retrieval.lead_from_manifest(ctx.conn, ctx.session_id, list(manifest))
    if selected is None:
        return "failed"
    seat, lead_record_id = selected
    # UX round 4, item 9: teammate shares render like the participant's — a
    # short header line with the record on its own card (``record_id`` → the
    # chat pill), not the stamp+body inlined into the message text. The typing
    # schedule's ``body_chars`` counts this header form.
    mid, seq = engines.deliver_message(
        ctx,
        actor=seat,
        channel="team",
        body=f"Sharing intel on Crate {p['crate']}",
        crate=int(p["crate"]),
        record_id=lead_record_id,
        request_id=request_id,
        scheduled_for_active_ms=p["plan"]["scheduled_ms"],
        active_ms=_active(ctx),
    )
    ctx.conn.execute(
        insert(s.share).values(
            session_id=ctx.session_id,
            share_id=uuid.uuid4(),
            sharer=seat,
            record_id=lead_record_id,
            recipients=["participant"],
            prompted_by_request_id=request_id,
            source="agent_proactive",
            delivered_seq=seq.seq,
            delivered_at=ctx.now,
        )
    )
    # O-4 (2026-10-03): delivered cards join the participant file automatically —
    # no manual Add-to-case-file step. Provenance stays ``added_from_team`` and
    # the sharer seat is derivable through this share row.
    worlds.upsert_added_to_file(ctx.conn, ctx.session_id, lead_record_id, seq.seq)
    # UX round 5 (items 1-2): the participant projection allowlists
    # ``record_added_to_file`` but nothing ever emitted it — so a live client saw
    # the chat card before its workbench refetch and fell back to "Attached
    # record <id>" until the next stage refresh. Emitting the membership change
    # with the full record body lets the client apply it synchronously in the
    # same event pass as the message — no placeholder flash, no case-file lag.
    shared_record = (
        ctx.conn.execute(
            select(
                s.record.c.crate,
                s.record.c.stamp,
                s.record.c.body,
                s.record.c.event_type,
            ).where(
                s.record.c.session_id == ctx.session_id,
                s.record.c.record_id == lead_record_id,
            )
        )
        .mappings()
        .one()
    )
    ctx.append_event(
        "record_added_to_file",
        actor=seat,
        payload={
            "record_id": lead_record_id,
            "crate": shared_record["crate"],
            "stamp": shared_record["stamp"],
            "body": shared_record["body"],
            "event_type": shared_record["event_type"],
            "source": "added_from_team",
            "shared_by": seat,
        },
        parent_event_id=seq.event_id,
    )
    ctx.conn.execute(
        update(s.request_episode)
        .where(
            s.request_episode.c.session_id == ctx.session_id,
            s.request_episode.c.request_id == request_id,
            s.request_episode.c.closed_at.is_(None),
        )
        .values(
            status="resolved",
            substantive_event_seq=seq.seq,
            closed_event_seq=seq.seq,
            closed_at=ctx.now,
            close_reason="delivered",
        )
    )
    engines.resolve_request(
        ctx.conn,
        ctx.session_id,
        request_id=request_id,
        resolution="delivered",
        seq=seq.seq,
        at_active_ms=_active(ctx),
    )
    ctx.append_event(
        "structured_response_delivered",
        actor=seat,
        payload={
            "request_id": str(request_id),
            "record_id": lead_record_id,
            "message_id": str(mid),
        },
        parent_event_id=seq.event_id,
    )
    return "committed"


def _team_line_or_substitute(
    ctx: TxContext,
    bound: BoundManifest,
    seat: Any,
    body: str,
    participant_text: str,
) -> tuple[str, str | None]:
    """AI-1 (teammate_output_v4/v7): delivery-time repeat recheck under the session lock.

    The generation-time ``repeat_message``/``near_repeat`` gates only see lines
    committed *at validation time* — two replies can validate near-identical text
    while both are still in flight. This recheck runs after every prior delivery,
    so the second committer substitutes a still-unused deterministic-script line
    instead of echoing the first. Returns ``(line, reason)`` where reason is
    ``"exact"``/``"near"``/``None`` for the audit record.

    Scope: teammate *text* deliveries only. Structured record shares and scripted-request
    announcements stay verbatim — substituting them would corrupt the request/record
    semantics they carry.
    """
    prior = engines.delivered_teammate_lines(ctx.conn, ctx.session_id)
    reason = (
        "exact"
        if engines.is_repeat(body, prior)
        else ("near" if engines.is_near_repeat(body, prior) else None)
    )
    if reason is None:
        return body, None
    log = DrawLog()
    substitute = engines.social_fallback(
        ctx.conn,
        _streams(ctx, log),
        bound,
        seat,
        participant_text,
        ctx.session_id,
        nl_only=bound.manifest.interaction_mode == "nl_only",
    )
    record_draws(ctx.conn, ctx.session_id, log.records)
    return substitute, reason


def _deliver_text(ctx: TxContext, bound: BoundManifest, job: Mapping[str, Any]) -> str | Deferred:
    """Deliver generated/fallback text for social_reply / assistant / social_greeting."""
    guard = _task_surface_guard(ctx, bound, job)
    if guard is not None:
        return guard
    p = job["payload"]
    channel = "assistant" if p["response_class"] == "assistant" else "team"
    body = p.get("body")
    voice_draw: engines.VoiceDraw | None = None
    if body is None and p.get("voice_kind") is not None:
        # INV-0702: the voice-pool draw is realized here at commit; persist it to draw_log.
        # UX round 4 (item 8): greeting replies must read like the greeting
        # buttons the participant was offered — verbatim pool text (no seat
        # lower-casing) and never the same greeting the other seat just sent.
        is_greeting = p["voice_kind"] == "greetings"
        log = DrawLog()
        voice_draw = engines.voice_line(
            ctx.conn,
            _streams(ctx, log),
            bound,
            p["seat"],
            p["voice_kind"],
            ctx.session_id,
            verbatim=is_greeting,
            avoid=engines.delivered_teammate_lines(ctx.conn, ctx.session_id) if is_greeting else (),
        )
        body = None if voice_draw is None else voice_draw.text
        record_draws(ctx.conn, ctx.session_id, log.records)
    if body is None:
        return "failed"
    body = str(body)
    repeat_reason: str | None = None
    fallback = p.get("fallback")
    # O-1/O-3: ``verbatim`` payloads (grounded statements, the fixed disclosure
    # line) carry semantics substitution would corrupt — same exempt class as
    # record shares and scripted announcements.
    if channel == "team" and not p.get("verbatim"):
        body, repeat_reason = _team_line_or_substitute(
            ctx, bound, p["seat"], body, str(p.get("participant_text") or "")
        )
        if repeat_reason is not None:
            # The shown text now comes from the deterministic-script engine; the
            # original call stays linked via call_id and the event flag.
            fallback = "deterministic_script"
    mid, seq = engines.deliver_message(
        ctx,
        actor=p["seat"] if channel == "team" else "assistant",
        channel=channel,
        body=body,
        crate=p.get("crate"),
        request_id=uuid.UUID(p["request_id"]) if p.get("request_id") else None,
        parent_message_id=(
            uuid.UUID(p["parent_message_id"]) if p.get("parent_message_id") else None
        ),
        scheduled_for_active_ms=p["plan"]["scheduled_ms"],
        voice_kind=p.get("voice_kind"),
        voice_draw=voice_draw,
        active_ms=_active(ctx),
        repeat_substituted=repeat_reason is not None,
        repeat_substituted_reason=repeat_reason,
    )
    if p.get("call_id") or fallback:
        ctx.conn.execute(
            insert(s.displayed_output).values(
                session_id=ctx.session_id,
                output_id=uuid.uuid4(),
                call_id=uuid.UUID(p["call_id"]) if p.get("call_id") else None,
                fallback=fallback or "none",
                shown_text=body,
                shown_sha256=hashlib.sha256(body.encode()).hexdigest(),
                message_id=mid,
                delivered_seq=seq.seq,
                validator_version=p.get("validator_version"),
            )
        )
    episode = p.get("episode_request_id")
    if episode:
        ctx.conn.execute(
            update(s.request_episode)
            .where(
                s.request_episode.c.session_id == ctx.session_id,
                s.request_episode.c.request_id == uuid.UUID(episode),
                s.request_episode.c.closed_at.is_(None),
            )
            .values(
                status="resolved",
                substantive_event_seq=seq.seq,
                closed_event_seq=seq.seq,
                closed_at=ctx.now,
                close_reason="delivered",
            )
        )
        engines.resolve_request(
            ctx.conn,
            ctx.session_id,
            request_id=uuid.UUID(episode),
            resolution="delivered",
            seq=seq.seq,
            at_active_ms=_active(ctx),
        )
    return "committed"


# provider-side error kinds that keep their own model_call status; anything else
# (unavailable endpoint, prompt-build failure, unknown) records ``failed`` with the
# kind preserved in ``validation.error_kind`` (INV-1005).
_GENERATION_ERROR_STATUS = {
    "timeout": "timeout",
    "invalid_response": "invalid_response",
}


def _generation_surface_closed(ctx: TxContext, bound: BoundManifest) -> bool:
    """Whether a model result committing now could still lawfully reach the participant —
    the same stage/deadline rules ``_task_surface_guard`` enforces on the deliver job.
    """
    if ctx.stage in s.TERMINAL_STAGES or ctx.stage == "completion":
        return True
    if ctx.stage not in OPEN_TASK_STAGES:
        return True
    return _active(ctx) >= _policy(ctx, bound).budget_ms


def _snapshot_record_ids(ctx: TxContext, snapshot_id_raw: Any) -> set[str] | None:
    """Verified assistant evidence IDs; None means integrity loss, never live file access."""
    try:
        snapshot_id = uuid.UUID(str(snapshot_id_raw))
    except (TypeError, ValueError):
        return None
    snap = (
        ctx.conn.execute(
            select(s.knowledge_snapshot).where(
                s.knowledge_snapshot.c.session_id == ctx.session_id,
                s.knowledge_snapshot.c.snapshot_id == snapshot_id,
            )
        )
        .mappings()
        .one_or_none()
    )
    if snap is None or snap["purpose"] != "assistant_context" or snap["seat"] != "assistant":
        return None
    try:
        records = prompts.load_manifest_records(ctx.conn, ctx.session_id, snap["record_manifest"])
    except prompts.PromptBuildError:
        return None
    return {str(r["record_id"]) for r in records}


def _generate(
    ctx: TxContext,
    bound: BoundManifest,
    job: Mapping[str, Any],
    generation: Mapping[str, Any] | None,
) -> str:
    """Record the provider outcome and derive the concrete delivery job.

    ``generation`` is the worker-side provider result collected outside the session lock;
    ``None`` means deterministic-only policy (no model call was ever intended).

    Outcome taxonomy (INV-1005): ``succeeded`` | ``empty`` | ``invalid_response`` |
    ``timeout`` | ``failed`` | ``validation_rejected`` | ``quarantined`` — each lands on
    ``model_call.status`` with parse/rejection detail in ``validation``, while the only
    degraded display path stays the pinned deterministic fallback (spec §7.4).

    Retry/late-output rule (INV-1006): a retried job reuses the original snapshot,
    grant_ms and timing components and stacks ``model_call.attempt``; a provider result
    committing after the task surface closed is ``quarantined`` — recorded, and the
    generate job finishes ``expired`` with no ``deliver`` job (no second bubble).
    """
    p = job["payload"]
    policy = _policy(ctx, bound)
    cls = policy.class_policy(p["response_class"], p["seat"])
    grant_ms = int(p["grant_ms"])
    deterministic_only = bool(p.get("deterministic_only"))
    text: str | None = None
    raw_text: str | None = None
    call_id: uuid.UUID | None = None
    failed = False
    status = "skipped"
    validation: dict[str, Any] | None = None
    parsed_claims: list[output_claims.OutputClaim] = []
    response_type: str | None = None
    surface_closed = _generation_surface_closed(ctx, bound)
    gen_result = generation or {}
    if not deterministic_only:
        call_id = uuid.uuid4()
        # The pinned snapshot is the FK anchor for model_call rows; a corrupt or
        # pruned snapshot_id must not crash commit — audit degrades to the event log.
        try:
            pinned_snapshot = uuid.UUID(str(p["snapshot_id"]))
        except (TypeError, ValueError):
            pinned_snapshot = None
        snapshot_exists = (
            pinned_snapshot is not None
            and ctx.conn.execute(
                select(func.count())
                .select_from(s.knowledge_snapshot)
                .where(
                    s.knowledge_snapshot.c.session_id == ctx.session_id,
                    s.knowledge_snapshot.c.snapshot_id == pinned_snapshot,
                )
            ).scalar_one()
            > 0
        )
        if generation is None:
            status, failed = "failed", True
            validation = {"error_kind": "no_result"}
        elif gen_result.get("error") is not None:
            err = str(gen_result["error"])
            status = _GENERATION_ERROR_STATUS.get(err, "failed")
            failed = True
            validation = {"error_kind": err}
            if gen_result.get("detail"):
                validation["detail"] = str(gen_result["detail"])
        else:
            raw_text = str(gen_result.get("text") or "")
            if raw_text.strip():
                text = raw_text
                status = "succeeded"
            else:
                status, failed = "empty", True
                validation = {
                    "validator_version": engines.VALIDATOR_VERSION,
                    "violations": ["empty_output"],
                }
        # INV-0703 + INV-1003 (spec §7.7): generated text must pass the deterministic
        # output validator, and for the assistant class the typed-claim checker bound to
        # the pinned snapshot's hash-verified records — never a silently edited string.
        if text is not None:
            allowed: frozenset[str] | None = None
            manifest_records: list[Mapping[str, Any]] | None = None
            if p["response_class"] == "assistant":
                manifest_rows: list[dict[str, Any]] | None = None
                if snapshot_exists:
                    manifest_rows = ctx.conn.execute(
                        select(s.knowledge_snapshot.c.record_manifest).where(
                            s.knowledge_snapshot.c.session_id == ctx.session_id,
                            s.knowledge_snapshot.c.snapshot_id == pinned_snapshot,
                        )
                    ).scalar_one()
                if manifest_rows is None:
                    manifest_records = None
                else:
                    try:
                        manifest_records = prompts.load_manifest_records(
                            ctx.conn, ctx.session_id, manifest_rows
                        )
                    except prompts.PromptBuildError:
                        manifest_records = None
                allowed = frozenset(
                    str(value)
                    for r in manifest_records or ()
                    for value in (r["location_id"], r.get("toward"))
                    if value
                )
                if _snapshot_record_ids(ctx, p.get("snapshot_id")) is None:
                    manifest_records = None
            violations = engines.validate_teammate_output(text, allowed_locations=allowed)
            if p["response_class"] != "assistant":
                prior_lines = engines.delivered_teammate_lines(ctx.conn, ctx.session_id)
                # AI-1 (teammate_output_v4/v7): checked inside the session lock against
                # the committed message log — authoritative, unlike prompt guidance.
                if engines.is_repeat(text, prior_lines):
                    violations.append("repeat_message")
                elif engines.is_near_repeat(text, prior_lines):
                    violations.append("near_repeat")
            if p["response_class"] == "assistant":
                if manifest_records is None:
                    violations.append("unverified_source")
                else:
                    crate_raw = p.get("crate")
                    if not isinstance(crate_raw, int):
                        # nl_only asks carry no structured crate field; the question's own
                        # wording names it ("Where is crate 8 now?") — same binding a
                        # structured ask would have given the checker (RCA-2026-10-09).
                        asked = engines.assistant_scope(
                            str(p.get("participant_text") or ""),
                            n_crates=bound.manifest.task.n_crates,
                        )
                        crate_raw = asked if asked is not None else None
                    check = output_claims.check_assistant_output(
                        text,
                        crate=crate_raw if isinstance(crate_raw, int) else None,
                        records=manifest_records,
                    )
                    parsed_claims = list(check.claims)
                    response_type = check.response_type
                    violations.extend(check.violations)
            violations = list(dict.fromkeys(violations))
            validation = {
                "validator_version": engines.VALIDATOR_VERSION,
                "violations": violations,
                "response_type": response_type,
                "claims": [
                    {
                        "crate": c.crate,
                        "location_id": c.location_id,
                        "event_type": c.event_type,
                        "assertion": c.assertion,
                        "verdict": c.verdict,
                        "historical": c.historical,
                        "evidence_time": c.evidence_time,
                        "claimed_citations": list(c.claimed_citations),
                        "supporting_record_ids": list(c.supporting_record_ids),
                        "role": c.role,
                        "source_record_ids": list(c.source_record_ids),
                    }
                    for c in parsed_claims
                ],
            }
            if p["response_class"] == "assistant":
                validation["claim_checker"] = output_claims.CHECKER_VERSION
            if violations:
                status, text, failed = "validation_rejected", None, True
        if surface_closed and status == "succeeded":
            # INV-1006: a clean result that arrives after the surface closed is
            # quarantined — recorded verbatim, never scheduled for delivery.
            status, text, failed = "quarantined", None, True
        if gen_result.get("finish_reason") and validation is not None:
            validation["finish_reason"] = str(gen_result["finish_reason"])
        # Retry lineage: an expired lease re-runs this job against the *same* snapshot;
        # attempts stack (attempt N's parent is attempt N-1) so the unique
        # (session, snapshot, attempt) contract holds and every outcome stays auditable.
        prior = None
        if snapshot_exists:
            prior = (
                ctx.conn.execute(
                    select(s.model_call.c.call_id, s.model_call.c.attempt)
                    .where(
                        s.model_call.c.session_id == ctx.session_id,
                        s.model_call.c.snapshot_id == pinned_snapshot,
                    )
                    .order_by(s.model_call.c.attempt.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        model_id = str(gen_result.get("model_id") or "unavailable")
        digest = str(gen_result.get("model_digest") or "")
        if not digest and "@sha256:" in model_id:
            digest = model_id.rsplit("@sha256:", 1)[1]
        if not snapshot_exists:
            call_id = None
            # the pinned snapshot cannot anchor the FK — the call is audited on the
            # job + generation_resolved/quarantine events instead of a model_call row
            logger.error(
                "generate job %s references missing snapshot; skipping model_call",
                job["job_id"],
            )
        else:
            ctx.conn.execute(
                insert(s.model_call).values(
                    session_id=ctx.session_id,
                    call_id=call_id,
                    snapshot_id=pinned_snapshot,
                    attempt=1 if prior is None else int(prior["attempt"]) + 1,
                    parent_call_id=None if prior is None else prior["call_id"],
                    provider=str(gen_result.get("provider_id") or "unavailable"),
                    model=model_id,
                    model_digest=digest or None,
                    # request params first (served digest etc.), then the audit
                    # covariates — seed + concurrent load (O-18/O-20)
                    parameters={
                        **dict(gen_result.get("params") or p.get("provider_params") or {}),
                        **dict(gen_result.get("audit_params") or {}),
                    },
                    # the hash of the constructed grounded prompt when one was built;
                    # the pinned artifact hash otherwise (INV-1002)
                    prompt_sha256=str(gen_result.get("prompt_sha256") or p["prompt_sha256"]),
                    input={
                        "prompt_id": p.get("prompt_id"),
                        "prompt_sha256": p["prompt_sha256"],
                        "prompt": p.get("prompt", ""),
                        "participant_text": p.get("participant_text"),
                        "grounded": {
                            "builder_version": gen_result.get("builder_version"),
                            "messages_sha256": gen_result.get("prompt_sha256"),
                            "record_ids": gen_result.get("record_ids"),
                            "rendered_record_ids": gen_result.get("rendered_record_ids"),
                            "truncated_record_ids": gen_result.get("truncated_record_ids"),
                        },
                    },
                    raw_response=raw_text,
                    status=status,
                    validation=validation,
                    timeout_ms=int(cls.timeout_ms or 0),
                    duration_ms=int(gen_result.get("latency_ms") or 0),
                    requested_active_ms=grant_ms,
                    finished_at=ctx.now,
                )
            )
            # INV-1003: assistant claims are registered on the ledger with their
            # verdicts — even rejected ones, so fabricated/unsupported output stays
            # auditable.
            for c in parsed_claims:
                if c.crate is None:
                    continue
                claims.register_claim(
                    ctx.conn,
                    ctx.session_id,
                    crate=c.crate,
                    location_id=c.location_id,
                    event_type=c.event_type,
                    assertion=c.assertion,
                    issuer="assistant",
                    source_record_ids=list(c.source_record_ids),
                    snapshot_id=pinned_snapshot,
                    model_call_id=call_id,
                    historical=c.historical,
                    evidence_time=c.evidence_time,
                    evaluated_verdict=c.verdict,
                    role=c.role,
                )
    if not deterministic_only and surface_closed:
        ctx.append_event(
            "generation_quarantined",
            actor="scheduler",
            active_ms=_active(ctx),
            payload={
                "logical_job_key": job["logical_job_key"],
                "call_id": None if call_id is None else str(call_id),
                "status": status,
                "reason": "task_surface_closed",
            },
        )
        # Expired, never delivered: the participant's clock and message list are
        # untouched, and the episode itself expires at the deadline as usual.
        return "expired"
    fallback: str | None = None
    if text is None:
        failed = True
        if p["response_class"] == "assistant":
            # Typed prompts carry crate=None — scope the summary by the crate
            # the question actually asks about (BA-64) so written questions
            # answer instead of deflecting. BA-93: file facts come only from the
            # accept-time snapshot's pinned record set — arrivals while the job
            # waited stay invisible to this answer.
            record_ids = _snapshot_record_ids(ctx, p.get("snapshot_id"))
            crate = p.get("crate")
            text_ask = str(p.get("participant_text") or "")
            if crate is None:
                crate = engines.assistant_scope(text_ask, n_crates=bound.manifest.task.n_crates)
            if record_ids is None:
                fallback = "deterministic_file_summary"
                text = "I couldn't verify the saved file for this request. Please try again."
            elif crate is not None:
                fallback = "deterministic_file_summary"
                text = engines.assistant_file_summary(
                    ctx.conn, ctx.session_id, crate, record_ids=record_ids
                )
            else:
                # SQ-8: no crate number — resolve the question's item vocabulary to
                # the fixed crate→contents map (no locations; the strict
                # answer-key bound on generated text is untouched). A numbered
                # ask outside the session's crate range gets the honest
                # boundary — without it the bare "crate" word falls through to
                # the broad-contents route and dumps the whole map (observed
                # 2026-10-05: "where is crate 9" → all eight crates).
                n_crates = bound.manifest.task.n_crates
                out_of_range = engines.crate_number_out_of_range(text_ask, n_crates)
                items = (
                    []
                    if out_of_range is not None
                    else engines.ask_items_mentioned(text_ask, n_crates)
                )
                if out_of_range is not None:
                    fallback = "deterministic_file_summary"
                    text = engines.assistant_unknown_crate(out_of_range, n_crates)
                elif items:
                    fallback = "deterministic_contents_summary"
                    text = engines.assistant_contents_summary(
                        ctx.conn, ctx.session_id, items, record_ids=record_ids
                    )
                elif engines.asks_crate_contents(text_ask):
                    fallback = "deterministic_contents_summary"
                    text = engines.assistant_contents_summary(
                        ctx.conn,
                        ctx.session_id,
                        list(range(1, n_crates + 1)),
                        record_ids=record_ids,
                    )
                else:
                    fallback = "deterministic_file_summary"
                    text = engines.assistant_file_summary(
                        ctx.conn, ctx.session_id, None, record_ids=record_ids
                    )
        else:
            fallback = "deterministic_script"
            # INV-0702: the fallback line draw is a realized jobs-stream draw — persist it.
            log = DrawLog()
            text = engines.social_fallback(
                ctx.conn,
                _streams(ctx, log),
                bound,
                p["seat"],
                str(p.get("participant_text") or ""),
                ctx.session_id,
                nl_only=bound.manifest.interaction_mode == "nl_only",
            )
            record_draws(ctx.conn, ctx.session_id, log.records)
    ready_ms = _active(ctx)  # generation finished "now" on the active axis
    gen = Generation(ready_ms=ready_ms, failed=failed, body_chars=len(text))
    components = p["components"]

    plan = plan_schedule(
        cls,
        grant_ms,
        SampledComponents(
            delay_ms=components["delay_ms"],
            platform_ms=components["platform_ms"],
            think_ms=components["think_ms"],
            floor_ms=components["floor_ms"],
        ),
        gen,
        body_chars=len(text),
    )
    plan_payload = {
        "mechanism": plan.mechanism,
        "grant_ms": plan.grant_ms,
        "typing_ms": plan.typing_ms,
        "scheduled_ms": plan.scheduled_ms,
        "effective_ready_ms": plan.effective_ready_ms,
        "eligible_ms": plan.eligible_ms,
        "readiness_overrun_ms": plan.readiness_overrun_ms,
        "fallback_reason": plan.fallback_reason
        or ("deterministic_only" if deterministic_only else None),
    }
    ctx.append_event(
        "generation_resolved",
        actor="scheduler",
        active_ms=ready_ms,
        payload={
            "logical_job_key": job["logical_job_key"],
            "call_id": None if call_id is None else str(call_id),
            "status": status,
            "fallback": fallback,
            "plan": plan_payload,
        },
    )
    _schedule_active(
        ctx,
        f"deliver.{p['request_key']}",
        kind="deliver",
        due_active_ms=plan.eligible_ms,
        payload={
            "response_class": p["response_class"],
            "seat": p["seat"],
            "body": text,
            "crate": p.get("crate"),
            "participant_text": p.get("participant_text"),
            "request_id": p.get("request_id"),
            "parent_message_id": p.get("parent_message_id"),
            "episode_request_id": p.get("request_id"),
            "call_id": None if call_id is None else str(call_id),
            "fallback": fallback,
            "grant_ms": grant_ms,
            "plan": plan_payload,
            "validator_version": engines.VALIDATOR_VERSION,
        },
        active_now=ready_ms,
    )
    return "committed"


def _connecting(ctx: TxContext, bound: BoundManifest, job: Mapping[str, Any]) -> str:
    """INV-0701: a scheduled teammate joins the scripted connecting screen. Committing the
    ``teammate_presented`` event is what unlocks Start (``ready`` gates on it)."""
    del bound
    ctx.append_event("teammate_presented", actor="system", payload={"seat": job["payload"]["seat"]})
    return "committed"


def _task_open(
    ctx: TxContext,
    bound: BoundManifest,
    job: Mapping[str, Any],
    *,
    time_pressure: bool = True,
) -> str:
    """INV-0701: end the post-start hold — transition ``ready`` into the variant's first task
    stage and start the active clock + commit the whole task schedule atomically, so no
    active millisecond can elapse before the participant's task surface is reachable."""
    del job
    if ctx.stage != "ready":
        # Idempotent under retries: the transition already committed, or the session left
        # the flow entirely (withdrawal/stop) — in both cases there is nothing to redo.
        return "committed"

    first = "solo_review" if variant_for_manifest(bound.manifest) == "tp_review_first" else "task"
    _transition(ctx, bound, first)
    ctx.conn.execute(
        insert(s.clock_interval).values(
            interval_id=uuid.uuid4(),
            session_id=ctx.session_id,
            kind="running",
            reason="task_start",
            active_ms_at_start=0,
        )
    )
    ctx.append_event("task_clock_started", actor="system", payload={"stage": first})
    # INV-0904: on a PE session the workbench strip and the task-open system message are
    # served as the task stage opens — commit their receipts and deliver the system
    # message in the same transaction. Control-arm sessions have empty copy for both
    # surfaces, so they record and deliver nothing.
    committed = engines.commit_pe_exposures(ctx, bound, ("pe_strip", "pe_system_message"))
    if "pe_system_message" in committed:
        texts = engines.pe_surface_texts(bound, ctx.session["arm"])
        engines.deliver_message(
            ctx,
            actor="system",
            channel="system",
            body=texts["system_message"],
            active_ms=0,
        )
    # INV-0603/0604: the whole active-time schedule (scripted requests, greeting prompt,
    # warnings, check-in, deadline) exists before the participant sees the task.
    schedule_task_jobs(ctx, bound, time_pressure=time_pressure)
    return "committed"


# ----- dispatch -----------------------------------------------------------------------------

_DELIVER_CLASSES = {
    "structured_record": _deliver_structured,
    "acknowledgment": _acknowledgment,
    "crosstalk": _crosstalk,
}


# Job kinds that exist only to drive the time-pressure manipulation. Under
# TIME_PRESSURE_ENABLED=0 they are never scheduled (``schedule_task_jobs``), and
# dispatch suppresses any straggler — e.g. a job pending across a flag flip —
# so the capability stays off in every mode.
PRESSURE_JOB_KINDS = frozenset(
    {"checkin", "checkin_reveal", "checkin_inactivity", "warning", "pressure_remark"}
)


def dispatch(
    ctx: TxContext,
    bound: BoundManifest,
    job: Mapping[str, Any],
    generation: Mapping[str, Any] | None = None,
    *,
    flags: FeatureFlags = DEFAULT_FLAGS,
    store: ContentStore | None = None,
) -> str | Deferred:
    """Run one due job inside the session transaction. Pressure job kinds are
    suppressed (and recorded) when the session's derived time-pressure is off; a
    session stranded mid-check-in by a flag flip is rescued back to the task stage.
    Other kinds route to their handler; ``Deferred`` asks the worker to retry later."""
    time_pressure = time_pressure_for_arm(ctx.session.get("arm"), enabled=flags.time_pressure)
    kind = job["kind"]
    if kind in PRESSURE_JOB_KINDS and not time_pressure:
        if kind == "checkin" and ctx.stage == "solo_review":
            outcome = _solo_review_end(ctx, bound, job)
            if isinstance(outcome, Deferred):
                return outcome
        ctx.append_event(
            "pressure_job_suppressed",
            actor="system",
            payload={"job": job["logical_job_key"], "reason": "time_pressure_disabled"},
        )
        if ctx.stage in CHECKIN_STAGES:
            # A session mid-check-in when the flag flips would be stranded:
            # participant commands are stage-gated to the task surface and the
            # inactivity kill is itself suppressed. Rescue it — return to the
            # task stage and restart the paused active clock.
            _transition(ctx, bound, "task")
            interval = activetime.open_interval(ctx.conn, ctx.session_id)
            if interval is not None and interval["kind"] == "paused":
                activetime.resume(
                    ctx.conn,
                    ctx.session_id,
                    reason="time_pressure_disabled",
                    now=ctx.now,
                )
            ctx.append_event(
                "checkin_rescinded",
                actor="system",
                payload={"reason": "time_pressure_disabled"},
            )
            ctx.append_event("stage_proceeded", actor="system", payload={"to": "task"})
        return "suppressed"
    if kind == "connecting":
        return _connecting(ctx, bound, job)
    if kind == "task_open":
        return _task_open(ctx, bound, job, time_pressure=time_pressure)
    if kind == "solo_review_end":
        return _solo_review_end(ctx, bound, job)
    if kind == "checkin":
        return _checkin(ctx, bound, job)
    if kind == "checkin_reveal":
        return _checkin_reveal(ctx, bound, job)
    if kind == "checkin_inactivity":
        return _checkin_inactivity(ctx, bound, job, store=store, bonus_payment=flags.bonus_payment)
    if kind == "deadline":
        return _deadline(ctx, bound, job)
    if kind == "warning":
        return _warning(ctx, bound, job)
    if kind == "greeting_prompt":
        return _greeting_prompt(ctx, bound, job)
    if kind == "scripted_request":
        return _scripted_request(ctx, bound, job)
    if kind == "pressure_remark":
        return _pressure_remark(ctx, bound, job)
    if kind == "generate":
        result = _generate(ctx, bound, job, generation)
        return result
    if kind == "qa_answer":
        return _qa_answer(ctx, bound, job, generation)
    if kind == "chat_judge":
        return _chat_judge(ctx, bound, job, generation)
    if kind == "deliver":
        cls = job["payload"]["response_class"]
        if cls in _DELIVER_CLASSES:
            return _DELIVER_CLASSES[cls](ctx, bound, job)
        return _deliver_text(ctx, bound, job)
    return "failed"


def make_job_handler(
    *,
    store: ContentStore,
    generation: Mapping[str, Any] | None = None,
    flags: FeatureFlags = DEFAULT_FLAGS,
) -> Callable[[TxContext, Mapping[str, Any]], str | Deferred]:
    """The ``finish_job`` handler: rebuild the pinned manifest and dispatch by job kind."""

    def handler(ctx: TxContext, row: Mapping[str, Any]) -> str | Deferred:
        bound = bound_for_session(ctx.conn, store, ctx.session)
        return dispatch(
            ctx,
            bound,
            row,
            generation,
            flags=flags,
            store=store,
        )

    return handler


# ----- advance_due: deadline/check-in transitions inside ordinary commands -------------------


def advance_due(
    ctx: TxContext,
    bound: BoundManifest,
    *,
    flags: FeatureFlags = DEFAULT_FLAGS,
    store: ContentStore | None = None,
) -> None:
    """Run any due transition jobs inside this command's session lock — spec §6.2 "at each
    command, advance any due deadline transitions". Whatever the scheduler is doing, the
    active-time transitions cannot be skipped by participant traffic. Due-ness is per clock
    basis (same rule as ``tx.due_clause``): wall jobs at ``due_wall_at``, active jobs only
    under a running interval."""
    from app.db.tx import db_now as _now  # noqa: F401 (ctx.now already is the db clock)

    j = s.scheduled_job
    ci = s.clock_interval
    now = ctx.now
    # Same rule as ``tx.due_clause``, resolved per session: the wall due instant divides
    # the remaining active ms by the bound clock_speed_factor (BA-14).
    factor = activetime.speed_factor_for(ctx.session)
    remaining_s = (j.c.due_active_ms - ci.c.active_ms_at_start) / (1000.0 * factor)
    active_due = ci.c.started_at + func.make_interval(0, 0, 0, 0, 0, 0, remaining_s)
    rows = (
        ctx.conn.execute(
            select(j)
            .select_from(
                j.join(
                    ci,
                    (ci.c.session_id == j.c.session_id) & ci.c.ended_at.is_(None),
                    isouter=True,
                )
            )
            .where(
                j.c.session_id == ctx.session_id,
                j.c.kind.in_(list(TRANSITION_KINDS)),
                j.c.status.in_(("pending", "claimed")),
                (
                    (j.c.status == "pending")
                    | (j.c.lease_expires_at.is_not(None) & (j.c.lease_expires_at < now))
                ),
                (
                    ((j.c.clock_basis == "wall") & (j.c.due_wall_at <= now))
                    | (
                        (j.c.clock_basis == "active_task")
                        & (ci.c.kind == "running")
                        & (active_due <= now)
                    )
                ),
            )
            .with_for_update(of=j)
        )
        .mappings()
        .all()
    )
    for row in rows:
        outcome = dispatch(
            ctx,
            bound,
            dict(row),
            flags=flags,
            store=store,
        )
        if isinstance(outcome, Deferred):
            continue  # still paused/not-due after all; the scheduler will pick it up

        ctx.conn.execute(
            update(j)
            .where(j.c.job_id == row["job_id"])
            .values(
                status=outcome,
                lease_owner=None,
                lease_expires_at=None,
                finished_at=now,
                finished_revision=ctx.revision + (1 if ctx.dirty else 0),
            )
        )


# ----- worker-side generation (outside the session lock) -------------------------------------


def derived_seed(*parts: object) -> int:
    """O-20: a deterministic 31-bit seed derived from stable session/job/request
    identity — never process state — recorded per call and passed to the model
    so a given session's generation is reproducible and auditable."""
    material = "|".join(str(x) for x in parts)
    return int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:4], "big") & 0x7FFFFFFF


def _model_load_covariates(conn: Connection, provider: Any) -> dict[str, int]:
    """O-18/BA-70: the load a call competed with — sessions in the task stage,
    generate jobs claimed by the scheduler (waiting *or* executing), jobs still
    pending, and the provider's own count of calls actually executing. Recorded
    on ``model_call.parameters`` so the fallback rate is interpretable as a
    covariate instead of a mystery."""
    sessions_in_task = conn.execute(
        select(func.count()).select_from(s.session).where(s.session.c.stage == "task")
    ).scalar_one()
    claimed = conn.execute(
        select(func.count())
        .select_from(s.scheduled_job)
        .where(
            s.scheduled_job.c.kind == "generate",
            s.scheduled_job.c.status == "claimed",
        )
    ).scalar_one()
    queued = conn.execute(
        select(func.count())
        .select_from(s.scheduled_job)
        .where(
            s.scheduled_job.c.kind == "generate",
            s.scheduled_job.c.status == "pending",
        )
    ).scalar_one()
    return {
        "concurrent_sessions": int(sessions_in_task),
        "claimed_generate": int(claimed),
        "queued_generate": int(queued),
        # BA-70: the adapter counter is process-local — a second worker's
        # in-flight calls are invisible to it, so the name says the scope.
        # `claimed_generate` is the cross-worker signal (DB-level).
        "provider_inflight_local": int(getattr(provider, "inflight_calls", 0) or 0),
    }


def run_generation(
    conn: Connection,
    job_row: Mapping[str, Any],
    *,
    provider: TextProvider | None,
    timeout_s: float,
) -> dict[str, Any] | None:
    """Execute the provider call for a ``generate`` job between claim and commit — never
    inside the session lock (spec §6.3: model computation is measured separately).

    The request is built by :func:`prompts.build_request` from the job's pinned snapshot
    (record hashes re-verified; INV-1002) and carries ``model_pin`` so a session job can
    never drift to a different model/digest than the adapter's pinned identity
    (INV-1001). The returned dict feeds ``model_call`` audit columns at commit."""
    p = job_row["payload"]
    if p.get("deterministic_only"):
        return None
    from app.services.generation_policy import matches
    from app.version import build_info

    expected = p.get("generation_policy")
    session = (
        conn.execute(
            select(
                s.session.c.mode,
                s.session.c.build_source_sha256,
                s.session.c.behavior_source_sha256,
            ).where(s.session.c.session_id == job_row["session_id"])
        )
        .mappings()
        .one()
    )
    if session["mode"] == "live" and not matches(
        expected,
        behavior=build_info().behavior_sha256,
        model=str(getattr(provider, "model_id", "none")),
        session_behavior=session["behavior_source_sha256"],
        session_build=session["build_source_sha256"],
    ):
        return {
            "error": "generation_policy_mismatch",
            "provider_id": "none",
            "model_id": "none",
            "audit_params": {"dispatched": False},
        }
    # O-20/O-18: a seed derived from stable session/job identity, plus the load
    # the call competed with — both land on model_call.parameters.
    seed = derived_seed(job_row["session_id"], job_row["job_id"])
    audit_params = {"seed": seed, **_model_load_covariates(conn, provider)}
    try:
        grounded = prompts.build_request(
            conn, job_row["session_id"], p, request_id=str(job_row["job_id"])
        )
    except prompts.PromptBuildError as exc:
        return {
            "error": "prompt_build",
            "detail": exc.reason,
            "provider_id": "none",
            "model_id": "none",
            "latency_ms": 0,
            "audit_params": {**audit_params, "dispatched": False},
        }
    meta: dict[str, Any] = {
        "prompt_sha256": grounded.messages_sha256,
        "builder_version": grounded.builder_version,
        "record_ids": list(grounded.record_ids),
        "rendered_record_ids": list(grounded.rendered_record_ids),
        "truncated_record_ids": list(grounded.truncated_record_ids),
    }
    params = dict(grounded.request.params)
    # Thinking-mode models (gemma4/qwen3 on ollama) spend most of the pinned class
    # timeout on hidden reasoning tokens — a ~24 s call is ~600 tokens of thought
    # before one sentence of reply. "none" keeps the visible answer inside the
    # 20-25 s budget; setdefault leaves a payload-level override lawful. Remote
    # APIs reject the parameter outright, so only providers that opt in get it.
    if getattr(provider, "supports_reasoning_effort", False):
        params.setdefault("reasoning_effort", "none")
    # Keep generation literal: at ~0.7 the 12B model narrates trajectories the claim
    # checker can't verify ("first seen at X, then moved to Y" from sighting records),
    # so generated answers get quarantined and the participant sees canned lines.
    params.setdefault("temperature", 0.2)
    params.setdefault("max_tokens", 400)
    # O-20: forwarded to providers that honor OpenAI's seed (Ollama does);
    # recorded on the call either way for provenance.
    params.setdefault("seed", seed)
    pin = getattr(provider, "model_id", None) if provider is not None else None
    if pin:
        params[MODEL_PIN_PARAM] = str(expected["model"] if session["mode"] == "live" else pin)
    if (
        params.get("temperature") != 0.2
        or params.get("max_tokens") != 400
        or params.get("seed") != seed
        or (
            getattr(provider, "supports_reasoning_effort", False)
            and params.get("reasoning_effort") != "none"
        )
    ):
        return {
            "error": "generation_policy_mismatch",
            "provider_id": "none",
            "model_id": "none",
            "audit_params": {**audit_params, "dispatched": False},
            **meta,
        }
    request = ProviderRequest(
        purpose=grounded.request.purpose,
        prompt_id=grounded.request.prompt_id,
        messages=grounded.request.messages,
        params=params,
        request_id=grounded.request.request_id,
    )
    if provider is None:
        return {
            "error": "unavailable",
            "provider_id": "none",
            "model_id": "none",
            "latency_ms": 0,
            # BA-71: the request was fully constructed — record the effective
            # params even though nothing was dispatched.
            "params": dict(params),
            "audit_params": {**audit_params, "dispatched": False},
            **meta,
        }
    # BA-35: the session/provider boundary is enforced at the call site too — a
    # grounded prompt built from session records must never leave the host through
    # a remote adapter, regardless of how the provider was injected.
    if getattr(provider, "is_remote", False):
        return {
            "error": "provider_not_allowed",
            "provider_id": provider.provider_id,
            "model_id": str(pin or "unavailable"),
            "latency_ms": 0,
            "params": dict(params),
            "audit_params": {**audit_params, "dispatched": False},
            **meta,
        }
    try:
        # BA-71: measure the call attempt so a dispatched failure still records
        # real elapsed time — a timeout after seconds on the wire must not log
        # as a zero-duration failure.
        call_started = time.monotonic()
        response: ProviderResponse = provider.generate(request, timeout_s=timeout_s)
    except ProviderError as exc:
        return {
            "error": exc.kind.value,
            "provider_id": provider.provider_id,
            "model_id": str(pin or "unavailable"),
            "latency_ms": round((time.monotonic() - call_started) * 1000),
            "params": dict(params),
            "audit_params": {**audit_params, "dispatched": exc.dispatched},
            **meta,
        }
    try:
        response.text.encode("utf-8", errors="strict")
        if "\x00" in response.text or response.finish_reason not in (None, "stop"):
            raise ValueError("invalid completion")
    except (UnicodeError, ValueError, AttributeError):
        return {
            "error": "invalid_response",
            "provider_id": provider.provider_id,
            "model_id": str(pin or "unavailable"),
            "latency_ms": response.latency_ms,
            "audit_params": {**audit_params, "dispatched": True},
            **meta,
        }
    return {
        "text": response.text,
        "provider_id": response.provider_id,
        "model_id": response.model_id,
        "model_digest": getattr(provider, "model_digest", ""),
        "latency_ms": response.latency_ms,
        "finish_reason": response.finish_reason,
        "params": dict(response.params),
        "audit_params": audit_params,
        **meta,
    }


def run_qa_generation(
    conn: Connection,
    job_row: Mapping[str, Any],
    *,
    store: ContentStore,
    provider: TextProvider | None,
    timeout_s: float,
    flags: FeatureFlags = DEFAULT_FLAGS,
) -> dict[str, Any] | None:
    """Execute the provider call for a ``qa_answer`` job between claim and
    commit — the same out-of-lock discipline as ``run_generation``, but the
    request is the bound-manifest rules prompt, not a pinned record snapshot
    (the Q&A answers about *rules*, which carry no session state)."""
    from app.services import qa

    p = job_row["payload"]
    exchange = (
        conn.execute(
            select(s.qa_exchange.c.question).where(
                s.qa_exchange.c.session_id == job_row["session_id"],
                s.qa_exchange.c.exchange_id == uuid.UUID(str(p["exchange_id"])),
            )
        )
        .mappings()
        .one_or_none()
    )
    if exchange is None:
        return {"error": "exchange_missing", "provider_id": "none", "model_id": "none"}
    session = (
        conn.execute(select(s.session).where(s.session.c.session_id == job_row["session_id"]))
        .mappings()
        .one()
    )
    time_pressure = time_pressure_for_arm(session.get("arm"), enabled=flags.time_pressure)
    try:
        bound = bound_for_session(conn, store, dict(session))
    except Exception as exc:  # noqa: BLE001 - any binding fault is a recorded outcome
        return {
            "error": "prompt_build",
            "detail": f"{type(exc).__name__}",
            "provider_id": "none",
            "model_id": "none",
        }
    seed = derived_seed(job_row["session_id"], job_row["job_id"])
    params: dict[str, Any] = {
        "temperature": 0.2,
        "max_tokens": qa.MAX_TOKENS,
        "seed": seed,
    }
    if getattr(provider, "supports_reasoning_effort", False):
        params["reasoning_effort"] = "none"
    pin = getattr(provider, "model_id", None) if provider is not None else None
    expected = p.get("generation_policy")
    if expected is None:
        # Jobs planted before provenance pinning still use the consent record.
        expected = conn.execute(
            select(s.event.c.payload).where(
                s.event.c.session_id == job_row["session_id"],
                s.event.c.type == "generation_policy_pinned",
            )
        ).scalar_one_or_none()
    if pin:
        params[MODEL_PIN_PARAM] = str(
            expected.get("model", pin)
            if session["mode"] == "live" and isinstance(expected, Mapping)
            else pin
        )
    request = ProviderRequest(
        purpose="ai_qa",
        prompt_id=qa.PROMPT_ID,
        messages=(
            Message(
                role="system",
                content=qa.rules_system_prompt(
                    bound, ai_assistant=flags.ai_assistant, time_pressure=time_pressure
                ),
            ),
            Message(role="user", content=str(exchange["question"])),
        ),
        params=params,
        request_id=str(job_row["job_id"]),
    )
    messages = [{"role": message.role, "content": message.content} for message in request.messages]
    audit: dict[str, Any] = {
        "prompt_id": qa.PROMPT_ID,
        "prompt_sha256": hashlib.sha256(
            json.dumps(messages, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
        "messages": messages,
        "parameters": dict(params),
        "timeout_ms": round(timeout_s * 1000),
        "flags": {"ai_assistant": flags.ai_assistant, "time_pressure": time_pressure},
        "generation_policy": expected,
        "dispatched": False,
    }
    from app.services.generation_policy import matches
    from app.version import build_info

    if session["mode"] == "live" and not matches(
        expected,
        behavior=build_info().behavior_sha256,
        model=str(pin or "none"),
        session_behavior=session["behavior_source_sha256"],
        session_build=session["build_source_sha256"],
    ):
        return {
            "error": "generation_policy_mismatch",
            "provider_id": "none",
            "model_id": "none",
            "audit": audit,
        }
    if provider is None:
        return {
            "error": "unavailable",
            "provider_id": "none",
            "model_id": "none",
            "latency_ms": 0,
            "params": dict(params),
            "audit": audit,
        }
    if getattr(provider, "is_remote", False):
        # Same session/provider boundary as run_generation — participant text
        # must never leave the host through a remote adapter.
        return {
            "error": "provider_not_allowed",
            "provider_id": provider.provider_id,
            "model_id": str(pin or "unavailable"),
            "latency_ms": 0,
            "params": dict(params),
            "audit": audit,
        }
    try:
        call_started = time.monotonic()
        response: ProviderResponse = provider.generate(request, timeout_s=timeout_s)
    except ProviderError as exc:
        return {
            "error": exc.kind.value,
            "provider_id": provider.provider_id,
            "model_id": str(pin or "unavailable"),
            "latency_ms": round((time.monotonic() - call_started) * 1000),
            "params": dict(params),
            "audit": {**audit, "dispatched": exc.dispatched},
        }
    try:
        response.text.encode("utf-8", errors="strict")
        if "\x00" in response.text or response.finish_reason not in (None, "stop"):
            raise ValueError("invalid completion")
    except (UnicodeError, ValueError, AttributeError):
        return {
            "error": "invalid_response",
            "provider_id": provider.provider_id,
            "model_id": str(pin or "unavailable"),
            "latency_ms": response.latency_ms,
            "params": dict(params),
            "audit": {
                **audit,
                "parameters": {**params, **dict(response.params)},
                "dispatched": True,
            },
        }
    return {
        "text": response.text,
        "provider_id": response.provider_id,
        "model_id": response.model_id,
        "model_digest": getattr(provider, "model_digest", ""),
        "latency_ms": response.latency_ms,
        "finish_reason": response.finish_reason,
        "params": dict(response.params),
        "audit": {
            **audit,
            "parameters": {**params, **dict(response.params)},
            "dispatched": True,
        },
    }


def _needs_provider_generation(job: Any) -> bool:
    if job.kind in ("qa_answer", "chat_judge"):
        # Rules-Q&A and AI-judge verdicts always want the provider — the
        # recorded outcome when the call cannot run is written by dispatch.
        return True
    return job.kind == "generate" and not job.row["payload"].get("deterministic_only")


def _qa_answer(
    ctx: TxContext,
    _bound: BoundManifest,
    job: Mapping[str, Any],
    generation: Mapping[str, Any] | None,
) -> str:
    """AI_QA_ENABLED (2026-10-16): commit the model's rules answer — or the
    deterministic fallback — onto the ``qa_exchange`` row. Always resolves the
    exchange: a pending question never outlives its job."""
    from app.services import qa

    p = job["payload"]
    row = (
        ctx.conn.execute(
            select(s.qa_exchange)
            .where(
                s.qa_exchange.c.session_id == ctx.session_id,
                s.qa_exchange.c.exchange_id == uuid.UUID(str(p["exchange_id"])),
            )
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return "expired"  # tombstoned/migrated-away session — nothing to answer
    if row["status"] != "pending":
        return "committed"  # a retried job must not rewrite a resolved exchange
    gen = generation or {}
    provider = gen.get("provider_id") or None
    model = gen.get("model_id") or None
    digest = gen.get("model_digest") or None
    latency = gen.get("latency_ms")
    answer: str
    status: str
    outcome: str | None = None
    if gen.get("error") is not None:
        answer, status, outcome = qa.FALLBACK_ANSWER, "fallback", str(gen["error"])
    else:
        cleaned = qa.clean_answer(gen.get("text"))
        if cleaned is None:
            answer, status, outcome = qa.FALLBACK_ANSWER, "fallback", "invalid_response"
        else:
            answer, status = cleaned, "answered"
    ref = ctx.append_event(
        "qa_answered",
        actor="assistant",
        payload={"seq": int(row["seq"]), "status": status},
    )
    ctx.conn.execute(
        update(s.qa_exchange)
        .where(
            s.qa_exchange.c.session_id == ctx.session_id,
            s.qa_exchange.c.exchange_id == row["exchange_id"],
        )
        .values(
            answer=answer,
            status=status,
            provider=provider,
            model=model,
            model_digest=digest,
            latency_ms=latency,
            outcome=outcome,
            generation_audit=dict(gen.get("audit") or {}),
            answered_seq=ref.seq,
            answered_at=ctx.now,
        )
    )
    return "committed"


def _finish(
    engine: Engine,
    store: ContentStore,
    job: Any,
    worker_id: str,
    generation: Any,
    *,
    flags: FeatureFlags = DEFAULT_FLAGS,
) -> bool:
    """Commit one claimed job; True when the handler committed."""
    from app.db.tx import finish_job

    try:
        status, _seqs = finish_job(
            engine,
            job,
            worker_id=worker_id,
            handler=make_job_handler(
                store=store,
                generation=generation,
                flags=flags,
            ),
        )
        return status == "committed"
    except Exception:
        logger.exception("job %s (%s) failed", job.job_id, job.kind)
        return False


def tick_dispatch(
    engine: Engine,
    store: ContentStore,
    *,
    worker_id: str,
    lease_s: float = 30.0,
    limit: int = 10,
    flags: FeatureFlags = DEFAULT_FLAGS,
) -> int:
    """Claim and finish only pure-dispatch jobs (BA-45).

    Provider-needing ``generate`` jobs are excluded AT CLAIM TIME so a job that
    becomes due while a generation is in flight is claimable on the very next
    pass — it never waits behind a model call inside this worker."""
    from app.db.tx import claim_jobs

    claimed = claim_jobs(
        engine,
        worker_id=worker_id,
        lease_s=lease_s,
        limit=limit,
        defer_provider_generation=True,
    )
    return sum(
        _finish(
            engine,
            store,
            job,
            worker_id,
            None,
            flags=flags,
        )
        for job in claimed
    )


def tick_generation(
    engine: Engine,
    store: ContentStore,
    *,
    worker_id: str,
    provider: TextProvider | None,
    lease_s: float = 30.0,
    limit: int = 10,
    flags: FeatureFlags = DEFAULT_FLAGS,
) -> int:
    """Claim and finish ``generate``/``qa_answer`` jobs, each under a lease
    re-extended to cover the blocking provider call plus the dispatch margin
    (BA-45).

    The lease extension is authorized by the same ``status='claimed' AND
    lease_owner`` check ``finish_job`` uses: a job another worker re-claimed
    cannot be extended, so it is skipped rather than double-run. Deterministic
    (``deterministic_only``) jobs skip the provider call entirely."""
    from app.db.tx import JobLeaseLostError, claim_jobs, extend_job_lease

    claimed = claim_jobs(
        engine,
        worker_id=worker_id,
        lease_s=lease_s,
        limit=limit,
        kinds=["generate", "qa_answer", "chat_judge"],
    )
    done = 0
    for job in claimed:
        generation: dict[str, Any] | None = None
        if job.attempts >= 3:
            if job.kind == "qa_answer":
                # Rules Q&A resolves instead of dead-lettering: three provider
                # misses commit the deterministic fallback — the exchange must
                # never sit pending forever.
                generation = {"error": "attempts_exhausted"}
            else:
                from app.db.tx import finish_job

                try:
                    finish_job(engine, job, worker_id=worker_id, handler=_generation_dead_letter)
                except Exception:
                    logger.exception("generation terminal outcome unavailable job=%s", job.job_id)
                continue
        elif _needs_provider_generation(job):
            from app.services import qa

            default_timeout = qa.TIMEOUT_MS if job.kind == "qa_answer" else 25000
            timeout_s = float(job.row["payload"].get("timeout_ms") or default_timeout) / 1000.0
            try:
                job = extend_job_lease(
                    engine, job, worker_id=worker_id, lease_s=timeout_s + lease_s
                )
            except JobLeaseLostError:
                continue
            try:
                with engine.connect() as conn:
                    if job.kind == "chat_judge":
                        generation = run_chat_judge(
                            conn,
                            job.row,
                            store=store,
                            provider=provider,
                            timeout_s=timeout_s,
                            flags=flags,
                        )
                    else:
                        generation = run_generation(
                            conn,
                            job.row,
                            provider=provider,
                            timeout_s=timeout_s,
                        )
            except Exception:
                # Keep unexpected infrastructure/programming faults distinct from model
                # rejection. Lease retry is bounded; process the other claimed jobs.
                logger.exception("generation execution failed job=%s", job.job_id)
                continue
        if _finish(
            engine,
            store,
            job,
            worker_id,
            generation,
            flags=flags,
        ):
            done += 1
    return done


def tick(
    engine: Engine,
    store: ContentStore,
    *,
    worker_id: str,
    provider: TextProvider | None,
    lease_s: float = 30.0,
    limit: int = 10,
    flags: FeatureFlags = DEFAULT_FLAGS,
) -> int:
    """One full pass: dispatch jobs, then generation jobs (BA-45).

    ``finish_job`` remains the sole commit point — the session lock and the
    lease-owner check keep delivery exactly-once even when a stale lease has
    already moved a job to another worker."""
    return tick_dispatch(
        engine,
        store,
        worker_id=worker_id,
        lease_s=lease_s,
        limit=limit,
        flags=flags,
    ) + tick_generation(
        engine,
        store,
        worker_id=worker_id,
        provider=provider,
        lease_s=lease_s,
        limit=limit,
        flags=flags,
    )


def _generation_dead_letter(ctx: TxContext, job: Mapping[str, Any]) -> str:
    """Bound persistent failures without repeating inference indefinitely."""
    seq = ctx.append_event(
        "generation_terminal_failure",
        actor="scheduler",
        payload={
            "job_id": str(job["job_id"]),
            "reason": "retry_limit",
            "request_id": job["payload"].get("request_id"),
        },
    )
    request_id = job["payload"].get("request_id")
    if request_id:
        ctx.conn.execute(
            update(s.request_episode)
            .where(
                s.request_episode.c.session_id == ctx.session_id,
                s.request_episode.c.request_id == uuid.UUID(request_id),
                s.request_episode.c.closed_at.is_(None),
            )
            .values(
                status="resolved",
                closed_at=ctx.now,
                closed_event_seq=seq.seq,
                close_reason="generation_terminal_failure",
            )
        )
    return "failed"


JUDGE_SYSTEM = (
    "You are an analytic judge reviewing one Investigators team-experiment "
    "transcript. The participant worked with two AI teammates (JT, PM) to "
    "record crate locations under a subjective workload manipulation. Judge "
    "the transcript against the researcher's question. Be concise, cite "
    "message timestamps like [3:12], and say 'not determinable' when the "
    "transcript lacks evidence."
)
JUDGE_TIMEOUT_MS = 60000
JUDGE_MAX_TOKENS = 700


def run_chat_judge(
    conn: Connection,
    job_row: Mapping[str, Any],
    *,
    store: ContentStore,
    provider: TextProvider | None,
    timeout_s: float,
    flags: FeatureFlags = DEFAULT_FLAGS,
) -> dict[str, Any]:
    """Provider call for a ``chat_judge`` job between claim and commit — same
    out-of-lock discipline as ``run_qa_generation``. Admin-originated, so no
    generation-policy pin is required; the prompt is the transcript plus the
    researcher's question."""
    p = job_row["payload"]
    question = str(p.get("question") or "").strip()[:2000] or (
        "Summarize how the participant used teammate input."
    )
    rows = conn.execute(
        select(
            s.message.c.actor, s.message.c.body, s.message.c.delivered_at,
            s.message.c.committed_seq, s.message.c.channel,
        )
        .where(s.message.c.session_id == job_row["session_id"])
        .order_by(s.message.c.committed_seq)
    ).mappings().all()
    t0 = rows[0]["delivered_at"] if rows else None
    lines = []
    for r in rows:
        sec = int((r["delivered_at"] - t0).total_seconds()) if t0 and r["delivered_at"] else 0
        lines.append(f"[{sec//60}:{sec%60:02d}] {r['actor']}: {r['body']}")
    transcript = "\n".join(lines)[-24000:] or "(no messages)"
    session = (
        conn.execute(select(s.session).where(s.session.c.session_id == job_row["session_id"]))
        .mappings()
        .one()
    )
    params: dict[str, Any] = {
        "temperature": 0.2,
        "max_tokens": JUDGE_MAX_TOKENS,
        "seed": derived_seed(job_row["session_id"], job_row["job_id"]),
    }
    if getattr(provider, "supports_reasoning_effort", False):
        params["reasoning_effort"] = "none"
    pin = getattr(provider, "model_id", None) if provider is not None else None
    audit: dict[str, Any] = {
        "prompt_id": "chat_judge.v1",
        "parameters": dict(params),
        "timeout_ms": round(timeout_s * 1000),
        "dispatched": False,
    }
    if provider is None:
        return {
            "error": "unavailable", "provider_id": "none", "model_id": "none",
            "latency_ms": 0, "audit": audit,
        }
    if getattr(provider, "is_remote", False):
        return {
            "error": "provider_not_allowed",
            "provider_id": provider.provider_id,
            "model_id": str(pin or "unavailable"),
            "latency_ms": 0, "audit": audit,
        }
    request = ProviderRequest(
        purpose="chat_judge",
        prompt_id="chat_judge.v1",
        messages=(
            Message(role="system", content=JUDGE_SYSTEM),
            Message(role="user", content=f"Transcript:\n{transcript}\n\nQuestion: {question}"),
        ),
        params=params,
        request_id=str(job_row["job_id"]),
    )
    try:
        call_started = time.monotonic()
        response: ProviderResponse = provider.generate(request, timeout_s=timeout_s)
    except ProviderError as exc:
        return {
            "error": exc.kind.value,
            "provider_id": provider.provider_id,
            "model_id": str(pin or "unavailable"),
            "latency_ms": round((time.monotonic() - call_started) * 1000),
            "audit": {**audit, "dispatched": exc.dispatched},
        }
    try:
        response.text.encode("utf-8", errors="strict")
        if "\x00" in response.text or response.finish_reason not in (None, "stop"):
            raise ValueError("invalid completion")
    except (UnicodeError, ValueError, AttributeError):
        return {
            "error": "invalid_response",
            "provider_id": provider.provider_id,
            "model_id": str(pin or "unavailable"),
            "latency_ms": response.latency_ms,
            "audit": {**audit, "dispatched": True},
        }
    return {
        "text": response.text,
        "provider_id": response.provider_id,
        "model_id": response.model_id,
        "model_digest": getattr(provider, "model_digest", ""),
        "latency_ms": response.latency_ms,
        "finish_reason": response.finish_reason,
        "audit": {**audit, "dispatched": True},
    }


def _chat_judge(
    ctx: TxContext,
    _bound: BoundManifest,
    job: Mapping[str, Any],
    generation: Mapping[str, Any] | None,
) -> str:
    """Commit an AI-judge verdict as an ``admin_audit`` row — auditable,
    session-scoped, and readable by the dashboard's judge panel. Always
    resolves: a provider miss is recorded, never silently dropped."""
    p = job["payload"]
    gen = generation or {}
    verdict = gen.get("text") or f"judge unavailable — {gen.get('error', 'no generation')}"
    ctx.conn.execute(
        s.admin_audit.insert().values(
            audit_id=uuid.uuid4(),
            session_id=ctx.session_id,
            actor="admin:judge",
            action="chat_judge_verdict",
            payload={
                "question": str(p.get("question") or ""),
                "asked_by": str(p.get("asked_by") or "admin"),
                "verdict": verdict,
                "provider_id": gen.get("provider_id"),
                "model_id": gen.get("model_id"),
                "model_digest": gen.get("model_digest"),
                "latency_ms": gen.get("latency_ms"),
                "finish_reason": gen.get("finish_reason"),
                "error": gen.get("error"),
                "audit": gen.get("audit"),
            },
            occurred_at=ctx.now,
        )
    )
    ctx.append_event(
        "chat_judged",
        actor="system",
        payload={"question": str(p.get("question") or "")[:120], "ok": gen.get("error") is None},
    )
    return "committed"
