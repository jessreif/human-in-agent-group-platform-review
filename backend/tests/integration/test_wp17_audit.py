"""Audit-remediation tests for the Study Admin Dashboard (WP17).

Each test maps to a finding in
``INVESTIGATORS_TEAM_EXPERIMENT_DASHBOARD_PLAN_AUDIT.md`` — these are
behavior-level proofs, not envelope smoke tests. Findings covered here:

DA-02 measured zeros · DA-03 canonical accuracy ratio · DA-04 partition
integrity · DA-06 bound-item value validation · DA-09 data-use gate before
content · DA-11 latest confidence revision · DA-12 bound-version missingness ·
DA-13 labeled diagnostic cohort · DA-14 scoped tombstone counting ·
DA-15/16 copy-kind serialization + prompt/payment restriction · DA-18 censored
budget · DA-19 arm-split timing · DA-20 audit scopes · DA-21 UTC date filters ·
DA-25 unknown filter 422 · DA-10 scoped export POST · AG-02 on-disk manifest ·
AG-04 REPEATABLE READ snapshot proof.
"""

from __future__ import annotations

import csv
import hashlib
import io
import pathlib
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import schema as s
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


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _client(
    make_settings: Any,
    db: MigratedDatabase,
    role: str = "researcher",
    **overrides: Any,
) -> TestClient:
    values: dict[str, Any] = {
        "APP_MODE": "qa",
        "DATABASE_URL": db.dsns.runtime,
        "ADMIN_PASSWORD": PW,
        "ADMIN_OPERATOR_PASSWORD": OPW,
        "ADMIN_COMPENSATION_PASSWORD": CPW,
        "PREVIEW_SIGNING_KEY": KEY,
    }
    values.update(overrides)
    app = create_app(
        make_settings(**values),
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
    csrf = resp.cookies.get(adminauth.CSRF_COOKIE)
    if csrf:
        client.headers["x-csrf-token"] = csrf
    return client


_UNSET: Any = object()


def _seed(
    db: MigratedDatabase,
    store: ContentStore,
    pid: str,
    batch: str,
    *,
    mode: str = "qa",
    arm: str | None = None,
    compat: str | None = None,
    manifest_id: str = "tp_demo_v3",
    build: str | None = None,
    behavior: Any = _UNSET,
) -> uuid.UUID:
    """``tp_demo_v3`` is not live-approvable (demo artifacts are preview/qa
    only), so ``mode='live'`` is simulated by entering under qa then flipping
    ``session.mode`` — eligibility reads the column, and the check constraint
    allows it because the qa binding is faithful-timed anyway."""
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
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256="ab" * 32,
    )
    with db.superuser.begin() as conn:
        sets: dict[str, Any] = {}
        if mode != "qa":
            sets["mode"] = mode
        if arm:
            sets["arm"] = arm
        if compat:
            sets["compatibility_key"] = compat
        if build:
            sets["build_source_sha256"] = build
        if behavior is not _UNSET:
            sets["behavior_source_sha256"] = behavior
        if sets:
            conn.execute(
                update(s.session).where(s.session.c.session_id == result.session_id).values(**sets)
            )
    return result.session_id


def _complete(
    db: MigratedDatabase,
    sid: uuid.UUID,
    *,
    data_use: str | None = "use_all",
    outcome: str = "completed",
) -> None:
    """Durable completion evidence: consent + stage + receipt + scored result
    + latest data-use choice (DA-05 reconciliation)."""
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
            update(s.session).where(s.session.c.session_id == sid).values(stage="completion")
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
        if data_use:
            conn.execute(
                insert(s.data_use_choice).values(
                    session_id=sid, choice_seq=1, choice=data_use, recorded_seq=9998
                )
            )


def _item(
    db: MigratedDatabase,
    sid: uuid.UUID,
    item_id: str,
    status: str,
    response: Any = None,
    *,
    instrument: str = "post_task.tp_demo",
    version: int = 2,
    reason: str | None = None,
) -> None:
    with db.runtime.begin() as conn:
        conn.execute(
            insert(s.item_response).values(
                session_id=sid,
                instrument_id=instrument,
                item_id=item_id,
                instrument_version=version,
                response=None if response is None else {"value": response},
                status=status,
                missing_reason=reason,
                revision_count=1,
                first_seq=1,
                last_seq=1,
            )
        )


def _confidence(
    db: MigratedDatabase,
    sid: uuid.UUID,
    crate: int,
    revision: int,
    status: str,
    value: int | None,
) -> None:
    with db.runtime.begin() as conn:
        conn.execute(
            insert(s.confidence_response).values(
                session_id=sid,
                crate=crate,
                revision=revision,
                value=value,
                status=status,
                instrument_version=1,
                event_seq=revision,
            )
        )


def _analysis(client: TestClient, qs: str = "mode=live") -> dict[str, Any]:
    resp = client.get(f"/admin/dashboard/analysis?{qs}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _power_row(body: dict[str, Any], measure: str, arm: str = "high") -> dict[str, Any] | None:
    for r in body["power_inputs"]["rows"]:
        if r["measure"] == measure and r["arm"] == arm:
            return r
    return None


# ----- DA-25: unknown filter keys ----------------------------------------------------------------


def test_unknown_filter_keys_rejected(migrated: MigratedDatabase, make_settings: Any) -> None:
    client = _client(make_settings, migrated)
    for path in (
        "/admin/dashboard/overview?bogus=1",
        "/admin/dashboard/timing?session_id=x",  # not an allowed extra here
        "/admin/dashboard/readiness?bogus=1",
        "/admin/dashboard/readiness?audit_scope=nonsense",
        "/admin/dashboard/analysis?arm=high",  # arm is not a filter key
        "/admin/exports?bogus=1",
    ):
        resp = client.get(path)
        assert resp.status_code == 422, f"{path}: {resp.status_code}"


def test_invalid_mode_and_dates_422(migrated: MigratedDatabase, make_settings: Any) -> None:
    client = _client(make_settings, migrated)
    assert client.get("/admin/dashboard/overview?mode=bogus").status_code == 422
    assert client.get("/admin/dashboard/overview?since=not-a-date").status_code == 422


def test_analysis_echoes_scripted_teammates_flag(
    migrated: MigratedDatabase, make_settings: Any
) -> None:
    """AID-63 follow-up: the client hides scripted cells when the flag is off,
    so the payload must echo the deployment's setting verbatim both ways."""
    on = _client(make_settings, migrated, SCRIPTED_TEAMMATES_ENABLED=True)
    assert _analysis(on)["scripted_teammates_enabled"] is True
    off = _client(make_settings, migrated, SCRIPTED_TEAMMATES_ENABLED=False)
    assert _analysis(off)["scripted_teammates_enabled"] is False


# ----- DA-14: tombstone counting shares the cohort filter predicates -----------------------------


def test_tombstones_scoped_to_filters(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    in_scope = _seed(migrated, store, "T1", "qa-tom-a")
    out_scope = _seed(migrated, store, "T2", "qa-tom-b")
    with migrated.runtime.begin() as conn:
        for sid in (in_scope, out_scope):
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
    body = client.get("/admin/dashboard/overview?mode=qa&batch_id=qa-tom-a").json()
    # Only the batch-A tombstone is counted — the batch-B tombstone is out of
    # scope and must not inflate the excluded total (DA-14).
    assert body["cohort"]["tombstoned_excluded"] == 1
    body_b = client.get("/admin/dashboard/overview?mode=qa&batch_id=qa-tom-b").json()
    assert body_b["cohort"]["tombstoned_excluded"] == 1
    all_body = client.get("/admin/dashboard/overview?mode=qa").json()
    assert all_body["cohort"]["tombstoned_excluded"] >= 2


# ----- DA-02/03/04: canonical scalar measures ----------------------------------------------------


def test_count_measures_are_measured_zeros(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    """A completed session with zero durable events contributes a measured 0,
    not missing data — zero must appear in n_measured and mean (DA-02)."""
    sid = _seed(
        migrated,
        store,
        "Z1",
        "live-zero",
        mode="live",
        arm="high",
        compat="ck1:" + "a" * 64,
    )
    _complete(migrated, sid)
    client = _client(make_settings, migrated)
    body = _analysis(client)
    assert body["cohort"]["eligible_research"] >= 1
    row = _power_row(body, "sharing.participant_share_events")
    assert row is not None
    assert row["n_measured"] >= 1
    assert row["mean"] == 0.0
    assert row["missing"] == 0


def test_accuracy_uses_canonical_ratio_semantics(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    """``accuracy.correct_total`` is score_rows' ratio — a session with no
    assigned-answer denominator is ``undefined``, never 0.0 (DA-03). Golden:
    3/4 assigned-correct → exactly 0.75."""
    no_world = _seed(
        migrated,
        store,
        "A1",
        "live-acc",
        mode="live",
        arm="low",
        compat="ck1:" + "b" * 64,
    )
    _complete(migrated, no_world)
    scored = _seed(
        migrated,
        store,
        "A2",
        "live-acc",
        mode="live",
        arm="low",
        compat="ck1:" + "b" * 64,
    )
    _complete(migrated, scored)
    truth = {1: "loc_a", 2: "loc_b", 3: "loc_c", 4: "loc_d"}
    with migrated.runtime.begin() as conn:
        conn.execute(
            insert(s.world_snapshot).values(
                world_id=uuid.uuid4(),
                session_id=scored,
                generator="test",
                content={
                    "assigned": [1, 2, 3, 4],
                    "role_of": {"1": "own", "2": "own", "3": "team", "4": "team"},
                    "crates": [{"num": n, "truth": t} for n, t in truth.items()],
                },
                content_sha256="ab" * 32,
            )
        )
        for crate in (1, 2, 3, 4):
            conn.execute(
                insert(s.answer_revision).values(
                    session_id=scored,
                    crate=crate,
                    revision=1,
                    location_id=truth[crate] if crate != 4 else "loc_wrong",
                    accepted_seq=crate,
                )
            )
            conn.execute(
                insert(s.answer_final).values(
                    session_id=scored,
                    crate=crate,
                    revision=1,
                    location_id=truth[crate] if crate != 4 else "loc_wrong",
                    finalized_seq=10 + crate,
                )
            )
    client = _client(make_settings, migrated)
    body = _analysis(client, "mode=live&batch_id=live-acc")
    # No world_snapshot/answer rows → undefined denominator; counts as missing,
    # never as accuracy = 0.0 (DA-03).
    row = _power_row(body, "accuracy.correct_total", arm="low")
    assert row is not None
    assert row["missing"] == 1
    # Golden: the scored session contributes exactly 3/4 = 0.75.
    assert row["n_measured"] == 1
    assert row["mean"] == 0.75


def test_partitions_never_pool(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    """Two compatibility keys produce two partition rows — power inputs are
    never pooled across compatibility boundaries (DA-04)."""
    s1 = _seed(
        migrated, store, "P1", "live-part", mode="live", arm="high", compat="ck1:" + "c" * 64
    )
    s2 = _seed(
        migrated, store, "P2", "live-part", mode="live", arm="high", compat="ck1:" + "d" * 64
    )
    _complete(migrated, s1)
    _complete(migrated, s2)
    client = _client(make_settings, migrated)
    body = _analysis(client, "mode=live&batch_id=live-part")
    rows = [
        r
        for r in body["power_inputs"]["rows"]
        if r["measure"] == "sharing.participant_share_events"
    ]
    partitions = {r["partition"] for r in rows}
    assert len(partitions) == 2
    assert all(r["n_sessions"] == 1 for r in rows)


def _pin_policy(db: MigratedDatabase, sid: uuid.UUID, model: str) -> None:
    """Durable allocation-time identity pin — the ``generation_policy_pinned``
    event the consent path writes for real sessions (provider/model digest,
    builder, checker, inference params)."""
    with db.runtime.begin() as conn:
        conn.execute(
            insert(s.event).values(
                session_id=sid,
                seq=9000,
                event_id=uuid.uuid4(),
                stage="consent",
                actor="system",
                type="generation_policy_pinned",
                payload={
                    "build": "ab" * 32,
                    "model": model,
                    "builder": "v1",
                    "checker": "v1",
                    "validator": "v1",
                    "fallback": "v1",
                    "inference": {"temperature": 0},
                },
            )
        )


def test_cohort_pools_only_within_recorded_equivalence(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    """BA-104: pooled cohort analysis can never cross a compatibility boundary.

    Two sessions on the same manifest/policy identity across two member
    batches AND two release digests merge under the recorded
    ``same-materials-cross-build`` equivalence (batch_id + build_source_sha256
    waived when manifest/bundle/behavior/policy identity match); a third on a
    different manifest, a fourth with a different pinned model, and a fifth
    sharing everything but with a NULL behavior digest each form their own
    pools, and their values can never leak into the merged pool's cells or
    arm contrast."""
    s1 = _seed(
        migrated,
        store,
        "PP1",
        "live-pa",
        mode="live",
        arm="high",
        compat="ck1:" + "1" * 64,
        behavior="be" * 32,
    )
    s2 = _seed(
        migrated,
        store,
        "PP2",
        "live-pb",
        mode="live",
        arm="low",
        compat="ck1:" + "2" * 64,
        build="cd" * 32,
        behavior="be" * 32,
    )
    s3 = _seed(
        migrated,
        store,
        "PP3",
        "live-pc",
        mode="live",
        arm="high",
        compat="ck1:" + "3" * 64,
        manifest_id="tp_demo_v5",
        behavior="be" * 32,
    )
    s4 = _seed(
        migrated,
        store,
        "PP4",
        "live-pd",
        mode="live",
        arm="high",
        compat="ck1:" + "4" * 64,
        behavior="be" * 32,
    )
    # s5 carries a NULL behavior digest — the shape of a session pinned before
    # the scoped digest existed and never backfilled. ``create_session_rows``
    # stamps new rows with the digest, so the test writes NULL explicitly.
    s5 = _seed(
        migrated,
        store,
        "PP5",
        "live-pe",
        mode="live",
        arm="high",
        compat="ck1:" + "5" * 64,
        behavior=None,
    )
    for sid in (s1, s2, s3, s4, s5):
        _complete(migrated, sid)
    _pin_policy(migrated, s1, "gemma@sha256:" + "aa" * 32)
    _pin_policy(migrated, s2, "gemma@sha256:" + "aa" * 32)
    _pin_policy(migrated, s3, "gemma@sha256:" + "aa" * 32)
    _pin_policy(migrated, s4, "gemma@sha256:" + "bb" * 32)
    _pin_policy(migrated, s5, "gemma@sha256:" + "aa" * 32)
    _confidence(migrated, s1, crate=1, revision=1, status="answered", value=80)
    _confidence(migrated, s2, crate=1, revision=1, status="answered", value=40)
    _confidence(migrated, s3, crate=1, revision=1, status="answered", value=0)
    _confidence(migrated, s4, crate=1, revision=1, status="answered", value=0)
    _confidence(migrated, s5, crate=1, revision=1, status="answered", value=0)
    client = _client(make_settings, migrated)
    body = _analysis(client, "mode=live")
    ca = body["cohort_analysis"]
    assert ca["status"] == "descriptive_only"
    assert ca["n_sessions"] == 5
    assert len(ca["pools"]) == 4
    merged = next(p for p in ca["pools"] if len(p["compatibility_keys"]) == 2)
    assert merged["pool_kind"] == "equivalence"
    assert merged["equivalence"] == "same-materials-cross-build"
    assert merged["n_sessions"] == 2
    conf = next(m for m in merged["measures"] if m["measure"] == "confidence.mean")
    # s3's and s4's zeros can never contaminate the merged pool.
    assert conf["overall"]["mean"] == 60.0
    assert set(conf["contrasts"]) == {"arm_high_minus_low"}
    assert conf["contrasts"]["arm_high_minus_low"]["diff"] == 40.0
    # s5 shares manifest/policy but has no behavior digest — the scoped record
    # refuses it; it can only pool under the legacy same-build record, where it
    # is alone (no same-build mate). It must never reach the merged pool.
    s5_pool = next(p for p in ca["pools"] if "ck1:" + "5" * 64 in p["compatibility_keys"])
    assert s5_pool["n_sessions"] == 1
    assert s5_pool["pool_id"] != merged["pool_id"]
    assert s5_pool["equivalence"] != "same-materials-cross-build"
    for solo in (p for p in ca["pools"] if p["n_sessions"] == 1):
        solo_conf = next(m for m in solo["measures"] if m["measure"] == "confidence.mean")
        # One arm only → no contrast is fabricated.
        assert solo_conf["contrasts"] == {}
    # The mechanism contrast shape is gone entirely — no pool can cross
    # manifests, so ai-vs-scripted can never be computed here.
    assert all(
        "mech_ai_minus_scripted" not in m["contrasts"] for p in ca["pools"] for m in p["measures"]
    )
    # Power Inputs and Arm Contrasts group by the same pools: the merged
    # pool's two arms appear as rows on one shared pool partition label
    # (naming the recorded equivalence), not two ck1: fragments.
    conf_rows = [r for r in body["power_inputs"]["rows"] if r["measure"] == "confidence.mean"]
    merged_rows = [r for r in conf_rows if "same-materials-cross-build" in r["partition"]]
    assert {r["arm"] for r in merged_rows} == {"high", "low"}
    assert all(r["n_sessions"] == 1 for r in merged_rows)
    assert all(r["n_measured"] == 1 for r in merged_rows)
    merged_pid = merged_rows[0]["pool_id"]
    assert merged_pid.startswith("eq:")
    assert all(r["pool_id"] == merged_pid for r in merged_rows)
    # The solo pools keep their strict per-ck partition labels.
    solo_parts = {
        r["partition"] for r in conf_rows if "same-materials-cross-build" not in r["partition"]
    }
    assert len(solo_parts) == 3
    assert all(p.startswith("investigators_time_pressure | ck1:") for p in solo_parts)
    # Item cells pool by bound instrument@version — tp_demo_v3 and v5 both
    # bind post_task.tp_demo@v1, so all five sessions share the item row
    # (provenance still lists the contributing strict partitions).
    p1 = next(
        r
        for r in body["items"]
        if r["instrument_id"] == "post_task.tp_demo" and r["item_id"] == "tp_post_01"
    )
    assert p1["partition"] == "pooled"
    assert p1["sessions_expected"] == 5
    assert len(p1["partitions"]) == 5  # all five strict partitions


# ----- DA-11: confidence latest-revision selection -----------------------------------------------


def test_confidence_latest_revision_before_status(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    """The latest committed revision decides — a superseded ``answered`` can
    never resurrect after a later ``skipped`` revision (DA-11)."""
    sid = _seed(
        migrated,
        store,
        "C1",
        "live-conf",
        mode="live",
        arm="high",
        compat="ck1:" + "e" * 64,
    )
    _complete(migrated, sid)
    # crate 1: answered 50 at rev1, then skipped at rev2 → excluded entirely.
    _confidence(migrated, sid, 1, 1, "answered", 50)
    _confidence(migrated, sid, 1, 2, "skipped", None)
    # crate 2: answered 40 at rev1, answered 70 at rev2 → latest wins (70).
    _confidence(migrated, sid, 2, 1, "answered", 40)
    _confidence(migrated, sid, 2, 2, "answered", 70)
    client = _client(make_settings, migrated)
    body = _analysis(client, "mode=live&batch_id=live-conf")
    row = _power_row(body, "confidence.mean")
    assert row is not None
    assert row["n_measured"] == 1
    # Only crate 2's latest revision (70) contributes.
    assert row["mean"] == 70.0


# ----- DA-06/12: bound-item expectations ---------------------------------------------------------


def test_item_counts_against_bound_version(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    """Expected keys come from the bound artifact version — answered/skipped/
    missing/absent/invalid/unexpected are all distinct counts (DA-06/12)."""
    # tp_demo_v6 binds post_task.tp_demo v2 (10 items incl. the categorical
    # tp_post_10); tp_demo_v3 binds v1 (9 likert items, no tp_post_10).
    sid = _seed(
        migrated,
        store,
        "I1",
        "live-items-v6",
        mode="live",
        arm="high",
        compat="ck1:" + "f" * 64,
        manifest_id="tp_demo_v6",
    )
    legacy = _seed(
        migrated,
        store,
        "I2",
        "live-items-v3",
        mode="live",
        arm="high",
        compat="ck1:" + "0" * 64,
        manifest_id="tp_demo_v3",
    )
    _complete(migrated, sid)
    _complete(migrated, legacy)
    _item(migrated, sid, "tp_post_01", "answered", 5, version=2)
    _item(migrated, sid, "tp_post_02", "skipped", version=2)
    _item(migrated, sid, "tp_post_03", "missing", reason="no_response", version=2)
    _item(migrated, sid, "tp_post_04", "answered", 99, version=2)  # out of 1-7 → invalid
    _item(migrated, sid, "tp_post_10", "answered", "bogus option", version=2)
    _item(migrated, sid, "totally_unexpected", "answered", 3, version=2)
    # The v1-bound session rows tp_post_10 too — under ITS bound version the
    # item does not exist, so it counts as unexpected rather than invalid.
    _item(migrated, legacy, "tp_post_10", "answered", "All were human", version=1)
    client = _client(make_settings, migrated)
    body = _analysis(client, "mode=live&compatibility_key=ck1:" + "f" * 64)
    items = {r["item_id"]: r for r in body["items"] if r["instrument_id"] == "post_task.tp_demo"}

    p1 = items["tp_post_01"]
    assert p1["sessions_expected"] == 1
    assert p1["counts"]["answered"] == 1
    p4 = items["tp_post_04"]
    assert p4["counts"]["invalid"] == 1
    assert "numeric" not in p4  # invalid is never averaged into a distribution
    assert items["tp_post_10"]["counts"]["invalid"] == 1
    # tp_post_03 carries an explicit missing-status row; tp_post_09 was never
    # rowed at all → expected-but-absent. The two states stay distinct.
    assert items["tp_post_03"]["counts"]["missing"] == 1
    assert items["tp_post_09"]["counts"]["absent"] == 1
    assert items["totally_unexpected"]["counts"]["unexpected"] == 1

    # Bound-version fidelity (DA-12): under v1 the tp_post_10 row is unexpected
    # — v1 sessions are never "expected" to answer it, and the v2 expectation
    # set is not retro-applied across the compatibility boundary.
    body_v1 = _analysis(client, "mode=live&compatibility_key=ck1:" + "0" * 64)
    items_v1 = {
        r["item_id"]: r for r in body_v1["items"] if r["instrument_id"] == "post_task.tp_demo"
    }
    assert items_v1["tp_post_10"]["sessions_expected"] == 0
    assert items_v1["tp_post_10"]["counts"]["unexpected"] == 1


# ----- DA-13: labeled diagnostic cohort ----------------------------------------------------------


def test_diagnostics_separate_from_research(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    sid = _seed(migrated, store, "X1", "qa-diag", mode="qa", arm="high")
    _complete(migrated, sid)
    client = _client(make_settings, migrated)
    body = _analysis(client, "mode=qa")
    assert body["cohort"]["eligible_research"] == 0
    diag = body["diagnostics"]
    assert diag["n_sessions"] >= 1
    assert (
        "non-live" in str(diag.get("label", "")).lower()
        or "qa" in str(diag.get("label", "")).lower()
    )


# ----- DA-09: data-use gate before content -------------------------------------------------------


def test_withdrawn_session_content_unavailable(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    sid = _seed(migrated, store, "W1", "qa-withdraw", mode="qa")
    _complete(migrated, sid, data_use="withdraw_all")
    client = _client(make_settings, migrated)
    body = client.get(
        "/admin/dashboard/readiness?mode=qa&batch_id=qa-withdraw&audit_scope=all"
    ).json()
    rows = [r for r in body["audit"]["rows"] if r["session_id"] == str(sid)]
    assert rows, "withdrawn session must still appear in the audit listing"
    row = rows[0]
    assert row["data_use"] == "withdraw_all"
    assert row["item_coverage"].get("unavailable") == "data_use_withdraw_all"
    assert "data_use_withdraw_all" in row["flags"]


# ----- DA-18/19: timing censoring + arm split ----------------------------------------------------


def test_timing_arm_split_and_censored_budget(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    high = _seed(migrated, store, "TM1", "qa-time", mode="qa", arm="high")
    low = _seed(migrated, store, "TM2", "qa-time", mode="qa", arm="low")
    with migrated.runtime.begin() as conn:
        for sid in (high, low):
            # occurred_at defaults to statement_timestamp() — the transition
            # must post-date entry.enter's own rows so the open visit is
            # 'task', not an earlier stage.
            conn.execute(
                insert(s.stage_transition).values(
                    transition_id=uuid.uuid4(),
                    session_id=sid,
                    from_stage="entry",
                    to_stage="task",
                    revision=10,
                )
            )
            conn.execute(
                update(s.session).where(s.session.c.session_id == sid).values(stage="task")
            )
    client = _client(make_settings, migrated)
    body = client.get("/admin/dashboard/timing?mode=qa&batch_id=qa-time").json()
    wall = body["wall_durations"]
    arms = {r["arm"] for r in wall if r["stage"] == "task"}
    assert {"high", "low"} <= arms
    # Both sessions are mid-task → their task visits are open → censored.
    censored = {r["arm"]: r["censored"] for r in wall if r["stage"] == "task"}
    assert censored["high"] >= 1 and censored["low"] >= 1
    budget = {r["session_id"]: r for r in body["task_budget"]}
    for sid in (str(high), str(low)):
        row = budget[sid]
        assert row["censored"] is True
        assert row["over_budget"] is None  # undecidable while open


# ----- DA-20: readiness audit scopes -------------------------------------------------------------


def test_readiness_audit_scopes(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    completer = _seed(migrated, store, "R1", "qa-scope", mode="qa")
    _complete(migrated, completer)
    in_progress = _seed(migrated, store, "R2", "qa-scope", mode="qa")
    ended_bad = _seed(migrated, store, "R3", "qa-scope", mode="qa")
    _complete(migrated, ended_bad, outcome="technical_stop")
    with migrated.runtime.begin() as conn:
        conn.execute(
            update(s.session).where(s.session.c.session_id == ended_bad).values(stage="debrief")
        )
    client = _client(make_settings, migrated)
    url = "/admin/dashboard/readiness?mode=qa&batch_id=qa-scope&audit_scope="

    def ids(scope: str) -> set[str]:
        body = client.get(url + scope).json()
        return {r["session_id"] for r in body["audit"]["rows"]}

    assert ids("completers") == {str(completer)}
    inc = ids("incomplete")
    assert str(ended_bad) in inc  # ended with a non-completed outcome
    exc = ids("exceptional")
    assert str(completer) not in exc
    all_ids = ids("all")
    assert {str(completer), str(in_progress), str(ended_bad)} <= all_ids


# ----- DA-21: UTC date filters -------------------------------------------------------------------


def test_utc_date_filters(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    sid = _seed(migrated, store, "U1", "qa-utc", mode="qa")
    client = _client(make_settings, migrated)
    # A far-past `since` includes; a far-future `since` excludes — ISO strings
    # with an explicit Z are parsed as UTC (frontend normalizes to this shape).
    inc = client.get("/admin/dashboard/overview?mode=qa&since=2020-01-01T00:00:00Z").json()
    assert inc["cohort"]["selected"] >= 1
    exc = client.get("/admin/dashboard/overview?mode=qa&since=2099-01-01T00:00:00Z").json()
    assert all(
        r["session_id"] != str(sid)
        for r in client.get(
            "/admin/dashboard/readiness?mode=qa&audit_scope=all&since=2099-01-01T00:00:00Z"
        ).json()["audit"]["rows"]
    )
    assert exc["cohort"]["selected"] == 0 or all(
        r["session_id"] != str(sid) for r in exc["recent_sessions"]
    )


# ----- DA-10 + AG-02: scoped export POST + on-disk manifest --------------------------------------


def test_scoped_export_resolves_and_writes_manifest(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore, tmp_path: Any
) -> None:
    sid = _seed(migrated, store, "E1", "qa-export", mode="qa")
    _complete(migrated, sid)
    client = _client(make_settings, migrated, ADMIN_EXPORT_DIR=str(tmp_path))
    resp = client.post(
        "/admin/exports",
        json={"kind": "study", "scope": {"mode": "qa", "batch_id": "qa-export"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The response echoes the server-resolved scope — the normalized filter set
    # actually used, not merely the requested filters (DA-10).
    assert body.get("session_count") is not None and body["session_count"] >= 1
    resolved = body.get("resolved_scope") or {}
    assert resolved.get("mode") == "qa" and resolved.get("batch_id") == "qa-export"
    # AG-02: the manifest is persisted on disk — the catalog can verify it.
    import json

    manifests = list(pathlib.Path(tmp_path).glob("*/manifest.json"))
    assert manifests, "export must persist manifest.json"
    manifest = json.loads(manifests[0].read_text())
    assert manifest["kind"] == "research_study"
    assert manifest["schema"] == "investigators.export_manifest/1"


def test_empty_session_selection_is_not_unrestricted(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore, tmp_path: Any
) -> None:
    """``session_ids: []`` is an explicit empty selection — it must produce a
    zero-row export, never fall back to the full cohort (DA-10)."""
    sid = _seed(migrated, store, "E2", "qa-export2", mode="qa")
    _complete(migrated, sid)
    client = _client(make_settings, migrated, ADMIN_EXPORT_DIR=str(tmp_path))
    resp = client.post("/admin/exports", json={"kind": "study", "session_ids": []})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("session_count") == 0


def test_sharing_export_requires_session_ids(
    migrated: MigratedDatabase, make_settings: Any, tmp_path: Any
) -> None:
    client = _client(make_settings, migrated, ADMIN_EXPORT_DIR=str(tmp_path))
    resp = client.post("/admin/exports", json={"kind": "sharing"})
    assert resp.status_code == 422


# ----- per-table CSV downloads (researcher grid; same INV-1106 definitions) ----------------------


def _download(client: TestClient, name: str, **scope: Any) -> Any:
    return client.post("/admin/exports/table", json={"name": name, "scope": scope})


def test_export_table_csv_download_and_audit(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    sid = _seed(migrated, store, "T1", "qa-table", mode="qa")
    _complete(migrated, sid)
    client = _client(make_settings, migrated)
    resp = _download(client, "participant_wide", mode="qa", batch_id="qa-table")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["cache-control"] == "no-store"
    assert 'filename="investigators_participant_wide_' in resp.headers["content-disposition"]
    assert "session_id" in resp.text.splitlines()[0].split(",")
    assert str(sid) in resp.text
    assert resp.headers["x-export-sessions"] == "1"
    # The download is a data-egress event — audit pins table, scope, counts and
    # the checksum of the bytes that left (DC-03).
    with migrated.superuser.connect() as conn:
        row = (
            conn.execute(
                select(s.admin_audit)
                .where(s.admin_audit.c.action == "export_table")
                .order_by(s.admin_audit.c.occurred_at.desc())
                .limit(1)
            )
            .mappings()
            .one()
        )
        assert row["payload"]["table"] == "participant_wide"
        assert row["payload"]["n_sessions"] == 1
        assert row["payload"]["scope"]["batch_id"] == "qa-table"
        assert row["payload"]["sha256"] == hashlib.sha256(resp.content).hexdigest()


def test_export_table_mutation_gate_and_body_contract(
    migrated: MigratedDatabase, make_settings: Any
) -> None:
    client = _client(make_settings, migrated)
    # GET no longer serves the download (the route is a POST mutation).
    assert client.get("/admin/exports/table", params={"name": "session"}).status_code == 405
    # The mutation gate fires before auth/role: a forged request without the
    # CSRF double-submit learns nothing.
    forged = TestClient(client.app, raise_server_exceptions=False)
    assert (
        forged.post("/admin/exports/table", json={"name": "session", "scope": {}}).status_code
        == 403
    )
    # Unknown keys in the body or inside scope are rejected, never widened (DA-25).
    assert (
        client.post(
            "/admin/exports/table", json={"name": "session", "scope": {}, "extra": 1}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/admin/exports/table",
            json={"name": "session", "scope": {"batch": "typo-not-batch_id"}},
        ).status_code
        == 422
    )


def test_export_table_vocabulary_never_carries_restricted(
    migrated: MigratedDatabase, make_settings: Any
) -> None:
    client = _client(make_settings, migrated)
    resp = client.get("/admin/exports/tables")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    names = set(resp.json()["tables"])
    assert {"session", "message", "participant_wide", "crate_long"} <= names
    # Payment/compensation tables and identity linkage never enter the research
    # vocabulary; the semantic coding table needs a coding run, not a download.
    assert names.isdisjoint(
        {"payment_entitlement", "completion_receipt", "platform_link", "semantic_coding"}
    )
    for bad in ("payment_entitlement", "completion_receipt", "platform_link", "bogus"):
        refused = _download(client, bad, mode="all")
        assert refused.status_code == 422, bad
        assert refused.json()["error"] == "unknown_table"


def test_export_table_scope_contract(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    sid = _seed(migrated, store, "T2", "qa-tscope", mode="qa")
    _complete(migrated, sid)
    client = _client(make_settings, migrated)
    # A filter scope that resolves to nothing refuses rather than widening (DA-10).
    empty = _download(client, "session", batch_id="no-such-batch")
    assert empty.status_code == 422
    assert empty.json()["error"] == "empty_scope"
    # withdraw_all sessions leave the resolved scope — a batch holding only one
    # resolves to zero sessions and refuses rather than exporting them (DC-03:
    # the choice is re-read inside the download snapshot).
    _complete(migrated, _seed(migrated, store, "T3", "qa-twithdrawn"), data_use="withdraw_all")
    withdrawn = _download(client, "session", batch_id="qa-twithdrawn")
    assert withdrawn.status_code == 422
    assert withdrawn.json()["error"] == "empty_scope"
    # Role boundary: only the researcher downloads research tables.
    for role in ("operator", "compensation"):
        c = _client(make_settings, migrated, role=role)
        assert _download(c, "session").status_code == 403
        assert c.get("/admin/exports/tables").status_code == 403


def test_export_table_free_text_contract(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """DC-01: the download never emits unreviewed participant free text, and
    sessions without an explicit ``use_all`` drop off text-bearing tables."""
    batch = "qa-tfree"
    keep = _seed(migrated, store, "TF1", batch, mode="qa")
    no_text = _seed(migrated, store, "TF2", batch, mode="qa")
    no_choice = _seed(migrated, store, "TF3", batch, mode="qa")
    _complete(migrated, keep, data_use="use_all")
    _complete(migrated, no_text, data_use="use_no_free_text")
    _complete(migrated, no_choice, data_use=None)
    kept_mid = uuid.uuid4()
    dropped_mid = uuid.uuid4()
    with migrated.runtime.begin() as conn:
        for sid, mid in ((keep, kept_mid), (no_text, dropped_mid)):
            conn.execute(
                insert(s.message).values(
                    session_id=sid,
                    message_id=mid,
                    actor="participant",
                    channel="team",
                    body=f"participant prose {sid}",
                    accepted_seq=1,
                )
            )
    ev_id = uuid.uuid4()
    with migrated.runtime.begin() as conn:
        conn.execute(
            insert(s.event).values(
                session_id=keep,
                seq=900,
                event_id=ev_id,
                stage="task",
                actor="participant",
                type="telemetry_probe",
                payload={"note": "event payload prose"},
            )
        )
    client = _client(make_settings, migrated)
    resp = _download(client, "message", mode="qa", batch_id=batch)
    assert resp.status_code == 200, resp.text
    # use_no_free_text and missing-choice sessions are absent from the table…
    assert str(no_text) not in resp.text
    assert str(no_choice) not in resp.text
    assert str(keep) in resp.text
    # …and the remaining row echoes the body verbatim (2026-10-15 owner
    # direction: the download is where review happens).
    assert "participant prose" in resp.text
    assert "withheld_unreviewed" not in resp.text
    assert "text_status" not in resp.text
    # The dropped sessions still appear on exact-record tables.
    sess = _download(client, "session", mode="qa", batch_id=batch)
    assert str(no_text) in sess.text and str(no_choice) in sess.text
    # 2026-10-16 owner direction: formerly gated tables also emit verbatim —
    # no exportable file carries withheld/scrubbed markers; the consent-driven
    # session drop above is the only redaction that remains.
    ev = _download(client, "event", mode="qa", batch_id=batch)
    assert "event payload prose" in ev.text
    assert "withheld_unreviewed" not in ev.text
    assert "text_status" not in ev.text
    # seed_hex never leaves via the browser download (world reconstruction is
    # an archival/ops field).
    assert "seed_hex" not in sess.text.splitlines()[0].split(",")


def test_export_table_chat_text_verbatim(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """``chat_text`` (2026-10-12 owner direction) emits every session free-text
    artifact verbatim in one download — message bodies, model-call
    prompt/response, and event payloads — with no redaction gating, while the
    consent-driven non-``use_all`` session drop still applies."""
    batch = "qa-chat-text"
    keep = _seed(migrated, store, "CT1", batch, mode="qa")
    no_text = _seed(migrated, store, "CT2", batch, mode="qa")
    _complete(migrated, keep, data_use="use_all")
    _complete(migrated, no_text, data_use="use_no_free_text")
    snap = uuid.uuid4()
    with migrated.runtime.begin() as conn:
        for sid, body in ((keep, "chat body verbatim"), (no_text, "dropped body")):
            conn.execute(
                insert(s.message).values(
                    session_id=sid,
                    message_id=uuid.uuid4(),
                    actor="participant",
                    channel="team",
                    body=body,
                    accepted_seq=1,
                )
            )
        conn.execute(
            insert(s.knowledge_snapshot).values(
                session_id=keep,
                snapshot_id=snap,
                seat="assistant",
                purpose="assistant_context",
                visible_event_seq=1,
                record_manifest={},
                created_event_seq=1,
            )
        )
        conn.execute(
            insert(s.model_call).values(
                session_id=keep,
                call_id=uuid.uuid4(),
                snapshot_id=snap,
                provider="p",
                model="m",
                prompt_sha256="a" * 64,
                input={"system": "prompt text verbatim"},
                raw_response="model response verbatim",
                status="succeeded",
            )
        )
        conn.execute(
            insert(s.event).values(
                session_id=keep,
                seq=500,
                event_id=uuid.uuid4(),
                stage="task",
                actor="participant",
                type="chat_sent",
                payload={"note": "event payload verbatim"},
            )
        )
    client = _client(make_settings, migrated)
    resp = _download(client, "chat_text", mode="qa", batch_id=batch)
    assert resp.status_code == 200, resp.text
    header = resp.text.splitlines()[0].split(",")
    for col in ("session_id", "occurred_at", "source", "actor", "kind", "ref_id", "text"):
        assert col in header
    assert "chat body verbatim" in resp.text
    assert "prompt text verbatim" in resp.text  # model_call input JSONB
    assert "model response verbatim" in resp.text
    assert "event payload verbatim" in resp.text
    assert "withheld" not in resp.text
    # the non-use_all session contributes nothing, not even its message row.
    assert str(keep) in resp.text and str(no_text) not in resp.text
    assert "dropped body" not in resp.text


def test_comprehension_scalars_and_gate_flag(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """The 2026-10-10 comprehension gate persists answers as training_step
    events, never item_response — analytic surfaces are the
    ``training.comprehension_*`` scalars, and readiness flags only the
    provable bypass (attempted + advanced + never passed)."""
    passed = _seed(migrated, store, "TC1", "qa-tcomp", mode="live")
    legacy = _seed(migrated, store, "TC2", "qa-tcomp", mode="live")
    bypass = _seed(migrated, store, "TC3", "qa-tcomp", mode="live", compat="ck1:" + "9" * 64)
    for sid in (passed, legacy, bypass):
        _complete(migrated, sid, data_use="use_all")
    with migrated.runtime.begin() as conn:
        for sid in (passed, legacy, bypass):
            conn.execute(
                insert(s.stage_transition).values(
                    transition_id=uuid.uuid4(),
                    session_id=sid,
                    from_stage="training",
                    to_stage="scripted_connecting",
                    revision=10,
                )
            )
        # passed: first attempt answered every item but missed two (True on
        # same_crates/assistant_reads_team where the key is False), passed on
        # the second — retries never rewrite the first-attempt analytic rows.
        first_answers = {
            "partial_intel": True,
            "same_crates": True,
            "accurate_not_latest": True,
            "teammates_may_have_mine": True,
            "record_before_timer": True,
            "assistant_reads_team": True,
        }
        passes_answers = dict(first_answers, same_crates=False, assistant_reads_team=False)
        for seq, answers, missed in (
            (100, first_answers, ["same_crates", "assistant_reads_team"]),
            (101, passes_answers, []),
        ):
            conn.execute(
                insert(s.event).values(
                    session_id=passed,
                    seq=seq,
                    event_id=uuid.uuid4(),
                    stage="training",
                    actor="participant",
                    type="training_step",
                    payload={
                        "step": "comprehension",
                        "answers": answers,
                        "missed": missed,
                    },
                )
            )
        # bypass: attempted once, never passed, yet advanced — the violation.
        conn.execute(
            insert(s.event).values(
                session_id=bypass,
                seq=100,
                event_id=uuid.uuid4(),
                stage="training",
                actor="participant",
                type="training_step",
                payload={
                    "step": "comprehension",
                    "answers": {
                        "partial_intel": False,
                        "same_crates": False,
                        "accurate_not_latest": True,
                        "teammates_may_have_mine": True,
                        "record_before_timer": True,
                        "assistant_reads_team": False,
                    },
                    "missed": ["partial_intel"],
                },
            )
        )
    client = _client(make_settings, migrated)
    body = _analysis(client, "mode=live&batch_id=qa-tcomp")

    def prow(measure: str, partition: str = "unkeyed:tp_demo_v3") -> dict[str, Any] | None:
        for r in body["power_inputs"]["rows"]:
            if r["measure"] == measure and partition in r["partition"]:
                return r
        return None

    # passed+legacy share the tp_demo_v3 partition: attempts pools [2, 0].
    row = prow("training.comprehension_attempts")
    assert row is not None and row["n_measured"] == 2 and row["mean"] == 1.0
    # First-attempt measures are missing (not zero) for the never-attempted
    # legacy session — the pre-gate cohort reads as missing, never as pass.
    row = prow("training.comprehension_first_misses")
    assert row["n_measured"] == 1 and row["missing"] == 1 and row["mean"] == 2.0
    row = prow("training.comprehension_passed_first_try")
    assert row["n_measured"] == 1 and row["mean"] == 0.0
    assert prow("training.comprehension_miss_same_crates")["mean"] == 1.0
    assert prow("training.comprehension_miss_partial_intel")["mean"] == 0.0
    # bypass sits in its own partition — its single miss shows per-item.
    row = prow("training.comprehension_miss_partial_intel", "ck1:" + "9" * 64)
    assert row is not None and row["n_measured"] == 1 and row["mean"] == 1.0

    # Item Analytics: the five gate items reconcile as true/false choice cells
    # from FIRST-attempt answers — cells pool by instrument@version across
    # partitions, so passed (unkeyed) and bypass (ck1:999…) share each cell
    # while legacy owes nothing (never attempted ⇒ not expected).
    comp = {r["item_id"]: r for r in body["items"] if r["instrument_id"] == "comprehension_check"}
    for iid in (
        "partial_intel",
        "same_crates",
        "accurate_not_latest",
        "teammates_may_have_mine",
        "assistant_reads_team",
    ):
        assert comp[iid]["sessions_expected"] == 2  # passed + bypass
        assert comp[iid]["counts"]["answered"] == 2
        assert comp[iid]["partition"] == "pooled"
        assert len(comp[iid]["partitions"]) == 2
    assert comp["same_crates"]["categories"] == {"true": 1, "false": 1}
    assert comp["partial_intel"]["categories"] == {"true": 1, "false": 1}
    assert comp["assistant_reads_team"]["categories"] == {"true": 1, "false": 1}
    # Derived per-option binary rows give the first-attempt share — pooled
    # across the two contributors (passed true, bypass false) — and they
    # register as scalar measures, so Power/Contrasts still carry them per pool.
    assert comp["same_crates@true"]["numeric"]["mean"] == 0.5
    assert comp["same_crates@false"]["numeric"]["mean"] == 0.5
    assert prow("item.comprehension_check.same_crates@true")["mean"] == 1.0

    audit = client.get(
        "/admin/dashboard/readiness?mode=live&batch_id=qa-tcomp&audit_scope=all"
    ).json()
    flags = {r["session_id"]: set(r["flags"]) for r in audit["audit"]["rows"]}
    assert "comprehension_gate_bypassed" in flags[str(bypass)]
    # The pass and the pre-gate legacy session never flag.
    assert "comprehension_gate_bypassed" not in flags[str(passed)]
    assert "comprehension_gate_bypassed" not in flags[str(legacy)]


def test_export_table_item_response_open_ended_row_scope(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """UX round 8 + 2026-10-12 owner direction: ``item_response.response`` is
    participant prose only for the code-defined open_ended instrument, and
    post-task items are exempt from the redaction gate — open_ended text
    exports verbatim. Only the consent-driven drop remains at row scope
    (row dropped without ``use_all``); exact-record item rows stay verbatim
    on the same download."""
    batch = "qa-toe"
    keep = _seed(migrated, store, "TOE1", batch, mode="qa")
    no_text = _seed(migrated, store, "TOE2", batch, mode="qa")
    _complete(migrated, keep, data_use="use_all")
    _complete(migrated, no_text, data_use="use_no_free_text")
    _item(
        migrated,
        keep,
        "crate_strategy",
        "answered",
        "participant oe prose",
        instrument="open_ended_followup",
        version=1,
    )
    _item(
        migrated,
        keep,
        "source_reliance",
        "answered",
        "second oe prose",
        instrument="open_ended.tp_followup",
        version=1,
    )
    _item(migrated, keep, "tp_post_01", "answered", 6)
    _item(
        migrated,
        no_text,
        "crate_strategy",
        "answered",
        "more oe prose",
        instrument="open_ended_followup",
        version=1,
        reason=None,
    )
    _item(migrated, no_text, "tp_post_01", "answered", 4)

    client = _client(make_settings, migrated)
    resp = _download(client, "item_response", mode="qa", batch_id=batch)
    assert resp.status_code == 200, resp.text
    # Open_ended text is exempt from the redaction gate — it echoes verbatim,
    # never a withheld/scrubbed marker (2026-10-12 owner direction).
    assert "participant oe prose" in resp.text
    assert "more oe prose" not in resp.text  # non-use_all row dropped, not withheld
    assert "withheld_unreviewed" not in resp.text
    assert "text_status" not in resp.text
    # The non-use_all session loses its open_ended row entirely…
    oe_rows = [ln for ln in resp.text.splitlines() if "open_ended" in ln]
    assert oe_rows and all(str(keep) in ln for ln in oe_rows)
    # …but keeps its exact-record item rows on the same table.
    assert str(no_text) in resp.text
    # Structured answers stay verbatim — the response column is not blanked
    # for exact-record rows.
    assert ",6," in resp.text or resp.text.count("6") >= 1
    # The follow-up pair also lands in named per-session columns (2026-10-15):
    # every open_ended row carries BOTH of the session's answers —
    # crate_strategy → response1, source_reliance → response2.
    header = resp.text.splitlines()[0].split(",")
    assert "response1" in header and "response2" in header
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    by_item = {r["item_id"]: r for r in rows}
    for item in ("crate_strategy", "source_reliance"):
        assert by_item[item]["response1"] == "participant oe prose"
        assert by_item[item]["response2"] == "second oe prose"
    # Non-open_ended rows stay null on both columns (no prose leak).
    assert by_item["tp_post_01"]["response1"] == ""
    assert by_item["tp_post_01"]["response2"] == ""
    # Every row carries the item wording as asked — post_task from the bound
    # artifact version, legacy open_ended from the frozen code items.
    assert "question" in header
    # Wording lands immediately after ``response`` so a value like
    # {"seat": null, "value": 5} reads next to the question it answers.
    assert header.index("question") == header.index("response") + 1
    assert (
        by_item["tp_post_01"]["question"]
        == "I had the information I needed to make good decisions."
    )
    assert (
        by_item["crate_strategy"]["question"]
        == "There was no single right way to approach this task, and people "
        "worked through it very differently. Briefly, how did you decide on "
        "a location for each of your crates?"
    )
    # source_reliance wording follows the assistant flag — default settings
    # have AI_ASSISTANT_ENABLED, so the three-source phrasing is exported.
    assert (
        by_item["source_reliance"]["question"]
        == "When you weren't sure about a crate, how did you decide which "
        "source to rely on: your case file, the team chat, or the AI "
        "assistant?"
    )


def test_export_table_formula_safety(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    sid = _seed(migrated, store, "T4", "qa-tcsv", mode="qa")
    _complete(migrated, sid)
    with migrated.runtime.begin() as conn:
        conn.execute(
            insert(s.consent_event).values(
                consent_id=uuid.uuid4(),
                session_id=sid,
                text_version="=cmd|'/c calc'!A1",
                action="consented",
            )
        )
    client = _client(make_settings, migrated)
    resp = _download(client, "consent_event", mode="qa", batch_id="qa-tcsv")
    assert resp.status_code == 200, resp.text
    assert "'=cmd" in resp.text  # quote-prefixed — never a live formula


def test_export_table_matches_published_export(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any, tmp_path: Any
) -> None:
    """Exact-record downloads are byte-identical to the file the published export
    writes — the download shares INV-1106 definitions and must not drift."""
    sid = _seed(migrated, store, "T5", "qa-tparity", mode="qa")
    _complete(migrated, sid)
    client = _client(make_settings, migrated, ADMIN_EXPORT_DIR=str(tmp_path))
    pub = client.post(
        "/admin/exports",
        json={"kind": "study", "scope": {"mode": "qa", "batch_id": "qa-tparity"}},
    )
    assert pub.status_code == 200, pub.text
    written = next(tmp_path.glob("*/crate_long.csv")).read_bytes()
    dl = _download(client, "crate_long", mode="qa", batch_id="qa-tparity")
    assert dl.status_code == 200, dl.text
    assert dl.content == written


# ----- DA-15/16: instrument serialization + restricted kinds -------------------------------------


def test_instruments_serialize_by_kind(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    sid = _seed(migrated, store, "N1", "qa-inst", mode="qa")
    _complete(migrated, sid)
    client = _client(make_settings, migrated)
    body = client.get("/admin/dashboard/instruments?mode=qa&batch_id=qa-inst").json()
    artifacts = body["artifacts"]
    kinds = {a["slot"]: a for a in artifacts}
    inst = kinds.get("instruments.post_task")
    assert inst is not None, f"bound instrument slot missing: {sorted(kinds)}"
    b = inst["body"]
    assert b.get("kind") == "instrument"
    assert b["items"], "instrument body must carry verbatim items"
    # Any bound prompt/payment/overlay artifact is provenance-only (DA-16).
    for a in artifacts:
        ab = a["body"]
        if ab.get("restricted") is True:
            assert "prompt" not in ab or not isinstance(ab.get("prompt"), str)
            assert not ab.get("items"), "restricted bodies must not carry items"


# ----- AG-04: REPEATABLE READ snapshot proof -----------------------------------------------------


def test_snapshot_isolation_repeatable_read(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """A REPEATABLE READ snapshot sees a stable cohort even when rows are
    committed mid-transaction by another connection (AG-04)."""
    from app.services import dashboard

    _seed(migrated, store, "S1", "qa-snap", mode="qa")
    with migrated.runtime.connect() as conn:
        conn.execute(
            __import__("sqlalchemy").text("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        )
        # First read inside the snapshot.
        flt = dashboard.parse_filters({"mode": "qa", "batch_id": "qa-snap"})
        before = len(dashboard.select_cohort(conn, flt))
        # A concurrent committed insert must not appear in this snapshot.
        with migrated.superuser.begin() as w:
            w.execute(
                insert(s.session).values(
                    session_id=uuid.uuid4(),
                    participant_id=_new_participant(w),
                    batch_id="qa-snap",
                    manifest_id="tp_demo_v3",
                    manifest_sha256="0" * 64,
                    bundle_sha256="0" * 64,
                    binding_sha256="0" * 64,
                    binding={"artifacts": {}},
                    study_key="tp",
                    mode="qa",
                    timing_mode="faithful",
                    seed_hex="ab" * 16,
                    build_source_sha256="ab" * 32,
                    stage="entry",
                )
            )
        after = len(dashboard.select_cohort(conn, flt))
        conn.rollback()
    assert before == after


def _new_participant(conn: Any) -> uuid.UUID:
    pid = uuid.uuid4()
    conn.execute(
        insert(s.participant).values(
            participant_id=pid,
            research_pid=f"SNAP-{pid.hex[:8]}",
        )
    )
    return pid


# ----- 1,000-session benchmark (INV-1712 evidence) ------------------------------------------------


def test_dashboard_1000_session_benchmark(
    migrated: MigratedDatabase, make_settings: Any, store: ContentStore
) -> None:
    """Performance/size/completeness benchmark at N=1000 eligible live
    sessions (audit §6.2): every dashboard endpoint must return complete
    aggregates — no silent row caps — inside a generous latency bound. Timings
    and payload sizes are printed for the evidence log."""
    import time

    bound = bind(store, "tp_demo_v6", mode="qa")
    with migrated.superuser.begin() as conn:
        register_manifest(conn, bound)
        conn.execute(
            pg_insert(s.batch)
            .values(
                batch_id="bench-bulk",
                study_key=bound.manifest.study_key,
                manifest_id=bound.manifest_id,
                mode="qa",
                enrollment_open=True,
            )
            .on_conflict_do_nothing()
        )

    N = 1000
    binding_rec = bound.binding_record()
    participants = []
    sessions = []
    receipts = []
    results = []
    choices = []
    consents = []
    items = []
    transitions = []
    for i in range(N):
        pid = uuid.uuid5(uuid.NAMESPACE_URL, f"bench/{i}/p")
        sid = uuid.uuid5(uuid.NAMESPACE_URL, f"bench/{i}/s")
        participants.append({"participant_id": pid, "research_pid": f"BENCH-{i:05d}"})
        sessions.append(
            {
                "session_id": sid,
                "participant_id": pid,
                "batch_id": "bench-bulk",
                "manifest_id": "tp_demo_v6",
                "manifest_sha256": bound.manifest_sha256,
                "bundle_sha256": bound.bundle_sha256,
                "binding_sha256": bound.binding_sha256,
                "binding": binding_rec,
                "study_key": bound.manifest.study_key,
                "mode": "live",
                "timing_mode": "faithful",
                "arm": "high" if i % 2 == 0 else "low",
                "seed_hex": f"{i:064x}"[-64:],
                "build_source_sha256": "ab" * 32,
                "compatibility_key": "ck1:" + ("a" if i % 2 == 0 else "b") * 64,
                "stage": "completion",
            }
        )
        consents.append(
            {
                "consent_id": uuid.uuid5(uuid.NAMESPACE_URL, f"bench/{i}/c"),
                "session_id": sid,
                "text_version": "v3",
                "action": "consented",
            }
        )
        receipts.append(
            {
                "session_id": sid,
                "outcome": "completed",
                "reconciled": {},
                "final_event_seq": 100,
            }
        )
        results.append(
            {
                "session_id": sid,
                "scoring_version": SCORING_VERSION,
                "correct_total": (i % 3) + (i % 2),  # ck_result_correct_sum
                "correct_teamdep": i % 3,
                "correct_ownfile": i % 2,
                "status": "scored",
                "finalized_seq": 100,
            }
        )
        choices.append(
            {"session_id": sid, "choice_seq": 1, "choice": "use_all", "recorded_seq": 99}
        )
        transitions.append(
            {
                "transition_id": uuid.uuid5(uuid.NAMESPACE_URL, f"bench/{i}/t1"),
                "session_id": sid,
                "from_stage": "entry",
                "to_stage": "task",
                "revision": 1,
            }
        )
        transitions.append(
            {
                "transition_id": uuid.uuid5(uuid.NAMESPACE_URL, f"bench/{i}/t2"),
                "session_id": sid,
                "from_stage": "task",
                "to_stage": "completion",
                "revision": 2,
            }
        )
        if i % 2 == 0:  # half the cohort rows full post_task v2 item data
            for n in range(1, 10):
                items.append(
                    {
                        "session_id": sid,
                        "instrument_id": "post_task.tp_demo",
                        "item_id": f"tp_post_{n:02d}",
                        "instrument_version": 2,
                        "response": {"value": (i + n) % 7 + 1},
                        "status": "answered",
                        "revision_count": 1,
                        "first_seq": 1,
                        "last_seq": 1,
                    }
                )

    with migrated.superuser.begin() as conn:
        conn.execute(insert(s.participant), participants)
        conn.execute(insert(s.session), sessions)
        conn.execute(insert(s.consent_event), consents)
        conn.execute(insert(s.completion_receipt), receipts)
        conn.execute(insert(s.result), results)
        conn.execute(insert(s.data_use_choice), choices)
        conn.execute(insert(s.stage_transition), transitions)
        conn.execute(insert(s.item_response), items)

    client = _client(make_settings, migrated)
    report: list[str] = []
    for path in (
        "/admin/dashboard/overview?mode=live&batch_id=bench-bulk",
        "/admin/dashboard/readiness?mode=live&batch_id=bench-bulk&audit_scope=all&page_size=200",
        "/admin/dashboard/timing?mode=live&batch_id=bench-bulk",
        "/admin/dashboard/instruments?mode=live&batch_id=bench-bulk",
        "/admin/dashboard/analysis?mode=live&batch_id=bench-bulk",
    ):
        t0 = time.perf_counter()
        resp = client.get(path)
        ms = (time.perf_counter() - t0) * 1000
        assert resp.status_code == 200, f"{path}: {resp.text[:300]}"
        report.append(
            f"{path.split('?')[0].rsplit('/', 1)[-1]:>12} {ms:8.0f} ms {len(resp.content):>9} B"
        )
        assert ms < 30_000, f"{path} took {ms:.0f} ms"

    body = resp.json()  # analysis
    assert body["cohort"]["eligible_research"] == N
    # DA-08 completeness: all 1000 expected-item slots counted — the sessions
    # bind the identical post_task artifact version, so the item cell pools
    # both compat partitions into one row with full provenance.
    p1s = [r for r in body["items"] if r["item_id"] == "tp_post_01"]
    assert sum(r["sessions_expected"] for r in p1s) == N
    assert len(p1s) == 1  # identical instrument@version pools across partitions
    assert p1s[0]["partition"] == "pooled"
    assert len(p1s[0]["partitions"]) == 2
    # Audit pagination: the whole cohort is addressable, total reflects scope.
    ready = client.get(
        "/admin/dashboard/readiness?mode=live&batch_id=bench-bulk&audit_scope=all&page_size=100"
    ).json()
    assert ready["audit"]["total"] == N
    assert len(ready["audit"]["rows"]) == 100
    print("\n=== dashboard N=1000 benchmark ===")
    print("\n".join(report))


def test_dashboard_batch_group_version_filter(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """``batch_group_id`` scopes the shared cohort contract to one fielding
    round (the version dropdown): member-batch sessions count, sessions that
    entered through a plain batch or another group do not."""
    from app.services import adminops, dashboard

    group, ma, mb = "qa.grpflt", "qa.grpflt.a", "qa.grpflt.b"
    for bid in (ma, mb):
        adminops.create_batch(
            migrated.runtime, store, actor="t", batch_id=bid, manifest_id="tp_demo_v3", mode="qa"
        )
    adminops.create_batch_group(
        migrated.runtime, store, actor="t", group_id=group, batch_ids=[ma, mb], mode="qa"
    )
    adminops.set_group_enrollment(migrated.runtime, group, open_=True, actor="t", reason="t")
    inside = {
        str(
            entry.enter(
                migrated.runtime,
                store,
                query={
                    "PROLIFIC_PID": f"GF{i}",
                    "STUDY_ID": "s",
                    "SESSION_ID": "x",
                    "batch": group,
                },
                app_mode="qa",
                preview_key=None,
                current_manifest_sha256={},
                build_source_sha256="ab" * 32,
            ).session_id
        )
        for i in range(2)
    }
    _seed(migrated, store, "GF9", "qa.grpflt.out")

    with migrated.runtime.connect() as conn:
        grouped = {
            str(r["session_id"])
            for r in dashboard.select_cohort(
                conn, dashboard.DashboardFilter(mode="qa", batch_group_id=group)
            )
        }
        assert grouped == inside
        assert (
            dashboard.select_cohort(
                conn, dashboard.DashboardFilter(mode="qa", batch_group_id="qa.nosuch")
            )
            == []
        )
        allqa = dashboard.select_cohort(conn, dashboard.DashboardFilter(mode="qa"))
        assert len(allqa) == 3

    client = _client(make_settings, migrated)
    body = _analysis(client, f"mode=qa&batch_group_id={group}")
    assert body["filters"]["batch_group_id"] == group
    assert body["cohort"]["selected"] == 2
