"""Period segmentation and exposure windows (INV-1105; spec §§6.3, 9.1, 9.3).

Derived analytics over committed ``clock_interval``/``manipulation_exposure``/``event``
rows — never a second clock, never client timing:

- ``clock_segments`` decomposes the session's active-time ledger into running/paused
  segments; a paused segment freezes active elapsed at ``active_ms_at_start`` (spec §6.1)
  and contributes zero active ms.
- ``period_summary`` gives the TP triphasic split — pre-check-in running, paused
  (check-in) wall time, post-resume running — and stays honest for PE (no check-in: a
  single running period; ``paused`` is empty, not fabricated).
- ``channel_open_ms`` computes per-channel opportunity time on the *active* axis from
  the profile's phase windows. Closed ``solo_review`` phases contribute zero — a
  participant who cannot act during review-first is not showing reluctance (spec §9.1).
- ``manipulation_windows`` anchors each exposure at its *actual* committed event —
  ``checkin_open``/``message_delivered``/``exposure_committed`` — never the nominal
  second 60/300 (spec §9.1: "time-pressure windows use actual reveal/resume events").
  Missing render/receipt stays missing; a non-committed exposure is not exposure.
- The participant's own ``pressure_checkin.own_rating`` is collected *before* the
  teammates' values are revealed — a pre-reveal manipulation input, never a
  post-exposure effect (spec §9.1).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Connection

from app.measurement.episodes import union_intervals
from app.measurement.rows import SessionRows, load_session_rows
from app.services.activetime import speed_factor_for

SegmentLabel = Literal["pre_checkin", "paused", "post_resume"]


@dataclass(frozen=True)
class ClockSegment:
    """One committed ``clock_interval`` as an analytic segment."""

    interval_id: str
    kind: str  # running | paused
    reason: str
    label: SegmentLabel
    started_at: datetime
    ended_at: datetime | None  # None = still open (censored at ``now``)
    wall_ms: int | None  # None when open and no ``now`` was supplied
    active_ms_at_start: int
    active_ms_at_end: int | None  # None when open-running and no ``now``
    active_ms_contributed: int | None  # paused: 0; open-running without now: None
    censored: bool


def _segment_active_end(
    segments: list[Mapping[str, Any]],
    i: int,
    *,
    now: datetime | None,
    factor: float,
) -> int | None:
    seg = segments[i]
    start = int(seg["active_ms_at_start"])
    if seg["kind"] == "paused":
        return start  # paused intervals freeze elapsed at their start (spec §6.1)
    if i + 1 < len(segments):
        return int(segments[i + 1]["active_ms_at_start"])
    end = seg["ended_at"]
    if end is None:
        if now is None:
            return None
        end = now
    return int(start + max(0, round((end - seg["started_at"]).total_seconds() * 1000 * factor)))


def clock_segments(rows: SessionRows, *, now: datetime | None = None) -> list[ClockSegment]:
    """Ordered clock-ledger segments labeled pre-check-in / paused / post-resume.

    ``label`` is assigned relative to the *first* paused interval (the TP pressure
    check-in): running before it → ``pre_checkin``; paused → ``paused``; running after
    → ``post_resume``. A profile with no pause yields only ``pre_checkin`` segments.
    An open trailing segment is ``censored`` and reports wall/active bounds only when
    ``now`` (a DB clock timestamp) is supplied.
    """
    ordered = sorted(rows.clock_intervals, key=lambda i: i["started_at"])
    seen_pause = False
    factor = speed_factor_for(rows.session or {})
    out: list[ClockSegment] = []
    for i, seg in enumerate(ordered):
        kind = str(seg["kind"])
        if kind == "paused":
            seen_pause = True
            label: SegmentLabel = "paused"
        elif seen_pause:
            label = "post_resume"
        else:
            label = "pre_checkin"
        a_start = int(seg["active_ms_at_start"])
        a_end = _segment_active_end(ordered, i, now=now, factor=factor)
        ended = seg["ended_at"]
        wall_ms = None
        if ended is not None:
            wall_ms = max(0, round((ended - seg["started_at"]).total_seconds() * 1000))
        elif now is not None:
            wall_ms = max(0, round((now - seg["started_at"]).total_seconds() * 1000))
        out.append(
            ClockSegment(
                interval_id=str(seg["interval_id"]),
                kind=kind,
                reason=str(seg["reason"]),
                label=label,
                started_at=seg["started_at"],
                ended_at=ended,
                wall_ms=wall_ms,
                active_ms_at_start=a_start,
                active_ms_at_end=a_end,
                active_ms_contributed=None if a_end is None else max(0, a_end - a_start),
                censored=ended is None,
            )
        )
    return out


def period_summary(rows: SessionRows, *, now: datetime | None = None) -> dict[str, Any]:
    """The triphasic split plus totals, on *cumulative* active boundaries.

    ``active_ms_at_start`` is cumulative elapsed at interval open, so the active
    split is read at the pause boundary rather than by summing per-segment deltas:
    ``pre_checkin`` = elapsed at the first pause's start; ``post_resume`` =
    horizon elapsed minus that boundary (intervening pauses contribute nothing —
    their elapsed is frozen). ``paused`` is wall-clock time: the check-in's
    collection window runs on the wall clock (spec §6.4). A profile with no pause
    (PE) reports ``paused_count: 0`` and ``None`` pre/post splits, and the horizon
    is the latest committed active stamp (``rows.active_end_ms``)."""
    segs = clock_segments(rows, now=now)
    paused = [s for s in segs if s.label == "paused"]
    # Cumulative active at the horizon is known exactly only when the ledger is
    # closed or ``now`` (a DB clock timestamp) is supplied; an open trailing
    # interval reports None, never a floor passed off as a total.
    horizon: int | None
    if not segs or (segs[-1].censored and now is None):
        horizon = None
    else:
        horizon = segs[-1].active_ms_at_end
        if horizon is not None and rows.active_end_ms is not None:
            horizon = max(horizon, rows.active_end_ms)
    boundary = paused[0].active_ms_at_start if paused else None
    post_resume: int | None = None
    if horizon is not None:
        post_resume = 0 if boundary is None else max(0, horizon - boundary)
    paused_wall = [s.wall_ms for s in paused]
    return {
        "segments": [s.__dict__ for s in segs],
        "pre_checkin_active_ms": boundary if paused else horizon,
        "paused_count": len(paused),
        "paused_wall_ms": sum(p for p in paused_wall if p is not None),
        "paused_wall_censored": any(s.censored for s in paused),
        "post_resume_active_ms": post_resume,
        "total_active_ms": horizon,
        "censored": any(s.censored for s in segs),
    }


def active_ms_at_wall(
    rows: SessionRows, at: datetime | None, *, factor: float | None = None
) -> int | None:
    """Active elapsed at wall instant ``at``, derived from the committed interval ledger.

    Some committed records (e.g. ``share.delivered_at``) carry wall times but no
    ``active_ms`` stamp; this converts them through the containing interval.
    Paused intervals freeze elapsed, so a wall time inside a pause maps to the
    frozen value. ``None`` when ``at`` is missing or falls outside every committed
    interval — never fabricated."""
    if at is None:
        return None
    f = speed_factor_for(rows.session or {}) if factor is None else factor
    for iv in sorted(rows.clock_intervals, key=lambda i: i["started_at"]):
        start, end = iv["started_at"], iv["ended_at"]
        if start <= at and (end is None or at <= end):
            base = int(iv["active_ms_at_start"])
            if iv["kind"] == "paused":
                return base
            return int(base + max(0, round((at - start).total_seconds() * 1000 * f)))
    return None


# ----- channel-open opportunity time ----------------------------------------------------------


@dataclass(frozen=True)
class PhaseWindow:
    """One manifest channel phase on the active axis: [from_ms, until_ms)."""

    name: str
    from_ms: int
    until_ms: int
    enabled: frozenset[str]


def phase_windows_from_policy(policy: Any) -> list[PhaseWindow]:
    """Adapt a resolved timing policy (``ResolvedPolicy.phases``) to windows."""
    return [
        PhaseWindow(str(p.name), int(p.from_ms), int(p.until_ms), frozenset(p.enabled))
        for p in policy.phases
    ]


def channel_open_ms(phases: Iterable[PhaseWindow], active_end_ms: int | None) -> dict[str, Any]:
    """Per-channel active-ms opportunity: Σ over phases enabling the channel of
    ``[from_ms, min(until_ms, active_end))``. The active axis already excludes paused
    time (a paused clock never advances), so paused check-in wall time is never
    opportunity time. ``solo_review`` phases enable nothing, so review-first time is
    structurally zero opportunity — reported as 0 open ms, not as reluctance."""
    channels = sorted({c for p in phases for c in p.enabled})
    end = float(active_end_ms) if active_end_ms is not None else 0.0
    per_channel: dict[str, float] = {}
    windows: dict[str, list[tuple[float, float]]] = {}
    for ch in channels:
        spans = [
            (float(p.from_ms), min(float(p.until_ms), end))
            for p in phases
            if ch in p.enabled and float(p.from_ms) < end and p.until_ms > p.from_ms
        ]
        windows[ch] = spans
        per_channel[ch] = sum(b - a for a, b in spans)
    all_open = union_intervals(s for spans in windows.values() for s in spans)
    return {
        "active_end_ms": active_end_ms,
        "per_channel_ms": per_channel,
        "any_channel_open_ms": sum(b - a for a, b in all_open),
        "any_channel_open_spans": all_open,
        "windows": windows,
    }


# ----- manipulation exposure windows ------------------------------------------------------------


def manipulation_windows(rows: SessionRows) -> dict[str, Any]:
    """Actual exposure windows anchored at committed events (spec §9.1).

    For each ``manipulation_exposure`` row: the committed event's server/active stamps,
    delivery receipts (received/rendered seqs), acknowledgment, and the post-exposure
    opportunity span [commit_active_ms → active horizon]. ``exposed`` requires a
    committed anchor — a scheduled-but-never-committed exposure is ``scheduled``, not
    exposure. Own pressure rating is labelled ``pre_reveal_manipulation_input``.
    """
    end_active = rows.active_end_ms
    end_wall = max(
        (e["server_time"] for e in rows.events if e.get("server_time") is not None),
        default=None,
    )
    out: list[dict[str, Any]] = []
    for x in rows.manipulation_exposures:
        commit_ms = rows.event_active_ms(x["committed_seq"])
        commit_wall = rows.event_time(x["committed_seq"])
        ack_ms = rows.event_active_ms(x["acknowledged_seq"])
        render_ms = rows.event_active_ms(x["rendered_seq"])
        post_active = (
            None if commit_ms is None or end_active is None else max(0, end_active - commit_ms)
        )
        post_wall = (
            None
            if commit_wall is None or end_wall is None
            else max(0, round((end_wall - commit_wall).total_seconds() * 1000))
        )
        out.append(
            {
                "exposure_id": str(x["exposure_id"]),
                "kind": str(x["kind"]),
                "status": str(x["status"]),
                "artifact_id": x["artifact_id"],
                "artifact_version": x["artifact_version"],
                "due_active_ms": x["due_active_ms"],
                "scheduled_at": x["scheduled_at"],
                "exposed": x["committed_seq"] is not None,
                "committed_seq": x["committed_seq"],
                "committed_active_ms": commit_ms,
                "committed_wall_at": commit_wall,
                "received_seq": x["received_seq"],
                "rendered_seq": x["rendered_seq"],
                "rendered_active_ms": render_ms,
                "acknowledged_seq": x["acknowledged_seq"],
                "acknowledged_active_ms": ack_ms,
                "post_exposure_active_ms": post_active,
                "post_exposure_wall_ms": post_wall,
                "own_rating": x["own_rating"],
                "own_rating_role": "pre_reveal_manipulation_input"
                if x["kind"] == "pressure_checkin"
                else None,
                "assigned_values": x["assigned_values"],
            }
        )
    return {"exposures": out}


def session_periods(
    conn: Connection,
    session_id: uuid.UUID,
    *,
    phases: Iterable[PhaseWindow] = (),
    now: datetime | None = None,
) -> dict[str, Any]:
    """The INV-1105 bundle: period segmentation, exposure windows, channel-open time."""
    rows = load_session_rows(conn, session_id)
    return {
        "periods": period_summary(rows, now=now),
        "exposures": manipulation_windows(rows),
        "channel_open": channel_open_ms(phases, rows.active_end_ms),
    }
