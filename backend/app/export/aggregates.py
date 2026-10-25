"""Compatibility-filtered aggregates (INV-1108, spec §9.5, hidden-bench HB-08).

Every aggregate produced here is keyed by ``compatibility_key`` — the full key covering
study, batch, manifest/bundle, build, generator, content, instruments, behavior policy,
prompts, validator, provider/model digest, inference params, metric/coding versions, mode
and timing (``app.domain.compat``). Rows never pool across keys: a session whose
``compatibility_key`` is NULL forms its own ``unkeyed`` group per ``(manifest_id,
manifest_sha256)`` so legacy/preview rows cannot silently contaminate a keyed analysis.

Every returned aggregate row carries ``filter`` (the exact filter set applied),
``n_sessions`` and the explicit denominator used — no post-treatment exclusions are applied
unless the caller names them in the filter.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

FIDELITY_CATEGORIES = (
    "empty",
    "fallback",
    "late",
    "suppressed",
    "failed",
    "off_schedule",
)

# model_call statuses that count toward each fidelity category (spec §9.3 fidelity row).
_MODEL_CALL_CATEGORY = {
    "empty": "empty",
    "invalid_response": "empty",
    "fallback": "fallback",
    "failed": "failed",
    "timeout": "failed",
    "validation_rejected": "failed",
    "quarantined": "failed",
    "cancelled": "suppressed",
}
_JOB_CATEGORY = {
    "suppressed": "suppressed",
    "failed": "failed",
    "expired": "late",
    "cancelled": "suppressed",
}
_MESSAGE_CATEGORY = {
    "suppressed": "suppressed",
    "failed": "failed",
    "expired": "late",
    "cancelled": "suppressed",
}

UNKEYED = "unkeyed"


def group_key(session: Mapping[str, Any]) -> str:
    """The pooling boundary for one session row — never silently broader than a
    compatibility key (spec §4.2, §9.5)."""
    key = session.get("compatibility_key")
    if key:
        return str(key)
    return f"{UNKEYED}:{session.get('manifest_id')}:{session.get('manifest_sha256')}"


def session_groups(
    sessions: Iterable[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in sessions:
        groups.setdefault(group_key(row), []).append(row)
    return groups


def _rate(numer: int, denom: int) -> float | None:
    return numer / denom if denom else None


def fidelity_rates(
    sessions: Iterable[Mapping[str, Any]],
    model_calls: Iterable[Mapping[str, Any]],
    messages: Iterable[Mapping[str, Any]],
    jobs: Iterable[Mapping[str, Any]],
    snapshots: Iterable[Mapping[str, Any]],
    *,
    filters: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Empty/fallback/late/suppressed/failed/off-schedule rates by arm, source, model and
    policy — one row per (compatibility_key, arm, source_kind, model, policy_ref) group.

    Sources: ``model_call`` (source = knowledge_snapshot purpose; model = provider/model
    digest), ``message`` (source = channel+actor seat; delivery statuses), ``scheduled_job``
    (source = job kind; late = finished after due, off_schedule = committed outside the
    declared due window). Denominators are explicit counts of rows in the group; a zero
    denominator yields a NULL rate, never 0.0 (spec §9.3)."""
    sess_by_id = {str(r["session_id"]): r for r in sessions}
    snap_purpose = {
        (str(r["session_id"]), str(r["snapshot_id"])): str(r["purpose"]) for r in snapshots
    }
    groups: dict[tuple[str, str, str, str, str], dict[str, int]] = {}

    def bump(group: tuple[str, str, str, str, str], category: str) -> None:
        g = groups.setdefault(group, {c: 0 for c in FIDELITY_CATEGORIES} | {"total": 0})
        g["total"] += 1
        if category in FIDELITY_CATEGORIES:
            g[category] += 1

    def session_meta(session_id: Any) -> tuple[str, str, str]:
        sess = sess_by_id.get(str(session_id), {})
        return group_key(sess), str(sess.get("arm") or ""), str(sess.get("binding_sha256") or "")

    for call in model_calls:
        ck, arm, binding = session_meta(call["session_id"])
        source = snap_purpose.get((str(call["session_id"]), str(call["snapshot_id"])), "unknown")
        model = f"{call.get('provider') or ''}/{call.get('model') or ''}"
        category = _MODEL_CALL_CATEGORY.get(str(call.get("status") or ""), "")
        bump((ck, arm, f"model_call:{source}", model, binding), category)
    for msg in messages:
        ck, arm, binding = session_meta(msg["session_id"])
        source = f"{msg.get('channel') or ''}:{msg.get('actor') or ''}"
        category = _MESSAGE_CATEGORY.get(str(msg.get("status") or ""), "")
        bump((ck, arm, f"message:{source}", "", binding), category)
    for job in jobs:
        ck, arm, binding = session_meta(job["session_id"])
        status = str(job.get("status") or "")
        category = _JOB_CATEGORY.get(status, "")
        # off-schedule: committed later than the declared due wall instant. Active-clock
        # lateness needs the job's completion event (not stored on the job row) and is
        # reported by the pending-interval products in WP11 measure modules, not here.
        if status in ("committed",) and job.get("clock_basis") == "wall":
            due_at, done_at = job.get("due_wall_at"), job.get("finished_at")
            if due_at is not None and done_at is not None and done_at > due_at:
                category = "off_schedule"
        bump((ck, arm, f"job:{job.get('kind') or ''}", "", binding), category)

    rows: list[dict[str, Any]] = []
    for (ck, arm, source, model, binding), counts in sorted(groups.items()):
        total = counts["total"]
        row: dict[str, Any] = {
            "compatibility_key": ck,
            "arm": arm,
            "source": source,
            "model": model,
            "policy_ref": binding,
            "n_total": total,
            "filter": dict(filters),
        }
        for category in FIDELITY_CATEGORIES:
            row[f"n_{category}"] = counts[category]
            row[f"rate_{category}"] = _rate(counts[category], total)
        rows.append(row)
    return rows


def incident_summary(
    sessions: Iterable[Mapping[str, Any]],
    incidents: Iterable[Mapping[str, Any]],
    *,
    filters: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Descriptive technical-incident summary keyed by compatibility_key x clock_effect x
    kind/surface (spec §9.5; INV-1108). Open intervals are counted and flagged censored —
    never silently assigned a zero or interpolated duration."""
    sess_by_id = {str(r["session_id"]): r for r in sessions}
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for inc in incidents:
        sess = sess_by_id.get(str(inc["session_id"]), {})
        key = (group_key(sess), str(inc.get("clock_effect") or ""), str(inc.get("kind") or ""))
        g = groups.setdefault(
            key,
            {"n_incidents": 0, "n_open": 0, "total_duration_ms": 0, "session_ids": set()},
        )
        g["n_incidents"] += 1
        g["session_ids"].add(str(inc["session_id"]))
        started, ended = inc.get("started_at"), inc.get("ended_at")
        if ended is None or started is None:
            g["n_open"] += 1
        else:
            g["total_duration_ms"] += int((ended - started).total_seconds() * 1000)
    rows: list[dict[str, Any]] = []
    for (ck, clock_effect, kind), g in sorted(groups.items()):
        n_closed = g["n_incidents"] - g["n_open"]
        rows.append(
            {
                "compatibility_key": ck,
                "clock_effect": clock_effect,
                "incident_kind": kind,
                "n_incidents": g["n_incidents"],
                "n_sessions": len(g["session_ids"]),
                "n_open_censored": g["n_open"],
                "total_duration_ms": g["total_duration_ms"] if n_closed else None,
                "mean_duration_ms": (g["total_duration_ms"] / n_closed if n_closed else None),
                "duration_denominator": "closed intervals only",
                "filter": dict(filters),
            }
        )
    return rows
