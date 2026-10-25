"""Participant-visible projections of session state (audit H5).

The single definition of what a participant may see: the event allowlist and
event projection, the session snapshot, and the workbench's instrument, Q&A,
material and exposure projections. Shared by the participant API and the admin
replay (``adminviews``), so a researcher sees exactly the participant's view.
Before 2026-10-17 this lived in ``app/api/participant.py`` and adminviews
imported a private function from the route module.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from app.db import schema as s
from app.db.tx import (
    db_now,
)
from app.domain.manifests.artifacts import InstrumentBody
from app.domain.manifests.binding import BoundManifest
from app.domain.manifests.store import ContentStore
from app.services import (
    engines,
    lifecycle,
)


def session_snapshot(conn: Connection, session: Mapping[str, Any]) -> dict[str, Any]:
    # INV-0907: once the completion receipt exists, its deterministic handoff code is
    # served on every snapshot so the completion screen survives reloads.
    handoff = conn.execute(
        select(s.completion_receipt.c.handoff).where(
            s.completion_receipt.c.session_id == session["session_id"]
        )
    ).scalar_one_or_none()
    code = handoff.get("completion_code") if isinstance(handoff, dict) else None
    url = handoff.get("completion_url") if isinstance(handoff, dict) else None
    platform = handoff.get("platform") if isinstance(handoff, dict) else None
    initials = conn.execute(
        select(s.participant_display.c.initials).where(
            s.participant_display.c.session_id == session["session_id"]
        )
    ).scalar_one_or_none()
    return {
        "session_id": str(session["session_id"]),
        "stage": session["stage"],
        "revision": session["revision"],
        "mode": session["mode"],
        "manifest_id": session["manifest_id"],
        "study_key": session["study_key"],
        "timing_mode": session["timing_mode"],
        # The arm/condition assignment is deliberately absent — the participant
        # projection never exposes experimental truth (INV-0809, spec §10).
        "next_event_seq": session["next_event_seq"],
        "compatibility_key": session["compatibility_key"],
        "terminal_reason": session["terminal_reason"],
        "completion_code": code if isinstance(code, str) else None,
        "completion_url": url if isinstance(url, str) else None,
        "completion_platform": platform if isinstance(platform, str) else None,
        # Display-only initials (UX round 2): served to this session's UI, absent from
        # research exports (participant_display is in EXPORT_EXCLUDED_TABLES).
        "initials": initials if isinstance(initials, str) else None,
        "writer_held": session["writer_id"] is not None
        and session["writer_lease_until"] is not None,
        "stage_entered_at": (
            session["stage_entered_at"].isoformat() if session["stage_entered_at"] else None
        ),
    }


# INV-0703/spec §4.3: the participant event projection is an explicit allowlist. Scheduler
# internals (``generation_resolved``, ``task_schedule_committed``, ``voice_line_committed``,
# ``scripted_request_delivered``, ``structured_response_delivered``, ``acknowledgment_skipped``,
# ``schedule_swept``, ``warning_suppressed``) carry implementation labels, timing draws and
# scripted provenance — they must never reach the browser. ``None`` passes the whole payload;
# a frozenset keeps only the named keys.
PARTICIPANT_EVENTS: dict[str, frozenset[str] | None] = {
    "teammate_presented": frozenset({"seat"}),
    "task_clock_started": frozenset({"stage"}),
    "task_deadline": frozenset(),
    # The committed one-minute warning: message id + low-time flag only (the job key is
    # internal). The warning text itself arrives as the paired ``message_delivered``.
    "warning_committed": frozenset({"message_id", "low_time"}),
    "answers_frozen": frozenset({"crates"}),
    "resumed": None,
    "technical_stop": frozenset({"outcome"}),
    "message_delivered": frozenset(
        {"message_id", "actor", "channel", "body", "crate", "record_id", "request_id"}
    ),
    "message_accepted": frozenset({"message_id", "channel", "body", "crate"}),
    "greeting_prompt": frozenset({"options"}),
    "teammate_requested": None,
    "assistant_requested": frozenset({"crate", "body"}),
    "record_shared": None,
    "record_added_to_file": None,
    # checkin_open's assigned (scripted) seat values must not reach the browser before
    # the participant's own rating — checkin_reveal carries them at reveal time.
    "checkin_open": frozenset({"at_active_ms", "copy"}),
    "checkin_response": None,
    "checkin_reveal": None,
    # AI_QA_ENABLED: the participant already sees their own question; the
    # answer event carries the resolved answer text for the Q&A page.
    "qa_asked": frozenset({"seq", "question"}),
    "qa_answered": frozenset({"seq", "status"}),
    "stage_proceeded": None,
    "answer_updated": None,
    "confidence_recorded": None,
    "item_recorded": None,
    "instrument_complete": None,
    "consent_recorded": frozenset({"action", "text_version"}),
    "training_started": None,
    "training_step": None,
    "debrief_acknowledged": None,
    "data_use_recorded": None,
    "session_completed": None,
    "writer_takeover": None,
}


def project_event(r: Mapping[str, Any]) -> dict[str, Any] | None:
    """Apply the participant allowlist to one raw event row. ``None`` = internal only."""
    keys = PARTICIPANT_EVENTS.get(r["type"])
    if keys is None and r["type"] not in PARTICIPANT_EVENTS:
        return None  # internal bookkeeping never leaves the server
    payload = r["payload"] if keys is None else {k: r["payload"].get(k) for k in keys}
    return {
        "seq": r["seq"],
        "event_id": str(r["event_id"]),
        "stage": r["stage"],
        "actor": r["actor"],
        "type": r["type"],
        "revision": r["revision"],
        "active_ms": r["active_ms"],
        "server_time": r["server_time"].isoformat() if r["server_time"] else None,
        "payload": payload,
    }


def session_head(conn: Connection, session_id: uuid.UUID) -> tuple[int, str]:
    row = conn.execute(
        select(s.session.c.next_event_seq).where(s.session.c.session_id == session_id)
    ).scalar_one_or_none()
    return (0 if row is None else int(row), db_now(conn).isoformat())


# ----- INV-0803/0805: read-only workbench projection --------------------------------------------
#
# One GET hands the client everything the task surface is authorized to render: the case file
# (``worlds.participant_file`` allowlist), records delivered to chat but not yet filed, the
# participant's own crate assignment, the location picker options, saved answers and frozen
# finals, the enabled channel set for the current clock phase, and — in instrument stages —
# the declared item content. It never reads truth, roles, holdings, seeds, schedules or any
# seat-internal state, and it never triggers model work or releases.


def instrument_projection(
    conn: Connection,
    bound: BoundManifest,
    session: Mapping[str, Any],
    *,
    ai_assistant: bool = True,
    time_pressure: bool = True,
) -> dict[str, Any] | None:
    """The participant-facing projection of the current instrument stage: the bound
    instrument's visible items for this session's flag posture, already-answered items,
    confidence crates still pending, and response metadata. ``None`` outside
    instrument stages or when the slot is unbound."""
    stage = str(session["stage"])
    slot = lifecycle.INSTRUMENT_SLOTS.get(stage)
    if slot is None:
        return None
    stored = bound.artifacts.get(slot)
    if stored is None or not isinstance(stored.artifact.body, InstrumentBody):
        return None
    body = stored.artifact.body
    instrument_id = stored.key.rsplit("@v", 1)[0]
    answered_items: list[str] = list(
        conn.execute(
            select(s.item_response.c.item_id).where(
                s.item_response.c.session_id == session["session_id"],
                s.item_response.c.instrument_id == instrument_id,
            )
        ).scalars()
    )
    answered_crates: list[int] = []
    pending_crates: list[int] = []
    if stage == "confidence":
        answered_crates = [
            int(r["crate"])
            for r in conn.execute(
                select(func.distinct(s.confidence_response.c.crate).label("crate")).where(
                    s.confidence_response.c.session_id == session["session_id"]
                )
            ).mappings()
        ]
        # Confidence is asked per *answered* crate (frozen final first, else any revision).
        pending_crates = sorted(
            int(r["crate"])
            for r in conn.execute(
                select(func.distinct(s.answer_revision.c.crate).label("crate")).where(
                    s.answer_revision.c.session_id == session["session_id"]
                )
            ).mappings()
        )
    return {
        "stage": stage,
        "instrument_id": instrument_id,
        "instrument_version": int(stored.key.rsplit("@v", 1)[1]),
        "applies_to": body.applies_to,
        "response": {
            "type": body.response.type,
            "min": body.response.min,
            "max": body.response.max,
            "min_label": body.response.min_label,
            "max_label": body.response.max_label,
        },
        "items": [
            {
                "item_id": i.item_id,
                "text": lifecycle.instrument_item_text(
                    i.item_id, i.text, ai_assistant=ai_assistant
                ),
                "skippable": i.skippable,
                "options": None if i.options is None else list(i.options),
                "free_text": body.response.type == "free_text",
            }
            for i in body.items
            if lifecycle.instrument_item_visible(
                i.item_id, ai_assistant=ai_assistant, time_pressure=time_pressure
            )
        ],
        "seats": ["JT", "PM"] if body.applies_to == "per_teammate_seat" else [],
        "answered_items": answered_items,
        "answered_crates": answered_crates,
        "pending_crates": pending_crates,
    }


def qa_projection(conn: Connection, session: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """AI_QA_ENABLED: the session's rules-Q&A exchanges, oldest first — only
    served on the ``ai_qa`` stage (its own presence is the flag signal)."""
    if str(session["stage"]) != "ai_qa":
        return None
    rows = conn.execute(
        select(
            s.qa_exchange.c.seq,
            s.qa_exchange.c.question,
            s.qa_exchange.c.answer,
            s.qa_exchange.c.status,
        )
        .where(s.qa_exchange.c.session_id == session["session_id"])
        .order_by(s.qa_exchange.c.seq)
    ).mappings()
    return [
        {
            "seq": int(r["seq"]),
            "question": r["question"],
            "answer": r["answer"],
            "status": r["status"],
        }
        for r in rows
    ]


# ----- INV-0901/0904: bound material + exposure surfaces --------------------------------------
#
# Participant-visible copy is bound through the manifest, never hardcoded. ``material``
# serves the bound ``material_reference`` artifact (key, version, status and body) for the
# consent/training/debrief stages; the artifacts' bodies currently name where the
# reference copy lives — final frozen wording is a D06 publication step, and the
# ``status`` flag makes the interim state visible to reviewers.
#
# ``exposure`` serves only the PE surfaces the current stage renders, resolved from the
# session's *bound* arm copy without exposing the arm label or the artifact key (which
# embeds eval/control wording). Empty control-arm surfaces are omitted entirely, so the
# client has nothing to fabricate.


_MATERIAL_SLOTS = {
    "consent": "instruments.consent",
    "training": "instruments.training",
    "debrief": "instruments.debrief",
}

_EXPOSURE_STAGE_SURFACES = {
    "instructions": ("intro", "callout"),
    "solo_review": ("strip",),
    "task": ("strip",),
    "checkin_collect": ("strip",),
    "checkin_wait": ("strip",),
    "checkin_reveal": ("strip",),
    "peer_ratings": ("peer_intro",),
}


def material_projection(
    bound: BoundManifest,
    session: Mapping[str, Any],
    *,
    store: ContentStore | None = None,
    bonus_payment_enabled: bool = True,
) -> dict[str, Any] | None:
    slot = _MATERIAL_SLOTS.get(str(session["stage"]))
    if slot is None:
        return None
    if slot == "instruments.consent":
        # BONUS_PAYMENT_ENABLED=0 serves the nobonus consent variant — the client
        # echoes its key as ``text_version``, so the recorded consent names the
        # copy the participant actually saw. Missing alternate = consent stage
        # serves nothing rather than bonus-on copy under a flag-off deploy.
        stored = lifecycle.effective_consent_artifact(
            bound, store, bonus_payment_enabled=bonus_payment_enabled
        )
    else:
        stored = bound.artifacts.get(slot)
    if stored is None:
        return None
    artifact_id, version = stored.key.rsplit("@v", 1)
    return {
        "slot": slot,
        "artifact_key": stored.key,
        "artifact_id": artifact_id,
        "artifact_version": int(version),
        "status": stored.artifact.status,
        "body": stored.artifact.body.model_dump(mode="json"),
    }


def exposure_projection(bound: BoundManifest, session: Mapping[str, Any]) -> dict[str, str] | None:
    surfaces = _EXPOSURE_STAGE_SURFACES.get(str(session["stage"]))
    if not surfaces:
        return None
    texts = engines.pe_surface_texts(bound, session["arm"])
    out = {name: texts[name] for name in surfaces if texts.get(name)}
    return out or None
