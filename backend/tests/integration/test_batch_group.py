"""Batch groups (2026-10-08 four-condition launch): one platform link, balanced blocks over
every (member batch x arm) cell, cell drawn at consent, session rebound to the cell's manifest."""

from __future__ import annotations

import uuid
from collections import Counter
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select, update

from app.db import schema as s
from app.db.tx import execute_command
from app.domain.manifests.binding import bind
from app.domain.manifests.store import ContentStore
from app.flags import FeatureFlags
from app.main import create_app
from app.services import adminops, credentials, lifecycle
from app.services.adminops import AdminActionError
from app.services.entry import enter
from app.services.lifecycle import bound_for_session
from app.services.sessions import register_manifest
from tests.integration import audit_helpers as ah
from tests.integration.conftest import MigratedDatabase

pytestmark = pytest.mark.integration
GROUP = "connect.live.4cell.test"
AI, SCRIPTED = "connect.live.4cell.test.ai", "connect.live.4cell.test.scripted"
MANIFESTS = {AI: "tp_ai_v2", SCRIPTED: "tp_scripted_v4"}
CONSENT = "consent.reference@v5"


def _group(db: MigratedDatabase, store: ContentStore) -> None:
    for batch_id, manifest_id in MANIFESTS.items():
        adminops.create_batch(
            db.runtime, store, actor="test", batch_id=batch_id, manifest_id=manifest_id, mode="live"
        )
    adminops.create_batch_group(
        db.runtime, store, actor="test", group_id=GROUP, batch_ids=[AI, SCRIPTED], mode="live"
    )
    with pytest.raises(AdminActionError) as exc:
        adminops.set_enrollment(db.runtime, AI, open_=True, actor="test", reason="direct")
    assert exc.value.reason == "batch_in_group"
    adminops.set_group_enrollment(db.runtime, GROUP, open_=True, actor="test", reason="launch")


def _enter(client: TestClient, pid: str, batch: str = GROUP) -> Any:
    client.cookies.clear()
    return client.post(
        "/entry",
        params={"batch": batch, "participantId": pid, "projectId": "P", "assignmentId": pid},
    )


def _consent(db: MigratedDatabase, client: TestClient, sid: uuid.UUID, action: str) -> Any:
    client.headers["x-writer-id"] = client.cookies.get("inv_writer")
    client.headers[credentials.CSRF_HEADER] = client.cookies.get(credentials.CSRF_COOKIE)
    return client.post(
        "/commands",
        json={
            "kind": "consent",
            "command_id": str(uuid.uuid4()),
            "expected_revision": ah.stage(db, sid)[1],
            "payload": {"action": action, "text_version": CONSENT},
        },
    )


def test_group_balances_four_cells_at_consent(
    migrated: MigratedDatabase, make_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INVESTIGATORS_BUILD_SOURCE_SHA256", ah.BUILD_SHA)
    store = ContentStore.load_default()
    _group(migrated, store)
    settings = make_settings(
        APP_MODE="live",
        DATABASE_URL=migrated.dsns.runtime,
        LOCAL_MODEL_DIGEST="ab" * 32,
        CONNECT_COMPLETION={AI: {"code": "C"}, SCRIPTED: {"code": "C"}},
        # This test fields the scripted member deliberately; the deployment flag
        # (default off) must be raised to exercise the four-cell block draw.
        SCRIPTED_TEAMMATES_ENABLED=True,
    )
    app = create_app(settings, engine=migrated.runtime, configure_logs=False)
    with TestClient(app, base_url="https://testserver/investigators/api") as client:
        # A member batch is never directly enterable.
        assert _enter(client, "DIRECT").status_code == 200  # never consents: no slot used
        direct = _enter(client, "DIRECT2", batch=AI)
        assert direct.status_code == 403 and "batch_closed" in direct.text

        # Decliners and consent-page abandoners consume no slot.
        declined = _enter(client, "DECLINER")
        dsid = uuid.UUID(declined.json()["session_id"])
        assert _consent(migrated, client, dsid, "declined").status_code == 200

        sids = []
        for i in range(12):
            entered = _enter(client, f"P{i:02d}")
            assert entered.status_code == 200, entered.text
            sid = uuid.UUID(entered.json()["session_id"])
            consent = _consent(migrated, client, sid, "consented")
            assert consent.status_code == 200, consent.text
            sids.append(sid)
            assert client.get("/session").status_code == 200

        # Re-entry through the group link is same-batch (credential recovery), never a
        # repeat_participation refusal, even though the session moved to a member batch.
        again = _enter(client, "P00")
        assert again.status_code == 403 and "credential_recovery_required" in again.text

    with migrated.superuser.connect() as conn:
        rows = {
            r["session_id"]: r
            for r in conn.execute(select(s.session).where(s.session.c.session_id.in_(sids)))
            .mappings()
            .all()
        }
        allocs = {
            r["session_id"]: r
            for r in conn.execute(
                select(s.allocation).where(s.allocation.c.session_id.in_(sids))
            ).mappings()
        }
        dec = conn.execute(select(s.session).where(s.session.c.session_id == dsid)).mappings().one()
        for sid in sids:
            bound = bound_for_session(conn, store, dict(rows[sid]))
            assert bound.manifest_id == MANIFESTS[rows[sid]["batch_id"]]
        balance = adminops.group_balance(conn, GROUP)

    assert dec["arm"] is None and dec["batch_group_id"] == GROUP
    cells = [(rows[sid]["batch_id"], rows[sid]["arm"]) for sid in sids]
    # 12 consents = 3 complete blocks of the 4 cells, each block holding every cell once.
    for block in range(3):
        assert len(set(cells[block * 4 : block * 4 + 4])) == 4, cells
    assert Counter(cells) == {(b, a): 3 for b in MANIFESTS for a in ("high", "low")}
    assert [allocs[sid]["block_index"] for sid in sids] == [i // 4 for i in range(12)]
    assert all(rows[sid]["batch_group_id"] == GROUP for sid in sids)
    assert all(rows[sid]["compatibility_key"] for sid in sids)
    assert balance["allocated"] == 12
    assert {c["allocated"] for c in balance["cells"]} == {3}


def test_group_refuses_mismatched_or_open_members(migrated: MigratedDatabase) -> None:
    store = ContentStore.load_default()
    adminops.create_batch(
        migrated.runtime, store, actor="t", batch_id="m.a", manifest_id="tp_ai_v2", mode="live"
    )
    adminops.create_batch(
        migrated.runtime,
        store,
        actor="t",
        batch_id="m.b",
        manifest_id="tp_scripted_v4",
        mode="live",
    )
    with migrated.runtime.begin() as conn:
        conn.execute(
            update(s.batch).where(s.batch.c.batch_id == "m.b").values(enrollment_open=True)
        )
    with pytest.raises(AdminActionError) as exc:
        adminops.create_batch_group(
            migrated.runtime,
            store,
            actor="t",
            group_id="g.x",
            batch_ids=["m.a", "m.b"],
            mode="live",
        )
    assert exc.value.reason == "member_open"
    with pytest.raises(AdminActionError) as exc:
        adminops.create_batch_group(
            migrated.runtime,
            store,
            actor="t",
            group_id="m.a",
            batch_ids=["m.a", "m.b"],
            mode="live",
        )
    assert exc.value.reason == "batch_exists"


def test_rebound_sessions_run_their_cell_contract(migrated: MigratedDatabase) -> None:
    """One full block driven into the task: each session generates its world under the
    cell's manifest. Clean 2x2 (2026-10-08): EVERY launch cell is typed chat only —
    the ask picker is closed in both builds — and the only differences across cells
    are the pressure arm and the teammate engine (model vs deterministic pool)."""
    from app.services.entry import enter

    store = ContentStore.load_default()
    for batch_id, manifest_id in (("q.ai", "tp_ai_v2"), ("q.sc", "tp_scripted_v4")):
        adminops.create_batch(
            migrated.runtime,
            store,
            actor="t",
            batch_id=batch_id,
            manifest_id=manifest_id,
            mode="qa",
        )
    adminops.create_batch_group(
        migrated.runtime, store, actor="t", group_id="q.g", batch_ids=["q.ai", "q.sc"], mode="qa"
    )
    adminops.set_group_enrollment(migrated.runtime, "q.g", open_=True, actor="t", reason="")
    seen = set()
    for i in range(4):
        result = enter(
            migrated.runtime,
            store,
            query=ah.query(f"QG{i}", "q.g"),
            app_mode="qa",
            preview_key=None,
            current_manifest_sha256={},
            build_source_sha256=ah.BUILD_SHA,
        )
        sid, writer = result.session_id, result.writer_id
        ah.run(
            migrated,
            store,
            sid,
            writer,
            "consent",
            {"action": "consented", "text_version": CONSENT},
            1,
        )
        for step in (
            {"step": 1},
            {"step": "practice", "action": "share", "record_id": "PRACTICE-K3-04"},
            {"step": "practice", "action": "pick_location", "crate": 3, "location": "The Sawmill"},
            {"step": "practice", "action": "ask", "text": "anything on crate 3?"},
            {
                "step": "comprehension",
                "complete": True,
                "answers": dict(lifecycle.COMPREHENSION_KEY),
            },
        ):
            ah.run(migrated, store, sid, writer, "training_step", step, ah.stage(migrated, sid)[1])
        ah.drive_connecting(migrated, store, sid, writer)
        with migrated.superuser.connect() as conn:
            row = (
                conn.execute(select(s.session).where(s.session.c.session_id == sid))
                .mappings()
                .one()
            )
            assert conn.execute(
                select(s.world_snapshot.c.world_id).where(s.world_snapshot.c.session_id == sid)
            ).first()
        seen.add((row["batch_id"], row["arm"]))
        with migrated.superuser.connect() as conn:
            bound = bound_for_session(conn, store, dict(row))
        # Both launch builds are nl_only: the structured ask channel is closed.
        out = ah.run(
            migrated,
            store,
            sid,
            writer,
            "teammate_request",
            {"crate": 1},
            ah.stage(migrated, sid)[1],
            accept=False,
        )
        assert not out.accepted and out.reason == "channel_closed"
        assert bound.manifest.interaction_mode == "nl_only"
        assert all("structured_seeking" not in ph.enabled for ph in bound.manifest.channels.phases)
        assert bound.manifest.teammates.social_generation == (
            "deterministic_only" if row["batch_id"] == "q.sc" else "model_with_validated_fallback"
        )
    assert seen == {(b, a) for b in ("q.ai", "q.sc") for a in ("high", "low")}


def test_scripted_teammates_flag_blocks_live_fielding(
    migrated: MigratedDatabase, make_settings: Any
) -> None:
    """Owner direction 2026-10-11: the scripted-teammate family
    (``social_generation: deterministic_only``) is excluded from live fielding
    unless the deployment opts in — refused at batch mint, group mint,
    enrollment-open, and consent allocation. Preview/QA are unaffected."""
    store = ContentStore.load_default()
    # Live batch mint on a scripted manifest refuses; QA mint and AI mint stay open.
    with pytest.raises(AdminActionError) as exc:
        adminops.create_batch(
            migrated.runtime,
            store,
            actor="t",
            batch_id="f.sc",
            manifest_id="tp_scripted_v4",
            mode="live",
            flags=FeatureFlags(scripted_teammates=False),
        )
    assert exc.value.reason == "scripted_teammates_disabled"
    adminops.create_batch(
        migrated.runtime,
        store,
        actor="t",
        batch_id="f.sc.qa",
        manifest_id="tp_scripted_v4",
        mode="qa",
        flags=FeatureFlags(scripted_teammates=False),
    )
    adminops.create_batch(
        migrated.runtime,
        store,
        actor="t",
        batch_id="f.ai",
        manifest_id="tp_ai_v2",
        mode="live",
        flags=FeatureFlags(scripted_teammates=False),
    )
    # Group mint refuses a scripted member under the flag; minted while enabled,
    # enrollment-open refuses it later (the flag-flip window).
    adminops.create_batch(
        migrated.runtime,
        store,
        actor="t",
        batch_id="f.sc2",
        manifest_id="tp_scripted_v4",
        mode="live",
    )
    with pytest.raises(AdminActionError) as exc:
        adminops.create_batch_group(
            migrated.runtime,
            store,
            actor="t",
            group_id="f.g",
            batch_ids=["f.ai", "f.sc2"],
            mode="live",
            flags=FeatureFlags(scripted_teammates=False),
        )
    assert exc.value.reason == "scripted_teammates_disabled"
    adminops.create_batch_group(
        migrated.runtime,
        store,
        actor="t",
        group_id="f.g",
        batch_ids=["f.ai", "f.sc2"],
        mode="live",
    )
    with pytest.raises(AdminActionError) as exc:
        adminops.set_group_enrollment(
            migrated.runtime,
            "f.g",
            open_=True,
            actor="t",
            reason="launch",
            flags=FeatureFlags(scripted_teammates=False),
        )
    assert exc.value.reason == "scripted_teammates_disabled"
    # Direct open of a standalone live scripted batch refuses.
    adminops.create_batch(
        migrated.runtime,
        store,
        actor="t",
        batch_id="f.sc3",
        manifest_id="tp_scripted_v4",
        mode="live",
    )
    with pytest.raises(AdminActionError) as exc:
        adminops.set_enrollment(
            migrated.runtime,
            "f.sc3",
            open_=True,
            actor="t",
            reason="direct",
            flags=FeatureFlags(scripted_teammates=False),
        )
    assert exc.value.reason == "scripted_teammates_disabled"
    # Consent gate (last line): a live scripted batch that somehow opened still
    # refuses the allocation at consent — the draw rolls back, no slot consumed.
    bound = bind(store, "tp_scripted_v4", mode="live")
    with migrated.superuser.begin() as conn:
        register_manifest(conn, bound)
        conn.execute(
            insert(s.batch).values(
                batch_id="f.open",
                study_key=bound.manifest.study_key,
                manifest_id="tp_scripted_v4",
                mode="live",
                enrollment_open=True,
            )
        )
    result = enter(
        migrated.runtime,
        store,
        query=ah.query("FLG", "f.open"),
        app_mode="live",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=ah.BUILD_SHA,
    )
    out = execute_command(
        migrated.runtime,
        ah.cmd(
            result.session_id,
            "consent",
            {"action": "consented", "text_version": bound.artifacts["instruments.consent"].key},
            1,
        ),
        lifecycle.handler_for(
            "consent",
            store=store,
            behavior_sha=ah.BUILD_SHA,
            writer=result.writer_id,
            provider_model_digest="d" * 64,
            flags=FeatureFlags(scripted_teammates=False),
        ),
    )
    assert not out.accepted and out.reason == "scripted_teammates_disabled"
    with migrated.runtime.connect() as conn:
        assert conn.execute(select(func.count()).select_from(s.allocation)).scalar_one() == 0


def test_crate_condition_flags_block_live_fielding(
    migrated: MigratedDatabase, make_settings: Any
) -> None:
    """Owner direction 2026-10-15: manifests pinning a non-legacy task shape —
    ``task.crate_overlap`` (none/some/full) or ``task.participant_intel_budget``
    — are excluded from live fielding while CRATE_OVERLAP_ENABLED /
    CRATE_NUMBER_ENABLED are 0. Refused at batch mint, group mint,
    enrollment-open, and consent allocation; preview/QA unaffected."""
    store = ContentStore.load_default()
    # --- CRATE_OVERLAP_ENABLED=0 -------------------------------------------------
    with pytest.raises(AdminActionError) as exc:
        adminops.create_batch(
            migrated.runtime,
            store,
            actor="t",
            batch_id="c.ov",
            manifest_id="tp_ai_ovnone_v1",
            mode="live",
            flags=FeatureFlags(crate_overlap=False),
        )
    assert exc.value.reason == "crate_overlap_disabled"
    # QA mint and live AI mint stay open; flag-on live mint works.
    adminops.create_batch(
        migrated.runtime,
        store,
        actor="t",
        batch_id="c.ov.qa",
        manifest_id="tp_ai_ovnone_v1",
        mode="qa",
        flags=FeatureFlags(crate_overlap=False),
    )
    adminops.create_batch(
        migrated.runtime,
        store,
        actor="t",
        batch_id="c.ai",
        manifest_id="tp_ai_v13",
        mode="live",
        flags=FeatureFlags(crate_overlap=False, crate_number=False),
    )
    adminops.create_batch(
        migrated.runtime,
        store,
        actor="t",
        batch_id="c.ov2",
        manifest_id="tp_ai_ovfull_v1",
        mode="live",
    )
    # Group mint + late flag-flip enrollment-open both refuse the overlap member.
    with pytest.raises(AdminActionError) as exc:
        adminops.create_batch_group(
            migrated.runtime,
            store,
            actor="t",
            group_id="c.g",
            batch_ids=["c.ai", "c.ov2"],
            mode="live",
            flags=FeatureFlags(crate_overlap=False),
        )
    assert exc.value.reason == "crate_overlap_disabled"
    adminops.create_batch_group(
        migrated.runtime,
        store,
        actor="t",
        group_id="c.g",
        batch_ids=["c.ai", "c.ov2"],
        mode="live",
    )
    with pytest.raises(AdminActionError) as exc:
        adminops.set_group_enrollment(
            migrated.runtime,
            "c.g",
            open_=True,
            actor="t",
            reason="launch",
            flags=FeatureFlags(crate_overlap=False),
        )
    assert exc.value.reason == "crate_overlap_disabled"
    adminops.create_batch(
        migrated.runtime,
        store,
        actor="t",
        batch_id="c.ov3",
        manifest_id="tp_ai_ovsome_v1",
        mode="live",
    )
    with pytest.raises(AdminActionError) as exc:
        adminops.set_enrollment(
            migrated.runtime,
            "c.ov3",
            open_=True,
            actor="t",
            reason="direct",
            flags=FeatureFlags(crate_overlap=False),
        )
    assert exc.value.reason == "crate_overlap_disabled"
    # Consent gate (last line): an open live overlap batch still refuses the
    # allocation at consent — the draw rolls back, no slot consumed.
    bound = bind(store, "tp_ai_ovfull_v1", mode="live")
    with migrated.superuser.begin() as conn:
        register_manifest(conn, bound)
        conn.execute(
            insert(s.batch).values(
                batch_id="c.open",
                study_key=bound.manifest.study_key,
                manifest_id="tp_ai_ovfull_v1",
                mode="live",
                enrollment_open=True,
            )
        )
    result = enter(
        migrated.runtime,
        store,
        query=ah.query("CRTOV", "c.open"),
        app_mode="live",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=ah.BUILD_SHA,
    )
    out = execute_command(
        migrated.runtime,
        ah.cmd(
            result.session_id,
            "consent",
            {"action": "consented", "text_version": bound.artifacts["instruments.consent"].key},
            1,
        ),
        lifecycle.handler_for(
            "consent",
            store=store,
            behavior_sha=ah.BUILD_SHA,
            writer=result.writer_id,
            provider_model_digest="d" * 64,
            flags=FeatureFlags(crate_overlap=False),
        ),
    )
    assert not out.accepted and out.reason == "crate_overlap_disabled"
    # --- CRATE_NUMBER_ENABLED=0 --------------------------------------------------
    with pytest.raises(AdminActionError) as exc:
        adminops.create_batch(
            migrated.runtime,
            store,
            actor="t",
            batch_id="c.thin",
            manifest_id="tp_ai_ovsome_thin_v1",
            mode="live",
            flags=FeatureFlags(crate_number=False),
        )
    assert exc.value.reason == "crate_number_disabled"
    # The thin profile also pins an overlap level — the overlap gate catches it
    # first when both flags are off.
    with pytest.raises(AdminActionError) as exc:
        adminops.create_batch(
            migrated.runtime,
            store,
            actor="t",
            batch_id="c.thin2",
            manifest_id="tp_ai_ovsome_thin_v1",
            mode="live",
            flags=FeatureFlags(crate_overlap=False, crate_number=False),
        )
    assert exc.value.reason == "crate_overlap_disabled"
    # Consent refuses under the number gate alone.
    bound_thin = bind(store, "tp_ai_ovsome_thin_v1", mode="live")
    with migrated.superuser.begin() as conn:
        register_manifest(conn, bound_thin)
        conn.execute(
            insert(s.batch).values(
                batch_id="c.thin.open",
                study_key=bound_thin.manifest.study_key,
                manifest_id="tp_ai_ovsome_thin_v1",
                mode="live",
                enrollment_open=True,
            )
        )
    result2 = enter(
        migrated.runtime,
        store,
        query=ah.query("CRTN", "c.thin.open"),
        app_mode="live",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=ah.BUILD_SHA,
    )
    out2 = execute_command(
        migrated.runtime,
        ah.cmd(
            result2.session_id,
            "consent",
            {
                "action": "consented",
                "text_version": bound_thin.artifacts["instruments.consent"].key,
            },
            1,
        ),
        lifecycle.handler_for(
            "consent",
            store=store,
            behavior_sha=ah.BUILD_SHA,
            writer=result2.writer_id,
            provider_model_digest="d" * 64,
            flags=FeatureFlags(crate_overlap=True, crate_number=False),
        ),
    )
    assert not out2.accepted and out2.reason == "crate_number_disabled"
    with migrated.runtime.connect() as conn:
        assert conn.execute(select(func.count()).select_from(s.allocation)).scalar_one() == 0
