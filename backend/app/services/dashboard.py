"""Study Admin Dashboard — shared cohort, filter, outcome and envelope contract (WP17).

Every dashboard builder works over the *same* selected sessions under one
``REPEATABLE READ READ ONLY`` snapshot per request (plan §1/§4). This module owns:

- ``DashboardFilter`` parsing/validation (§1.1) — batch/study/compatibility/mode
  and a half-open UTC ``created_at`` window. Related rows are never clipped by
  the window: once a session is selected, all of its committed rows are in scope.
- ``select_cohort`` — the operational cohort: sessions matching the filter,
  minus tombstoned sessions (``admin_audit.action='deletion_tombstone'``).
- Outcome reconciliation — ``completion_receipt.outcome`` is the durable outcome
  (§1.1 item 2); a successful session is expected to have ``stage='completion'``
  + receipt + scored ``result``. Disagreements are anomalies, not inferences.
- ``research_eligibility`` — the default research-descriptive cohort (§1.1
  item 3): completed receipt, affirmative consent, ``mode='live'``,
  ``timing_mode='faithful'``, latest data-use choice permitting measures.
- Envelope helpers — schema/builder versions, snapshot time, normalized
  filters, cohort counts, partition labels and per-section status.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.engine import Connection

from app.db import schema as s
from app.db.tx import db_now
from app.db.vocabulary import ELIGIBLE_DATA_USE
from app.domain.manifests.schema import SEATS
from app.domain.manifests.store import ContentStore
from app.export.aggregates import group_key
from app.measurement.codebook import CODEBOOK_VERSION
from app.measurement.scoring import SCORING_VERSION
from app.services.adminviews import jsonable

DASHBOARD_SCHEMA_VERSION = "dashboard.a1"

# Measure families the numeric item/behavior summaries consume — the
# ``use_no_free_text`` choice withholds free-text-derived content only; every
# measure this dashboard reports is exact-record (counts, ratings, answers),
# so that choice does NOT make the session ineligible. ``withdraw_all`` and a
# missing choice do (§1.1 item 3).

MAX_RECENT = 100
DEFAULT_RECENT = 25
MAX_AUDIT_PAGE = 200
DEFAULT_AUDIT_PAGE = 50

MODES = ("live", "qa", "preview", "all")

# The recognized filter names (DA-25). Anything else in the query string is a
# rejected unknown parameter — a misspelled bound must never silently widen the
# cohort. Route-specific extras (readiness page_size/offset/audit_scope,
# overview recent_limit) are passed via ``allowed_extra``.
_FILTER_KEYS = frozenset(
    {
        "batch_id",
        "batch_group_id",
        "study_key",
        "compatibility_key",
        "mode",
        "since",
        "until",
        "manifest_id",
    }
)


class FilterError(ValueError):
    """Raised on invalid/unsupported dashboard filters (→ HTTP 422)."""


@dataclass(frozen=True)
class DashboardFilter:
    batch_id: str | None = None
    batch_group_id: str | None = None
    study_key: str | None = None
    compatibility_key: str | None = None
    mode: str = "live"
    since: datetime | None = None
    until: datetime | None = None
    manifest_id: str | None = None

    def normalized(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "batch_group_id": self.batch_group_id,
            "study_key": self.study_key,
            "compatibility_key": self.compatibility_key,
            "mode": self.mode,
            "since": self.since.isoformat() if self.since else None,
            "until": self.until.isoformat() if self.until else None,
            "manifest_id": self.manifest_id,
        }


def _bounded(value: str | None, name: str, limit: int) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if len(v) > limit:
        raise FilterError(f"{name} is bounded to {limit} characters")
    return v


def _utc(value: str | None, name: str) -> datetime | None:
    if value is None or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FilterError(f"{name} must be ISO-8601 (e.g. 2026-10-01T00:00:00Z)") from exc
    if parsed.tzinfo is None:
        raise FilterError(f"{name} must carry a timezone (use trailing Z for UTC)")
    return parsed.astimezone(UTC)


def parse_filters(
    params: Mapping[str, Any], *, allowed_extra: Iterable[str] = ()
) -> DashboardFilter:
    """Parse/validate query params → ``DashboardFilter`` or raise ``FilterError``.

    Unknown parameter names are rejected (DA-25): a misspelled or obsolete
    filter must fail loudly instead of silently widening the cohort. Repeated
    keys are rejected for the same reason (``a=1&a=2`` is ambiguous scope)."""
    allowed = _FILTER_KEYS | set(allowed_extra)
    unknown = [k for k in params if k not in allowed]
    if unknown:
        raise FilterError(f"unsupported filter(s): {', '.join(sorted(unknown))}")
    multi = getattr(params, "multi_items", None)
    if callable(multi):
        seen: set[str] = set()
        for k, _v in multi():
            if k in seen:
                raise FilterError(f"duplicate filter parameter: {k}")
            seen.add(k)
    mode = (params.get("mode") or "live").strip()
    if mode not in MODES:
        raise FilterError(f"mode must be one of {MODES}")
    ck = _bounded(params.get("compatibility_key"), "compatibility_key", 80)
    if ck is not None and ck != "unkeyed" and not (ck.startswith("ck1:") and len(ck) == 68):
        raise FilterError("compatibility_key must be ck1:<64 hex> or 'unkeyed'")
    since, until = _utc(params.get("since"), "since"), _utc(params.get("until"), "until")
    if since and until and not since < until:
        raise FilterError("since must be before until")
    return DashboardFilter(
        batch_id=_bounded(params.get("batch_id"), "batch_id", 64),
        batch_group_id=_bounded(params.get("batch_group_id"), "batch_group_id", 64),
        study_key=_bounded(params.get("study_key"), "study_key", 64),
        compatibility_key=ck,
        mode=mode,
        since=since,
        until=until,
        manifest_id=_bounded(params.get("manifest_id"), "manifest_id", 64),
    )


def parse_page(params: Mapping[str, Any], *, default: int, maximum: int) -> int:
    raw = params.get("page_size")
    if raw is None or raw == "":
        return default
    try:
        n = int(raw)
    except (TypeError, ValueError) as exc:
        raise FilterError("page_size must be an integer") from exc
    if not 1 <= n <= maximum:
        raise FilterError(f"page_size must be 1..{maximum}")
    return n


def parse_offset(params: Mapping[str, Any]) -> int:
    raw = params.get("offset")
    if raw is None or raw == "":
        return 0
    try:
        n = int(raw)
    except (TypeError, ValueError) as exc:
        raise FilterError("offset must be an integer") from exc
    if n < 0:
        raise FilterError("offset must be >= 0")
    return n


# Completer-audit selections (DA-20): the readiness audit defaults to completed
# sessions; the selector makes incomplete/exceptional listings explicit rather
# than letting the oldest in-progress rows fill the page under a "completer"
# label.
AUDIT_SCOPES = ("completers", "incomplete", "exceptional", "all")


def parse_audit_scope(params: Mapping[str, Any]) -> str:
    raw = (params.get("audit_scope") or "completers").strip()
    if raw not in AUDIT_SCOPES:
        raise FilterError(f"audit_scope must be one of {AUDIT_SCOPES}")
    return raw


# ----- cohort selection ---------------------------------------------------------------------

_SESSION_COLS = (
    s.session.c.session_id,
    s.session.c.participant_id,
    s.session.c.batch_id,
    s.session.c.manifest_id,
    s.session.c.study_key,
    s.session.c.mode,
    s.session.c.timing_mode,
    s.session.c.arm,
    s.session.c.stage,
    s.session.c.terminal_reason,
    s.session.c.compatibility_key,
    s.session.c.manifest_sha256,
    s.session.c.bundle_sha256,
    s.session.c.build_source_sha256,
    s.session.c.behavior_source_sha256,
    s.session.c.binding,
    s.session.c.created_at,
    s.session.c.stage_entered_at,
    s.session.c.revision,
    s.session.c.next_event_seq,
)

_TOMBSTONE = (
    select(s.admin_audit.c.session_id)
    .where(
        s.admin_audit.c.session_id == s.session.c.session_id,
        s.admin_audit.c.action == "deletion_tombstone",
    )
    .exists()
)

# Test traffic must never count in the analytic cohort (2026-10-18, found live):
# smoke sessions arrive through the real live path (mode='live') and complete
# cleanly, so mode alone cannot exclude them. Two predicates cover both marks:
# the participant's research_pid carries a TEST_/QA_ tag (either bare or after
# the platform prefix — "prolific:TEST_*", "lab:TEST_*", "connect:TEST_*"), or
# the session's batch sits on a batch_group flagged test_only. Before this
# fix, 26 smoke completions were silently eligible and inflated the
# v3/v4-era columns in Findings/Contrasts/Exports.
_TEST_PARTICIPANT = or_(
    s.participant.c.research_pid.ilike("TEST_%"),
    s.participant.c.research_pid.ilike("%:TEST_%"),
    s.participant.c.research_pid.ilike("QA_%"),
    s.participant.c.research_pid.ilike("%:QA_%"),
)

_TEST_GROUP = (
    select(s.batch_group_member.c.batch_id)
    .join(
        s.batch_group,
        s.batch_group.c.group_id == s.batch_group_member.c.group_id,
    )
    .where(
        s.batch_group_member.c.batch_id == s.session.c.batch_id,
        s.batch_group.c.enrollment_policy["test_only"].as_boolean().is_(True),
    )
    .exists()
)


def _scope(q: Any, flt: DashboardFilter) -> Any:
    """The full cohort predicate set (DA-14): inclusion AND exclusion accounting
    share exactly these predicates, so a scoped tombstone count can never
    report deletions from unrelated builds/manifests."""
    if flt.batch_id:
        q = q.where(s.session.c.batch_id == flt.batch_id)
    if flt.batch_group_id:
        q = q.where(s.session.c.batch_group_id == flt.batch_group_id)
    if flt.study_key:
        q = q.where(s.session.c.study_key == flt.study_key)
    if flt.compatibility_key:
        if flt.compatibility_key == "unkeyed":
            q = q.where(s.session.c.compatibility_key.is_(None))
        else:
            q = q.where(s.session.c.compatibility_key == flt.compatibility_key)
    if flt.mode != "all":
        q = q.where(s.session.c.mode == flt.mode)
    if flt.manifest_id:
        q = q.where(s.session.c.manifest_id == flt.manifest_id)
    if flt.since:
        q = q.where(s.session.c.created_at >= flt.since)
    if flt.until:
        q = q.where(s.session.c.created_at < flt.until)
    return q


def select_cohort(conn: Connection, flt: DashboardFilter) -> list[dict[str, Any]]:
    """Operational cohort (§1.1 item 1): filtered sessions minus tombstoned."""
    q = _scope(
        select(*_SESSION_COLS, s.participant.c.research_pid).select_from(
            s.session.join(
                s.participant, s.session.c.participant_id == s.participant.c.participant_id
            )
        ),
        flt,
    )
    q = (
        q.where(~_TOMBSTONE, ~_TEST_PARTICIPANT, ~_TEST_GROUP)
        .order_by(s.session.c.created_at)
    )
    return [dict(r) for r in conn.execute(q).mappings().all()]


def count_tombstoned(conn: Connection, flt: DashboardFilter) -> int:
    """How many filter-matching sessions were excluded as tombstoned — same
    scope predicates as ``select_cohort`` (DA-14)."""
    q = _scope(select(func.count()).select_from(s.session).where(_TOMBSTONE), flt)
    return int(conn.execute(q).scalar_one())


# ----- outcome / eligibility -----------------------------------------------------------------


def per_session(
    conn: Connection, table: Any, sids: list[uuid.UUID], *cols: Any
) -> dict[uuid.UUID, list[dict[str, Any]]]:
    if not sids:
        return {}
    wanted = [table.c.session_id] + [
        c for c in cols if c is not table.c.session_id and c.name != "session_id"
    ]
    rows = (
        conn.execute(
            select(*wanted).where(table.c.session_id.in_(sids)).order_by(table.c.session_id)
        )
        .mappings()
        .all()
    )
    out: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["session_id"], []).append(dict(r))
    return out


def outcome_map(
    conn: Connection, sids: list[uuid.UUID]
) -> dict[str, dict[uuid.UUID, dict[str, Any]]]:
    """Durable outcome per session: receipt + stage/result reconciliation (§1.1.2)."""
    receipts = {
        sid: rows[0]
        for sid, rows in per_session(
            conn,
            s.completion_receipt,
            sids,
            s.completion_receipt.c.session_id,
            s.completion_receipt.c.outcome,
            s.completion_receipt.c.final_event_seq,
            s.completion_receipt.c.occurred_at,
        ).items()
    }
    results = {
        sid: rows[0]
        for sid, rows in per_session(
            conn,
            s.result,
            sids,
            s.result.c.session_id,
            s.result.c.status,
            s.result.c.correct_total,
            s.result.c.scoring_version,
        ).items()
    }
    return {"receipts": receipts, "results": results}


def consented_sessions(conn: Connection, sids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not sids:
        return set()
    return {
        r[0]
        for r in conn.execute(
            select(func.distinct(s.consent_event.c.session_id)).where(
                s.consent_event.c.session_id.in_(sids),
                s.consent_event.c.action == "consented",
            )
        ).all()
    }


def latest_data_use(conn: Connection, sids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Latest committed data-use choice per session (max ``choice_seq``)."""
    out: dict[uuid.UUID, str] = {}
    if not sids:
        return out
    rows = conn.execute(
        select(
            s.data_use_choice.c.session_id,
            s.data_use_choice.c.choice,
            s.data_use_choice.c.choice_seq,
        )
        .where(s.data_use_choice.c.session_id.in_(sids))
        .order_by(s.data_use_choice.c.session_id, s.data_use_choice.c.choice_seq.desc())
    ).all()
    for sid, choice, _seq in rows:
        out.setdefault(sid, choice)
    return out


def research_eligibility(conn: Connection, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """Split the operational cohort into the research-descriptive cohort, a
    separately labeled diagnostic cohort, and named exclusions (§1.1 item 3,
    DA-05, DA-13).

    A session is analytic-complete only when the durable chain agrees:
    ``completion_receipt.outcome='completed'`` + ``stage='completion'`` + a
    ``result`` row ``status='scored'`` under the pinned ``SCORING_VERSION``.
    Consent and an eligible latest data-use choice are also required.
    Sessions passing every one of those checks but failing live/faithful are
    ``diagnostic`` — they can drive explicitly labeled non-live descriptive
    summaries but never enter the research N."""
    sids = [r["session_id"] for r in sessions]
    oc = outcome_map(conn, sids)
    consented = consented_sessions(conn, sids)
    du = latest_data_use(conn, sids)
    eligible: list[dict[str, Any]] = []
    diagnostic: list[dict[str, Any]] = []
    excluded: dict[str, int] = {
        "no_completed_receipt": 0,
        "no_consent": 0,
        "stage_receipt_mismatch": 0,
        "result_missing_or_unscored": 0,
        "scoring_version_mismatch": 0,
        "not_live_mode": 0,
        "not_faithful_timing": 0,
        "data_use_ineligible": 0,
    }
    for sess in sessions:
        sid = sess["session_id"]
        rec = oc["receipts"].get(sid)
        if rec is None or rec["outcome"] != "completed":
            excluded["no_completed_receipt"] += 1
            continue
        if sid not in consented:
            excluded["no_consent"] += 1
            continue
        # Stage/receipt/result reconciliation (DA-05): inconsistent evidence is
        # excluded from both analytic cohorts and surfaced by readiness/overview
        # as an anomaly — never admitted on the receipt alone.
        if sess["stage"] != "completion":
            excluded["stage_receipt_mismatch"] += 1
            continue
        res = oc["results"].get(sid)
        if res is None or res["status"] != "scored":
            excluded["result_missing_or_unscored"] += 1
            continue
        if res["scoring_version"] != SCORING_VERSION:
            excluded["scoring_version_mismatch"] += 1
            continue
        # Diagnostic split before the data-use gate (DA-13): non-live or
        # non-faithful sessions are never research-eligible, so their data-use
        # choice cannot change the research N — they join the labeled
        # diagnostic cohort regardless. (Content-level views still gate on
        # data-use before loading bodies; see dashboard_ops.data_use_forbids.)
        if sess["mode"] != "live":
            excluded["not_live_mode"] += 1
            diagnostic.append(sess)
            continue
        if sess["timing_mode"] != "faithful":
            excluded["not_faithful_timing"] += 1
            diagnostic.append(sess)
            continue
        choice = du.get(sid)
        if choice not in ELIGIBLE_DATA_USE:
            excluded["data_use_ineligible"] += 1
            continue
        eligible.append(sess)
    return {
        "eligible": eligible,
        "diagnostic": diagnostic,
        "excluded": excluded,
        "outcomes": oc,
        "latest_data_use": du,
        "consented": consented,
    }


def partition_of(sess: Mapping[str, Any]) -> str:
    """Analytic partition label: study_key | group_key | mode | timing_mode."""
    return (
        f"{sess.get('study_key')} | {group_key(sess)} | "
        f"{sess.get('mode')} | {sess.get('timing_mode')}"
    )


def envelope(
    conn: Connection,
    flt: DashboardFilter,
    *,
    selected: int,
    tombstoned: int,
    sections: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": DASHBOARD_SCHEMA_VERSION,
        "codebook_version": CODEBOOK_VERSION,
        "snapshot_time": jsonable(db_now(conn)),
        "filters": flt.normalized(),
        "cohort": {
            "selected": selected,
            "tombstoned_excluded": tombstoned,
            **(dict(extra) if extra else {}),
        },
        "sections": dict(sections) if sections else {},
    }


def session_ids(sessions: Iterable[Mapping[str, Any]]) -> list[uuid.UUID]:
    return [r["session_id"] for r in sessions]


# ----- bound instrument expectations -----------------------------------------------------------

# Binding slots that carry response-bearing instruments (DA-12/DA-16): expected
# item keys and response validity come from the *bound* artifact version, never
# the latest registered one.
INSTRUMENT_SLOTS = ("instruments.post_task", "instruments.peer_ratings", "instruments.open_ended")


def open_ended_instrument_spec() -> dict[str, Any]:
    """Expected-items spec for the LEGACY code-defined ``open_ended`` follow-up
    instrument (UX round 8, v10-era rows only): sessions whose binding lacks
    ``instruments.open_ended`` stored rows under ``open_ended_followup`` and
    need this spec for item-status accounting. Post-BA-105 sessions resolve
    the bound ``open_ended.tp_followup`` artifact through the slot loop
    instead. Free-text items count answer status only; the response body is
    participant prose and is never validated into numeric/category surfaces."""
    from app.services.lifecycle import (
        LEGACY_OPEN_ENDED_ITEMS,
        LEGACY_OPEN_ENDED_MAX_CHARS,
        LEGACY_OPEN_ENDED_VERSION,
        OPEN_ENDED_INSTRUMENT_ID,
    )

    return {
        OPEN_ENDED_INSTRUMENT_ID: {
            "artifact_key": None,
            "version": LEGACY_OPEN_ENDED_VERSION,
            "applies_to": "session",
            "response": {
                "type": "free_text",
                "min": 0,
                "max": LEGACY_OPEN_ENDED_MAX_CHARS,
            },
            "items": {
                i["item_id"]: {
                    "options": None,
                    "response": {
                        "type": "free_text",
                        "min": 0,
                        "max": LEGACY_OPEN_ENDED_MAX_CHARS,
                    },
                }
                for i in LEGACY_OPEN_ENDED_ITEMS
            },
        }
    }


def comprehension_instrument_spec(*, ai_assistant: bool = True) -> dict[str, Any]:
    """Expected-items spec for the code-defined comprehension gate
    (2026-10-10). Answers persist as ``training_step`` events, never
    ``item_response`` — the analysis layer synthesizes first-attempt rows from
    the earliest attempt so the items reconcile through the same
    expected/unexpected/answered machinery as every other instrument. Items
    are modeled as ``true``/``false`` choice items, so the categorical
    pipeline emits the answer distribution plus one derived binary row per
    option — the first-attempt share answering each way. With
    ``AI_ASSISTANT_ENABLED=0`` the assistant item is not presented, so it is
    not expected of those sessions either."""
    from app.services.lifecycle import (
        ASSISTANT_COMPREHENSION_ITEM,
        COMPREHENSION_INSTRUMENT_ID,
        COMPREHENSION_ITEMS,
        COMPREHENSION_VERSION,
    )

    options = ["false", "true"]
    return {
        COMPREHENSION_INSTRUMENT_ID: {
            "artifact_key": None,
            "version": COMPREHENSION_VERSION,
            "applies_to": "session",
            "response": {"type": "choice"},
            "items": {
                i["item_id"]: {
                    "options": options,
                    "response": {"type": "choice"},
                }
                for i in COMPREHENSION_ITEMS
                if ai_assistant or i["item_id"] != ASSISTANT_COMPREHENSION_ITEM
            },
        }
    }


def expected_items(
    sess: Mapping[str, Any],
    store: ContentStore,
    *,
    include_open_ended: bool = False,
    include_comprehension: bool = False,
    ai_assistant: bool = True,
    time_pressure: bool = True,
) -> dict[str, dict[str, Any]]:
    """Expected instrument items for one session, resolved from its *bound*
    artifact keys (§3.2/§3.5).

    Returns ``{instrument_id: {"artifact_key", "version", "applies_to",
    "response": {"type","min","max"}, "items": {item_id: {"options"}}}}``.
    ``per_teammate_seat`` instruments expand item ids with a ``.<seat>`` suffix
    to match ``item_response.item_id``. Sessions whose binding names an artifact
    absent from this build's store simply omit that instrument — the readiness
    section reports the unresolved slot separately instead of pretending the
    items are known.

    ``include_open_ended`` adds the ``open_ended`` instrument — the bound
    ``instruments.open_ended`` slot when the manifest pins one (BA-105), else
    the legacy code-defined spec for v10-era sessions. Pass it only once the
    session actually entered that stage (a session that never visited owes
    nothing). ``include_comprehension`` adds the
    comprehension-gate items — pass it only once the session has a
    ``training_step`` comprehension event (the durable marker that it reached
    the check; pre-gate sessions and participants who never reached the page
    owe nothing)."""
    out: dict[str, dict[str, Any]] = {}
    artifacts = (sess.get("binding") or {}).get("artifacts") or {}
    for slot in INSTRUMENT_SLOTS:
        if slot == "instruments.open_ended" and not include_open_ended:
            continue
        pair = artifacts.get(slot)
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        key = pair[0]
        if not isinstance(key, str):
            continue
        stored = store.artifacts.get(key)
        body = getattr(getattr(stored, "artifact", None), "body", None)
        items = getattr(body, "items", None) or []
        applies_to = getattr(body, "applies_to", "session")
        resp = getattr(body, "response", None)

        def _v(obj: Any, attr: str) -> Any:
            return obj.get(attr) if isinstance(obj, Mapping) else getattr(obj, attr, None)

        resp_spec = (
            None
            if resp is None
            else {"type": _v(resp, "type"), "min": _v(resp, "min"), "max": _v(resp, "max")}
        )
        item_map: dict[str, dict[str, Any]] = {}
        for item in items:
            iid = _v(item, "item_id")
            if not iid:
                continue
            from app.services.lifecycle import instrument_item_visible

            if not instrument_item_visible(
                str(iid), ai_assistant=ai_assistant, time_pressure=time_pressure
            ):
                continue
            opts = _v(item, "options")
            spec = {"options": list(opts) if opts else None, "response": resp_spec}
            if applies_to == "per_teammate_seat":
                for seat in SEATS:
                    item_map[f"{iid}.{seat}"] = spec
            else:
                item_map[str(iid)] = spec
        instrument_id = getattr(getattr(stored, "artifact", None), "artifact_id", None) or str(key)
        out[str(instrument_id)] = {
            "artifact_key": str(key),
            "version": getattr(getattr(stored, "artifact", None), "version", None),
            "applies_to": applies_to,
            "response": resp_spec,
            "items": item_map,
        }
    if include_open_ended and "instruments.open_ended" not in artifacts:
        out.update(open_ended_instrument_spec())
    if include_comprehension:
        out.update(comprehension_instrument_spec(ai_assistant=ai_assistant))
    return out


def valid_item_value(spec: Mapping[str, Any], value: Any) -> bool:
    """Is ``value`` valid for the bound item definition (DA-06)?

    Choice items accept only exact declared option strings. Numeric
    likert/slider items accept only finite numbers inside the declared
    ``min``/``max`` (booleans are rejected — ``bool`` is a subclass of ``int``).
    ``free_text`` items (the code-defined open_ended instrument) accept only a
    non-blank string within the declared character ``max`` — valid means
    *countable as answered*, never that the body may be echoed; callers must
    keep free text out of numeric/category surfaces. Anything else is invalid
    and must be counted, never averaged or echoed."""
    options = spec.get("options")
    if options:
        return isinstance(value, str) and value in options
    resp = spec.get("response") or {}
    if resp.get("type") == "free_text":
        if not isinstance(value, str) or not value.strip():
            return False
        max_chars = resp.get("max")
        return not (isinstance(max_chars, int) and len(value) > max_chars)
    lo, hi = resp.get("min"), resp.get("max")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if not math.isfinite(float(value)):
        return False
    return not (isinstance(lo, int) and isinstance(hi, int) and not (lo <= value <= hi))


# ----- scoped chat transcripts (dashboard Team Chat tab) -------------------------------------

def _chat_cell_label(sess: Mapping[str, Any]) -> str:
    """``ovsome_thin v9 (high)`` — same manifest+arm label the condition
    comparison uses, so the chat filter reads like the Findings columns."""
    mid = str(sess.get("manifest_id") or "")
    short = mid.removeprefix("tp_ai_").removeprefix("tp_scripted_")
    ver = ""
    for tag in ("_v9", "_v8", "_v7", "_v6", "_v5", "_v4", "_v3", "_v13", "_v11"):
        if mid.endswith(tag):
            ver = f" {tag[1:]}"
            short = short.removesuffix(tag)
            break
    arm = str(sess.get("arm") or "")
    label = f"{short.strip() or mid}{ver}"
    # Bare-version manifests (tp_ai_v9) keep their version in `short` — avoid
    # the doubled "v9 v9" when both branches fire.
    label = label.strip()
    if label.split()[:1] == label.split()[1:2]:
        label = label.split()[0]
    return f"{label} ({arm})" if arm else label


def chat_transcripts(conn: Connection, flt: DashboardFilter) -> dict[str, Any]:
    """Team Chat tab payload: every scoped cohort session with its message
    transcript and enough session meta to filter/skim (condition, request
    counts, correctness, automation judgment, workload pins).

    Participant-authored bodies are withheld unless the session's latest
    data-use choice is ``use_all`` — same free-text gate as the exports.
    """
    sessions = select_cohort(conn, flt)
    if not sessions:
        return {"sessions": []}
    sids = [r["session_id"] for r in sessions]
    du = latest_data_use(conn, sids)

    msgs: dict[Any, list[dict[str, Any]]] = {}
    for m in conn.execute(
        select(
            s.message.c.session_id, s.message.c.actor, s.message.c.channel,
            s.message.c.body, s.message.c.crate, s.message.c.request_id,
            s.message.c.committed_seq, s.message.c.delivered_at,
            s.message.c.scheduled_for_active_ms,
        )
        .where(s.message.c.session_id.in_(sids))
        .order_by(s.message.c.session_id, s.message.c.committed_seq)
    ).mappings().all():
        msgs.setdefault(m["session_id"], []).append(dict(m))

    results = {
        r["session_id"]: r for r in conn.execute(
            select(
                s.result.c.session_id, s.result.c.correct_total,
                s.result.c.correct_ownfile, s.result.c.correct_teamdep,
            ).where(s.result.c.session_id.in_(sids))
        ).mappings().all()
    }

    items: dict[Any, dict[str, Any]] = {}
    for r in conn.execute(
        select(s.item_response.c.session_id, s.item_response.c.item_id,
               s.item_response.c.response)
        .where(s.item_response.c.session_id.in_(sids))
        .where(s.item_response.c.item_id.in_(
            ("tp_post_05", "tp_post_07", "tp_post_08", "tp_post_10", "tp_post_12")))
    ).mappings().all():
        resp = r["response"]
        items.setdefault(r["session_id"], {})[r["item_id"]] = (
            resp.get("value") if isinstance(resp, dict) else resp
        )

    shares = {
        r["session_id"]: int(r["n"]) for r in conn.execute(
            select(s.share.c.session_id, func.count().label("n"))
            .where(s.share.c.session_id.in_(sids))
            .group_by(s.share.c.session_id)
        ).mappings().all()
    }

    checkins: dict[Any, dict[str, Any]] = {}
    for r in conn.execute(
        select(
            s.manipulation_exposure.c.session_id,
            s.manipulation_exposure.c.own_rating,
            s.manipulation_exposure.c.assigned_values,
            s.manipulation_exposure.c.committed_seq,
        )
        .where(s.manipulation_exposure.c.session_id.in_(sids))
        .where(s.manipulation_exposure.c.kind == "pressure_checkin")
        .order_by(s.manipulation_exposure.c.committed_seq)
    ).mappings().all():
        checkins[r["session_id"]] = dict(r)  # latest wins

    out: list[dict[str, Any]] = []
    for sess in sessions:
        sid = sess["session_id"]
        raw = msgs.get(sid, [])
        t0 = None
        for m in raw:
            t = m.get("delivered_at")
            if t is not None and (t0 is None or t < t0):
                t0 = t
        free = str(du.get(sid) or "") == "use_all"
        mechanism = (
            "generated" if str(sess.get("manifest_id") or "").startswith("tp_ai")
            else "scripted"
            if str(sess.get("manifest_id") or "").startswith("tp_scripted")
            else "other"
        )
        mlist: list[dict[str, Any]] = []
        for m in raw:
            actor = str(m["actor"] or "")
            at = m.get("delivered_at")
            sec = int((at - t0).total_seconds()) if at is not None and t0 else 0
            body = m["body"]
            withheld = actor == "participant" and not free
            mlist.append({
                "s": sec, "a": actor, "ch": str(m["channel"] or ""),
                "b": None if withheld else body,
                "withheld": withheld,
                "crate": m["crate"],
                "req": m["request_id"] is not None,
                "sched": m["scheduled_for_active_ms"] is not None,
                "src": "system" if actor == "system" else (
                    "participant" if actor == "participant" else mechanism),
            })
        ck = checkins.get(sid) or {}
        av = ck.get("assigned_values") or {}
        res = results.get(sid) or {}
        it = items.get(sid) or {}
        light = None
        if isinstance(av, dict) and av:
            light = min(av, key=lambda k: av[k])
        out.append({
            "session_id": str(sid),
            "cell": _chat_cell_label(sess),
            "arm": str(sess.get("arm") or ""),
            "stage": str(sess.get("stage") or ""),
            "created_at": sess.get("created_at"),
            "n_msgs": len(mlist),
            "n_req_msgs": sum(1 for m in mlist if m["req"]),
            "n_shares": shares.get(sid, 0),
            "correct_total": res.get("correct_total"),
            "correct_own": res.get("correct_ownfile"),
            "correct_team": res.get("correct_teamdep"),
            "own_workload": ck.get("own_rating"),
            "assigned_values": av,
            "light_seat": light,
            "tp_post_05": it.get("tp_post_05"),
            "tp_post_07": it.get("tp_post_07"),
            "tp_post_08": it.get("tp_post_08"),
            "tp_post_10": it.get("tp_post_10"),
            "tp_post_12": it.get("tp_post_12"),
            "data_use": str(du.get(sid) or ""),
            "msgs": mlist,
        })
    return {"sessions": out, "n_sessions": len(out)}
