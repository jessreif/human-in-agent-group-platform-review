"""Request-episode measures (INV-1104; spec §9.3 episodes and pending overlaps).

A ``request`` + ``request_episode`` pair is one episode: accepted/grant times, pending
interval, acknowledgment, substantive response, resolution/cancellation and subsequent
action. This module reports, per episode and per session:

- the pending interval on both clocks (wall ``opened_at``→``closed_at``, and active ms
  via the referenced event seqs);
- the resolution kind and request→response latencies;
- the *union* of overlapping pending windows — concurrent pending requests must not
  double-count wait time (spec §9.3: "compute unions of overlapping pending intervals;
  productive work may continue while requests are pending");
- per-participant opportunity windows: episodes where the participant is the responder
  open a response opportunity at request commit and close at resolution or at the task
  horizon (censored).

Open episodes at the observation horizon are censored — their pending end is the
horizon, flagged ``censored``, never treated as resolved.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Connection

from app.measurement.rows import SessionRows, load_session_rows

ClockAxis = Literal["active", "wall"]


def union_intervals(intervals: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    """Merge overlapping (or touching) intervals; returns sorted disjoint spans.

    Two pending windows that overlap — e.g. a structured request to JT while a
    free-text ask to PM is still open — count once in the union, never twice.
    """
    spans = sorted((float(a), float(b)) for a, b in intervals if b >= a)
    merged: list[list[float]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def union_total(intervals: Iterable[tuple[float, float]]) -> float:
    return sum(b - a for a, b in union_intervals(intervals))


@dataclass(frozen=True)
class EpisodeRow:
    episode_id: str
    request_id: str
    requester: str
    responder: str
    crate: int | None
    kind: str
    target: str
    status: str
    close_reason: str | None
    accepted_seq: int
    accepted_active_ms: int | None
    opened_at: datetime | None
    closed_at: datetime | None
    acknowledged_event_seq: int | None
    substantive_event_seq: int | None
    closed_event_seq: int | None
    # latencies: active axis from event stamps; wall axis from timestamps (ms)
    pending_active_ms: int | None
    pending_wall_ms: int | None
    response_latency_active_ms: int | None
    subsequent_action: str | None
    censored: bool  # still open at the observation horizon


def episode_rows(rows: SessionRows) -> list[EpisodeRow]:
    """One row per committed request episode, deterministic order by request accept seq."""
    req_by_id = {r["request_id"]: r for r in rows.requests}
    out: list[EpisodeRow] = []
    for ep in rows.request_episodes:
        req = req_by_id.get(ep["request_id"])
        if req is None:
            continue  # orphan episode cannot happen (FK); kept for safety
        accepted_ms = req["accepted_active_ms"]
        close_seq = ep["closed_event_seq"]
        close_active = rows.event_active_ms(None if close_seq is None else int(close_seq))
        sub_seq = ep["substantive_event_seq"]
        sub_active = rows.event_active_ms(None if sub_seq is None else int(sub_seq))
        opened_at = ep["opened_at"]
        closed_at = ep["closed_at"]
        censored = closed_at is None
        if close_active is None and censored:
            close_active = rows.active_end_ms
        pending_active = (
            None
            if accepted_ms is None or close_active is None
            else max(0, int(close_active) - int(accepted_ms))
        )
        pending_wall = None
        if opened_at is not None and closed_at is not None:
            pending_wall = max(0, round((closed_at - opened_at).total_seconds() * 1000))
        response_latency = (
            None
            if accepted_ms is None or sub_active is None
            else max(0, int(sub_active) - int(accepted_ms))
        )
        out.append(
            EpisodeRow(
                episode_id=str(ep["episode_id"]),
                request_id=str(ep["request_id"]),
                requester=str(req["requester"]),
                responder=str(req["responder"]),
                crate=None if req["crate"] is None else int(req["crate"]),
                kind=str(req["kind"]),
                target=str(req["target"]),
                status=str(ep["status"]),
                close_reason=ep["close_reason"],
                accepted_seq=int(req["accepted_seq"]),
                accepted_active_ms=None if accepted_ms is None else int(accepted_ms),
                opened_at=opened_at,
                closed_at=closed_at,
                acknowledged_event_seq=ep["acknowledged_event_seq"],
                substantive_event_seq=sub_seq,
                closed_event_seq=close_seq,
                pending_active_ms=pending_active,
                pending_wall_ms=pending_wall,
                response_latency_active_ms=response_latency,
                subsequent_action=ep["subsequent_action"],
                censored=censored,
            )
        )
    out.sort(key=lambda e: (e.accepted_seq, e.request_id))
    return out


def _pending_active_spans(
    eps: Iterable[EpisodeRow], horizon_ms: int | None
) -> list[tuple[float, float]]:
    spans: list[tuple[float, float]] = []
    for ep in eps:
        if ep.accepted_active_ms is None:
            continue  # no active stamp: wall-only episode, excluded from the active union
        end = ep.pending_active_ms
        start = float(ep.accepted_active_ms)
        stop = start + float(end) if end is not None else None
        if stop is None:
            if horizon_ms is None:
                continue
            stop = float(horizon_ms)
        spans.append((start, stop))
    return spans


def _pending_wall_spans(
    eps: Iterable[EpisodeRow], now: datetime | None
) -> list[tuple[float, float]]:
    spans = []
    for ep in eps:
        if ep.opened_at is None:
            continue
        start = ep.opened_at.timestamp() * 1000
        end = ep.closed_at
        if end is None:
            if now is None:
                continue
            end = now
        spans.append((start, end.timestamp() * 1000))
    return spans


def pending_overlap(
    rows: SessionRows,
    *,
    responder: str | None = None,
    requester: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Union of pending windows across matching episodes, on both clocks.

    ``responder``/``requester`` filter the episode set (e.g. ``requester='participant'``
    for outbound asks, ``responder='participant'`` for inbound asks awaiting the
    participant). Overlapping pending intervals union — they never sum."""
    eps = [
        e
        for e in episode_rows(rows)
        if (responder is None or e.responder == responder)
        and (requester is None or e.requester == requester)
    ]
    active_spans = _pending_active_spans(eps, rows.active_end_ms)
    wall_spans = _pending_wall_spans(eps, now)
    active_union = union_intervals(active_spans)
    wall_union = union_intervals(wall_spans)
    return {
        "episodes": len(eps),
        "censored": sum(1 for e in eps if e.censored),
        "pending_active_union_ms": sum(b - a for a, b in active_union),
        "pending_active_spans": active_union,
        "pending_wall_union_ms": None
        if now is None and any(e.censored for e in eps)
        else sum(b - a for a, b in wall_union),
        "pending_wall_spans": wall_union,
        "summed_pending_active_ms": sum(  # diagnostic only: the naive, wrong-on-overlap total
            e.pending_active_ms for e in eps if e.pending_active_ms is not None
        ),
    }


def opportunity_windows(
    rows: SessionRows,
    *,
    responder: str = "participant",
) -> dict[str, Any]:
    """Opportunity intervals: for episodes where ``responder`` owes the response, the
    window runs from request accept until the episode's substantive response / close /
    the active horizon (censored). Unioned so overlapping asks don't double-count."""
    eps = [e for e in episode_rows(rows) if e.responder == responder]
    spans = _pending_active_spans(eps, rows.active_end_ms)
    merged = union_intervals(spans)
    return {
        "responder": responder,
        "episodes": len(eps),
        "opportunity_active_ms": sum(b - a for a, b in merged),
        "opportunity_spans": merged,
        "per_episode": [
            {
                "request_id": e.request_id,
                "crate": e.crate,
                "opened_active_ms": e.accepted_active_ms,
                "closed": not e.censored,
                "status": e.status,
            }
            for e in eps
        ],
    }


def episode_table(conn: Connection, session_id: uuid.UUID) -> dict[str, Any]:
    """The INV-1104 bundle: per-episode rows plus pending/opportunity unions."""
    rows = load_session_rows(conn, session_id)
    eps = episode_rows(rows)
    return {
        "episodes": [e.__dict__ for e in eps],
        "pending_participant_asks": pending_overlap(rows, requester="participant"),
        "pending_agent_asks": pending_overlap(rows, responder="participant"),
        "pending_all": pending_overlap(rows),
        "opportunity_participant": opportunity_windows(rows, responder="participant"),
    }
