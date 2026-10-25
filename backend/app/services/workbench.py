"""Participant workbench projection (audit H5).

The body of ``GET /workbench``, moved behind a service function so the route only
authenticates and serializes.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, TypedDict

from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.db import schema as s
from app.db.tx import (
    RejectionError,
    db_now,
)
from app.domain.manifests.binding import BoundManifest
from app.domain.manifests.store import ContentStore
from app.domain.world import CRATE_CONTENTS, HIDEOUTS
from app.flags import FeatureFlags
from app.services import (
    activetime,
    lifecycle,
    practice,
    session_binding,
    worlds,
)
from app.services.projection import (
    exposure_projection,
    instrument_projection,
    material_projection,
    qa_projection,
)
from app.timing.policy import resolve_policy, time_pressure_for_arm


class WorkbenchView(TypedDict):
    """``GET /workbench`` response shape (audit H3). Keys are pinned to the
    frontend ``WorkbenchResponse`` interface by
    ``tests/contracts/test_workbench_contract.py``; mypy checks the literal
    returned below against this key set. Nested projections keep their own
    builders' types."""

    ai_assistant: bool
    answers: Any
    assigned_crates: Any
    bonus_payment: bool
    channels: Any
    crate_contents: Any
    delivered: Any
    exposure: Any
    file: Any
    frozen_answers: Any
    instrument: Any
    locations: Any
    material: Any
    practice: Any
    qa: Any
    revision: Any
    server_now: Any
    shared: Any
    solo_review_seconds: Any
    stage: Any
    structured_requests: Any
    task_shape: Any
    time_pressure: bool
    world_ready: Any


def workbench_view(
    engine: Engine,
    store: ContentStore,
    session_id: uuid.UUID,
    session: Mapping[str, Any],
    *,
    flags: FeatureFlags,
) -> WorkbenchView:
    """The participant workbench projection served by ``GET /workbench``: world
    file, deliveries, answers, clocks, practice world, instrument/Q&A/material/
    exposure projections and the flag posture the client renders against.
    Read-only; a pin drift degrades to the rows already committed rather than
    failing the page (audit H5: moved out of the route module)."""
    with engine.connect() as conn:
        now = db_now(conn)
        active = activetime.active_ms(conn, session_id, now, default=0)
        bound: BoundManifest | None
        try:
            bound = session_binding.bound_for_session(conn, store, session)
        except RejectionError:
            bound = None  # pin drift: the projection still serves rows already committed
        snapshot_row = (
            conn.execute(
                select(s.world_snapshot.c.content).where(
                    s.world_snapshot.c.session_id == session_id
                )
            )
            .mappings()
            .first()
        )
        file_rows: list[Mapping[str, Any]] = []
        delivered: list[dict[str, Any]] = []
        assigned: list[int] = []
        if snapshot_row is not None:
            # O-4: delivered cards auto-join the file, so ``delivered`` stays
            # empty — the not-yet-added bucket no longer exists.
            file_rows = worlds.participant_file(conn, session_id)
            assigned = sorted(int(n) for n in snapshot_row["content"].get("assigned") or [])
        answers: dict[str, dict[str, Any]] = {}
        for r in conn.execute(
            select(
                s.answer_revision.c.crate,
                s.answer_revision.c.location_id,
                s.answer_revision.c.revision,
            )
            .where(s.answer_revision.c.session_id == session_id)
            .order_by(s.answer_revision.c.revision)
        ).mappings():
            answers[str(r["crate"])] = {
                "location_id": r["location_id"],
                "revision": r["revision"],
            }
        frozen = {
            str(r["crate"]): r["location_id"]
            for r in conn.execute(
                select(s.answer_final.c.crate, s.answer_final.c.location_id).where(
                    s.answer_final.c.session_id == session_id
                )
            ).mappings()
        }
        ai_assistant = flags.ai_assistant
        time_pressure = time_pressure_for_arm(session.get("arm"), enabled=flags.time_pressure)
        bonus_payment = flags.bonus_payment
        channels: list[str] | None = None
        if (
            bound is not None
            and session["arm"] is not None
            and session["stage"] in ("solo_review", "task")
        ):
            # The canonical resolver is the single source for channel phases (BA-16).
            channels = sorted(resolve_policy(bound, session["arm"]).channels_open(max(0.0, active)))
            if not ai_assistant:
                # Feature flag: the prompting channel never opens for a
                # no-assistant deployment — not merely hidden in the UI.
                channels = [c for c in channels if c != "assistant_prompting"]
        shared = [
            str(r["record_id"])
            for r in conn.execute(
                select(s.share.c.record_id).where(
                    s.share.c.session_id == session_id,
                    s.share.c.sharer == "participant",
                )
            ).mappings()
        ]
        instrument = (
            instrument_projection(
                conn, bound, session, ai_assistant=ai_assistant, time_pressure=time_pressure
            )
            if bound is not None
            else None
        )
        material = (
            material_projection(bound, session, store=store, bonus_payment_enabled=bonus_payment)
            if bound is not None
            else None
        )
        exposure = exposure_projection(bound, session) if bound is not None else None
        # INV-0901: the fixed practice world for training — separate namespace, never
        # analytic (see app/services/practice.py). BA-106: the ask-required flag
        # follows the bound training artifact version, not the build.
        practice_world = (
            practice.practice_world(
                ask_required=lifecycle.practice_ask_required(bound),
                ai_assistant=ai_assistant,
            )
            if session["stage"] == "training"
            else None
        )
        # AI_QA_ENABLED: the exchange list must be read inside the connection
        # scope — flag-on sessions on ai_qa would otherwise query a closed conn.
        qa = qa_projection(conn, session)
    # 2026-10-15 crate-condition experiment (audit S01): the administered task
    # shape resolves snapshot-first — the persisted world_params is what the
    # generator actually dealt — falling back to the bound manifest's task for
    # pre-world sessions, never the deployment flags.
    task_shape = worlds.task_shape_for_session(
        snapshot_row["content"] if snapshot_row is not None else None, bound
    )
    return {
        "stage": session["stage"],
        "revision": session["revision"],
        "server_now": now.isoformat(),
        "world_ready": snapshot_row is not None,
        "locations": list(HIDEOUTS),
        # SQ-11: the fixed crate→item map, so pickers/ask dropdowns can label
        # "Crate 7 — tablets" — record cards still carry no crate badge. Bounded
        # by the session's task shape (4/8/12 crates under overlap_v1).
        "crate_contents": {
            str(n): CRATE_CONTENTS[n - 1] for n in range(1, task_shape["n_crates"] + 1)
        },
        # The participant-visible task shape — the case-background page renders
        # condition-aware copy from this (it is told the overlap level explicitly
        # in the brief).
        "task_shape": task_shape,
        "assigned_crates": assigned,
        "file": [dict(r) for r in file_rows],
        "delivered": delivered,
        "shared": shared,
        "answers": answers,
        "frozen_answers": frozen,
        "channels": channels,
        # FR-1: neutral capability flag (no arm label, no profile vocabulary) — the UI
        # renders the per-crate request controls only when it is true.
        "structured_requests": (
            bound is None or bound.manifest.interaction_mode == "structured_and_nl"
        ),
        # Seconds of the bound solo-review phase, if any — lets instructions
        # pre-announce "review your file alone first" only where it is true.
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
        "instrument": instrument,
        "material": material,
        "exposure": exposure,
        "practice": practice_world,
        # AI_QA_ENABLED: the rules-Q&A exchanges on the ai_qa stage (None on
        # every other stage — the stage itself is the flag signal).
        "qa": qa,
        # AI-assistant feature flag (AI_ASSISTANT_ENABLED): neutral capability
        # flag like ``structured_requests`` — when false the client drops the
        # assistant pane, its practice page, and its questionnaire items.
        "ai_assistant": ai_assistant,
        # Time-pressure flag (TIME_PRESSURE_ENABLED): when false the client
        # keeps the countdown as a neutral timer — no low-time styling, no
        # "One minute left" warning, no check-in pop-up — and the post-task
        # instrument omits the rushed-feeling follow-ups.
        "time_pressure": time_pressure,
        # BONUS_PAYMENT_ENABLED: when false the client drops every accuracy-bonus
        # mention (case background, gameplay banner, debrief disclosure); the
        # consent artifact itself is substituted server-side and the entitlement
        # is computed under the no-bonus policy.
        "bonus_payment": bonus_payment,
    }
