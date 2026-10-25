"""Admin read models (INV-1303/1304/1306/1308, spec §11).

Every view here is a *query* over committed rows — the admin surface never claims a check
that was not run: sections that cannot be computed (e.g. coding tables the runtime role
cannot read, or a pipeline that has not produced runs) report ``status: "unavailable"``
with the reason instead of a blanket "complete".

The participant replay reuses the *same* event allowlist as the participant API
(``app.services.projection.project_event``) so a researcher always sees exactly what the
participant could see — the admin truth side is a separate ``restricted`` block gated to
the researcher role, never a substitution.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError

from app.db import schema as s
from app.db.export import export_session
from app.db.tx import RejectionError, db_now
from app.domain.manifests.artifacts import InstrumentBody
from app.domain.manifests.store import ContentError, ContentStore
from app.export.aggregates import fidelity_rates, group_key, incident_summary
from app.export.restricted import FREE_TEXT_COLUMNS
from app.flags import DEFAULT_FLAGS, FeatureFlags
from app.measurement.fidelity import fidelity_monitor, teammate_fidelity
from app.services import activetime, lifecycle, session_binding, worlds
from app.services.projection import project_event
from app.timing.policy import time_pressure_for_arm

MAX_LIST = 500
MAX_SECTION_ROWS = 1000
MAX_REPLAY_EVENTS = 2000


def jsonable(value: Any) -> Any:
    """Recursively convert DB values (uuid/datetime/Decimal/bytes) for JSON output."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    return value


def _rows(
    conn: Connection, table: Any, session_id: uuid.UUID, *, limit: int = MAX_SECTION_ROWS
) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(select(table).where(table.c.session_id == session_id).limit(limit))
        .mappings()
        .all()
    ]


# ----- session monitor list ----------------------------------------------------------------------


def list_sessions(
    conn: Connection,
    *,
    batch_id: str | None = None,
    batch_group_id: str | None = None,
    mode: str | None = None,
    stage: str | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    """Session monitor rows (spec §11: stage, remaining active time, exposure,
    saved-answer count, reconnects, errors, terminal status). Bounded; ``q`` matches a
    research-PID or session-id prefix."""
    limit = max(1, min(limit, MAX_LIST))
    sess, part = s.session, s.participant
    base = (
        select(
            sess.c.session_id,
            sess.c.stage,
            sess.c.revision,
            sess.c.mode,
            sess.c.timing_mode,
            sess.c.arm,
            sess.c.batch_id,
            sess.c.manifest_id,
            sess.c.study_key,
            sess.c.compatibility_key,
            sess.c.terminal_reason,
            sess.c.created_at,
            sess.c.stage_entered_at,
            part.c.research_pid,
        )
        .select_from(sess.join(part, sess.c.participant_id == part.c.participant_id))
        .where(
            # A tombstoned session is deleted by contract (INV-1307); the surviving
            # append-only rows are the work order for the migrated mechanism, not live
            # data — never list them on the monitor board.
            ~select(s.admin_audit.c.session_id)
            .where(
                s.admin_audit.c.session_id == sess.c.session_id,
                s.admin_audit.c.action == "deletion_tombstone",
            )
            .exists()
        )
        .order_by(sess.c.created_at.desc())
        .limit(limit)
        .offset(max(0, offset))
    )
    if batch_id:
        base = base.where(sess.c.batch_id == batch_id)
    if batch_group_id:
        # Sessions carry the member batch id; a group filter expands to its
        # members (also covers rows whose session.batch_group_id is unset).
        members = select(s.batch_group_member.c.batch_id).where(
            s.batch_group_member.c.group_id == batch_group_id
        )
        base = base.where(sess.c.batch_id.in_(members))
    if mode:
        base = base.where(sess.c.mode == mode)
    if stage:
        base = base.where(sess.c.stage == stage)
    if q:
        probe = q.strip()
        base = base.where(
            or_(
                part.c.research_pid.ilike(f"{probe}%"),
                cast(sess.c.session_id, Text).ilike(f"{probe}%"),
            )
        )
    rows = [dict(r) for r in conn.execute(base).mappings().all()]
    if not rows:
        return {"sessions": [], "limit": limit, "offset": offset}
    sids = [r["session_id"] for r in rows]
    now = db_now(conn)

    def count(table: Any, *extra: Any) -> dict[uuid.UUID, int]:
        counts: dict[Any, Any] = dict(
            conn.execute(
                select(table.c.session_id, func.count())
                .where(table.c.session_id.in_(sids), *extra)
                .group_by(table.c.session_id)
            ).all()
        )
        return {k: int(v) for k, v in counts.items()}

    answers = count(s.answer_revision)
    reconnects = count(
        s.event, s.event.c.type.in_(("session_resumed", "writer_takeover", "resumed"))
    )
    rejections = count(s.command_receipt, s.command_receipt.c.outcome == "rejected")
    exposures = count(s.manipulation_exposure, s.manipulation_exposure.c.committed_seq.is_not(None))
    open_episodes = count(s.request_episode, s.request_episode.c.closed_at.is_(None))
    pending_jobs = count(s.scheduled_job, s.scheduled_job.c.status.in_(("pending", "claimed")))
    receipts: dict[Any, Any] = dict(
        conn.execute(
            select(s.completion_receipt.c.session_id, s.completion_receipt.c.outcome).where(
                s.completion_receipt.c.session_id.in_(sids)
            )
        ).all()
    )
    out = []
    for r in rows:
        sid = r["session_id"]
        out.append(
            {
                "session_id": str(sid),
                "research_pid": r["research_pid"],
                "batch_id": r["batch_id"],
                "study_key": r["study_key"],
                "manifest_id": r["manifest_id"],
                "mode": r["mode"],
                "timing_mode": r["timing_mode"],
                "arm": r["arm"],
                "compatibility_key": r["compatibility_key"],
                "stage": r["stage"],
                "revision": r["revision"],
                "terminal_reason": r["terminal_reason"],
                "created_at": r["created_at"],
                "stage_entered_at": r["stage_entered_at"],
                "active_ms": activetime.active_ms(conn, sid, now, default=0),
                "n_answered_crates": int(answers.get(sid, 0)),
                "n_reconnects": int(reconnects.get(sid, 0)),
                "n_rejected_commands": int(rejections.get(sid, 0)),
                "n_committed_exposures": int(exposures.get(sid, 0)),
                "n_open_episodes": int(open_episodes.get(sid, 0)),
                "n_pending_jobs": int(pending_jobs.get(sid, 0)),
                "completion_outcome": receipts.get(sid),
            }
        )
    return {"sessions": jsonable(out), "limit": limit, "offset": offset}


def my_previews(conn: Connection, *, actor: str) -> dict[str, Any]:
    """The caller's own issued preview links with live status (lab self-service).

    Issuance is recorded as ``admin_audit`` rows (``action='preview_issue'``) whose
    payload carries the descriptor nonce + token; the session joins on the preview
    participant identity ``preview-<nonce>``. Rows from before the nonce was recorded
    carry no token and are skipped — they cannot be relisted without one anyway."""
    from app.services import retention

    audit_rows = conn.execute(
        select(s.admin_audit.c.payload, s.admin_audit.c.occurred_at)
        .where(s.admin_audit.c.action == "preview_issue")
        .where(s.admin_audit.c.actor == actor)
        .order_by(s.admin_audit.c.occurred_at.desc())
        .limit(MAX_LIST)
    ).mappings()
    links: list[dict[str, Any]] = []
    for row in audit_rows:
        payload = row["payload"] or {}
        nonce = payload.get("nonce")
        token = payload.get("token")
        if not isinstance(nonce, str) or not isinstance(token, str):
            continue
        sess = (
            conn.execute(
                select(
                    s.session.c.session_id,
                    s.session.c.stage,
                    s.session.c.terminal_reason,
                    s.session.c.created_at,
                    s.session.c.stage_entered_at,
                )
                .select_from(
                    s.participant.join(
                        s.session, s.participant.c.participant_id == s.session.c.participant_id
                    )
                )
                .where(s.participant.c.research_pid == f"preview-{nonce}")
            )
            .mappings()
            .first()
        )
        tombstoned = sess is not None and bool(
            conn.execute(
                select(func.count())
                .select_from(s.admin_audit)
                .where(
                    s.admin_audit.c.session_id == sess["session_id"],
                    s.admin_audit.c.action == "deletion_tombstone",
                )
            ).scalar_one()
        )
        # BA-39: a tombstone does not mean every row is gone — surface how many rows
        # still await the privileged purge so the status label is truthful.
        purge_pending = (
            retention.pending_purge_rows(conn, sess["session_id"])
            if tombstoned and sess is not None
            else None
        )
        links.append(
            {
                "manifest_id": payload.get("manifest_id"),
                "arm": payload.get("arm"),
                "nonce": nonce,
                "entry_url": payload.get("entry_url") or f"/investigators/?pt={token}",
                "issued_at": jsonable(row["occurred_at"]),
                "expires_at": payload.get("expires_at"),
                "session": (
                    {
                        **{k: jsonable(v) for k, v in sess.items()},
                        "deleted": tombstoned,
                        "purge_pending": purge_pending,
                    }
                    if sess is not None
                    else None
                ),
            }
        )
    return {"links": links}


def _get_session(conn: Connection, session_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        conn.execute(
            select(s.session, s.participant.c.research_pid)
            .select_from(
                s.session.join(
                    s.participant, s.session.c.participant_id == s.participant.c.participant_id
                )
            )
            .where(s.session.c.session_id == session_id)
        )
        .mappings()
        .first()
    )
    return None if row is None else dict(row)


def session_detail(conn: Connection, session_id: uuid.UUID) -> dict[str, Any] | None:
    """Admin session detail: the session row, deletion/purge state, exposures,
    request episodes, incidents, entitlements, the latest data-use choice, the
    completion receipt and tombstones. ``None`` when the session does not exist."""
    from app.services import retention

    row = _get_session(conn, session_id)
    if row is None:
        return None
    sid = session_id
    exposures = _rows(conn, s.manipulation_exposure, sid, limit=200)
    episodes = _rows(conn, s.request_episode, sid, limit=200)
    incidents = _rows(conn, s.incident_interval, sid, limit=100)
    entitlements = _rows(conn, s.payment_entitlement, sid, limit=20)
    data_use = (
        conn.execute(
            select(s.data_use_choice)
            .where(s.data_use_choice.c.session_id == sid)
            .order_by(s.data_use_choice.c.choice_seq.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    receipt = (
        conn.execute(select(s.completion_receipt).where(s.completion_receipt.c.session_id == sid))
        .mappings()
        .first()
    )
    tombstones = [
        dict(r)
        for r in conn.execute(
            select(
                s.admin_audit.c.audit_id,
                s.admin_audit.c.actor,
                s.admin_audit.c.action,
                s.admin_audit.c.occurred_at,
                s.admin_audit.c.payload,
            )
            .where(
                s.admin_audit.c.session_id == sid,
                s.admin_audit.c.action.in_(("deletion_tombstone", "deletion_reapplied")),
            )
            .order_by(s.admin_audit.c.occurred_at)
        )
        .mappings()
        .all()
    ]
    detail = dict(row)
    detail.pop("binding", None)  # large; available via replay restricted view
    # BA-39: a tombstoned session must be labeled as such even on direct lookup, with
    # the count of rows still awaiting privileged purge — never presented as active.
    tombstoned = any(t["action"] == "deletion_tombstone" for t in tombstones)
    out: dict[str, Any] = jsonable(
        {
            "session": {
                **detail,
                "deleted": tombstoned,
                "purge_pending": (retention.pending_purge_rows(conn, sid) if tombstoned else None),
            },
            "exposures": exposures,
            "episodes": episodes,
            "incidents": incidents,
            "entitlements": [
                {
                    "kind": e["kind"],
                    "cents": e["cents"],
                    "status": e["status"],
                    "decided_by": e["decided_by"],
                    "decided_at": e["decided_at"],
                    "paid_at": e["paid_at"],
                    # platform linkage is never joined here — compensation queue only
                }
                for e in entitlements
            ],
            "data_use_choice": dict(data_use) if data_use else None,
            "completion": dict(receipt) if receipt else None,
            "tombstones": tombstones,
        }
    )
    return out


# ----- replay (INV-1303) --------------------------------------------------------------------------


def _participant_pages(
    conn: Connection,
    store: ContentStore | None,
    session: Mapping[str, Any],
    export: Mapping[str, Any],
    participant_events: Sequence[Mapping[str, Any]],
    *,
    flags: FeatureFlags = DEFAULT_FLAGS,
) -> dict[str, Any]:
    """Ordered participant-facing page content for the admin "participant view".

    Everything returned is participant-visible data: the bound consent/training/debrief
    copy, instrument items with *this* participant's recorded responses, the check-in
    rating and the teammate values the reveal showed, frozen answers, the data-use
    choice and the completion handoff. Truth, holdings, model calls, scheduler internals
    and timing context are deliberately absent — the view replays what the
    participant's browser rendered, not what a researcher sees.

    ``ai_assistant_enabled`` is the deployment flag; a session that actually
    touched the assistant (an ask, an assistant-channel message, or a
    comprehension attempt naming the assistant item) counts as assistant-on even
    under a since-disabled deployment, so its replay keeps the real experience.
    """
    transitions = sorted(export.get("stage_transition", []), key=lambda r: int(r["revision"]))
    stage_order = [str(transitions[0]["from_stage"])] if transitions else []
    stage_order += [str(t["to_stage"]) for t in transitions]
    entered_at = {str(t["to_stage"]): t["occurred_at"] for t in transitions}

    bound = None
    if store is not None:
        try:
            bound = session_binding.bound_for_session(conn, store, session)
        except (RejectionError, ContentError, KeyError):
            # A rebuilt binding must never break the replay — pin drift surfaces
            # elsewhere; pages fall back to "copy unavailable" per stage.
            bound = None

    # Per-session assistant evidence: a session that asked the assistant, got an
    # assistant-channel message, or answered the assistant comprehension item was
    # run with the feature on regardless of the deployment flag's current value.
    def _assistant_evidence(ev: Mapping[str, Any]) -> bool:
        payload = ev.get("payload") or {}
        if ev.get("type") == "assistant_requested" or payload.get("channel") == "assistant":
            return True
        return (
            ev.get("type") == "training_step"
            and isinstance(payload.get("answers"), Mapping)
            and lifecycle.ASSISTANT_COMPREHENSION_ITEM in payload["answers"]
        )

    # A session whose committed comprehension attempt lacks the assistant item
    # is provably flag-off even under a flag-on deployment — flag-on servers
    # record every rendered answer key. Sessions too early to carry evidence
    # follow the deployment flag.
    comp_attempted = any(
        ev.get("type") == "training_step"
        and (ev.get("payload") or {}).get("step") == "comprehension"
        for ev in participant_events
    )
    ai_session = bool(
        any(_assistant_evidence(ev) for ev in participant_events)
        or (flags.ai_assistant and not comp_attempted)
    )

    # TIME_PRESSURE_ENABLED mirror of ``ai_session``: check-in stage
    # transitions or a committed pressure-item row prove the session ran
    # flag-on regardless of the deployment flag's current value; advancing
    # PAST post_questionnaire without either is provably flag-off — flag-on
    # servers require every served item answered or skipped before the stage
    # completes. Sessions too early to tell follow the deployment flag.
    pressure_evidence = any(str(st).startswith("checkin_") for st in stage_order) or any(
        str(r["item_id"]).split(".")[0] in lifecycle.PRESSURE_INSTRUMENT_ITEMS
        for r in export.get("item_response", [])
    )
    post_questionnaire_done = bool(
        {"open_ended", "debrief", "data_use_choice", "completion"} & set(stage_order)
    )
    pressure_session = bool(
        pressure_evidence
        or (
            time_pressure_for_arm(session.get("arm"), enabled=flags.time_pressure)
            and not post_questionnaire_done
        )
    )

    consent_rows = sorted(export.get("consent_event", []), key=lambda r: str(r["occurred_at"]))
    recorded_text_version = str(consent_rows[-1]["text_version"]) if consent_rows else None

    # BONUS_PAYMENT_ENABLED mirror of ``pressure_session``: the recorded
    # consent text_version is definitive (the nobonus artifact is only ever
    # served flag-off), the entitlement's policy artifact corroborates for
    # terminal rows, and ``session_opened.feature_flags`` covers sessions
    # that never reached consent. Sessions too early to tell — and any
    # predating flag recording — follow the deployment flag.
    recorded_policy_id = conn.execute(
        select(s.payment_entitlement.c.policy_artifact_id)
        .where(s.payment_entitlement.c.session_id == session["session_id"])
        .order_by(s.payment_entitlement.c.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    opened_flags: Mapping[str, Any] = {}
    for ev in export.get("event", []):
        if ev.get("type") == "session_opened":
            payload = ev.get("payload")
            if isinstance(payload, Mapping) and isinstance(payload.get("feature_flags"), Mapping):
                opened_flags = payload["feature_flags"]
            break
    if recorded_text_version is not None:
        bonus_session = recorded_text_version != lifecycle.NO_BONUS_CONSENT_ARTIFACT_KEY
    elif recorded_policy_id is not None:
        bonus_session = (
            str(recorded_policy_id) != lifecycle.NO_BONUS_PAYMENT_ARTIFACT_KEY.rsplit("@v", 1)[0]
        )
    elif "bonus_payment_enabled" in opened_flags:
        bonus_session = bool(opened_flags["bonus_payment_enabled"])
    else:
        bonus_session = flags.bonus_payment

    def material(slot: str) -> dict[str, Any] | None:
        stored = bound.artifacts.get(slot) if bound is not None else None
        if (
            slot == "instruments.consent"
            and recorded_text_version is not None
            and store is not None
        ):
            # The replay must show the artifact the participant actually signed —
            # flag-off sessions recorded the nobonus key even though the manifest
            # stays pinned to the bonus-on consent.
            stored = store.artifacts.get(recorded_text_version, stored)
        if stored is None:
            return None
        return {
            "artifact_key": stored.key,
            "status": stored.artifact.status,
            "body": stored.artifact.body.model_dump(mode="json"),
        }

    # Instruments bound to instrument stages, with this participant's responses.
    instruments: dict[str, Any] = {}
    for stage_name, slot in lifecycle.INSTRUMENT_SLOTS.items():
        stored = bound.artifacts.get(slot) if bound is not None else None
        if stored is None or not isinstance(stored.artifact.body, InstrumentBody):
            continue
        body = stored.artifact.body
        instrument_id = stored.key.rsplit("@v", 1)[0]
        # No-assistant sessions never saw the assistant items — drop them from
        # the replayed item list, but only when no response was recorded: a
        # flag-on-era row keeps its question so the answer stays interpretable.
        responded_ids = {
            str(r["item_id"])
            for r in export.get("item_response", [])
            if r["instrument_id"] == instrument_id
        }
        inst: dict[str, Any] = {
            "stage": stage_name,
            "artifact_key": stored.key,
            "instrument_id": instrument_id,
            "applies_to": body.applies_to,
            "response": body.response.model_dump(mode="json"),
            "items": [
                {
                    "item_id": i.item_id,
                    "text": lifecycle.instrument_item_text(
                        i.item_id, i.text, ai_assistant=ai_session
                    ),
                    "skippable": i.skippable,
                    "options": None if i.options is None else list(i.options),
                }
                for i in body.items
                if lifecycle.instrument_item_visible(
                    i.item_id, ai_assistant=ai_session, time_pressure=pressure_session
                )
                or i.item_id in responded_ids
            ],
            "seats": ["JT", "PM"] if body.applies_to == "per_teammate_seat" else [],
        }
        if stage_name == "confidence":
            latest: dict[int, tuple[int, Mapping[str, Any]]] = {}
            for r in export.get("confidence_response", []):
                crate, rev = int(r["crate"]), int(r["revision"])
                if crate not in latest or rev > latest[crate][0]:
                    latest[crate] = (rev, r)
            inst["pending_crates"] = sorted(
                {int(r["crate"]) for r in export.get("answer_revision", [])}
            )
            inst["crate_responses"] = {
                str(c): {"value": r["value"], "status": r["status"]}
                for c, (_rev, r) in latest.items()
            }
        else:
            inst["responses"] = {
                str(r["item_id"]): {
                    "value": (r.get("response") or {}).get("value"),
                    "status": r["status"],
                    "missing_reason": r.get("missing_reason"),
                }
                for r in export.get("item_response", [])
                if r["instrument_id"] == instrument_id
            }
        instruments[stage_name] = inst

    # UX round 8 legacy: v10-era sessions stored the open-ended follow-ups
    # under code-defined ``open_ended_followup`` (no bound artifact). Post-
    # BA-105 sessions resolve the same stage through the bound
    # ``instruments.open_ended`` slot in the loop above — project the legacy
    # shape only when no slot is bound.
    if bound is None or bound.artifacts.get("instruments.open_ended") is None:
        instruments["open_ended"] = {
            "stage": "open_ended",
            "artifact_key": None,
            "instrument_id": lifecycle.OPEN_ENDED_INSTRUMENT_ID,
            "applies_to": "session",
            "response": {
                "type": "free_text",
                "min": 0,
                "max": lifecycle.LEGACY_OPEN_ENDED_MAX_CHARS,
                "min_label": "",
                "max_label": "",
            },
            "items": [
                {
                    **i,
                    "text": lifecycle.instrument_item_text(
                        str(i["item_id"]), str(i["text"]), ai_assistant=ai_session
                    ),
                }
                for i in lifecycle.LEGACY_OPEN_ENDED_ITEMS
            ],
            "seats": [],
            "responses": {
                str(r["item_id"]): {
                    "value": (r.get("response") or {}).get("value"),
                    "status": r["status"],
                    "missing_reason": r.get("missing_reason"),
                }
                for r in export.get("item_response", [])
                if r["instrument_id"] == lifecycle.OPEN_ENDED_INSTRUMENT_ID
            },
        }

    consent = (
        {
            "action": consent_rows[-1]["action"],
            "text_version": consent_rows[-1]["text_version"],
            "occurred_at": consent_rows[-1]["occurred_at"],
        }
        if consent_rows
        else None
    )

    # The check-in overlay copy is participant-visible and travels on the
    # checkin_open event; the rating + revealed teammate values sit on the
    # pressure_checkin exposure row.
    checkin_copy = None
    for ev in export.get("event", []):
        if ev.get("type") == "checkin_open":
            checkin_copy = (ev.get("payload") or {}).get("copy")
    checkin = None
    for ex in export.get("manipulation_exposure", []):
        if ex["kind"] == "pressure_checkin":
            checkin = {
                "copy": checkin_copy,
                "own_rating": ex["own_rating"],
                "assigned_values": ex["assigned_values"],
                "status": ex["status"],
            }

    frozen = {str(r["crate"]): r["location_id"] for r in export.get("answer_final", [])}

    data_use_rows = sorted(export.get("data_use_choice", []), key=lambda r: int(r["choice_seq"]))
    receipt = (export.get("completion_receipt") or [None])[0]

    # The participant's workbench, from participant-visible rows only: their case
    # file is ``file_membership``; records they saw in chat are the record_ids on
    # participant-projected message/share events; the crate assignment is their own.
    visible_rids = {str(m["record_id"]) for m in export.get("file_membership", [])}
    for ev in participant_events:
        rid = (ev.get("payload") or {}).get("record_id")
        if rid:
            visible_rids.add(str(rid))
    records = {
        str(r["record_id"]): {
            "stamp": r["stamp"],
            "body": r["body"],
            "crate": r["crate"],
        }
        for r in export.get("record", [])
        if str(r["record_id"]) in visible_rids
    }
    world = (export.get("world_snapshot") or [{}])[0]

    pages: dict[str, Any] = jsonable(
        {
            "stage_order": stage_order,
            "entered_at": entered_at,
            "initials": conn.execute(
                select(s.participant_display.c.initials).where(
                    s.participant_display.c.session_id == session["session_id"]
                )
            ).scalar_one_or_none(),
            "materials": {
                stage_name: material(slot)
                for stage_name, slot in (
                    ("consent", "instruments.consent"),
                    ("training", "instruments.training"),
                    ("debrief", "instruments.debrief"),
                )
            },
            "instruments": instruments,
            "consent": consent,
            "workbench": {
                "assigned_crates": sorted(
                    int(n) for n in (world.get("content") or {}).get("assigned") or []
                ),
                "initial_file": [
                    str(m["record_id"])
                    for m in export.get("file_membership", [])
                    if m["source"] == "initial"
                ],
                "records": records,
            },
            "checkin": checkin,
            # AI_QA_ENABLED: the rules-Q&A exchanges the participant had on the
            # ai_qa page — question text, the committed answer, and how it was
            # produced (model vs deterministic fallback).
            "qa_exchanges": [
                {
                    "seq": int(r["seq"]),
                    "question": r["question"],
                    "answer": r["answer"],
                    "status": r["status"],
                    "model": r.get("model"),
                    "latency_ms": r.get("latency_ms"),
                    "generation_audit": r.get("generation_audit") or {},
                }
                for r in sorted(export.get("qa_exchange", []), key=lambda r: int(r["seq"]))
            ],
            "frozen_answers": frozen,
            "data_use": {"choice": data_use_rows[-1]["choice"]} if data_use_rows else None,
            "completion": (
                None
                if receipt is None
                else {
                    "outcome": receipt["outcome"],
                    "code": (receipt.get("handoff") or {}).get("completion_code"),
                    "url": (receipt.get("handoff") or {}).get("completion_url"),
                    "platform": (receipt.get("handoff") or {}).get("platform"),
                }
            ),
            "terminal": (
                {
                    "stage": session["stage"],
                    "reason": session.get("terminal_reason"),
                }
                if str(session["stage"]) in s.TERMINAL_STAGES
                else None
            ),
            "budget_s": bound.manifest.timing.active_budget_s if bound is not None else None,
            "structured_requests": (
                bound.manifest.interaction_mode == "structured_and_nl"
                if bound is not None
                else None
            ),
            "solo_review_seconds": (
                next(
                    (
                        ph.until_active_s - ph.from_active_s
                        for ph in bound.manifest.channels.phases
                        if ph.name == "solo_review"
                    ),
                    None,
                )
                if bound is not None
                else None
            ),
            "ai_assistant": ai_session,
            # Audit mirror of ``ai_assistant``: whether this session ran the
            # time-pressure manipulation (check-in, pressure remarks, warning,
            # low-time styling, pressure follow-ups) — session-evidence first,
            # deployment flag only for sessions too early to tell.
            "time_pressure": pressure_session,
            # Audit mirror of ``pressure_session``: whether this session ran
            # under the bonus offer — consent text_version, then entitlement
            # policy, then session_opened flags; the deployment flag is the
            # last resort for sessions too early to tell.
            "bonus_payment": bonus_session,
            # Audit F02: the session's *persisted* merchandise labels — replay
            # renders "Crate 9 — sneakers" from what the world actually held,
            # not a static map that can drift from historical snapshots.
            "crate_contents": {
                str(c["num"]): c["contents"]
                for c in (world.get("content") or {}).get("crates") or []
            },
            # 2026-10-15 crate-condition experiment (audit S01): replay copy
            # derives crate count/overlap wording from the persisted
            # world_params (the administered shape), then the bound manifest —
            # never the deployment flags.
            "task_shape": (
                worlds.task_shape_for_session(world.get("content") or {}, bound)
                if bound is not None or world.get("content")
                else None
            ),
        }
    )
    return pages


def replay(
    engine: Engine,
    session_id: uuid.UUID,
    *,
    include_restricted: bool,
    event_limit: int = MAX_REPLAY_EVENTS,
    store: ContentStore | None = None,
    flags: FeatureFlags = DEFAULT_FLAGS,
) -> dict[str, Any] | None:
    """Participant projection beside the restricted truth/context view for one session.

    ``participant.events`` are filtered through ``app.services.projection.project_event`` —
    the same allowlist the participant API applies — so the researcher sees exactly the
    participant-visible record, never the admin truth projection. ``restricted`` (world
    truth, holdings, per-call context, scheduled-vs-actual timing, claims, raw internal
    events) is included only when the caller's role permits.
    """
    # Imported lazily: participant.py is the frozen participant surface; reusing its
    # projection keeps admin replay bit-identical to what the browser received.

    with engine.connect() as conn:
        row = _get_session(conn, session_id)
        if row is None:
            return None
    export = export_session(engine, session_id)
    events = sorted(export.get("event", []), key=lambda r: int(r["seq"]))
    limited = events[: max(1, min(event_limit, MAX_REPLAY_EVENTS))]
    participant_events = [p for p in (project_event(r) for r in limited) if p is not None]
    internal_events = [jsonable(dict(r)) for r in limited if project_event(r) is None]
    out: dict[str, Any] = {
        "session": jsonable({k: v for k, v in row.items() if k != "binding"}),
        "participant": {
            "events": participant_events,
            "event_count": len(participant_events),
            "truncated": len(events) > len(limited),
            "pages": None,
        },
        "restricted_status": "ok" if include_restricted else "requires_researcher_role",
        "restricted": None,
    }
    with engine.connect() as conn:
        out["participant"]["pages"] = _participant_pages(
            conn,
            store,
            row,
            export,
            participant_events,
            flags=flags,
        )
    if include_restricted:
        internal_only = [r for r in internal_events]
        out["restricted"] = {
            "world": jsonable(export.get("world_snapshot", [])),
            "records": jsonable(export.get("record", [])),
            "seat_holdings": jsonable(export.get("seat_holding", [])),
            "file_membership": jsonable(export.get("file_membership", [])),
            "knowledge_snapshots": jsonable(export.get("knowledge_snapshot", [])),
            "model_calls": jsonable(export.get("model_call", [])),
            "displayed_outputs": jsonable(export.get("displayed_output", [])),
            "claims": jsonable(export.get("claim", [])),
            "claim_sources": jsonable(export.get("claim_source", [])),
            "agent_state": jsonable(export.get("agent_state", [])),
            "scheduled_jobs": jsonable(export.get("scheduled_job", [])),
            "clock_intervals": jsonable(export.get("clock_interval", [])),
            "delivery_receipts": jsonable(export.get("delivery_receipt", [])),
            # The command ledger: accepted AND rejected commands with expected/committed
            # revisions — the only record of stale-revision retries, which produce no
            # events at all and are otherwise invisible on the timeline.
            "command_receipts": jsonable(export.get("command_receipt", [])),
            "request_episodes": jsonable(export.get("request_episode", [])),
            "manipulation_exposures": jsonable(export.get("manipulation_exposure", [])),
            "incidents": jsonable(export.get("incident_interval", [])),
            "draw_log": jsonable(export.get("draw_log", [])),
            # Internal (non-participant-visible) events: scheduler internals, timing draws.
            "internal_events": internal_only,
            "binding": jsonable(row.get("binding")),
        }
    return out


# ----- fidelity / operations monitor (INV-1304) ----------------------------------------------


def monitor(conn: Connection) -> dict[str, Any]:
    """Dashboard sections. Each section carries its own status — an unrun or unreadable
    pipeline reports ``unavailable``/``not_run``, never a false "complete" (spec §11.1)."""
    checks: list[dict[str, Any]] = []

    def section(name: str, fn: Any) -> dict[str, Any]:
        try:
            data = fn()
        except DBAPIError as exc:
            checks.append({"name": name, "status": "unavailable", "note": type(exc).__name__})
            return {"status": "unavailable", "note": f"{type(exc).__name__}"}
        checks.append({"name": name, "status": "ok"})
        return {"status": "ok", "data": data}

    sessions = _all_sessions(conn)

    def _fidelity() -> Any:
        model_calls = [dict(r) for r in conn.execute(select(s.model_call)).mappings().all()]
        messages = [dict(r) for r in conn.execute(select(s.message)).mappings().all()]
        jobs = [dict(r) for r in conn.execute(select(s.scheduled_job)).mappings().all()]
        snaps = [
            dict(r)
            for r in conn.execute(
                select(
                    s.knowledge_snapshot.c.session_id,
                    s.knowledge_snapshot.c.snapshot_id,
                    s.knowledge_snapshot.c.purpose,
                )
            )
            .mappings()
            .all()
        ]
        return fidelity_rates(sessions, model_calls, messages, jobs, snaps, filters={})

    def _exposures() -> Any:
        rows = conn.execute(
            select(
                s.manipulation_exposure.c.kind,
                s.manipulation_exposure.c.status,
                func.count(),
            ).group_by(s.manipulation_exposure.c.kind, s.manipulation_exposure.c.status)
        ).all()
        missing = conn.execute(
            select(func.count())
            .select_from(s.manipulation_exposure)
            .where(
                s.manipulation_exposure.c.committed_seq.is_not(None),
                s.manipulation_exposure.c.received_seq.is_(None),
            )
        ).scalar_one()
        warnings = conn.execute(
            select(s.manipulation_exposure.c.status, func.count())
            .where(s.manipulation_exposure.c.kind == "one_minute_warning")
            .group_by(s.manipulation_exposure.c.status)
        ).all()
        return {
            "by_kind_status": [{"kind": k, "status": st, "n": int(n)} for k, st, n in rows],
            "committed_without_receive": int(missing),
            "one_minute_warning": {st: int(n) for st, n in warnings},
        }

    def _teammate_fidelity() -> Any:
        rows = teammate_fidelity(
            sessions,
            [dict(r) for r in conn.execute(select(s.experiment_manifest)).mappings()],
            [dict(r) for r in conn.execute(select(s.model_call)).mappings()],
            [dict(r) for r in conn.execute(select(s.message)).mappings()],
            [dict(r) for r in conn.execute(select(s.displayed_output)).mappings()],
        )
        return {"cohorts": fidelity_monitor(rows), "sessions": jsonable(rows)}

    def _episodes() -> Any:
        rows = conn.execute(
            select(s.request_episode.c.session_id, s.request_episode.c.status)
            .where(s.request_episode.c.closed_at.is_(None))
            .limit(200)
        ).all()
        return {
            "n_unresolved": len(rows),
            "session_ids": sorted({str(r.session_id) for r in rows})[:50],
            "by_status": {
                st: sum(1 for r in rows if r.status == st) for st in {r.status for r in rows}
            },
        }

    def _incidents() -> Any:
        incidents = [dict(r) for r in conn.execute(select(s.incident_interval)).mappings().all()]
        return {
            "summary": incident_summary(sessions, incidents, filters={}),
            "open": [jsonable(dict(r)) for r in incidents if r["ended_at"] is None][:100],
        }

    def _coding() -> Any:
        runs = conn.execute(
            select(s.coding_run.c.status, func.count()).group_by(s.coding_run.c.status)
        ).all()
        results = conn.execute(
            select(s.coding_result.c.status, func.count()).group_by(s.coding_result.c.status)
        ).all()
        gates = conn.execute(
            select(s.validation_gate.c.name, s.validation_gate.c.status)
            .order_by(s.validation_gate.c.created_at)
            .limit(200)
        ).all()
        if not runs:
            return {"note": "no coding runs committed", "runs": {}, "results": {}, "gates": []}
        return {
            "runs": {st: int(n) for st, n in runs},
            "results": {st: int(n) for st, n in results},
            "gates": [{"name": g.name, "status": g.status} for g in gates],
        }

    def _stages() -> Any:
        return {
            st: int(n)
            for st, n in conn.execute(
                select(s.session.c.stage, func.count()).group_by(s.session.c.stage)
            ).all()
        }

    return {
        "sessions_by_stage": section("sessions_by_stage", _stages),
        "fidelity_rates": section("fidelity_rates", _fidelity),
        "teammate_fidelity": section("teammate_fidelity", _teammate_fidelity),
        "manipulation_receipts": section("manipulation_receipts", _exposures),
        "unresolved_episodes": section("unresolved_episodes", _episodes),
        "incidents": section("incidents", _incidents),
        "coding_coverage": section("coding_coverage", _coding),
        "checks": checks,
        "note": "sections report their own status; unrun/unreadable checks are never 'complete'",
    }


def _live_sessions_q() -> Any:
    """Sessions minus tombstoned ones — a tombstoned session is deleted by contract
    (BA-39); its surviving append-only rows are a purge work order, not live data that
    should feed monitor/balance surfaces."""
    return select(s.session).where(
        ~select(s.admin_audit.c.session_id)
        .where(
            s.admin_audit.c.session_id == s.session.c.session_id,
            s.admin_audit.c.action == "deletion_tombstone",
        )
        .exists()
    )


def _all_sessions(conn: Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(_live_sessions_q()).mappings().all()]


def balance_report(conn: Connection, *, batch_id: str | None = None) -> dict[str, Any]:
    """Assignment/exposure/completion balance by compatibility_key x arm (INV-1302).
    Unkeyed sessions form their own ``unkeyed`` groups — never pooled with keyed rows."""
    q = _live_sessions_q()
    if batch_id:
        q = q.where(s.session.c.batch_id == batch_id)
    sessions = [dict(r) for r in conn.execute(q).mappings().all()]
    return {
        "rows": balance_rows(conn, sessions),
        "filter": {"batch_id": batch_id},
    }


def balance_rows(conn: Connection, sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Balance computation over a caller-scoped session set (INV-1700 lets the
    dashboard pass its filtered cohort instead of re-selecting)."""
    sids = [r["session_id"] for r in sessions]
    exposed: set[uuid.UUID] = set()
    completed: set[uuid.UUID] = set()
    latency: dict[uuid.UUID, list[float]] = {}
    results: dict[uuid.UUID, dict[str, Any]] = {}
    if sids:
        exposed = {
            r[0]
            for r in conn.execute(
                select(func.distinct(s.manipulation_exposure.c.session_id)).where(
                    s.manipulation_exposure.c.session_id.in_(sids),
                    s.manipulation_exposure.c.committed_seq.is_not(None),
                )
            ).all()
        }
        completed = {
            r[0]
            for r in conn.execute(
                select(s.completion_receipt.c.session_id).where(
                    s.completion_receipt.c.session_id.in_(sids),
                    s.completion_receipt.c.outcome == "completed",
                )
            ).all()
        }
        for r in conn.execute(
            select(s.model_call.c.session_id, s.model_call.c.duration_ms).where(
                s.model_call.c.session_id.in_(sids),
                s.model_call.c.duration_ms.is_not(None),
            )
        ).all():
            latency.setdefault(r[0], []).append(float(r[1]))
        results = {
            r["session_id"]: dict(r)
            for r in conn.execute(select(s.result).where(s.result.c.session_id.in_(sids)))
            .mappings()
            .all()
        }
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for sess in sessions:
        key = (group_key(sess), str(sess.get("arm") or "unassigned"))
        g = groups.setdefault(
            key,
            {
                "compatibility_key": key[0],
                "arm": key[1],
                "n_sessions": 0,
                "n_assigned": 0,
                "n_exposed": 0,
                "n_completed": 0,
                "n_terminal": 0,
                "durations_ms": [],
                "correct_total": [],
            },
        )
        g["n_sessions"] += 1
        if sess.get("arm"):
            g["n_assigned"] += 1
        if sess["session_id"] in exposed:
            g["n_exposed"] += 1
        if sess["session_id"] in completed:
            g["n_completed"] += 1
        if sess["stage"] in s.TERMINAL_STAGES:
            g["n_terminal"] += 1
        g["durations_ms"].extend(latency.get(sess["session_id"], []))
        res = results.get(sess["session_id"])
        if res is not None:
            g["correct_total"].append(int(res["correct_total"]))
    rows = []
    for g in groups.values():
        durs = g.pop("durations_ms")
        correct = g.pop("correct_total")
        rows.append(
            {
                **g,
                "mean_model_latency_ms": (sum(durs) / len(durs)) if durs else None,
                "n_model_calls_timed": len(durs),
                "mean_correct_total": (sum(correct) / len(correct)) if correct else None,
                "n_scored": len(correct),
            }
        )
    rows.sort(key=lambda r: (r["compatibility_key"], r["arm"]))
    return rows


# ----- dispositions / retention / queues ------------------------------------------------------


def dispositions(conn: Connection, *, batch_id: str | None = None) -> dict[str, Any]:
    """Withdrawal/data-use disposition per session: latest choice, terminal status and
    entitlement state. Compensation eligibility is shown independently of the data-use
    choice (INV-1307)."""
    q = select(s.session)
    if batch_id:
        q = q.where(s.session.c.batch_id == batch_id)
    sessions = [dict(r) for r in conn.execute(q.order_by(s.session.c.created_at)).mappings().all()]
    sids = [r["session_id"] for r in sessions]
    choices: dict[uuid.UUID, dict[str, Any]] = {}
    entitlements: dict[uuid.UUID, list[dict[str, Any]]] = {}
    if sids:
        for r in (
            conn.execute(
                select(s.data_use_choice)
                .where(s.data_use_choice.c.session_id.in_(sids))
                .order_by(s.data_use_choice.c.choice_seq)
            )
            .mappings()
            .all()
        ):
            choices[r["session_id"]] = dict(r)  # last wins
        for r in (
            conn.execute(
                select(s.payment_entitlement).where(s.payment_entitlement.c.session_id.in_(sids))
            )
            .mappings()
            .all()
        ):
            entitlements.setdefault(r["session_id"], []).append(dict(r))
    rows = [
        {
            "session_id": str(r["session_id"]),
            "batch_id": r["batch_id"],
            "mode": r["mode"],
            "stage": r["stage"],
            "terminal_reason": r["terminal_reason"],
            "data_use_choice": (choices.get(r["session_id"]) or {}).get("choice"),
            "entitlements": [
                {"kind": e["kind"], "status": e["status"], "cents": e["cents"]}
                for e in entitlements.get(r["session_id"], [])
            ],
        }
        for r in sessions
    ]
    return {"rows": jsonable(rows), "filter": {"batch_id": batch_id}}


def retention_status(conn: Connection, *, now: datetime | None = None) -> dict[str, Any]:
    """Identity-retention posture (INV-1306). The runtime role cannot read
    ``platform_link.purge_after`` — a denial is reported ``unavailable``, never zeroed.
    Call inside an open transaction (``engine.begin()``) so the savepoint guard works."""
    from app.services.retention import try_savepoint

    now = now or db_now(conn)

    def _count(*where: Any) -> Any:
        return conn.execute(
            select(func.count()).select_from(s.platform_link).where(*where)
        ).scalar_one()

    ok_total, total = try_savepoint(conn, lambda: _count())
    ok_sched, scheduled = try_savepoint(
        conn,
        lambda: _count(s.platform_link.c.purge_after.is_not(None)),
    )
    ok_due, due = try_savepoint(
        conn,
        lambda: _count(
            s.platform_link.c.purge_after.is_not(None),
            s.platform_link.c.purge_after <= now,
        ),
    )
    if ok_total and ok_sched and ok_due:
        links: dict[str, Any] = {
            "status": "ok",
            "total": int(total),
            "scheduled": int(scheduled),
            "due": int(due),
        }
    else:
        links = {
            "status": "unavailable",
            "note": "runtime role lacks identity read grants; the privileged retention "
            "path (scripts/retention_purge.py) reports these counts",
        }
    tombstoned_sids = [
        r[0]
        for r in conn.execute(
            select(s.admin_audit.c.session_id)
            .where(
                s.admin_audit.c.action == "deletion_tombstone",
                s.admin_audit.c.session_id.is_not(None),
            )
            .distinct()
        ).all()
    ]
    # BA-39: pending purge is visible at the posture level too — a tombstone is a work
    # order, and the count of still-resident rows is the honest completion signal.
    pending_total = 0
    pending_sessions = 0
    from app.services import retention

    for sid in tombstoned_sids:
        n = retention.pending_purge_rows(conn, sid)["total"]
        pending_total += n
        pending_sessions += n > 0
    return {
        "platform_links": links,
        "deletion_tombstones": len(tombstoned_sids),
        "purge_pending_rows": pending_total,
        "purge_pending_sessions": int(pending_sessions),
        "retention_rule": "platform IDs purgeable 48h after study close (S5); physical "
        "deletion of identity rows runs under the privileged retention path, never the "
        "runtime role",
        "now": now.isoformat(),
    }


def redaction_queue(conn: Connection, *, limit: int = 200) -> dict[str, Any]:
    """Free-text cells lacking a ``redaction_review`` row (INV-1308). Source refs use the
    same ``<table>:<session_id>:<pk...>`` convention as ``app.export.restricted``."""
    reviewed = {r[0] for r in conn.execute(select(s.redaction_review.c.source_ref)).all()}
    pending: list[dict[str, Any]] = []
    for table in (s.message, s.displayed_output, s.model_call):
        cols = FREE_TEXT_COLUMNS.get(table.name, ())
        if not cols:
            continue
        pk_names = [c.name for c in table.primary_key.columns]
        rows = (
            conn.execute(
                select(*[table.c[c] for c in pk_names], *[table.c[c] for c in cols]).limit(
                    MAX_SECTION_ROWS
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            ref = f"{table.name}:" + ":".join(str(row[c]) for c in pk_names)
            cells = [c for c in cols if row.get(c)]
            if cells and ref not in reviewed:
                pending.append({"source_ref": ref, "kind": table.name, "free_text_columns": cells})
    return {"pending": pending[:limit], "n_pending": len(pending), "n_reviewed": len(reviewed)}


def compensation_queue(conn: Connection) -> dict[str, Any]:
    """The compensation-role view: entitlement + outcome + the platform linkage columns
    the runtime role is granted (``platform``, ``platform_pid``, ``session_id``). Study/
    session refs are intentionally not granted to runtime; the restricted payment export
    (``app.export.restricted``) is the privileged path for the full linkage."""
    links = {
        r.session_id: {"platform": r.platform, "platform_pid": r.platform_pid}
        for r in conn.execute(
            select(
                s.platform_link.c.session_id,
                s.platform_link.c.platform,
                s.platform_link.c.platform_pid,
            )
        ).all()
    }
    receipts = {
        r.session_id: r.outcome
        for r in conn.execute(
            select(s.completion_receipt.c.session_id, s.completion_receipt.c.outcome)
        ).all()
    }
    rows = []
    for r in (
        conn.execute(
            select(
                s.payment_entitlement.c.session_id,
                s.payment_entitlement.c.kind,
                s.payment_entitlement.c.cents,
                s.payment_entitlement.c.status,
                s.payment_entitlement.c.decided_by,
                s.payment_entitlement.c.paid_ref,
            )
            .join(s.session, s.session.c.session_id == s.payment_entitlement.c.session_id)
            .where(
                s.session.c.mode == "live",
                ~select(s.batch_group.c.group_id)
                .where(
                    s.batch_group.c.group_id == s.session.c.batch_group_id,
                    s.batch_group.c.enrollment_policy["test_only"].as_boolean().is_(True),
                )
                .exists(),
            )
            .order_by(s.payment_entitlement.c.created_at)
        )
        .mappings()
        .all()
    ):
        sid = r["session_id"]
        rows.append(
            {
                "session_id": str(sid),
                "kind": r["kind"],
                "cents": r["cents"],
                "status": r["status"],
                "decided_by": r["decided_by"],
                "paid_ref": r["paid_ref"],
                "completion_outcome": receipts.get(sid),
                "platform": (links.get(sid) or {}).get("platform"),
                "platform_pid": (links.get(sid) or {}).get("platform_pid"),
            }
        )
    return {"rows": rows}


def audit_log(conn: Connection, *, limit: int = 200) -> dict[str, Any]:
    rows = (
        conn.execute(
            select(s.admin_audit).order_by(s.admin_audit.c.occurred_at.desc()).limit(limit)
        )
        .mappings()
        .all()
    )
    return {"entries": jsonable([dict(r) for r in rows])}


def audit_coverage(conn: Connection, session_id: uuid.UUID, *, limit: int = 200) -> dict[str, Any]:
    rows = (
        conn.execute(
            select(s.admin_audit)
            .where(s.admin_audit.c.session_id == session_id)
            .order_by(s.admin_audit.c.occurred_at)
            .limit(limit)
        )
        .mappings()
        .all()
    )
    return {"entries": jsonable([dict(r) for r in rows])}
