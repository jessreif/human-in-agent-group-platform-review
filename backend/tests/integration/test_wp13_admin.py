"""INV-1301..1309: admin surface against disposable PostgreSQL.

Covers the HTTP level exactly as the deployment exposes it: session monitor, replay
(participant projection vs restricted truth), audited support actions, previews,
enrollment controls, entitlements, retention/deletion/tombstones, redaction review and
the role boundary (researcher/operator/compensation) enforced app-side — Apache never
authenticates these paths.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select

from app.db import schema as s
from app.domain.manifests.store import ContentStore
from app.main import create_app
from app.services import adminauth, credentials, entry
from app.services.entry import EntryResult
from app.timing.clock import VirtualClock

from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration

PW = "test-admin-passphrase-1234"
OPW = "test-operator-passphrase-5678"
CPW = "test-compensation-passphrase-90ab"
KEY = "k" * 40
BUILD_SHA = "ab" * 32

ALL_COMBOS = (
    ("tp_demo_v1", "high"),
    ("tp_demo_v1", "low"),
    ("pe_demo_v1", "eval"),
    ("pe_demo_v1", "control"),
    ("tp_review_first_v2", "high"),
    ("tp_review_first_v2", "low"),
    ("pe_pilot_v2", "eval"),
    ("pe_pilot_v2", "control"),
    # BA-37: consent-v2 lineage — literal copy, superseded by v3 ('#XXXX-XXXX' fix).
    ("tp_demo_v2", "high"),
    ("tp_demo_v2", "low"),
    ("pe_demo_v2", "eval"),
    ("pe_demo_v2", "control"),
    ("tp_review_first_v3", "high"),
    ("tp_review_first_v3", "low"),
    ("pe_pilot_v3", "eval"),
    ("pe_pilot_v3", "control"),
    # BA-37 correction: consent-v3 lineage — the current profiles.
    ("tp_demo_v3", "high"),
    ("tp_demo_v3", "low"),
    ("pe_demo_v3", "eval"),
    ("pe_demo_v3", "control"),
    ("tp_review_first_v4", "high"),
    ("tp_review_first_v4", "low"),
    ("pe_pilot_v4", "eval"),
    ("pe_pilot_v4", "control"),
    # O-1/O-2/O-3 lineage: fallback_bank v3 (asks_intel_nl + identity_probe).
    ("tp_demo_v5", "high"),
    ("tp_demo_v5", "low"),
    ("pe_demo_v5", "eval"),
    ("pe_demo_v5", "control"),
    ("tp_review_first_v6", "high"),
    ("tp_review_first_v6", "low"),
    ("pe_pilot_v6", "eval"),
    ("pe_pilot_v6", "control"),
    ("tp_demo_nl_v2", "high"),
    ("tp_demo_nl_v2", "low"),
    ("pe_demo_nl_v2", "eval"),
    ("pe_demo_nl_v2", "control"),
    # UX round 4 lineage (UX_Feedback_v4): requests.demo@v2, fallback v4,
    # post_task v2 with the pre-debrief choice item — the current profiles.
    ("tp_demo_v6", "high"),
    ("tp_demo_v6", "low"),
    ("pe_demo_v6", "eval"),
    ("pe_demo_v6", "control"),
    ("tp_review_first_v7", "high"),
    ("tp_review_first_v7", "low"),
    ("pe_pilot_v7", "eval"),
    ("pe_pilot_v7", "control"),
    ("tp_demo_nl_v3", "high"),
    ("tp_demo_nl_v3", "low"),
    ("pe_demo_nl_v3", "eval"),
    ("pe_demo_nl_v3", "control"),
    # BA-96/99 fielded lineage: approved-only artifacts, model generation on.
    ("tp_review_first_v10", "high"),
    ("tp_review_first_v10", "low"),
    # Scripted-teammate comparison member (UX v6): structured asks,
    # deterministic_only generation, no model draw.
    ("tp_scripted_v1", "high"),
    ("tp_scripted_v1", "low"),
    # v11 (owner direction 2026-10-07): drops the solo_review phase; identical
    # bindings otherwise — becomes the newest review-first-family build.
    ("tp_review_first_v11", "high"),
    ("tp_review_first_v11", "low"),
    # 2026-10-08 four-condition launch: approved typed-chat AI-teammate and scripted-
    # teammate builds (v11 bindings + one switch each), randomized by one group link.
    ("tp_ai_v1", "high"),
    ("tp_ai_v1", "low"),
    ("tp_scripted_v2", "high"),
    ("tp_scripted_v2", "low"),
    # v3 (same-day correction): scripted build also drops the ask picker — a clean
    # 2x2 where pressure and teammate engine are the only differences.
    ("tp_scripted_v3", "high"),
    ("tp_scripted_v3", "low"),
    # v2/v4 (owner direction 2026-10-08): all measurement items required —
    # consent.reference@v5 (skip promise removed), required-only instruments,
    # and own_rating_skippable=false. These are the fielded launch builds.
    ("tp_ai_v2", "high"),
    ("tp_ai_v2", "low"),
    ("tp_scripted_v4", "high"),
    ("tp_scripted_v4", "low"),
    # 2026-10-09 pilot revision: 300 s task, consent v6 ($3.00), teammate remarks.
    ("tp_ai_v4", "high"),
    ("tp_ai_v4", "low"),
    ("tp_scripted_v6", "high"),
    ("tp_scripted_v6", "low"),
    # RCA-2026-10-09 remediation: repin prompt.assistant_demo@v3 — narrowed
    # assistant contract after the v5 100%-rejection incident.
    ("tp_ai_v6", "high"),
    ("tp_ai_v6", "low"),
    ("tp_scripted_v8", "high"),
    ("tp_scripted_v8", "low"),
    # 2026-10-10 incentive compatibility: consent v7 ($3.00 + $0.50 bonus offer,
    # paid to every completer), payment v5 (350c), training/debrief v3.
    ("tp_ai_v7", "high"),
    ("tp_ai_v7", "low"),
    ("tp_scripted_v9", "high"),
    ("tp_scripted_v9", "low"),
    # 2026-10-10 comprehension check + eligibility phrasing: consent v8,
    # training v4; preview/QA first — no new fielding group minted.
    ("tp_ai_v8", "high"),
    ("tp_ai_v8", "low"),
    ("tp_scripted_v10", "high"),
    ("tp_scripted_v10", "low"),
    # 2026-10-10 ask-routing lineage: training v5 (typed practice ask),
    # social prompt v4, assigned_own 130s schedule — preview/QA only.
    ("tp_ai_v9", "high"),
    ("tp_ai_v9", "low"),
    ("tp_scripted_v11", "high"),
    ("tp_scripted_v11", "low"),
    # 2026-10-11 UX v7 successors: seat_plan schedule (dealt assignments,
    # 5 asks 45/100/155/210/245), same artifact pins — preview/QA only.
    ("tp_ai_v10", "high"),
    ("tp_ai_v10", "low"),
    ("tp_scripted_v12", "high"),
    ("tp_scripted_v12", "low"),
    # 2026-10-11 UX v8 follow-up: post_task v5 adds the teammate-rushed
    # manipulation check after tp_post_09 — preview/QA only.
    ("tp_ai_v11", "high"),
    ("tp_ai_v11", "low"),
    ("tp_scripted_v13", "high"),
    ("tp_scripted_v13", "low"),
    # v15 workload build: the mixed arm's declared pair (high, mixed).
    ("tp_ai_ovsome_thin_v5", "high"),
    ("tp_ai_ovsome_thin_v5", "mixed"),
)


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _settings(make_settings: Any, db: MigratedDatabase, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "APP_MODE": "qa",
        "DATABASE_URL": db.dsns.runtime,
        "ADMIN_PASSWORD": PW,
        "ADMIN_OPERATOR_PASSWORD": OPW,
        "ADMIN_COMPENSATION_PASSWORD": CPW,
        "ADMIN_EMAILS": "pi@university.edu",
        "PREVIEW_SIGNING_KEY": KEY,
    }
    values.update(overrides)
    return make_settings(**values)


def _admin_client(
    make_settings: Any,
    db: MigratedDatabase,
    role: str = "researcher",
    **settings_overrides: Any,
) -> TestClient:
    """A logged-in admin client. Not a context manager: the lifespan would dispose the
    shared disposable-database engine."""
    app = create_app(
        _settings(make_settings, db, **settings_overrides),
        clock=VirtualClock(),
        engine=db.runtime,
        configure_logs=False,
    )
    client = TestClient(app, raise_server_exceptions=False)
    password = {
        "researcher": PW,
        "operator": OPW,
        "compensation": CPW,
    }[role]
    resp = client.post("/admin/login", json={"role": role, "password": password})
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == role
    # The deployed cookie path (/investigators/) doesn't match the internal /admin/...
    # test paths; copy the issued cookies onto "/" for the jar.
    for name in (adminauth.SESSION_COOKIE, adminauth.CSRF_COOKIE):
        value = resp.cookies.get(name)
        if value:
            client.cookies.set(name, value, path="/")
    return client


def _mutate(client: TestClient, method: str, path: str, **kwargs: Any) -> Any:
    headers = dict(kwargs.pop("headers", {}))
    headers["X-CSRF-Token"] = client.cookies.get(adminauth.CSRF_COOKIE, path="/") or ""
    return client.request(method, path, headers=headers, **kwargs)


def _post(client: TestClient, path: str, body: dict[str, Any]) -> Any:
    return _mutate(client, "POST", path, json=body)


def _seed_batch(db: MigratedDatabase, store: ContentStore, batch_id: str) -> None:
    from app.domain.manifests.binding import bind
    from app.services.sessions import register_manifest

    bound = bind(store, "tp_demo_v3", mode="qa")
    with db.superuser.begin() as conn:
        register_manifest(conn, bound)
        conn.execute(
            insert(s.batch).values(
                batch_id=batch_id,
                study_key=bound.manifest.study_key,
                manifest_id=bound.manifest_id,
                mode="qa",
                enrollment_open=True,
            )
        )


def _seed_session(db: MigratedDatabase, store: ContentStore, pid: str, batch: str) -> EntryResult:
    _seed_batch(db, store, batch)
    return entry.enter(
        db.runtime,
        store,
        query={"PROLIFIC_PID": pid, "STUDY_ID": "s", "SESSION_ID": "x", "batch": batch},
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )


def _audit_actions(db: MigratedDatabase, sid: uuid.UUID | None = None) -> list[str]:
    q = select(s.admin_audit.c.action).order_by(s.admin_audit.c.occurred_at)
    if sid is not None:
        q = q.where(s.admin_audit.c.session_id == sid)
    with db.runtime.connect() as conn:
        return [r[0] for r in conn.execute(q).all()]


# ----- INV-1301: auth boundary --------------------------------------------------------------


def test_admin_requires_session_cookie(migrated: MigratedDatabase, make_settings: Any) -> None:
    client = _admin_client(make_settings, migrated)
    client.cookies.clear()
    for path in (
        "/admin/sessions",
        "/admin/monitor",
        "/admin/batches",
        "/admin/redaction/queue",
        "/admin/audit",
        "/admin/retention",
        "/admin/compensation/queue",
    ):
        assert client.get(path).status_code == 401, path


# ----- INV-1303/1304: monitor, detail, replay --------------------------------------------------


def test_session_list_detail_replay(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    result = _seed_session(migrated, store, "P-LIST-1", "wp13-list")
    sid = str(result.session_id)
    client = _admin_client(make_settings, migrated)

    listing = client.get("/admin/sessions").json()
    row = next(r for r in listing["sessions"] if r["session_id"] == sid)
    assert row["stage"] == "consent"
    assert row["batch_id"] == "wp13-list"
    # Monitor columns the spec asks for exist even before the task runs.
    for field in (
        "active_ms",
        "n_answered_crates",
        "n_reconnects",
        "n_rejected_commands",
        "n_committed_exposures",
        "n_open_episodes",
        "n_pending_jobs",
        "completion_outcome",
        "terminal_reason",
    ):
        assert field in row, field

    detail = client.get(f"/admin/sessions/{sid}")
    assert detail.status_code == 200
    assert detail.json()["session"]["session_id"] == sid
    assert {"exposures", "episodes", "incidents", "entitlements"} <= set(detail.json())

    replay = client.get(f"/admin/sessions/{sid}/replay")
    assert replay.status_code == 200
    body = replay.json()
    assert body["restricted_status"] == "ok"  # researcher
    assert body["restricted"] is not None
    assert "world" in body["restricted"]
    # The command ledger is part of the restricted view — rejected commands leave
    # no events, so without it the walkthrough cannot show stale-revision retries.
    assert "command_receipts" in body["restricted"]
    for receipt in body["restricted"]["command_receipts"]:
        assert {"kind", "outcome", "expected_revision", "committed_revision"} <= set(receipt)
    participant_events = body["participant"]["events"]
    for ev in participant_events:
        # The participant projection carries no truth/configuration fields.
        flat = repr(ev).lower()
        for banned in ("arm", "seed", "answer_key", "provider"):
            assert banned not in flat, (banned, ev)


def test_sessions_filter_expands_batch_group(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """Sessions carry the member batch id — a ``batch_group_id`` filter expands to
    the group's members (the Sessions tab defaults to the current round's group)."""
    a = _seed_session(migrated, store, "P-GRP-A", "wp13-grp.a")
    b = _seed_session(migrated, store, "P-GRP-B", "wp13-grp.b")
    other = _seed_session(migrated, store, "P-GRP-X", "wp13-grp-out")
    with migrated.superuser.begin() as conn:
        study_key = conn.execute(
            select(s.batch.c.study_key).where(s.batch.c.batch_id == "wp13-grp.a")
        ).scalar_one()
        conn.execute(
            insert(s.batch_group).values(
                group_id="wp13-grp",
                study_key=study_key,
                mode="qa",
                enrollment_open=False,
            )
        )
        for pos, bid in enumerate(("wp13-grp.a", "wp13-grp.b")):
            conn.execute(
                insert(s.batch_group_member).values(group_id="wp13-grp", batch_id=bid, position=pos)
            )
    client = _admin_client(make_settings, migrated)

    grouped = client.get("/admin/sessions", params={"batch_group_id": "wp13-grp"}).json()
    ids = {r["session_id"] for r in grouped["sessions"]}
    assert ids == {str(a.session_id), str(b.session_id)}
    assert str(other.session_id) not in ids
    # An unknown group yields an empty listing rather than an unfiltered one.
    empty = client.get("/admin/sessions", params={"batch_group_id": "wp13-nope"}).json()
    assert empty["sessions"] == []
    # No filter still returns everything.
    all_rows = client.get("/admin/sessions").json()["sessions"]
    assert {str(a.session_id), str(b.session_id), str(other.session_id)} <= {
        r["session_id"] for r in all_rows
    }


def test_replay_participant_pages(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """participant.pages carries the page-by-page participant-facing record: bound
    copy, recorded responses, and the participant-visible workbench subset — never
    restricted data (truth, model calls, internals)."""
    from . import audit_helpers as ah

    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "P-PAGES-1", "wp13-pages")
    sid = str(result.session_id)
    client = _admin_client(make_settings, migrated)

    body = client.get(f"/admin/sessions/{sid}/replay").json()
    pages = body["participant"]["pages"]

    # Page order follows the committed stage transitions.
    assert pages["stage_order"][0] == "entry"
    for stage in ("consent", "instructions", "training", "task", "solo_review"):
        # tp_demo_v3 keeps the solo_review phase; fielded v11+ builds drop it.
        if stage in ("task", "solo_review"):
            assert "task" in pages["stage_order"] or "solo_review" in pages["stage_order"]
        else:
            assert stage in pages["stage_order"], stage

    # Bound consent copy resolves and the recorded action lands.
    assert pages["consent"]["action"] == "consented"
    assert pages["materials"]["consent"]["body"]["text"]["heading"]

    # Bound instruments resolve; a session that never reached the stage has no
    # fabricated responses.
    post = pages["instruments"]["post_questionnaire"]
    assert post["items"]
    assert post["responses"] == {}
    assert pages["instruments"]["confidence"]["items"]

    # The workbench subset is participant-visible only: own file + own assignment.
    assert pages["workbench"]["initial_file"]
    assert pages["workbench"]["assigned_crates"]
    assert len(pages["workbench"]["assigned_crates"]) == 4
    assert set(pages["workbench"]["records"]) >= set(pages["workbench"]["initial_file"])

    # Nothing restricted leaks into the participant-side payload.
    for banned_key in (
        "world",
        "seat_holdings",
        "model_calls",
        "claims",
        "scheduled_jobs",
        "internal_events",
        "command_receipts",
        "knowledge_snapshots",
    ):
        assert banned_key not in pages, banned_key


def test_replay_restricted_is_researcher_only(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    result = _seed_session(migrated, store, "P-REPLAY-OP", "wp13-replay-op")
    sid = str(result.session_id)
    op = _admin_client(make_settings, migrated, role="operator")
    replay = op.get(f"/admin/sessions/{sid}/replay")
    assert replay.status_code == 200
    body = replay.json()
    assert body["restricted_status"] == "requires_researcher_role"
    assert body["restricted"] is None
    # Compensation can see nothing session-shaped at all.
    comp = _admin_client(make_settings, migrated, role="compensation")
    assert comp.get(f"/admin/sessions/{sid}/replay").status_code == 403
    assert comp.get("/admin/sessions").status_code == 403
    assert comp.get("/admin/monitor").status_code == 403


def test_monitor_reports_section_status_not_blanket_complete(
    migrated: MigratedDatabase, make_settings: Any
) -> None:
    client = _admin_client(make_settings, migrated)
    monitor = client.get("/admin/monitor")
    assert monitor.status_code == 200
    body = monitor.json()
    for section_name in (
        "sessions_by_stage",
        "fidelity_rates",
        "manipulation_receipts",
        "unresolved_episodes",
        "incidents",
        "coding_coverage",
    ):
        section = body[section_name]
        assert section["status"] in ("ok", "unavailable"), section_name
    # Coding tables are unreadable by the runtime role (RUNTIME_NO_ACCESS) — the section
    # must say so rather than claim coverage.
    assert body["coding_coverage"]["status"] == "unavailable"
    assert body["checks"]


# ----- INV-1305: audited support actions ------------------------------------------------------


def test_technical_stop_audited_and_role_gated(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    result = _seed_session(migrated, store, "P-STOP-1", "wp13-stop")
    sid = str(result.session_id)
    with migrated.runtime.connect() as conn:
        arm_before = conn.execute(
            select(s.session.c.arm).where(s.session.c.session_id == result.session_id)
        ).scalar_one()

    # Researcher cannot run operator support actions.
    res = _admin_client(make_settings, migrated)
    denied = _post(res, f"/admin/sessions/{sid}/technical_stop", {"reason": "not my role"})
    assert denied.status_code == 403

    op = _admin_client(make_settings, migrated, role="operator")
    resp = _post(op, f"/admin/sessions/{sid}/technical_stop", {"reason": "bench fault"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["stage"] == "technical_stop"

    with migrated.runtime.connect() as conn:
        sess = conn.execute(
            select(s.session.c.stage, s.session.c.terminal_reason).where(
                s.session.c.session_id == result.session_id
            )
        ).one()
        assert sess.stage == "technical_stop"
        assert sess.terminal_reason == "operator_stop"
        # The durable outcome exists: receipt + base entitlement.
        receipt = conn.execute(
            select(s.completion_receipt.c.outcome).where(
                s.completion_receipt.c.session_id == result.session_id
            )
        ).scalar_one()
        assert receipt == "technical_stop"
        ent = conn.execute(
            select(s.payment_entitlement.c.status).where(
                s.payment_entitlement.c.session_id == result.session_id,
                s.payment_entitlement.c.kind == "base",
            )
        ).scalar_one()
        assert ent is not None
        # Assignment/truth are untouched: arm unchanged, no admin edit path exists.
        arm_after = conn.execute(
            select(s.session.c.arm).where(s.session.c.session_id == result.session_id)
        ).scalar_one()
        assert arm_after == arm_before

    assert "technical_stop" in _audit_actions(migrated, result.session_id)
    # Second stop is refused, not silently re-applied.
    again = _post(op, f"/admin/sessions/{sid}/technical_stop", {"reason": "again"})
    assert again.status_code == 409


def test_cross_session_and_unknown_session_denied(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    _seed_session(migrated, store, "P-X1", "wp13-x1")
    op = _admin_client(make_settings, migrated, role="operator")
    missing = str(uuid.uuid4())
    for path, body in (
        (f"/admin/sessions/{missing}/technical_stop", {"reason": "x"}),
        (f"/admin/sessions/{missing}/credential_recovery", {"reason": "x"}),
        (
            f"/admin/sessions/{missing}/incidents",
            {"kind": "other", "clock_effect": "excluded"},
        ),
    ):
        resp = _post(op, path, body)
        assert resp.status_code == 404, (path, resp.text)
    assert op.get(f"/admin/sessions/{missing}").status_code == 404
    assert op.get(f"/admin/sessions/{missing}/replay").status_code == 404
    # Malformed ids are 422, never a 500.
    assert op.get("/admin/sessions/not-a-uuid").status_code == 422


def test_credential_recovery_rotates_and_audits(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    result = _seed_session(migrated, store, "P-CRED-1", "wp13-cred")
    sid = str(result.session_id)
    old_token = result.credential_token

    op = _admin_client(make_settings, migrated, role="operator")
    resp = _post(op, f"/admin/sessions/{sid}/credential_recovery", {"reason": "lost browser"})
    assert resp.status_code == 200, resp.text
    new_token = resp.json()["credential_token"]
    assert new_token and new_token != old_token

    with migrated.runtime.connect() as conn:
        # Only the hash is stored: the old bearer is revoked, the new one authenticates.
        assert credentials.authenticate(conn, old_token) is None
        assert credentials.authenticate(conn, new_token) == result.session_id
        # The raw token never lands in the audit payload.
        payloads = conn.execute(
            select(s.admin_audit.c.payload).where(
                s.admin_audit.c.session_id == result.session_id,
                s.admin_audit.c.action == "credential_recovery",
            )
        ).all()
        assert payloads and all(new_token not in repr(p) for (p,) in payloads)


def test_incident_open_close_audited(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    result = _seed_session(migrated, store, "P-INC-1", "wp13-inc")
    sid = str(result.session_id)
    op = _admin_client(make_settings, migrated, role="operator")
    opened = _post(
        op,
        f"/admin/sessions/{sid}/incidents",
        {"kind": "server_outage", "clock_effect": "excluded", "notes": "api blip"},
    )
    assert opened.status_code == 200, opened.text
    iid = opened.json()["incident_id"]
    closed = _post(op, f"/admin/sessions/{sid}/incidents/{iid}/close", {"reason": "resolved"})
    assert closed.status_code == 200
    again = _post(op, f"/admin/sessions/{sid}/incidents/{iid}/close", {"reason": "twice"})
    assert again.status_code == 404  # idempotent-close, no silent double-close
    actions = _audit_actions(migrated, result.session_id)
    assert "incident_open" in actions and "incident_close" in actions


# ----- INV-1302: batches, enrollment, previews ---------------------------------------------------


def test_batch_create_validates_and_enrollment_toggles(
    migrated: MigratedDatabase, make_settings: Any
) -> None:
    res = _admin_client(make_settings, migrated)
    op = _admin_client(make_settings, migrated, role="operator")

    bad = _post(
        res,
        "/admin/batches",
        {"batch_id": "wp13-bad", "manifest_id": "nope_v9", "mode": "qa"},
    )
    assert bad.status_code == 404

    ok = _post(
        res,
        "/admin/batches",
        {"batch_id": "wp13-b1", "manifest_id": "tp_demo_v1", "mode": "qa"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["enrollment_open"] is False

    # Operator cannot create batches but may pause/resume enrollment.
    denied = _post(
        op,
        "/admin/batches",
        {"batch_id": "wp13-b2", "manifest_id": "tp_demo_v1", "mode": "qa"},
    )
    assert denied.status_code == 403

    paused = _post(op, "/admin/batches/wp13-b1/enrollment", {"open": True, "reason": "launch"})
    assert paused.status_code == 200
    with migrated.runtime.connect() as conn:
        assert (
            conn.execute(
                select(s.batch.c.enrollment_open).where(s.batch.c.batch_id == "wp13-b1")
            ).scalar_one()
            is True
        )
    assert (
        _post(
            op, "/admin/batches/wp13-b1/enrollment", {"open": False, "reason": "pause"}
        ).status_code
        == 200
    )

    batches = res.get("/admin/batches").json()["batches"]
    entry_ = next(b for b in batches if b["batch_id"] == "wp13-b1")
    assert entry_["manifest_pin"] == "matches"


def test_preview_issuance_all_eight_combos_no_allocation(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    res = _admin_client(make_settings, migrated)
    combos = res.get("/admin/preview-combos").json()["combos"]
    assert len(combos) == 170  # 85 immutable manifests x 2 arms
    seen = {(c["manifest_id"], c["arm"]) for c in combos}
    # v9 removes the duplicate payment paragraph for seven preview successors.
    # All old consent bindings remain present, but are honestly marked historical.
    current_ids = {
        "tp_ai_v14",
        "tp_ai_ovnone_v3",
        "tp_ai_ovsome_v3",
        "tp_ai_ovfull_v3",
        "tp_ai_ovnone_thin_v3",
        "tp_ai_ovsome_thin_v3",
        "tp_ai_ovfull_thin_v3",
        # v15 (2026-10-18): newest two of the ovsome_thin family on current consent.
        "tp_ai_ovsome_thin_v4",
        "tp_ai_ovsome_thin_v5",
        "tp_ai_ovsome_thin_v6",
        "tp_ai_ovsome_thin_v7",
        "tp_ai_ovsome_thin_v8",
        "tp_ai_ovsome_thin_v9",
    }
    assert {c["manifest_id"] for c in combos if c["consent_current"]} == current_ids
    assert all(c["consent_current"] == (c["manifest_id"] in current_ids) for c in combos)
    # Only profiles that are both the newest of their family AND bound to the current
    # consent lineage surface in the picker — the preview successors
    # (gated to archive under the crate flags, but
    # still the newest build of their family on current consent). v15:
    # tp_ai_ovsome_thin_v5 is now the family's newest — v3/v4 keep a current
    # consent pin but are no longer issuable-current.
    assert {c["manifest_id"] for c in combos if c["current"]} == current_ids - {
        "tp_ai_ovsome_thin_v3",
        "tp_ai_ovsome_thin_v4",
        "tp_ai_ovsome_thin_v5",
        "tp_ai_ovsome_thin_v6",
        "tp_ai_ovsome_thin_v7",
        "tp_ai_ovsome_thin_v8",
    }
    for c in combos:
        assert c["consent_key"].startswith("consent.reference@v")

    for manifest_id, arm in ALL_COMBOS:
        assert (manifest_id, arm) in seen
        resp = _post(
            res,
            "/admin/previews",
            {"manifest_id": manifest_id, "arm": arm, "seed": "cd" * 16},
        )
        assert resp.status_code == 200, (manifest_id, arm, resp.text)

    # One issued descriptor enters without consuming an allocation or creating payment.
    token = _post(
        res,
        "/admin/previews",
        {"manifest_id": "tp_demo_v1", "arm": "high", "seed": "cd" * 16},
    ).json()["descriptor"]
    result = entry.enter(
        migrated.runtime,
        store,
        query={"pt": token},
        app_mode="qa",
        preview_key=KEY.encode(),
        current_manifest_sha256={mid: m.sha256 for mid, m in store.manifests.items()},
        build_source_sha256=BUILD_SHA,
    )
    with migrated.runtime.connect() as conn:
        sid = result.session_id
        assert (
            conn.execute(
                select(func.count())
                .select_from(s.allocation)
                .where(s.allocation.c.session_id == sid)
            ).scalar_one()
            == 0
        )
        assert (
            conn.execute(
                select(func.count())
                .select_from(s.payment_entitlement)
                .where(s.payment_entitlement.c.session_id == sid)
            ).scalar_one()
            == 0
        )
        mode = conn.execute(
            select(s.session.c.mode).where(s.session.c.session_id == sid)
        ).scalar_one()
        assert mode == "qa"
    assert "preview_issue" in _audit_actions(migrated)

    # Operator may also issue previews (lab self-service); compensation may not, and a
    # bad arm stays a 422 for everyone.
    op = _admin_client(make_settings, migrated, role="operator")
    assert (
        _post(op, "/admin/previews", {"manifest_id": "tp_demo_v1", "arm": "high"}).status_code
        == 200
    )
    comp = _admin_client(make_settings, migrated, role="compensation")
    assert (
        _post(comp, "/admin/previews", {"manifest_id": "tp_demo_v1", "arm": "high"}).status_code
        == 403
    )
    assert comp.get("/admin/previews/mine").status_code == 403
    assert (
        _post(res, "/admin/previews", {"manifest_id": "tp_demo_v1", "arm": "eval"}).status_code
        == 422
    )

    # Issued links are listable by their issuer with live session status — the
    # researcher's entered descriptor shows its session; the operator's unused link
    # shows none; neither sees the other's links.
    mine = res.get("/admin/previews/mine")
    assert mine.status_code == 200, mine.text
    links = mine.json()["links"]
    assert len(links) == len(ALL_COMBOS) + 1
    entered = next(
        link for link in links if link["session"] and link["session"]["session_id"] == str(sid)
    )
    assert entered["manifest_id"] == "tp_demo_v1" and entered["arm"] == "high"
    assert entered["session"]["stage"] == "consent"
    assert entered["entry_url"].startswith("/investigators/?pt=")
    unopened = [link for link in links if link["session"] is None]
    assert len(unopened) == len(ALL_COMBOS)

    op_links = op.get("/admin/previews/mine").json()["links"]
    assert len(op_links) == 1 and op_links[0]["session"] is None


def test_preview_combos_summary_states_the_manifest_design(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """BA-103 follow-up: each combo carries plain-English design bullets read off
    the manifest — solo review, ask controls, check-in reveal, request schedule —
    so a tester is never surprised by what a minted link does."""
    res = _admin_client(make_settings, migrated)
    by_key = {
        (c["manifest_id"], c["arm"]): c["summary"]
        for c in res.get("/admin/preview-combos").json()["combos"]
    }
    high = by_key[("tp_review_first_v11", "high")]
    assert any("no solo-review period" in line for line in high)
    assert any("Ask the team about" in line for line in high)
    assert any("Optional check-in at 60 s: teammate ratings 6 and 7" in line for line in high)
    assert any("Teammates request intel at 80 s, 180 s, 270 s" in line for line in high)
    low = by_key[("tp_review_first_v11", "low")]
    assert any("teammate ratings 2 and 3" in line for line in low)
    nl = by_key[("tp_demo_nl_v3", "high")]
    assert any("no solo-review period" in line for line in nl)
    assert any("typed chat only" in line for line in nl)
    control = by_key[("pe_demo_v1", "control")]
    assert any("no evaluation premise" in line for line in control)
    assert all(len(v) >= 5 for v in by_key.values())
    assert all("pressure" not in line.lower() for lines in by_key.values() for line in lines)


def test_preview_combos_default_build_summary_omits_disabled_feature_framing(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    res = _admin_client(
        make_settings, migrated, TIME_PRESSURE_ENABLED=False, AI_ASSISTANT_ENABLED=False
    )
    body = res.get("/admin/preview-combos").json()
    assert body["time_pressure_enabled"] is False
    current = next(c for c in body["combos"] if c["manifest_id"] == "tp_ai_v14")
    assert current["arm"] == "nopress"
    # crate flags off gate the ov builds, so the legacy independent-task
    # build is the deployable contract.
    assert current["test_group"] == "pilot_contract"
    for combo in body["combos"]:
        assert all("pressure" not in line.lower() for line in combo["summary"])
        assert not any("check-in" in line.lower() for line in combo["summary"])
        assert not any("Optional teammate message" in line for line in combo["summary"])


def test_preview_combos_test_group_marks_the_lab_test_set(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """UX v6 lab test set: the pilot contract (approved + current) plus the
    teammate-mechanism comparison pair — the newest nl_only build and the newest
    scripted-teammate (deterministic_only) build of the fielded study — are
    grouped for default display. LLM structured demos (tp_demo_v6) are not the
    contrast and stay archived, as do older versions and the pe study (no
    approved contract)."""
    res = _admin_client(make_settings, migrated, SCRIPTED_TEAMMATES_ENABLED=True)
    groups = {
        (c["manifest_id"], c["arm"]): c["test_group"]
        for c in res.get("/admin/preview-combos").json()["combos"]
    }
    # v11 binds consent v4 — superseded by the required-items consent v5, so it
    # is no longer the current contract and leaves the default listing.
    assert groups[("tp_review_first_v11", "high")] == "archive"
    assert groups[("tp_review_first_v11", "low")] == "archive"
    # Only the consent-v9 AI successor is the contract while the ov builds
    # are flag-gated. Existing fielded and scripted consent-v8 profiles
    # remain accessible as historical builds.
    for arm in ("high", "low"):
        assert groups[("tp_ai_v14", arm)] == "pilot_contract"
        assert groups[("tp_ai_v13", arm)] == "archive"
        assert groups[("tp_scripted_v13", arm)] == "archive"
        assert groups[("tp_ai_v12", arm)] == "archive"  # superseded by the open_ended repin
        assert groups[("tp_ai_v11", arm)] == "archive"  # superseded by the training v6 repin
        assert groups[("tp_ai_v10", arm)] == "archive"  # superseded by the post_task v5 repin
        assert groups[("tp_scripted_v12", arm)] == "archive"
        assert groups[("tp_ai_v9", arm)] == "archive"  # superseded by the seat_plan repin
        assert groups[("tp_scripted_v11", arm)] == "archive"
        assert groups[("tp_ai_v8", arm)] == "archive"  # superseded by the ask-routing repin
        assert groups[("tp_scripted_v10", arm)] == "archive"
        assert groups[("tp_ai_v7", arm)] == "archive"  # superseded by the consent v8 repin
        assert groups[("tp_scripted_v9", arm)] == "archive"
        assert groups[("tp_ai_v6", arm)] == "archive"  # superseded by the incentive repin
        assert groups[("tp_scripted_v8", arm)] == "archive"
        assert groups[("tp_ai_v4", arm)] == "archive"  # superseded by the RCA repin
        assert groups[("tp_ai_v3", arm)] == "archive"  # superseded by the 300 s revision
        assert groups[("tp_scripted_v5", arm)] == "archive"
        assert groups[("tp_ai_v2", arm)] == "archive"
        assert groups[("tp_scripted_v4", arm)] == "archive"
        assert groups[("tp_ai_v1", arm)] == "archive"  # superseded by tp_ai_v2
        assert groups[("tp_scripted_v3", arm)] == "archive"  # superseded by v4
        assert groups[("tp_scripted_v2", arm)] == "archive"  # superseded by v3
        assert groups[("tp_demo_nl_v3", arm)] == "archive"
        assert groups[("tp_scripted_v1", arm)] == "archive"
    # LLM structured demos are not the scripted contrast → archive.
    assert groups[("tp_demo_v6", "high")] == "archive"
    assert groups[("tp_demo_v6", "low")] == "archive"
    assert groups[("tp_demo_nl_v2", "high")] == "archive"
    assert groups[("tp_demo_v5", "high")] == "archive"
    assert groups[("tp_review_first_v9", "high")] == "archive"
    assert groups[("tp_review_first_v10", "high")] == "archive"
    # pe has nl_only demos too, but no approved contract → never "comparison".
    assert groups[("pe_demo_nl_v3", "eval")] == "archive"
    assert set(groups.values()) <= {"pilot_contract", "comparison", "archive"}


def test_preview_combos_scripted_flag_off_demotes_to_archive(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """AID-63 follow-up: while SCRIPTED_TEAMMATES_ENABLED=0 the deterministic
    (scripted-teammate) manifests leave the default test set — even the current
    approved tp_scripted_v13 demotes to ``archive`` (deliberately mintable for
    preview), the response advertises the flag, and archive combos carry no
    mock link."""
    res = _admin_client(make_settings, migrated, SCRIPTED_TEAMMATES_ENABLED=False)
    body = res.get("/admin/preview-combos").json()
    assert body["scripted_teammates_enabled"] is False
    groups = {(c["manifest_id"], c["arm"]): c["test_group"] for c in body["combos"]}
    urls = {(c["manifest_id"], c["arm"]): c["demo_url"] for c in body["combos"]}
    engines = {(c["manifest_id"], c["arm"]): c["social_generation"] for c in body["combos"]}
    for arm in ("high", "low"):
        # v15: the contract build links a playable mock; archive combos do not.
        assert groups[("tp_ai_v14", arm)] == "pilot_contract"
        assert urls[("tp_ai_v14", arm)] is not None
        assert groups[("tp_ai_v12", arm)] == "archive"
        assert groups[("tp_scripted_v13", arm)] == "archive"
        assert urls[("tp_scripted_v13", arm)] is None
        assert engines[("tp_scripted_v13", arm)] == "deterministic_only"
        assert engines[("tp_ai_v14", arm)] == "model_with_validated_fallback"
    # No scripted manifest anywhere in the default set.
    assert all(
        groups[key] == "archive"
        for key, engine in engines.items()
        if engine == "deterministic_only"
    )


def test_preview_combos_crate_flags_gate_and_un_gate_overlap_profiles(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """2026-10-15 crate-condition flags: while CRATE_OVERLAP_ENABLED=0 /
    CRATE_NUMBER_ENABLED=0 the overlap/thin profiles demote to ``archive``
    exactly like scripted — but their demo_url stays linked, because the
    playable mock is the pre-flag review surface. Enabling both flags returns
    the current approved ov builds to the launch set (``comparison``)."""
    OV = tuple(
        f"tp_ai_ov{overlap}{thin}_v3"
        for overlap in ("none", "some", "full")
        for thin in ("", "_thin")
    )
    # v15: ovsome_thin's newest build is v5 (arms high/mixed) — v3/v4 demote
    # to archive like superseded versions, and v5 joins the launch set under
    # its own declared arms.
    V15 = "tp_ai_ovsome_thin_v9"

    res = _admin_client(make_settings, migrated)
    body = res.get("/admin/preview-combos").json()
    assert body["crate_overlap_enabled"] is False
    assert body["crate_number_enabled"] is False
    groups = {(c["manifest_id"], c["arm"]): c["test_group"] for c in body["combos"]}
    urls = {(c["manifest_id"], c["arm"]): c["demo_url"] for c in body["combos"]}
    for arm in ("high", "low"):
        # flags off: v4 is gated out of the launch set, so the legacy
        # independent-task build is the deployable contract.
        assert groups[("tp_ai_v14", arm)] == "pilot_contract"
        for mid in OV:
            assert groups[(mid, arm)] == "archive"
            # v15: playable mocks link only on the pilot-contract build.
            assert urls[(mid, arm)] is None
        # The superseded v1s archive too.
        assert groups[("tp_ai_ovnone_v1", arm)] == "archive"
        assert urls[("tp_ai_ovnone_v1", arm)] is None
    for arm in ("high", "mixed"):
        assert groups[(V15, arm)] == "archive"
        assert urls[(V15, arm)] is None

    # Both flags on: the newest approved ov builds join the launch set.
    res_on = _admin_client(
        make_settings,
        migrated,
        CRATE_OVERLAP_ENABLED=True,
        CRATE_NUMBER_ENABLED=True,
    )
    groups_on = {
        (c["manifest_id"], c["arm"]): c["test_group"]
        for c in res_on.get("/admin/preview-combos").json()["combos"]
    }
    for arm in ("high", "low"):
        for mid in OV:
            if mid == "tp_ai_ovsome_thin_v3":
                # Superseded by tp_ai_ovsome_thin_v9 — never regains launch set.
                assert groups_on[(mid, arm)] == "archive"
            else:
                assert groups_on[(mid, arm)] == "comparison"
    for arm in ("high", "mixed"):
        assert groups_on[(V15, arm)] == "pilot_contract"
        # The v4 build this succeeded is archived with the rest.
        assert groups_on[("tp_ai_ovsome_thin_v4", arm)] == "archive"
        assert groups_on[("tp_ai_ovsome_thin_v5", arm)] == "archive"
        assert groups_on[("tp_ai_ovsome_thin_v6", arm)] == "archive"
        assert groups_on[("tp_ai_ovsome_thin_v7", arm)] == "archive"
        assert groups_on[("tp_ai_ovsome_thin_v8", arm)] == "archive"


def test_preview_combos_carry_demo_mock_urls(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """v15 (2026-10-17): playable mocks link only on the pilot-contract build —
    the newest-published approved launch-eligible manifest (tp_ai_ovsome_thin_v4
    for TP). Every other combo stays mintable but links no mock."""
    res = _admin_client(
        make_settings,
        migrated,
        SCRIPTED_TEAMMATES_ENABLED=True,
        CRATE_OVERLAP_ENABLED=True,
        CRATE_NUMBER_ENABLED=True,
    )
    urls = {
        (c["manifest_id"], c["arm"]): c["demo_url"]
        for c in res.get("/admin/preview-combos").json()["combos"]
    }
    assert urls[("tp_review_first_v11", "high")] is None  # archived: no mock link
    assert urls[("tp_review_first_v11", "low")] is None
    for a in ("high", "mixed"):
        assert (
            urls[("tp_ai_ovsome_thin_v9", a)] is not None
            and "ovsome_thin" in urls[("tp_ai_ovsome_thin_v9", a)]
        )
    for a in ("high", "low"):
        assert urls[("tp_ai_v14", a)] is None  # comparison: mintable, no mock
        assert urls[("tp_ai_v13", a)] is None
        assert urls[("tp_scripted_v13", a)] is None
        assert urls[("tp_ai_v12", a)] is None  # superseded by the open_ended line
        assert urls[("tp_ai_v11", a)] is None  # superseded by the training v6 line
        assert urls[("tp_ai_v10", a)] is None  # superseded by the post_task v5 line
        assert urls[("tp_scripted_v12", a)] is None
        assert urls[("tp_ai_v9", a)] is None  # superseded by the seat_plan line
        assert urls[("tp_scripted_v11", a)] is None
        assert urls[("tp_ai_v8", a)] is None  # superseded by the ask-routing line
        assert urls[("tp_scripted_v10", a)] is None
        assert urls[("tp_ai_v7", a)] is None  # superseded by the consent v8 line
        assert urls[("tp_scripted_v9", a)] is None
        assert urls[("tp_ai_v6", a)] is None  # superseded by the incentive line
        assert urls[("tp_scripted_v8", a)] is None
        assert urls[("tp_ai_v3", a)] is None
        assert urls[("tp_scripted_v5", a)] is None
        assert urls[("tp_ai_v2", a)] is None
        assert urls[("tp_scripted_v4", a)] is None
        assert urls[("tp_ai_v1", a)] is None  # archived: no mock link
        assert urls[("tp_scripted_v3", a)] is None
        assert urls[("tp_scripted_v2", a)] is None
        assert urls[("tp_demo_nl_v3", a)] is None
    assert urls[("tp_demo_v6", "high")] is None


# ----- INV-1306: entitlements + compensation isolation -------------------------------------------


def test_entitlement_decision_and_compensation_payment(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    from app.domain.manifests.binding import bind
    from app.services.sessions import register_manifest

    # Payment-role coverage must use a genuinely live, approved binding.
    bound = bind(store, "tp_ai_v11", mode="live")
    with migrated.superuser.begin() as conn:
        register_manifest(conn, bound)
        conn.execute(
            insert(s.batch).values(
                batch_id="wp13-pay",
                study_key=bound.manifest.study_key,
                manifest_id=bound.manifest_id,
                mode="live",
                enrollment_open=True,
            )
        )
    result = entry.enter(
        migrated.runtime,
        store,
        query={"PROLIFIC_PID": "P-PAY-1", "STUDY_ID": "s", "SESSION_ID": "x", "batch": "wp13-pay"},
        app_mode="live",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    sid = str(result.session_id)
    op = _admin_client(make_settings, migrated, role="operator")
    assert (
        _post(op, f"/admin/sessions/{sid}/technical_stop", {"reason": "bench"}).status_code == 200
    )

    res = _admin_client(make_settings, migrated)
    decided = _post(
        res,
        f"/admin/entitlements/{sid}/base/decision",
        {"decision": "approved", "note": "stopped early, full base"},
    )
    assert decided.status_code == 200

    comp = _admin_client(make_settings, migrated, role="compensation")
    queue = comp.get("/admin/compensation/queue")
    assert queue.status_code == 200
    row = next(r for r in queue.json()["rows"] if r["session_id"] == sid)
    assert row["platform"] == "prolific"
    assert row["status"] == "approved"

    # Compensation cannot decide; researcher cannot mark paid.
    assert (
        _post(
            comp,
            f"/admin/entitlements/{sid}/base/decision",
            {"decision": "void"},
        ).status_code
        == 403
    )
    assert (
        _post(
            res,
            f"/admin/compensation/entitlements/{sid}/base/paid",
            {"paid_ref": "prolific-pay-1"},
        ).status_code
        == 403
    )
    paid = _post(
        comp,
        f"/admin/compensation/entitlements/{sid}/base/paid",
        {"paid_ref": "prolific-pay-1"},
    )
    assert paid.status_code == 200
    with migrated.runtime.connect() as conn:
        status = conn.execute(
            select(s.payment_entitlement.c.status).where(
                s.payment_entitlement.c.session_id == result.session_id,
                s.payment_entitlement.c.kind == "base",
            )
        ).scalar_one()
        assert status == "paid"
    # No re-marking, no deciding a paid row — false-completion prevention.
    assert (
        _post(
            comp,
            f"/admin/compensation/entitlements/{sid}/base/paid",
            {"paid_ref": "again"},
        ).status_code
        == 409
    )
    assert (
        _post(
            res,
            f"/admin/entitlements/{sid}/base/decision",
            {"decision": "void"},
        ).status_code
        == 409
    )


def test_compensation_cannot_research_export(
    migrated: MigratedDatabase, make_settings: Any
) -> None:
    comp = _admin_client(make_settings, migrated, role="compensation")
    assert _post(comp, "/admin/exports", {"kind": "study"}).status_code == 403
    assert comp.get("/admin/dispositions").status_code == 403


# ----- INV-1308: redaction queue + exports -------------------------------------------------------


def _insert_message(db: MigratedDatabase, sid: uuid.UUID, body: str) -> uuid.UUID:
    mid = uuid.uuid4()
    with db.runtime.begin() as conn:
        conn.execute(
            insert(s.message).values(
                session_id=sid,
                message_id=mid,
                actor="participant",
                channel="team",
                body=body,
                accepted_seq=1,
            )
        )
    return mid


def test_redaction_queue_review_and_sharing_export(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    result = _seed_session(migrated, store, "P-RED-1", "wp13-red")
    sid = result.session_id
    mid = _insert_message(migrated, sid, 'free text with =HYPERLINK("http://x") attempt')
    ref = f"message:{sid}:{mid}"

    res = _admin_client(make_settings, migrated)
    queue = res.get("/admin/redaction/queue")
    assert queue.status_code == 200
    assert ref in {r["source_ref"] for r in queue.json()["pending"]}

    # 2026-10-16 owner direction: the sharing bundle is verbatim — the summary
    # carries row counts only (no withheld/reviewed counters exist any more).
    emitted = _post(res, "/admin/exports", {"kind": "sharing", "session_ids": [str(sid)]})
    assert emitted.status_code == 200, emitted.text
    assert emitted.json()["tables"]["message"] == {"rows": 1}

    review = _post(
        res,
        "/admin/redaction/review",
        {
            "kind": "message",
            "source_ref": ref,
            "session_id": str(sid),
            "scrubbed_sha256": "ef" * 32,
            "notes": "reviewed",
        },
    )
    assert review.status_code == 200, review.text
    with migrated.runtime.connect() as conn:
        row = (
            conn.execute(select(s.redaction_review).where(s.redaction_review.c.source_ref == ref))
            .mappings()
            .one()
        )
        assert row["reviewer"]
    # The study export stays formula-safe (csvsafe layer) — checked structurally here.
    study = _post(res, "/admin/exports", {"kind": "study", "batch_id": "wp13-red"})
    assert study.status_code == 200, study.text
    assert "session" in study.json()["tables"]


# ----- INV-1306/1307: retention, deletion, tombstones ---------------------------------------------


def test_retention_status_and_schedule_under_runtime_role(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    _seed_session(migrated, store, "P-RET-1", "wp13-ret")
    res = _admin_client(make_settings, migrated)
    status = res.get("/admin/retention")
    assert status.status_code == 200
    links = status.json()["platform_links"]
    # The runtime role holds SELECT(platform, platform_pid, session_id) only — purge_after
    # counts are privileged; the view reports "unavailable", never a false number.
    assert links["status"] in ("ok", "unavailable")

    sched = _post(
        res,
        "/admin/retention/schedule",
        {"study_close_at": "2030-01-01T00:00:00+00:00"},
    )
    assert sched.status_code == 200
    assert sched.json()["status"] in ("applied", "blocked_privileged")
    purge = _post(
        _admin_client(make_settings, migrated, role="operator"),
        "/admin/retention/purge",
        {"dry_run": True},
    )
    assert purge.status_code == 200
    assert purge.json()["status"] in ("dry_run", "blocked_privileged")


def test_deletion_dry_run_tombstone_and_reapply(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    result = _seed_session(migrated, store, "P-DEL-1", "wp13-del")
    sid = result.session_id
    _insert_message(migrated, sid, "withdraw my data please")

    op = _admin_client(make_settings, migrated, role="operator")
    dry = _post(op, f"/admin/sessions/{sid}/deletion", {"reason": "participant request"})
    assert dry.status_code == 200, dry.text
    assert dry.json()["applied"]["dry_run"] is True
    with migrated.runtime.connect() as conn:
        assert (
            conn.execute(
                select(func.count()).select_from(s.message).where(s.message.c.session_id == sid)
            ).scalar_one()
            == 1
        )
        # A dry run must never mint a replayable tombstone.
        tombstones = conn.execute(
            select(func.count())
            .select_from(s.admin_audit)
            .where(
                s.admin_audit.c.session_id == sid,
                s.admin_audit.c.action == "deletion_tombstone",
            )
        ).scalar_one()
        assert tombstones == 0

    live = _post(
        op,
        f"/admin/sessions/{sid}/deletion",
        {"dry_run": False, "reason": "participant request"},
    )
    assert live.status_code == 200, live.text
    assert live.json()["applied"]["dry_run"] is False
    with migrated.runtime.connect() as conn:
        assert (
            conn.execute(
                select(func.count()).select_from(s.message).where(s.message.c.session_id == sid)
            ).scalar_one()
            == 0
        )
        # The session row, tombstone and privileged tables survive.
        assert (
            conn.execute(
                select(func.count()).select_from(s.session).where(s.session.c.session_id == sid)
            ).scalar_one()
            == 1
        )
        tombstones = conn.execute(
            select(func.count())
            .select_from(s.admin_audit)
            .where(
                s.admin_audit.c.session_id == sid,
                s.admin_audit.c.action == "deletion_tombstone",
            )
        ).scalar_one()
        assert tombstones == 1

    # Restore rehearsal: a backup restores the message; reapply must delete it again.
    _insert_message(migrated, sid, "restored text")
    reapplied = _post(op, "/admin/retention/reapply", {"confirm": True})
    assert reapplied.status_code == 200, reapplied.text
    assert str(sid) in reapplied.json()["reapplied"]
    with migrated.runtime.connect() as conn:
        assert (
            conn.execute(
                select(func.count()).select_from(s.message).where(s.message.c.session_id == sid)
            ).scalar_one()
            == 0
        )


def test_cleanup_scope_defaults_dry_run_and_live_guard(
    migrated: MigratedDatabase, make_settings: Any
) -> None:
    op = _admin_client(make_settings, migrated, role="operator")
    dry = _post(op, "/admin/cleanup", {"scope": "preview"})
    assert dry.status_code == 200
    assert dry.json()["dry_run"] is True
    # Live scope refuses without the full confirmation triple.
    live = _post(op, "/admin/cleanup", {"scope": "live", "dry_run": False})
    assert live.status_code == 422
    # Researcher may not run cleanup at all.
    res = _admin_client(make_settings, migrated)
    assert _post(res, "/admin/cleanup", {"scope": "preview"}).status_code == 403


def test_cleanup_survives_displayed_output_fk_block(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """BA-34: append-only ``displayed_output`` FK-references ``model_call``. A mutable
    delete blocked by an immutable child is reported ``blocked`` — never a 500 that
    aborts the whole scope."""
    result = _seed_session(migrated, store, "P-CLEAN-FK", "wp13-clean-fk")
    sid = result.session_id
    with migrated.superuser.begin() as conn:
        conn.execute(
            insert(s.event).values(
                session_id=sid,
                seq=9000,
                event_id=uuid.uuid4(),
                stage="task",
                actor="system",
                type="stage_entered",
                payload={},
            )
        )
        snap = uuid.uuid4()
        conn.execute(
            insert(s.knowledge_snapshot).values(
                session_id=sid,
                snapshot_id=snap,
                seat="assistant",
                purpose="assistant_context",
                visible_event_seq=9000,
                record_manifest={},
                created_event_seq=9000,
            )
        )
        call = uuid.uuid4()
        conn.execute(
            insert(s.model_call).values(
                session_id=sid,
                call_id=call,
                snapshot_id=snap,
                provider="p",
                model="m",
                prompt_sha256="a" * 64,
                input={},
            )
        )
        conn.execute(
            insert(s.displayed_output).values(
                session_id=sid,
                output_id=uuid.uuid4(),
                call_id=call,
                shown_text="shown",
                shown_sha256="b" * 64,
                delivered_seq=9000,
            )
        )
    op = _admin_client(make_settings, migrated, role="operator")
    resp = _post(op, "/admin/cleanup", {"scope": "qa", "dry_run": False})
    assert resp.status_code == 200, resp.text
    actions = _audit_actions(migrated, sid)
    assert "deletion_tombstone" in actions
    # model_call could not be deleted while its immutable child lives — recorded, not fatal.
    with migrated.runtime.connect() as conn:
        tomb = conn.execute(
            select(s.admin_audit.c.payload).where(
                s.admin_audit.c.session_id == sid,
                s.admin_audit.c.action == "deletion_tombstone",
            )
        ).scalar_one()
    assert "model_call" in tomb["blocked"]
    # Tombstoned sessions are deleted by contract — hidden from the monitor board.
    assert str(sid) not in {
        str(r["session_id"]) for r in op.get("/admin/sessions").json()["sessions"]
    }


# ----- BA-39: a tombstoned session is deleted by contract --------------------------


def test_tombstoned_session_refused_on_every_path(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """Once tombstoned a session cannot re-authenticate, re-enter on its panel
    linkage, or be recovered — and admin surfaces label it deleted with the
    privileged-purge backlog visible."""
    result = _seed_session(migrated, store, "P-TOMB-1", "wp13-tomb")
    sid, token = result.session_id, result.credential_token
    _insert_message(migrated, sid, "remove my data")

    op = _admin_client(make_settings, migrated, role="operator")
    live = _post(
        op,
        f"/admin/sessions/{sid}/deletion",
        {"dry_run": False, "reason": "participant request"},
    )
    assert live.status_code == 200, live.text

    with migrated.runtime.connect() as conn:
        # The issued bearer token can never re-authenticate a deleted session — the
        # tombstone is the revocation (identity rows survive under the runtime role).
        assert credentials.authenticate(conn, token) is None

    # Same-batch re-entry on the panel ID is refused as session_deleted — a distinct
    # audited reason that is never overwritten by credential_recovery_required.
    with pytest.raises(entry.EntryError) as excinfo:
        entry.enter(
            migrated.runtime,
            store,
            query={
                "PROLIFIC_PID": "P-TOMB-1",
                "STUDY_ID": "s",
                "SESSION_ID": "x",
                "batch": "wp13-tomb",
            },
            app_mode="qa",
            preview_key=None,
            current_manifest_sha256={},
            build_source_sha256=BUILD_SHA,
        )
    assert excinfo.value.reason == "session_deleted"
    with migrated.runtime.connect() as conn:
        refusals = (
            conn.execute(
                select(s.admin_audit.c.payload)
                .where(
                    s.admin_audit.c.session_id == sid,
                    s.admin_audit.c.action == "reentry_refused",
                )
                .order_by(s.admin_audit.c.occurred_at)
            )
            .scalars()
            .all()
        )
    assert refusals and all(r["reason"] == "session_deleted" for r in refusals)

    # Support-mediated recovery is refused — a deleted session has no identity to recover.
    rec = _post(
        op,
        f"/admin/sessions/{sid}/credential_recovery",
        {"reason": "lost cookie"},
    )
    assert rec.status_code == 409 and rec.json()["error"] == "session_deleted"

    # The monitor board never lists a deleted session …
    assert str(sid) not in {
        str(r["session_id"]) for r in op.get("/admin/sessions").json()["sessions"]
    }
    # … but a direct lookup is honest about the state: deleted, with the count of
    # rows still awaiting the privileged purge.
    detail = op.get(f"/admin/sessions/{sid}")
    assert detail.status_code == 200, detail.text
    sess = detail.json()["session"]
    assert sess["deleted"] is True
    assert sess["purge_pending"]["privileged"] > 0
    assert sess["purge_pending"]["total"] > 0
    # Posture view aggregates the same backlog.
    status = op.get("/admin/retention").json()
    assert status["deletion_tombstones"] >= 1
    assert status["purge_pending_rows"] >= sess["purge_pending"]["total"]


def test_tombstoned_preview_link_reports_deleted_with_purge_backlog(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """ "My links" must not claim 'test data removed' while append-only/identity rows
    still await the privileged purge — the label is tombstoned + pending counts."""
    res = _admin_client(make_settings, migrated)
    token = _post(
        res,
        "/admin/previews",
        {"manifest_id": "tp_demo_v1", "arm": "high", "seed": "cd" * 16},
    ).json()["descriptor"]
    entered = entry.enter(
        migrated.runtime,
        store,
        query={"pt": token},
        app_mode="qa",
        preview_key=KEY.encode(),
        current_manifest_sha256={mid: m.sha256 for mid, m in store.manifests.items()},
        build_source_sha256=BUILD_SHA,
    )
    sid = entered.session_id
    op = _admin_client(make_settings, migrated, role="operator")
    live = _post(
        op,
        f"/admin/sessions/{sid}/deletion",
        {"dry_run": False, "reason": "link cleanup"},
    )
    assert live.status_code == 200, live.text

    mine = res.get("/admin/previews/mine").json()["links"]
    link = next(row for row in mine if row["session"] and row["session"]["session_id"] == str(sid))
    assert link["session"]["deleted"] is True
    assert link["session"]["purge_pending"]["total"] > 0


# ----- documentation ------------------------------------------------------------------------------


def test_admin_docs_lists_serves_and_gates(
    migrated: MigratedDatabase, make_settings: Any, tmp_path: Any
) -> None:
    """/admin/docs serves an allowlisted file set from ADMIN_DOCS_DIR to the
    researcher only; arbitrary doc ids cannot reach the filesystem."""
    (tmp_path / "README.md").write_text("# hello docs", encoding="utf-8")
    client = _admin_client(make_settings, migrated, ADMIN_DOCS_DIR=str(tmp_path))

    listing = client.get("/admin/docs").json()["docs"]
    by_id = {d["id"]: d for d in listing}
    assert set(by_id) == {
        "readme",
        "decisions",
        "behaviors",
        "harness",
        "study_decisions",
        "prolific_connect",
        "pre_launch_checklist",
        "post_launch_checklist",
        "prolific_playbook",
        "four_condition_pilot",
        "feature_flag_playbook",
        "time_pressure_readme",
        "ai_qa_readme",
        "ai_assistant_readme",
        "crate_distribution_readme",
        "bonus_payment_readme",
        "e2e_smoke_playbook",
        "risk_assessment",
        "platform_overview",
        "codegraph",
    }
    assert by_id["readme"]["size"] == len("# hello docs")

    view = client.get("/admin/docs/readme")
    assert view.status_code == 200 and view.text == "# hello docs"
    assert "attachment" not in view.headers.get("content-disposition", "")
    dl = client.get("/admin/docs/readme?download=1")
    assert "attachment" in dl.headers.get("content-disposition", "")

    # Allowlist misses are 404 — never a path lookup.
    assert client.get("/admin/docs/..%2F.env").status_code == 404
    assert client.get("/admin/docs/.env").status_code == 404
    # Allowlisted but absent on disk → 404, not an error page.
    assert client.get("/admin/docs/codegraph").status_code == 404

    # Researcher-only: operator and compensation cannot list or fetch.
    op = _admin_client(make_settings, migrated, role="operator", ADMIN_DOCS_DIR=str(tmp_path))
    assert op.get("/admin/docs").status_code == 403
    assert op.get("/admin/docs/readme").status_code == 403
    comp = _admin_client(make_settings, migrated, role="compensation", ADMIN_DOCS_DIR=str(tmp_path))
    assert comp.get("/admin/docs").status_code == 403


def test_admin_docs_unconfigured_root_lists_without_sizes(
    migrated: MigratedDatabase, make_settings: Any
) -> None:
    client = _admin_client(make_settings, migrated)  # ADMIN_DOCS_DIR unset
    listing = client.get("/admin/docs").json()["docs"]
    assert all("size" not in d for d in listing)
    assert client.get("/admin/docs/readme").status_code == 503


# ----- audit trail --------------------------------------------------------------------------------


def test_audit_log_covers_admin_actions(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    res = _admin_client(make_settings, migrated)
    audit = res.get("/admin/audit")
    assert audit.status_code == 200
    actions = {e["action"] for e in audit.json()["entries"]}
    assert "admin.login" in actions
    op = _admin_client(make_settings, migrated, role="operator")
    assert op.get("/admin/audit").status_code == 403
