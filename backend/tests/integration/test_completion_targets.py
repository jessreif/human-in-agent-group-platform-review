"""New four-cell pilot: capacity, replacement, completion, and concurrent commands.

Completion-boundary fixtures deliberately skip the task in DISPOSABLE databases.
The separate full-flow browser gate validates the actual 300-second participant path.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from app.db import schema as s
from app.db.tx import CommandResult, execute_command
from app.domain.manifests.store import ContentStore
from app.flags import FeatureFlags
from app.main import create_app
from app.services import adminops, adminviews, lifecycle
from app.services.enrollment import EnrollmentTarget
from app.services.entry import EntryError, EntryResult, enter
from tests.integration import audit_helpers as ah
from tests.integration.conftest import MigratedDatabase

pytestmark = pytest.mark.integration
GROUP = "quota.pilot"
MANIFESTS = [
    "tp_ai_ovnone_v3",
    "tp_ai_ovnone_thin_v3",
    "tp_ai_ovsome_v3",
    "tp_ai_ovsome_thin_v3",
]
BATCHES = [f"quota.cell{i}" for i in range(4)]


@pytest.fixture
def pilot(migrated: MigratedDatabase) -> ContentStore:
    store = ContentStore.load_default()
    for batch, manifest in zip(BATCHES, MANIFESTS, strict=True):
        adminops.create_batch(
            migrated.runtime, store, actor="test", batch_id=batch, manifest_id=manifest, mode="live"
        )
    adminops.create_batch_group(
        migrated.runtime,
        store,
        actor="test",
        group_id=GROUP,
        batch_ids=BATCHES,
        mode="live",
        flags=FeatureFlags(time_pressure=False),
        enrollment_target=EnrollmentTarget(platform="prolific", target_completers_per_cell=4),
    )
    adminops.set_group_enrollment(
        migrated.runtime,
        GROUP,
        open_=True,
        actor="test",
        reason="test",
        flags=FeatureFlags(time_pressure=False),
    )
    return store


def _entry(db: MigratedDatabase, store: ContentStore, i: int, **kwargs: Any) -> EntryResult:
    options = {
        "query": ah.query(f"QUOTA_{i}", GROUP),
        "app_mode": "live",
        "preview_key": None,
        "current_manifest_sha256": {},
        "build_source_sha256": ah.BUILD_SHA,
        "time_pressure_enabled": False,
        **kwargs,
    }
    return enter(db.runtime, store, **options)


def _consent(db: MigratedDatabase, store: ContentStore, entry: EntryResult) -> CommandResult:
    sid = entry.session_id
    return execute_command(
        db.runtime,
        ah.cmd(
            sid,
            "consent",
            {"action": "consented", "text_version": "consent.reference@v9"},
            ah.stage(db, sid)[1],
        ),
        lifecycle.handler_for(
            "consent",
            store=store,
            behavior_sha=ah.BUILD_SHA,
            writer=entry.writer_id,
            provider_model_digest="cd" * 32,
            flags=FeatureFlags(time_pressure=False),
        ),
    )


def _fill(db: MigratedDatabase, store: ContentStore) -> list[EntryResult]:
    entries = [_entry(db, store, i) for i in range(16)]
    for entry in entries:
        out = _consent(db, store, entry)
        assert out.accepted, out.reason
    return entries


def _balance(db: MigratedDatabase) -> dict[str, Any]:
    with db.runtime.connect() as conn:
        return adminops.group_balance(conn, GROUP, time_pressure_enabled=False)


def _prepare_completion(
    db: MigratedDatabase, store: ContentStore, entry: EntryResult, choice: str = "use_all"
) -> None:
    sid = entry.session_id
    with db.runtime.begin() as conn:
        conn.execute(update(s.session).where(s.session.c.session_id == sid).values(stage="debrief"))
        conn.execute(
            insert(s.result).values(
                session_id=sid,
                scoring_version="quota-boundary-fixture",
                correct_total=0,
                correct_teamdep=0,
                correct_ownfile=0,
                status="scored",
                finalized_seq=1,
            )
        )
    ah.run(db, store, sid, entry.writer_id, "debrief_ack", {}, ah.stage(db, sid)[1])
    ah.run(
        db, store, sid, entry.writer_id, "data_use_choice", {"choice": choice}, ah.stage(db, sid)[1]
    )


def _complete(db: MigratedDatabase, store: ContentStore, entry: EntryResult) -> CommandResult:
    return execute_command(
        db.runtime,
        ah.cmd(entry.session_id, "complete", {}, ah.stage(db, entry.session_id)[1]),
        lifecycle.completion_handler(store=store, writer=entry.writer_id, prolific_code="PLACEHOLDER-CODE"),
    )


def test_four_exact_cells_sixteen_holds_and_authenticated_resume(
    migrated: MigratedDatabase, pilot: ContentStore
) -> None:
    entries = _fill(migrated, pilot)
    balance = _balance(migrated)
    assert balance["allocated"] == 16
    assert {c["manifest_id"] for c in balance["cells"]} == set(MANIFESTS)
    assert {
        (c["arm"], c["active"], c["completed"], c["available"], c["target"])
        for c in balance["cells"]
    } == {("nopress", 4, 0, 0, 4)}
    with pytest.raises(EntryError, match="batch_at_capacity"):
        _entry(migrated, pilot, 100)
    adminops.set_group_enrollment(
        migrated.runtime, GROUP, open_=False, actor="test", reason="pause"
    )
    resumed = _entry(migrated, pilot, 0, presented_token=entries[0].credential_token)
    assert resumed.resumed and resumed.session_id == entries[0].session_id
    assert _balance(migrated)["allocated"] == 16


def test_concurrent_consents_cannot_overbook_or_leave_partial_consent(
    migrated: MigratedDatabase, pilot: ContentStore
) -> None:
    entries = [_entry(migrated, pilot, i) for i in range(24)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda e: _consent(migrated, pilot, e), entries))
    assert Counter((r.accepted, r.reason) for r in results) == {
        (True, None): 16,
        (False, "batch_at_capacity"): 8,
    }
    assert {c["active"] for c in _balance(migrated)["cells"]} == {4}
    with migrated.runtime.connect() as conn:
        for entry, result in zip(entries, results, strict=True):
            if not result.accepted:
                assert ah.stage(migrated, entry.session_id)[0] == "consent"
                assert not conn.execute(
                    select(s.consent_event).where(s.consent_event.c.session_id == entry.session_id)
                ).first()


@pytest.mark.parametrize("terminal", ["withdrawn", "abandoned", "technical_stop"])
def test_terminal_dropout_releases_only_its_cell_without_rewriting_history(
    migrated: MigratedDatabase, pilot: ContentStore, terminal: str
) -> None:
    entries = _fill(migrated, pilot)
    sid = entries[0].session_id
    with migrated.runtime.connect() as conn:
        before = (
            conn.execute(select(s.allocation).where(s.allocation.c.session_id == sid))
            .mappings()
            .one()
        )
        before = dict(before)
    if terminal == "withdrawn":
        ah.run(
            migrated, pilot, sid, entries[0].writer_id, "withdraw", {}, ah.stage(migrated, sid)[1]
        )
    else:
        # Model an already committed terminal disposition, not a fabricated resume.
        with migrated.runtime.begin() as conn:
            conn.execute(
                update(s.session)
                .where(s.session.c.session_id == sid)
                .values(
                    stage=terminal,
                    terminal_reason="test_boundary",
                )
            )
    replacement = _entry(migrated, pilot, 100)
    assert _consent(migrated, pilot, replacement).accepted
    with migrated.runtime.connect() as conn:
        after = dict(
            conn.execute(select(s.allocation).where(s.allocation.c.session_id == sid))
            .mappings()
            .one()
        )
        new = (
            conn.execute(
                select(s.allocation).where(s.allocation.c.session_id == replacement.session_id)
            )
            .mappings()
            .one()
        )
    assert after == before
    assert new["batch_id"] == before["batch_id"] and new["arm"] == before["arm"]
    assert new["block_index"] > before["block_index"]
    assert _balance(migrated)["allocated"] == 17
    assert {c["active"] for c in _balance(migrated)["cells"]} == {4}
    old = _entry(migrated, pilot, 0, presented_token=entries[0].credential_token)
    assert old.resumed and ah.stage(migrated, sid)[0] == terminal
    out = _consent(migrated, pilot, old)
    assert not out.accepted and out.reason == "wrong_stage"


def test_concurrent_valid_completions_close_intake_exactly_once(
    migrated: MigratedDatabase, pilot: ContentStore
) -> None:
    entries = _fill(migrated, pilot)
    for i, entry in enumerate(entries):
        _prepare_completion(migrated, pilot, entry, "use_no_free_text" if i == 0 else "use_all")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda e: _complete(migrated, pilot, e), entries))
    assert all(r.accepted for r in results), [(r.accepted, r.reason) for r in results]
    assert {r.result["completion_code"] for r in results} == {"PLACEHOLDER-CODE"}
    balance = _balance(migrated)
    assert balance["quota"]["completed"] == 16
    assert balance["quota"]["target_met"]
    assert {(c["completed"], c["active"], c["available"]) for c in balance["cells"]} == {(4, 0, 0)}
    with migrated.runtime.connect() as conn:
        assert not conn.execute(
            select(s.batch_group.c.enrollment_open).where(s.batch_group.c.group_id == GROUP)
        ).scalar_one()
        audits = list(
            conn.execute(
                select(s.admin_audit).where(s.admin_audit.c.action == "quota_target_reached")
            )
        )
        assert len(audits) == 1
    with pytest.raises(adminops.AdminActionError, match="quota_target_reached"):
        adminops.set_group_enrollment(
            migrated.runtime,
            GROUP,
            open_=True,
            actor="test",
            reason="no overfill",
            flags=FeatureFlags(time_pressure=False),
        )
    with pytest.raises(EntryError, match="batch_closed"):
        _entry(migrated, pilot, 200)


def test_raise_enrollment_target_tops_off_a_met_quota_increase_only(
    migrated: MigratedDatabase, pilot: ContentStore
) -> None:
    entries = _fill(migrated, pilot)
    for entry in entries:
        _prepare_completion(migrated, pilot, entry)
        assert _complete(migrated, pilot, entry).accepted
    assert _balance(migrated)["quota"]["target_met"]
    for bad in (4, 3):
        with pytest.raises(adminops.AdminActionError, match="target_must_increase"):
            adminops.raise_enrollment_target(
                migrated.runtime, GROUP, target_completers_per_cell=bad, actor="r", reason="x"
            )
    with pytest.raises(adminops.AdminActionError, match="unknown_batch"):
        adminops.raise_enrollment_target(
            migrated.runtime, "no.such", target_completers_per_cell=8, actor="r", reason="x"
        )
    out = adminops.raise_enrollment_target(
        migrated.runtime, GROUP, target_completers_per_cell=6, actor="researcher", reason="wave 2"
    )
    assert out == {
        "group_id": GROUP,
        "previous_target_completers_per_cell": 4,
        "target_completers_per_cell": 6,
        "total_target": 24,
    }
    balance = _balance(migrated)
    assert balance["quota"]["target_completers_per_cell"] == 6
    assert not balance["quota"]["target_met"]
    assert balance["quota"]["available"] == 8
    with migrated.runtime.connect() as conn:
        group = (
            conn.execute(select(s.batch_group).where(s.batch_group.c.group_id == GROUP))
            .mappings()
            .one()
        )
        assert not group["enrollment_open"]
        policy = group["enrollment_policy"]
        assert [c["manifest_id"] for c in policy["cells"]] == MANIFESTS
        assert policy["platform"] == "prolific" and policy["test_only"] is False
        audit_row = (
            conn.execute(
                select(s.admin_audit).where(s.admin_audit.c.action == "enrollment_target_update")
            )
            .mappings()
            .one()
        )
        assert audit_row["actor"] == "researcher"
        assert audit_row["payload"]["previous_target_completers_per_cell"] == 4
    adminops.set_group_enrollment(
        migrated.runtime,
        GROUP,
        open_=True,
        actor="test",
        reason="top-off",
        flags=FeatureFlags(time_pressure=False),
    )
    extra = _entry(migrated, pilot, 300)
    assert _consent(migrated, pilot, extra).accepted


def test_enrollment_target_endpoint_requires_researcher_and_validates(
    migrated: MigratedDatabase, pilot: ContentStore, make_settings: Any
) -> None:
    from tests.integration.test_wp13_admin import _admin_client, _post

    url = f"/admin/batch-groups/{GROUP}/enrollment-target"
    body = {"target_completers_per_cell": 6, "reason": "wave 2"}
    operator = _admin_client(make_settings, migrated, role="operator")
    assert _post(operator, url, body).status_code == 403
    client = _admin_client(make_settings, migrated)
    assert _post(client, url, {**body, "target_completers_per_cell": "6"}).status_code == 422
    assert _post(client, url, {**body, "cells": []}).status_code == 422
    ok = _post(client, url, body)
    assert ok.status_code == 200, ok.text
    assert ok.json()["target_completers_per_cell"] == 6
    again = _post(client, url, body)
    assert again.status_code == 422, again.text
    assert again.json()["error"] == "target_must_increase"


def test_withdraw_all_completion_frees_capacity_but_does_not_change_payment(
    migrated: MigratedDatabase, pilot: ContentStore
) -> None:
    entries = _fill(migrated, pilot)
    _prepare_completion(migrated, pilot, entries[0], "withdraw_all")
    result = _complete(migrated, pilot, entries[0])
    assert result.accepted and result.result["entitlement"] == {
        "kind": "base",
        "status": "pending",
        "cents": 350,
    }
    assert _balance(migrated)["quota"]["completed"] == 0
    assert _balance(migrated)["quota"]["available"] == 1
    replacement = _entry(migrated, pilot, 100)
    assert _consent(migrated, pilot, replacement).accepted
    assert _balance(migrated)["allocated"] == 17


def test_platform_and_flag_controls_apply_before_entry_and_at_consent(
    migrated: MigratedDatabase,
    pilot: ContentStore,
    make_settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(EntryError, match="test_identity_not_allowed"):
        _entry(migrated, pilot, 1, query=ah.query("TEST_NOT_A_REAL_PARTICIPANT", GROUP))
    for query in (
        {"batch": GROUP, "LAB_PID": "wrong"},
        {"batch": GROUP, "participantId": "wrong"},
    ):
        with pytest.raises(EntryError, match="platform_not_allowed"):
            _entry(migrated, pilot, 1, query=query)
    with pytest.raises(EntryError, match="quota_configuration_changed"):
        _entry(migrated, pilot, 1, time_pressure_enabled=True)
    with pytest.raises(adminops.AdminActionError, match="quota_configuration_changed"):
        adminops.set_group_enrollment(
            migrated.runtime,
            GROUP,
            open_=True,
            actor="test",
            reason="flag drift",
            flags=FeatureFlags(time_pressure=True),
        )
    entered = _entry(migrated, pilot, 1)
    out = execute_command(
        migrated.runtime,
        ah.cmd(
            entered.session_id,
            "consent",
            {"action": "consented", "text_version": "consent.reference@v9"},
            1,
        ),
        lifecycle.handler_for(
            "consent",
            store=pilot,
            behavior_sha=ah.BUILD_SHA,
            writer=entered.writer_id,
            flags=FeatureFlags(time_pressure=True),
        ),
    )
    assert not out.accepted and out.reason == "quota_configuration_changed"
    assert _balance(migrated)["allocated"] == 0
    with migrated.runtime.connect() as conn:
        drift = adminops.group_balance(conn, GROUP, time_pressure_enabled=True)
        assert not drift["quota"]["configuration_matches"]
        assert drift["quota"]["available"] == 0
        assert len(drift["cells"]) == 4
        assert not any(c["allocatable"] for c in drift["cells"])
    monkeypatch.setenv("INVESTIGATORS_BUILD_SOURCE_SHA256", ah.BUILD_SHA)
    settings = make_settings(
        APP_MODE="live",
        DATABASE_URL=migrated.dsns.runtime,
        TIME_PRESSURE_ENABLED=False,
        LOCAL_MODEL_DIGEST="ab" * 32,
    )
    with TestClient(
        create_app(settings, engine=migrated.runtime, configure_logs=False),
        base_url="https://testserver/investigators/api",
    ) as client:
        response = client.post("/entry", params=ah.query("HTTP_QUOTA", GROUP))
        assert response.status_code == 200, response.text
        refused = client.post("/entry", params={"batch": GROUP, "participantId": "HTTP_CONNECT"})
        assert refused.status_code == 403 and refused.json()["detail"] == "platform_not_allowed"


def test_direct_member_bypass_remains_blocked_even_if_a_row_is_misopened(
    migrated: MigratedDatabase, pilot: ContentStore
) -> None:
    with migrated.runtime.begin() as conn:
        conn.execute(
            update(s.batch).where(s.batch.c.batch_id == BATCHES[0]).values(enrollment_open=True)
        )
    with pytest.raises(EntryError, match="batch_in_group"):
        _entry(migrated, pilot, 1, query=ah.query("DIRECT", BATCHES[0]))


def test_separate_test_group_requires_test_ids_and_never_creates_payable_data(
    migrated: MigratedDatabase,
) -> None:
    store = ContentStore.load_default()
    group = "study.smoke.quota"
    batches = [f"smoke.quota.cell{i}" for i in range(4)]
    for batch, manifest in zip(batches, MANIFESTS, strict=True):
        adminops.create_batch(
            migrated.runtime, store, actor="test", batch_id=batch, manifest_id=manifest, mode="live"
        )
    adminops.create_batch_group(
        migrated.runtime,
        store,
        actor="test",
        group_id=group,
        batch_ids=batches,
        mode="live",
        flags=FeatureFlags(time_pressure=False),
        enrollment_target=EnrollmentTarget(
            platform="prolific", target_completers_per_cell=1, test_only=True
        ),
    )
    adminops.set_group_enrollment(
        migrated.runtime,
        group,
        open_=True,
        actor="test",
        reason="synthetic only",
        flags=FeatureFlags(time_pressure=False),
    )
    with pytest.raises(EntryError, match="test_only_batch"):
        _entry(migrated, store, 1, query=ah.query("ordinary_id", group))
    entry = _entry(migrated, store, 1, query=ah.query("TEST_QUOTA", group))
    assert _consent(migrated, store, entry).accepted
    _prepare_completion(migrated, store, entry)
    out = _complete(migrated, store, entry)
    assert out.accepted and out.result["entitlement"] == {
        "kind": "base",
        "status": "ineligible",
        "cents": 0,
    }
    with pytest.raises(adminops.AdminActionError, match="test_session"):
        adminops.decide_entitlement(
            migrated.runtime, entry.session_id, "base", decision="approved", actor="test"
        )
    with migrated.runtime.connect() as conn:
        assert not adminviews.compensation_queue(conn)["rows"]
        balance = adminops.group_balance(conn, group, time_pressure_enabled=False)
        assert balance["quota"]["test_only"] and balance["quota"]["completed"] == 1


def test_deleted_completer_releases_capacity_without_reassigning_the_deleted_session(
    migrated: MigratedDatabase, pilot: ContentStore
) -> None:
    import uuid

    entries = _fill(migrated, pilot)
    _prepare_completion(migrated, pilot, entries[0])
    assert _complete(migrated, pilot, entries[0]).accepted
    assert _balance(migrated)["quota"]["completed"] == 1
    with migrated.runtime.begin() as conn:
        conn.execute(
            insert(s.admin_audit).values(
                audit_id=uuid.uuid4(),
                session_id=entries[0].session_id,
                actor="test",
                action="deletion_tombstone",
                payload={},
            )
        )
    assert _balance(migrated)["quota"]["completed"] == 0
    assert _balance(migrated)["quota"]["available"] == 1
    replacement = _entry(migrated, pilot, 100)
    assert _consent(migrated, pilot, replacement).accepted
    assert _balance(migrated)["allocated"] == 17
    with pytest.raises(EntryError, match="session_deleted"):
        _entry(migrated, pilot, 0, presented_token=entries[0].credential_token)
