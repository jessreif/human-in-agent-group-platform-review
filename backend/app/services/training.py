"""Training stage: practice gate, comprehension check (canonical items + answer key)
and the hand-off to scripted connecting (audit H2).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from app.db import schema as s
from app.db.tx import RejectionError, TxContext
from app.domain.manifests.binding import BoundManifest
from app.services import (
    engines,
    scheduler,
)
from app.services.lifecycle_core import transition

# AI-assistant feature flag (``AI_ASSISTANT_ENABLED``): when a deployment sets
# it to 0 the assistant is removed from the experiment — the comprehension item
# below leaves the answer key, the post-task "AI assistant was useful" items
# leave the declared/required item sets, and the open-ended follow-up drops its
# ", or the AI assistant" clause. The canonical artifact lists stay intact so
# re-enabling the flag — and replaying flag-on sessions — keeps the original
# shape; these constants are the projection filters, not content edits.
ASSISTANT_COMPREHENSION_ITEM = "assistant_reads_team"


# Comprehension check (2026-10-10 revision, owner direction): the final pre-
# connecting screen — six True/False items the participant must answer
# (v2 2026-10-16: five originals + record_before_timer)
# correctly before the team connects. The server holds the key: each attempt
# posts ``answers`` and the committed event records the server-computed
# ``missed`` list (every miss is provable from the event stream); ``complete``
# is refused until a zero-miss comprehension event exists.
#
# ``COMPREHENSION_ITEMS`` is the canonical item list — the participant UI
# (``StageScreens.tsx`` ``COMPREHENSION_ITEMS``) mirrors the ids and texts and
# a contract test keeps them verbatim-identical. Responses persist as
# ``training_step`` events (``step="comprehension"``), never ``item_response``
# — they are a gate, not a survey instrument.
COMPREHENSION_INSTRUMENT_ID = "comprehension_check"
# v2 (2026-10-16): + record_before_timer (answer-entry task-model probe, TRUE).
COMPREHENSION_VERSION = 2
COMPREHENSION_ITEMS: tuple[dict[str, Any], ...] = (
    {
        "item_id": "partial_intel",
        "correct": True,
        "text": "Every team member receives some, but not all, available intelligence.",
    },
    {
        "item_id": "same_crates",
        "correct": False,
        "text": "Every team member is working on the exact same crates.",
    },
    {
        "item_id": "accurate_not_latest",
        "correct": True,
        "text": (
            "The intelligence in my case file is accurate, but may not be the latest available."
        ),
    },
    {
        "item_id": "teammates_may_have_mine",
        "correct": True,
        "text": "My teammates may have intelligence about my assigned crates.",
    },
    {
        "item_id": "assistant_reads_team",
        "correct": False,
        # The apostrophe is U+2019 — it matches the participant-facing copy
        # byte-for-byte (contract test enforces verbatim parity).
        "text": "The AI assistant has access to my teammates’ case files.",  # noqa: RUF001
    },
    # 2026-10-16 (v14, owner direction): answer-entry comprehension item — the
    # v13 zero-answer cohort never discovered where picks land; this item probes
    # exactly that task model. Unconditional (not flag-gated): every build
    # freezes answers at the deadline.
    {
        "item_id": "record_before_timer",
        "correct": True,
        "text": "I record each of my crate locations before the timer expires.",
    },
)
COMPREHENSION_KEY: dict[str, bool] = {i["item_id"]: i["correct"] for i in COMPREHENSION_ITEMS}


def comprehension_key(*, ai_assistant: bool, overlap: str = "independent") -> dict[str, bool]:
    """The session's comprehension answer key. ``AI_ASSISTANT_ENABLED=0`` drops
    the assistant item (the UI never renders it, so it must not gate); the
    ``same_crates`` truth value flips under the full-overlap task shape —
    derived from the bound manifest per session, never the deployment flag."""
    key = {
        q: want
        for q, want in COMPREHENSION_KEY.items()
        if ai_assistant or q != ASSISTANT_COMPREHENSION_ITEM
    }
    if overlap == "full":
        key["same_crates"] = True
    return key


# BA-106 (2026-10-12): the practice gate is bound-version aware.
# training.reference@v5 copy told the v10 cohort the typed ask was *required*;
# UX_Feedback_v9's v6 gates completion on the share alone. Sessions keep the
# gate their bound artifact promised — a stale preview link pinned to v5 still
# enforces the ask, matching the copy that cohort saw.
PRACTICE_ASK_REQUIRED_TRAINING = frozenset({"training.reference@v5"})


def practice_ask_required(bound: BoundManifest | None) -> bool:
    stored = bound.artifacts.get("instruments.training") if bound is not None else None
    return stored is not None and stored.key in PRACTICE_ASK_REQUIRED_TRAINING


def enter_scripted_connecting(ctx: TxContext, bound: BoundManifest) -> None:
    """Transition into ``scripted_connecting`` and plant its presentation job —
    shared by training completion (AI_QA_ENABLED=0) and the ``ai_qa`` Continue."""
    transition(ctx, bound, "scripted_connecting")
    # INV-0701 (spec §5.2): scripted connecting presentation. The participant and JT are
    # shown connected at stage entry; PM's join is a wall-clock job due at the manifest's
    # ``CONNECT_PM_JOIN_WALL_MS`` — Start stays disabled until that job commits (demo fidelity:
    # PM connects ~10.6 s in). The clock basis is ``wall``: connecting is presentation,
    # not active time.
    ctx.append_event("teammate_presented", actor="system", payload={"seat": "JT"})

    join_at = ctx.now + timedelta(milliseconds=scheduler.CONNECT_PM_JOIN_WALL_MS)
    ctx.schedule_job(
        "connecting.pm_join",
        kind="connecting",
        due_wall_at=join_at,
        claimable_at=join_at,
        payload={"seat": "PM"},
    )


def training_step(
    ctx: TxContext,
    bound: BoundManifest,
    p: Mapping[str, Any],
    *,
    ai_assistant: bool,
    ai_qa_enabled: bool,
) -> dict[str, Any]:
    """Walk the pre-task spine: instructions -> training on the first step; each step is a
    durable event; ``complete: true`` on the final step moves to scripted_connecting (or
    the optional ``ai_qa`` stage when AI_QA_ENABLED=1), where the connecting screen then
    runs (spec §5.2)."""
    if ctx.stage == "instructions":
        transition(ctx, bound, "training")
        ctx.append_event("training_started", actor="participant")
    missed: list[str] | None = None
    practice_card: dict[str, Any] | None = None
    if p.get("step") == "practice" and p.get("action") == "ask":
        # 2026-10-10 revision: the practice ask teaches the typed-ask mechanic —
        # the text must name a crate and read like an ask (the same classifier
        # the task chat uses); a canned teammate card comes back in the result
        # so the preview chat shows what a share looks like. Invalid text is
        # rejected so the client can show the "name a crate" hint.

        ask_text = str(p.get("text") or "").strip()
        ask_crates = engines.crates_referenced(ask_text, bound.manifest.task.n_crates)
        if not ask_text or not engines.wants_intel_ask(ask_text, ask_crates):
            raise RejectionError("practice_ask_invalid")
        crate = ask_crates[0]
        practice_card = {
            "record_id": "PRACTICE-ASK-REPLY",
            "crate": crate,
            "stamp": "Today, 9:20 AM",
            "body": f"Crate {crate} sighted near The Marina dock entrance.",
            # UX v7: a generic label — naming a seat in practice leaks that the
            # teammate initials are scripted fixtures.
            "shared_by": "Teammate",
        }
        payload = {**p, "crate": crate}
    elif p.get("step") == "comprehension":
        answers = p.get("answers")
        if not isinstance(answers, Mapping):
            raise RejectionError("malformed_comprehension")
        # Feature flag + task shape: a disabled assistant drops its item, and
        # full overlap flips the ``same_crates`` truth value (bound manifest,
        # not deployment flags — see ``comprehension_key``).
        key = comprehension_key(ai_assistant=ai_assistant, overlap=bound.manifest.task.overlap)
        missed = sorted(q for q, want in key.items() if answers.get(q) is not want)
        payload = {**p, "missed": missed}
    else:
        payload = dict(p)
    ctx.append_event("training_step", actor="participant", payload=payload)
    if p.get("complete") is True:
        if missed:
            # A completing submission that still has misses is accepted and
            # recorded (the attempt must be durable) but does not advance —
            # the client highlights `missed` and the participant retries.
            return {"stage": ctx.stage, "missed": missed}
        # Comprehension gate (mirrors the BA-95 practice-share gate): the
        # completing event itself may carry the zero-miss pass — the commit above
        # is visible to this select inside the transaction.
        checked = (
            ctx.conn.execute(
                select(func.count())
                .select_from(s.event)
                .where(
                    s.event.c.session_id == ctx.session_id,
                    s.event.c.type == "training_step",
                    s.event.c.payload["step"].astext == "comprehension",
                    s.event.c.payload["missed"].astext == "[]",
                )
            ).scalar_one()
            > 0
        )
        if not checked:
            raise RejectionError("comprehension_required")
        # BA-95: the participant may only advance once a *committed* share step exists —
        # the client shows the shared card only on an accepted receipt, and the server
        # is the backstop so a failed share command can never be skipped by completing.
        shared = (
            ctx.conn.execute(
                select(func.count())
                .select_from(s.event)
                .where(
                    s.event.c.session_id == ctx.session_id,
                    s.event.c.type == "training_step",
                    s.event.c.payload["step"].astext == "practice",
                    s.event.c.payload["action"].astext == "share",
                    s.event.c.payload["record_id"].astext.isnot(None),
                )
            ).scalar_one()
            > 0
        )
        if not shared:
            raise RejectionError("practice_share_required")
        # 2026-10-16 (v14, owner direction): the mock answer-entry step gates
        # completion like the share — the DV mechanic is rehearsed, not just
        # described. Unconditional like the share gate: no bound training
        # version ever told a cohort the pick was optional.
        picked = (
            ctx.conn.execute(
                select(func.count())
                .select_from(s.event)
                .where(
                    s.event.c.session_id == ctx.session_id,
                    s.event.c.type == "training_step",
                    s.event.c.payload["step"].astext == "practice",
                    s.event.c.payload["action"].astext == "pick_location",
                    s.event.c.payload["location"].astext.isnot(None),
                )
            ).scalar_one()
            > 0
        )
        if not picked:
            raise RejectionError("practice_pick_required")
        # BA-106: the typed practice ask gates completion only on profiles whose
        # bound training artifact promised it (training.reference@v5 — the v10
        # cohort). UX_Feedback_v9's v6+ builds gate on the share alone; asks stay
        # validated optional practice in every build.
        if practice_ask_required(bound):
            asked = (
                ctx.conn.execute(
                    select(func.count())
                    .select_from(s.event)
                    .where(
                        s.event.c.session_id == ctx.session_id,
                        s.event.c.type == "training_step",
                        s.event.c.payload["step"].astext == "practice",
                        s.event.c.payload["action"].astext == "ask",
                    )
                ).scalar_one()
                > 0
            )
            if not asked:
                raise RejectionError("practice_ask_required")
        if ai_qa_enabled:
            # AI_QA_ENABLED=1: the optional rules-Q&A page sits between the
            # comprehension check and connecting; its Continue re-enters here.
            transition(ctx, bound, "ai_qa")
            out: dict[str, Any] = {"stage": "ai_qa"}
        else:
            enter_scripted_connecting(ctx, bound)
            out = {"stage": "scripted_connecting"}
        if missed is not None:
            out["missed"] = missed
        return out
    result: dict[str, Any] = {"stage": ctx.stage}
    if practice_card is not None:
        result["card"] = practice_card
    if missed is not None:
        result["missed"] = missed
    return result
