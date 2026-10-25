"""INV-1700..1710: Study Admin Dashboard against disposable PostgreSQL.

Covers the researcher-only gate (401/403), filter validation (422), the shared
cohort contract (tombstone exclusion, mode scoping, receipt-based outcome
reconciliation), the REPEATABLE READ snapshot wrapper, and the section
envelopes for overview/readiness/timing/instruments/analysis/exports.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError

from app.db import schema as s
from app.db.tx import db_now
from app.domain.manifests.binding import bind
from app.domain.manifests.store import ContentStore
from app.main import create_app
from app.measurement.scoring import SCORING_VERSION
from app.services import adminauth, entry
from app.services.sessions import register_manifest
from app.timing.clock import VirtualClock

from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration

PW = "test-admin-passphrase-1234"
OPW = "test-operator-passphrase-5678"
CPW = "test-compensation-passphrase-90ab"
KEY = "k" * 40

DASHBOARD_GETS = (
    "/admin/dashboard/overview",
    "/admin/dashboard/readiness",
    "/admin/dashboard/timing",
    "/admin/dashboard/instruments",
    "/admin/dashboard/analysis",
    "/admin/exports",
)


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _client(
    make_settings: Any,
    db: MigratedDatabase,
    role: str = "researcher",
    **settings_overrides: Any,
) -> TestClient:
    app = create_app(
        _settings(make_settings, db, **settings_overrides),
        clock=VirtualClock(),
        engine=db.runtime,
        configure_logs=False,
    )
    client = TestClient(app, raise_server_exceptions=False)
    password = {"researcher": PW, "operator": OPW, "compensation": CPW}[role]
    resp = client.post("/admin/login", json={"role": role, "password": password})
    assert resp.status_code == 200, resp.text
    for name in (adminauth.SESSION_COOKIE, adminauth.CSRF_COOKIE):
        value = resp.cookies.get(name)
        if value:
            client.cookies.set(name, value, path="/")
    return client


def _settings(make_settings: Any, db: MigratedDatabase, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "APP_MODE": "qa",
        "DATABASE_URL": db.dsns.runtime,
        "ADMIN_PASSWORD": PW,
        "ADMIN_OPERATOR_PASSWORD": OPW,
        "ADMIN_COMPENSATION_PASSWORD": CPW,
        "PREVIEW_SIGNING_KEY": KEY,
    }
    values.update(overrides)
    return make_settings(**values)


def _seed_session(
    db: MigratedDatabase,
    store: ContentStore,
    pid: str,
    batch: str,
    *,
    manifest_id: str = "tp_demo_v3",
    app_mode: str = "qa",
) -> uuid.UUID:
    """Register the manifest + batch, enter a participant, return session_id."""
    bound = bind(store, manifest_id, mode="qa")
    with db.superuser.begin() as conn:
        register_manifest(conn, bound)
        conn.execute(
            pg_insert(s.batch)
            .values(
                batch_id=batch,
                study_key=bound.manifest.study_key,
                manifest_id=bound.manifest_id,
                mode="qa",
                enrollment_open=True,
            )
            .on_conflict_do_nothing()
        )
    result = entry.enter(
        db.runtime,
        store,
        query={"PROLIFIC_PID": pid, "STUDY_ID": "s", "SESSION_ID": "x", "batch": batch},
        app_mode=app_mode,
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256="ab" * 32,
    )
    return result.session_id


def _mark_completed(db: MigratedDatabase, sid: uuid.UUID, *, outcome: str = "completed") -> None:
    """Simulate a finished run: consent + stage=completion + receipt + result."""
    with db.runtime.begin() as conn:
        conn.execute(
            insert(s.consent_event).values(
                consent_id=uuid.uuid4(),
                session_id=sid,
                text_version="v3",
                action="consented",
            )
        )
        conn.execute(
            s.session.update().where(s.session.c.session_id == sid).values(stage="completion")
        )
        conn.execute(
            insert(s.completion_receipt).values(
                session_id=sid, outcome=outcome, reconciled={}, final_event_seq=9999
            )
        )
        conn.execute(
            insert(s.result).values(
                session_id=sid,
                scoring_version=SCORING_VERSION,
                correct_total=5,
                correct_teamdep=3,
                correct_ownfile=2,
                status="scored",
                finalized_seq=9999,
            )
        )


def _insert_telemetry(
    db: MigratedDatabase,
    sid: uuid.UUID,
    items: list[tuple[str, str, int, dict[str, Any]]],
) -> None:
    """Append ``(type, stage, active_ms, payload)`` event rows at free seqs —
    the persisted telemetry shape is ``{client_ms, client_epoch, data}``."""
    with db.runtime.begin() as conn:
        nxt = int(
            conn.execute(
                select(s.session.c.next_event_seq).where(s.session.c.session_id == sid)
            ).scalar_one()
        )
        now = db_now(conn)
        for i, (t, stage, active_ms, payload) in enumerate(items):
            conn.execute(
                insert(s.event).values(
                    session_id=sid,
                    seq=nxt + i,
                    event_id=uuid.uuid4(),
                    stage=stage,
                    actor="participant",
                    type=t,
                    server_time=now,
                    active_ms=active_ms,
                    payload=payload,
                )
            )
        conn.execute(
            s.session.update()
            .where(s.session.c.session_id == sid)
            .values(next_event_seq=nxt + len(items))
        )


def _seed_world(db: MigratedDatabase, sid: uuid.UUID, n_records: int = 2) -> None:
    """Minimal world + records + initial file memberships (record FK chain)."""
    wid = uuid.uuid4()
    with db.runtime.begin() as conn:
        conn.execute(
            insert(s.world_snapshot).values(
                world_id=wid,
                session_id=sid,
                generator="test",
                content={"crates": []},
                content_sha256="a" * 64,
            )
        )
        for i in range(1, n_records + 1):
            conn.execute(
                insert(s.record).values(
                    session_id=sid,
                    record_id=f"R{i}",
                    world_id=wid,
                    crate=1,
                    stamp="d1",
                    body=f"record {i}",
                    event_type="arrival",
                    timeline_ordinal=i,
                    content_sha256="b" * 64,
                )
            )
            conn.execute(
                insert(s.file_membership).values(
                    session_id=sid,
                    record_id=f"R{i}",
                    source="initial",
                    acquired_at=db_now(conn),
                )
            )


def _insert_message(db: MigratedDatabase, sid: uuid.UUID, seq: int = 1) -> None:
    with db.runtime.begin() as conn:
        conn.execute(
            insert(s.message).values(
                session_id=sid,
                message_id=uuid.uuid4(),
                actor="JT",
                channel="team",
                body="hello",
                accepted_seq=seq,
                committed_seq=seq,
            )
        )


# ----- auth boundary (INV-1710) -----------------------------------------------------------------


def test_dashboard_requires_login(migrated: MigratedDatabase, make_settings: Any) -> None:
    client = _client(make_settings, migrated)
    client.cookies.clear()
    for path in DASHBOARD_GETS:
        resp = client.get(path)
        assert resp.status_code == 401, f"{path}: {resp.status_code}"


@pytest.mark.parametrize("role", ["operator", "compensation"])
def test_dashboard_forbids_non_researcher(
    migrated: MigratedDatabase, make_settings: Any, role: str
) -> None:
    client = _client(make_settings, migrated, role=role)
    for path in DASHBOARD_GETS:
        resp = client.get(path)
        assert resp.status_code == 403, f"{path} as {role}: {resp.status_code}"
        assert resp.json()["error"] == "forbidden_role"


def test_dashboard_no_store_header(migrated: MigratedDatabase, make_settings: Any) -> None:
    client = _client(make_settings, migrated)
    for path in DASHBOARD_GETS:
        resp = client.get(path)
        assert resp.status_code == 200, f"{path}: {resp.status_code} {resp.text[:300]}"
        assert resp.headers["cache-control"] == "no-store"


# ----- filters ----------------------------------------------------------------------------------


def test_dashboard_filter_validation(migrated: MigratedDatabase, make_settings: Any) -> None:
    client = _client(make_settings, migrated)
    for qs, msg in (
        ("?mode=bogus", "mode"),
        ("?compatibility_key=not-a-key", "compatibility_key"),
        ("?since=not-a-date", "since"),
        ("?since=2026-10-02T00:00:00Z&until=2026-10-01T00:00:00Z", "ordering"),
        ("?page_size=9999", "page_size"),
    ):
        path = "/admin/dashboard/readiness" if "page_size" in qs else "/admin/dashboard/overview"
        resp = client.get(path + qs)
        assert resp.status_code == 422, f"{qs}: {resp.status_code}"
        assert resp.json()["error"] == "bad_filter", msg


# ----- cohort + envelope -------------------------------------------------------------------------


def test_overview_cohort_and_envelope(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    _seed_session(migrated, store, "D1", "qa-dash1")
    _mark_completed(migrated, _seed_session(migrated, store, "D2", "qa-dash1"))
    client = _client(make_settings, migrated)

    resp = client.get("/admin/dashboard/overview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["schema_version"] == "dashboard.a1"
    assert body["codebook_version"].startswith("cb-")
    assert body["snapshot_time"]
    cohort = body["cohort"]
    # Default mode=live: the qa seeds are out of scope.
    assert cohort["selected"] == 0
    assert cohort["tombstoned_excluded"] == 0

    # mode=live default excludes the qa seeds — they appear only with mode=qa.
    qa = client.get("/admin/dashboard/overview?mode=qa").json()
    assert qa["cohort"]["selected"] >= 2
    assert qa["mode_counts"].get("qa", 0) >= 2
    # The completed qa session counts in outcomes via receipt.
    assert qa["outcomes"].get("completed", 0) >= 1
    # QA is never research-eligible even with a completed receipt.
    assert qa["cohort"]["eligible_research"] == 0
    assert qa["cohort"]["excluded"]["not_live_mode"] >= 1


def test_overview_excludes_tombstoned(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    sid = _seed_session(migrated, store, "D3", "qa-dash2")
    with migrated.runtime.begin() as conn:
        conn.execute(
            insert(s.admin_audit).values(
                audit_id=uuid.uuid4(),
                actor="test",
                action="deletion_tombstone",
                session_id=sid,
                payload={"reason": "test"},
            )
        )
    client = _client(make_settings, migrated)
    body = client.get("/admin/dashboard/overview?mode=all").json()
    assert all(str(sid) not in str(r["session_id"]) for r in body["recent_sessions"])
    assert body["cohort"]["tombstoned_excluded"] >= 1


# ----- section envelopes -------------------------------------------------------------------------


def test_readiness_endpoint(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    sid = _seed_session(migrated, store, "D4", "qa-dash3")
    client = _client(make_settings, migrated)
    # audit_scope=all: the fresh seed is still in progress, so the default
    # completers view correctly returns an empty audit.
    assert client.get("/admin/dashboard/readiness?mode=qa").json()["audit"]["total"] == 0
    resp = client.get("/admin/dashboard/readiness?mode=qa&audit_scope=all")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sections"]["status"] == "ok"
    assert body["audit"]["total"] >= 1
    assert body["flag_definitions"]
    codes = {d["code"] for d in body["flag_definitions"]}
    assert "session_in_progress" in codes
    row = next(r for r in body["audit"]["rows"] if r["session_id"] == str(sid))
    assert "session_in_progress" in row["flags"]


def test_timing_endpoint(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    sid = _seed_session(migrated, store, "D5", "qa-dash4")
    client = _client(make_settings, migrated)
    resp = client.get("/admin/dashboard/timing?mode=qa")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["wall_durations"], list)
    # The seeded session's current stage is an open visit — counted as censored,
    # never averaged.
    assert sum(r["censored"] for r in body["wall_durations"]) >= 1
    # Per-session visit detail exists for every visit, open ones flagged.
    assert body["stage_visits_total"] == len(body["stage_visits"]) >= 1
    assert any(v["session_id"] == str(sid) and v["open"] for v in body["stage_visits"])


def test_timing_deadline_drift(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    """Deadline drift: the timer-hit-0 row reports idle-before (gap since the
    last participant action) and next-action (gap to the first command after
    the freeze). Telemetry flushes never count as actions."""
    sid = _seed_session(migrated, store, "D5b", "qa-dash4b")
    with migrated.runtime.begin() as conn:
        row = conn.execute(
            select(s.session.c.next_event_seq, s.session.c.revision).where(
                s.session.c.session_id == sid
            )
        ).one()
        base = int(row.next_event_seq)
        now = db_now(conn)
        for i, (etype, actor, offset_s) in enumerate(
            (
                ("answer_updated", "participant", -40),  # idle 40 s at the bell
                ("telemetry_blur", "participant", -30),  # passive — ignored
                ("task_deadline", "system", 0),
                ("answers_frozen", "system", 0),
                ("telemetry_receive", "participant", 2),  # passive — ignored
                ("stage_proceeded", "participant", 12),  # the continue click
            )
        ):
            conn.execute(
                insert(s.event).values(
                    session_id=sid,
                    seq=base + i,
                    event_id=uuid.uuid4(),
                    stage="task",
                    actor=actor,
                    type=etype,
                    revision=row.revision,
                    server_time=now + timedelta(seconds=offset_s),
                    payload={},
                )
            )
    client = _client(make_settings, migrated)
    resp = client.get("/admin/dashboard/timing?mode=qa")
    assert resp.status_code == 200, resp.text
    drift = [r for r in resp.json()["deadline_drift"] if r["session_id"] == str(sid)]
    assert len(drift) == 1
    row = drift[0]
    assert row["idle_before_ms"] == 40_000
    assert row["idle_before_type"] == "answer_updated"
    assert row["next_action_ms"] == 12_000
    assert row["next_action_type"] == "stage_proceeded"
    assert row["censored"] is False


def test_instruments_endpoint(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    _seed_session(migrated, store, "D6", "qa-dash5")
    client = _client(make_settings, migrated)
    resp = client.get("/admin/dashboard/instruments?mode=qa")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["codebook"]["codebook_version"].startswith("cb-")
    keys = {a["key"] for a in body["artifacts"]}
    assert any(k.startswith("consent.reference") for k in keys)
    post = next(a for a in body["artifacts"] if a["slot"] == "instruments.post_task")
    assert post["status"] == "ok"
    assert post["body"]["items"]


def test_analysis_endpoint(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    sid = _seed_session(migrated, store, "D7", "qa-dash6")
    _mark_completed(migrated, sid)
    client = _client(make_settings, migrated)
    resp = client.get("/admin/dashboard/analysis?mode=qa")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["power_inputs"]["status"] == "descriptive_only"
    assert body["power_inputs"]["mde_scenarios"]["status"] == "unsupported_model"
    # QA sessions never enter the eligible research cohort.
    assert body["cohort"]["eligible_research"] == 0
    assert body["findings"][0]["card_id"] == "empty_cohort"
    for cell in body["contrasts"]:
        assert cell["inference"]["status"] == "analysis_not_configured"
        assert cell["inference"]["p"] is None


def test_exports_catalog_unconfigured(migrated: MigratedDatabase, make_settings: Any) -> None:
    client = _client(make_settings, migrated)
    resp = client.get("/admin/exports")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unavailable"
    assert body["exports"] == []


def test_exports_catalog_lists_manifests(
    migrated: MigratedDatabase, make_settings: Any, tmp_path: Any
) -> None:
    (tmp_path / "study-abc123").mkdir()
    (tmp_path / "study-abc123" / "manifest.json").write_text(
        '{"schema":"investigators.export_manifest/1","kind":"research_study",'
        '"snapshot_time":"2026-10-01T00:00:00Z",'
        '"schema_version":"es-1","metric_version":"mv-1","manifest_sha256":"x",'
        '"tables":{"result":{"row_count":3,"file":"result.csv"}}}'
    )
    # Compensation products must never reach the researcher listing (DA-23).
    (tmp_path / "payment-def456").mkdir()
    (tmp_path / "payment-def456" / "manifest.json").write_text(
        '{"schema":"investigators.export_manifest/1","kind":"payment",'
        '"snapshot_time":"2026-10-01T00:00:00Z","tables":{"payout":{"row_count":2,"file":"payout.csv"}}}'
    )
    (tmp_path / "not-a-dir.txt").write_text("junk")
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "manifest.json").write_text("{invalid")
    client = _client(make_settings, migrated, ADMIN_EXPORT_DIR=str(tmp_path))
    body = client.get("/admin/exports").json()
    assert body["status"] == "ok"
    by_dir = {e["dir"]: e for e in body["exports"]}
    assert by_dir["study-abc123"]["kind"] == "research_study"
    assert by_dir["study-abc123"]["status"] == "published"
    assert by_dir["study-abc123"]["tables"][0]["name"] == "result"
    assert by_dir["bad"]["status"] == "invalid"
    assert "payment-def456" not in by_dir
    assert body["restricted_entries"] >= 1
    assert body["skipped_entries"] >= 1  # not-a-dir.txt


# ----- read-only snapshot ------------------------------------------------------------------------


def test_dashboard_snapshot_is_read_only(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    """A write attempt inside the snapshot tx must fail — proves READ ONLY mode."""
    _seed_session(migrated, store, "D8", "qa-dash7")
    client = _client(make_settings, migrated)
    before = client.get("/admin/dashboard/overview?mode=qa").json()["cohort"]["selected"]
    with migrated.runtime.connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with pytest.raises(DBAPIError):
            conn.execute(
                insert(s.batch).values(
                    batch_id="x",
                    study_key="x",
                    manifest_id="x",
                    mode="qa",
                    enrollment_open=True,
                )
            )
    after = client.get("/admin/dashboard/overview?mode=qa").json()["cohort"]["selected"]
    assert before == after


# ----- telemetry (cb-1.8): readiness flags, scalars, vocabulary -----------------------------------


def test_readiness_telemetry_flags(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    """Telemetry-health flags + per-session summary in the readiness audit."""
    healthy = _seed_session(migrated, store, "TH1", "qa-tel")
    _mark_completed(migrated, healthy)
    _seed_world(migrated, healthy)
    _insert_message(migrated, healthy)
    _insert_telemetry(
        migrated,
        healthy,
        [
            (
                "telemetry_file_meta",
                "task",
                1000,
                {"client_epoch": "e1", "data": {"io_supported": True, "file_size": 2}},
            ),
            (
                "telemetry_receive",
                "task",
                1100,
                {"client_epoch": "e1", "client_ms": 800, "data": {"seq": 1}},
            ),
            (
                "telemetry_render",
                "task",
                1150,
                {"client_epoch": "e1", "client_ms": 900, "data": {"seq": 1}},
            ),
            (
                "telemetry_file_record_view",
                "task",
                2000,
                {"client_epoch": "e1", "client_ms": 5000, "data": {"record_id": "R1", "ms": 400}},
            ),
        ],
    )

    empty = _seed_session(migrated, store, "TH2", "qa-tel")
    _mark_completed(migrated, empty)

    legacy = _seed_session(migrated, store, "TH3", "qa-tel")
    _mark_completed(migrated, legacy)
    _insert_telemetry(
        migrated,
        legacy,
        [
            ("telemetry_receive", "task", 100, {"data": {"seq": 1}}),
            ("telemetry_render", "task", 150, {"data": {"seq": 1}}),
        ],
    )

    no_meta = _seed_session(migrated, store, "TH4", "qa-tel")
    _mark_completed(migrated, no_meta)
    _insert_telemetry(
        migrated,
        no_meta,
        [
            (
                "telemetry_visibility",
                "task",
                100,
                {"client_epoch": "e1", "client_ms": 10, "data": {"state": "hidden"}},
            ),
        ],
    )

    multi = _seed_session(migrated, store, "TH5", "qa-tel")
    _mark_completed(migrated, multi)
    _insert_telemetry(
        migrated,
        multi,
        [
            (
                "telemetry_visibility",
                "task",
                100,
                {"client_epoch": "e1", "client_ms": 10, "data": {"state": "hidden"}},
            ),
            (
                "telemetry_file_meta",
                "task",
                200,
                {
                    "client_epoch": "e2",
                    "client_ms": 10,
                    "data": {"io_supported": True, "file_size": 4},
                },
            ),
        ],
    )

    client = _client(make_settings, migrated)
    body = client.get("/admin/dashboard/readiness?mode=qa&audit_scope=completers").json()
    rows = {r["session_id"]: r for r in body["audit"]["rows"]}

    h = rows[str(healthy)]
    assert h["telemetry"]["events"] == 4
    assert h["telemetry"]["epochs"] == 1
    assert h["telemetry"]["file_meta"] is True
    assert h["telemetry"]["receipts"] == 2
    assert not [f for f in h["flags"] if f.startswith("telemetry_")]

    assert "telemetry_absent" in rows[str(empty)]["flags"]
    assert rows[str(empty)]["telemetry"]["events"] == 0
    assert "telemetry_legacy_bundle" in rows[str(legacy)]["flags"]
    assert rows[str(legacy)]["telemetry"]["epochs"] == 0
    assert "telemetry_file_missing" in rows[str(no_meta)]["flags"]
    assert "telemetry_multi_epoch" in rows[str(multi)]["flags"]

    # Summary counts each flag once per affected session.
    summary = body["flag_summary"]
    assert summary["telemetry_absent"] >= 1
    assert summary["telemetry_file_missing"] >= 1


def test_readiness_telemetry_receipts_absent(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    """Committed chat + zero receive/render receipts = queue starvation."""
    sid = _seed_session(migrated, store, "TH6", "qa-tel2")
    _mark_completed(migrated, sid)
    _insert_message(migrated, sid)
    # Some telemetry persisted (epoch-bearing) but no receipts at all.
    _insert_telemetry(
        migrated,
        sid,
        [
            (
                "telemetry_file_meta",
                "task",
                500,
                {"client_epoch": "e1", "data": {"io_supported": True, "file_size": 1}},
            ),
        ],
    )
    client = _client(make_settings, migrated)
    body = client.get("/admin/dashboard/readiness?mode=qa").json()
    row = next(r for r in body["audit"]["rows"] if r["session_id"] == str(sid))
    assert "telemetry_receipts_absent" in row["flags"]
    assert "telemetry_absent" not in row["flags"]
    assert "telemetry_file_missing" not in row["flags"]


def test_analysis_telemetry_scalars(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    """Telemetry measures flow into the diagnostic cohort's power inputs."""
    sid = _seed_session(migrated, store, "TA1", "qa-tel3")
    _mark_completed(migrated, sid)
    _seed_world(migrated, sid)  # file_size = 2
    _insert_telemetry(
        migrated,
        sid,
        [
            (
                "telemetry_file_meta",
                "task",
                1000,
                {
                    "client_epoch": "e1",
                    "client_ms": 500,
                    "data": {"io_supported": True, "file_size": 2},
                },
            ),
            (
                "telemetry_file_record_view",
                "task",
                2000,
                {"client_epoch": "e1", "client_ms": 5000, "data": {"record_id": "R1", "ms": 400}},
            ),
            (
                "telemetry_file_record_dwell",
                "task",
                2100,
                {
                    "client_epoch": "e1",
                    "client_ms": 6000,
                    "data": {"record_id": "R1", "dwell_ms": 1200},
                },
            ),
            (
                "telemetry_file_scroll_summary",
                "task",
                2200,
                {
                    "client_epoch": "e1",
                    "client_ms": 7000,
                    "data": {
                        "depth_frac": 1.0,
                        "max_depth_frac": 1.0,
                        "farthest_index": 1,
                        "n_items": 2,
                        "direction_reversals": 2,
                        "distance_px": 800,
                    },
                },
            ),
            (
                "telemetry_share_attempt",
                "task",
                2300,
                {
                    "client_epoch": "e1",
                    "client_ms": 8000,
                    "data": {"record_id": "R1", "via": "drag", "result": "sent"},
                },
            ),
            (
                "telemetry_visibility",
                "task",
                2400,
                {"client_epoch": "e1", "client_ms": 9000, "data": {"state": "hidden"}},
            ),
            (
                "telemetry_visibility",
                "task",
                2500,
                {"client_epoch": "e1", "client_ms": 12000, "data": {"state": "visible"}},
            ),
        ],
    )
    # A durable participant share of R1 lets the channel join attribute it.
    with migrated.runtime.begin() as conn:
        conn.execute(
            insert(s.share).values(
                session_id=sid,
                share_id=uuid.uuid4(),
                sharer="participant",
                record_id="R1",
                recipients=["JT", "PM"],
                source="participant_action",
                delivered_seq=1,
                delivered_at=db_now(conn),
            )
        )

    client = _client(make_settings, migrated)
    body = client.get("/admin/dashboard/analysis?mode=qa").json()
    diag = body["diagnostics"]
    assert diag["n_sessions"] >= 1
    pi = {r["measure"]: r for r in diag["power_inputs"]}

    assert pi["telemetry.events"]["n_measured"] == 1
    assert pi["telemetry.events"]["mean"] == 7.0
    assert pi["telemetry.client_epochs"]["mean"] == 1.0
    assert pi["file.coverage_frac"]["mean"] == 0.5  # 1 of 2 file records viewed
    assert pi["file.dwell_ms_total"]["mean"] == 1200.0
    assert pi["file.max_scroll_depth"]["mean"] == 1.0
    assert pi["file.reached_bottom"]["mean"] == 1.0
    assert pi["share.attempts"]["mean"] == 1.0
    assert pi["share.via_drag_frac"]["mean"] == 1.0
    assert pi["attention.hidden_ms_total"]["mean"] == 3000.0
    assert pi["attention.blur_count"]["mean"] == 0.0
    # No added_from_team intel → no return-latency observations (missing, not 0).
    assert pi["file.post_share_return_ms"]["n_measured"] == 0

    # The same measures appear in arm contrasts with descriptive-only inference.
    contrast = next(c for c in diag["contrasts"] if c["measure"] == "file.dwell_ms_total")
    assert contrast["inference"]["status"] == "analysis_not_configured"


def test_instruments_telemetry_vocabulary(migrated: MigratedDatabase, make_settings: Any) -> None:
    """Survey Documentation exposes the telemetry event vocabulary."""
    client = _client(make_settings, migrated)
    body = client.get("/admin/dashboard/instruments?mode=qa").json()
    kinds = {k["kind"] for k in body["codebook"]["telemetry_kinds"]}
    assert "file_meta" in kinds
    assert "share_attempt" in kinds
    assert len(kinds) == 15  # parity with the participant.py whitelist
