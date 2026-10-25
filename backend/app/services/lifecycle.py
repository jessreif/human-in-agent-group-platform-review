"""Session lifecycle command handlers (INV-0403/0405/0407; spec §5.2, §8.2).

Each handler runs inside :func:`app.db.tx.execute_command` — the session row is locked, the
receipt/event/outbox write is atomic, and rejections roll back only the handler's writes.

Stage gating uses ``COMMAND_STAGES``; the legal-move and prerequisite checks live in
``app.domain.stage_graph``. Instrument-stage advancement is completeness-driven: when every
declared item (or, for per-crate instruments, every answered crate's item) has a recorded
response — answered *or* skipped, per S5's skip-without-penalty rule — the stage advances.

Domain payloads (answer crates, requests, shares) are persisted as events now; their full
domain rows gain writers in WP05-WP07 when the world and teammate engines land.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any

from sqlalchemy import func, insert, select, update

from app.db import schema as s
from app.db.tx import RejectionError, TxContext
from app.domain import stage_graph as sg
from app.domain.compat import UNRESOLVED_MODEL
from app.domain.manifests.binding import BoundManifest, bind
from app.domain.manifests.store import ContentStore, StoredArtifact
from app.flags import DEFAULT_FLAGS, FeatureFlags
from app.services import (
    activetime,
    credentials,
    engines,
    qa,
    scheduler,
    worlds,
)
from app.services.allocation import AllocationError, GroupCell, allocate, allocate_group
from app.services.completion import (
    ABANDONMENT_IDLE_S as ABANDONMENT_IDLE_S,
)
from app.services.completion import (
    completion_code as completion_code,
)
from app.services.completion import (
    completion_handler as completion_handler,
)
from app.services.completion import (
    sweep_abandoned as sweep_abandoned,
)
from app.services.generation_policy import identity
from app.services.instrument_commands import (
    ASSISTANT_INSTRUMENT_ITEMS as ASSISTANT_INSTRUMENT_ITEMS,
)
from app.services.instrument_commands import (
    LEGACY_OPEN_ENDED_ITEMS as LEGACY_OPEN_ENDED_ITEMS,
)
from app.services.instrument_commands import (
    LEGACY_OPEN_ENDED_MAX_CHARS as LEGACY_OPEN_ENDED_MAX_CHARS,
)
from app.services.instrument_commands import (
    LEGACY_OPEN_ENDED_VERSION as LEGACY_OPEN_ENDED_VERSION,
)
from app.services.instrument_commands import (
    OPEN_ENDED_INSTRUMENT_ID as OPEN_ENDED_INSTRUMENT_ID,
)
from app.services.instrument_commands import (
    OPEN_ENDED_INSTRUMENT_IDS as OPEN_ENDED_INSTRUMENT_IDS,
)
from app.services.instrument_commands import (
    PRESSURE_INSTRUMENT_ITEMS as PRESSURE_INSTRUMENT_ITEMS,
)
from app.services.instrument_commands import (
    SOURCE_RELIANCE_NO_ASSISTANT_TEXT as SOURCE_RELIANCE_NO_ASSISTANT_TEXT,
)
from app.services.instrument_commands import (
    advance_if_complete,
    item_response,
)
from app.services.instrument_commands import (
    instrument_item_text as instrument_item_text,
)
from app.services.instrument_commands import (
    instrument_item_visible as instrument_item_visible,
)
from app.services.instrument_commands import (
    instrument_items as instrument_items,
)
from app.services.lifecycle_core import (
    COMMAND_STAGES as COMMAND_STAGES,
)
from app.services.lifecycle_core import (
    INSTRUMENT_SLOTS as INSTRUMENT_SLOTS,
)
from app.services.lifecycle_core import (
    INSTRUMENT_STAGES,
    enforce_rate_limit,
    gate,
    resolved_policy,
    transition,
)
from app.services.outcomes import (  # explicit re-exports: historical import path
    NO_BONUS_PAYMENT_ARTIFACT_KEY as NO_BONUS_PAYMENT_ARTIFACT_KEY,
)
from app.services.outcomes import (
    record_terminal_outcome as record_terminal_outcome,
)
from app.services.session_binding import (  # explicit re-exports: historical import path
    bound_for_session as bound_for_session,
)
from app.services.session_binding import (
    variant_for_manifest,
)
from app.services.sessions import rebind_session
from app.services.team_chat import (
    assistant_request,
    greeting,
    record_share,
    removed_add_to_file,
    social_reply,
    teammate_request,
)
from app.services.training import (
    ASSISTANT_COMPREHENSION_ITEM as ASSISTANT_COMPREHENSION_ITEM,
)
from app.services.training import (
    COMPREHENSION_INSTRUMENT_ID as COMPREHENSION_INSTRUMENT_ID,
)
from app.services.training import (
    COMPREHENSION_ITEMS as COMPREHENSION_ITEMS,
)
from app.services.training import (
    COMPREHENSION_KEY as COMPREHENSION_KEY,
)
from app.services.training import (
    COMPREHENSION_VERSION as COMPREHENSION_VERSION,
)
from app.services.training import (
    comprehension_key as comprehension_key,
)
from app.services.training import (
    enter_scripted_connecting,
    training_step,
)
from app.services.training import (
    practice_ask_required as practice_ask_required,
)
from app.timing.policy import resolve_policy, time_pressure_for_arm

# ----- individual handlers -----------------------------------------------------------------


_INITIALS_RE = re.compile(r"[A-Z]{1,3}")


def _set_initials(ctx: TxContext, p: Mapping[str, Any]) -> dict[str, Any]:
    """Store the participant's display initials on the background page (UX round 2):
    1-3 letters, normalized to uppercase, written once. The row lives in
    ``app.participant_display`` — server-side, excluded from every research export,
    and deleted by the tombstone's mutable purge. The marker event carries no
    payload so initials never reach the exported event log."""
    raw = p.get("initials")
    if not isinstance(raw, str):
        raise RejectionError("malformed_initials")
    initials = raw.strip().upper()
    if not _INITIALS_RE.fullmatch(initials):
        raise RejectionError("malformed_initials")
    existing = ctx.conn.execute(
        select(s.participant_display.c.initials).where(
            s.participant_display.c.session_id == ctx.session_id
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing == initials:
            return {"set": True}  # idempotent retry
        raise RejectionError("initials_locked")
    ctx.conn.execute(
        insert(s.participant_display).values(session_id=ctx.session_id, initials=initials)
    )
    ctx.append_event("initials_recorded", actor="participant", payload={})
    # The result persists verbatim in command_receipt.result, which is exported — it must
    # not echo the initials value (same reason the event payload is empty).
    return {"set": True}


# BONUS_PAYMENT_ENABLED=0 (owner direction 2026-10-16): the no-bonus posture is a
# deployment-flag artifact substitution, not a manifest repin — manifests keep
# pinning the bonus-on contract (consent.reference / payment.s5_base_only) and
# these alternates are served and recorded while the flag is off, so the
# consent text_version and payment policy on the session always name what was
# actually shown and paid.
NO_BONUS_CONSENT_ARTIFACT_KEY = "consent.reference.nobonus@v1"


def effective_consent_artifact(
    bound: BoundManifest,
    store: ContentStore | None,
    *,
    bonus_payment_enabled: bool = True,
) -> StoredArtifact | None:
    """The consent artifact this deployment actually serves/accepts (BA-37 follow-up).

    Under BONUS_PAYMENT_ENABLED=0 the served consent is the ``nobonus`` variant —
    a distinct artifact whose key the client echoes as ``text_version``, so the
    recorded consent always names the copy the participant actually saw. A flag-off
    deployment that cannot resolve the alternate returns None rather than falling
    back to a consent that still offers the bonus."""
    if bonus_payment_enabled:
        return bound.artifacts.get("instruments.consent")
    if store is None:
        return None
    return store.artifacts.get(NO_BONUS_CONSENT_ARTIFACT_KEY)


def _consent(
    ctx: TxContext,
    bound: BoundManifest,
    p: Mapping[str, Any],
    *,
    behavior_sha: str,
    provider_model_digest: str = UNRESOLVED_MODEL,
    store: ContentStore | None = None,
    flags: FeatureFlags = DEFAULT_FLAGS,
) -> dict[str, Any]:
    """Record consent or decline. The text version must equal the bound consent
    artifact (BA-37; the no-bonus substitute when ``bonus_payment`` is off). Consent
    allocates a group cell (rebinding the session to that cell's manifest), pins the
    generation policy, refuses manifests that need a disabled flag, and advances to
    instructions; decline terminates with a recorded outcome."""
    action = p.get("action")
    text_version = p.get("text_version")
    if action not in ("consented", "declined") or not isinstance(text_version, str):
        raise RejectionError("malformed_consent")
    # BA-37: the recorded text version must be the *bound* consent artifact key, so the
    # consent receipt can never drift from the copy the session was actually served.
    consent_art = effective_consent_artifact(
        bound, store, bonus_payment_enabled=flags.bonus_payment
    )
    if consent_art is None:
        # Flag-off without the nobonus artifact is an operator incident — refuse
        # rather than accept consent against copy that still offers the bonus.
        raise RejectionError(
            "consent_unavailable" if not flags.bonus_payment else "consent_version_mismatch"
        )
    if text_version != consent_art.key:
        raise RejectionError("consent_version_mismatch")
    # Fail closed (BA-37 follow-up): a pointer-only consent artifact carries no literal
    # copy, so no valid consent can be recorded against it. Declining stays allowed —
    # the session must still reach an honest terminal state.
    if action == "consented" and not getattr(consent_art.artifact.body, "text", None):
        raise RejectionError("consent_unavailable")
    seq = ctx.append_event("consent_recorded", actor="participant", payload={"action": action})
    ctx.conn.execute(
        insert(s.consent_event).values(
            consent_id=uuid.uuid4(),
            session_id=ctx.session_id,
            text_version=text_version,
            action=action,
            occurred_at=ctx.now,
        )
    )
    if action == "declined":
        transition(ctx, bound, "declined", reason="consent_declined")
        record_terminal_outcome(
            ctx,
            bound,
            outcome="declined",
            reconciled={"reason": "consent_declined"},
            store=store,
            bonus_payment_enabled=flags.bonus_payment,
        )
        return {"outcome": "declined"}
    if ctx.session["arm"] is None and ctx.session.get("batch_group_id") and store is not None:
        # Batch-group entry: the cell (member batch x arm) is drawn now, so declines and
        # consent-page abandonment never consume a balanced slot. The session moves onto the
        # cell's manifest before any manifest-dependent state exists (the world is generated
        # at ready); every member binds the same consent artifact (create_batch_group).
        def _rebind(cell: GroupCell) -> BoundManifest:
            new = bind(store, cell.manifest_id, mode=bound.mode)
            new_consent = new.artifacts.get("instruments.consent")
            bound_consent = bound.artifacts.get("instruments.consent")
            if new_consent is None or bound_consent is None or new_consent.key != bound_consent.key:
                raise RejectionError("batch_group_consent_mismatch")
            rebind_session(ctx.conn, ctx.session_id, batch_id=cell.batch_id, bound=new)
            return new

        cell, bound = allocate_group(
            ctx.conn,
            session_id=ctx.session_id,
            group_id=str(ctx.session["batch_group_id"]),
            rebind=_rebind,
            behavior_source_sha256=behavior_sha,
            now=ctx.now,
            provider_model_digest=provider_model_digest,
            time_pressure_enabled=flags.time_pressure,
        )
        ctx.session = {
            **ctx.session,
            "arm": cell.arm,
            "batch_id": cell.batch_id,
            "manifest_id": bound.manifest_id,
            "manifest_sha256": bound.manifest_sha256,
            "bundle_sha256": bound.bundle_sha256,
            "binding_sha256": bound.binding_sha256,
            "binding": bound.binding_record(),
        }
    elif ctx.session["arm"] is None:  # live entry assigns at consent; previews pin the arm
        arm = allocate(
            ctx.conn,
            session_id=ctx.session_id,
            bound=bound,
            batch_id=ctx.session["batch_id"],
            behavior_source_sha256=behavior_sha,
            now=ctx.now,
            provider_model_digest=provider_model_digest,
            time_pressure_enabled=flags.time_pressure,
        )
        ctx.session = {**ctx.session, "arm": arm}
    # Feature flag (owner direction 2026-10-11): the scripted-teammate family
    # (``social_generation: deterministic_only``) is excluded from live-mode
    # fielding unless the deployment opts in. Batch/group mint and enrollment-open
    # refuse it too; this is the last-line check before a live session lands on
    # the cell (the rejection rolls back the draw, rebind and allocation row
    # atomically, so no slot is consumed).
    if (
        ctx.session["mode"] == "live"
        and bound.manifest.teammates.social_generation == "deterministic_only"
        and not flags.scripted_teammates
    ):
        raise RejectionError("scripted_teammates_disabled")
    # Same flag family (owner direction 2026-10-15): manifests pinning
    # non-legacy task shapes — a crate_overlap level or an intel budget — are
    # excluded from live fielding unless their gate is enabled.
    if ctx.session["mode"] == "live":
        overlap = bound.manifest.task.crate_overlap
        if overlap is not None and overlap != "independent" and not flags.crate_overlap:
            raise RejectionError("crate_overlap_disabled")
        if bound.manifest.task.participant_intel_budget is not None and not flags.crate_number:
            raise RejectionError("crate_number_disabled")

    ctx.append_event(
        "generation_policy_pinned",
        actor="system",
        payload=identity(behavior_sha, provider_model_digest),
    )
    transition(ctx, bound, "instructions", consent_action="consented")
    # INV-0904: entering instructions serves the PE intro + callout surfaces; commit
    # durable per-surface receipts for the bound copy actually rendered (control-arm
    # empty surfaces are skipped — exposure is never fabricated).

    engines.commit_pe_exposures(ctx, bound, ("pe_intro", "pe_callout"))
    ctx.append_event(
        "assigned",
        actor="system",
        payload={"arm": ctx.session["arm"]},
        parent_event_id=seq.event_id,
    )
    return {"outcome": "consented", "arm": ctx.session["arm"]}


def _ready(ctx: TxContext, bound: BoundManifest, _p: Mapping[str, Any]) -> dict[str, Any]:
    # INV-0701: Start is disabled until every teammate has presented (the demo's disabled
    # button). ``advance_due`` has already swept due wall jobs inside this transaction, so a
    # matured PM join is committed by the time this gate reads the event log.
    presented = ctx.conn.execute(
        select(func.count())
        .select_from(s.event)
        .where(
            s.event.c.session_id == ctx.session_id,
            s.event.c.type == "teammate_presented",
            s.event.c.payload["seat"].astext == "PM",
        )
    ).scalar_one()
    if not presented:
        raise RejectionError("connecting_incomplete")
    transition(ctx, bound, "ready")
    # INV-0504: the participant's file becomes visible next stage, so the world snapshot,
    # records, holdings, initial file order and realized draws are committed here.

    worlds.ensure_world(
        ctx.conn,
        session_id=ctx.session_id,
        seed_hex=ctx.session["seed_hex"],
        arm=ctx.session["arm"],
        bound=bound,
    )
    # INV-0701: the demo's post-start "waiting for teammates" hold — the task_open wall job
    # performs the ready -> first-task-stage hop and starts the active clock, so the clock
    # begins at task-open rather than at the button press.

    open_at = ctx.now + timedelta(milliseconds=scheduler.CONNECT_TASK_OPEN_WALL_MS)
    ctx.schedule_job("task_open", kind="task_open", due_wall_at=open_at, claimable_at=open_at)
    return {"stage": "ready"}


def _withdraw(
    ctx: TxContext,
    bound: BoundManifest,
    _p: Mapping[str, Any],
    *,
    store: ContentStore | None = None,
    bonus_payment_enabled: bool = True,
) -> dict[str, Any]:
    """Participant withdrawal (spec §8.1): at consent this is a ``declined`` disposition;
    afterwards a ``withdrawn`` one. Both persist their ``completion_receipt`` and base
    ``payment_entitlement`` atomically with the terminal transition — whether the base
    is kept follows the manifest's ``outcomes.withdrawal_payment`` (BA-97)."""
    from_stage = ctx.stage
    if from_stage == "consent":
        transition(ctx, bound, "declined", reason="consent_declined")
        record_terminal_outcome(
            ctx,
            bound,
            outcome="declined",
            reconciled={"reason": "consent_declined"},
            store=store,
            bonus_payment_enabled=bonus_payment_enabled,
        )
        return {"outcome": "declined"}
    transition(ctx, bound, "withdrawn", reason="participant_withdrawal")
    seq = ctx.append_event("session_terminated", actor="system", payload={"outcome": "withdrawn"})
    # AB-19: withdrawal mid-task can never be answered again — expire open episodes
    # explicitly instead of leaving them pending.

    engines.expire_open_episodes(
        ctx.conn,
        ctx.session_id,
        seq=seq.seq,
        at=ctx.now,
        at_active_ms=activetime.active_ms(ctx.conn, ctx.session_id, ctx.now),
        reason="session_abandoned",
    )
    record_terminal_outcome(
        ctx,
        bound,
        outcome="withdrawn",
        final_event_seq=seq.seq,
        reconciled={
            "reason": "participant_withdrawal",
            "withdrawn_from_stage": from_stage,
        },
        store=store,
        bonus_payment_enabled=bonus_payment_enabled,
    )
    return {"outcome": "withdrawn"}


def _answer_update(ctx: TxContext, bound: BoundManifest, p: Mapping[str, Any]) -> dict[str, Any]:
    crate, location = p.get("crate"), p.get("location_id")
    if (
        not isinstance(crate, int)
        or not (1 <= crate <= bound.manifest.task.n_crates)
        or not isinstance(location, str)
    ):
        raise RejectionError("malformed_answer")
    # Spec §6.2: an answer is accepted only while active elapsed is strictly below the
    # budget at locked server validation; client timestamps never override this.

    budget = resolve_policy(bound, ctx.session["arm"]).budget_ms
    if activetime.active_ms(ctx.conn, ctx.session_id, ctx.now) >= budget:
        raise RejectionError("task_closed")
    revision = (
        ctx.conn.execute(
            select(func.count())
            .select_from(s.answer_revision)
            .where(
                s.answer_revision.c.session_id == ctx.session_id,
                s.answer_revision.c.crate == crate,
            )
        ).scalar_one()
        + 1
    )
    seq = ctx.append_event(
        "answer_updated",
        actor="participant",
        payload={"crate": crate, "location_id": location, "revision": revision},
    )
    ctx.conn.execute(
        insert(s.answer_revision).values(
            session_id=ctx.session_id,
            crate=crate,
            revision=revision,
            location_id=location,
            command_id=ctx.command_id,
            accepted_seq=seq.seq,
        )
    )
    return {"crate": crate, "revision": revision}


def _pressure(
    ctx: TxContext,
    bound: BoundManifest,
    p: Mapping[str, Any],
    *,
    skip: bool,
    time_pressure: bool,
) -> dict[str, Any]:
    """Pressure check-in: record own rating on the open pressure_checkin exposure and move
    to checkin_wait (spec §5.2 TP loop)."""
    if not time_pressure:
        # TIME_PRESSURE_ENABLED=0 removes the manipulation in every mode — the
        # checkin job is never scheduled, so an open exposure shouldn't exist;
        # fail closed anyway rather than honoring a stray command.
        raise RejectionError("time_pressure_disabled")
    value = p.get("value")
    if not skip and not (isinstance(value, (int, float)) and 0 <= value <= 10):
        raise RejectionError("pressure_out_of_range")
    if skip:
        # UX round 3 (item 5): profiles that make the own rating required refuse the
        # skip server-side — hiding the button alone is not an experimental control.
        policy_checkin = resolved_policy(ctx, bound).checkin
        if policy_checkin is not None and not policy_checkin["own_rating_skippable"]:
            raise RejectionError("skip_not_permitted")
    exposure = (
        ctx.conn.execute(
            select(s.manipulation_exposure)
            .where(
                s.manipulation_exposure.c.session_id == ctx.session_id,
                s.manipulation_exposure.c.kind == "pressure_checkin",
                s.manipulation_exposure.c.acknowledged_seq.is_(None),
            )
            .order_by(s.manipulation_exposure.c.scheduled_at.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    if exposure is None:
        raise RejectionError("no_open_checkin")
    seq = ctx.append_event(
        "checkin_response",
        actor="participant",
        payload={"exposure_id": str(exposure["exposure_id"]), "skipped": skip},
    )
    ctx.conn.execute(
        update(s.manipulation_exposure)
        .where(s.manipulation_exposure.c.exposure_id == exposure["exposure_id"])
        .values(
            own_rating=None if skip else value,
            acknowledged_seq=seq.seq,
            status="acknowledged",
        )
    )
    transition(ctx, bound, "checkin_wait")
    # The 3.5 s collection presentation runs on the wall clock inside the permitted pause
    # (spec §6.4); the reveal job transitions checkin_wait -> checkin_reveal.

    checkin = resolve_policy(bound, ctx.session["arm"]).checkin
    if checkin is None:
        raise RejectionError("checkin_unavailable")
    collect_ms = int(checkin["collect_wall_ms"])
    ctx.schedule_job(
        "checkin_reveal",
        kind="checkin_reveal",
        due_wall_at=ctx.now + timedelta(milliseconds=int(collect_ms)),
        claimable_at=ctx.now + timedelta(milliseconds=int(collect_ms)),
        payload={"exposure_id": str(exposure["exposure_id"])},
    )
    return {"stage": "checkin_wait"}


def _data_use_choice(ctx: TxContext, p: Mapping[str, Any]) -> dict[str, Any]:
    """Record the data-use disposition exactly once per session (BA-44). Re-submitting the
    SAME choice is an accepted idempotent no-op — the debrief→complete handoff retries it
    when the earlier command committed but its acknowledgement (or the completion) was
    lost. A different choice after a committed row is refused, never overwritten."""
    choice = p.get("choice")
    if choice not in s.DATA_USE_CHOICES:
        raise RejectionError("malformed_choice")
    existing = ctx.conn.execute(
        select(s.data_use_choice.c.choice).where(
            s.data_use_choice.c.session_id == ctx.session_id,
            s.data_use_choice.c.choice_seq == 1,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing != choice:
            raise RejectionError("choice_locked")
        return {"choice": existing, "already": True}
    seq = ctx.append_event("data_use_recorded", actor="participant", payload={"choice": choice})
    ctx.conn.execute(
        insert(s.data_use_choice).values(
            session_id=ctx.session_id,
            choice_seq=1,
            choice=choice,
            recorded_seq=seq.seq,
            recorded_at=ctx.now,
        )
    )
    return {"choice": choice}


def _debrief_ack(ctx: TxContext, bound: BoundManifest, _p: Mapping[str, Any]) -> dict[str, Any]:
    transition(ctx, bound, "data_use_choice")
    ctx.append_event("debrief_acknowledged", actor="participant")
    return {"stage": "data_use_choice"}


def _resume(
    ctx: TxContext, _p: Mapping[str, Any], writer: uuid.UUID | None = None
) -> dict[str, Any]:
    """Reconnect: reclaim the writer lease (free or expired) and record the resume.
    When the caller already holds a live lease this is an idempotent renew — entry grants
    the lease at session open, so the bootstrap resume must not self-reject."""
    holder = ctx.session["writer_id"]
    until = ctx.session["writer_lease_until"]
    if holder is not None and until is not None and until > ctx.now:
        if holder != writer:
            raise RejectionError("writer_lease_held")
        credentials.renew_writer(ctx.conn, ctx.session_id, writer, now=ctx.now)
        return {"stage": ctx.stage, "writer_id": str(holder)}
    try:
        acquired = credentials.acquire_writer(ctx.conn, ctx.session_id, now=ctx.now)
    except credentials.CredentialError:
        raise RejectionError("writer_lease_held") from None
    ctx.append_event("resumed", actor="participant")
    return {"stage": ctx.stage, "writer_id": str(acquired)}


def _qa_ask(
    ctx: TxContext,
    _bound: BoundManifest,
    p: Mapping[str, Any],
    *,
    ai_qa_enabled: bool,
) -> dict[str, Any]:
    """AI_QA_ENABLED (2026-10-16): record one rules-Q&A question and plant the
    ``qa_answer`` wall job — the provider call runs outside the session lock in
    the generation lane, the same shape as teammate/assistant generation."""
    if not ai_qa_enabled:
        raise RejectionError("qa_disabled")
    question = qa.clean_question(p.get("question"))
    if question is None:
        raise RejectionError("malformed_question")
    count = int(
        ctx.conn.execute(
            select(func.count())
            .select_from(s.qa_exchange)
            .where(s.qa_exchange.c.session_id == ctx.session_id)
        ).scalar_one()
    )
    if count >= qa.MAX_EXCHANGES:
        raise RejectionError("qa_limit_reached")
    seq = count + 1
    exchange_id = uuid.uuid4()
    ref = ctx.append_event(
        "qa_asked", actor="participant", payload={"seq": seq, "question": question}
    )
    ctx.conn.execute(
        insert(s.qa_exchange).values(
            session_id=ctx.session_id,
            exchange_id=exchange_id,
            seq=seq,
            question=question,
            status="pending",
            asked_seq=ref.seq,
        )
    )
    ctx.schedule_job(
        f"qa_answer.{seq}",
        kind="qa_answer",
        due_wall_at=ctx.now,
        claimable_at=ctx.now,
        payload={
            "exchange_id": str(exchange_id),
            "timeout_ms": qa.TIMEOUT_MS,
            "generation_policy": ctx.conn.execute(
                select(s.event.c.payload).where(
                    s.event.c.session_id == ctx.session_id,
                    s.event.c.type == "generation_policy_pinned",
                )
            ).scalar_one_or_none(),
        },
    )
    return {"seq": seq, "status": "pending"}


def _proceed(
    ctx: TxContext,
    bound: BoundManifest,
    _p: Mapping[str, Any],
    *,
    ai_assistant: bool,
    time_pressure: bool,
) -> dict[str, Any]:
    """Advance along the stage's unique non-terminal forward edge (checkin_reveal -> task,
    answers_locked -> confidence), or past a *complete* instrument stage. The instrument
    path exists for the zero-answered-crates confidence case — no item commit can advance
    a stage with nothing to rate — and as an explicit continue for the others."""
    if ctx.stage in INSTRUMENT_STAGES:
        new_stage = advance_if_complete(
            ctx, bound, ctx.stage, ai_assistant=ai_assistant, time_pressure=time_pressure
        )
        if new_stage is None:
            raise RejectionError("instrument_incomplete")
        return {"stage": new_stage}
    variant = variant_for_manifest(bound.manifest)
    targets = [t for t in sg.legal_targets(variant, ctx.stage) if t not in s.TERMINAL_STAGES]
    if len(targets) != 1:
        raise RejectionError("no_unique_next_stage")
    if targets[0] == "task":
        # Explicit resume from the check-in reveal: restart the active clock in the same
        # transaction as the stage change (spec §6.4 "Generation may finish during the
        # pause, but readiness does not authorize display").

        activetime.resume(ctx.conn, ctx.session_id, reason="checkin_resume", now=ctx.now)
    if targets[0] == "scripted_connecting":
        # AI_QA_ENABLED=1: the ai_qa Continue enters connecting the same way
        # training completion does — JT presented, PM join job planted.
        enter_scripted_connecting(ctx, bound)
    else:
        transition(ctx, bound, targets[0])
    ctx.append_event("stage_proceeded", actor="participant", payload={"to": targets[0]})
    return {"stage": targets[0]}


def _takeover(ctx: TxContext, _p: Mapping[str, Any]) -> dict[str, Any]:
    writer = credentials.force_takeover(ctx.conn, ctx.session_id, now=ctx.now)
    ctx.append_event("writer_takeover", actor="participant", payload={"writer": str(writer)})
    return {"writer_id": str(writer)}


def handler_for(
    kind: str,
    *,
    store: ContentStore,
    behavior_sha: str,
    writer: uuid.UUID | None = None,
    provider_model_digest: str = UNRESOLVED_MODEL,
    flags: FeatureFlags = DEFAULT_FLAGS,
) -> Callable[[TxContext, Mapping[str, Any]], dict[str, Any]]:
    """Dispatch one command kind. ``writer`` (the presented writer-lease id) is checked
    inside the transaction against the locked session row, so a stale/incumbent cookie
    pre-gate cannot let a second writer through.

    Every handler receives its flags by keyword (audit C2): several share the
    ``(ctx, bound, p)`` prefix and then take only booleans, so a positional call
    could silently swap ``skip`` with a feature flag."""
    ai_assistant = flags.ai_assistant

    def pressure(ctx: TxContext) -> bool:
        return time_pressure_for_arm(ctx.session.get("arm"), enabled=flags.time_pressure)

    mapping: dict[str, Callable[[TxContext, BoundManifest, Mapping[str, Any]], dict[str, Any]]] = {
        "consent": lambda ctx, bound, p: _consent(
            ctx,
            bound,
            p,
            behavior_sha=behavior_sha,
            provider_model_digest=provider_model_digest,
            store=store,
            flags=flags,
        ),
        "set_initials": lambda ctx, _bound, p: _set_initials(ctx, p),
        "training_step": lambda ctx, bound, p: training_step(
            ctx, bound, p, ai_assistant=ai_assistant, ai_qa_enabled=flags.ai_qa
        ),
        "ready": _ready,
        "withdraw": lambda ctx, bound, p: _withdraw(
            ctx, bound, p, store=store, bonus_payment_enabled=flags.bonus_payment
        ),
        "answer_update": _answer_update,
        "pressure_response": lambda ctx, bound, p: _pressure(
            ctx, bound, p, skip=False, time_pressure=pressure(ctx)
        ),
        "pressure_skip": lambda ctx, bound, p: _pressure(
            ctx, bound, p, skip=True, time_pressure=pressure(ctx)
        ),
        "qa_ask": lambda ctx, bound, p: _qa_ask(ctx, bound, p, ai_qa_enabled=flags.ai_qa),
        "item_response": lambda ctx, bound, p: item_response(
            ctx, bound, p, skip=False, ai_assistant=ai_assistant, time_pressure=pressure(ctx)
        ),
        "item_skip": lambda ctx, bound, p: item_response(
            ctx, bound, p, skip=True, ai_assistant=ai_assistant, time_pressure=pressure(ctx)
        ),
        "debrief_ack": _debrief_ack,
        "proceed": lambda ctx, bound, p: _proceed(
            ctx, bound, p, ai_assistant=ai_assistant, time_pressure=pressure(ctx)
        ),
        "data_use_choice": lambda ctx, _bound, p: _data_use_choice(ctx, p),
        "resume": lambda ctx, _bound, p: _resume(ctx, p, writer),
        "takeover": lambda ctx, _bound, p: _takeover(ctx, p),
        "teammate_request": teammate_request,
        "record_share": record_share,
        "add_to_file": removed_add_to_file,
        "social_reply": social_reply,
        "assistant_request": lambda ctx, bound, p: assistant_request(
            ctx, bound, p, ai_assistant=ai_assistant
        ),
        "greeting": greeting,
    }
    if kind not in mapping:
        raise RejectionError("unknown_command")
    fn = mapping[kind]

    def dispatch(ctx: TxContext, payload: Mapping[str, Any]) -> dict[str, Any]:
        gate(ctx, kind)
        if (
            kind not in ("resume", "takeover")
            and ctx.session["writer_id"] is not None
            and ctx.session["writer_id"] != writer
        ):
            raise RejectionError("writer_lease_held")
        bound = bound_for_session(ctx.conn, store, ctx.session)
        # BA-75: a recognized automation probe bypasses the operational rate
        # limit too — a mandatory truthful disclosure must never be refused by
        # queue state, and its ``automation_probe`` audit trail must always
        # exist. Probe cost stays independently bounded: no model call, at most
        # MAX_PENDING_PROBE_DISCLOSURES unfinished disclosure lines per seat.
        probe_bypass = (
            kind == "social_reply"
            and isinstance(payload.get("body"), str)
            and engines.is_automation_probe(str(payload["body"]))
        )
        if not probe_bypass:
            enforce_rate_limit(ctx, bound)
        try:
            return fn(ctx, bound, payload)
        except AllocationError as exc:
            raise RejectionError(str(exc)) from exc

    return dispatch
