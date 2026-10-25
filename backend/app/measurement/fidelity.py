"""Realized teammate manipulation, measured from delivered output, not model attempts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.export.aggregates import group_key

GENERATED_REPLY_THRESHOLD = 0.80
MIN_FIDELITY_SESSIONS = 2
_CONDITIONS = {
    "model_with_validated_fallback": "ai",
    "deterministic_only": "scripted",
}


def teammate_fidelity(
    sessions: Iterable[Mapping[str, Any]],
    manifests: Iterable[Mapping[str, Any]],
    model_calls: Iterable[Mapping[str, Any]],
    messages: Iterable[Mapping[str, Any]],
    outputs: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """One row per session. Only output-audited, delivered JT/PM team replies count.

    Scripted requests, record shares, acknowledgements, disclosures and assistant replies
    are not the model-backed teammate manipulation. Retries are not deliveries. A
    succeeded call whose delivered text was substituted is a fallback, not generated.
    Missing audit linkage yields a NULL share, never an invented successful reply.
    """
    conditions = {
        str(m["manifest_id"]): _CONDITIONS.get(
            (m.get("content") or {}).get("teammates", {}).get("social_generation")
        )
        for m in manifests
    }
    calls = {(str(c["session_id"]), str(c["call_id"])): c for c in model_calls}
    delivered = {
        (str(m["session_id"]), str(m["message_id"]))
        for m in messages
        if m.get("actor") in ("JT", "PM")
        and m.get("channel") == "team"
        and m.get("status") == "delivered"
    }
    counts: dict[str, dict[str, int]] = {}
    seen: set[tuple[str, str]] = set()
    for output in outputs:
        sid = str(output["session_id"])
        key = (sid, str(output.get("message_id")))
        if key not in delivered or key in seen:
            continue
        seen.add(key)
        counter = counts.setdefault(sid, {"generated": 0, "fallback": 0, "unknown": 0})
        fallback = output.get("fallback")
        call = calls.get((sid, str(output.get("call_id"))))
        if fallback and fallback != "none":
            counter["fallback"] += 1
        elif fallback == "none" and call is not None and call.get("status") == "succeeded":
            counter["generated"] += 1
        else:
            counter["unknown"] += 1

    rows = []
    for session in sessions:
        sid = str(session["session_id"])
        condition = conditions.get(str(session.get("manifest_id")))
        counter = counts.get(sid, {"generated": 0, "fallback": 0, "unknown": 0})
        total = sum(counter.values())
        state = (
            "not_applicable"
            if condition == "scripted"
            else (
                "missing_data"
                if condition is None or counter["unknown"]
                else ("measured" if total else "undefined_denominator")
            )
        )
        rows.append(
            {
                "session_id": session["session_id"],
                "manifest_id": session.get("manifest_id"),
                "batch_id": session.get("batch_id"),
                "batch_group_id": session.get("batch_group_id"),
                "compatibility_key": group_key(session),
                "mode": session.get("mode"),
                "teammate_condition": condition,
                "teammate_generated_replies": counter["generated"],
                "teammate_fallback_replies": counter["fallback"],
                "teammate_unknown_replies": counter["unknown"],
                "teammate_reply_denominator": total,
                "teammate_generated_reply_share": (
                    counter["generated"] / total if state == "measured" else None
                ),
                "teammate_fidelity_state": state,
            }
        )
    return rows


def fidelity_monitor(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Operational 80% flag after two live AI sessions with replies. Never changes intake.

    Pool pressure arms only for this operational check, never builds/compatibility keys or
    batch groups. Operators pause the entire group and platform, not just one cell.
    """
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        if row["mode"] != "live" or row["teammate_condition"] != "ai":
            continue
        key = (
            str(row.get("batch_group_id") or row.get("batch_id")),
            str(row["manifest_id"]),
            str(row["compatibility_key"]),
        )
        groups.setdefault(key, []).append(row)
    out = []
    for (cohort, manifest, compatibility), members in sorted(groups.items()):
        observed = [r for r in members if r["teammate_reply_denominator"]]
        total = sum(r["teammate_reply_denominator"] for r in observed)
        generated = sum(r["teammate_generated_replies"] for r in observed)
        unknown = sum(r["teammate_unknown_replies"] for r in observed)
        share = generated / total if total and not unknown else None
        ready = len(observed) >= MIN_FIDELITY_SESSIONS
        status = (
            "insufficient_sessions"
            if not ready
            else (
                "missing_data"
                if share is None
                else ("pause_recommended" if share < GENERATED_REPLY_THRESHOLD else "ok")
            )
        )
        out.append(
            {
                "cohort": cohort,
                "manifest_id": manifest,
                "compatibility_key": compatibility,
                "n_sessions_with_replies": len(observed),
                "n_generated": generated,
                "n_replies": total,
                "n_unknown": unknown,
                "generated_reply_share": share,
                "threshold": GENERATED_REPLY_THRESHOLD,
                "minimum_sessions": MIN_FIDELITY_SESSIONS,
                "status": status,
                "action": "pause_group_and_platform"
                if status in ("pause_recommended", "missing_data")
                else "continue_monitoring",
            }
        )
    return out
