"""Telemetry-derived measures (2026-10-12 case-file telemetry plan; remediated
2026-10-13 per `INVESTIGATORS_TEAM_EXPERIMENT_CASE_FILE_TELEMETRY_PLAN_AUDIT.md`).

Pure functions over committed event rows — both the ``SessionRows`` path
(``metrics.session_metrics``) and the bulk export path (``export.study``) call
these. Every reader tolerates absence: sessions predating the telemetry build
(or whose client telemetry never arrived) produce ``missing_data`` semantics —
never zeros, never fabricated values (CL-S8.3-02).

Event shape read here: ``event.type = telemetry_<kind>``,
``event.payload = {"client_ms": <client monotonic ms>,
"client_epoch": <per-load id|None>, "data": {...}}``,
``event.active_ms`` = persist-time task stamp (batch-level), ``event.stage`` =
stage at persist time.

Epoch discipline (audit F06): ``client_ms`` is comparable only within one
``client_epoch`` (each page load mints one). Pairing and cumulative
aggregation happen per epoch; epochs are ordered by server ``seq``. Rows
persisted before the epoch field existed carry ``client_epoch=None`` and are
treated as one unverifiable "legacy" epoch — ``client_epoch_seen`` flags
whether the partition is trustworthy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: Kinds that mark the case-file telemetry build's presence in a session.
FILE_TELEMETRY_TYPES = frozenset(
    {
        "telemetry_file_meta",
        "telemetry_file_record_view",
        "telemetry_file_record_dwell",
        "telemetry_file_scroll_summary",
        "telemetry_file_reorder",
        "telemetry_file_order_final",
        "telemetry_share_attempt",
    }
)

#: Stages in which the workbench (and its telemetry) is live — the declared
#: "task window" for attention measures (audit F09). The check-in overlay
#: stages stay inside: the participant is still in the task even though the
#: file is occluded (exposure suspension is tracked separately on the client).
#: Attribution is by persist-time ``event.stage`` — the batch flush adds up to
#: ~4 s of boundary drift; documented, not hidden.
TASK_WINDOW_STAGES = frozenset(
    {
        "solo_review",
        "checkin_wait",
        "task",
        "checkin_collect",
        "checkin_reveal",
        "answers_locked",
    }
)

_BOTTOM_DEPTH = 0.95
_RECEIPT_TYPES = frozenset(
    {"telemetry_receive", "telemetry_render", "telemetry_render_first_final"}
)


def _data(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return {}
    data = payload.get("data")
    return data if isinstance(data, Mapping) else {}


def _client_ms(event: Mapping[str, Any]) -> int | None:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("client_ms")
    return int(value) if isinstance(value, (int, float)) else None


def _epoch(event: Mapping[str, Any]) -> str | None:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("client_epoch")
    return value if isinstance(value, str) and value else None


def _seq(event: Mapping[str, Any]) -> int:
    try:
        return int(event.get("seq", 0))
    except (TypeError, ValueError):
        return 0


def _ts_ms(value: Any) -> int | None:
    """Epoch ms for a timestamp-ish value, else None."""
    try:
        return int(value.timestamp() * 1000)
    except (AttributeError, OverflowError, OSError):
        return None


def telemetry_events(events: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Telemetry rows among committed events, in *occurrence* order: epochs
    sorted by their earliest persisted seq; inside an epoch, sorted by
    client_ms (unstamped items fall to the end, tie-broken by seq). Never
    compare client_ms across epochs (audit F06)."""
    mine = [e for e in events if str(e.get("type", "")).startswith("telemetry_")]
    return [e for ep in telemetry_epochs(mine) for e in ep]


def telemetry_epochs(events: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    """Telemetry events partitioned by ``client_epoch``; epochs ordered by
    minimum server seq. Rows without an epoch (pre-2026-10-13 deploys) share
    the ``None`` bucket — one unverifiable epoch."""
    by_epoch: dict[str | None, list[Mapping[str, Any]]] = {}
    for e in events:
        if not str(e.get("type", "")).startswith("telemetry_"):
            continue
        by_epoch.setdefault(_epoch(e), []).append(e)
    ordered = sorted(
        by_epoch.items(),
        key=lambda kv: min((_seq(e) for e in kv[1]), default=0),
    )
    return [
        sorted(bucket, key=lambda e: (_client_ms(e) is None, _client_ms(e) or 0, _seq(e)))
        for _, bucket in ordered
    ]


def attention_measures(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Window/tab-level attention from ``telemetry_visibility``/``focus``/``blur``,
    restricted to the task window (audit F09): events attributed to
    ``TASK_WINDOW_STAGES`` by persist-time stage.

    ``hidden_ms_total`` sums hidden→visible pairs closed *within the window and
    within one epoch*. A hidden interval that opens in-window but closes after
    the boundary counts fully (persist-stage drift ≤ ~4 s, documented); one
    that opens before the window or whose epoch ends while hidden adds to
    ``attention_hidden_carried`` / is flagged by ``attention_hidden_censored``
    rather than being guessed.

    ``attention_state`` is ``missing_data`` when the session has no telemetry
    at all (pre-build or dead queue) — otherwise counts are *observed* counts;
    queue loss can only undercount (documented, not imputed).
    """
    epochs = telemetry_epochs(events)
    total_tel = sum(len(ep) for ep in epochs)
    if total_tel == 0:
        return {
            "attention_state": "missing_data",
            "attention_window": None,
            "attention_hidden_count": None,
            "attention_hidden_ms_total": None,
            "attention_hidden_censored": None,
            "attention_hidden_carried": None,
            "attention_blur_count": None,
            "attention_focus_count": None,
            "client_epoch_count": 0,
        }
    hidden_count = 0
    blur_count = 0
    focus_count = 0
    hidden_ms = 0
    carried = 0
    hidden_censored = False
    for epoch in epochs:
        hidden_open: int | None = None
        hidden_in_window = False
        # Carry-in: a hidden that opened before the window (stage outside) is
        # remembered so an in-window close is flagged rather than dropped.
        carried_open = False
        for e in epoch:
            t = e["type"]
            stage = str(e.get("stage", ""))
            in_window = stage in TASK_WINDOW_STAGES
            ms = _client_ms(e)
            if t == "telemetry_visibility":
                st = _data(e).get("state")
                if st == "hidden":
                    if hidden_open is None and not carried_open:
                        if in_window:
                            hidden_count += 1
                            hidden_open = ms
                            hidden_in_window = True
                        else:
                            carried_open = True
                elif st == "visible":
                    if hidden_open is not None:
                        if ms is not None:
                            hidden_ms += max(0, ms - hidden_open)
                        hidden_open = None
                        hidden_in_window = False
                    elif carried_open:
                        # Hidden began outside the window (or before an
                        # unobserved boundary) and lifted inside it.
                        carried += 1
                        carried_open = False
            elif t == "telemetry_blur" and in_window:
                blur_count += 1
            elif t == "telemetry_focus" and in_window:
                focus_count += 1
        if hidden_open is not None and hidden_in_window:
            hidden_censored = True
    return {
        "attention_state": "measured",
        "attention_window": "solo_review..answers_locked",
        "attention_hidden_count": hidden_count,
        "attention_hidden_ms_total": hidden_ms,
        "attention_hidden_censored": hidden_censored,
        "attention_hidden_carried": carried,
        "attention_blur_count": blur_count,
        "attention_focus_count": focus_count,
        "client_epoch_count": len(epochs),
    }


def item_timing(events: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-item engagement (audit F11/F06): ``item_start``→``item_end`` pairs
    by item_id, FIFO on client_ms *within one epoch*. Unpaired starts/ends are
    reported per item instead of vanishing — ``partial`` marks any item whose
    interval stream is incomplete."""
    out: dict[str, dict[str, Any]] = {}
    for epoch in telemetry_epochs(events):
        open_items: dict[str, list[int]] = {}
        for e in epoch:
            t = e["type"]
            if t not in ("telemetry_item_start", "telemetry_item_end"):
                continue
            item_id = _data(e).get("item_id")
            ms = _client_ms(e)
            if not isinstance(item_id, str) or ms is None:
                continue
            rec = out.setdefault(
                item_id,
                {
                    "dwell_ms": 0,
                    "complete_pairs": 0,
                    "unmatched_starts": 0,
                    "unmatched_ends": 0,
                    "partial": False,
                },
            )
            if t == "telemetry_item_start":
                open_items.setdefault(item_id, []).append(ms)
            else:
                starts = open_items.get(item_id)
                if starts:
                    start = starts.pop(0)
                    rec["dwell_ms"] += max(0, ms - start)
                    rec["complete_pairs"] += 1
                else:
                    rec["unmatched_ends"] += 1
        for item_id, starts in open_items.items():
            if starts:
                rec = out.setdefault(
                    item_id,
                    {
                        "dwell_ms": 0,
                        "complete_pairs": 0,
                        "unmatched_starts": 0,
                        "unmatched_ends": 0,
                        "partial": False,
                    },
                )
                rec["unmatched_starts"] += len(starts)
    for rec in out.values():
        rec["partial"] = rec["unmatched_starts"] > 0 or rec["unmatched_ends"] > 0
    return out


def item_dwell_ms(events: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Scalar per-item dwell (completed pairs only) — kept for the metrics
    bundle's compact map; consumers needing completeness use ``item_timing``."""
    return {k: v["dwell_ms"] for k, v in item_timing(events).items()}


def per_record_file_stats(
    events: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]] | None:
    """Per-record view/dwell aggregates. ``None`` when the session has no
    case-file telemetry at all (pre-build or dead queue — both mean
    "no data", documented as ``missing_data``).

    ``first_view_event`` is the view with the lowest server ``seq`` — server
    order is the honest "first persisted view" (client_ms alone cannot order
    across reload epochs, audit F06).
    """
    tel = [e for e in telemetry_events(events) if e["type"] in FILE_TELEMETRY_TYPES]
    if not tel:
        return None
    out: dict[str, dict[str, Any]] = {}
    for e in sorted(tel, key=_seq):
        t = e["type"]
        data = _data(e)
        rid = data.get("record_id")
        if t == "telemetry_file_record_view" and isinstance(rid, str):
            rec = out.setdefault(rid, {"views": 0, "dwell_ms": 0, "first_view_event": None})
            rec["views"] += 1
            if rec["first_view_event"] is None:
                rec["first_view_event"] = e
        elif t == "telemetry_file_record_dwell" and isinstance(rid, str):
            rec = out.setdefault(rid, {"views": 0, "dwell_ms": 0, "first_view_event": None})
            dwell = data.get("dwell_ms")
            if isinstance(dwell, (int, float)):
                rec["dwell_ms"] += int(dwell)
    return out


def _scroll_totals_per_epoch(tel: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Terminal cumulative counters per epoch, then combined honestly (F06):
    distance and reversals *sum* across epochs (each epoch's counters restart
    at zero); depth and farthest index are positional → *max* across epochs.
    The terminal summary is the last one by server seq inside the epoch."""
    epochs: dict[str | None, list[Mapping[str, Any]]] = {}
    for e in tel:
        if e["type"] == "telemetry_file_scroll_summary":
            epochs.setdefault(_epoch(e), []).append(e)
    distance = 0
    reversals = 0
    max_depth = 0.0
    farthest: int | None = None
    n_epochs = 0
    for bucket in epochs.values():
        n_epochs += 1
        terminal = max(bucket, key=_seq)
        data = _data(terminal)
        dist = data.get("distance_px")
        rev = data.get("direction_reversals")
        if isinstance(dist, (int, float)):
            distance += int(dist)
        if isinstance(rev, (int, float)):
            reversals += int(rev)
        for e in bucket:
            d = _data(e).get("max_depth_frac")
            if isinstance(d, (int, float)):
                max_depth = max(max_depth, float(d))
            fi = _data(e).get("farthest_index")
            if isinstance(fi, (int, float)):
                farthest = int(fi) if farthest is None else max(farthest, int(fi))
    return {
        "distance_px": distance,
        "reversals": reversals,
        "max_depth": max_depth,
        "farthest": farthest,
        "epochs": n_epochs,
    }


def record_return_latency(
    events: Sequence[Mapping[str, Any]],
    member: Mapping[str, Any],
    teammate_shares: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Occurrence-time return latency for one teammate-added record (F14).

    Acquisition anchor = the earliest receive/render telemetry referencing the
    membership's ``acquired_event_seq`` or any teammate share's
    ``delivered_seq`` of this record (the moment the card could first appear in
    chat). The view anchor is the first persisted ``file_record_view`` for the
    record (lowest seq).

    - ``occ_ms``: client_ms delta — only when both anchors share an epoch;
      otherwise None (unknown, never guessed).
    - ``server_ms``: server_time delta — a persist-time bound on the same gap
      (batch delay ≤ ~4 s on each end; kept labeled, not silent).
    """
    rid = str(member.get("record_id"))
    anchors: set[int] = set()
    seq_val = member.get("acquired_event_seq")
    if seq_val is not None:
        anchors.add(int(seq_val))
    anchor_server_ms: int | None = None
    for share in teammate_shares:
        if str(share.get("record_id")) != rid:
            continue
        if share.get("delivered_seq") is not None:
            anchors.add(int(share["delivered_seq"]))
        if anchor_server_ms is None:
            anchor_server_ms = _ts_ms(share.get("delivered_at"))
    if anchor_server_ms is None:
        anchor_server_ms = _ts_ms(member.get("acquired_at"))

    acquire_client: tuple[str | None, int] | None = None
    view_event: Mapping[str, Any] | None = None
    telemetry = [x for x in events if str(x.get("type", "")).startswith("telemetry_")]
    for e in sorted(telemetry, key=_seq):
        t = e["type"]
        if t in _RECEIPT_TYPES and acquire_client is None:
            ref = _data(e).get("seq")
            if isinstance(ref, (int, float)) and int(ref) in anchors:
                ms = _client_ms(e)
                acquire_client = (_epoch(e), ms) if ms is not None else None
        elif t == "telemetry_file_record_view" and view_event is None:
            if _data(e).get("record_id") == rid:
                view_event = e
    occ_ms = None
    if acquire_client is not None and view_event is not None:
        view_ms = _client_ms(view_event)
        same_epoch = acquire_client[0] is not None and acquire_client[0] == _epoch(view_event)
        if view_ms is not None and same_epoch:
            occ_ms = max(0, view_ms - acquire_client[1])
    server_ms = None
    if view_event is not None and anchor_server_ms is not None:
        vst = _ts_ms(view_event.get("server_time"))
        if vst is not None:
            server_ms = max(0, vst - anchor_server_ms)
    return {
        "occ_ms": occ_ms,
        "server_ms": server_ms,
        "anchor_found": acquire_client is not None or anchor_server_ms is not None,
        "viewed": view_event is not None,
    }


def file_telemetry_measures(
    events: Sequence[Mapping[str, Any]],
    *,
    file_size: int | None,
    added_from_team: Sequence[Mapping[str, Any]],
    participant_shares: Sequence[Mapping[str, Any]],
    teammate_shares: Sequence[Mapping[str, Any]],
    event_active_ms: Any,
) -> dict[str, Any]:
    """Session-level file measures (remediated 2026-10-13, audit F06-F14).

    Family-level observation states (F10): ``file_meta`` marks the collector
    having run, so each family distinguishes *instrumented, nothing observed*
    (true zero) from *not observed* (``missing_data``). A lone ``share_attempt``
    no longer marks view/scroll/reorder measured.

    ``participant_shares``/``teammate_shares`` are durable ``share`` rows —
    channel attribution joins telemetry attempts to durable shares (F12), and
    ``file_post_share_return_ms`` is the occurrence-time latency to the first
    view of teammate-added intel (F14).
    """
    tel = [e for e in telemetry_events(events) if e["type"] in FILE_TELEMETRY_TYPES]
    any_telemetry = any(str(e.get("type", "")).startswith("telemetry_") for e in events)
    meta = next((e for e in tel if e["type"] == "telemetry_file_meta"), None)
    io_supported: bool | None = None
    if meta is not None:
        val = _data(meta).get("io_supported")
        io_supported = val if isinstance(val, bool) else None
    n_epochs = len(telemetry_epochs(events)) if any_telemetry else 0

    has_view_family = meta is not None or any(
        e["type"] in ("telemetry_file_record_view", "telemetry_file_record_dwell") for e in tel
    )
    has_scroll = meta is not None or any(e["type"] == "telemetry_file_scroll_summary" for e in tel)
    has_reorder = meta is not None or any(
        e["type"] in ("telemetry_file_reorder", "telemetry_file_order_final") for e in tel
    )
    has_share_family = meta is not None or any(e["type"] == "telemetry_share_attempt" for e in tel)

    empty: dict[str, Any] = {
        "telemetry_events": sum(
            1 for e in events if str(e.get("type", "")).startswith("telemetry_")
        ),
        "client_epoch_count": n_epochs,
        "file_telemetry_state": "missing_data",
        "file_io_supported": io_supported,
        "file_view_state": "missing_data",
        "file_coverage_frac": None,
        "file_viewed_count": None,
        "file_dwell_ms_total": None,
        "file_scroll_state": "missing_data",
        "file_max_scroll_depth": None,
        "file_reached_bottom": None,
        "file_scroll_reversals": None,
        "file_scroll_distance_px": None,
        "file_reorder_state": "missing_data",
        "file_reorder_count": None,
        "file_reorder_distance": None,
        "file_share_state": "missing_data",
        "share_attempts": None,
        "share_participant_total": len(participant_shares),
        "share_channel_known": None,
        "share_channel_unknown": None,
        "share_via_drag_frac": None,
        "share_via_drag_frac_observed": None,
        "file_post_share_return_ms": None,
        "file_post_share_return_n": None,
        "file_post_share_return_persist_ms": None,
    }
    if not tel:
        return empty

    out = dict(empty)
    out["file_telemetry_state"] = "measured"

    # --- view/dwell family -------------------------------------------------
    stats = per_record_file_stats(events) or {}
    if not has_view_family:
        out["file_view_state"] = "missing_data"
    elif io_supported is False:
        out["file_view_state"] = "unobservable"
    else:
        out["file_view_state"] = "measured"
        viewed_ids = {rid for rid, s in stats.items() if s["views"] > 0}
        out["file_viewed_count"] = len(viewed_ids)
        out["file_coverage_frac"] = len(viewed_ids) / file_size if file_size else None
        out["file_dwell_ms_total"] = sum(s["dwell_ms"] for s in stats.values())

    # --- scroll family ------------------------------------------------------
    if has_scroll:
        out["file_scroll_state"] = "measured"
        totals = _scroll_totals_per_epoch(tel)
        out["file_max_scroll_depth"] = totals["max_depth"]
        out["file_reached_bottom"] = totals["max_depth"] >= _BOTTOM_DEPTH
        out["file_scroll_reversals"] = totals["reversals"]
        out["file_scroll_distance_px"] = totals["distance_px"]

    # --- reorder family -----------------------------------------------------
    if has_reorder:
        out["file_reorder_state"] = "measured"
        reorder_count = 0
        reorder_distance = 0
        for e in tel:
            if e["type"] != "telemetry_file_reorder":
                continue
            reorder_count += 1
            data = _data(e)
            frm, to = data.get("from_index"), data.get("to_index")
            if isinstance(frm, (int, float)) and isinstance(to, (int, float)):
                reorder_distance += abs(int(to) - int(frm))
        out["file_reorder_count"] = reorder_count
        out["file_reorder_distance"] = reorder_distance

    # --- share channel (F12: join attempts to durable participant shares) ----
    if has_share_family or participant_shares:
        out["file_share_state"] = "measured"
        attempts = 0
        sent_drag = 0
        sent_total = 0
        via_by_record: dict[str, str] = {}
        for e in tel:
            if e["type"] != "telemetry_share_attempt":
                continue
            attempts += 1
            data = _data(e)
            rid = data.get("record_id")
            if data.get("result") == "sent":
                sent_total += 1
                if data.get("via") == "drag":
                    sent_drag += 1
                if isinstance(rid, str) and rid not in via_by_record:
                    via = data.get("via")
                    if isinstance(via, str):
                        via_by_record[rid] = via
        durable_total = len(participant_shares)
        known = sum(1 for s in participant_shares if str(s.get("record_id")) in via_by_record)
        out["share_attempts"] = attempts
        out["share_participant_total"] = durable_total
        out["share_channel_known"] = known
        out["share_channel_unknown"] = durable_total - known
        out["share_via_drag_frac"] = (
            sum(
                1
                for s in participant_shares
                if via_by_record.get(str(s.get("record_id"))) == "drag"
            )
            / durable_total
            if durable_total
            else None
        )
        out["share_via_drag_frac_observed"] = sent_drag / sent_total if sent_total else None

    # --- return latency (F14) -----------------------------------------------
    occ_returns: list[int] = []
    persist_returns: list[int] = []
    for member in added_from_team:
        lat = record_return_latency(events, member, teammate_shares)
        if lat["occ_ms"] is not None:
            occ_returns.append(lat["occ_ms"])
        # Persist proxy: view active_ms minus acquisition event's active_ms.
        seq = member.get("acquired_event_seq")
        rec_stats = stats.get(str(member.get("record_id")))
        if rec_stats is not None and rec_stats["first_view_event"] is not None:
            anchor_active = event_active_ms(seq) if seq is not None else None
            view_active = rec_stats["first_view_event"].get("active_ms")
            if anchor_active is not None and view_active is not None:
                persist_returns.append(max(0, int(view_active) - int(anchor_active)))
    out["file_post_share_return_ms"] = min(occ_returns) if occ_returns else None
    out["file_post_share_return_n"] = len(occ_returns)
    out["file_post_share_return_persist_ms"] = min(persist_returns) if persist_returns else None
    return out
