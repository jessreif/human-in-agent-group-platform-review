"""TS-B12: exported telemetry cells versus ``session_metrics`` and a hand count.

One disposable session. Telemetry is inserted with ``POST /telemetry`` (the
BA-05 path), not a hand-written metrics dict. The F20 unit checklist is the
module docstring of ``tests/test_telemetry_measures.py``.

There is no ``session_metrics`` export table. ``session_metrics`` is the
direct reader; ``participant_wide`` spreads the same attention and file
measures. ``crate_long`` and ``evidence_transfer`` carry the per-crate and
per-record exposure cells. Raw ``client_epoch`` lives in ``event.payload``;
``delivery_receipt.client_monotonic_ms`` is the receipt clock column. Tables
that do not carry those fields are asserted not to.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import schema as s
from app.domain.manifests.store import ContentStore
from app.export.study import export_study
from app.main import create_app
from app.measurement.metrics import session_metrics_for
from app.measurement.rows import load_session_rows
from app.measurement.telemetry import TASK_WINDOW_STAGES, file_telemetry_measures
from app.services import credentials
from app.timing.clock import VirtualClock

from . import audit_helpers as ah
from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration

_EPOCH_A = "ep-reload-a"
_EPOCH_B = "ep-reload-b"
_DWELL_MS = 2_500
_STAMP_COLUMNS = ("client_epoch", "client_monotonic_ms")


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _authed_client(make_settings: Any, db: MigratedDatabase, result: Any) -> TestClient:
    settings = make_settings(
        APP_MODE="qa",
        DATABASE_URL=db.dsns.runtime,
        PREVIEW_SIGNING_KEY="k" * 40,
    )
    app = create_app(settings, clock=VirtualClock(), engine=db.runtime, configure_logs=False)
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set(credentials.SESSION_COOKIE, result.credential_token, path="/")
    client.cookies.set(credentials.CSRF_COOKIE, "csrf-test-token", path="/")
    client.cookies.set("inv_writer", str(result.writer_id), path="/")
    return client


def _post(client: TestClient, events: list[dict[str, Any]]) -> None:
    resp = client.post(
        "/telemetry",
        json={"kind": "batch", "data": {"events": events}},
        headers={credentials.CSRF_HEADER: "csrf-test-token"},
    )
    assert resp.status_code == 200, resp.text


def test_export_telemetry_values_match_session_metrics_and_hand_fraction(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """Two epochs, one in-window view/dwell, one joined share and one stray
    attempt, and a final order of this session's real record ids.

    Coverage and dwell are checked against a hand count of the stored rows, so
    a patch that forces ``file_coverage_frac`` to 0 fails even when the export
    and ``session_metrics`` share the bug.
    """
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "B12", "b-b12")
    sid = result.session_id
    with migrated.runtime.connect() as conn:
        members = (
            conn.execute(
                select(
                    s.file_membership.c.record_id,
                    s.record.c.crate,
                    s.record.c.timeline_ordinal,
                )
                .select_from(
                    s.file_membership.join(
                        s.record,
                        (s.record.c.session_id == s.file_membership.c.session_id)
                        & (s.record.c.record_id == s.file_membership.c.record_id),
                    )
                )
                .where(s.file_membership.c.session_id == sid)
                .order_by(s.record.c.record_id)
            )
            .mappings()
            .all()
        )
        anchor_seq = int(
            conn.execute(
                select(s.event.c.seq)
                .where(s.event.c.session_id == sid)
                .order_by(s.event.c.seq)
                .limit(1)
            ).scalar_one()
        )
    by_crate: dict[int, list[Any]] = {}
    for member in members:
        by_crate.setdefault(int(member["crate"]), []).append(member)
    assert len(by_crate) >= 2
    shared_crate = sorted(by_crate)[0]
    shared = max(
        by_crate[shared_crate],
        key=lambda m: (int(m["timeline_ordinal"]), str(m["record_id"])),
    )
    shared_id = str(shared["record_id"])
    other = next(m for m in members if str(m["record_id"]) != shared_id)
    other_id = str(other["record_id"])
    ids = [str(m["record_id"]) for m in members]
    assert len(ids) >= 2
    rotated = ids[1:] + ids[:1]
    assert rotated != sorted(rotated)
    assert set(rotated) == set(ids)

    ah.run(
        migrated,
        store,
        sid,
        result.writer_id,
        "record_share",
        {"record_id": shared_id},
        ah.stage(migrated, sid)[1],
    )
    epoch_a = [
        {
            "kind": "file_meta",
            "client_monotonic_ms": 0,
            "client_epoch": _EPOCH_A,
            "data": {"io_supported": True, "n_items": len(ids)},
        },
        {
            "kind": "receive",
            "client_monotonic_ms": 1_200,
            "client_epoch": _EPOCH_A,
            "data": {"seq": anchor_seq},
        },
        {
            "kind": "file_record_view",
            "client_monotonic_ms": 100,
            "client_epoch": _EPOCH_A,
            "data": {"record_id": shared_id},
        },
        {
            "kind": "file_record_dwell",
            "client_monotonic_ms": 9_000,
            "client_epoch": _EPOCH_A,
            "data": {"record_id": shared_id, "dwell_ms": _DWELL_MS},
        },
        {
            "kind": "share_attempt",
            "client_monotonic_ms": 5_000,
            "client_epoch": _EPOCH_A,
            "data": {"record_id": shared_id, "via": "drag", "result": "sent"},
        },
        {
            "kind": "share_attempt",
            "client_monotonic_ms": 5_100,
            "client_epoch": _EPOCH_A,
            "data": {"record_id": other_id, "via": "button", "result": "sent"},
        },
    ]
    epoch_b = [
        {
            "kind": "file_order_final",
            "client_monotonic_ms": 0,
            "client_epoch": _EPOCH_B,
            "data": {"order": rotated, "n_items": len(rotated)},
        },
        {
            "kind": "focus",
            "client_monotonic_ms": 0,
            "client_epoch": _EPOCH_B,
        },
    ]
    client = _authed_client(make_settings, migrated, result)
    with client:
        # Separate posts so each epoch has its own persist-time batch stamp.
        ah.set_active(migrated, sid, 10_000)
        _post(client, epoch_a)
        ah.set_active(migrated, sid, 80_000)
        _post(client, epoch_b)

    with migrated.research.connect() as conn:
        bundle = session_metrics_for(conn, sid)["telemetry"]
        rows = load_session_rows(conn, sid)
    direct = file_telemetry_measures(
        list(rows.events),
        file_size=len(rows.file_memberships) or None,
        added_from_team=[f for f in rows.file_memberships if f["source"] == "added_from_team"],
        participant_shares=[r for r in rows.shares if r["sharer"] == "participant"],
        teammate_shares=[r for r in rows.shares if r["sharer"] != "participant"],
        event_active_ms=rows.event_active_ms,
    )
    tel = [e for e in rows.events if str(e["type"]).startswith("telemetry_")]
    assert tel
    assert {e["stage"] for e in tel} <= TASK_WINDOW_STAGES
    viewed = {
        e["payload"]["data"]["record_id"] for e in tel if e["type"] == "telemetry_file_record_view"
    }
    dwell = sum(
        int(e["payload"]["data"]["dwell_ms"])
        for e in tel
        if e["type"] == "telemetry_file_record_dwell"
    )
    n_file = len(rows.file_memberships)
    assert viewed == {shared_id}
    assert dwell == _DWELL_MS
    assert n_file >= 2
    expected_coverage = 1 / n_file
    assert expected_coverage not in (0, 1)
    assert direct["file_coverage_frac"] == expected_coverage
    assert direct["file_dwell_ms_total"] == _DWELL_MS
    assert direct["file_viewed_count"] == 1
    participant_shares = [r for r in rows.shares if r["sharer"] == "participant"]
    assert [str(r["record_id"]) for r in participant_shares] == [shared_id]
    assert direct["share_participant_total"] == 1
    assert direct["share_attempts"] == 2
    assert direct["share_channel_known"] == 1
    assert direct["share_channel_unknown"] == 0
    assert direct["share_via_drag_frac"] == 1.0
    assert direct["share_via_drag_frac_observed"] == 0.5
    assert direct["client_epoch_count"] == 2
    assert direct["file_post_share_return_ms"] is None
    assert direct["file_post_share_return_n"] == 0
    assert bundle["attention_focus_count"] == 1
    assert bundle["attention_state"] == "measured"
    for key, value in direct.items():
        assert bundle[key] == value, key

    export = export_study(migrated.research, session_ids=[sid])
    assert "session_metrics" not in export.tables
    wide_rows = export.tables["participant_wide"].rows
    assert len(wide_rows) == 1
    wide = wide_rows[0]
    for key, value in bundle.items():
        assert wide[key] == value, key
    assert wide["file_coverage_frac"] == expected_coverage
    assert wide["file_dwell_ms_total"] == _DWELL_MS
    for column in _STAMP_COLUMNS:
        assert column not in wide

    epochs_at_zero = {e["payload"]["client_epoch"] for e in tel if e["payload"]["client_ms"] == 0}
    assert epochs_at_zero == {_EPOCH_A, _EPOCH_B}
    stamps: dict[str, set[int]] = {}
    for event in tel:
        stamps.setdefault(str(event["payload"]["client_epoch"]), set()).add(int(event["active_ms"]))
    assert len(stamps[_EPOCH_A]) == 1
    assert len(stamps[_EPOCH_B]) == 1
    assert next(iter(stamps[_EPOCH_B])) > next(iter(stamps[_EPOCH_A])) + 1_000

    exported_events = [
        e
        for e in export.tables["event"].rows
        if e["session_id"] == sid and str(e["type"]).startswith("telemetry_")
    ]
    stored_by_seq = {int(e["seq"]): e for e in tel}
    assert {int(e["seq"]) for e in exported_events} == set(stored_by_seq)
    for exported in exported_events:
        stored = stored_by_seq[int(exported["seq"])]
        assert "client_epoch" not in exported
        assert "client_monotonic_ms" not in exported
        assert exported["active_ms"] == stored["active_ms"]
        assert exported["payload"]["client_epoch"] == stored["payload"]["client_epoch"]
        assert exported["payload"]["client_ms"] == stored["payload"]["client_ms"]
        assert exported["payload"]["data"] == stored["payload"]["data"]
    posted = {
        (item["kind"], item["client_monotonic_ms"], item["client_epoch"]): item
        for item in epoch_a + epoch_b
    }
    for event in tel:
        kind = str(event["type"]).removeprefix("telemetry_")
        item = posted[(kind, event["payload"]["client_ms"], event["payload"]["client_epoch"])]
        assert event["payload"]["client_epoch"] == item["client_epoch"]
        assert event["payload"]["client_ms"] == item["client_monotonic_ms"]
        assert event["payload"]["data"] == item.get("data", {})
    final = next(e for e in tel if e["type"] == "telemetry_file_order_final")
    assert final["payload"]["data"]["order"] == rotated
    assert final["payload"]["data"]["n_items"] == len(rotated)
    assert final["payload"]["data"]["order"] != sorted(rotated)

    receipts = [
        r
        for r in export.tables["delivery_receipt"].rows
        if r["session_id"] == sid and int(r["seq"]) == anchor_seq
    ]
    assert len(receipts) == 1
    assert receipts[0]["client_monotonic_ms"] == 1_200
    assert "client_epoch" not in receipts[0]
    receive = next(e for e in tel if e["type"] == "telemetry_receive")
    assert receive["payload"]["client_epoch"] == _EPOCH_A
    assert receive["payload"]["client_ms"] == 1_200

    for name in ("share", "crate_long", "evidence_transfer"):
        table_rows = [r for r in export.tables[name].rows if r["session_id"] == sid]
        assert table_rows
        for row in table_rows:
            for column in _STAMP_COLUMNS:
                assert column not in row, name

    shares = [
        r
        for r in export.tables["share"].rows
        if r["session_id"] == sid and r["sharer"] == "participant"
    ]
    assert [str(r["record_id"]) for r in shares] == [shared_id]
    assert "file_coverage_frac" not in shares[0]

    crates = {
        int(r["crate"]): r for r in export.tables["crate_long"].rows if r["session_id"] == sid
    }
    assert crates[shared_crate]["file_latest_record_viewed"] is True
    assert crates[shared_crate]["file_n_records_viewed"] == 1
    assert crates[shared_crate]["file_dwell_ms"] == _DWELL_MS
    other_crate = int(other["crate"])
    if other_crate != shared_crate:
        assert crates[other_crate]["file_latest_record_viewed"] is False
        assert crates[other_crate]["file_dwell_ms"] == 0
    world_in_file = []
    for crate, group in by_crate.items():
        file_ids = {str(m["record_id"]) for m in group}
        world_rows = [r for r in rows.records if int(r["crate"]) == crate]
        latest_world = max(world_rows, key=lambda r: int(r["timeline_ordinal"]))
        in_file = str(latest_world["record_id"]) in file_ids
        world_in_file.append(in_file)
        assert crates[crate]["file_latest_world_in_file"] is in_file
        latest_file = max(group, key=lambda m: int(m["timeline_ordinal"]))
        viewed_latest = str(latest_file["record_id"]) == shared_id
        assert crates[crate]["file_latest_record_viewed"] is viewed_latest
    assert True in world_in_file
    assert False in world_in_file

    evidence = {
        str(r["record_id"]): r
        for r in export.tables["evidence_transfer"].rows
        if r["session_id"] == sid
    }
    assert evidence[shared_id]["viewed_in_file"] is True
    assert evidence[shared_id]["file_dwell_ms"] == _DWELL_MS
    assert evidence[shared_id]["is_latest_in_file"] is True
    assert evidence[other_id]["viewed_in_file"] is False
    assert evidence[other_id]["file_dwell_ms"] == 0
    assert "file_coverage_frac" not in evidence[shared_id]
