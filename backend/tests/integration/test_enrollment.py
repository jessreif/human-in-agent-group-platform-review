"""INV-0401/0402/0404/0405/0406/0407/0408: enrollment, credentials, writer lease,
lifecycle commands and completion against disposable PostgreSQL at head."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select, update

from app.db import schema as s
from app.db.engine import make_engine
from app.db.tx import Command, db_now, execute_command, lock_session
from app.domain.manifests.store import ContentStore
from app.main import create_app
from app.services import credentials, lifecycle
from app.services.entry import EntryError, enter
from app.services.preview import PreviewError, issue_descriptor
from app.settings import Settings
from app.timing.clock import VirtualClock

from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration

BUILD_SHA = "ab" * 32
KEY = b"k" * 32


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _seed_batch(
    db: MigratedDatabase,
    store: ContentStore,
    batch_id: str,
    manifest_id: str = "tp_demo_v3",
    mode: str = "qa",
) -> None:
    """Register the manifest and open a QA batch for it (as the migrator/superuser)."""
    from app.domain.manifests.binding import bind
    from app.services.sessions import register_manifest

    bound = bind(store, manifest_id, mode=mode)  # type: ignore[arg-type]
    with db.superuser.begin() as conn:
        register_manifest(conn, bound)
        conn.execute(
            insert(s.batch).values(
                batch_id=batch_id,
                study_key=bound.manifest.study_key,
                manifest_id=bound.manifest_id,
                mode=mode,
                enrollment_open=True,
            )
        )


def _query(pid: str, batch: str) -> dict[str, str]:
    return {
        "PROLIFIC_PID": pid,
        "STUDY_ID": "study-ref",
        "SESSION_ID": "sub-ref",
        "batch": batch,
    }


def test_entry_creates_session_credential_link_and_lease(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    _seed_batch(migrated, store, "qa-b1")
    result = enter(
        migrated.runtime,
        store,
        query=_query("P100", "qa-b1"),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    assert result.resumed is False and result.stage == "consent"
    with migrated.runtime.connect() as conn:
        session = (
            conn.execute(select(s.session).where(s.session.c.session_id == result.session_id))
            .mappings()
            .one()
        )
        assert session["stage"] == "consent" and session["writer_id"] == result.writer_id
        # Runtime may read only the eligibility-lookup columns of platform_link.
        link_partial = (
            conn.execute(
                select(
                    s.platform_link.c.platform,
                    s.platform_link.c.platform_pid,
                    s.platform_link.c.session_id,
                ).where(s.platform_link.c.session_id == result.session_id)
            )
            .mappings()
            .one()
        )
        assert link_partial["platform"] == "prolific" and link_partial["platform_pid"] == "P100"
    with migrated.superuser.connect() as conn:
        link = (
            conn.execute(
                select(s.platform_link).where(s.platform_link.c.session_id == result.session_id)
            )
            .mappings()
            .one()
        )
        assert link["platform"] == "prolific" and link["platform_pid"] == "P100"
    with migrated.runtime.connect() as conn:
        cred_row = (
            conn.execute(
                select(s.session_credential).where(
                    s.session_credential.c.session_id == result.session_id
                )
            )
            .mappings()
            .one()
        )
        # Only the hash is persisted; the raw token never touches the database.
        assert cred_row["credential_sha256"] == credentials.hash_token(result.credential_token)
        assert result.credential_token not in cred_row["credential_sha256"]
        assert credentials.authenticate(conn, result.credential_token) == result.session_id


def test_reentry_same_batch_pid_only_refused_credential_not_superseded(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """BA-01 / spec §5.1: a panel-ID match is linkage, never a credential. Re-entry that
    carries no valid live credential is refused ``credential_recovery_required``, audited,
    and the victim's live credential is never superseded."""
    _seed_batch(migrated, store, "qa-b2")
    first = enter(
        migrated.runtime,
        store,
        query=_query("P200", "qa-b2"),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    # Adversary knows the panel ID and batch but holds no credential.
    with pytest.raises(EntryError, match="credential_recovery_required"):
        enter(
            migrated.runtime,
            store,
            query=_query("P200", "qa-b2"),
            app_mode="qa",
            preview_key=None,
            current_manifest_sha256={},
            build_source_sha256=BUILD_SHA,
        )
    with migrated.runtime.connect() as conn:
        # The victim's credential is untouched — still authenticates to the session.
        assert credentials.authenticate(conn, first.credential_token) == first.session_id
        audits = (
            conn.execute(select(s.admin_audit).where(s.admin_audit.c.action == "reentry_refused"))
            .mappings()
            .all()
        )
        assert len(audits) == 1
        assert audits[0]["actor"] == "system"
        assert audits[0]["session_id"] == first.session_id
        assert audits[0]["payload"]["reason"] == "credential_recovery_required"
        assert "P200" not in str(audits[0]["payload"])  # panel PID never persisted


def test_reentry_same_batch_with_live_credential_resumes(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """BA-01 / INV-0407: re-entry carrying the session's live credential resumes the same
    session — and keeps the issued credential rather than superseding it."""
    _seed_batch(migrated, store, "qa-b2r")
    first = enter(
        migrated.runtime,
        store,
        query=_query("P200", "qa-b2r"),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    second = enter(
        migrated.runtime,
        store,
        query=_query("P200", "qa-b2r"),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
        presented_token=first.credential_token,
    )
    assert second.resumed is True and second.session_id == first.session_id
    assert second.credential_token == first.credential_token
    with migrated.runtime.connect() as conn:
        assert credentials.authenticate(conn, first.credential_token) == first.session_id
        resumed = (
            conn.execute(
                select(s.event.c.type)
                .where(s.event.c.session_id == first.session_id)
                .where(s.event.c.type == "session_resumed")
            )
            .scalars()
            .all()
        )
        assert resumed == ["session_resumed"]


def test_reentry_same_batch_expired_credential_refused(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """A superseded/expired credential no longer authenticates (spec §5.1); re-entry
    presenting it is refused with ``credential_recovery_required`` and audited."""
    _seed_batch(migrated, store, "qa-b2x")
    first = enter(
        migrated.runtime,
        store,
        query=_query("P200", "qa-b2x"),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    with migrated.superuser.begin() as conn:
        conn.execute(
            update(s.session_credential)
            .where(s.session_credential.c.session_id == first.session_id)
            .values(revoked_at=func.now(), revoked_reason="superseded")
        )
    with pytest.raises(EntryError, match="credential_recovery_required"):
        enter(
            migrated.runtime,
            store,
            query=_query("P200", "qa-b2x"),
            app_mode="qa",
            preview_key=None,
            current_manifest_sha256={},
            build_source_sha256=BUILD_SHA,
            presented_token=first.credential_token,
        )


def test_reentry_different_batch_is_repeat_participation(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    _seed_batch(migrated, store, "qa-b3")
    _seed_batch(migrated, store, "qa-b3b")
    enter(
        migrated.runtime,
        store,
        query=_query("P300", "qa-b3"),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    with pytest.raises(EntryError, match="repeat_participation"):
        enter(
            migrated.runtime,
            store,
            query=_query("P300", "qa-b3b"),
            app_mode="qa",
            preview_key=None,
            current_manifest_sha256={},
            build_source_sha256=BUILD_SHA,
        )
    with migrated.runtime.connect() as conn:
        audit = (
            conn.execute(
                select(s.admin_audit.c.payload).where(s.admin_audit.c.action == "reentry_refused")
            )
            .mappings()
            .one()
        )
        assert audit["payload"]["reason"] == "repeat_participation"


def test_entry_rejects_unknown_or_closed_batch_and_override_params(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    _seed_batch(migrated, store, "qa-b4")
    with migrated.superuser.begin() as conn:
        conn.execute(
            update(s.batch).where(s.batch.c.batch_id == "qa-b4").values(enrollment_open=False)
        )
    with pytest.raises(EntryError, match="batch_closed"):
        enter(
            migrated.runtime,
            store,
            query=_query("P400", "qa-b4"),
            app_mode="qa",
            preview_key=None,
            current_manifest_sha256={},
            build_source_sha256=BUILD_SHA,
        )
    with pytest.raises(EntryError, match="unknown_batch"):
        enter(
            migrated.runtime,
            store,
            query=_query("P400", "nope"),
            app_mode="qa",
            preview_key=None,
            current_manifest_sha256={},
            build_source_sha256=BUILD_SHA,
        )
    with pytest.raises(PreviewError, match="override"):
        enter(
            migrated.runtime,
            store,
            query={**_query("P400", "qa-b4"), "arm": "high"},
            app_mode="qa",
            preview_key=None,
            current_manifest_sha256={},
            build_source_sha256=BUILD_SHA,
        )


def test_preview_descriptor_entry_pins_arm_without_platform_link(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    stored = store.manifests["tp_demo_v3"]
    token = issue_descriptor(
        KEY,
        manifest_id="tp_demo_v3",
        manifest_sha256=stored.sha256,
        arm="high",
        seed="cd" * 16,
        mode="preview",
        timing_mode="faithful",
        now=datetime.now(UTC),
        lifetime=timedelta(hours=1),
        issuer="test",
    )
    result = enter(
        migrated.runtime,
        store,
        query={"pt": token},
        app_mode="preview",
        preview_key=KEY,
        current_manifest_sha256={"tp_demo_v3": stored.sha256},
        build_source_sha256=BUILD_SHA,
    )
    assert result.stage == "consent"
    with migrated.runtime.connect() as conn:
        session = (
            conn.execute(select(s.session).where(s.session.c.session_id == result.session_id))
            .mappings()
            .one()
        )
        assert session["arm"] == "high" and session["mode"] == "preview"
        assert (
            conn.execute(
                select(func.count())
                .select_from(s.platform_link)
                .where(s.platform_link.c.session_id == result.session_id)
            ).scalar_one()
            == 0
        )
        # previews never consume an allocation
        assert (
            conn.execute(
                select(func.count())
                .select_from(s.allocation)
                .where(s.allocation.c.session_id == result.session_id)
            ).scalar_one()
            == 0
        )


def test_preview_descriptor_represent_resumes_same_session(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """Reusing a signed descriptor must resume its preview session — the descriptor is the
    credential — never a second insert tripping uq_participant_research_pid (live-found bug)."""
    stored = store.manifests["tp_demo_v3"]
    token = issue_descriptor(
        KEY,
        manifest_id="tp_demo_v3",
        manifest_sha256=stored.sha256,
        arm="high",
        seed="ab" * 16,
        mode="preview",
        timing_mode="faithful",
        now=datetime.now(UTC),
        lifetime=timedelta(hours=1),
        issuer="test",
    )
    first = enter(
        migrated.runtime,
        store,
        query={"pt": token},
        app_mode="preview",
        preview_key=KEY,
        current_manifest_sha256={"tp_demo_v3": stored.sha256},
        build_source_sha256=BUILD_SHA,
    )
    second = enter(
        migrated.runtime,
        store,
        query={"pt": token},
        app_mode="preview",
        preview_key=KEY,
        current_manifest_sha256={"tp_demo_v3": stored.sha256},
        build_source_sha256=BUILD_SHA,
    )
    assert second.resumed and second.session_id == first.session_id
    with migrated.runtime.connect() as conn:
        # one participant, two live credentials, a session_resumed event
        assert conn.execute(select(func.count()).select_from(s.participant)).scalar_one() == 1
        assert (
            conn.execute(
                select(func.count())
                .select_from(s.event)
                .where(
                    s.event.c.session_id == first.session_id,
                    s.event.c.type == "session_resumed",
                )
            ).scalar_one()
            == 1
        )


def test_preview_descriptor_refused_after_tombstone(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """BA-39: cleanup tombstones the nonce's session — an outstanding descriptor is
    revoked and can neither resume nor re-mint a session for that nonce."""
    from app.services import retention

    stored = store.manifests["tp_demo_v3"]
    token = issue_descriptor(
        KEY,
        manifest_id="tp_demo_v3",
        manifest_sha256=stored.sha256,
        arm="high",
        seed="ef" * 16,
        mode="preview",
        timing_mode="faithful",
        now=datetime.now(UTC),
        lifetime=timedelta(hours=1),
        issuer="test",
    )

    def _enter() -> Any:
        return enter(
            migrated.runtime,
            store,
            query={"pt": token},
            app_mode="preview",
            preview_key=KEY,
            current_manifest_sha256={"tp_demo_v3": stored.sha256},
            build_source_sha256=BUILD_SHA,
        )

    first = _enter()
    with migrated.runtime.begin() as conn:
        retention.apply_deletion(
            conn,
            first.session_id,
            actor="test:cleanup",
            reason="preview cleanup",
            scope="preview",
            dry_run=False,
        )
    with pytest.raises(EntryError) as excinfo:
        _enter()
    assert excinfo.value.reason == "session_deleted"
    with migrated.runtime.connect() as conn:
        # No second session or participant was minted for the dead nonce.
        assert conn.execute(select(func.count()).select_from(s.session)).scalar_one() == 1
        assert conn.execute(select(func.count()).select_from(s.participant)).scalar_one() == 1
        refusal = (
            conn.execute(
                select(s.admin_audit.c.payload).where(
                    s.admin_audit.c.session_id == first.session_id,
                    s.admin_audit.c.action == "reentry_refused",
                )
            )
            .scalars()
            .all()
        )
        assert refusal and refusal[-1]["reason"] == "session_deleted"


def test_tombstoned_session_credential_never_authenticates(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """BA-39: ``authenticate`` resolves no session for a tombstoned one — identity
    credential rows survive cleanup (runtime role cannot delete them), so the
    tombstone check is the effective revocation."""
    from app.services import retention

    _seed_batch(migrated, store, "qa-tomb-auth")
    result = enter(
        migrated.runtime,
        store,
        query=_query("P-TOMB-AUTH", "qa-tomb-auth"),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    with migrated.runtime.connect() as conn:
        assert credentials.authenticate(conn, result.credential_token) == result.session_id
    with migrated.runtime.begin() as conn:
        retention.apply_deletion(
            conn,
            result.session_id,
            actor="test:cleanup",
            reason="participant request",
            scope="qa",
            dry_run=False,
        )
    with migrated.runtime.connect() as conn:
        assert credentials.authenticate(conn, result.credential_token) is None


def test_preview_rejected_without_key_and_accepted_in_live(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    stored = store.manifests["tp_demo_v3"]
    token = issue_descriptor(
        KEY,
        manifest_id="tp_demo_v3",
        manifest_sha256=stored.sha256,
        arm="low",
        seed="ef" * 16,
        mode="preview",
        timing_mode="faithful",
        now=datetime.now(UTC),
        lifetime=timedelta(hours=1),
        issuer="test",
    )
    with pytest.raises(EntryError, match="previews_not_configured"):
        enter(
            migrated.runtime,
            store,
            query={"pt": token},
            app_mode="preview",
            preview_key=None,
            current_manifest_sha256={"tp_demo_v3": stored.sha256},
            build_source_sha256=BUILD_SHA,
        )
    # 2026-10-07 amendment: a live deployment accepts the (always non-live)
    # descriptor as an analytically-excluded test session.
    result = enter(
        migrated.runtime,
        store,
        query={"pt": token},
        app_mode="live",
        preview_key=KEY,
        current_manifest_sha256={"tp_demo_v3": stored.sha256},
        build_source_sha256=BUILD_SHA,
    )
    assert result.resumed is False and result.stage == "consent"
    with migrated.runtime.connect() as conn:
        mode = conn.execute(
            select(s.session.c.mode).where(s.session.c.session_id == result.session_id)
        ).scalar_one()
        assert mode == "preview"


def _cmd(session_id: uuid.UUID, kind: str, payload: dict, revision: int) -> Command:
    return Command(
        command_id=uuid.uuid4(),
        session_id=session_id,
        kind=kind,
        expected_revision=revision,
        payload=payload,
    )


def test_consent_assigns_arm_and_idempotent_replay(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    _seed_batch(migrated, store, "qa-b5")
    result = enter(
        migrated.runtime,
        store,
        query=_query("P500", "qa-b5"),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    handler = lifecycle.handler_for(
        "consent", store=store, behavior_sha=BUILD_SHA, writer=result.writer_id
    )
    command = _cmd(
        result.session_id,
        "consent",
        {"action": "consented", "text_version": "consent.reference@v3"},
        revision=1,
    )
    first = execute_command(migrated.runtime, command, handler)
    assert first.accepted, first.reason
    replayed = execute_command(migrated.runtime, command, handler)
    assert replayed.accepted and replayed.replayed
    with migrated.runtime.connect() as conn:
        session = (
            conn.execute(select(s.session).where(s.session.c.session_id == result.session_id))
            .mappings()
            .one()
        )
        assert session["arm"] in ("high", "low")
        assert session["stage"] == "instructions"
        assert session["compatibility_key"].startswith("ck1:")
        alloc = (
            conn.execute(select(s.allocation).where(s.allocation.c.session_id == result.session_id))
            .mappings()
            .one()
        )
        assert alloc["arm"] == session["arm"]
        draws = conn.execute(
            select(s.draw_log).where(s.draw_log.c.session_id == result.session_id)
        ).all()
        assert len(draws) == 1 and draws[0].stream == "assignment"


def test_live_consent_needs_resolved_model_digest(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """Live promotion found allocate() never received the provider/model pin, so live
    consent always raised CompatibilityError. The digest must thread handler_for →
    _consent → allocate → compatibility_key."""
    from app.domain.compat import CompatibilityError

    _seed_batch(migrated, store, "live-b1", manifest_id="tp_review_first_v10", mode="live")
    result = enter(
        migrated.runtime,
        store,
        query=_query("P900", "live-b1"),
        app_mode="live",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    assert result.session_id

    # Unresolved digest → fail closed at consent (the bug this regression covers).
    blocked = lifecycle.handler_for(
        "consent", store=store, behavior_sha=BUILD_SHA, writer=result.writer_id
    )
    with pytest.raises(CompatibilityError, match="model digest"):
        execute_command(
            migrated.runtime,
            _cmd(
                result.session_id,
                "consent",
                {"action": "consented", "text_version": "consent.reference@v4"},
                revision=1,
            ),
            blocked,
        )

    # Resolved digest → allocation runs and the compat key pins the session.
    handler = lifecycle.handler_for(
        "consent",
        store=store,
        behavior_sha=BUILD_SHA,
        writer=result.writer_id,
        provider_model_digest="d" * 64,
    )
    accepted = execute_command(
        migrated.runtime,
        _cmd(
            result.session_id,
            "consent",
            {"action": "consented", "text_version": "consent.reference@v4"},
            revision=1,
        ),
        handler,
    )
    assert accepted.accepted, accepted.reason
    with migrated.runtime.connect() as conn:
        session = (
            conn.execute(select(s.session).where(s.session.c.session_id == result.session_id))
            .mappings()
            .one()
        )
        assert session["arm"] in ("high", "low")
        assert session["compatibility_key"].startswith("ck1:")
        alloc = (
            conn.execute(select(s.allocation).where(s.allocation.c.session_id == result.session_id))
            .mappings()
            .one()
        )
        assert alloc["arm"] == session["arm"]


def test_declined_consent_terminates(migrated: MigratedDatabase, store: ContentStore) -> None:
    _seed_batch(migrated, store, "qa-b6")
    result = enter(
        migrated.runtime,
        store,
        query=_query("P600", "qa-b6"),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    handler = lifecycle.handler_for(
        "consent", store=store, behavior_sha=BUILD_SHA, writer=result.writer_id
    )
    outcome = execute_command(
        migrated.runtime,
        _cmd(
            result.session_id,
            "consent",
            {"action": "declined", "text_version": "consent.reference@v3"},
            revision=1,
        ),
        handler,
    )
    assert outcome.accepted
    with migrated.runtime.connect() as conn:
        stage = conn.execute(
            select(s.session.c.stage, s.session.c.terminal_reason).where(
                s.session.c.session_id == result.session_id
            )
        ).one()
        assert stage == ("declined", "consent_declined")
        # no allocation was consumed
        assert (
            conn.execute(
                select(func.count())
                .select_from(s.allocation)
                .where(s.allocation.c.session_id == result.session_id)
            ).scalar_one()
            == 0
        )


def test_consent_text_version_must_match_bound_artifact(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """BA-37: the recorded consent version is the session's *bound* artifact key — a client
    naming any other version is refused, so the receipt can't drift from the copy served."""
    _seed_batch(migrated, store, "qa-b6m")
    result = enter(
        migrated.runtime,
        store,
        query=_query("P610", "qa-b6m"),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    handler = lifecycle.handler_for(
        "consent", store=store, behavior_sha=BUILD_SHA, writer=result.writer_id
    )
    out = execute_command(
        migrated.runtime,
        _cmd(
            result.session_id,
            "consent",
            {"action": "consented", "text_version": "consent.reference@v9"},
            revision=1,
        ),
        handler,
    )
    assert not out.accepted and out.reason == "consent_version_mismatch"
    with migrated.runtime.connect() as conn:
        stage = conn.execute(
            select(s.session.c.stage).where(s.session.c.session_id == result.session_id)
        ).scalar_one()
        assert stage == "consent"
        assert (
            conn.execute(
                select(func.count())
                .select_from(s.consent_event)
                .where(s.consent_event.c.session_id == result.session_id)
            ).scalar_one()
            == 0
        )


def test_pointer_bound_legacy_consent_fails_closed(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """BA-37: a session bound to a pointer-only consent artifact (the v1 lineage) cannot
    record consent at all — even when the client submits the *matching* bound key, the
    command boundary refuses because no literal approved copy exists for that binding.
    Declining is still allowed so the session can reach an honest terminal state."""
    _seed_batch(migrated, store, "qa-b6p", manifest_id="tp_demo_v1")
    result = enter(
        migrated.runtime,
        store,
        query=_query("P611", "qa-b6p"),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    handler = lifecycle.handler_for(
        "consent", store=store, behavior_sha=BUILD_SHA, writer=result.writer_id
    )
    refused = execute_command(
        migrated.runtime,
        _cmd(
            result.session_id,
            "consent",
            {"action": "consented", "text_version": "consent.reference@v1"},
            revision=1,
        ),
        handler,
    )
    assert not refused.accepted and refused.reason == "consent_unavailable"
    declined = execute_command(
        migrated.runtime,
        _cmd(
            result.session_id,
            "consent",
            {"action": "declined", "text_version": "consent.reference@v1"},
            revision=1,  # a rejected command does not consume the revision
        ),
        handler,
    )
    assert declined.accepted
    with migrated.runtime.connect() as conn:
        stage = conn.execute(
            select(s.session.c.stage).where(s.session.c.session_id == result.session_id)
        ).scalar_one()
        assert stage == "declined"


def test_stale_revision_and_wrong_stage_rejected(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    _seed_batch(migrated, store, "qa-b7")
    result = enter(
        migrated.runtime,
        store,
        query=_query("P700", "qa-b7"),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    handler_for = lifecycle.handler_for
    stale = execute_command(
        migrated.runtime,
        _cmd(
            result.session_id,
            "consent",
            {"action": "consented", "text_version": "consent.reference@v3"},
            revision=99,
        ),
        handler_for("consent", store=store, behavior_sha=BUILD_SHA, writer=result.writer_id),
    )
    assert not stale.accepted and stale.reason == "stale_revision"
    wrong_stage = execute_command(
        migrated.runtime,
        _cmd(result.session_id, "data_use_choice", {"choice": "use_all"}, revision=1),
        handler_for(
            "data_use_choice", store=store, behavior_sha=BUILD_SHA, writer=result.writer_id
        ),
    )
    assert not wrong_stage.accepted and wrong_stage.reason == "wrong_stage"


def test_writer_lease_blocks_second_writer_and_takeover_recovers(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    _seed_batch(migrated, store, "qa-b8")
    result = enter(
        migrated.runtime,
        store,
        query=_query("P800", "qa-b8"),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    handler_for = lifecycle.handler_for
    impostor = uuid.uuid4()
    blocked = execute_command(
        migrated.runtime,
        _cmd(
            result.session_id,
            "consent",
            {"action": "consented", "text_version": "consent.reference@v3"},
            revision=1,
        ),
        handler_for("consent", store=store, behavior_sha=BUILD_SHA, writer=impostor),
    )
    assert not blocked.accepted and blocked.reason == "writer_lease_held"
    takeover = execute_command(
        migrated.runtime,
        _cmd(result.session_id, "takeover", {}, revision=1),
        handler_for("takeover", store=store, behavior_sha=BUILD_SHA),
    )
    assert takeover.accepted
    new_writer = uuid.UUID(takeover.result["writer_id"])
    # the incumbent can no longer mutate; the new writer can
    stale_writer = execute_command(
        migrated.runtime,
        _cmd(
            result.session_id,
            "consent",
            {"action": "consented", "text_version": "consent.reference@v3"},
            revision=takeover.committed_revision,
        ),
        handler_for("consent", store=store, behavior_sha=BUILD_SHA, writer=result.writer_id),
    )
    assert not stale_writer.accepted and stale_writer.reason == "writer_lease_held"
    ok = execute_command(
        migrated.runtime,
        _cmd(
            result.session_id,
            "consent",
            {"action": "consented", "text_version": "consent.reference@v3"},
            revision=takeover.committed_revision,
        ),
        handler_for("consent", store=store, behavior_sha=BUILD_SHA, writer=new_writer),
    )
    assert ok.accepted, ok.reason


def test_resume_renews_holder_blocks_second_writer_and_expiry_releases(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """Entry grants the writer lease, so the bootstrap ``resume`` must be an idempotent
    renew for the holder (regression: acquire-on-resume self-rejected a fresh session
    into read-only), refuse a different writer while live, and allow reacquisition
    after expiry."""
    _seed_batch(migrated, store, "qa-b8r")
    result = enter(
        migrated.runtime,
        store,
        query=_query("P810", "qa-b8r"),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    handler_for = lifecycle.handler_for
    same = execute_command(
        migrated.runtime,
        _cmd(result.session_id, "resume", {}, revision=1),
        handler_for("resume", store=store, behavior_sha=BUILD_SHA, writer=result.writer_id),
    )
    assert same.accepted, same.reason
    assert same.result["writer_id"] == str(result.writer_id)
    impostor = uuid.uuid4()
    blocked = execute_command(
        migrated.runtime,
        _cmd(result.session_id, "resume", {}, revision=same.committed_revision),
        handler_for("resume", store=store, behavior_sha=BUILD_SHA, writer=impostor),
    )
    assert not blocked.accepted and blocked.reason == "writer_lease_held"
    holderless = execute_command(
        migrated.runtime,
        _cmd(result.session_id, "resume", {}, revision=blocked.committed_revision),
        handler_for("resume", store=store, behavior_sha=BUILD_SHA, writer=None),
    )
    assert not holderless.accepted and holderless.reason == "writer_lease_held"
    with migrated.runtime.connect() as conn:
        conn.execute(
            update(s.session)
            .where(s.session.c.session_id == result.session_id)
            .values(writer_lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )
        conn.commit()
    regained = execute_command(
        migrated.runtime,
        _cmd(result.session_id, "resume", {}, revision=holderless.committed_revision),
        handler_for("resume", store=store, behavior_sha=BUILD_SHA, writer=impostor),
    )
    assert regained.accepted, regained.reason
    new_writer = uuid.UUID(regained.result["writer_id"])
    assert new_writer != result.writer_id
    late_incumbent = execute_command(
        migrated.runtime,
        _cmd(result.session_id, "resume", {}, revision=regained.committed_revision),
        handler_for("resume", store=store, behavior_sha=BUILD_SHA, writer=result.writer_id),
    )
    assert not late_incumbent.accepted and late_incumbent.reason == "writer_lease_held"


def test_full_lifecycle_to_completion(migrated: MigratedDatabase, store: ContentStore) -> None:
    """Drive the QA spine: consent -> ... -> task -> (task closed by SQL stand-in for the
    WP06 deadline job) -> confidence -> post -> debrief -> data_use -> complete."""
    _seed_batch(migrated, store, "qa-b9")
    handler_for = lifecycle.handler_for
    completion_handler = lifecycle.completion_handler
    result = enter(
        migrated.runtime,
        store,
        query=_query("P900", "qa-b9"),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    sid, writer = result.session_id, result.writer_id

    def run(kind: str, payload: dict, revision: int):
        out = execute_command(
            migrated.runtime,
            _cmd(sid, kind, payload, revision),
            handler_for(kind, store=store, behavior_sha=BUILD_SHA, writer=writer),
        )
        assert out.accepted, (kind, out.reason)
        return out

    def stage() -> tuple[str, int]:
        with migrated.runtime.connect() as conn:
            row = conn.execute(
                select(s.session.c.stage, s.session.c.revision).where(s.session.c.session_id == sid)
            ).one()
            return row.stage, row.revision

    run(
        "consent",
        {"action": "consented", "text_version": "consent.reference@v3"},
        stage()[1],
    )
    assert stage()[0] == "instructions"

    def force_wall(kind: str) -> None:
        with migrated.runtime.begin() as conn:
            conn.execute(
                update(s.scheduled_job)
                .where(
                    s.scheduled_job.c.session_id == sid,
                    s.scheduled_job.c.kind == kind,
                    s.scheduled_job.c.status == "pending",
                )
                .values(due_wall_at=func.now(), claimable_at=func.now())
            )
        from app.services import scheduler

        scheduler.tick(migrated.runtime, store, worker_id="t1", provider=None, limit=25)

    run("training_step", {"step": 1}, stage()[1])
    # BA-95: completion requires a committed practice share first.
    run(
        "training_step",
        {"step": "practice", "action": "share", "record_id": "PRACTICE-K3-04"},
        stage()[1],
    )
    run(
        "training_step",
        {"step": "practice", "action": "pick_location", "crate": 3, "location": "The Sawmill"},
        stage()[1],
    )

    run(
        "training_step",
        {"step": "practice", "action": "ask", "text": "anything on crate 3?"},
        stage()[1],
    )
    run(
        "training_step",
        {
            "step": "comprehension",
            "complete": True,
            "answers": dict(lifecycle.COMPREHENSION_KEY),
        },
        stage()[1],
    )
    assert stage()[0] == "scripted_connecting"
    # INV-0701: PM's join is a wall job; Start stays gated until it commits.
    force_wall("connecting")
    run("ready", {}, stage()[1])
    assert stage()[0] == "ready"
    force_wall("task_open")
    assert stage()[0] == "task"
    run("answer_update", {"crate": 3, "location_id": "L9"}, stage()[1])

    # Stand-in for the WP06 deadline job: lock the task.
    with migrated.superuser.begin() as conn:
        conn.execute(
            update(s.session).where(s.session.c.session_id == sid).values(stage="answers_locked")
        )
    run("proceed", {}, stage()[1])
    assert stage()[0] == "confidence"
    run(
        "item_response",
        {"item_id": "confidence", "crate": 3, "value": 80},
        stage()[1],
    )
    assert stage()[0] == "post_questionnaire"
    # post_task.tp_demo items are all skippable (S5)
    for i in range(1, 10):
        run("item_skip", {"item_id": f"tp_post_{i:02d}"}, stage()[1])
    # BA-105: tp_demo_v3 binds no instruments.open_ended — post goes straight
    # to debrief (artifact-bound coverage lives on tp_ai_v13).
    assert stage()[0] == "debrief"
    run("debrief_ack", {}, stage()[1])
    run("data_use_choice", {"choice": "use_all"}, stage()[1])
    final = execute_command(
        migrated.runtime,
        _cmd(sid, "complete", {}, stage()[1]),
        completion_handler(store=store, writer=writer),
    )
    assert final.accepted, final.reason
    assert final.result["entitlement"]["status"] == "ineligible"  # qa is never payable
    with migrated.runtime.connect() as conn:
        receipt = (
            conn.execute(
                select(s.completion_receipt).where(s.completion_receipt.c.session_id == sid)
            )
            .mappings()
            .one()
        )
        assert receipt["outcome"] == "completed"
        entitlement = (
            conn.execute(
                select(s.payment_entitlement).where(s.payment_entitlement.c.session_id == sid)
            )
            .mappings()
            .one()
        )
        assert entitlement["status"] == "ineligible" and entitlement["cents"] == 0


def test_http_entry_and_command_flow(
    migrated: MigratedDatabase,
    store: ContentStore,
    make_settings: Callable[..., Settings],
) -> None:
    """API level: cookies are set on entry, CSRF is enforced on commands."""
    _seed_batch(migrated, store, "qa-b10")
    settings = make_settings(
        APP_MODE="qa",
        DATABASE_URL=migrated.dsns.runtime,
        PREVIEW_SIGNING_KEY="k" * 40,
    )
    app = create_app(settings, clock=VirtualClock(), engine=migrated.runtime, configure_logs=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/entry", params=_query("PX1", "qa-b10"))
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert body["stage"] == "consent" and body["resumed"] is False
        assert credentials.SESSION_COOKIE in client.cookies
        assert credentials.CSRF_COOKIE in client.cookies
        # Cookies are path-scoped to /investigators/; the test client calls the raw app
        # routes, so clear the jar and re-scope them to "/" for the jar to send them.
        values = {
            name: resp.cookies.get(name)
            for name in (credentials.SESSION_COOKIE, credentials.CSRF_COOKIE, "inv_writer")
        }
        client.cookies.clear()
        for name, value in values.items():
            if value is not None:
                client.cookies.set(name, value, path="/")

        session = client.get("/session").json()["session"]
        assert session["stage"] == "consent"

        # mutating without CSRF header is refused
        no_csrf = client.post(
            "/commands",
            json={"kind": "consent", "payload": {"action": "consented", "text_version": "v"}},
        )
        assert no_csrf.status_code == 403

        csrf = client.cookies.get(credentials.CSRF_COOKIE)
        cmd = client.post(
            "/commands",
            json={
                "kind": "consent",
                "command_id": str(uuid.uuid4()),
                "payload": {"action": "consented", "text_version": "consent.reference@v3"},
            },
            # BA-26: mutating commands name the holder in x-writer-id; the shared
            # inv_writer cookie stays in the jar but no longer suffices on its own.
            headers={credentials.CSRF_HEADER: csrf, "x-writer-id": values["inv_writer"]},
        )
        assert cmd.status_code == 200, cmd.json()
        assert cmd.json()["accepted"] is True
        assert client.get("/session").json()["session"]["stage"] == "instructions"


def test_http_resume_after_entry_and_foreign_writer(
    migrated: MigratedDatabase,
    store: ContentStore,
    make_settings: Callable[..., Settings],
) -> None:
    """API level (live-found bug): entry grants the lease, so the bootstrap ``resume``
    carrying the entry's writer cookie must renew — not 409 the only tab into
    read-only. A foreign writer id still gets ``writer_lease_held``, and the
    ``x-writer-id`` header wins over the shared cookie."""
    _seed_batch(migrated, store, "qa-b12")
    settings = make_settings(
        APP_MODE="qa",
        DATABASE_URL=migrated.dsns.runtime,
        PREVIEW_SIGNING_KEY="k" * 40,
    )
    app = create_app(settings, clock=VirtualClock(), engine=migrated.runtime, configure_logs=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/entry", params=_query("PW1", "qa-b12"))
        assert resp.status_code == 200, resp.json()
        entry_writer = resp.cookies.get("inv_writer")
        assert entry_writer is not None
        values = {
            name: resp.cookies.get(name)
            for name in (credentials.SESSION_COOKIE, credentials.CSRF_COOKIE)
        }
        client.cookies.clear()
        for name, value in values.items():
            if value is not None:
                client.cookies.set(name, value, path="/")
        csrf = client.cookies.get(credentials.CSRF_COOKIE)

        def _resume(headers: dict[str, str]) -> Any:
            revision = client.get("/session").json()["session"]["revision"]
            return client.post(
                "/commands",
                json={
                    "kind": "resume",
                    "command_id": str(uuid.uuid4()),
                    "expected_revision": revision,
                    "payload": {},
                },
                headers={credentials.CSRF_HEADER: csrf, **headers},
            )

        # holder via header (per-tab identity): accepted
        held = _resume({"x-writer-id": entry_writer})
        assert held.status_code == 200 and held.json()["accepted"], held.json()
        assert held.json()["result"]["writer_id"] == entry_writer
        # a foreign writer id is refused even though the shared cookie is absent
        foreign = _resume({"x-writer-id": str(uuid.uuid4())})
        assert foreign.status_code == 409 and foreign.json()["error"] == "writer_lease_held"
        # header beats cookie: holder cookie + foreign header still refuses
        client.cookies.set("inv_writer", entry_writer, path="/")
        mixed = _resume({"x-writer-id": str(uuid.uuid4())})
        assert mixed.status_code == 409 and mixed.json()["error"] == "writer_lease_held"
        # cookie alone (entry bootstrap before sessionStorage is populated) renews
        client.cookies.set("inv_writer", entry_writer, path="/")
        cookie_only = _resume({})
        assert cookie_only.status_code == 200 and cookie_only.json()["accepted"]


def test_concurrent_consent_allocates_distinct_slots(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """INV-0409: four sessions consenting simultaneously in one batch each reserve a
    distinct allocation slot; with two arms the result is exactly 2 high + 2 low."""
    import concurrent.futures

    _seed_batch(migrated, store, "qa-b11")
    entries = [
        enter(
            migrated.runtime,
            store,
            query=_query(f"PC{n}", "qa-b11"),
            app_mode="qa",
            preview_key=None,
            current_manifest_sha256={},
            build_source_sha256=BUILD_SHA,
        )
        for n in range(4)
    ]

    def consent(result):
        out = execute_command(
            migrated.runtime,
            _cmd(
                result.session_id,
                "consent",
                {"action": "consented", "text_version": "consent.reference@v3"},
                1,
            ),
            lifecycle.handler_for(
                "consent", store=store, behavior_sha=BUILD_SHA, writer=result.writer_id
            ),
        )
        assert out.accepted, out.reason
        return out

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(consent, entries))

    with migrated.runtime.connect() as conn:
        arms = sorted(
            conn.execute(
                select(s.session.c.arm).where(
                    s.session.c.session_id.in_([r.session_id for r in entries])
                )
            ).scalars()
        )
    assert arms == ["high", "high", "low", "low"]


def test_http_entry_with_live_cookie_resumes_same_session(
    migrated: MigratedDatabase,
    store: ContentStore,
    make_settings: Callable[..., Settings],
) -> None:
    """BA-29: POST /entry must carry the HttpOnly ``inv_session`` cookie into
    ``enter()`` — a browser whose GET /session transiently failed still holds the live
    credential, so a same-batch re-entry resumes instead of refusing
    ``credential_recovery_required``. A bare panel ID stays refused (BA-01)."""
    _seed_batch(migrated, store, "qa-b14")
    settings = make_settings(
        APP_MODE="qa",
        DATABASE_URL=migrated.dsns.runtime,
        PREVIEW_SIGNING_KEY="k" * 40,
    )
    app = create_app(settings, clock=VirtualClock(), engine=migrated.runtime, configure_logs=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        first = client.post("/entry", params=_query("PX2", "qa-b14"))
        assert first.status_code == 200, first.json()
        assert first.json()["resumed"] is False
        # Cookies are path-scoped to /investigators/; re-scope them to "/" for the jar.
        values = {
            name: first.cookies.get(name)
            for name in (credentials.SESSION_COOKIE, credentials.CSRF_COOKIE, "inv_writer")
        }
        client.cookies.clear()
        for name, value in values.items():
            if value is not None:
                client.cookies.set(name, value, path="/")

        # The jar still holds the live credential: re-entry resumes the same session.
        second = client.post("/entry", params=_query("PX2", "qa-b14"))
        assert second.status_code == 200, second.json()
        assert second.json()["resumed"] is True
        assert second.json()["session_id"] == first.json()["session_id"]

    # A stranger's jar — same panel ID, no credential — is still refused (BA-01).
    with TestClient(app, raise_server_exceptions=False) as stranger:
        refused = stranger.post("/entry", params=_query("PX2", "qa-b14"))
        assert refused.status_code == 403
        assert refused.json()["error"] == "entry_refused"
        assert refused.json()["detail"] == "credential_recovery_required"


def test_http_superseded_tab_cannot_write_via_shared_cookie(
    migrated: MigratedDatabase,
    store: ContentStore,
    make_settings: Callable[..., Settings],
) -> None:
    """BA-26: the profile-shared ``inv_writer`` cookie names the current holder in
    EVERY tab, so it can never authorize an ordinary mutation — only ``x-writer-id``
    counts. ``resume``/``takeover`` stay the claim paths that may establish identity
    from the cookie."""
    _seed_batch(migrated, store, "qa-b15")
    settings = make_settings(
        APP_MODE="qa",
        DATABASE_URL=migrated.dsns.runtime,
        PREVIEW_SIGNING_KEY="k" * 40,
    )
    app = create_app(settings, clock=VirtualClock(), engine=migrated.runtime, configure_logs=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        entry = client.post("/entry", params=_query("PW3", "qa-b15"))
        assert entry.status_code == 200, entry.json()
        first_writer = entry.cookies.get("inv_writer")
        assert first_writer is not None
        values = {
            name: entry.cookies.get(name)
            for name in (credentials.SESSION_COOKIE, credentials.CSRF_COOKIE)
        }
        client.cookies.clear()
        for name, value in values.items():
            if value is not None:
                client.cookies.set(name, value, path="/")
        client.cookies.set("inv_writer", first_writer, path="/")
        csrf = client.cookies.get(credentials.CSRF_COOKIE)

        def _command(kind: str, payload: dict[str, Any], headers: dict[str, str]) -> Any:
            revision = client.get("/session").json()["session"]["revision"]
            return client.post(
                "/commands",
                json={
                    "kind": kind,
                    "command_id": str(uuid.uuid4()),
                    "expected_revision": revision,
                    "payload": payload,
                },
                headers={credentials.CSRF_HEADER: csrf, **headers},
            )

        # Even the legitimate holder's cookie alone no longer authorizes a mutation —
        # the per-tab header is required (BA-26 server half).
        cookie_only = _command(
            "consent", {"action": "consented", "text_version": "consent.reference@v3"}, {}
        )
        assert cookie_only.status_code == 409
        assert cookie_only.json()["error"] == "writer_lease_held"

        # With the header naming the holder the same write commits.
        held = _command(
            "consent",
            {"action": "consented", "text_version": "consent.reference@v3"},
            {"x-writer-id": first_writer},
        )
        assert held.status_code == 200 and held.json()["accepted"], held.json()

        # A second tab takes over — a claim path, so no per-tab header is needed.
        takeover = _command("takeover", {}, {})
        assert takeover.status_code == 200 and takeover.json()["accepted"], takeover.json()
        new_writer = takeover.json()["result"]["writer_id"]
        assert new_writer != first_writer

        # The profile-shared cookie now names the NEW holder in both tabs. The
        # superseded tab's queued write (its header cleared) must not go through.
        client.cookies.set("inv_writer", new_writer, path="/")
        queued = _command("training_step", {"step": 1}, {})
        assert queued.status_code == 409
        assert queued.json()["error"] == "writer_lease_held"
        # A stale per-tab header is refused just the same.
        stale = _command("training_step", {"step": 1}, {"x-writer-id": first_writer})
        assert stale.status_code == 409 and stale.json()["error"] == "writer_lease_held"
        # /complete takes the same header-only rule.
        revision = client.get("/session").json()["session"]["revision"]
        complete = client.post(
            "/complete",
            json={"command_id": str(uuid.uuid4()), "expected_revision": revision},
            headers={credentials.CSRF_HEADER: csrf},
        )
        assert complete.status_code == 409
        assert complete.json()["error"] == "writer_lease_held"

        # The current holder — named in the header — writes normally.
        ok = _command("training_step", {"step": 1}, {"x-writer-id": new_writer})
        assert ok.status_code == 200 and ok.json()["accepted"], ok.json()


def test_reentry_locks_session_before_reading_sequence(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """BA-30: same-batch re-entry reads revision/next_event_seq under the session row
    lock — a command committing an event between the read and the lease takeover must
    never strand the re-entry on a duplicate (session_id, seq)."""
    _seed_batch(migrated, store, "qa-b16")
    first = enter(
        migrated.runtime,
        store,
        query=_query("PB30", "qa-b16"),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    sid = first.session_id
    engine = make_engine(migrated.dsns.runtime)
    try:
        conn = engine.connect()
        tx = conn.begin()
        # A concurrent command holds the session row lock while it appends its event —
        # exactly the interleaving an unlocked _resume read would lose (BA-30).
        row = lock_session(conn, sid)
        conn.execute(
            insert(s.event).values(
                session_id=sid,
                seq=row["next_event_seq"],
                event_id=uuid.uuid4(),
                stage=row["stage"],
                actor="participant",
                type="answer_updated",
                revision=row["revision"] + 1,
                server_time=db_now(conn),
                payload={},
            )
        )
        conn.execute(
            update(s.session)
            .where(s.session.c.session_id == sid)
            .values(revision=row["revision"] + 1, next_event_seq=row["next_event_seq"] + 1)
        )

        results: list[Any] = []
        errors: list[BaseException] = []

        def reenter() -> None:
            try:
                results.append(
                    enter(
                        engine,
                        store,
                        query=_query("PB30", "qa-b16"),
                        app_mode="qa",
                        preview_key=None,
                        current_manifest_sha256={},
                        build_source_sha256=BUILD_SHA,
                        presented_token=first.credential_token,
                    )
                )
            except BaseException as exc:  # noqa: BLE001 - re-raised in the main thread
                errors.append(exc)

        worker = threading.Thread(target=reenter, daemon=True)
        worker.start()
        # The row lock is still held: the re-entry must be blocked on it — if it had
        # already read next_event_seq without FOR UPDATE its event append would now be
        # racing the uncommitted event above.
        time.sleep(0.5)
        assert worker.is_alive(), "re-entry did not wait on the held session row lock"
        tx.commit()
        conn.close()
        worker.join(timeout=15)
        assert not worker.is_alive(), "re-entry did not finish after the lock released"
        if errors:
            raise errors[0]
        (second,) = results
        assert second.resumed is True and second.session_id == sid
    finally:
        engine.dispose()

    with migrated.runtime.connect() as conn:
        seqs = sorted(
            conn.execute(select(s.event.c.seq).where(s.event.c.session_id == sid)).scalars()
        )
        resumed_count = conn.execute(
            select(func.count())
            .select_from(s.event)
            .where(s.event.c.session_id == sid, s.event.c.type == "session_resumed")
        ).scalar_one()
    # Contiguous unique sequences: the re-entry saw the committed event, never a dup.
    assert seqs == list(range(1, seqs[-1] + 1))
    assert resumed_count == 1


def test_concurrent_preview_first_use_of_one_descriptor(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """BA-31: two simultaneous first uses of one signed descriptor serialize on the
    descriptor nonce — one entry creates the session, the loser of the advisory lock
    re-reads and resumes. Exactly one participant/session, no integrity error."""
    stored = store.manifests["tp_demo_v3"]
    token = issue_descriptor(
        KEY,
        manifest_id="tp_demo_v3",
        manifest_sha256=stored.sha256,
        arm="high",
        seed="be" * 16,
        mode="preview",
        timing_mode="faithful",
        now=datetime.now(UTC),
        lifetime=timedelta(hours=1),
        issuer="test",
    )
    engines = [make_engine(migrated.dsns.runtime) for _ in range(2)]
    barrier = threading.Barrier(2)
    results: list[Any] = []
    errors: list[BaseException] = []

    def run(engine: Any) -> None:
        try:
            barrier.wait(timeout=10)
            results.append(
                enter(
                    engine,
                    store,
                    query={"pt": token},
                    app_mode="preview",
                    preview_key=KEY,
                    current_manifest_sha256={"tp_demo_v3": stored.sha256},
                    build_source_sha256=BUILD_SHA,
                )
            )
        except BaseException as exc:  # noqa: BLE001 - re-raised in the main thread
            errors.append(exc)

    try:
        workers = [threading.Thread(target=run, args=(engine,), daemon=True) for engine in engines]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=30)
        assert all(not worker.is_alive() for worker in workers), "an entry never finished"
    finally:
        for engine in engines:
            engine.dispose()
    if errors:
        raise errors[0]
    # One entry created the session; the other resumed it — never a second insert.
    assert sorted(r.resumed for r in results) == [False, True]
    assert results[0].session_id == results[1].session_id
    with migrated.runtime.connect() as conn:
        assert conn.execute(select(func.count()).select_from(s.participant)).scalar_one() == 1
        assert conn.execute(select(func.count()).select_from(s.session)).scalar_one() == 1
