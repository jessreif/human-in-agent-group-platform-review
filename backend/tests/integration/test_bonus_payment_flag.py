"""BONUS_PAYMENT_ENABLED — disposable-DB acceptance runs.

Removal flag (artifact-substitution archetype): manifests keep pinning the
bonus-on contract (``consent.reference`` + ``payment.s5_base_only``); under the
flag-off deployment the consent stage serves/accepts the
``consent.reference.nobonus`` artifact and every entitlement computes under the
``payment.s5_nobonus`` policy ($3.00 flat — the $0.50 bonus is absent, not
merely unpaid). What the session records always names what it was served/paid:
``consent_event.text_version`` and ``payment_entitlement.policy_artifact_id``
carry the alternate artifact keys under the flag-off deployment.

- Flag on (default): unchanged — consent v9 key, 350-cent entitlement under
  ``payment.s5_base_only@v5``.
- Flag off: consent accepts only the nobonus key; the bonus-on key is refused
  with ``consent_version_mismatch``; completing records the nobonus policy and
  a 300-cent pending base.
- Flag off at the group boundary: consent allocates + rebinds to the member
  cell — the uniform-consent check compares bound pins, which the flag does
  not change.
- Wire: ``GET /workbench`` serves the nobonus material projection and the
  neutral ``bonus_payment`` capability flag; ``session_opened`` records the
  flag in ``feature_flags``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.db import schema as s
from app.db.tx import execute_command
from app.domain.manifests.binding import bind
from app.domain.manifests.store import ContentStore
from app.flags import FeatureFlags
from app.services import adminops, lifecycle
from app.services.entry import enter
from harness.personas import NormalPersona
from harness.runner import Runner
from tests.integration.test_wp08_workbench_api import (
    BUILD_SHA,
    _authed_client,
    _cmd,
    _query,
    _stage,
)

from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration

MANIFEST = "tp_ai_ovnone_v3"
NOBONUS_CONSENT = "consent.reference.nobonus@v1"
NOBONUS_POLICY = "payment.s5_nobonus"
BONUS_CONSENT_V9 = "consent.reference@v9"
BONUS_POLICY = "payment.s5_base_only"
MODEL_DIGEST = "gemma4:26b@sha256:" + "cd" * 32


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _seed_live(db: MigratedDatabase, store: ContentStore, batch: str, manifest: str) -> None:
    bound = bind(store, manifest, mode="live")
    from app.services.sessions import register_manifest

    with db.superuser.begin() as conn:
        register_manifest(conn, bound)
        conn.execute(
            s.batch.insert().values(
                batch_id=batch,
                study_key=bound.manifest.study_key,
                manifest_id=bound.manifest_id,
                mode="live",
                enrollment_open=True,
            )
        )


def _live_enter(db: MigratedDatabase, store: ContentStore, pid: str, batch: str, manifest: str):
    _seed_live(db, store, batch, manifest)
    return enter(
        db.runtime,
        store,
        query=_query(pid, batch),
        app_mode="live",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )


def _run(
    db: MigratedDatabase,
    store: ContentStore,
    sid: uuid.UUID,
    writer: uuid.UUID,
    kind: str,
    payload: dict[str, Any],
    revision: int,
    *,
    bonus_payment: bool = True,
    accept: bool = True,
) -> Any:
    out = execute_command(
        db.runtime,
        _cmd(sid, kind, payload, revision),
        lifecycle.handler_for(
            kind,
            store=store,
            behavior_sha=BUILD_SHA,
            writer=writer,
            provider_model_digest=MODEL_DIGEST,
            flags=FeatureFlags(
                crate_overlap=True,
                crate_number=True,
                time_pressure=False,
                bonus_payment=bonus_payment,
            ),
        ),
    )
    if accept:
        assert out.accepted, (kind, out.reason)
    return out


def _consent(
    db: MigratedDatabase,
    store: ContentStore,
    result: Any,
    text_version: str,
    *,
    bonus_payment: bool,
    accept: bool = True,
) -> Any:
    return _run(
        db,
        store,
        result.session_id,
        result.writer_id,
        "consent",
        {"action": "consented", "text_version": text_version},
        1,
        bonus_payment=bonus_payment,
        accept=accept,
    )


def _entitlement_row(db: MigratedDatabase, sid: uuid.UUID) -> dict[str, Any] | None:
    with db.runtime.connect() as conn:
        row = (
            conn.execute(
                select(s.payment_entitlement).where(s.payment_entitlement.c.session_id == sid)
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row is not None else None


def _consent_row(db: MigratedDatabase, sid: uuid.UUID) -> dict[str, Any] | None:
    with db.runtime.connect() as conn:
        row = (
            conn.execute(select(s.consent_event).where(s.consent_event.c.session_id == sid))
            .mappings()
            .one_or_none()
        )
        return dict(row) if row is not None else None


def _drive_to_completion(
    db: MigratedDatabase,
    store: ContentStore,
    sid: uuid.UUID,
    writer: uuid.UUID,
    *,
    bonus_payment: bool,
) -> Any:
    """Consent already recorded — jump the stage spine and complete honestly."""
    from sqlalchemy import update

    with db.runtime.begin() as conn:
        conn.execute(update(s.session).where(s.session.c.session_id == sid).values(stage="debrief"))
    _run(db, store, sid, writer, "debrief_ack", {}, _stage(db, sid)[1], bonus_payment=bonus_payment)
    _run(
        db,
        store,
        sid,
        writer,
        "data_use_choice",
        {"choice": "use_all"},
        _stage(db, sid)[1],
        bonus_payment=bonus_payment,
    )
    return execute_command(
        db.runtime,
        _cmd(sid, "complete", {}, _stage(db, sid)[1]),
        lifecycle.completion_handler(
            store=store, writer=writer, bonus_payment_enabled=bonus_payment
        ),
    )


# ----- flag on: status quo -----------------------------------------------------


def test_flag_on_consent_and_entitlement_unchanged(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    batch = f"bonus.on.{uuid.uuid4().hex[:8]}"
    result = _live_enter(migrated, store, "BP-ON-1", batch, MANIFEST)
    sid, writer = result.session_id, result.writer_id
    _consent(migrated, store, result, BONUS_CONSENT_V9, bonus_payment=True)
    assert _consent_row(migrated, sid)["text_version"] == BONUS_CONSENT_V9

    out = _drive_to_completion(migrated, store, sid, writer, bonus_payment=True)
    assert out.accepted, out.reason
    ent = _entitlement_row(migrated, sid)
    assert ent is not None
    assert ent["status"] == "pending" and ent["cents"] == 350
    assert ent["policy_artifact_id"] == BONUS_POLICY and ent["policy_version"] == 5


# ----- flag off: consent + entitlement ------------------------------------------


def test_flag_off_consent_accepts_nobonus_key(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """Under the flag-off deployment the served consent is the nobonus artifact —
    its key is the only valid text_version and it is what the session records."""
    batch = f"bonus.off.{uuid.uuid4().hex[:8]}"
    result = _live_enter(migrated, store, "BP-OFF-1", batch, MANIFEST)
    _consent(migrated, store, result, NOBONUS_CONSENT, bonus_payment=False)
    row = _consent_row(migrated, result.session_id)
    assert row is not None and row["text_version"] == NOBONUS_CONSENT
    assert row["action"] == "consented"


def test_flag_off_consent_rejects_bonus_on_key(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """A flag-off deployment never accepts consent against copy that still
    offers the bonus — the served artifact is the only valid text_version."""
    batch = f"bonus.offrej.{uuid.uuid4().hex[:8]}"
    result = _live_enter(migrated, store, "BP-OFF-2", batch, MANIFEST)
    out = _consent(migrated, store, result, BONUS_CONSENT_V9, bonus_payment=False, accept=False)
    assert not out.accepted and out.reason == "consent_version_mismatch"


def test_flag_on_consent_rejects_nobonus_key(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """Symmetric guard: a flag-on deployment refuses the nobonus consent key —
    participants must consent against the copy they were actually served."""
    batch = f"bonus.onrej.{uuid.uuid4().hex[:8]}"
    result = _live_enter(migrated, store, "BP-ON-2", batch, MANIFEST)
    out = _consent(migrated, store, result, NOBONUS_CONSENT, bonus_payment=True, accept=False)
    assert not out.accepted and out.reason == "consent_version_mismatch"


def test_flag_off_completion_entitles_300_under_nobonus_policy(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """The ledger names the policy that computed the payment: flag-off
    completers owe $3.00 flat under payment.s5_nobonus, never the $3.50 the
    bonus-on policy folds in."""
    batch = f"bonus.offpay.{uuid.uuid4().hex[:8]}"
    result = _live_enter(migrated, store, "BP-OFF-3", batch, MANIFEST)
    sid, writer = result.session_id, result.writer_id
    _consent(migrated, store, result, NOBONUS_CONSENT, bonus_payment=False)

    out = _drive_to_completion(migrated, store, sid, writer, bonus_payment=False)
    assert out.accepted, out.reason
    ent = _entitlement_row(migrated, sid)
    assert ent is not None
    assert ent["status"] == "pending" and ent["cents"] == 300
    assert ent["policy_artifact_id"] == NOBONUS_POLICY and ent["policy_version"] == 1


def test_flag_off_declined_records_nobonus_policy_ineligible(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """Declining at consent still records which policy stood behind the (zero)
    entitlement — the ledger is honest even when nothing is owed."""
    batch = f"bonus.offdec.{uuid.uuid4().hex[:8]}"
    result = _live_enter(migrated, store, "BP-OFF-4", batch, MANIFEST)
    out = _run(
        migrated,
        store,
        result.session_id,
        result.writer_id,
        "consent",
        {"action": "declined", "text_version": NOBONUS_CONSENT},
        1,
        bonus_payment=False,
    )
    assert out.result.get("outcome") == "declined"
    ent = _entitlement_row(migrated, result.session_id)
    assert ent is not None and ent["status"] == "ineligible" and ent["cents"] == 0
    assert ent["policy_artifact_id"] == NOBONUS_POLICY and ent["policy_version"] == 1


def test_flag_off_missing_alternate_refuses_consent(store: ContentStore) -> None:
    """A flag-off deployment that cannot resolve the nobonus artifact refuses —
    it never silently serves or accepts the bonus-on consent copy."""
    bound = bind(store, MANIFEST, mode="live")
    assert lifecycle.effective_consent_artifact(bound, None, bonus_payment_enabled=False) is None
    # Flag-on resolution is unaffected by a missing store — the bound pin is the artifact.
    assert lifecycle.effective_consent_artifact(bound, None, bonus_payment_enabled=True) is not None


# ----- flag off at the group boundary ------------------------------------------


def test_flag_off_group_consent_allocates_and_rebinds(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """The v13 flow: one group link over the four condition manifests, all
    pinning consent.reference@v9. Flag-off consent against the nobonus key
    still allocates a cell and rebinds to the member manifest — the
    uniform-consent check compares bound pins, which the flag never changes."""
    group = f"bonus.grp.{uuid.uuid4().hex[:8]}"
    members = {
        f"{group}.a": "tp_ai_ovnone_v3",
        f"{group}.b": "tp_ai_ovsome_v3",
    }
    for batch_id, manifest_id in members.items():
        adminops.create_batch(
            migrated.runtime,
            store,
            actor="test",
            batch_id=batch_id,
            manifest_id=manifest_id,
            mode="live",
        )
    adminops.create_batch_group(
        migrated.runtime, store, actor="test", group_id=group, batch_ids=list(members), mode="live"
    )
    adminops.set_group_enrollment(
        migrated.runtime, group, open_=True, actor="test", reason="launch"
    )

    result = enter(
        migrated.runtime,
        store,
        query=_query("BP-GRP-1", group),
        app_mode="live",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    sid, writer = result.session_id, result.writer_id
    _run(
        migrated,
        store,
        sid,
        writer,
        "consent",
        {"action": "consented", "text_version": NOBONUS_CONSENT},
        _stage(migrated, sid)[1],
        bonus_payment=False,
    )
    with migrated.runtime.connect() as conn:
        row = conn.execute(select(s.session).where(s.session.c.session_id == sid)).mappings().one()
        alloc = (
            conn.execute(select(s.allocation).where(s.allocation.c.session_id == sid))
            .mappings()
            .one()
        )
    assert row["batch_id"] in members and row["arm"] is not None
    assert alloc is not None
    assert _consent_row(migrated, sid)["text_version"] == NOBONUS_CONSENT


# ----- runner + wire -------------------------------------------------------------


def test_runner_flag_off_completes_under_nobonus_posture(
    migrated: MigratedDatabase,
) -> None:
    """The sim harness mirrors the deployment flag end to end: nobonus consent
    key, nobonus policy on the (QA-ineligible) entitlement, clean completion."""
    runner = Runner(migrated, ContentStore.load_default(), time_pressure=False, bonus_payment=False)
    batch = f"bonus.simoff.{uuid.uuid4().hex[:8]}"
    runner.seed_batch(MANIFEST, batch)
    report = runner.run_session(MANIFEST, batch, NormalPersona(7), seed=7)
    assert report.stage == "completion", report.findings
    sid = uuid.UUID(report.session_id)
    assert _consent_row(migrated, sid)["text_version"] == NOBONUS_CONSENT
    ent = _entitlement_row(migrated, sid)
    assert ent is not None and ent["status"] == "ineligible" and ent["cents"] == 0
    assert ent["policy_artifact_id"] == NOBONUS_POLICY


def test_workbench_serves_nobonus_material_and_flag(
    migrated: MigratedDatabase, make_settings: Any
) -> None:
    """HTTP surface: the consent stage projects the nobonus artifact (the client
    renders its text and echoes its key) and the neutral ``bonus_payment``
    capability flag reads false."""
    store = ContentStore.load_default()
    batch = f"bonus.http.{uuid.uuid4().hex[:8]}"
    bound = bind(store, MANIFEST, mode="qa")
    from app.services.sessions import register_manifest

    with migrated.superuser.begin() as conn:
        register_manifest(conn, bound)
        conn.execute(
            s.batch.insert().values(
                batch_id=batch,
                study_key=bound.manifest.study_key,
                manifest_id=bound.manifest_id,
                mode="qa",
                enrollment_open=True,
            )
        )
    result = enter(
        migrated.runtime,
        store,
        query=_query("BP-HTTP-1", batch),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    client = _authed_client(make_settings, migrated, result, BONUS_PAYMENT_ENABLED=False)
    resp = client.get("/workbench")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bonus_payment"] is False
    assert body["material"]["slot"] == "instruments.consent"
    assert body["material"]["artifact_key"] == NOBONUS_CONSENT
    text = body["material"]["body"]["text"]
    compensation = next(
        p for sec in text["sections"] for p in sec["paragraphs"] if "you will be paid" in p
    )
    assert "bonus" not in compensation
    assert "$3.00" in compensation


def test_session_opened_records_bonus_flag(migrated: MigratedDatabase, make_settings: Any) -> None:
    """Era inference survives a later flag flip: the entry event carries the
    deployment posture that served the session."""
    store = ContentStore.load_default()
    batch = f"bonus.flags.{uuid.uuid4().hex[:8]}"
    bound = bind(store, MANIFEST, mode="qa")
    from app.services.sessions import register_manifest

    with migrated.superuser.begin() as conn:
        register_manifest(conn, bound)
        conn.execute(
            s.batch.insert().values(
                batch_id=batch,
                study_key=bound.manifest.study_key,
                manifest_id=bound.manifest_id,
                mode="qa",
                enrollment_open=True,
            )
        )
    from app.services.entry import enter as service_enter

    result = service_enter(
        migrated.runtime,
        store,
        query=_query("BP-FLAG-1", batch),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
        feature_flags={"bonus_payment_enabled": False},
    )
    with migrated.runtime.connect() as conn:
        payload = conn.execute(
            select(s.event.c.payload).where(
                s.event.c.session_id == result.session_id,
                s.event.c.type == "session_opened",
            )
        ).scalar_one()
    assert payload["feature_flags"]["bonus_payment_enabled"] is False
