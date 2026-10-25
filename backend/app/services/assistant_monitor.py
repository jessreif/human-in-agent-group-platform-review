"""Assistant request-to-render reconciliation; counts only, never research text."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, select

from app.db import schema as s
from app.db.tx import db_now


def summarize(rows: Sequence[Mapping[str, Any]], now: datetime) -> dict[str, Any]:
    statuses: Counter[str] = Counter()
    violations: Counter[str] = Counter()
    intended = deterministic = delivered = generated = rendered = attempts = unresolved = 0
    oldest = 0.0
    for row in rows:
        deterministic += bool(row["deterministic"])
        intended += not row["deterministic"]
        attempts += row["attempts"]
        statuses.update(row["statuses"])
        violations.update(row["violations"])
        delivered += bool(row["delivered"])
        generated += bool(row["generated"])
        rendered += bool(row["rendered"])
        if not row["delivered"] and not row["terminal"]:
            unresolved += 1
            oldest = max(oldest, (now - row["accepted_at"]).total_seconds())
    calls = sum(statuses.values())
    acceptance = statuses["succeeded"] / calls if calls else None
    alerts = []
    if intended and calls and statuses["succeeded"] / calls < 0.5:
        alerts.append("assistant_generation_degraded")
    if oldest > 120:
        alerts.append("assistant_delivery_stalled")
    if statuses["succeeded"] > generated:
        alerts.append("generated_output_not_delivered")
    return {
        "accepted": len(rows),
        "intended_provider": intended,
        "intentional_deterministic": deterministic,
        "attempts": attempts,
        "call_statuses": dict(statuses),
        "violations": dict(violations),
        "acceptance": acceptance,
        "delivered": delivered,
        "generated_delivered": generated,
        "rendered": rendered,
        "unresolved": unresolved,
        "oldest_unresolved_seconds": oldest,
        "alerts": alerts,
    }


def _mark_current(
    partitions: list[dict[str, Any]], current_build: str | None
) -> list[dict[str, Any]]:
    """Flag partitions recorded on the deployed build and float them first.

    model_call rows are immutable history — a superseded build keeps its
    recorded incident state forever, so the UI must be able to tell which
    partitions describe the running code.
    """
    for part in partitions:
        part["current"] = current_build is not None and part["build"] == current_build
    return sorted(partitions, key=lambda p: not p["current"])


def monitor(
    conn: Connection,
    sessions: Sequence[Mapping[str, Any]],
    *,
    current_build: str | None = None,
) -> dict[str, Any]:
    """Assistant-request monitor for the given sessions: per partition, each assistant
    request joined to its generation job, model call, displayed output, chat message
    and delivery receipt, so stalled, fallback or undelivered responses are visible."""
    sids = [row["session_id"] for row in sessions]
    if not sids:
        return {"partitions": []}

    def rows(table: Any) -> list[Mapping[str, Any]]:
        return [
            dict(r)
            for r in conn.execute(select(table).where(table.c.session_id.in_(sids))).mappings()
        ]

    requests = [r for r in rows(s.request) if r["responder"] == "assistant"]
    jobs = rows(s.scheduled_job)
    calls = rows(s.model_call)
    displays = rows(s.displayed_output)
    messages = rows(s.message)
    receipts = rows(s.delivery_receipt)
    session_by_id = {r["session_id"]: r for r in sessions}
    partitions: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for request in requests:
        sid = request["session_id"]
        session = session_by_id[sid]
        generate = next(
            (
                j
                for j in jobs
                if j["session_id"] == sid
                and j["kind"] == "generate"
                and j["payload"].get("request_id") == str(request["request_id"])
            ),
            None,
        )
        payload = generate["payload"] if generate else {}
        snapshot = payload.get("snapshot_id")
        outcomes = [
            c for c in calls if c["session_id"] == sid and str(c["snapshot_id"]) == snapshot
        ]
        replies = [
            m
            for m in messages
            if m["session_id"] == sid
            and m["request_id"] == request["request_id"]
            and m["actor"] == "assistant"
        ]
        mids = {m["message_id"] for m in replies}
        shown = [d for d in displays if d["session_id"] == sid and d["message_id"] in mids]
        rendered = any(
            r["session_id"] == sid
            and r["rendered_at"] is not None
            and any(m["committed_seq"] == r["seq"] for m in replies)
            for r in receipts
        )
        cohort = (
            "diagnostic"
            if session["mode"] != "live"
            or str(session.get("research_pid", "")).lower().startswith(("preview-", "test_", "qa_"))
            else "live"
        )
        key = (
            *tuple(
                str(session.get(k) or "unknown")
                for k in ("compatibility_key", "build_source_sha256", "manifest_id", "arm", "mode")
            ),
            cohort,
            str(
                payload.get("generation_policy", {}).get("model", "unknown")
                if payload.get("generation_policy")
                else "legacy"
            ),
        )
        partitions[key].append(
            {
                "deterministic": bool(payload.get("deterministic_only")),
                "attempts": int(generate["attempts"]) if generate else 0,
                "statuses": [c["status"] for c in outcomes],
                "violations": [
                    v for c in outcomes for v in (c["validation"] or {}).get("violations", [])
                ],
                "delivered": bool(replies),
                "generated": any(d["fallback"] == "none" for d in shown),
                "rendered": rendered,
                "accepted_at": request["accepted_at"],
                "terminal": bool(
                    generate and generate["status"] in ("expired", "cancelled", "failed")
                )
                or session["stage"] in (*s.TERMINAL_STAGES, "completion"),
            }
        )
    now = db_now(conn)
    labels = ("compatibility", "build", "profile", "arm", "mode", "cohort", "model")
    return {
        "partitions": _mark_current(
            [
                {**dict(zip(labels, key, strict=True)), **summarize(values, now)}
                for key, values in sorted(partitions.items())
            ],
            current_build,
        )
    }
