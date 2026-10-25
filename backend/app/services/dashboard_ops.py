"""Study Admin Dashboard — operational builders (WP17 Phase A).

Four builders over the shared ``dashboard.select_cohort`` contract, each run under
one REPEATABLE READ snapshot per request:

- ``overview`` (§3.1): mode mix, stage occupancy vs ever-entered, outcome mix,
  manipulation anchors, arm balance, batch fill, operational fidelity, recent
  sessions — no single misleading linear funnel.
- ``readiness`` (§3.2): profile-aware coverage flags + paginated completer audit.
- ``timing`` (§3.3): stage visits (re-entry preserved), wall vs active durations,
  censoring, check-in segments, task-budget consumption.
- ``instruments`` (§3.4): bound artifact wording resolved through the
  ContentStore + codebook presentation.

Nothing here is research-causal; sections that cannot be computed report
``status: "unavailable"`` with a reason rather than fabricating numbers.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.engine import Connection

from app.db import schema as s
from app.db.tx import db_now
from app.domain import stage_graph as sg
from app.domain.manifests.store import ContentStore
from app.flags import DEFAULT_FLAGS, FeatureFlags
from app.measurement.codebook import (
    CODEBOOK_VERSION,
    MEASURES,
    TELEMETRY_VOCABULARY,
)
from app.services import adminviews, dashboard
from app.services.dashboard import DashboardFilter
from app.timing.policy import time_pressure_for_arm


def _rows_for(conn: Connection, table: Any, sids: list[uuid.UUID]) -> list[dict[str, Any]]:
    """All committed rows for the selected sessions — no silent truncation
    (DA-08). Cohort size, not row count, is the bounding knob: filters scope the
    session set and every related row for a selected session is always in
    scope, so aggregates here are always complete inputs."""
    if not sids:
        return []
    return [
        dict(r)
        for r in conn.execute(select(table).where(table.c.session_id.in_(sids))).mappings().all()
    ]


def _by_session(rows: Iterable[Mapping[str, Any]]) -> dict[uuid.UUID, list[dict[str, Any]]]:
    out: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[r["session_id"]].append(dict(r))
    return out


def _iqr(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    xs = sorted(values)
    mid = len(xs) // 2
    lower = xs[:mid]
    upper = xs[mid + (len(xs) % 2) :]
    return float(median(upper) - median(lower)) if lower and upper else None


def _dist(values: list[float]) -> dict[str, Any]:
    """Median/IQR summary with explicit n/missing/censored handling (§3.3)."""
    xs = sorted(values)
    if not xs:
        return {"n": 0, "median": None, "iqr": None}
    return {"n": len(xs), "median": float(median(xs)), "iqr": _iqr(xs)}


# ----- §3.1 overview -------------------------------------------------------------------------


def overview(
    conn: Connection,
    flt: DashboardFilter,
    *,
    recent_limit: int = dashboard.DEFAULT_RECENT,
    current_build: str | None = None,
) -> dict[str, Any]:
    """Dashboard overview for the filtered cohort: mode mix (qa/preview labelled as
    diagnostics, never in the research N), current stage occupancy vs ever-entered,
    and the outcome mix from durable completion receipts (sessions without one are
    reported by stage, never assumed complete) (§1.1.2, §3.1)."""
    sessions = dashboard.select_cohort(conn, flt)
    sids = dashboard.session_ids(sessions)
    tombstoned = dashboard.count_tombstoned(conn, flt)
    elig = dashboard.research_eligibility(conn, sessions)

    # Mode mix describes the *selected* scope; qa/preview rows are labelled
    # diagnostics and never silently enter the research N (§3.1).
    mode_counts: dict[str, int] = {}
    for r in sessions:
        mode_counts[r["mode"]] = mode_counts.get(r["mode"], 0) + 1

    # Current stage occupancy vs ever-entered (stage_transition targets).
    transitions = _rows_for(conn, s.stage_transition, sids)
    ever: dict[str, int] = {}
    ever_sessions: dict[str, set[uuid.UUID]] = defaultdict(set)
    for t in transitions:
        ever[t["to_stage"]] = ever.get(t["to_stage"], 0) + 1
        ever_sessions[t["to_stage"]].add(t["session_id"])
    occupancy: dict[str, int] = {}
    for r in sessions:
        occupancy[r["stage"]] = occupancy.get(r["stage"], 0) + 1
    stage_rows = [
        {
            "stage": stage,
            "current": occupancy.get(stage, 0),
            "ever_entered": len(ever_sessions.get(stage, set())),
        }
        for stage in s.STAGES
        if occupancy.get(stage, 0) or ever_sessions.get(stage)
    ]

    # Outcome mix from the durable receipt; sessions without a receipt are
    # reported by stage/terminal state, never assumed (§1.1.2).
    outcomes: dict[str, int] = {}
    anomalies: list[dict[str, Any]] = []
    receipts = elig["outcomes"]["receipts"]
    results = elig["outcomes"]["results"]
    for sess in sessions:
        sid = sess["session_id"]
        rec = receipts.get(sid)
        res = results.get(sid)
        if rec is not None:
            outcomes[rec["outcome"]] = outcomes.get(rec["outcome"], 0) + 1
            if rec["outcome"] == "completed" and sess["stage"] != "completion":
                anomalies.append(
                    {
                        "session_id": sid,
                        "flag": "receipt_completed_but_stage",
                        "stage": sess["stage"],
                    }
                )
            if rec["outcome"] == "completed" and (res is None or res["status"] != "scored"):
                anomalies.append(
                    {
                        "session_id": sid,
                        "flag": "receipt_completed_but_result",
                        "result_status": None if res is None else res["status"],
                    }
                )
        elif sess["stage"] == "completion":
            anomalies.append({"session_id": sid, "flag": "stage_completion_but_no_receipt"})
            outcomes["stage_completion_no_receipt"] = (
                outcomes.get("stage_completion_no_receipt", 0) + 1
            )
        else:
            key = sess["stage"] if sess["stage"] in s.TERMINAL_STAGES else "in_progress"
            outcomes[key] = outcomes.get(key, 0) + 1

    # Manipulation exposure anchors (§3.1): scheduled/committed/rendered/
    # acknowledged counts by kind — an exposure row alone is not proof of
    # rendering; each anchor is counted from its own seq column.
    exposures = _rows_for(conn, s.manipulation_exposure, sids)
    anchors: dict[str, dict[str, int]] = defaultdict(
        lambda: {"scheduled": 0, "committed": 0, "received": 0, "rendered": 0, "acknowledged": 0}
    )
    for e in exposures:
        a = anchors[e["kind"]]
        a["scheduled"] += 1
        for col in ("committed_seq", "received_seq", "rendered_seq", "acknowledged_seq"):
            if e.get(col) is not None:
                a[col.removesuffix("_seq")] += 1

    # Batch fill: registered batches with allocation counts for selected scope.
    batches = [
        dict(r)
        for r in conn.execute(select(s.batch).order_by(s.batch.c.created_at.desc()).limit(200))
        .mappings()
        .all()
    ]
    fill_rows = []
    for b in batches:
        alloc = conn.execute(
            select(
                func.count().label("slots"),
                func.count(s.allocation.c.session_id).label("used"),
            ).where(s.allocation.c.batch_id == b["batch_id"])
        ).one()
        scope_sessions = sum(1 for r in sessions if r["batch_id"] == b["batch_id"])
        fill_rows.append(
            {
                "batch_id": b["batch_id"],
                "manifest_id": b["manifest_id"],
                "mode": b["mode"],
                "enrollment_open": b["enrollment_open"],
                "slots": int(alloc.slots),
                "slots_used": int(alloc.used),
                "sessions_in_scope": scope_sessions,
            }
        )

    balance = adminviews.balance_rows(conn, sessions)

    # Operational fidelity by arm — model-call outcomes over selected sessions.
    fidelity: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    if sids:
        calls = conn.execute(
            select(s.model_call.c.session_id, s.model_call.c.status).where(
                s.model_call.c.session_id.in_(sids)
            )
        ).all()
        arm_of = {r["session_id"]: str(r.get("arm") or "unassigned") for r in sessions}
        for sid, status in calls:
            fidelity[arm_of[sid]][str(status)] += 1

    recent = [
        {
            "session_id": r["session_id"],
            "research_pid": r["research_pid"],
            "batch_id": r["batch_id"],
            "stage": r["stage"],
            "arm": r["arm"],
            "mode": r["mode"],
            "created_at": r["created_at"],
            "terminal_reason": r["terminal_reason"],
        }
        for r in sorted(sessions, key=lambda x: x["created_at"], reverse=True)[
            : max(1, min(recent_limit, dashboard.MAX_RECENT))
        ]
    ]

    env = dashboard.envelope(
        conn,
        flt,
        selected=len(sessions),
        tombstoned=tombstoned,
        sections={"status": "ok"},
        extra={
            "eligible_research": len(elig["eligible"]),
            "excluded": elig["excluded"],
        },
    )
    from app.services.assistant_monitor import monitor

    return {
        **env,
        "assistant_generation": monitor(conn, sessions, current_build=current_build),
        "mode_counts": mode_counts,
        "stage_table": stage_rows,
        "outcomes": outcomes,
        "outcome_anomalies": adminviews.jsonable(anomalies[:100]),
        "manipulation_anchors": adminviews.jsonable(dict(anchors)),
        "arm_balance": balance,
        "batch_fill": adminviews.jsonable(fill_rows),
        "model_call_status_by_arm": adminviews.jsonable({k: dict(v) for k, v in fidelity.items()}),
        "recent_sessions": adminviews.jsonable(recent),
        "recent_limit": max(1, min(recent_limit, dashboard.MAX_RECENT)),
    }


# ----- §3.2 readiness ------------------------------------------------------------------------


# Exceptional/early exits are never part of the completed-run path: a completed
# session could not have passed through them (DA-07).
_NON_COMPLETION_TARGETS = frozenset({"declined", "withdrawn", "technical_stop", "abandoned"})


def _successful_path_stages(variant: sg.StageVariant) -> frozenset[str]:
    """Stages reachable from ``entry`` via non-exceptional edges (DA-07).

    ``sg.EDGES`` unions every stage into the graph (each carries exceptional
    exits and structurally-absent variant keys), so a raw key union marks PE
    sessions missing TP check-ins and vice versa. Instead we walk forward from
    ``entry`` following only edges that stay on a completion path: for every
    variant this yields the actual successful route (e.g. solo_review only for
    review-first, peer_ratings only for PE, check-in stages only for TP)."""
    edges = sg.EDGES[variant]
    seen: set[str] = {"entry"}
    frontier = ["entry"]
    while frontier:
        stage = frontier.pop()
        for target in edges.get(stage, frozenset()):
            if target in _NON_COMPLETION_TARGETS or target in seen:
                continue
            seen.add(target)
            frontier.append(target)
    return frozenset(seen)


def _stage_order_violation(variant: sg.StageVariant, transitions: list[dict[str, Any]]) -> bool:
    """True when committed transitions step off the legal edge set (DA-07):
    replayed in order, each ``to_stage`` must be a legal target of the previous
    stage (re-entry loops are legal — ``task -> checkin_*`` -> task)."""
    current = "entry"
    for t in sorted(transitions, key=lambda x: (x["occurred_at"], x["revision"])):
        if t["to_stage"] not in sg.EDGES[variant].get(current, frozenset()):
            return True
        current = t["to_stage"]
    return False


_FLAG_DEFS: dict[str, tuple[str, str]] = {
    # code -> (severity, category)
    "stage_missing_required": ("review", "stage_coverage"),
    "stage_order_violation": ("review", "stage_coverage"),
    # Provable gate violation only: the session attempted the 2026-10-10
    # comprehension check, advanced past training, yet no zero-miss event
    # exists. Pre-gate sessions have no comprehension events and never flag.
    "comprehension_gate_bypassed": ("review", "stage_coverage"),
    "receipt_missing": ("review", "outcome_reconcile"),
    "receipt_outcome_mismatch": ("review", "outcome_reconcile"),
    "result_missing_or_unscored": ("review", "outcome_reconcile"),
    "item_expected_missing": ("review", "item_coverage"),
    "item_unexpected": ("info", "item_coverage"),
    "exposure_scheduled_not_rendered": ("info", "exposure"),
    "exposure_committed_not_rendered": ("review", "exposure"),
    "answer_final_missing": ("review", "outcome_reconcile"),
    "data_use_withdraw_all": ("info", "eligibility"),
    "session_in_progress": ("info", "stage_coverage"),
    "session_terminal_exception": ("info", "stage_coverage"),
    "coding_tables_unreadable": ("info", "availability"),
    # Telemetry health (cb-1.8, 2026-10-13): completer-only flags. A completed
    # session with zero telemetry rows means the client queue never persisted;
    # ``telemetry_file_missing`` fires only when epoch-bearing rows prove the
    # instrumented bundle served yet ``file_meta`` never announced — the D6
    # starvation check. ``telemetry_legacy_bundle`` marks pre-epoch rows (data
    # exists, pairing caveats apply); ``telemetry_multi_epoch`` marks reloads.
    "telemetry_absent": ("review", "telemetry"),
    "telemetry_receipts_absent": ("review", "telemetry"),
    "telemetry_file_missing": ("review", "telemetry"),
    "telemetry_legacy_bundle": ("info", "telemetry"),
    "telemetry_multi_epoch": ("info", "telemetry"),
}


def _flag_defs() -> list[dict[str, str]]:
    return [
        {"code": code, "severity": sev, "category": cat}
        for code, (sev, cat) in sorted(_FLAG_DEFS.items())
    ]


def _variant_for(sess: Mapping[str, Any], store: ContentStore) -> sg.StageVariant | None:
    stored = store.manifests.get(sess["manifest_id"])
    if stored is None:
        return None
    m = stored.manifest
    first = m.channels.phases[0].name if m.channels.phases else None
    try:
        return sg.variant_for(m.study_key, first)
    except sg.IllegalTransitionError:
        return None


@dataclass(frozen=True)
class _ReadinessCtx:
    """Cohort-wide inputs ``readiness`` bulk-loads once, read by every per-session
    audit (audit H1: extracted from the former 210-line loop body)."""

    comp_ai: Any
    comp_attempted: Any
    comp_passed: Any
    du: Any
    exposures: Any
    feature_flags: FeatureFlags
    finals: Any
    item_rows: Any
    msg_counts: Any
    oc: Any
    qa_rows: Any
    store: ContentStore
    tel_epoch_counts: Any
    tel_type_counts: Any
    transitions: Any


def _audit_item_coverage(
    sess: Mapping[str, Any],
    ctx: _ReadinessCtx,
    *,
    visited: set[str],
    pressure_era: bool,
    withdrawn: bool,
    flags: set[str],
) -> dict[str, Any]:
    """Item coverage against the bound artifacts' expectations; adds coverage
    flag codes to ``flags`` and returns the per-session item counts."""
    sid = sess["session_id"]
    # Item coverage vs bound artifact expectations (shared
    # dashboard.expected_items — the bound artifact version is the contract).
    item_counts: dict[str, Any]
    if withdrawn:
        flags.add("data_use_withdraw_all")
        item_counts = {"unavailable": "data_use_withdraw_all"}
    else:
        # UX round 8: the code-defined follow-up instrument is expected
        # only once the session actually entered the stage — a session
        # that never visited it owes nothing, and its rows (impossible)
        # would still count as unexpected below.
        # AI_ASSISTANT_ENABLED=0 sessions are never served the assistant
        # items, so they aren't expected either; a committed assistant item
        # row or an assistant comprehension answer proves the session ran
        # flag-on regardless of the deployment's current flag. Sessions
        # that attempted comprehension carry their era in the answers, so
        # the deployment flag only fills in for sessions too early to have
        # evidence.
        from app.services.lifecycle import ASSISTANT_INSTRUMENT_ITEMS

        ai_evidence = sid in ctx.comp_ai or any(
            str(r["item_id"]).split(".")[0] in ASSISTANT_INSTRUMENT_ITEMS
            for r in ctx.item_rows.get(sid, [])
        )
        ai_era = ai_evidence or (ctx.feature_flags.ai_assistant and sid not in ctx.comp_attempted)
        expected = dashboard.expected_items(
            sess,
            ctx.store,
            include_open_ended="open_ended" in visited,
            ai_assistant=ai_era,
            time_pressure=pressure_era,
        )
        got: dict[str, dict[str, str]] = defaultdict(dict)
        for r in ctx.item_rows.get(sid, []):
            got[r["instrument_id"]][r["item_id"]] = r["status"]
        item_counts = {
            "answered": 0,
            "skipped": 0,
            "missing": 0,
            "absent_expected": 0,
            "unexpected": 0,
        }
        for inst, spec in expected.items():
            for iid in spec["items"]:
                st = got.get(inst, {}).get(iid)
                if st is None:
                    item_counts["absent_expected"] += 1
                elif st in ("answered", "skipped", "missing"):
                    item_counts[st] += 1
        if sess["stage"] == "completion" and item_counts["absent_expected"]:
            flags.add("item_expected_missing")
        for inst, items in got.items():
            exp = expected.get(inst)
            for iid in items:
                if exp is None or iid not in exp["items"]:
                    item_counts["unexpected"] += 1
        if item_counts["unexpected"]:
            flags.add("item_unexpected")
    return item_counts


def _audit_telemetry_health(
    sess: Mapping[str, Any], ctx: _ReadinessCtx, *, flags: set[str]
) -> dict[str, Any]:
    """Telemetry health for completers (queue never flushed vs instrumentation
    absent); adds telemetry flag codes to ``flags`` and returns the summary."""
    sid = sess["session_id"]
    # Telemetry health (completers only): zero rows = the queue never
    # landed; committed chat with zero receive/render receipts = starvation
    # for the receipt kinds; epoch-bearing rows without file_meta = the
    # instrumented build served yet the case-file collector stayed silent.
    tel_counts = ctx.tel_type_counts.get(sid, {})
    tel_total = sum(tel_counts.values())
    n_epochs = ctx.tel_epoch_counts.get(sid, 0)
    receipts = sum(
        n
        for t, n in tel_counts.items()
        if t in ("telemetry_receive", "telemetry_render", "telemetry_render_first_final")
    )
    telemetry_summary = {
        "events": tel_total,
        "epochs": n_epochs,
        "file_meta": tel_counts.get("telemetry_file_meta", 0) > 0,
        "file_events": sum(n for t, n in tel_counts.items() if t.startswith("telemetry_file_"))
        + tel_counts.get("telemetry_share_attempt", 0),
        "receipts": receipts,
    }
    if sess["stage"] == "completion":
        if tel_total == 0:
            flags.add("telemetry_absent")
        else:
            if n_epochs == 0:
                flags.add("telemetry_legacy_bundle")
            else:
                if n_epochs > 1:
                    flags.add("telemetry_multi_epoch")
                if not telemetry_summary["file_meta"]:
                    flags.add("telemetry_file_missing")
            if ctx.msg_counts.get(sid, 0) > 0 and receipts == 0:
                flags.add("telemetry_receipts_absent")
    return telemetry_summary


def _audit_session(sess: Mapping[str, Any], ctx: _ReadinessCtx) -> dict[str, Any]:
    """One completer-audit row: stage/era/comprehension/outcome reconciliation,
    item coverage, exposure anchors and telemetry health, with the session's
    data-quality flag codes in stable order (DA-17)."""
    sid = sess["session_id"]
    flags: set[str] = set()
    variant = _variant_for(sess, ctx.store)
    withdrawn = ctx.du.get(sid) == "withdraw_all"

    # Stage coverage — visited stages come from transition rows plus the
    # creation-anchored ``entry`` stage and every from_stage (a transition
    # out of X proves X was visited).
    sess_trs = ctx.transitions.get(sid, [])
    visited = {"entry"} | {t["to_stage"] for t in sess_trs} | {t["from_stage"] for t in sess_trs}
    # TIME_PRESSURE_ENABLED era (checked early — stage coverage needs it):
    # check-in stage ctx.transitions or a committed pressure-item row prove
    # flag-on; advancing PAST post_questionnaire without them is provably
    # flag-off — flag-on servers require every served item answered or
    # skipped before the stage completes, so flag-on rows would exist.
    # Sessions too early to tell follow the deployment flag.
    from app.services.lifecycle import PRESSURE_INSTRUMENT_ITEMS

    pressure_evidence = bool(
        visited & {"checkin_collect", "checkin_wait", "checkin_reveal"}
    ) or any(
        str(r["item_id"]).split(".")[0] in PRESSURE_INSTRUMENT_ITEMS
        for r in ctx.item_rows.get(sid, [])
    )
    post_questionnaire_done = bool(
        visited & {"open_ended", "debrief", "data_use_choice", "completion"}
    )
    pressure_era = pressure_evidence or (
        time_pressure_for_arm(sess.get("arm"), enabled=ctx.feature_flags.time_pressure)
        and not post_questionnaire_done
    )
    # AI_QA_ENABLED era (2026-10-16): visiting ``ai_qa`` or a committed
    # exchange row proves flag-on; reaching ``scripted_connecting``
    # without either is provably flag-off — flag-on servers route every
    # training completion through ``ai_qa``. Sessions too early to tell
    # follow the deployment flag.
    qa_era = (
        "ai_qa" in visited
        or bool(ctx.qa_rows.get(sid))
        or (ctx.feature_flags.ai_qa and "scripted_connecting" not in visited)
    )
    if sess["stage"] in s.TERMINAL_STAGES:
        flags.add("session_terminal_exception")
    elif sess["stage"] != "completion":
        flags.add("session_in_progress")
    if variant is not None:
        if _stage_order_violation(variant, sess_trs):
            flags.add("stage_order_violation")
        # Required-and-unvisited only when the session finished its run;
        # for in-progress rows missing stages are just "not yet reached".
        # The required set is the variant's successful path — structurally
        # absent stages (PE check-ins, round-robin solo_review) never flag,
        # and neither do the check-in stages on flag-off-era sessions.
        if sess["stage"] == "completion":
            missing_required = _successful_path_stages(variant) - visited
            if not pressure_era:
                missing_required -= {"checkin_collect", "checkin_wait", "checkin_reveal"}
            if not qa_era:
                missing_required -= {"ai_qa"}
            if missing_required:
                flags.add("stage_missing_required")
    # Comprehension gate (2026-10-10): reaching scripted_connecting means
    # training completed — a zero-miss comprehension event must exist.
    # Only a provable violation flags: pre-gate sessions have no
    # comprehension events at all and are silent.
    if (
        sid in ctx.comp_attempted
        and sid not in ctx.comp_passed
        and visited & {"scripted_connecting", "ai_qa"}
    ):
        flags.add("comprehension_gate_bypassed")

    # Outcome reconciliation (§1.1.2).
    rec = ctx.oc["receipts"].get(sid)
    res = ctx.oc["results"].get(sid)
    if sess["stage"] == "completion" and rec is None:
        flags.add("receipt_missing")
    elif rec is not None and rec["outcome"] == "completed" and sess["stage"] != "completion":
        flags.add("receipt_outcome_mismatch")
    if rec is not None and rec["outcome"] == "completed":
        if res is None or res["status"] != "scored":
            flags.add("result_missing_or_unscored")
        if not withdrawn and not ctx.finals.get(sid):
            flags.add("answer_final_missing")

    item_counts = _audit_item_coverage(
        sess, ctx, visited=visited, pressure_era=pressure_era, withdrawn=withdrawn, flags=flags
    )

    # Exposure anchors — an exposure row is never proof of rendering (§3.2).
    for e in ctx.exposures.get(sid, []):
        if e["committed_seq"] is not None and e["rendered_seq"] is None:
            flags.add("exposure_committed_not_rendered")
            break
    else:
        if any(e["committed_seq"] is None for e in ctx.exposures.get(sid, [])):
            flags.add("exposure_scheduled_not_rendered")

    telemetry_summary = _audit_telemetry_health(sess, ctx, flags=flags)

    return {
        "session_id": str(sid),
        "research_pid": sess["research_pid"],
        "batch_id": sess["batch_id"],
        "stage": sess["stage"],
        "arm": sess["arm"],
        "mode": sess["mode"],
        "manifest_id": sess["manifest_id"],
        "variant": variant,
        "outcome": None if rec is None else rec["outcome"],
        "data_use": ctx.du.get(sid),
        "item_coverage": item_counts,
        "telemetry": telemetry_summary,
        # One flag code per session, stable order (DA-17): the summary
        # counts affected sessions, never item-count multiplicities.
        "flags": sorted(flags),
    }


def readiness(
    conn: Connection,
    store: ContentStore,
    flt: DashboardFilter,
    *,
    page_size: int = dashboard.DEFAULT_AUDIT_PAGE,
    offset: int = 0,
    audit_scope: str = "completers",
    feature_flags: FeatureFlags = DEFAULT_FLAGS,
) -> dict[str, Any]:
    """Data readiness for the filtered cohort: bulk-loads the cohort-wide inputs once,
    runs ``_audit_session`` per session (stage/era coverage, comprehension gate,
    outcome reconciliation, item coverage, exposure anchors, telemetry health),
    summarises flag codes by affected-session count, validates coding-table access
    honestly, and returns the paged completer audit. ``eligible_research`` here is
    the analytic N."""
    sessions = dashboard.select_cohort(conn, flt)
    sids = dashboard.session_ids(sessions)
    tombstoned = dashboard.count_tombstoned(conn, flt)

    transitions = _by_session(_rows_for(conn, s.stage_transition, sids))
    exposures = _by_session(_rows_for(conn, s.manipulation_exposure, sids))
    # Comprehension-gate state (2026-10-10 gate): per session, did it ever
    # attempt the check and did any attempt score zero misses. Sessions whose
    # build predates the gate have no comprehension events at all and are
    # never flagged — only the provable violation (attempted, advanced past
    # training, never passed) trips ``comprehension_gate_bypassed``.
    comp_attempted: set[uuid.UUID] = set()
    comp_passed: set[uuid.UUID] = set()
    # Sessions whose committed comprehension attempt names the assistant item —
    # durable proof the run predates AI_ASSISTANT_ENABLED=0 (flag-on servers
    # record every rendered answer key, so its absence is provably flag-off).
    comp_ai: set[uuid.UUID] = set()
    from app.services.lifecycle import ASSISTANT_COMPREHENSION_ITEM

    if sids:
        for sid, missed_json, answers in conn.execute(
            select(
                s.event.c.session_id,
                s.event.c.payload["missed"].astext,
                s.event.c.payload["answers"],
            ).where(
                s.event.c.session_id.in_(sids),
                s.event.c.type == "training_step",
                s.event.c.payload["step"].astext == "comprehension",
            )
        ):
            comp_attempted.add(sid)
            if missed_json == "[]":
                comp_passed.add(sid)
            if (isinstance(answers, Mapping) and ASSISTANT_COMPREHENSION_ITEM in answers) or (
                isinstance(missed_json, str) and ASSISTANT_COMPREHENSION_ITEM in missed_json
            ):
                comp_ai.add(sid)
    oc = dashboard.outcome_map(conn, sids)
    # Telemetry health (cb-1.8): per-session telemetry type counts, distinct
    # client epochs, and committed message counts (the receipts comparator).
    # ``client_epoch`` presence is the data-derived instrumented-build marker —
    # honest about era without pinning build SHAs that go stale every deploy.
    tel_type_counts: dict[uuid.UUID, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    tel_epoch_counts: dict[uuid.UUID, int] = {}
    msg_counts: dict[uuid.UUID, int] = {}
    if sids:
        for sid, t, n in conn.execute(
            select(s.event.c.session_id, s.event.c.type, func.count())
            .where(s.event.c.session_id.in_(sids), s.event.c.type.like("telemetry_%"))
            .group_by(s.event.c.session_id, s.event.c.type)
        ):
            tel_type_counts[sid][str(t)] = int(n)
        for sid, n in conn.execute(
            select(
                s.event.c.session_id,
                func.count(distinct(s.event.c.payload["client_epoch"].astext)),
            )
            .where(s.event.c.session_id.in_(sids), s.event.c.type.like("telemetry_%"))
            .group_by(s.event.c.session_id)
        ):
            tel_epoch_counts[sid] = int(n)
        for sid, n in conn.execute(
            select(s.message.c.session_id, func.count())
            .where(s.message.c.session_id.in_(sids))
            .group_by(s.message.c.session_id)
        ):
            msg_counts[sid] = int(n)
    # Data-use disposition is resolved BEFORE response-derived reads (DA-09):
    # a withdraw_all session's item/final rows are never loaded — its audit row
    # carries disposition metadata only.
    du = dashboard.latest_data_use(conn, sids)
    content_sids = [sid for sid in sids if du.get(sid) != "withdraw_all"]
    item_rows = _by_session(_rows_for(conn, s.item_response, content_sids))
    finals = _by_session(_rows_for(conn, s.answer_final, content_sids))
    qa_rows = _by_session(_rows_for(conn, s.qa_exchange, content_sids))

    audits: list[dict[str, Any]] = []
    summary: dict[str, int] = {code: 0 for code in _FLAG_DEFS}
    ctx = _ReadinessCtx(
        comp_ai=comp_ai,
        comp_attempted=comp_attempted,
        comp_passed=comp_passed,
        du=du,
        exposures=exposures,
        feature_flags=feature_flags,
        finals=finals,
        item_rows=item_rows,
        msg_counts=msg_counts,
        oc=oc,
        qa_rows=qa_rows,
        store=store,
        tel_epoch_counts=tel_epoch_counts,
        tel_type_counts=tel_type_counts,
        transitions=transitions,
    )
    for sess in sessions:
        row = _audit_session(sess, ctx)
        for f in row["flags"]:
            summary[f] = summary.get(f, 0) + 1
        audits.append(row)

    # Coding-table validation: the runtime role may not read coding tables —
    # report that honestly rather than passing silently (§3.2).
    coding_status: dict[str, Any] = {"status": "unavailable", "reason": "no_coding_pipeline"}

    # The completer audit (DA-20): rows are filtered by the validated selector
    # *before* pagination so the total matches the page contents. The default
    # is completed runs; in-progress/exceptional listings are explicit choices.
    scoped = [
        a
        for a in audits
        if (
            audit_scope == "all"
            or (audit_scope == "completers" and a["outcome"] == "completed")
            # ended-but-not-completed: a receipt with a non-completed outcome,
            # or a completion-stage row missing its receipt (reconciled above)
            or (
                audit_scope == "incomplete"
                and a["outcome"] is not None
                and a["outcome"] != "completed"
            )
            or (audit_scope == "exceptional" and a["stage"] in s.TERMINAL_STAGES)
        )
    ]
    total = len(scoped)
    page = scoped[offset : offset + page_size]
    env = dashboard.envelope(
        conn,
        flt,
        selected=len(sessions),
        tombstoned=tombstoned,
        sections={"status": "ok"},
    )
    return {
        **env,
        "flag_definitions": _flag_defs(),
        "flag_summary": summary,
        "coding_validation": coding_status,
        "audit": {
            "rows": page,
            "total": total,
            "offset": offset,
            "page_size": page_size,
            "scope": audit_scope,
            "sessions_audit_total": len(audits),
        },
    }


# ----- §3.3 timing ---------------------------------------------------------------------------


def _visits_for(sess: Mapping[str, Any], transitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct stage visits from ordered transitions (§3.3): entry anchored
    at ``created_at``; each visit = stage occupied between transitions. TP task
    re-entry after check-in produces a *second* task visit — visits are never
    collapsed into a single linear funnel."""
    trs = sorted(transitions, key=lambda t: (t["occurred_at"], t["revision"]))
    visits: list[dict[str, Any]] = []
    start = sess["created_at"]
    stage = "entry"
    for t in trs:
        visits.append(
            {
                "stage": stage,
                "start": start,
                "end": t["occurred_at"],
                "to_stage": t["to_stage"],
                "open": False,
            }
        )
        stage, start = t["to_stage"], t["occurred_at"]
    visits.append({"stage": stage, "start": start, "end": None, "to_stage": None, "open": True})
    return visits


def _active_ms_during(
    intervals: list[dict[str, Any]], start: datetime, end: datetime, factor: float
) -> float:
    """Active elapsed between wall instants: sum of running-interval overlap,
    scaled by the bound clock factor. Paused/other kinds contribute 0 — the
    task clock is the only authority (§3.3/§6.1)."""
    total = 0.0
    for iv in intervals:
        if iv["kind"] != "running":
            continue
        iv_end = iv["ended_at"] or end
        lo, hi = max(start, iv["started_at"]), min(end, iv_end)
        if hi > lo:
            total += (hi - lo).total_seconds() * 1000.0 * factor
    return total


# Stages the active clock can run during — non-task stages carry n/a active
# durations rather than a misleading zero (§3.3).
_CLOCK_STAGES = frozenset({"task", "solo_review"})


def timing(conn: Connection, flt: DashboardFilter) -> dict[str, Any]:
    """Page and stage timing for the filtered cohort: per (partition, arm, stage)
    duration summaries (arm kept as a separate split so pooled medians cannot hide a
    pressure difference, DA-19), active-task budget consumption against each
    manifest's budget, deadline drift, and per-session stage visits with open
    visits reported as censored."""
    sessions = dashboard.select_cohort(conn, flt)
    sids = dashboard.session_ids(sessions)
    tombstoned = dashboard.count_tombstoned(conn, flt)
    now = db_now(conn)

    transitions = _by_session(_rows_for(conn, s.stage_transition, sids))
    intervals = _by_session(_rows_for(conn, s.clock_interval, sids))

    # Per-(partition, arm, stage): compatibility partitions are arm-invariant by
    # construction, so arm is a *separate* split inside each partition (DA-19) —
    # pooling high/low durations into one median would hide a real difference.
    wall: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    active: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    censored: dict[tuple[str, str, str], int] = defaultdict(int)
    totals: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    budget_rows: list[dict[str, Any]] = []
    visit_rows: list[dict[str, Any]] = []
    # Per-manifest active budget (300 s from the 2026-10-09 revision, 360 s before).
    manifest_ids = sorted({str(x["manifest_id"]) for x in sessions if x.get("manifest_id")})
    budgets: dict[str, float] = {
        str(r.manifest_id): float(int(r.budget_s) * 1000)
        for r in conn.execute(
            select(
                s.experiment_manifest.c.manifest_id,
                s.experiment_manifest.c.content["timing"]["active_budget_s"].astext.label(
                    "budget_s"
                ),
            ).where(s.experiment_manifest.c.manifest_id.in_(manifest_ids))
        )
        if r.budget_s is not None
    }
    for sess in sessions:
        part = dashboard.partition_of(sess)
        arm = str(sess.get("arm") or "unassigned")
        factor = 1.0
        binding = sess.get("binding") or {}
        try:
            factor = float(binding.get("clock_speed_factor") or 1.0) or 1.0
        except (TypeError, ValueError):
            factor = 1.0
        task_active = 0.0
        task_wall = 0.0
        observed_censored = False  # the session's current visit is still open
        had_clock_visit = False
        visits = _visits_for(sess, transitions.get(sess["session_id"], []))
        for v in visits:
            key = (part, arm, v["stage"])
            end = v["end"] or now
            wall_ms = max(0.0, (end - v["start"]).total_seconds() * 1000.0)
            if v["stage"] in _CLOCK_STAGES:
                a = _active_ms_during(
                    intervals.get(sess["session_id"], []), v["start"], end, factor
                )
                task_active += a
                task_wall += wall_ms
                had_clock_visit = True
            if v["open"]:
                censored[key] += 1
                # Open visits are right-censored at the observation horizon —
                # counted, but never averaged as if complete. Their *observed*
                # consumption still counts toward the running budget total
                # (DA-18): an in-progress task is spending budget now.
                if v["stage"] in _CLOCK_STAGES:
                    observed_censored = True
                continue
            wall[key].append(wall_ms)
            if v["stage"] in _CLOCK_STAGES:
                active[key].append(a)
        # Total study time: wall ms from entry (created_at) to reaching the
        # final stage — only for sessions that reached one, so an in-progress
        # visit can't masquerade as a completed total (DA-18-style honesty).
        if sess["stage"] == "completion" or sess["stage"] in s.TERMINAL_STAGES:
            total_ms = max(0.0, (visits[-1]["start"] - sess["created_at"]).total_seconds() * 1000.0)
            totals[(part, arm, str(sess["stage"]))].append(total_ms)
        # Per-session stage visits — the wall table above aggregates; this is
        # the per-session detail (e.g. spotting the stage where an outlier
        # idled). Open visits report observed-so-far wall time.
        for v in visits:
            end = v["end"] or now
            visit_rows.append(
                {
                    "session_id": str(sess["session_id"]),
                    "partition": part,
                    "arm": arm,
                    "stage": v["stage"],
                    "wall_ms": round(max(0.0, (end - v["start"]).total_seconds() * 1000.0)),
                    "open": v["open"],
                }
            )
        # Budget consumption (DA-18): the total is *observed* consumption —
        # it includes any still-open task/solo_review visit and is explicitly
        # marked censored so it can never be read as a completed total. A
        # session mid-first-task-visit still appears (observed > 0).
        if had_clock_visit:
            budget_ms = budgets.get(str(sess.get("manifest_id")), 360_000.0)
            budget_rows.append(
                {
                    "session_id": str(sess["session_id"]),
                    "partition": part,
                    "arm": arm,
                    "observed_active_ms": round(task_active),
                    "observed_wall_ms": round(task_wall),
                    "budget_ms": budget_ms,
                    "clock_speed_factor": factor,
                    "censored": observed_censored,
                    # Over-budget is only *not-yet-decidable* while a clock
                    # stage is still open and under budget — it can still grow.
                    # Already-over is over regardless of censoring.
                    "over_budget": (
                        True
                        if task_active > budget_ms + 500
                        else (None if observed_censored else False)
                    ),
                }
            )

    # Deadline drift (2026-10-15): for every task_deadline event — the moment
    # the on-page timer hit 0:00 and answers froze — report how idle the
    # participant already was when it fired and how long until their next real
    # action. "Action" = a participant-actor command event; telemetry_* rows
    # are passive flushes (blur/dwell/scroll), never a click, so they don't
    # count on either side. A session still parked at answers_locked (or swept
    # abandoned) has no after-event — censored, not zero.
    meta = {
        sess["session_id"]: (dashboard.partition_of(sess), str(sess.get("arm") or "unassigned"))
        for sess in sessions
    }
    deadline_rows: list[dict[str, Any]] = []
    if sids:
        deadlines = [
            dict(r)
            for r in conn.execute(
                select(s.event.c.session_id, s.event.c.seq, s.event.c.server_time)
                .where(s.event.c.session_id.in_(sids), s.event.c.type == "task_deadline")
                .order_by(s.event.c.session_id, s.event.c.seq)
            )
            .mappings()
            .all()
        ]
        dl_sids = [r["session_id"] for r in deadlines]
        clicks = (
            _by_session(
                dict(r)
                for r in conn.execute(
                    select(
                        s.event.c.session_id, s.event.c.seq, s.event.c.type, s.event.c.server_time
                    )
                    .where(
                        s.event.c.session_id.in_(dl_sids),
                        s.event.c.actor == "participant",
                        ~s.event.c.type.like("telemetry%"),
                    )
                    .order_by(s.event.c.session_id, s.event.c.seq)
                ).mappings()
            )
            if dl_sids
            else {}
        )
        for dl in deadlines:
            part, arm = meta.get(dl["session_id"], ("?", "?"))
            evs = clicks.get(dl["session_id"], [])
            before = [e for e in evs if e["seq"] < dl["seq"]]
            after = [e for e in evs if e["seq"] > dl["seq"]]
            deadline_rows.append(
                {
                    "session_id": str(dl["session_id"]),
                    "partition": part,
                    "arm": arm,
                    "deadline_at": dl["server_time"].isoformat(),
                    "idle_before_ms": (
                        round(
                            (dl["server_time"] - before[-1]["server_time"]).total_seconds() * 1000.0
                        )
                        if before
                        else None
                    ),
                    "idle_before_type": before[-1]["type"] if before else None,
                    "next_action_ms": (
                        round(
                            (after[0]["server_time"] - dl["server_time"]).total_seconds() * 1000.0
                        )
                        if after
                        else None
                    ),
                    "next_action_type": after[0]["type"] if after else None,
                    "censored": not after,
                }
            )

    def _rows(
        wall_map: dict[tuple[str, str, str], list[float]],
    ) -> list[dict[str, Any]]:
        out = []
        for part, arm, stage in sorted(set(wall_map) | set(censored)):
            d = _dist(wall_map.get((part, arm, stage), []))
            out.append(
                {
                    "partition": part,
                    "arm": arm,
                    "stage": stage,
                    **d,
                    "censored": censored.get((part, arm, stage), 0),
                    "unit": "ms",
                }
            )
        return out

    env = dashboard.envelope(
        conn,
        flt,
        selected=len(sessions),
        tombstoned=tombstoned,
        sections={"status": "ok"},
    )
    checkin_stages = ("checkin_collect", "checkin_wait", "checkin_reveal")
    total_rows = [
        {
            "partition": part,
            "arm": arm,
            "stage": stage,
            **_dist(vals),
            "unit": "ms",
        }
        for (part, arm, stage), vals in sorted(totals.items())
    ]
    return {
        **env,
        "session_totals": total_rows,
        "wall_durations": _rows(wall),
        "active_durations": [r for r in _rows(active) if r["stage"] in _CLOCK_STAGES],
        "checkin": [r for r in _rows(wall) if r["stage"] in checkin_stages],
        # Per-session rows are bounded for transport — the bound is reported,
        # never silent (DA-08): consumers see exactly how many rows exist.
        "task_budget": budget_rows[:1000],
        "task_budget_total": len(budget_rows),
        "task_budget_truncated": len(budget_rows) > 1000,
        "stage_visits": visit_rows[:4000],
        "stage_visits_total": len(visit_rows),
        "stage_visits_truncated": len(visit_rows) > 4000,
        "deadline_drift": deadline_rows[:1000],
        "deadline_drift_total": len(deadline_rows),
        "deadline_drift_truncated": len(deadline_rows) > 1000,
        "notes": [
            "Open stage visits are right-censored at snapshot_time and counted, not averaged.",
            "Active durations are only defined for clock-running stages; other stages report n/a.",
            "Server timestamps bound stage occupancy — they are not browser reading time.",
            "Accelerated QA visits scale active elapsed by the bound clock_speed_factor.",
        ],
    }


# ----- §3.4 instruments ----------------------------------------------------------------------

# Artifact kinds whose *bodies* are reviewed participant-facing wording — safe
# and useful to publish verbatim in Survey Documentation (DA-15/DA-16).
# Everything else (``prompt`` model bodies, ``payment_policy`` compensation
# metadata, QA overlays, coding products, unknown kinds) is provenance-only:
# kind + counts, never the payload.
_COPY_KINDS = frozenset(
    {
        "instrument",
        "material_reference",
        "checkin_copy",
        "exposure_copy",
        "warning",
        "request_script",
        "voice_bank",
        "fallback_bank",
    }
)


def _ival(item: Any, attr: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(attr)
    return getattr(item, attr, None)


def _serialize_body(kind: Any, body: Any) -> dict[str, Any]:
    """Type-aware verbatim serializer for participant-facing copy (DA-15).

    Each copy kind emits its real named fields — never a Python repr and never
    a blind ``body.text`` fallback. Kinds outside ``_COPY_KINDS`` return
    metadata only (DA-16): no model-prompt or payment-policy payload ever
    leaves this serializer. ``pointer_only`` marks genuine unresolved
    material references (literal ``text`` absent), not missing-attr accidents.
    """
    k = str(kind or "")
    if k not in _COPY_KINDS:
        return {
            "kind": k or None,
            "restricted": True,
            "note": "body kind not published to dashboard",
        }

    def _dump(obj: Any) -> Any:
        return obj.model_dump() if hasattr(obj, "model_dump") else obj

    if k == "instrument":
        return {
            "kind": k,
            "applies_to": getattr(body, "applies_to", "session"),
            "order": getattr(body, "order", None),
            "response": _dump(getattr(body, "response", None)),
            "items": [
                {
                    "item_id": _ival(i, "item_id"),
                    "text": _ival(i, "text"),
                    "skippable": _ival(i, "skippable"),
                    "options": _ival(i, "options"),
                }
                for i in (getattr(body, "items", None) or [])
            ],
        }
    if k == "material_reference":
        text = getattr(body, "text", None)
        return {
            "kind": k,
            "material": getattr(body, "material", None),
            "reference_copy": getattr(body, "reference_copy", None),
            "freeze_decision": getattr(body, "freeze_decision", None),
            "pointer_only": text is None,
            # Verbatim structured copy (heading/sections/paragraphs/labels) —
            # model_dump keeps it canonical, not a repr.
            "text": _dump(text) if text is not None else None,
        }
    if k == "checkin_copy":
        return {
            "kind": k,
            "question": getattr(body, "question", None),
            "scale_min_label": getattr(body, "scale_min_label", None),
            "scale_max_label": getattr(body, "scale_max_label", None),
            "note": getattr(body, "note", None),
            "collecting": getattr(body, "collecting", None),
            "reveal_title": getattr(body, "reveal_title", None),
            "reveal_note": getattr(body, "reveal_note", None),
            "resume_label": getattr(body, "resume_label", None),
        }
    if k == "exposure_copy":
        return {
            "kind": k,
            "arm": getattr(body, "arm", None),
            "format": getattr(body, "format", None),
            "intro": getattr(body, "intro", None),
            "callout": getattr(body, "callout", None),
            "strip": getattr(body, "strip", None),
            "system_message": getattr(body, "system_message", None),
            "peer_intro": getattr(body, "peer_intro", None),
        }
    if k == "warning":
        return {
            "kind": k,
            "text": getattr(body, "text", None),
            "trigger": _dump(getattr(body, "trigger", None)),
            "low_time_state": _dump(getattr(body, "low_time_state", None)),
        }
    if k == "request_script":
        return {
            "kind": k,
            "templates": list(getattr(body, "templates", None) or []),
            "selection": getattr(body, "selection", None),
        }
    if k == "voice_bank":
        seats = getattr(body, "seats", None) or {}
        return {
            "kind": k,
            "repeat_policy": getattr(body, "repeat_policy", None),
            "seats": {seat: _dump(v) for seat, v in seats.items()},
        }
    if k == "fallback_bank":
        return {
            "kind": k,
            "classifier": getattr(body, "classifier", None),
            "rules": [
                {"when": _ival(r, "when"), "lines": _ival(r, "lines")}
                for r in (getattr(body, "rules", None) or [])
            ],
        }
    # Unreachable: k ∈ _COPY_KINDS covers every branch above — defensive.
    return {"kind": k, "restricted": True}


def instruments(conn: Connection, store: ContentStore, flt: DashboardFilter) -> dict[str, Any]:
    """Instrument materials actually bound by the filtered cohort: per (slot, artifact
    key, bound hash) session counts and resolution status through the baked
    ContentStore. Historical pointer-only or missing artifacts are labelled
    ``unresolved_pointer`` and never substituted with the latest version (§3.4)."""
    sessions = dashboard.select_cohort(conn, flt)
    tombstoned = dashboard.count_tombstoned(conn, flt)

    # Bound artifact usage across the selected cohort: (slot, key, bound_sha)
    # → session count + resolution status through the baked ContentStore.
    usage: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    for sess in sessions:
        binding_artifacts = (sess.get("binding") or {}).get("artifacts") or {}
        for slot, pair in binding_artifacts.items():
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            key, bound_sha = pair
            u = usage.setdefault(
                (slot, key, bound_sha),
                {
                    "slot": slot,
                    "key": key,
                    "bound_sha256": bound_sha,
                    "sessions": 0,
                    "status": None,
                },
            )
            u["sessions"] += 1

    rows: list[dict[str, Any]] = []
    for u in sorted(usage.values(), key=lambda x: (x["slot"], x["key"])):
        stored = store.artifacts.get(u["key"])
        if stored is None:
            # Historical pointer-only reference or artifact absent from this
            # build's store — label non-literal, never substitute latest (§3.4).
            rows.append(
                {
                    **u,
                    "status": "unresolved_pointer",
                    "artifact_id": None,
                    "version": None,
                    "hash_match": None,
                    "body": None,
                }
            )
            continue
        a = stored.artifact
        body = a.body
        hash_match = stored.sha256 == (u["bound_sha256"] or "")
        kind = getattr(body, "kind", None) or getattr(a, "kind", None)
        detail = _serialize_body(kind, body)
        rows.append(
            {
                **u,
                "status": "ok" if hash_match else "hash_mismatch",
                "artifact_id": a.artifact_id,
                "version": a.version,
                "hash_match": hash_match,
                "store_sha256": stored.sha256,
                "body": detail,
            }
        )

    # Codebook presentation — the generated measure registry, not a hand copy.
    codebook = {
        "codebook_version": CODEBOOK_VERSION,
        "measures": [
            {
                "key": m.key,
                "name": m.name,
                "source": m.source,
                "denominator": m.denominator,
                "missing_rule": m.missing_rule,
            }
            for m in MEASURES
        ],
        # Telemetry vocabulary (cb-1.8): the whitelisted client-event kinds
        # and their payload.data semantics — same registry CODEBOOK.md renders.
        "telemetry_kinds": [dict(k) for k in TELEMETRY_VOCABULARY],
        "note": "docs/CODEBOOK.md is generated presentation of this registry.",
    }

    # Code-defined participant-facing instruments: the comprehension gate's
    # answers persist as training_step events, never item_response, so it is
    # otherwise invisible on this tab. (The open_ended follow-up pair left
    # this list at BA-105 — it is now the bound artifact
    # ``open_ended.tp_followup@v1``; v10-era sessions hold legacy
    # ``open_ended_followup`` rows documented in the codebook.)
    from app.services.lifecycle import (
        COMPREHENSION_INSTRUMENT_ID,
        COMPREHENSION_ITEMS,
        COMPREHENSION_VERSION,
        comprehension_key,
    )

    # 2026-10-15 audit A01: the `same_crates` key is condition-dependent —
    # True only under the full-overlap task shape. Resolve which overlap
    # levels this cohort actually ran from the sessions' pinned manifest
    # tasks (immutable per manifest_id), and publish the effective key per
    # applicable level — a single boolean would misrepresent full-overlap
    # sessions or mixed cohorts. Uses the same helper `_training_step`
    # scores with; the canonical item's static `correct` stays False.
    cohort_overlaps: set[str] = set()
    cohort_manifest_ids = {sess["manifest_id"] for sess in sessions if sess.get("manifest_id")}
    if cohort_manifest_ids:
        for row in conn.execute(
            select(s.experiment_manifest.c.content["task"]["crate_overlap"].astext).where(
                s.experiment_manifest.c.manifest_id.in_(cohort_manifest_ids)
            )
        ):
            cohort_overlaps.add(row[0] or "independent")
    if not cohort_overlaps:
        cohort_overlaps.add("independent")
    keys_by_overlap = {
        level: comprehension_key(ai_assistant=True, overlap=level)
        for level in sorted(cohort_overlaps)
    }

    code_defined = [
        {
            # The comprehension gate (2026-10-10) is participant-facing copy
            # that lives in code — and its answers persist as training_step
            # events, never item_response, so it is otherwise invisible to
            # every artifact-driven surface. ``correct`` is published because
            # this is the researcher's audit of "what was the key" — the
            # participant API never serves it.
            "instrument_id": COMPREHENSION_INSTRUMENT_ID,
            "version": COMPREHENSION_VERSION,
            "origin": "code_defined",
            "applies_to": "session",
            "response": {"type": "true_false", "options": ["false", "true"]},
            "storage": (
                "training_step events (step=comprehension), not item_response; "
                "first-attempt answers synthesize item cells in analytics"
            ),
            # Overlap levels present in the filtered cohort — read each item's
            # `correct_by_overlap` against these; a single-level cohort has a
            # single effective key.
            "cohort_overlaps": sorted(cohort_overlaps),
            "items": [
                {
                    "item_id": i["item_id"],
                    "text": i["text"],
                    "correct": i["correct"],
                    # Effective key per overlap level present in this cohort —
                    # the value `_training_step` actually scored (same_crates
                    # is True under `full`, False elsewhere; every other item
                    # is identical across levels).
                    "correct_by_overlap": {
                        level: keys[i["item_id"]] for level, keys in keys_by_overlap.items()
                    },
                    "skippable": False,
                    "options": ["true", "false"],
                }
                for i in COMPREHENSION_ITEMS
            ],
        },
    ]

    env = dashboard.envelope(
        conn,
        flt,
        selected=len(sessions),
        tombstoned=tombstoned,
        sections={"status": "ok"},
    )
    return {
        **env,
        "artifacts": adminviews.jsonable(rows),
        "code_defined_instruments": code_defined,
        "codebook": codebook,
        "notes": [
            "{seat} is a documented placeholder rendered per teammate seat at runtime.",
            "unresolved_pointer = bound key absent from this build's store; "
            "never substituted with a newer artifact.",
            "code_defined_instruments lists participant-facing instruments that "
            "live in code, not artifacts (the comprehension gate) — version is "
            "the code constant, not a content pin. Comprehension answers "
            "persist as training_step events; its per-attempt misses surface "
            "as training.comprehension_* scalars. The open_ended follow-ups "
            "became artifact-bound at BA-105; pre-pin sessions show legacy "
            "open_ended_followup rows.",
        ],
    }
