"""Study Admin Dashboard — descriptive analysis snapshot (WP17 Phase A).

One response, one snapshot: the eligible research-descriptive cohort AND the
separately labeled diagnostic cohort are resolved once, then computed from the
same committed rows:

- ``items`` (§3.5): instrument item descriptives per instrument@version x
  item (DA-04) — cells pool across partitions when sessions bind the
  identical artifact version (the version pins the wording); ``partition``
  reports the single contributor or ``pooled`` and ``partitions`` lists
  provenance. n, mean, sample SD, median, IQR, range, category distribution,
  and expected/applicable/answered/skipped/missing/absent/invalid counts
  derived from *bound* expected keys (DA-12). Answered values are validated
  against the bound item definition (DA-06): out-of-range numbers and
  undeclared strings count as ``invalid``, never averaged or echoed. No
  alpha/PCA/composites.
- ``power_inputs`` (§3.6): per-pool per-arm coverage/variance/N on the
  registered scalar measures — descriptive inputs only. A pool is one
  compatibility partition or a merge across partitions under a recorded
  ``PoolingEquivalence`` (``pooling_policy``). MDE scenarios report
  ``unsupported_model`` (Phase B, gated on D12/D14/O-13).
- ``contrasts`` (§3.7): per-pool per-arm descriptives + direction labels.
  Inferential fields are ``null`` with ``analysis_not_configured``.
- ``findings`` (§3.8): neutral cards citing cohort, measure keys, Ns,
  missingness and the analysis digest.
- ``diagnostics`` (DA-13): the same descriptive machinery over the non-live
  analytic cohort — qa/preview/accelerated sessions that pass every
  analytic-completeness check but can never enter the research N.

Scalar measures come from the canonical measurement layer (DA-02/03/11):
durable-event counts are measured zeros when a completed session has no rows;
``accuracy.correct_total`` is the pinned ``scoring.score_rows`` ratio
(correct/assigned), not the persisted raw count; ``confidence.mean`` picks the
latest revision *then* filters to answered so a later skip can never resurrect
a superseded answer.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any, Protocol

from sqlalchemy import and_, func, or_, select
from sqlalchemy.engine import Connection

from app.db import schema as s
from app.domain.manifests.canonical import content_hash
from app.domain.manifests.store import ContentStore
from app.flags import DEFAULT_FLAGS, FeatureFlags
from app.measurement import scoring
from app.measurement.codebook import CODEBOOK_VERSION
from app.measurement.rows import SessionRows
from app.measurement.telemetry import attention_measures, file_telemetry_measures
from app.services import adminviews, dashboard, pooling_policy
from app.services.dashboard import DashboardFilter
from app.services.lifecycle import (
    ASSISTANT_COMPREHENSION_ITEM,
    ASSISTANT_INSTRUMENT_ITEMS,
    COMPREHENSION_INSTRUMENT_ID,
)
from app.timing.policy import time_pressure_for_arm

ANALYSIS_SCHEMA = "dashboard.analysis.a2"

# Registered scalar measures (§3.6): codebook key → (unit, description).
# ``_COUNT_SCALARS`` are durable-event counts — a completed session with no
# matching rows has a *measured* 0, not missing data (DA-02). The other
# measures stay missing/undefined until their committed evidence exists.
_SCALARS: dict[str, dict[str, str]] = {
    "seeking.structured_teammate_requests": {
        "unit": "accepted_commands",
        "description": "Accepted participant teammate_request commands",
    },
    "seeking.free_text_teammate_messages": {
        "unit": "accepted_commands",
        "description": "Accepted participant social_reply (typed chat) commands",
    },
    "ai_use.prompts": {
        "unit": "accepted_commands",
        "description": "Accepted assistant_request commands",
    },
    "sharing.participant_share_events": {
        "unit": "share_events",
        "description": "share rows with sharer='participant'",
    },
    "accuracy.correct_total": {
        "unit": "proportion_of_assigned",
        "description": "correct assigned / assigned (scoring.score_rows ratio)",
    },
    "accuracy.entered_count": {
        "unit": "assigned_crates",
        "description": "assigned crates with a frozen final answer (0 to n_assigned)",
    },
    "accuracy.correct_ownfile": {
        "unit": "proportion_of_assigned_own",
        "description": "correct own-file assigned / own-file assigned",
    },
    "accuracy.correct_teamdep": {
        "unit": "proportion_of_assigned_team",
        "description": "correct team-dependent assigned / team-dependent assigned",
    },
    "answers.update_events": {
        "unit": "answer_updated_events",
        "description": "answer_updated events — every committed pick/revision, not just finals",
    },
    "answers.first_answer_s": {
        "unit": "wall_s_since_task_start",
        "description": "wall seconds from task_clock_started to first answer_updated",
    },
    "answers.last_answer_s": {
        "unit": "wall_s_since_task_start",
        "description": "wall seconds from task start to last answer_updated",
    },
    "request.episodes": {
        "unit": "request_episodes",
        "description": "open request episodes whose responder is the participant (teammate asks)",
    },
    "request.addressed": {
        "unit": "episodes_closed_by_share",
        "description": "teammate asks resolved by a participant share on the asked crate",
    },
    "request.addressed_frac": {
        "unit": "proportion_of_episodes",
        "description": "request.addressed / request.episodes; undefined at 0 episodes",
    },
    "request.answer_s_median": {
        "unit": "wall_s",
        "description": "median open→close seconds across participant_share-resolved episodes",
    },
    "confidence.rated_count": {
        "unit": "crates_rated",
        "description": "assigned crates carrying a latest-revision answered confidence rating",
    },
    "items.median_dwell_ms": {
        "unit": "ms",
        "description": "median per-item dwell (telemetry_item_start→end) across instruments",
    },
    "items.dwell_lt1s_count": {
        "unit": "items",
        "description": "instrument items with paired dwell under 1s — flat-liner signal",
    },
    "telemetry.reconnects": {
        "unit": "reconnects",
        "description": "distinct client epochs minus one — mid-session client restarts",
    },
    "chat.rendered_frac": {
        "unit": "proportion_of_delivered",
        "description": "team-channel deliveries with a client render receipt / delivered",
    },
    "chat.scroll_events": {
        "unit": "telemetry_events",
        "description": "participant chat-pane scroll telemetry — the read-side engagement signal",
    },
    "open_ended.total_chars": {
        "unit": "chars",
        "description": "total characters across answered open_ended free-text items",
    },
    "open_ended.min_item_chars": {
        "unit": "chars",
        "description": "shortest answered open_ended item response, in characters",
    },
    "checkin.own_rating": {
        "unit": "rating_1_7",
        "description": "pressure_checkin own_rating recorded before the reveal",
    },
    "seeking.typed_teammate_requests": {
        "unit": "teammate_requested_events",
        "description": "teammate_requested events tagged via='typed' (nl_only asks)",
    },
    "automation.probes": {
        "unit": "automation_probe_events",
        "description": "automation_probe events (participant identity probes)",
    },
    # Comprehension gate (2026-10-10): answers persist as training_step events
    # (step=comprehension, payload.missed), never item_response — these are the
    # only analytic surfaces for it. First-attempt measures are missing (not 0)
    # when a session never reached the check; the item keys mirror
    # lifecycle.COMPREHENSION_ITEMS (contract test asserts parity).
    "training.comprehension_attempts": {
        "unit": "training_step_events",
        "description": "comprehension-check attempts (training_step events)",
    },
    "training.comprehension_first_misses": {
        "unit": "items_missed",
        "description": "items missed on the first comprehension attempt",
    },
    "training.comprehension_passed_first_try": {
        "unit": "share",
        "description": "1 if the first comprehension attempt had zero misses",
    },
    "training.comprehension_miss_partial_intel": {
        "unit": "share",
        "description": "first-attempt missed 'partial_intel'",
    },
    "training.comprehension_miss_same_crates": {
        "unit": "share",
        "description": "first-attempt missed 'same_crates'",
    },
    "training.comprehension_miss_accurate_not_latest": {
        "unit": "share",
        "description": "first-attempt missed 'accurate_not_latest'",
    },
    "training.comprehension_miss_teammates_may_have_mine": {
        "unit": "share",
        "description": "first-attempt missed 'teammates_may_have_mine'",
    },
    "training.comprehension_miss_assistant_reads_team": {
        "unit": "share",
        "description": "first-attempt missed 'assistant_reads_team'",
    },
    "training.comprehension_miss_record_before_timer": {
        "unit": "share",
        "description": "first-attempt missed 'record_before_timer'",
    },
    "confidence.mean": {
        "unit": "confidence_0_100",
        "description": "Mean over crates of latest-revision answered confidence",
    },
    # Exploratory telemetry process measures (case-file telemetry, cb-1.8).
    # Values come from the same ``measurement.telemetry`` layer the export uses;
    # the family's *_state rides through as the scalar state, so a session with
    # no file telemetry is *missing* on these — never counted as a zero.
    "telemetry.events": {
        "unit": "telemetry_rows",
        "description": "persisted telemetry_* events (queue health, exploratory)",
    },
    "telemetry.client_epochs": {
        "unit": "epochs",
        "description": "distinct client_epoch values (page loads/reloads, exploratory)",
    },
    "attention.hidden_count": {
        "unit": "episodes",
        "description": "tab-hidden episodes inside the task window (exploratory)",
    },
    "attention.hidden_ms_total": {
        "unit": "ms",
        "description": "total task-window hidden time (exploratory)",
    },
    "attention.blur_count": {
        "unit": "episodes",
        "description": "window blur episodes inside the task window (exploratory)",
    },
    "file.coverage_frac": {
        "unit": "share",
        "description": "viewed records / file size, view ≥300 ms at ≥60% visible (exploratory)",
    },
    "file.n_records_viewed": {
        "unit": "records",
        "description": "records with ≥1 qualifying viewport impression (exploratory)",
    },
    "file.dwell_ms_total": {
        "unit": "ms",
        "description": "total case-file dwell across all records (exploratory)",
    },
    "file.max_scroll_depth": {
        "unit": "share",
        "description": "deepest scroll fraction of the file list (exploratory)",
    },
    "file.reached_bottom": {
        "unit": "share",
        "description": "1 if the file list was scrolled to ≥0.95 depth (exploratory)",
    },
    "file.scroll_reversals": {
        "unit": "events",
        "description": "scroll direction reversals in the file list (exploratory)",
    },
    "file.scroll_distance_px": {
        "unit": "px",
        "description": "total scroll distance traveled (exploratory)",
    },
    "file.reorder_count": {
        "unit": "events",
        "description": "file-card reorders via drag/grip/arrow (exploratory)",
    },
    "share.attempts": {
        "unit": "events",
        "description": "share attempts incl. rejected/ignored (exploratory)",
    },
    "share.via_drag_frac": {
        "unit": "share",
        "description": "durable participant shares attributed to drag-to-chat (exploratory)",
    },
    "file.post_share_return_ms": {
        "unit": "ms",
        "description": "occurrence-time latency teammate-added intel → first view (exploratory)",
    },
}

_COUNT_SCALARS = frozenset(
    {
        "seeking.structured_teammate_requests",
        "seeking.free_text_teammate_messages",
        "seeking.typed_teammate_requests",
        "ai_use.prompts",
        "sharing.participant_share_events",
        "automation.probes",
        "training.comprehension_attempts",
        "telemetry.events",
        "telemetry.client_epochs",
        "answers.update_events",
        "request.episodes",
        "request.addressed",
    }
)

# A scalar slot's measured value: ``value`` is None whenever the measure is
# missing/undefined — such sessions count as missing, never as zero.
ScalarValue = dict[str, Any]  # {"value": float|None, "state": str, "denominator": int|None}

# Treatment-minus-reference contrast direction: the arm named here sorts as the
# minuend (high - low, eval - control); otherwise the later alphabetical arm.
_TREATED_ARMS = ("high", "eval", "treatment")


def _comprehension_firsts(
    rows: Iterable[tuple[uuid.UUID, Any]],
) -> dict[uuid.UUID, list[str]]:
    """First-attempt missed-item list per session from seq-ordered
    ``(session_id, payload.missed)`` comprehension rows. Retries follow the
    first row; a non-list ``missed`` degrades to an empty list rather than
    fabricating misses."""
    firsts: dict[uuid.UUID, list[str]] = {}
    for sid, missed in rows:
        if sid not in firsts:
            firsts[sid] = list(missed) if isinstance(missed, list) else []
    return firsts


def _comprehension_first_answers(
    rows: Iterable[tuple[uuid.UUID, Any]],
) -> dict[uuid.UUID, dict[str, Any]]:
    """First-attempt ``answers`` mapping per session from seq-ordered
    ``(session_id, payload.answers)`` comprehension rows — the durable marker
    that the session reached the check (no row ⇒ the instrument is not
    expected of it)."""
    firsts: dict[uuid.UUID, dict[str, Any]] = {}
    for sid, answers in rows:
        if sid not in firsts:
            firsts[sid] = dict(answers) if isinstance(answers, Mapping) else {}
    return firsts


class _PutScalar(Protocol):
    """``_load_scalars``' recorder: one canonical scalar value for a session."""

    def __call__(
        self,
        sid: uuid.UUID,
        key: str,
        value: Any,
        state: str = "measured",
        denom: int | None = None,
    ) -> None: ...


@dataclass(frozen=True)
class _ScalarCtx:
    """What every scalar family reads: the connection, the session ids, the
    recorder and the comprehension events the caller already fetched."""

    conn: Connection
    sids: list[uuid.UUID]
    put: _PutScalar
    comp_events: Sequence[tuple[uuid.UUID, Any, Any]]


def _scalars_event_counts(ctx: _ScalarCtx) -> None:
    """Durable-event counts, typed intel asks and identity probes."""
    # Durable-event counts: measured zero is the honest value for a completed
    # session with no matching committed rows (DA-02).
    for sid in ctx.sids:
        for key in _COUNT_SCALARS:
            ctx.put(sid, key, 0.0)

    kinds = {
        "teammate_request": "seeking.structured_teammate_requests",
        "social_reply": "seeking.free_text_teammate_messages",
        "assistant_request": "ai_use.prompts",
    }
    for sid, kind, n in ctx.conn.execute(
        select(
            s.command_receipt.c.session_id,
            s.command_receipt.c.kind,
            func.count(),
        )
        .where(
            s.command_receipt.c.session_id.in_(ctx.sids),
            s.command_receipt.c.outcome == "accepted",
            s.command_receipt.c.kind.in_(kinds),
        )
        .group_by(s.command_receipt.c.session_id, s.command_receipt.c.kind)
    ).all():
        ctx.put(sid, kinds[kind], float(n))

    for sid, n in ctx.conn.execute(
        select(s.share.c.session_id, func.count())
        .where(s.share.c.session_id.in_(ctx.sids), s.share.c.sharer == "participant")
        .group_by(s.share.c.session_id)
    ).all():
        ctx.put(sid, "sharing.participant_share_events", float(n))

    # Typed intel asks (nl_only ask mechanism): teammate_requested events
    # tagged via='typed' — the typed counterpart of the structured
    # teammate_request command count. Structured-picker events carry no `via`.
    for sid, n in ctx.conn.execute(
        select(s.event.c.session_id, func.count())
        .where(
            s.event.c.session_id.in_(ctx.sids),
            s.event.c.type == "teammate_requested",
            s.event.c.payload["via"].astext == "typed",
        )
        .group_by(s.event.c.session_id)
    ).all():
        ctx.put(sid, "seeking.typed_teammate_requests", float(n))

    # Identity probes: every recognized automation_probe event (BA-75 — probes
    # are always logged even when the disclosure line is deferred).
    for sid, n in ctx.conn.execute(
        select(s.event.c.session_id, func.count())
        .where(s.event.c.session_id.in_(ctx.sids), s.event.c.type == "automation_probe")
        .group_by(s.event.c.session_id)
    ).all():
        ctx.put(sid, "automation.probes", float(n))


def _scalars_comprehension(ctx: _ScalarCtx) -> None:
    """Comprehension-gate attempts and first-attempt item misses."""
    # Comprehension gate (2026-10-10): attempts count every step=comprehension
    # training_step event (``ctx.comp_events`` is the seq-ordered fetch the caller
    # also uses to synthesize first-attempt item rows); first-attempt measures
    # derive from the seq-earliest row and are missing (not zero) for sessions
    # that never attempted — which includes every session run before the gate.
    attempts: dict[uuid.UUID, int] = defaultdict(int)
    for sid, _missed, _answers in ctx.comp_events:
        attempts[sid] += 1
    for sid, n in attempts.items():
        ctx.put(sid, "training.comprehension_attempts", float(n))
    from app.services.lifecycle import COMPREHENSION_ITEMS

    comp_item_ids = [i["item_id"] for i in COMPREHENSION_ITEMS]
    firsts = _comprehension_firsts((sid, missed) for sid, missed, _a in ctx.comp_events)
    # Per-item scalars emit only for items the session was actually presented:
    # ``AI_ASSISTANT_ENABLED=0`` sessions never see ``assistant_reads_team``, so
    # its absence from first-attempt answers is the durable era marker and the
    # scalar stays missing — never a fabricated "answered correctly".
    first_answers = _comprehension_first_answers(
        (sid, answers) for sid, _m, answers in ctx.comp_events
    )
    for sid, missed in firsts.items():
        presented = set(first_answers.get(sid, {})) | set(missed)
        ctx.put(sid, "training.comprehension_first_misses", float(len(missed)))
        ctx.put(sid, "training.comprehension_passed_first_try", 0.0 if missed else 1.0)
        for iid in comp_item_ids:
            if iid in presented:
                ctx.put(sid, f"training.comprehension_miss_{iid}", float(iid in missed))


def _scalars_accuracy(ctx: _ScalarCtx) -> None:
    """Scored accuracy via the pinned scoring adapter, plus ratings."""
    # accuracy.correct_total via the pinned scoring adapter (DA-03): bulk-load
    # the three committed inputs, then call the same pure score_rows the
    # measurement layer uses — value/denominator/state are its Measure fields,
    # never a raw persisted count substituted under the codebook key.
    worlds = {
        r["session_id"]: r["content"]
        for r in ctx.conn.execute(
            select(s.world_snapshot.c.session_id, s.world_snapshot.c.content).where(
                s.world_snapshot.c.session_id.in_(ctx.sids)
            )
        )
        .mappings()
        .all()
    }
    finals = dashboard.per_session(
        ctx.conn,
        s.answer_final,
        ctx.sids,
        s.answer_final.c.crate,
        s.answer_final.c.revision,
        s.answer_final.c.location_id,
        s.answer_final.c.finalized_seq,
        s.answer_final.c.finalized_at,
    )
    revisions = dashboard.per_session(
        ctx.conn,
        s.answer_revision,
        ctx.sids,
        s.answer_revision.c.crate,
        s.answer_revision.c.revision,
        s.answer_revision.c.location_id,
        s.answer_revision.c.accepted_seq,
    )
    for sid in ctx.sids:
        score = scoring.score_rows(
            SessionRows(
                session_id=sid,
                world_content=worlds.get(sid),
                answer_finals=tuple(finals.get(sid, ())),
                answer_revisions=tuple(revisions.get(sid, ())),
            )
        )
        m = score.correct_total
        ctx.put(sid, m.name, m.value, state=m.state.value, denom=m.denominator)
        ctx.put(
            sid,
            "accuracy.entered_count",
            float(score.answered_count),
            denom=m.denominator,
        )
        for m in (score.correct_ownfile, score.correct_teamdep):
            ctx.put(sid, m.name, m.value, state=m.state.value, denom=m.denominator)

    for sid, rating in ctx.conn.execute(
        select(s.manipulation_exposure.c.session_id, s.manipulation_exposure.c.own_rating).where(
            s.manipulation_exposure.c.session_id.in_(ctx.sids),
            s.manipulation_exposure.c.kind == "pressure_checkin",
            s.manipulation_exposure.c.own_rating.is_not(None),
        )
    ).all():
        ctx.put(sid, "checkin.own_rating", float(rating))


def _scalars_confidence(ctx: _ScalarCtx) -> None:
    """confidence.mean over latest answered revisions (DA-11)."""
    # confidence.mean (DA-11): latest revision per crate across ALL statuses
    # first, then only crates whose latest revision is answered contribute — a
    # later ``skipped`` can never resurrect a superseded answer.
    latest: dict[tuple[uuid.UUID, int], tuple[int, str, Any]] = {}
    for sid, crate, rev, status, val in ctx.conn.execute(
        select(
            s.confidence_response.c.session_id,
            s.confidence_response.c.crate,
            s.confidence_response.c.revision,
            s.confidence_response.c.status,
            s.confidence_response.c.value,
        ).where(s.confidence_response.c.session_id.in_(ctx.sids))
    ).all():
        k = (sid, int(crate))
        if k not in latest or rev > latest[k][0]:
            latest[k] = (int(rev), str(status), val)
    vals: dict[uuid.UUID, list[float]] = defaultdict(list)
    for (sid, _crate), (_rev, status, val) in latest.items():
        if status == "answered" and val is not None:
            vals[sid].append(float(val))
    for sid, xs in vals.items():
        if xs:
            ctx.put(sid, "confidence.mean", sum(xs) / len(xs), denom=len(xs))
            ctx.put(sid, "confidence.rated_count", float(len(xs)))


def _scalars_requests(ctx: _ScalarCtx) -> None:
    """Request-episode ledger: addressed share and answer latency."""
    # Request-episode ledger (cb-1.11): teammate asks aimed at the participant
    # (request.responder='participant') — shares on the asked crate close them
    # as 'participant_share'; scripted deliveries close them as 'delivered' and
    # the deadline/abandon sweep expires them. ``sharing.participant_share_events``
    # alone cannot tell responsive sharing from unrequested sharing.
    episodes: dict[uuid.UUID, list[tuple[Any, Any, str]]] = defaultdict(list)
    for sid, opened, closed, reason in ctx.conn.execute(
        select(
            s.request_episode.c.session_id,
            s.request_episode.c.opened_at,
            s.request_episode.c.closed_at,
            s.request_episode.c.close_reason,
        )
        .select_from(
            s.request_episode.join(
                s.request,
                (s.request.c.session_id == s.request_episode.c.session_id)
                & (s.request.c.request_id == s.request_episode.c.request_id),
            )
        )
        .where(
            s.request_episode.c.session_id.in_(ctx.sids),
            s.request.c.responder == "participant",
        )
    ).all():
        episodes[sid].append((opened, closed, str(reason or "")))
    for sid in ctx.sids:
        rows = episodes.get(sid, [])
        ctx.put(sid, "request.episodes", float(len(rows)))
        addressed = [(o, c) for o, c, r in rows if r == "participant_share"]
        ctx.put(sid, "request.addressed", float(len(addressed)))
        if rows:
            ctx.put(
                sid,
                "request.addressed_frac",
                len(addressed) / len(rows),
                denom=len(rows),
            )
        lats = sorted(
            (c - o).total_seconds() for o, c in addressed if o is not None and c is not None
        )
        if lats:
            ctx.put(sid, "request.answer_s_median", median(lats), denom=len(lats))


def _scalars_answer_timing(ctx: _ScalarCtx) -> None:
    """Answer-entry timing against the task-clock start."""
    # Answer-entry timing (cb-1.11): ``answer_updated`` carries no active_ms —
    # wall seconds against the task-clock start event. A completed session with
    # no answers shows missing timings, never 0.
    answer_times: dict[uuid.UUID, list[Any]] = defaultdict(list)
    task_starts: dict[uuid.UUID, Any] = {}
    for sid, typ, st in ctx.conn.execute(
        select(s.event.c.session_id, s.event.c.type, s.event.c.server_time)
        .where(
            s.event.c.session_id.in_(ctx.sids),
            s.event.c.type.in_(("answer_updated", "task_clock_started")),
        )
        .order_by(s.event.c.session_id, s.event.c.seq)
    ).all():
        if typ == "task_clock_started":
            task_starts.setdefault(sid, st)
        elif st is not None:
            answer_times[sid].append(st)
    for sid in ctx.sids:
        ups = answer_times.get(sid, [])
        ctx.put(sid, "answers.update_events", float(len(ups)))
        t0 = task_starts.get(sid)
        if ups and t0 is not None:
            ctx.put(sid, "answers.first_answer_s", (ups[0] - t0).total_seconds())
            ctx.put(sid, "answers.last_answer_s", (ups[-1] - t0).total_seconds())


def _scalars_open_ended(ctx: _ScalarCtx) -> None:
    """Open-ended free-text volume."""
    # Open-ended free-text volume (cb-1.11): character counts on answered
    # open_ended items — "i actually never got to" vs a substantive answer is a
    # length tell no scalar previously surfaced.
    oe_lens: dict[uuid.UUID, list[int]] = defaultdict(list)
    for sid, val in ctx.conn.execute(
        select(
            s.item_response.c.session_id,
            s.item_response.c.response["value"].astext,
        ).where(
            s.item_response.c.session_id.in_(ctx.sids),
            s.item_response.c.instrument_id.like("open_ended%"),
            s.item_response.c.status == "answered",
        )
    ).all():
        if isinstance(val, str):
            oe_lens[sid].append(len(val))
    for sid, lens in oe_lens.items():
        if lens:
            ctx.put(sid, "open_ended.total_chars", float(sum(lens)), denom=len(lens))
            ctx.put(sid, "open_ended.min_item_chars", float(min(lens)), denom=len(lens))


def _scalars_chat_and_telemetry(ctx: _ScalarCtx) -> None:
    """Chat read-side coverage and the telemetry/attention scalars."""
    # Chat read-side (cb-1.11): per-session delivered team messages vs client
    # render receipts. ``chat.scroll_events`` needs the chat_scroll emit
    # (frontend added cb-1.11); sessions bound before that build are
    # ``unobservable``, not zero.
    team_delivered: dict[uuid.UUID, int] = defaultdict(int)
    for sid, n in ctx.conn.execute(
        select(s.message.c.session_id, func.count())
        .where(
            s.message.c.session_id.in_(ctx.sids),
            s.message.c.channel == "team",
            s.message.c.status == "delivered",
        )
        .group_by(s.message.c.session_id)
    ).all():
        team_delivered[sid] = int(n)
    sess_behavior: dict[uuid.UUID, Any] = dict(
        ctx.conn.execute(
            select(s.session.c.session_id, s.session.c.behavior_source_sha256).where(
                s.session.c.session_id.in_(ctx.sids)
            )
        ).all()
    )
    build_behavior = os.environ.get("INVESTIGATORS_BEHAVIOR_SOURCE_SHA256")

    # Telemetry scalars (cb-1.8): bulk-load the same committed inputs the
    # measurement layer uses — telemetry_* events, file memberships, shares —
    # then call ``file_telemetry_measures``/``attention_measures`` directly so
    # dashboard numbers can never diverge from export numbers.
    tel_events: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for r in (
        ctx.conn.execute(
            select(
                s.event.c.session_id,
                s.event.c.seq,
                s.event.c.type,
                s.event.c.stage,
                s.event.c.active_ms,
                s.event.c.server_time,
                s.event.c.payload,
            )
            .where(
                s.event.c.session_id.in_(ctx.sids),
                s.event.c.type.like("telemetry_%"),
            )
            .order_by(s.event.c.session_id, s.event.c.seq)
        )
        .mappings()
        .all()
    ):
        tel_events.setdefault(r["session_id"], []).append(dict(r))

    memberships = dashboard.per_session(
        ctx.conn,
        s.file_membership,
        ctx.sids,
        s.file_membership.c.record_id,
        s.file_membership.c.source,
        s.file_membership.c.acquired_event_seq,
        s.file_membership.c.acquired_at,
    )
    shares = dashboard.per_session(
        ctx.conn,
        s.share,
        ctx.sids,
        s.share.c.record_id,
        s.share.c.sharer,
        s.share.c.delivered_seq,
        s.share.c.delivered_at,
    )

    # Anchor events for the persist-time return-latency proxy: the event rows
    # telemetry/acquisition anchors point at (seq within the session).
    anchor_seqs = {
        int(m["acquired_event_seq"])
        for ms in memberships.values()
        for m in ms
        if m.get("acquired_event_seq") is not None
    }
    anchor_active: dict[tuple[uuid.UUID, int], int] = {}
    if anchor_seqs:
        for sid, seq, ams in ctx.conn.execute(
            select(s.event.c.session_id, s.event.c.seq, s.event.c.active_ms).where(
                s.event.c.session_id.in_(ctx.sids),
                s.event.c.seq.in_(anchor_seqs),
            )
        ):
            if ams is not None:
                anchor_active[(sid, int(seq))] = int(ams)

    def _num(v: Any) -> float | None:
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    for sid in ctx.sids:
        evs = tel_events.get(sid, [])
        members = memberships.get(sid, [])
        shs = shares.get(sid, [])
        fm = file_telemetry_measures(
            evs,
            file_size=len(members) or None,
            added_from_team=[m for m in members if m["source"] == "added_from_team"],
            participant_shares=[x for x in shs if x["sharer"] == "participant"],
            teammate_shares=[x for x in shs if x["sharer"] != "participant"],
            event_active_ms=lambda q, _sid=sid: anchor_active.get((_sid, int(q))),
        )
        att = attention_measures(evs)
        ctx.put(sid, "telemetry.events", float(fm["telemetry_events"]))
        ctx.put(sid, "telemetry.client_epochs", float(fm["client_epoch_count"]))
        epochs = fm["client_epoch_count"]
        if epochs:
            ctx.put(sid, "telemetry.reconnects", float(max(0, int(epochs) - 1)))

        # Per-item dwell pairing (cb-1.11): item_start→item_end share
        # data.item_id; client_ms is comparable only within a client_epoch
        # (BA-49) — cross-epoch pairs fall back to server_time.
        starts: dict[str, tuple[Any, Any, Any]] = {}
        dwells: list[float] = []
        team_renders = 0
        chat_scrolls = 0
        for e in evs:
            typ = e["type"]
            if typ == "telemetry_render":
                if ((e.get("payload") or {}).get("data") or {}).get("channel") == "team":
                    team_renders += 1
                continue
            if typ == "telemetry_chat_scroll":
                chat_scrolls += 1
                continue
            if typ not in ("telemetry_item_start", "telemetry_item_end"):
                continue
            pay = e.get("payload") or {}
            iid = (pay.get("data") or {}).get("item_id")
            if not iid:
                continue
            if typ == "telemetry_item_start":
                starts[str(iid)] = (pay.get("client_ms"), pay.get("client_epoch"), e["server_time"])
            else:
                st = starts.pop(str(iid), None)
                if st is None:
                    continue
                cms, cepoch, stime = st
                ems, eepoch = pay.get("client_ms"), pay.get("client_epoch")
                dwell = None
                if (
                    isinstance(cms, (int, float))
                    and isinstance(ems, (int, float))
                    and cepoch == eepoch
                ):
                    dwell = float(ems) - float(cms)
                elif stime is not None and e["server_time"] is not None:
                    dwell = (e["server_time"] - stime).total_seconds() * 1000.0
                if dwell is not None and dwell >= 0:
                    dwells.append(dwell)
        if dwells:
            ctx.put(sid, "items.median_dwell_ms", median(dwells), denom=len(dwells))
            ctx.put(
                sid,
                "items.dwell_lt1s_count",
                float(sum(1 for d in dwells if d < 1000)),
                denom=len(dwells),
            )
        deliv = team_delivered.get(sid, 0)
        if deliv:
            ctx.put(
                sid,
                "chat.rendered_frac",
                min(1.0, team_renders / deliv),
                denom=deliv,
            )
        if build_behavior and sess_behavior.get(sid) == build_behavior:
            ctx.put(sid, "chat.scroll_events", float(chat_scrolls))
        else:
            ctx.put(sid, "chat.scroll_events", None, state="unobservable")
        astate = str(att["attention_state"])
        ctx.put(sid, "attention.hidden_count", _num(att["attention_hidden_count"]), state=astate)
        ctx.put(
            sid,
            "attention.hidden_ms_total",
            _num(att["attention_hidden_ms_total"]),
            state=astate,
        )
        ctx.put(sid, "attention.blur_count", _num(att["attention_blur_count"]), state=astate)
        ctx.put(
            sid,
            "file.coverage_frac",
            _num(fm["file_coverage_frac"]),
            state=fm["file_view_state"],
        )
        ctx.put(
            sid,
            "file.n_records_viewed",
            _num(fm["file_viewed_count"]),
            state=fm["file_view_state"],
        )
        ctx.put(
            sid,
            "file.dwell_ms_total",
            _num(fm["file_dwell_ms_total"]),
            state=fm["file_view_state"],
        )
        ctx.put(
            sid,
            "file.max_scroll_depth",
            _num(fm["file_max_scroll_depth"]),
            state=fm["file_scroll_state"],
        )
        rb = fm["file_reached_bottom"]
        ctx.put(
            sid,
            "file.reached_bottom",
            float(rb) if isinstance(rb, bool) else None,
            state=fm["file_scroll_state"],
        )
        ctx.put(
            sid,
            "file.scroll_reversals",
            _num(fm["file_scroll_reversals"]),
            state=fm["file_scroll_state"],
        )
        ctx.put(
            sid,
            "file.scroll_distance_px",
            _num(fm["file_scroll_distance_px"]),
            state=fm["file_scroll_state"],
        )
        ctx.put(
            sid,
            "file.reorder_count",
            _num(fm["file_reorder_count"]),
            state=fm["file_reorder_state"],
        )
        ctx.put(
            sid,
            "share.attempts",
            _num(fm["share_attempts"]),
            state=fm["file_share_state"],
        )
        ctx.put(
            sid,
            "share.via_drag_frac",
            _num(fm["share_via_drag_frac"]),
            state=fm["file_share_state"],
        )
        ctx.put(
            sid,
            "file.post_share_return_ms",
            _num(fm["file_post_share_return_ms"]),
            state=fm["file_view_state"],
        )


# Scalar families in evaluation order (audit H1): each writes its keys through
# ``ctx.put``; add a new family here rather than growing ``_load_scalars``.
_SCALAR_FAMILIES: tuple[Callable[[_ScalarCtx], None], ...] = (
    _scalars_event_counts,
    _scalars_comprehension,
    _scalars_accuracy,
    _scalars_confidence,
    _scalars_requests,
    _scalars_answer_timing,
    _scalars_open_ended,
    _scalars_chat_and_telemetry,
)


def _load_scalars(
    conn: Connection,
    sids: list[uuid.UUID],
    comp_events: Sequence[tuple[uuid.UUID, Any, Any]] = (),
) -> dict[uuid.UUID, dict[str, ScalarValue]]:
    """Canonical per-session scalar values for the registry (§3.6, DA-02/03/11)."""
    out: dict[uuid.UUID, dict[str, ScalarValue]] = {sid: {} for sid in sids}
    if not sids:
        return out

    def _put(
        sid: uuid.UUID,
        key: str,
        value: Any,
        state: str = "measured",
        denom: int | None = None,
    ) -> None:
        out[sid][key] = {"value": value, "state": state, "denominator": denom}

    ctx = _ScalarCtx(conn, sids, _put, comp_events)
    for family in _SCALAR_FAMILIES:
        family(ctx)

    return out


def _measured(sv: ScalarValue | None) -> float | None:
    """The measured value or None — missing/undefined states are not zeros."""
    if sv is None or sv.get("value") is None or sv.get("state") != "measured":
        return None
    return float(sv["value"])


def _sd(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def _quantile(xs: Sequence[float], q: float) -> float:
    idx = (len(xs) - 1) * q
    lo, hi = int(idx), min(int(idx) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (idx - lo)


def _desc(values: Sequence[float]) -> dict[str, Any]:
    xs = sorted(values)
    if not xs:
        return {
            "n": 0,
            "mean": None,
            "sd": None,
            "median": None,
            "q1": None,
            "q3": None,
            "min": None,
            "max": None,
        }
    return {
        "n": len(xs),
        "mean": sum(xs) / len(xs),
        "sd": _sd(xs),
        "median": float(median(xs)),
        "q1": _quantile(xs, 0.25),
        "q3": _quantile(xs, 0.75),
        "min": xs[0],
        "max": xs[-1],
    }


def _decode_value(response: Any) -> Any:
    if isinstance(response, Mapping):
        return response.get("value")
    return response


_OPT_SLUG = re.compile(r"[^a-z0-9]+")


def _opt_slug(option: str) -> str:
    """Stable slug for a declared choice option → derived binary item suffix."""
    return _OPT_SLUG.sub("_", option.lower()).strip("_")


def _new_cell(
    inst: str,
    iid: str,
    ver: Any,
    spec: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "partition": None,
        "partitions": [],
        "instrument_id": inst,
        "item_id": iid,
        "instrument_version": ver,
        "applies_to": None if spec is None else spec["applies_to"],
        "artifact_key": None if spec is None else spec["artifact_key"],
        "sessions_expected": 0,
        "counts": {
            "answered": 0,
            "skipped": 0,
            "missing": 0,
            "absent": 0,
            "invalid": 0,
            "unexpected": 0,
        },
        "_parts": set(),
        "_nums": [],
        "_cats": defaultdict(int),
    }


# ----- item descriptives (expected-key reconciliation, DA-06/12) -----------------------------


_ASSISTANT_ROW_ITEMS = ASSISTANT_INSTRUMENT_ITEMS | {ASSISTANT_COMPREHENSION_ITEM}


def _rows_show_assistant_era(rows: Iterable[dict[str, Any]]) -> bool:
    """Committed or synthesized item rows naming an assistant-only item prove
    the session was served them — independent of the caller's precomputed
    ``ai_sids`` or the deployment flag."""
    return any(str(r["item_id"]).split(".")[0] in _ASSISTANT_ROW_ITEMS for r in rows)


def _rows_show_pressure_era(rows: Iterable[dict[str, Any]]) -> bool:
    """A committed item row naming a pressure-only item proves the session
    was served it — independent of the caller's ``pressure_sids`` or the
    deployment flag."""
    from app.services.lifecycle import PRESSURE_INSTRUMENT_ITEMS

    return any(str(r["item_id"]).split(".")[0] in PRESSURE_INSTRUMENT_ITEMS for r in rows)


def _assistant_era_session(
    sid: uuid.UUID,
    ai_sids: frozenset[uuid.UUID] | None,
    comprehension_sids: frozenset[uuid.UUID] | None,
    *,
    ai_assistant_enabled: bool,
    row_evidence: bool = False,
) -> bool:
    """Was this session served the assistant items?

    Sessions with durable assistant evidence (a committed assistant item row, a
    comprehension attempt naming ``assistant_reads_team``, or an
    assistant-channel/ask event) were run flag-on regardless of the deployment's
    current flag. A session with a comprehension event and no assistant evidence
    is provably flag-off — flag-on servers record every rendered answer key.
    Sessions too early to carry evidence fall back to the deployment flag.
    """
    if row_evidence or sid in (ai_sids or frozenset()):
        return True
    if sid in (comprehension_sids or frozenset()):
        return False
    return ai_assistant_enabled


def _values_show_assistant_era(values: Mapping[str, float]) -> bool:
    """Validated numeric measure keys naming an assistant-only item are the
    same era evidence once item rows have been reduced to scalars."""
    return any(
        k.split(".", 2)[2].split("@")[0].split(".")[0] in _ASSISTANT_ROW_ITEMS
        for k in values
        if k.startswith("item.")
    )


def _pressure_era_session(
    visited: frozenset[str],
    *,
    time_pressure_enabled: bool,
    row_evidence: bool = False,
) -> bool:
    """Was this session served the pressure manipulation items?

    Sessions with durable evidence (a check-in stage visit or a committed
    ``tp_post_09``/``tp_post_11`` row) ran flag-on regardless of the
    deployment's current flag. A session that advanced PAST post_questionnaire
    with no pressure evidence is provably flag-off — flag-on servers require
    every served item answered or skipped before the stage completes.
    Sessions too early to carry evidence fall back to the deployment flag.
    """
    if row_evidence:
        return True
    if visited & {"checkin_collect", "checkin_wait", "checkin_reveal"}:
        return True
    if visited & {"open_ended", "debrief", "data_use_choice", "completion"}:
        return False
    return time_pressure_enabled


def _values_show_pressure_era(values: Mapping[str, float]) -> bool:
    """Scalar-reduced equivalent of a committed pressure-item row."""
    from app.services.lifecycle import PRESSURE_INSTRUMENT_ITEMS

    return any(
        k.split(".", 2)[2].split("@")[0].split(".")[0] in PRESSURE_INSTRUMENT_ITEMS
        for k in values
        if k.startswith("item.")
    )


def _bump(cell: dict[str, Any], opt_cells: list[dict[str, Any]], status: str) -> None:
    """Count one status on an item cell and on every derived option cell."""
    cell["counts"][status] += 1
    for oc in opt_cells:
        oc["counts"][status] += 1


def _tally_item_row(
    cell: dict[str, Any],
    opt_cells: list[dict[str, Any]],
    ispec: Mapping[str, Any],
    row: Mapping[str, Any],
    session_numeric: dict[str, float],
    inst: str,
) -> None:
    """Tally one committed item row into its cell (audit H1: lifted out of
    ``_item_cells``' deepest nesting). Answered values are validated against the
    bound definition (DA-06); numeric values feed the scalar registry; choice
    answers also fill the derived per-option binary cells. Free-text answers are
    count-only: the prose never enters numeric or category surfaces."""
    status = str(row["status"])
    if status in ("skipped", "missing"):
        _bump(cell, opt_cells, status)
        return
    if status != "answered":
        _bump(cell, opt_cells, "invalid")
        return
    v = _decode_value(row["response"])
    if not dashboard.valid_item_value(ispec, v):
        _bump(cell, opt_cells, "invalid")
        return
    cell["counts"]["answered"] += 1
    if (ispec.get("response") or {}).get("type") == "free_text":
        return
    if isinstance(v, (int, float)):
        cell["_nums"].append(float(v))
        session_numeric[f"item.{inst}.{cell['item_id']}"] = float(v)
        return
    cell["_cats"][str(v)] += 1
    for oc, o in zip(opt_cells, ispec.get("options") or [], strict=True):
        oc["counts"]["answered"] += 1
        bv = 1.0 if str(v) == o else 0.0
        oc["_nums"].append(bv)
        session_numeric[f"item.{inst}.{oc['item_id']}"] = bv


def _finalize_cell(cell: dict[str, Any]) -> dict[str, Any]:
    """Turn accumulator fields into reported fields: partitions, numeric
    summary and distribution, category counts."""
    parts = sorted(cell.pop("_parts"))
    cell["partitions"] = parts
    cell["partition"] = parts[0] if len(parts) == 1 else "pooled"
    nums: list[float] = cell.pop("_nums")
    cats: dict[str, int] = cell.pop("_cats")
    if nums:
        cell["numeric"] = _desc(nums)
        dist: dict[float, int] = defaultdict(int)
        for v in nums:
            dist[v] += 1
        cell["distribution"] = {str(int(k2)): c for k2, c in sorted(dist.items())}
    if cats:
        cell["categories"] = dict(sorted(cats.items(), key=lambda kv: -kv[1]))
    return cell


def _item_cells(
    sessions: list[dict[str, Any]],
    store: ContentStore,
    item_rows: dict[uuid.UUID, list[dict[str, Any]]],
    visited_by_sid: Mapping[uuid.UUID, frozenset[str]] | None = None,
    comprehension_sids: frozenset[uuid.UUID] | None = None,
    ai_sids: frozenset[uuid.UUID] | None = None,
    *,
    flags: FeatureFlags = DEFAULT_FLAGS,
) -> tuple[list[dict[str, Any]], dict[uuid.UUID, dict[str, float]]]:
    """Per-(instrument@version, item) reconciliation, pooled across partitions.

    Rows pool whenever sessions bind the *identical* instrument artifact
    version — the version pins the wording, so pooled cells are provably the
    same question (a factorial-era manifest difference never merges cells; a
    mid-round build redeploy does not split them). ``partition`` reports the
    single contributing partition or ``pooled``; ``partitions`` lists every
    contributing strict partition for auditability.

    Expected item keys come from each session's *bound* artifact versions
    (``dashboard.expected_items``): an item the binding never declared is
    ``unexpected``; an expected item with no committed row is ``absent``;
    answered values are validated against the bound definition (DA-06) so
    out-of-range numbers and undeclared strings count as ``invalid`` — never
    averaged, never echoed into the response. The code-defined ``open_ended``
    instrument joins the expected set once the session visited that stage —
    its free-text responses count as answered/invalid only; the prose never
    enters numeric or category surfaces (participant text is withheld content).
    ``comprehension_sids`` holds sessions with at least one comprehension
    event — their first-attempt answers arrive as synthesized item rows so the
    gate items reconcile through the same machinery (categorical
    true/false cells plus derived per-option binary rows).
    Returns the cell rows plus validated per-session numeric values for the
    item-derived scalar registry.
    """
    cells: dict[tuple[str, Any, str], dict[str, Any]] = {}
    numeric_values: dict[uuid.UUID, dict[str, float]] = defaultdict(dict)
    for sess in sessions:
        sid = sess["session_id"]
        part = dashboard.partition_of(sess)
        got = item_rows.get(sid, [])
        expected = dashboard.expected_items(
            sess,
            store,
            include_open_ended="open_ended" in (visited_by_sid or {}).get(sid, frozenset()),
            include_comprehension=sid in (comprehension_sids or frozenset()),
            ai_assistant=_assistant_era_session(
                sid,
                ai_sids,
                comprehension_sids,
                ai_assistant_enabled=flags.ai_assistant,
                row_evidence=_rows_show_assistant_era(got),
            ),
            time_pressure=_pressure_era_session(
                (visited_by_sid or {}).get(sid, frozenset()),
                time_pressure_enabled=time_pressure_for_arm(
                    sess.get("arm"), enabled=flags.time_pressure
                ),
                row_evidence=_rows_show_pressure_era(got),
            ),
        )
        seen: set[tuple[str, str]] = set()
        for inst, spec in expected.items():
            ver = spec["version"]
            for iid, ispec in spec["items"].items():
                cell = cells.setdefault((inst, ver, iid), _new_cell(inst, iid, ver, spec))
                cell["_parts"].add(part)
                cell["sessions_expected"] += 1
                row = next(
                    (r for r in got if r["instrument_id"] == inst and r["item_id"] == iid),
                    None,
                )
                # Choice items also emit one derived binary item per declared
                # option (``<item>@<option-slug>``, 1 = that option chosen) so a
                # categorical answer is analyzable as separate binary measures.
                opt_cells = [
                    cells.setdefault(
                        (inst, ver, f"{iid}@{_opt_slug(o)}"),
                        _new_cell(inst, f"{iid}@{_opt_slug(o)}", ver, spec)
                        | {"binary_option": o, "derived_from": iid},
                    )
                    for o in (ispec.get("options") or [])
                ]
                for oc in opt_cells:
                    oc["_parts"].add(part)
                    oc["sessions_expected"] += 1
                if row is None:
                    cell["counts"]["absent"] += 1
                    for oc in opt_cells:
                        oc["counts"]["absent"] += 1
                    continue
                seen.add((inst, iid))
                _tally_item_row(cell, opt_cells, ispec, row, numeric_values[sid], inst)
        for r in got:
            if (r["instrument_id"], r["item_id"]) in seen:
                continue
            inst, iid = str(r["instrument_id"]), str(r["item_id"])
            exp = expected.get(inst)
            if exp is not None and iid in exp["items"]:
                continue
            key = (inst, r["instrument_version"], iid)
            cell = cells.setdefault(key, _new_cell(inst, iid, r["instrument_version"], None))
            cell["_parts"].add(part)
            cell["counts"]["unexpected"] += 1

    rows: list[dict[str, Any]] = []
    for key in sorted(cells, key=lambda k: (k[0], str(k[1]), k[2])):
        rows.append(_finalize_cell(cells[key]))
    return rows, numeric_values


# ----- per-cohort descriptive machinery ----------------------------------------------------------


def _pool_groups(
    sessions: list[dict[str, Any]],
) -> dict[str, tuple[str | None, list[dict[str, Any]]]]:
    """Analytic pools: ``{pool_id: (equivalence_or_None, members)}``.

    A pool is a single compatibility partition — or a merge across partitions
    under a recorded ``PoolingEquivalence`` (``pooling_policy``)."""
    groups: dict[str, tuple[str | None, list[dict[str, Any]]]] = {}
    for sess in sessions:
        pid, eq = pooling_policy.pool_membership(sess)
        groups.setdefault(pid, (eq, []))[1].append(sess)
    return groups


def _pool_label(members: list[dict[str, Any]], eq: str | None, pid: str) -> str:
    """Partition-style label for one analytic pool: the strict partition label
    when the pool is a single compatibility partition, else a manifest-level
    label naming the recorded equivalence and the pool digest."""
    cks = {str(s.get("compatibility_key") or "unkeyed") for s in members}
    if len(cks) <= 1:
        return dashboard.partition_of(members[0])
    s0 = members[0]
    return (
        f"{s0.get('study_key')} | {s0.get('manifest_id')} "
        f"[{eq}; {len(cks)} compat keys; {pid[:15]}] | "
        f"{s0.get('mode')} | {s0.get('timing_mode')}"
    )


def _power_rows(
    sessions: list[dict[str, Any]],
    scalars: dict[uuid.UUID, dict[str, ScalarValue]],
    scalar_defs: Mapping[str, Mapping[str, str]],
) -> list[dict[str, Any]]:
    """Per-(pool, arm, measure) coverage/variance (DA-04): pools never cross
    study/compat/mode/timing boundaries except under a recorded equivalence."""
    pools = _pool_groups(sessions)
    labels = {pid: _pool_label(members, eq, pid) for pid, (eq, members) in pools.items()}
    cells: dict[tuple[str, str], list[uuid.UUID]] = defaultdict(list)
    for pid, (_eq, members) in pools.items():
        for sess in members:
            cells[(pid, str(sess.get("arm") or "unassigned"))].append(sess["session_id"])
    rows: list[dict[str, Any]] = []
    for key in sorted(scalar_defs):
        for (pid, arm), cell_sids in sorted(
            cells.items(), key=lambda kv: (labels[kv[0][0]], kv[0][1])
        ):
            vals = [
                v
                for sid in cell_sids
                if (v := _measured(scalars.get(sid, {}).get(key))) is not None
            ]
            rows.append(
                {
                    "partition": labels[pid],
                    "pool_id": pid,
                    "measure": key,
                    "unit": scalar_defs[key]["unit"],
                    "arm": arm,
                    "n_sessions": len(cell_sids),
                    "n_measured": len(vals),
                    "mean": None if not vals else sum(vals) / len(vals),
                    "sd": _sd(vals),
                    "missing": len(cell_sids) - len(vals),
                }
            )
    return rows


def _contrast_rows(
    sessions: list[dict[str, Any]],
    scalars: dict[uuid.UUID, dict[str, ScalarValue]],
    scalar_defs: Mapping[str, Mapping[str, str]],
) -> list[dict[str, Any]]:
    """Per-(compatibility-pool, arm, measure) descriptive contrasts: pooling
    partitions, per-arm descriptives, and the labeled direction/diff for the
    known pairs (high-low, high-mixed, eval-control)."""
    pools = _pool_groups(sessions)
    labels = {pid: _pool_label(members, eq, pid) for pid, (eq, members) in pools.items()}
    contrasts: list[dict[str, Any]] = []
    for pid, (_eq, part_rows) in sorted(pools.items(), key=lambda kv: labels[kv[0]]):
        part = labels[pid]
        by_arm: dict[str, list[uuid.UUID]] = defaultdict(list)
        for sess in part_rows:
            by_arm[str(sess.get("arm") or "unassigned")].append(sess["session_id"])
        arm_list = sorted(by_arm)
        for key in sorted(scalar_defs):
            cell: dict[str, Any] = {
                "partition": part,
                "pool_id": pid,
                "measure": key,
                "inference": {
                    "status": "analysis_not_configured",
                    "gated_on": ["D12", "D14", "O-13"],
                    "test": None,
                    "p": None,
                    "effect": None,
                    "ci": None,
                },
                "arms": {},
            }
            for arm in arm_list:
                vals = [
                    v
                    for sid in by_arm[arm]
                    if (v := _measured(scalars.get(sid, {}).get(key))) is not None
                ]
                cell["arms"][arm] = {
                    "n_sessions": len(by_arm[arm]),
                    "n_measured": len(vals),
                    **_desc(vals),
                }
            if {"high", "low"} <= set(arm_list):
                h, lo = cell["arms"]["high"], cell["arms"]["low"]
                cell["direction"] = "high_minus_low"
                cell["mean_diff"] = (
                    None if h["mean"] is None or lo["mean"] is None else h["mean"] - lo["mean"]
                )
            elif {"high", "mixed"} <= set(arm_list):
                # v15 workload cells: uniform-high vs split-level teammates.
                h, mx = cell["arms"]["high"], cell["arms"]["mixed"]
                cell["direction"] = "high_minus_mixed"
                cell["mean_diff"] = (
                    None if h["mean"] is None or mx["mean"] is None else h["mean"] - mx["mean"]
                )
            elif {"eval", "control"} <= set(arm_list):
                e, c = cell["arms"]["eval"], cell["arms"]["control"]
                cell["direction"] = "eval_minus_control"
                cell["mean_diff"] = (
                    None if e["mean"] is None or c["mean"] is None else e["mean"] - c["mean"]
                )
            else:
                cell["direction"] = None
                cell["mean_diff"] = None
            contrasts.append(cell)
    return contrasts


# ----- pooled cohort analysis (mechanism x arm factorial, descriptive) -----------

_MECHANISM_RE = re.compile(r"^tp_(ai|scripted)_")


def _mechanism_of(sess: Mapping[str, Any]) -> str:
    """Teammate mechanism from the bound manifest: ``tp_ai_*`` → ``ai``,
    ``tp_scripted_*`` → ``scripted``; any other manifest id is its own
    mechanism label so future profiles never mis-pool."""
    mid = str(sess.get("manifest_id") or "")
    m = _MECHANISM_RE.match(mid)
    return m.group(1) if m else (mid or "unknown")


def _pooled_sd(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Within-group pooled SD across two samples (descriptive)."""
    na, nb = len(a), len(b)
    if na + nb < 3:
        return None
    if na < 2 and nb < 2:
        return None
    va = 0.0 if na < 2 else sum((v - sum(a) / na) ** 2 for v in a) / (na - 1)
    vb = 0.0 if nb < 2 else sum((v - sum(b) / nb) ** 2 for v in b) / (nb - 1)
    df = max(na - 1, 0) + max(nb - 1, 0)
    return math.sqrt((va * max(na - 1, 0) + vb * max(nb - 1, 0)) / df) if df else None


def _mean_diff(a: Sequence[float], b: Sequence[float]) -> dict[str, Any]:
    """Descriptive mean difference a-b with pooled SD and Cohen's d — never a
    test result; inference stays gated on D12/D14/O-13."""
    if not a or not b:
        return {"diff": None, "pooled_sd": None, "cohen_d": None}
    diff = sum(a) / len(a) - sum(b) / len(b)
    psd = _pooled_sd(a, b)
    return {
        "diff": diff,
        "pooled_sd": psd,
        "cohen_d": None if not psd else diff / psd,
    }


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    return sxy / math.sqrt(sxx * syy)


def _pool_summary(
    pool_id: str,
    equivalence: str | None,
    sessions: list[dict[str, Any]],
    scalars: dict[uuid.UUID, dict[str, ScalarValue]],
    scalar_defs: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    """Descriptives for one compatibility pool.

    Every session in a pool shares a compatibility key — or an explicit
    ``PoolingEquivalence`` record — so mechanism is constant by construction
    and cells split on arm only (the ``mech|arm`` key shape is kept for the
    dashboard). The arm contrast is computed on exactly the sessions shown;
    nothing outside the pool contributes to any estimate here.
    """
    cells: dict[tuple[str, str], list[uuid.UUID]] = defaultdict(list)
    manifests: dict[str, set[str]] = defaultdict(set)
    partitions: set[str] = set()
    compat_keys: set[str] = set()
    for sess in sessions:
        mech = _mechanism_of(sess)
        arm = str(sess.get("arm") or "unassigned")
        cells[(mech, arm)].append(sess["session_id"])
        manifests[mech].add(str(sess.get("manifest_id") or ""))
        partitions.add(dashboard.partition_of(sess))
        ck = sess.get("compatibility_key")
        compat_keys.add(ck if isinstance(ck, str) and ck else "unkeyed")
    arm_list = sorted({a for _, a in cells} if cells else set())

    def vals(key: str, sids: Sequence[uuid.UUID]) -> list[float]:
        return [v for sid in sids if (v := _measured(scalars.get(sid, {}).get(key))) is not None]

    measures: list[dict[str, Any]] = []
    for key in sorted(scalar_defs):
        cell_desc: dict[str, Any] = {}
        cell_vals: dict[tuple[str, str], list[float]] = {}
        for (mech, arm), sids in cells.items():
            xs = vals(key, sids)
            cell_vals[(mech, arm)] = xs
            cell_desc[f"{mech}|{arm}"] = {
                "n_sessions": len(sids),
                "n_missing": len(sids) - len(xs),
                **_desc(xs),
            }
        pooled_sids = [sid for sids in cells.values() for sid in sids]
        contrasts: dict[str, Any] = {}
        if len(arm_list) == 2:
            a1 = next((a for a in _TREATED_ARMS if a in arm_list), arm_list[1])
            a2 = arm_list[0] if arm_list[0] != a1 else arm_list[1]
            xs1 = [v for (m, a), sids in cell_vals.items() if a == a1 for v in sids]
            xs2 = [v for (m, a), sids in cell_vals.items() if a == a2 for v in sids]
            contrasts[f"arm_{a1}_minus_{a2}"] = _mean_diff(xs1, xs2)
        measures.append(
            {
                "measure": key,
                "unit": scalar_defs[key]["unit"],
                "overall": {"n_sessions": len(pooled_sids), **_desc(vals(key, pooled_sids))},
                "cells": cell_desc,
                "contrasts": contrasts,
            }
        )

    # Pairwise Pearson over the fixed scalar registry (item-derived keys are
    # too sparse for a readable matrix; they remain in the per-measure cells).
    corr: list[dict[str, Any]] = []
    core = [k for k in sorted(_SCALARS) if k in scalar_defs]
    for i, ka in enumerate(core):
        for kb in core[i + 1 :]:
            pairs = [
                (va, vb)
                for sids in cells.values()
                for sid in sids
                if (va := _measured(scalars.get(sid, {}).get(ka))) is not None
                and (vb := _measured(scalars.get(sid, {}).get(kb))) is not None
            ]
            if len(pairs) < 3:
                continue
            corr.append(
                {
                    "a": ka,
                    "b": kb,
                    "n": len(pairs),
                    "r": _pearson([p[0] for p in pairs], [p[1] for p in pairs]),
                }
            )

    return {
        "pool_id": pool_id,
        "pool_kind": (
            "equivalence"
            if equivalence
            else ("unkeyed" if pool_id.startswith("unkeyed:") else "compatibility_key")
        ),
        "equivalence": equivalence,
        "n_sessions": len(sessions),
        "mechanisms": {m: sorted(mids) for m, mids in sorted(manifests.items())},
        "compatibility_keys": sorted(compat_keys),
        "cells": {f"{m}|{a}": len(sids) for (m, a), sids in sorted(cells.items())},
        "partitions_pooled": sorted(partitions),
        "measures": measures,
        "correlations": corr,
    }


def _cohort_analysis(
    sessions: list[dict[str, Any]],
    scalars: dict[uuid.UUID, dict[str, ScalarValue]],
    scalar_defs: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    """Partition-first pooled descriptives over the eligible cohort (BA-104).

    Sessions pool only within one compatibility key — or across keys under a
    recorded ``PoolingEquivalence`` (``app.services.pooling_policy``), which
    names the fields that must be identical and the compat components it
    waives. No record, no crossing: unrelated budgets, consent/payment,
    prompt, training or instrument versions can never contribute to a common
    estimate silently. A mechanism (ai/scripted) contrast is therefore never
    produced here — a factorial comparison across manifests would need its
    own recorded equivalence, and none exists.

    Each pool stays descriptive: cell descriptives, an arm mean difference
    with Cohen's d computed on exactly the shown sessions, and pairwise
    Pearson r — no tests, no p-values, no CIs on differences.
    """
    by_pool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    via_eq: dict[str, str | None] = {}
    for sess in sessions:
        pid, eq = pooling_policy.pool_membership(sess)
        by_pool[pid].append(sess)
        via_eq[pid] = eq

    pools = [
        _pool_summary(pid, via_eq[pid], sess_list, scalars, scalar_defs)
        for pid, sess_list in sorted(by_pool.items())
    ]
    return {
        "status": "descriptive_only",
        "n_sessions": len(sessions),
        "pooling": {
            "boundary": (
                "compatibility_key (INV-0208); cross-key pooling only under a "
                "recorded equivalence in app.services.pooling_policy"
            ),
            "equivalences": pooling_policy.equivalence_records(),
        },
        "pools": pools,
    }


def _condition_comparison(
    sessions: list[dict[str, Any]],
    scalars: dict[uuid.UUID, dict[str, ScalarValue]],
    scalar_defs: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    """Side-by-side descriptives over the eligible cohort's experimental
    conditions — one column per ``(manifest, arm)`` pair plus one ``overall``
    column for the whole eligible cohort.

    2026-10-18 correction: columns used to be raw compatibility pools, which
    split on the build checksum under mid-fielding deploys — two columns
    could carry the same manifest label and differ only by build, reading as
    a duplicate (v15 wave-1 showed two identical ``ovsome_thin`` columns).
    The comparison view exists to put the study's *conditions* side by side,
    so grouping is now (manifest, arm); each column still records the compat
    pool ids it spans so downstream visibility filtering keeps working.
    Compat pools still govern inference in the cohort/contrast surfaces —
    these cells are descriptive only.
    """
    by_cond: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cond_pools: dict[str, set[str]] = defaultdict(set)
    for sess in sessions:
        cid = f"{sess.get('manifest_id') or 'unkeyed'}|{sess.get('arm') or 'unassigned'}"
        by_cond[cid].append(sess)
        pid, _ = pooling_policy.pool_membership(sess)
        cond_pools[cid].add(pid)

    def _short(mid: str) -> str:
        # Keep the manifest version: ovnone_thin_v3 and ovnone_thin_v9 are
        # different conditions in the same cohort — strip only the prefix.
        return re.sub(r"^tp_(ai|scripted)_", "", mid).replace("_v", " v") or "unkeyed"

    def _label(members: list[dict[str, Any]], arm: str) -> str:
        mids = sorted({str(s_.get("manifest_id") or "") for s_ in members})
        base = "+".join(_short(m) for m in mids) or "unkeyed"
        return f"{base} ({arm})"

    columns = sorted(
        (
            {
                "pool_id": cid,
                "pool_ids": sorted(cond_pools[cid]),
                "label": _label(members, cid.split("|", 1)[1]),
                "n_sessions": len(members),
                # Newest session in the condition — the client uses it to
                # surface the current round's columns ahead of historical ones.
                "latest_at": max(
                    str(s_.get("created_at") or "") for s_ in members
                ),
                "manifests": sorted({str(s_.get("manifest_id") or "") for s_ in members}),
                "mechanisms": sorted({_mechanism_of(s_) for s_ in members}),
            }
            for cid, members in by_cond.items()
        ),
        key=lambda c: str(c["label"]),
    )
    pool_sids = {cid: [s_["session_id"] for s_ in members] for cid, members in by_cond.items()}
    all_sids = [s_["session_id"] for s_ in sessions]

    def _vals(key: str, sids: Iterable[uuid.UUID]) -> list[float]:
        return [v for sid in sids if (v := _measured(scalars.get(sid, {}).get(key))) is not None]

    rows = [
        {
            "measure": key,
            "unit": scalar_defs[key]["unit"],
            "overall": {
                "n_sessions": len(all_sids),
                **_desc(_vals(key, all_sids)),
            },
            "cells": {
                str(c["pool_id"]): {
                    "n_sessions": c["n_sessions"],
                    **_desc(_vals(key, pool_sids[str(c["pool_id"])])),
                }
                for c in columns
            },
        }
        for key in sorted(scalar_defs)
    ]
    return {
        "n_sessions": len(sessions),
        "columns": columns,
        "rows": rows,
    }


def _scalar_registry(
    sessions: list[dict[str, Any]],
    store: ContentStore,
    numeric_values: dict[uuid.UUID, dict[str, float]],
    visited_by_sid: Mapping[uuid.UUID, frozenset[str]] | None = None,
    comprehension_sids: frozenset[uuid.UUID] | None = None,
    ai_sids: frozenset[uuid.UUID] | None = None,
    *,
    flags: FeatureFlags = DEFAULT_FLAGS,
) -> dict[str, dict[str, str]]:
    """The measure registry for this cohort: the fixed scalars plus item-derived
    scalar keys for *numeric* bound items (``item.<instrument>.<item>``)."""
    defs = dict(_SCALARS)
    seen: set[str] = set()
    for sess in sessions:
        sid = sess["session_id"]
        expected = dashboard.expected_items(
            sess,
            store,
            include_open_ended="open_ended" in (visited_by_sid or {}).get(sid, frozenset()),
            include_comprehension=sid in (comprehension_sids or frozenset()),
            ai_assistant=_assistant_era_session(
                sid,
                ai_sids,
                comprehension_sids,
                ai_assistant_enabled=flags.ai_assistant,
                row_evidence=_values_show_assistant_era(numeric_values.get(sid, {})),
            ),
            time_pressure=_pressure_era_session(
                (visited_by_sid or {}).get(sid, frozenset()),
                time_pressure_enabled=time_pressure_for_arm(
                    sess.get("arm"), enabled=flags.time_pressure
                ),
                row_evidence=_values_show_pressure_era(numeric_values.get(sid, {})),
            ),
        )
        for inst, spec in expected.items():
            for iid, ispec in spec["items"].items():
                # Free-text items (code-defined open_ended) never become
                # numeric measures — they are count-only everywhere.
                if (ispec.get("response") or {}).get("type") == "free_text":
                    continue
                options = ispec.get("options")
                # Choice items register their derived binary measures
                # (``<item>@<option-slug>`` → share selecting that option);
                # numeric items register the item itself.
                keys = (
                    [
                        (f"item.{inst}.{iid}@{_opt_slug(o)}", f"= '{o}' (binary share)")
                        for o in options
                    ]
                    if options
                    else [(f"item.{inst}.{iid}", "(bound instrument version)")]
                )
                for key, desc_suffix in keys:
                    if key not in seen and any(
                        key in numeric_values.get(s["session_id"], {}) for s in sessions
                    ):
                        seen.add(key)
                        defs[key] = {
                            "unit": "share" if options else "likert",
                            "description": f"{inst} item {iid} {desc_suffix}",
                        }
    return defs


def _analysis_id(
    flt: DashboardFilter,
    eligible: Sequence[Mapping[str, Any]],
    diagnostic: Sequence[Mapping[str, Any]],
    du: Mapping[uuid.UUID, str],
) -> str:
    """Deterministic digest over inputs + settings — identical inputs produce
    the same id (§2.5); used to cite snapshot provenance on findings cards."""
    payload = {
        "schema": ANALYSIS_SCHEMA,
        "filters": flt.normalized(),
        "codebook_version": CODEBOOK_VERSION,
        "scalars": sorted(_SCALARS),
        "eligible": sorted(
            [
                str(r["session_id"]),
                int(r["revision"]),
                int(r["next_event_seq"]),
                du.get(r["session_id"]) or "",
            ]
            for r in eligible
        ),
        "diagnostic": sorted(str(r["session_id"]) for r in diagnostic),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


def analysis(
    conn: Connection,
    store: ContentStore,
    flt: DashboardFilter,
    *,
    flags: FeatureFlags = DEFAULT_FLAGS,
) -> dict[str, Any]:
    """Combined descriptive snapshot (§3.5-3.8) — one cohort, one read.

    ``ai_assistant_enabled``/``time_pressure_enabled`` are deployment flags;
    per-session era evidence overrides them so flag-on sessions analyzed under
    a since-disabled deployment keep their recorded items expected."""
    sessions = dashboard.select_cohort(conn, flt)
    tombstoned = dashboard.count_tombstoned(conn, flt)
    elig = dashboard.research_eligibility(conn, sessions)
    eligible = elig["eligible"]
    diagnostic = elig["diagnostic"]
    eids = dashboard.session_ids(eligible)
    dids = dashboard.session_ids(diagnostic)

    all_ids = eids + dids
    # BA-104: the allocation-time env-resolved compat components (provider/model
    # digest, prompt builder, claim checker, fallback, inference params) are not
    # session columns — they are durably pinned in the ``generation_policy_pinned``
    # event. Surface its canonical hash as ``policy_identity`` so pooling
    # equivalences can require them identical rather than waiving them. The
    # payload's ``build`` field is excluded: build identity is already carried
    # by the ``build_source_sha256`` session column, and baking it into the
    # policy hash would make the two impossible to waive independently (a
    # mid-round redeploy with zero behavior-bearing change would split pools).
    if eids:
        pinned: dict[uuid.UUID, Any] = {}
        for sid, payload in conn.execute(
            select(s.event.c.session_id, s.event.c.payload).where(
                s.event.c.session_id.in_(eids),
                s.event.c.type == "generation_policy_pinned",
            )
        ):
            pinned.setdefault(sid, payload)
        for sess in eligible:
            if sess["session_id"] in pinned:
                payload = pinned[sess["session_id"]]
                sess["policy_identity"] = content_hash(
                    {k: v for k, v in payload.items() if k != "build"}
                )
    # Comprehension events (seq-ordered): the scalars count attempts and read
    # the first attempt's missed list; the item layer synthesizes first-attempt
    # answers into item rows so the gate items reconcile like any other
    # instrument's cells.
    comp_events: list[tuple[uuid.UUID, Any, Any]] = []
    if all_ids:
        comp_events = [
            (sid, missed, answers)
            for sid, missed, answers in conn.execute(
                select(
                    s.event.c.session_id,
                    s.event.c.payload["missed"],
                    s.event.c.payload["answers"],
                )
                .where(
                    s.event.c.session_id.in_(all_ids),
                    s.event.c.type == "training_step",
                    s.event.c.payload["step"].astext == "comprehension",
                )
                .order_by(s.event.c.session_id, s.event.c.seq)
            )
        ]
    comp_first_answers = _comprehension_first_answers(
        (sid, answers) for sid, _m, answers in comp_events
    )
    comprehension_sids = frozenset(comp_first_answers)
    scalars = _load_scalars(conn, all_ids, comp_events)
    item_rows: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    # Visited stage keys per session — the code-defined open_ended instrument's
    # expected items are owed only once its stage was entered (same rule as the
    # readiness audit: entry anchor + every from_stage + every to_stage).
    visited_by_sid: dict[uuid.UUID, frozenset[str]] = defaultdict(lambda: frozenset({"entry"}))
    if all_ids:
        for r in conn.execute(
            select(s.item_response).where(s.item_response.c.session_id.in_(all_ids))
        ).mappings():
            item_rows[r["session_id"]].append(dict(r))
        trs: dict[uuid.UUID, set[str]] = defaultdict(lambda: {"entry"})
        for t in conn.execute(
            select(
                s.stage_transition.c.session_id,
                s.stage_transition.c.from_stage,
                s.stage_transition.c.to_stage,
            ).where(s.stage_transition.c.session_id.in_(all_ids))
        ):
            trs[t.session_id].update((t.from_stage, t.to_stage))
        for sid, keys in trs.items():
            visited_by_sid[sid] = frozenset(k for k in keys if k)

    # Assistant-era evidence per session (AI_ASSISTANT_ENABLED): first-attempt
    # comprehension answers/missed naming the gate item, a committed assistant
    # instrument row, or any assistant ask / assistant-channel message prove a
    # flag-on run even when the deployment flag is off now.
    ai_sids: set[uuid.UUID] = {
        sid
        for sid, answers in comp_first_answers.items()
        if ASSISTANT_COMPREHENSION_ITEM in answers
    }
    ai_sids |= {
        sid
        for sid, missed, _a in comp_events
        if isinstance(missed, list) and ASSISTANT_COMPREHENSION_ITEM in missed
    }
    ai_sids |= {
        sid
        for sid, rows in item_rows.items()
        for r in rows
        if str(r["item_id"]).split(".")[0] in ASSISTANT_INSTRUMENT_ITEMS
    }
    if all_ids:
        ai_sids |= {
            row.session_id
            for row in conn.execute(
                select(s.event.c.session_id)
                .where(
                    s.event.c.session_id.in_(all_ids),
                    or_(
                        s.event.c.type == "assistant_requested",
                        and_(
                            s.event.c.type.in_(("message_delivered", "message_accepted")),
                            s.event.c.payload["channel"].astext == "assistant",
                        ),
                    ),
                )
                .group_by(s.event.c.session_id)
            )
        }
    ai_sids_frozen = frozenset(ai_sids)

    # Synthesized first-attempt item rows: bools become "true"/"false" (the
    # choice-item surface), non-bool payloads pass through so provable garbage
    # lands in ``invalid`` rather than silently vanishing.
    from app.services.lifecycle import COMPREHENSION_VERSION

    for sid, answers in comp_first_answers.items():
        for iid, val in answers.items():
            if val is True:
                shown = "true"
            elif val is False:
                shown = "false"
            else:
                shown = str(val)
            item_rows[sid].append(
                {
                    "session_id": sid,
                    "instrument_id": COMPREHENSION_INSTRUMENT_ID,
                    "item_id": iid,
                    "instrument_version": COMPREHENSION_VERSION,
                    "status": "answered",
                    "response": {"value": shown},
                }
            )

    items, numeric_values = _item_cells(
        eligible,
        store,
        item_rows,
        visited_by_sid=visited_by_sid,
        comprehension_sids=comprehension_sids,
        ai_sids=ai_sids_frozen,
        flags=flags,
    )
    scalar_defs = _scalar_registry(
        eligible,
        store,
        numeric_values,
        visited_by_sid=visited_by_sid,
        comprehension_sids=comprehension_sids,
        ai_sids=ai_sids_frozen,
        flags=flags,
    )
    for sid, vals in numeric_values.items():
        for key, v in vals.items():
            if key in scalar_defs:
                scalars[sid][key] = {"value": v, "state": "measured", "denominator": None}

    power_rows = _power_rows(eligible, scalars, scalar_defs)
    contrasts = _contrast_rows(eligible, scalars, scalar_defs)
    cohort_analysis = _cohort_analysis(eligible, scalars, scalar_defs)
    condition_comparison = _condition_comparison(eligible, scalars, scalar_defs)

    # DA-13: the same descriptive machinery over the diagnostic cohort — qa/
    # preview/accelerated sessions that pass analytic completeness but can never
    # enter the research N. Identities are preserved by partition; inference
    # stays disabled; nothing here pools with live summaries.
    diag_items, diag_numeric = _item_cells(
        diagnostic,
        store,
        item_rows,
        visited_by_sid=visited_by_sid,
        comprehension_sids=comprehension_sids,
        ai_sids=ai_sids_frozen,
        flags=flags,
    )
    diag_defs = _scalar_registry(
        diagnostic,
        store,
        diag_numeric,
        visited_by_sid=visited_by_sid,
        comprehension_sids=comprehension_sids,
        ai_sids=ai_sids_frozen,
        flags=flags,
    )
    for sid, vals in diag_numeric.items():
        for key, v in vals.items():
            if key in diag_defs:
                scalars[sid][key] = {"value": v, "state": "measured", "denominator": None}
    diagnostics = {
        "cohort": "diagnostic",
        "label": "non-live descriptive diagnostics — qa/preview/accelerated traffic; "
        "never part of the research N",
        "n_sessions": len(diagnostic),
        "items": diag_items,
        "power_inputs": _power_rows(diagnostic, scalars, diag_defs),
        "contrasts": _contrast_rows(diagnostic, scalars, diag_defs),
    }

    # ---- §3.8 findings (neutral cards) ---------------------------------------------
    aid = _analysis_id(flt, eligible, diagnostic, elig["latest_data_use"])
    snapshot_note = f"eligible completer cohort N={len(eligible)}; snapshot digest {aid}"
    cards: list[dict[str, Any]] = []

    def _card(cid: str, keys: list[str], text: str, cohort: str = "research_descriptive") -> None:
        cards.append(
            {
                "card_id": cid,
                "measure_keys": keys,
                "cohort": cohort,
                "n_eligible": len(eligible),
                "analysis_id": aid,
                "text": text,
            }
        )

    if not eligible:
        _card(
            "empty_cohort",
            [],
            "No sessions meet the research-descriptive cohort definition under "
            "these filters (live mode, faithful timing, completed receipt, "
            "consent, scored result, eligible data-use). "
            + (
                f"{len(diagnostic)} diagnostic (non-live) session(s) are "
                "summarized separately in the diagnostics block."
                if diagnostic
                else "Operational sections remain available via the diagnostics views."
            ),
        )
    else:
        arms_of: dict[str, int] = defaultdict(int)
        for r in eligible:
            arms_of[str(r.get("arm") or "unassigned")] += 1
        _card(
            "cohort_coverage",
            [],
            "Eligible completers by arm: "
            + ", ".join(f"{a}={n}" for a, n in sorted(arms_of.items()))
            + f". {snapshot_note}. Descriptive only — no causal or "
            "hypothesis claims are made from this cohort.",
        )
        for cell in contrasts:
            if cell["direction"] is None:
                continue
            d = cell["mean_diff"]
            if d is None:
                continue
            a1, a2 = cell["direction"].split("_minus_")
            m1, m2 = cell["arms"][a1]["mean"], cell["arms"][a2]["mean"]
            n1, n2 = cell["arms"][a1]["n_measured"], cell["arms"][a2]["n_measured"]
            _card(
                f"contrast:{cell['partition']}:{cell['measure']}",
                [cell["measure"]],
                f"In this eligible completer cohort, {a1}-arm mean "
                f"{cell['measure']} was {m1:.2f} (measured n={n1}) vs "
                f"{m2:.2f} in {a2} (measured n={n2}); descriptive gap "
                f"{d:+.2f} — not a test result; no inference about "
                "significance or equivalence is implied.",
            )
    if diagnostic:
        _card(
            "diagnostic_cohort",
            [],
            f"{len(diagnostic)} diagnostic (non-live) session(s) passed "
            "analytic completeness under these filters. Their item and scalar "
            "summaries are labeled diagnostic and kept out of the research N — "
            "they describe harness/traffic health, not study evidence.",
            cohort="diagnostic",
        )

    env = dashboard.envelope(
        conn,
        flt,
        selected=len(sessions),
        tombstoned=tombstoned,
        sections={
            "items": "ok",
            "power_inputs": "descriptive_only",
            "contrasts": "descriptive_only",
            "findings": "ok",
            "diagnostics": "ok" if diagnostic else "empty",
        },
        extra={
            "eligible_research": len(eligible),
            "diagnostic_sessions": len(diagnostic),
            "excluded": elig["excluded"],
            "analysis_id": aid,
        },
    )
    return {
        **env,
        "schema": ANALYSIS_SCHEMA,
        "items": items,
        "power_inputs": {
            "status": "descriptive_only",
            "rows": power_rows,
            "mde_scenarios": {
                "status": "unsupported_model",
                "gated_on": ["D12", "D14", "O-13"],
                "note": "Prospective MDE/power needs a declared supported model "
                "and method; pilot variances are inputs, not guarantees.",
            },
        },
        "contrasts": adminviews.jsonable(contrasts),
        "cohort_analysis": adminviews.jsonable(cohort_analysis),
        "condition_comparison": adminviews.jsonable(condition_comparison),
        "diagnostics": adminviews.jsonable(diagnostics),
        "findings": cards,
        "measure_registry": [{"key": k, **scalar_defs[k]} for k in sorted(scalar_defs)],
        "notes": [
            "Items are single standalone measures — no alpha/PCA/composites "
            "(no multi-item scale exists in the bound instruments).",
            "Item counts reconcile against *bound* expected keys: absent = "
            "expected but unrecorded; invalid = answered but out-of-contract; "
            "unexpected = recorded but not bound.",
            "Suspicion-item frequencies distinguish the 'I am not sure' option "
            "from identity-probe events.",
            "Descriptive gaps are not significance tests; inference is gated "
            "on declared outcome families and D12/D14/O-13.",
            "Diagnostics summarize non-live traffic only — labeled separately "
            "and never pooled into the research N.",
        ],
    }
