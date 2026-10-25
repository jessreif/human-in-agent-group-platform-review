"""BA-05/BA-15/BA-17: telemetry receipts, CSRF on /telemetry, and the poll cursor.

- BA-15: ``POST /telemetry`` mutates state, so it takes the same CSRF gate as every
  other participant POST — missing/mismatched tokens are refused before auth work.
- BA-05: bounded telemetry batches persist ``delivery_receipt`` rows
  (``received_at``/``rendered_at`` + per-item ``client_monotonic_ms``), emit the
  ``render_first_final`` telemetry event, and advance ``manipulation_exposure``
  receipt seqs on the referenced committed surface.
- BA-17: ``GET /events`` returns ``scanned_seq`` — the last event scanned, internal
  events included — so a poll cursor never starves behind an internal-only run.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from app.db import schema as s
from app.db.tx import db_now
from app.domain.manifests.store import ContentStore
from app.main import create_app
from app.services import credentials
from app.timing.clock import VirtualClock

from . import audit_helpers as ah
from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration


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


def _drive_to_warning(db: MigratedDatabase, store: ContentStore, pid: str, batch: str):
    """tp_demo_v1 to task, then commit the one-minute warning at active 300 s."""
    result = ah.drive_to_task(db, store, "tp_demo_v3", pid, batch)
    sid = result.session_id
    ah.set_active(db, sid, 300_000)
    ah.tick(db, store)
    with db.runtime.connect() as conn:
        exposure = (
            conn.execute(
                select(s.manipulation_exposure).where(
                    s.manipulation_exposure.c.session_id == sid,
                    s.manipulation_exposure.c.kind == "one_minute_warning",
                )
            )
            .mappings()
            .one()
        )
        warn_seq = int(exposure["committed_seq"])
        message_id = conn.execute(
            select(s.message.c.message_id).where(
                s.message.c.session_id == sid,
                s.message.c.channel == "system",
                s.message.c.committed_seq == warn_seq,
            )
        ).scalar_one()
    return result, warn_seq, str(message_id)


def test_telemetry_requires_csrf(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "TL0", "b-tl0")
    client = _authed_client(make_settings, migrated, result)
    with client:
        body = {"kind": "batch", "data": {"events": [{"kind": "focus", "client_monotonic_ms": 1}]}}
        resp = client.post("/telemetry", json=body)
        assert resp.status_code == 403
        assert resp.json()["error"] == "bad_csrf"
        resp = client.post(
            "/telemetry", json=body, headers={credentials.CSRF_HEADER: "wrong-token"}
        )
        assert resp.status_code == 403


def test_telemetry_persists_receive_render_receipts(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    result, warn_seq, warn_mid = _drive_to_warning(migrated, store, "TL1", "b-tl1")
    sid = result.session_id
    client = _authed_client(make_settings, migrated, result)
    with client:
        resp = client.post(
            "/telemetry",
            json={
                "kind": "batch",
                "data": {
                    "events": [
                        {
                            "kind": "receive",
                            "client_monotonic_ms": 1200,
                            "data": {"seq": warn_seq, "type": "warning_committed"},
                        },
                        {
                            "kind": "render",
                            "client_monotonic_ms": 1500,
                            "data": {"message_id": warn_mid, "channel": "system"},
                        },
                        {
                            "kind": "render_first_final",
                            "client_monotonic_ms": 1600,
                            "data": {"message_id": warn_mid, "channel": "system"},
                        },
                        {"kind": "not_a_kind", "client_monotonic_ms": 9, "data": {}},
                        {
                            "kind": "receive",
                            "client_monotonic_ms": 8,
                            "data": {"seq": warn_seq, "replayed": True},
                        },
                    ]
                },
            },
            headers={credentials.CSRF_HEADER: "csrf-test-token"},
        )
        assert resp.status_code == 200, resp.text

    with migrated.runtime.connect() as conn:
        receipts = (
            conn.execute(
                select(s.delivery_receipt).where(
                    s.delivery_receipt.c.session_id == sid,
                    s.delivery_receipt.c.seq == warn_seq,
                )
            )
            .mappings()
            .all()
        )
        by_kind = {r["kind"]: r for r in receipts}
        assert by_kind["initial"]["received_at"] is not None
        assert by_kind["initial"]["rendered_at"] is not None
        assert by_kind["initial"]["client_monotonic_ms"] == 1600  # latest receipt write wins
        assert by_kind["replay"]["received_at"] is not None
        assert by_kind["replay"]["client_monotonic_ms"] == 8
        telemetry_types = {
            r[0]
            for r in conn.execute(
                select(s.event.c.type).where(
                    s.event.c.session_id == sid, s.event.c.type.like("telemetry_%")
                )
            )
        }
        assert telemetry_types == {
            "telemetry_receive",
            "telemetry_render",
            "telemetry_render_first_final",
        }  # unknown kinds are dropped, never recorded
        exposure = (
            conn.execute(
                select(s.manipulation_exposure).where(
                    s.manipulation_exposure.c.session_id == sid,
                    s.manipulation_exposure.c.kind == "one_minute_warning",
                )
            )
            .mappings()
            .one()
        )
    assert exposure["received_seq"] is not None
    assert exposure["rendered_seq"] is not None and exposure["status"] == "rendered"


def test_telemetry_persists_file_kinds_with_active_ms(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """2026-10-12 case-file telemetry: the six file_*/share_attempt kinds persist
    as ``telemetry_<kind>`` events carrying a persist-time ``active_ms`` stamp
    (previously NULL), while unknown kinds are still dropped."""
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "TL3", "b-tl3")
    sid = result.session_id
    client = _authed_client(make_settings, migrated, result)
    with client:
        resp = client.post(
            "/telemetry",
            json={
                "kind": "batch",
                "data": {
                    "events": [
                        {
                            "kind": "file_record_view",
                            "client_monotonic_ms": 5000,
                            "data": {"record_id": "r1", "crate": 1, "source": "initial"},
                        },
                        {
                            "kind": "file_record_dwell",
                            "client_monotonic_ms": 8200,
                            "data": {"record_id": "r1", "dwell_ms": 3100},
                        },
                        {
                            "kind": "file_scroll_summary",
                            "client_monotonic_ms": 9000,
                            "data": {
                                "depth_frac": 0.8,
                                "max_depth_frac": 0.8,
                                "farthest_index": 38,
                                "n_items": 48,
                                "direction_reversals": 3,
                                "distance_px": 4200,
                            },
                        },
                        {
                            "kind": "file_reorder",
                            "client_monotonic_ms": 12000,
                            "data": {
                                "record_id": "r2",
                                "from_index": 4,
                                "to_index": 1,
                                "method": "drag",
                            },
                        },
                        {
                            "kind": "file_order_final",
                            "client_monotonic_ms": 300000,
                            # n_items matches the ids listed. A real 48-id order
                            # is test_telemetry_order_final_48_ids_and_oversize_data_degrades;
                            # item-for-item order is the unit test, not a 48-row session.
                            "data": {"order": ["r2", "r1"], "n_items": 2},
                        },
                        {
                            "kind": "share_attempt",
                            "client_monotonic_ms": 20000,
                            "data": {"record_id": "r2", "via": "drag", "result": "sent"},
                        },
                        {"kind": "file_not_a_kind", "client_monotonic_ms": 1, "data": {}},
                    ]
                },
            },
            headers={credentials.CSRF_HEADER: "csrf-test-token"},
        )
        assert resp.status_code == 200, resp.text

    with migrated.runtime.connect() as conn:
        rows = (
            conn.execute(
                select(s.event.c.type, s.event.c.active_ms, s.event.c.payload).where(
                    s.event.c.session_id == sid, s.event.c.type.like("telemetry_%")
                )
            )
            .mappings()
            .all()
        )
    by_type = {r["type"]: r for r in rows}
    assert set(by_type) == {
        "telemetry_file_record_view",
        "telemetry_file_record_dwell",
        "telemetry_file_scroll_summary",
        "telemetry_file_reorder",
        "telemetry_file_order_final",
        "telemetry_share_attempt",
    }  # file_not_a_kind dropped
    for r in rows:
        assert r["active_ms"] is not None and int(r["active_ms"]) >= 0
    final = by_type["telemetry_file_order_final"]["payload"]["data"]
    assert final["order"] == ["r2", "r1"]
    assert final["n_items"] == 2
    assert by_type["telemetry_share_attempt"]["payload"]["data"]["via"] == "drag"


def test_telemetry_batch_shares_one_persist_stamp_and_keeps_epoch(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """2026-10-13 (audit F06/F-stamps): every item in one POST shares the batch's
    persist-time ``active_ms`` (there is no honest per-item task time), while
    ``client_epoch`` passes through per item — a bad epoch shape degrades to
    None rather than 500ing or truncating the batch."""
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "TL4", "b-tl4")
    sid = result.session_id
    client = _authed_client(make_settings, migrated, result)
    with client:
        resp = client.post(
            "/telemetry",
            json={
                "kind": "batch",
                "data": {
                    "events": [
                        {"kind": "focus", "client_monotonic_ms": 100, "client_epoch": "ep-1"},
                        {"kind": "blur", "client_monotonic_ms": 200, "client_epoch": "ep-1"},
                        {
                            "kind": "file_meta",
                            "client_monotonic_ms": 300,
                            "client_epoch": "ep-2",
                            "data": {"io_supported": True, "n_items": 48},
                        },
                        {"kind": "focus", "client_monotonic_ms": 400, "client_epoch": 12345},
                        {"kind": "focus", "client_monotonic_ms": 500, "client_epoch": "x" * 65},
                    ]
                },
            },
            headers={credentials.CSRF_HEADER: "csrf-test-token"},
        )
        assert resp.status_code == 200, resp.text

    with migrated.runtime.connect() as conn:
        rows = (
            conn.execute(
                select(s.event.c.type, s.event.c.active_ms, s.event.c.payload).where(
                    s.event.c.session_id == sid, s.event.c.type.like("telemetry_%")
                )
            )
            .mappings()
            .all()
        )
    assert len(rows) == 5
    assert {r["active_ms"] for r in rows} != set()
    assert len({r["active_ms"] for r in rows}) == 1  # one batch = one stamp
    epochs = [r["payload"]["client_epoch"] for r in rows]
    assert epochs == ["ep-1", "ep-1", "ep-2", None, None]


def test_telemetry_order_final_48_ids_and_oversize_data_degrades(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """A real 48-record order payload persists intact (≈1.9 KB, under the data
    cap); a ``data`` payload over _TELEMETRY_DATA_MAX_CHARS degrades to ``{}``
    rather than 500ing or dropping the row (audit F-order/bounds)."""
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "TL5", "b-tl5")
    sid = result.session_id
    client = _authed_client(make_settings, migrated, result)
    order = [f"rec-{i:03d}" for i in range(48)]
    with client:
        resp = client.post(
            "/telemetry",
            json={
                "kind": "batch",
                "data": {
                    "events": [
                        {
                            "kind": "file_order_final",
                            "client_monotonic_ms": 299_900,
                            "data": {"order": order, "n_items": 48},
                        },
                        {
                            "kind": "file_scroll_summary",
                            "client_monotonic_ms": 299_950,
                            "data": {"distance_px": 1, "blob": "x" * 5000},
                        },
                    ]
                },
            },
            headers={credentials.CSRF_HEADER: "csrf-test-token"},
        )
        assert resp.status_code == 200, resp.text

    with migrated.runtime.connect() as conn:
        rows = {
            r["type"]: r["payload"]
            for r in conn.execute(
                select(s.event.c.type, s.event.c.payload).where(
                    s.event.c.session_id == sid, s.event.c.type.like("telemetry_%")
                )
            ).mappings()
        }
    assert rows["telemetry_file_order_final"]["data"]["order"] == order
    assert rows["telemetry_file_scroll_summary"]["data"] == {}  # oversize → empty


def test_telemetry_persists_after_task_close(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """Departure drains land after the task has closed (pagehide races the
    deadline): the session must still accept the batch, stamped with the
    post-close stage — the file's last moments are never silently dropped."""
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "TL6", "b-tl6")
    sid = result.session_id
    # Jump past the task deadline and let the scheduler close the task.
    ah.set_active(migrated, sid, 600_000)
    ah.tick(migrated, store)
    post_stage, _ = ah.stage(migrated, sid)
    assert post_stage != "task"

    client = _authed_client(make_settings, migrated, result)
    with client:
        resp = client.post(
            "/telemetry",
            json={
                "kind": "batch",
                "data": {
                    "events": [
                        {
                            "kind": "file_order_final",
                            "client_monotonic_ms": 299_900,
                            "client_epoch": "ep-9",
                            "data": {"order": ["r1", "r2"], "n_items": 2},
                        }
                    ]
                },
            },
            headers={credentials.CSRF_HEADER: "csrf-test-token"},
        )
        assert resp.status_code == 200, resp.text

    with migrated.runtime.connect() as conn:
        rows = (
            conn.execute(
                select(s.event.c.type, s.event.c.stage, s.event.c.active_ms).where(
                    s.event.c.session_id == sid, s.event.c.type.like("telemetry_%")
                )
            )
            .mappings()
            .all()
        )
    assert len(rows) == 1
    assert rows[0]["type"] == "telemetry_file_order_final"
    assert rows[0]["stage"] == post_stage  # stamped at flush, not interaction
    assert rows[0]["active_ms"] is not None


def test_events_cursor_advances_past_internal_events(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "TL2", "b-tl2")
    sid = result.session_id
    client = _authed_client(make_settings, migrated, result)
    with client:
        first = client.get("/events", params={"after_seq": 0})
        assert first.status_code == 200
        scanned_1 = first.json()["scanned_seq"]

    # Commit an internal-only event (never participant-visible) plus a visible one.
    with migrated.runtime.begin() as conn:
        row = conn.execute(
            select(s.session.c.stage, s.session.c.revision, s.session.c.next_event_seq).where(
                s.session.c.session_id == sid
            )
        ).one()
        base = int(row.next_event_seq)
        now = db_now(conn)
        for i, (etype, actor) in enumerate(
            (("generation_resolved", "scheduler"), ("message_delivered", "JT"))
        ):
            conn.execute(
                insert(s.event).values(
                    session_id=sid,
                    seq=base + i,
                    event_id=uuid.uuid4(),
                    stage=row.stage,
                    actor=actor,
                    type=etype,
                    revision=row.revision,
                    server_time=now,
                    payload={},
                )
            )
        conn.execute(
            update(s.session).where(s.session.c.session_id == sid).values(next_event_seq=base + 2)
        )

    with client:
        second = client.get("/events", params={"after_seq": scanned_1})
        assert second.status_code == 200
        body = second.json()
    assert body["scanned_seq"] == base + 1  # internal event scanned, not rescanned
    types = {e["type"] for e in body["events"]}
    assert "generation_resolved" not in types  # internal type stays filtered
    assert "message_delivered" in types
