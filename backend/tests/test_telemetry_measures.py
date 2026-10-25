"""Telemetry-measure unit tests (2026-10-13 audit G01 / F20 TS-B11).

Events are plain mappings shaped like committed ``app.event`` rows —
``{"seq", "type", "stage", "active_ms", "server_time", "payload"}`` where
``payload = {"client_ms", "client_epoch", "data"}``.

F20 checklist (no unknown rows). Each name is a test in this module that
fails a wrong or all-zero result for that acceptance item.

- Epoch partition and order — ``test_epochs_partition_and_order_by_first_seq``,
  ``test_epochless_rows_form_one_legacy_bucket``.
- Task-stage window — ``test_attention_window_restricts_to_task_stages``.
- Hidden pairs, censoring, no cross-epoch pair —
  ``test_attention_hidden_pair_inside_window_durations``,
  ``test_attention_hidden_open_at_epoch_end_is_censored_not_estimated``,
  ``test_attention_hidden_spanning_epochs_never_pairs``,
  ``test_attention_carried_open_flags_pre_window_hidden``.
- Partial item timing, no cross-epoch dwell —
  ``test_item_timing_pairs_fifo_and_flags_partials``,
  ``test_item_timing_never_pairs_across_epochs``.
- Missing versus true zero versus unobservable —
  ``test_file_measures_all_missing_without_telemetry``,
  ``test_file_meta_marker_turns_absent_families_into_true_zeros``,
  ``test_io_unsupported_reports_unobservable_not_zero``.
- Coverage fraction — ``test_file_view_coverage_and_dwell``.
- Durable share join — ``test_share_channel_joins_attempts_to_durable_shares``,
  ``test_share_channel_all_unknown_when_attempts_lost``.
- Scroll across epochs — ``test_scroll_totals_combine_epochs_honestly``.
- Reorder — ``test_reorder_counts_and_distance``.
- Reload, clocks restart at 0, no cross-boundary subtract —
  ``test_reload_clocks_restart_at_zero_and_do_not_subtract_across_epochs``.
- Dropped or final file events (final dwell kept, view without dwell,
  dwell without a view) —
  ``test_dropped_and_final_file_events_do_not_invent_views_or_dwell``.
- Undefined denominator (``file_coverage_frac`` is None when file size is
  missing; 0 only for an instrumented file empty of views) —
  ``test_file_coverage_frac_none_when_file_size_missing``
  (0-with-a-real-size also in the file_meta marker test).
- Paused clock: a ``client_ms`` gap is not filled in —
  ``test_paused_clock_gap_in_client_ms_is_not_filled_in``.
- Latest unavailable versus available —
  ``test_return_latency_unviewed_is_missing_not_zero`` (no anchor),
  ``test_return_latency_anchor_without_view_is_unavailable`` (anchor, no view),
  ``test_return_latency_occurrence_same_epoch`` (viewed, latency available).
- Full final order, item-for-item not as a set —
  ``test_file_order_final_preserved_item_for_item``.
- All-zero fabricated measure fails one targeted test per helper —
  ``attention_measures``:
  ``test_attention_hidden_pair_inside_window_durations``;
  ``item_dwell_ms``: ``test_item_dwell_ms_is_not_an_all_zero_fabricated_map``;
  ``per_record_file_stats``:
  ``test_per_record_stats_aggregates_and_keeps_first_view``;
  ``file_telemetry_measures``: ``test_file_view_coverage_and_dwell``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.measurement.telemetry import (
    attention_measures,
    file_telemetry_measures,
    item_dwell_ms,
    item_timing,
    per_record_file_stats,
    record_return_latency,
    telemetry_epochs,
    telemetry_events,
)

_EPOCH_A = "ep-a"
_EPOCH_B = "ep-b"


def ev(
    seq: int,
    kind: str,
    *,
    stage: str = "task",
    client_ms: int | None = 0,
    epoch: str | None = _EPOCH_A,
    data: dict[str, Any] | None = None,
    active_ms: int | None = None,
    server_time: datetime | None = None,
) -> dict[str, Any]:
    return {
        "seq": seq,
        "type": f"telemetry_{kind}",
        "stage": stage,
        "active_ms": active_ms,
        "server_time": server_time,
        "payload": {"client_ms": client_ms, "client_epoch": epoch, "data": data or {}},
    }


def _noop_active(_seq: int | None) -> None:
    return None


# ---------------------------------------------------------------------------
# Epoch partitioning (F06)
# ---------------------------------------------------------------------------


def test_epochs_partition_and_order_by_first_seq() -> None:
    events = [
        ev(10, "focus", epoch=_EPOCH_B, client_ms=50),
        ev(20, "blur", epoch=_EPOCH_A, client_ms=900),
        ev(30, "focus", epoch=_EPOCH_A, client_ms=100),
        ev(40, "blur", epoch=_EPOCH_B, client_ms=10),
    ]
    epochs = telemetry_epochs(events)
    assert len(epochs) == 2
    # Epoch A's earliest seq (20) precedes B's (10)? No — B starts at seq 10,
    # so B is the first epoch despite being minted "later" alphabetically.
    assert [e["seq"] for e in epochs[0]] == [40, 10]  # epoch B, client_ms order
    assert [e["seq"] for e in epochs[1]] == [30, 20]  # epoch A, client_ms order


def test_epochless_rows_form_one_legacy_bucket() -> None:
    events = [ev(1, "focus", epoch=None), ev(2, "blur", epoch=None)]
    assert len(telemetry_epochs(events)) == 1


# ---------------------------------------------------------------------------
# Attention measures (F09)
# ---------------------------------------------------------------------------


def test_attention_missing_when_no_telemetry() -> None:
    out = attention_measures([{"seq": 1, "type": "record_shared", "stage": "task"}])
    assert out["attention_state"] == "missing_data"
    assert out["attention_hidden_count"] is None
    assert out["client_epoch_count"] == 0


def test_attention_window_restricts_to_task_stages() -> None:
    events = [
        ev(1, "blur", stage="consent", client_ms=10),
        ev(2, "blur", stage="task", client_ms=20),
        ev(3, "focus", stage="post_task", client_ms=30),
        ev(4, "focus", stage="task", client_ms=40),
    ]
    out = attention_measures(events)
    assert out["attention_state"] == "measured"
    assert out["attention_window"] == "solo_review..answers_locked"
    assert out["attention_blur_count"] == 1
    assert out["attention_focus_count"] == 1


def test_attention_hidden_pair_inside_window_durations() -> None:
    events = [
        ev(1, "visibility", stage="task", client_ms=1_000, data={"state": "hidden"}),
        ev(2, "visibility", stage="task", client_ms=2_500, data={"state": "visible"}),
    ]
    out = attention_measures(events)
    assert out["attention_hidden_count"] == 1
    assert out["attention_hidden_ms_total"] == 1_500
    assert out["attention_hidden_censored"] is False
    assert out["attention_hidden_carried"] == 0


def test_attention_hidden_open_at_epoch_end_is_censored_not_estimated() -> None:
    events = [ev(1, "visibility", stage="task", client_ms=100, data={"state": "hidden"})]
    out = attention_measures(events)
    assert out["attention_hidden_count"] == 1
    assert out["attention_hidden_ms_total"] == 0
    assert out["attention_hidden_censored"] is True


def test_attention_hidden_spanning_epochs_never_pairs() -> None:
    # Hidden opens in epoch A, closes in epoch B — client_ms is incomparable
    # across loads, so no duration is fabricated (F06).
    events = [
        ev(1, "visibility", stage="task", client_ms=100, epoch=_EPOCH_A, data={"state": "hidden"}),
        ev(2, "visibility", stage="task", client_ms=50, epoch=_EPOCH_B, data={"state": "visible"}),
    ]
    out = attention_measures(events)
    assert out["attention_hidden_ms_total"] == 0
    assert out["attention_hidden_censored"] is True  # epoch A ended still hidden
    assert out["client_epoch_count"] == 2


def test_attention_carried_open_flags_pre_window_hidden() -> None:
    events = [
        ev(1, "visibility", stage="briefing", client_ms=10, data={"state": "hidden"}),
        ev(2, "visibility", stage="task", client_ms=50, data={"state": "visible"}),
    ]
    out = attention_measures(events)
    assert out["attention_hidden_carried"] == 1
    assert out["attention_hidden_ms_total"] == 0


# ---------------------------------------------------------------------------
# Item timing (F11/F06)
# ---------------------------------------------------------------------------


def test_item_timing_pairs_fifo_and_flags_partials() -> None:
    events = [
        ev(1, "item_start", client_ms=100, data={"item_id": "q1"}),
        ev(2, "item_end", client_ms=400, data={"item_id": "q1"}),
        ev(3, "item_start", client_ms=500, data={"item_id": "q1"}),
        ev(4, "item_end", client_ms=900, data={"item_id": "q1"}),
        ev(5, "item_start", client_ms=100, data={"item_id": "q2"}),  # never ends
        ev(6, "item_end", client_ms=700, data={"item_id": "q3"}),  # never starts
    ]
    out = item_timing(events)
    assert out["q1"]["dwell_ms"] == 700
    assert out["q1"]["complete_pairs"] == 2
    assert out["q1"]["partial"] is False
    assert out["q2"]["unmatched_starts"] == 1 and out["q2"]["partial"] is True
    assert out["q3"]["unmatched_ends"] == 1 and out["q3"]["partial"] is True


def test_item_timing_never_pairs_across_epochs() -> None:
    events = [
        ev(1, "item_start", epoch=_EPOCH_A, client_ms=100, data={"item_id": "q1"}),
        ev(2, "item_end", epoch=_EPOCH_B, client_ms=400, data={"item_id": "q1"}),
    ]
    out = item_timing(events)
    assert out["q1"]["dwell_ms"] == 0
    assert out["q1"]["complete_pairs"] == 0
    assert out["q1"]["partial"] is True
    assert item_dwell_ms(events) == {"q1": 0}


# ---------------------------------------------------------------------------
# Per-record file stats + file_telemetry_measures (F10/F12/F14)
# ---------------------------------------------------------------------------


def test_per_record_stats_none_without_file_telemetry() -> None:
    assert per_record_file_stats([ev(1, "focus")]) is None


def test_per_record_stats_aggregates_and_keeps_first_view() -> None:
    events = [
        ev(3, "file_record_dwell", client_ms=9, data={"record_id": "r1", "dwell_ms": 800}),
        ev(1, "file_record_view", client_ms=5, data={"record_id": "r1"}),
        ev(2, "file_record_view", client_ms=7, data={"record_id": "r1"}),
        ev(4, "file_record_dwell", client_ms=12, data={"record_id": "r1", "dwell_ms": 200}),
    ]
    stats = per_record_file_stats(events)
    assert stats is not None
    assert stats["r1"]["views"] == 2
    assert stats["r1"]["dwell_ms"] == 1_000
    assert stats["r1"]["first_view_event"]["seq"] == 1  # lowest server seq


def _file_measures(events: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    kw.setdefault("file_size", 3)
    kw.setdefault("added_from_team", [])
    kw.setdefault("participant_shares", [])
    kw.setdefault("teammate_shares", [])
    kw.setdefault("event_active_ms", _noop_active)
    return file_telemetry_measures(events, **kw)


def test_file_measures_all_missing_without_telemetry() -> None:
    out = _file_measures([{"seq": 1, "type": "record_shared", "stage": "task"}])
    assert out["file_telemetry_state"] == "missing_data"
    assert out["file_view_state"] == "missing_data"
    assert out["file_scroll_state"] == "missing_data"
    assert out["file_reorder_state"] == "missing_data"
    assert out["file_share_state"] == "missing_data"


def test_file_meta_marker_turns_absent_families_into_true_zeros() -> None:
    # The once-per-mount marker proves the collector ran: a session with no
    # views/scrolls/reorders is measured-zero, not missing (F10).
    out = _file_measures([ev(1, "file_meta", data={"io_supported": True, "n_items": 3})])
    assert out["file_telemetry_state"] == "measured"
    assert out["file_view_state"] == "measured"
    assert out["file_viewed_count"] == 0
    assert out["file_coverage_frac"] == 0
    assert out["file_scroll_state"] == "measured"
    assert out["file_scroll_distance_px"] == 0
    assert out["file_reorder_state"] == "measured"
    assert out["file_reorder_count"] == 0
    assert out["file_share_state"] == "measured"


def test_io_unsupported_reports_unobservable_not_zero() -> None:
    out = _file_measures([ev(1, "file_meta", data={"io_supported": False, "n_items": 3})])
    assert out["file_view_state"] == "unobservable"
    assert out["file_viewed_count"] is None
    # Scroll does not depend on IntersectionObserver — still measured-zero.
    assert out["file_scroll_state"] == "measured"


def test_lone_share_attempt_leaves_other_families_missing() -> None:
    # Partial telemetry loss: a share attempt survived but no file_meta — view,
    # scroll and reorder families stay missing rather than reading as zeros.
    out = _file_measures(
        [ev(1, "share_attempt", data={"record_id": "r1", "via": "button", "result": "sent"})]
    )
    assert out["file_share_state"] == "measured"
    assert out["share_attempts"] == 1
    assert out["file_view_state"] == "missing_data"
    assert out["file_scroll_state"] == "missing_data"
    assert out["file_reorder_state"] == "missing_data"


def test_file_view_coverage_and_dwell() -> None:
    events = [
        ev(1, "file_meta", data={"io_supported": True, "n_items": 3}),
        ev(2, "file_record_view", client_ms=100, data={"record_id": "r1"}),
        ev(3, "file_record_view", client_ms=200, data={"record_id": "r2"}),
        ev(4, "file_record_dwell", client_ms=800, data={"record_id": "r1", "dwell_ms": 600}),
        ev(5, "file_record_dwell", client_ms=900, data={"record_id": "r1", "dwell_ms": 400}),
    ]
    out = _file_measures(events, file_size=4)
    assert out["file_viewed_count"] == 2
    assert out["file_coverage_frac"] == 0.5
    assert out["file_dwell_ms_total"] == 1_000


def test_scroll_totals_combine_epochs_honestly() -> None:
    # Distance/reversals are cumulative counters that restart per epoch → sum;
    # depth/farthest are positional → max (F06).
    events = [
        ev(1, "file_meta", data={"io_supported": True, "n_items": 48}),
        ev(
            2,
            "file_scroll_summary",
            epoch=_EPOCH_A,
            client_ms=5_000,
            data={
                "max_depth_frac": 0.4,
                "farthest_index": 10,
                "direction_reversals": 2,
                "distance_px": 1_000,
            },
        ),
        ev(
            3,
            "file_scroll_summary",
            epoch=_EPOCH_B,
            client_ms=4_000,
            data={
                "max_depth_frac": 0.97,
                "farthest_index": 47,
                "direction_reversals": 1,
                "distance_px": 2_500,
            },
        ),
    ]
    out = _file_measures(events)
    assert out["file_max_scroll_depth"] == 0.97
    assert out["file_reached_bottom"] is True
    assert out["file_scroll_reversals"] == 3
    assert out["file_scroll_distance_px"] == 3_500


def test_reorder_counts_and_distance() -> None:
    events = [
        ev(1, "file_meta", data={"io_supported": True, "n_items": 3}),
        ev(2, "file_reorder", data={"record_id": "r1", "from_index": 0, "to_index": 2}),
        ev(3, "file_reorder", data={"record_id": "r2", "from_index": 1, "to_index": 0}),
        ev(4, "file_order_final", data={"order": ["r3", "r2", "r1"], "n_items": 3}),
    ]
    out = _file_measures(events)
    assert out["file_reorder_count"] == 2
    assert out["file_reorder_distance"] == 3


def test_share_channel_joins_attempts_to_durable_shares() -> None:
    events = [
        ev(1, "file_meta", data={"io_supported": True, "n_items": 3}),
        ev(2, "share_attempt", data={"record_id": "r1", "via": "drag", "result": "sent"}),
        ev(3, "share_attempt", data={"record_id": "r2", "via": "button", "result": "sent"}),
    ]
    shares = [
        {"record_id": "r1", "sharer": "participant"},
        {"record_id": "r2", "sharer": "participant"},
        {"record_id": "r3", "sharer": "participant"},  # attempt never arrived
    ]
    out = _file_measures(events, participant_shares=shares)
    assert out["share_participant_total"] == 3
    assert out["share_channel_known"] == 2
    assert out["share_channel_unknown"] == 1
    assert out["share_via_drag_frac"] == 1 / 3
    assert out["share_via_drag_frac_observed"] == 0.5


def test_share_channel_all_unknown_when_attempts_lost() -> None:
    events = [ev(1, "file_meta", data={"io_supported": True, "n_items": 3})]
    shares = [{"record_id": "r1", "sharer": "participant"}]
    out = _file_measures(events, participant_shares=shares)
    # The durable share keeps the family measured; the channel is unrecoverable.
    assert out["file_share_state"] == "measured"
    assert out["share_channel_known"] == 0
    assert out["share_channel_unknown"] == 1
    assert out["share_via_drag_frac"] == 0.0


# ---------------------------------------------------------------------------
# Return latency (F14)
# ---------------------------------------------------------------------------


def test_return_latency_occurrence_same_epoch() -> None:
    member = {"record_id": "r9", "acquired_event_seq": 50}
    share = {"record_id": "r9", "sharer": "JT", "delivered_seq": 50}
    events = [
        ev(40, "receive", client_ms=2_000, epoch=_EPOCH_A, data={"seq": 50}),
        ev(60, "file_record_view", client_ms=5_500, epoch=_EPOCH_A, data={"record_id": "r9"}),
    ]
    lat = record_return_latency(events, member, [share])
    assert lat["occ_ms"] == 3_500
    assert lat["viewed"] is True
    assert lat["anchor_found"] is True


def test_return_latency_occ_none_when_epochs_differ() -> None:
    member = {"record_id": "r9", "acquired_event_seq": 50}
    share = {"record_id": "r9", "sharer": "JT", "delivered_seq": 50}
    events = [
        ev(40, "receive", client_ms=2_000, epoch=_EPOCH_A, data={"seq": 50}),
        ev(60, "file_record_view", client_ms=5_500, epoch=_EPOCH_B, data={"record_id": "r9"}),
    ]
    lat = record_return_latency(events, member, [share])
    assert lat["occ_ms"] is None
    assert lat["viewed"] is True


def test_return_latency_server_ms_from_timestamps() -> None:
    member = {"record_id": "r9", "acquired_event_seq": 50}
    share = {
        "record_id": "r9",
        "sharer": "JT",
        "delivered_seq": 50,
        "delivered_at": datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC),
    }
    events = [
        ev(
            60,
            "file_record_view",
            client_ms=5_500,
            epoch=_EPOCH_B,
            data={"record_id": "r9"},
            server_time=datetime(2026, 10, 13, 12, 0, 4, tzinfo=UTC),
        ),
    ]
    lat = record_return_latency(events, member, [share])
    assert lat["occ_ms"] is None  # no receipt anchor for occurrence semantics
    assert lat["server_ms"] == 4_000


def test_return_latency_unviewed_is_missing_not_zero() -> None:
    member = {"record_id": "r9", "acquired_event_seq": 50}
    lat = record_return_latency([], member, [])
    # anchor_found=False: the member carries an anchor seq but no receipt event
    # or timestamp resolved it to a clock — an honest "not found", not a 0.
    assert lat == {"occ_ms": None, "server_ms": None, "anchor_found": False, "viewed": False}


def test_return_latency_anchor_without_view_is_unavailable() -> None:
    """Acquisition is available (receipt anchor) but the record was never viewed.

    Latency stays missing, not zero. A second record viewed in the same epoch
    is the available case: a real occurrence latency, and the session minimum
    does not collapse to the unviewed record's missing value.
    """
    member_unviewed = {"record_id": "r_late", "acquired_event_seq": 50}
    member_viewed = {"record_id": "r_seen", "acquired_event_seq": 50}
    share = {"record_id": "r_late", "sharer": "JT", "delivered_seq": 50}
    events = [
        ev(40, "receive", client_ms=2_000, data={"seq": 50}),
        ev(60, "file_record_view", client_ms=2_800, data={"record_id": "r_seen"}),
    ]
    unseen = record_return_latency(events, member_unviewed, [share])
    assert unseen["anchor_found"] is True
    assert unseen["viewed"] is False
    assert unseen["occ_ms"] is None
    assert unseen["server_ms"] is None
    seen = record_return_latency(events, member_viewed, [share])
    assert seen["anchor_found"] is True
    assert seen["viewed"] is True
    assert seen["occ_ms"] == 800
    out = _file_measures(
        events,
        added_from_team=[member_unviewed, member_viewed],
        teammate_shares=[share],
    )
    assert out["file_post_share_return_ms"] == 800
    assert out["file_post_share_return_n"] == 1
    assert out["file_post_share_return_persist_ms"] is None


# ---------------------------------------------------------------------------
# F20 faults an all-zero or cross-epoch measure would still pass
# ---------------------------------------------------------------------------


def test_reload_clocks_restart_at_zero_and_do_not_subtract_across_epochs() -> None:
    """Both epochs' monotonic clocks restart at 0. A running delta that crosses
    the reload boundary subtracts the new origin from the previous epoch's end
    and undercounts (2500 becomes 500)."""
    events = [
        ev(1, "visibility", epoch=_EPOCH_A, client_ms=0, data={"state": "hidden"}),
        ev(2, "visibility", epoch=_EPOCH_A, client_ms=2_000, data={"state": "visible"}),
        ev(3, "item_start", epoch=_EPOCH_A, client_ms=0, data={"item_id": "q1"}),
        ev(4, "item_end", epoch=_EPOCH_A, client_ms=2_000, data={"item_id": "q1"}),
        ev(5, "visibility", epoch=_EPOCH_B, client_ms=0, data={"state": "hidden"}),
        ev(6, "visibility", epoch=_EPOCH_B, client_ms=500, data={"state": "visible"}),
        ev(7, "item_start", epoch=_EPOCH_B, client_ms=0, data={"item_id": "q1"}),
        ev(8, "item_end", epoch=_EPOCH_B, client_ms=500, data={"item_id": "q1"}),
    ]
    out = attention_measures(events)
    assert out["client_epoch_count"] == 2
    assert out["attention_hidden_count"] == 2
    assert out["attention_hidden_ms_total"] == 2_500
    assert out["attention_hidden_censored"] is False
    assert item_dwell_ms(events) == {"q1": 2_500}


def test_item_dwell_ms_is_not_an_all_zero_fabricated_map() -> None:
    events = [
        ev(1, "item_start", client_ms=100, data={"item_id": "q1"}),
        ev(2, "item_end", client_ms=450, data={"item_id": "q1"}),
        ev(3, "item_start", client_ms=10, data={"item_id": "q2"}),
        ev(4, "item_end", client_ms=15, data={"item_id": "q2"}),
    ]
    assert item_dwell_ms(events) == {"q1": 350, "q2": 5}


def test_dropped_and_final_file_events_do_not_invent_views_or_dwell() -> None:
    """A dropped final dwell is not reconstructed from the client clock. A
    ``final: true`` segment still counts. A dwell with no view is not a view."""
    events = [
        ev(1, "file_meta", data={"io_supported": True, "n_items": 4}),
        ev(2, "file_record_view", client_ms=100, data={"record_id": "r_view"}),
        ev(3, "file_record_view", client_ms=5_000, data={"record_id": "r_view"}),
        ev(4, "file_record_dwell", client_ms=200, data={"record_id": "r_dwell", "dwell_ms": 640}),
        ev(
            5,
            "file_record_dwell",
            client_ms=9_000,
            data={"record_id": "r_final", "dwell_ms": 275, "final": True},
        ),
    ]
    stats = per_record_file_stats(events)
    assert stats is not None
    assert stats["r_view"] == {"views": 2, "dwell_ms": 0, "first_view_event": events[1]}
    assert stats["r_dwell"]["views"] == 0
    assert stats["r_dwell"]["dwell_ms"] == 640
    assert stats["r_final"]["views"] == 0
    assert stats["r_final"]["dwell_ms"] == 275
    out = _file_measures(events, file_size=4)
    assert out["file_viewed_count"] == 1
    assert out["file_coverage_frac"] == 0.25
    assert out["file_dwell_ms_total"] == 915


def test_file_coverage_frac_none_when_file_size_missing() -> None:
    """Missing file size is an undefined denominator (None, not 0). 0 is only
    the instrumented-and-empty case, and only when a real size exists."""
    viewed = [
        ev(1, "file_meta", data={"io_supported": True, "n_items": 3}),
        ev(2, "file_record_view", client_ms=10, data={"record_id": "r1"}),
    ]
    missing = _file_measures(viewed, file_size=None)
    assert missing["file_view_state"] == "measured"
    assert missing["file_viewed_count"] == 1
    assert missing["file_coverage_frac"] is None
    assert _file_measures(viewed, file_size=0)["file_coverage_frac"] is None
    empty = _file_measures(
        [ev(1, "file_meta", data={"io_supported": True, "n_items": 3})],
        file_size=5,
    )
    assert empty["file_view_state"] == "measured"
    assert empty["file_viewed_count"] == 0
    assert empty["file_coverage_frac"] == 0


def test_paused_clock_gap_in_client_ms_is_not_filled_in() -> None:
    """A hole in client_monotonic_ms (stored as client_ms) is not engagement
    time and is not scroll distance. Pair deltas and the terminal scroll
    counter stand; the pause between them is not imputed."""
    events = [
        ev(1, "item_start", client_ms=1_000, data={"item_id": "q1"}),
        ev(2, "item_end", client_ms=1_300, data={"item_id": "q1"}),
        ev(3, "blur", client_ms=1_300),
        ev(4, "focus", client_ms=40_000),
        ev(5, "item_start", client_ms=40_000, data={"item_id": "q1"}),
        ev(6, "item_end", client_ms=40_200, data={"item_id": "q1"}),
        ev(7, "file_meta", client_ms=1_000, data={"io_supported": True, "n_items": 3}),
        ev(
            8,
            "file_scroll_summary",
            client_ms=1_000,
            data={
                "distance_px": 80,
                "max_depth_frac": 0.1,
                "direction_reversals": 0,
                "farthest_index": 1,
            },
        ),
        ev(
            9,
            "file_scroll_summary",
            client_ms=40_000,
            data={
                "distance_px": 80,
                "max_depth_frac": 0.1,
                "direction_reversals": 0,
                "farthest_index": 1,
            },
        ),
    ]
    assert item_dwell_ms(events) == {"q1": 500}
    attention = attention_measures(events)
    assert attention["attention_blur_count"] == 1
    assert attention["attention_focus_count"] == 1
    assert attention["attention_hidden_ms_total"] == 0
    out = _file_measures(events)
    assert out["file_scroll_distance_px"] == 80
    assert out["file_max_scroll_depth"] == 0.1
    assert out["file_scroll_reversals"] == 0


def test_file_order_final_preserved_item_for_item() -> None:
    """The two snapshots have the same ids. Order is the list, not the set.
    The later epoch's final order (clock restarted at 0) is the last one."""
    first = ["r1", "r2", "r3"]
    final = ["r3", "r1", "r2"]
    assert set(first) == set(final)
    events = [
        ev(1, "file_meta", epoch=_EPOCH_A, client_ms=0, data={"io_supported": True, "n_items": 3}),
        ev(
            2,
            "file_order_final",
            epoch=_EPOCH_A,
            client_ms=100,
            data={"order": first, "n_items": 3},
        ),
        ev(
            3,
            "file_reorder",
            epoch=_EPOCH_B,
            client_ms=0,
            data={"record_id": "r3", "from_index": 2, "to_index": 0},
        ),
        ev(4, "file_order_final", epoch=_EPOCH_B, client_ms=0, data={"order": final, "n_items": 3}),
    ]
    finals = [e for e in telemetry_events(events) if e["type"] == "telemetry_file_order_final"]
    assert [e["payload"]["data"]["order"] for e in finals] == [first, final]
    assert finals[-1]["payload"]["data"]["order"] == ["r3", "r1", "r2"]
    assert finals[0]["payload"]["data"]["order"] != finals[-1]["payload"]["data"]["order"]
    out = _file_measures(events)
    assert out["file_reorder_count"] == 1
    assert out["file_reorder_distance"] == 2
