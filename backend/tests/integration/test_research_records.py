"""INV-0304/0305/0306/0308/0309 on real PostgreSQL: the research-record tables, per-role
privileges on them, append-only enforcement, and consistent-snapshot export/replay."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db import schema as s
from app.db.export import (
    EXPORT_EXCLUDED_TABLES,
    SESSION_TABLES,
    ReplayMismatchError,
    export_payment_linkage,
    export_session,
    reconstruct_session,
)
from app.db.tx import Command, db_now, execute_command
from tests.integration.conftest import MigratedDatabase, run_alembic
from tests.integration.test_persistence_schema import _denied, _new_session

pytestmark = pytest.mark.integration

ZERO = "0" * 64


def _world_and_record(db: MigratedDatabase, session_id: uuid.UUID) -> str:
    world_id = uuid.uuid4()
    with db.runtime.begin() as conn:
        conn.execute(
            s.world_snapshot.insert().values(
                world_id=world_id,
                session_id=session_id,
                generator="g",
                content={},
                content_sha256=ZERO,
            )
        )
        conn.execute(
            s.record.insert().values(
                session_id=session_id,
                record_id="R1",
                world_id=world_id,
                crate=1,
                stamp="Yesterday, 9:00 AM",
                body="body",
                event_type="arrival",
                timeline_ordinal=1,
                content_sha256=ZERO,
            )
        )
    return "R1"


def _accepted_command(db: MigratedDatabase, session_id: uuid.UUID) -> uuid.UUID:
    """One accepted command that appends exactly one event."""
    command_id = uuid.uuid4()

    def handler(ctx, payload):
        ctx.append_event("answer_updated", actor="participant", payload=payload)
        return {"echo": payload["crate"]}

    with db.runtime.connect() as conn:
        revision = conn.execute(
            select(s.session.c.revision).where(s.session.c.session_id == session_id)
        ).scalar_one()
    result = execute_command(
        db.runtime,
        Command(command_id, session_id, "answer_update", revision, {"crate": 1}),
        handler,
    )
    assert result.accepted
    return command_id


def test_stepwise_upgrade_from_prior_release(
    bootstrapped: MigratedDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INV-0308: migrate from the previous schema revision, not only from empty."""
    run_alembic(monkeypatch, bootstrapped.dsns.migrator, "upgrade", "0001_core")
    with bootstrapped.migrator.connect() as conn:
        names = set(inspect(conn).get_table_names(schema="app"))
        assert "message" not in names and "answer_final" not in names
    run_alembic(monkeypatch, bootstrapped.dsns.migrator, "upgrade", "head")
    with bootstrapped.migrator.connect() as conn:
        names = set(inspect(conn).get_table_names(schema="app"))
        assert {"message", "answer_final", "coding_run"} <= names


def test_new_append_only_tables_reject_mutation(migrated: MigratedDatabase) -> None:
    session_id = _new_session(migrated)
    _world_and_record(migrated, session_id)
    command_id = _accepted_command(migrated, session_id)
    with migrated.runtime.begin() as conn:
        conn.execute(
            s.share.insert().values(
                session_id=session_id,
                share_id=uuid.uuid4(),
                sharer="participant",
                record_id="R1",
                recipients=["JT"],
                source="participant_action",
                delivered_seq=1,
            )
        )
    _denied(migrated.runtime, "UPDATE app.share SET source = 'crosstalk'")
    _denied(migrated.runtime, "DELETE FROM app.share")
    _denied(migrated.runtime, "TRUNCATE app.answer_revision")
    with migrated.runtime.begin() as conn:
        conn.execute(
            s.answer_revision.insert().values(
                session_id=session_id,
                crate=1,
                revision=1,
                location_id="marina",
                command_id=command_id,
                accepted_seq=1,
            )
        )
    _denied(migrated.runtime, "UPDATE app.answer_revision SET location_id = 'depot'")


def test_compensation_reads_payment_only(migrated: MigratedDatabase) -> None:
    session_id = _new_session(migrated)
    with migrated.runtime.begin() as conn:
        conn.execute(
            s.payment_entitlement.insert().values(
                session_id=session_id,
                kind="base",
                cents=400,
                policy_artifact_id="payment.s5_base_only",
                policy_version=1,
            )
        )
        conn.execute(
            s.result.insert().values(
                session_id=session_id,
                scoring_version="v1",
                correct_total=2,
                correct_teamdep=1,
                correct_ownfile=1,
                status="scored",
                finalized_seq=1,
            )
        )
    with migrated.compensation.connect() as conn:
        assert (
            conn.execute(
                text("SELECT cents FROM app.payment_entitlement WHERE session_id = :sid"),
                {"sid": session_id},
            ).scalar_one()
            == 400
        )
        assert (
            conn.execute(
                text("SELECT status FROM app.result WHERE session_id = :sid"), {"sid": session_id}
            ).scalar_one()
            == "scored"
        )
    # may mark paid, nothing else; the role default is read-only, so the payout
    # path must deliberately opt in per transaction before writing
    with migrated.compensation.begin() as conn:
        conn.execute(text("SET TRANSACTION READ WRITE"))
        conn.execute(
            text(
                "UPDATE app.payment_entitlement SET status = 'paid', paid_at = now(), "
                "paid_ref = 'recon-1' WHERE session_id = :sid"
            ),
            {"sid": session_id},
        )
    # amount columns stay off-limits even when the transaction deliberately opts into writes
    with pytest.raises(DBAPIError) as info, migrated.compensation.begin() as conn:
        conn.execute(text("SET TRANSACTION READ WRITE"))
        conn.execute(text("UPDATE app.payment_entitlement SET cents = 999"))
    assert "permission denied" in str(info.value).lower()
    _denied(migrated.compensation, "SELECT * FROM app.message")
    _denied(migrated.compensation, "SELECT * FROM app.answer_revision")
    _denied(migrated.compensation, "INSERT INTO app.result DEFAULT VALUES")


def test_coding_tables_invisible_to_runtime(migrated: MigratedDatabase) -> None:
    """INV-0306: interfaces exist; runtime has no grant on them (WP12a owns writes)."""
    _denied(migrated.runtime, "SELECT * FROM app.coding_run")
    _denied(
        migrated.runtime,
        "INSERT INTO app.coding_result (run_id, subject_key, status) "
        "VALUES (gen_random_uuid(), 's', 'coded')",
    )
    with migrated.research.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM app.coding_run")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM app.validation_gate")).scalar_one() == 0


def test_composite_integrity_on_new_tables(migrated: MigratedDatabase) -> None:
    a, b = _new_session(migrated), _new_session(migrated, "pe_demo_v1")
    _world_and_record(migrated, a)
    # B cannot reference A's record in a share or message
    with pytest.raises(IntegrityError), migrated.runtime.begin() as conn:
        conn.execute(
            s.share.insert().values(
                session_id=b,
                share_id=uuid.uuid4(),
                sharer="participant",
                record_id="R1",
                recipients=[],
                source="participant_action",
                delivered_seq=1,
            )
        )
    with pytest.raises(IntegrityError), migrated.runtime.begin() as conn:
        conn.execute(
            s.message.insert().values(
                session_id=b,
                message_id=uuid.uuid4(),
                actor="participant",
                channel="team",
                record_id="R1",
                body="hi",
                accepted_seq=1,
            )
        )


def test_one_final_per_crate_and_revision_must_exist(migrated: MigratedDatabase) -> None:
    session_id = _new_session(migrated)
    command_id = _accepted_command(migrated, session_id)
    with migrated.runtime.begin() as conn:
        conn.execute(
            s.answer_revision.insert().values(
                session_id=session_id,
                crate=2,
                revision=1,
                location_id="marina",
                command_id=command_id,
                accepted_seq=1,
            )
        )
        conn.execute(
            s.answer_final.insert().values(
                session_id=session_id,
                crate=2,
                revision=1,
                location_id="marina",
                finalized_seq=1,
            )
        )
    # a second final for the same crate is impossible
    with pytest.raises(IntegrityError), migrated.runtime.begin() as conn:
        conn.execute(
            s.answer_final.insert().values(
                session_id=session_id,
                crate=2,
                revision=1,
                location_id="depot",
                finalized_seq=1,
            )
        )
    # a final cannot point at a revision that was never accepted
    with pytest.raises(IntegrityError), migrated.runtime.begin() as conn:
        conn.execute(
            s.answer_final.insert().values(
                session_id=session_id,
                crate=3,
                revision=9,
                location_id="depot",
                finalized_seq=1,
            )
        )


def test_reconstruct_matches_committed_session_state(migrated: MigratedDatabase) -> None:
    session_id = _new_session(migrated)
    _accepted_command(migrated, session_id)
    _accepted_command(migrated, session_id)
    with migrated.runtime.connect() as conn:
        rebuilt = reconstruct_session(conn, session_id)
    assert rebuilt["stage"] == "entry"
    assert rebuilt["next_event_seq"] >= 1
    assert rebuilt["open_clock_intervals"] == 0


def test_reconstruct_flags_a_missing_commit(migrated: MigratedDatabase) -> None:
    session_id = _new_session(migrated)
    _accepted_command(migrated, session_id)
    # Simulate a drifted session row (as if a revision bump lost its event): the check must
    # notice rather than fabricate agreement.
    with migrated.migrator.begin() as conn:
        conn.execute(
            text("UPDATE app.session SET stage = 'training' WHERE session_id = :sid"),
            {"sid": session_id},
        )
    with migrated.runtime.connect() as conn, pytest.raises(ReplayMismatchError):
        reconstruct_session(conn, session_id)


def test_export_session_is_consistent_and_excludes_identity(migrated: MigratedDatabase) -> None:
    session_id = _new_session(migrated)
    _accepted_command(migrated, session_id)
    with migrated.runtime.begin() as conn:
        conn.execute(
            s.platform_link.insert().values(
                link_id=uuid.uuid4(),
                session_id=session_id,
                platform="prolific",
                platform_pid="P-SECRET",
            )
        )
    bundle = export_session(migrated.runtime, session_id)
    assert "platform_link" not in bundle
    assert "participant_display" not in bundle
    assert set(bundle) == set(SESSION_TABLES) - EXPORT_EXCLUDED_TABLES
    assert bundle["command_receipt"] and bundle["event"]
    serialized = str(bundle)
    assert "P-SECRET" not in serialized
    # research role can run the same export
    assert export_session(migrated.research, session_id)["session"][0]["session_id"] == session_id


def test_payment_linkage_is_compensation_scoped(migrated: MigratedDatabase) -> None:
    session_id = _new_session(migrated)
    with migrated.runtime.begin() as conn:
        conn.execute(
            s.platform_link.insert().values(
                link_id=uuid.uuid4(),
                session_id=session_id,
                platform="connect",
                platform_pid="C-9",
            )
        )
        conn.execute(
            s.payment_entitlement.insert().values(
                session_id=session_id,
                kind="base",
                cents=400,
                policy_artifact_id="payment.s5_base_only",
                policy_version=1,
            )
        )
    bundle = export_payment_linkage(migrated.compensation, session_id)
    assert bundle["platform_link"][0]["platform_pid"] == "C-9"
    assert bundle["payment_entitlement"][0]["cents"] == 400
    assert set(bundle) == {"platform_link", "payment_entitlement", "completion_receipt", "result"}
    # runtime and research cannot run it (identity read is denied)
    _denied(migrated.runtime, "SELECT * FROM identity.platform_link")
    _denied(migrated.research, "SELECT * FROM identity.platform_link")


def test_manipulation_exposure_lifecycle(migrated: MigratedDatabase) -> None:
    """INV-0305: one exposure row carries scheduled → committed → rendered → acknowledged."""
    session_id = _new_session(migrated)
    exposure_id = uuid.uuid4()
    with migrated.runtime.begin() as conn:
        conn.execute(
            s.manipulation_exposure.insert().values(
                session_id=session_id,
                exposure_id=exposure_id,
                kind="one_minute_warning",
                artifact_id="warning.one_minute_left",
                artifact_version=1,
                due_active_ms=300_000,
            )
        )
        conn.execute(
            s.manipulation_exposure.update()
            .where(s.manipulation_exposure.c.exposure_id == exposure_id)
            .values(status="committed", committed_seq=1)
        )
        conn.execute(
            s.manipulation_exposure.update()
            .where(s.manipulation_exposure.c.exposure_id == exposure_id)
            .values(status="rendered", rendered_seq=1)
        )
        conn.execute(
            s.manipulation_exposure.update()
            .where(s.manipulation_exposure.c.exposure_id == exposure_id)
            .values(status="acknowledged", acknowledged_seq=1)
        )
    with migrated.runtime.connect() as conn:
        row = (
            conn.execute(
                select(s.manipulation_exposure).where(
                    s.manipulation_exposure.c.exposure_id == exposure_id
                )
            )
            .mappings()
            .one()
        )
    assert row["status"] == "acknowledged"
    assert row["committed_seq"] == 1 and row["rendered_seq"] == 1


def test_delivery_receipt_tracks_receive_and_render(migrated: MigratedDatabase) -> None:
    session_id = _new_session(migrated)
    _accepted_command(migrated, session_id)
    with migrated.runtime.begin() as conn:
        conn.execute(
            s.delivery_receipt.insert().values(
                session_id=session_id,
                seq=1,
                recipient="participant",
                kind="initial",
                received_at=db_now(conn),
                client_monotonic_ms=42,
            )
        )
        conn.execute(
            s.delivery_receipt.update()
            .where(s.delivery_receipt.c.session_id == session_id)
            .values(rendered_at=db_now(conn) + timedelta(milliseconds=90))
        )
    with migrated.runtime.connect() as conn:
        row = (
            conn.execute(
                select(s.delivery_receipt).where(s.delivery_receipt.c.session_id == session_id)
            )
            .mappings()
            .one()
        )
    assert row["received_at"] is not None and row["rendered_at"] is not None
    # a receipt must point at a real event of the same session
    other = _new_session(migrated, "pe_demo_v1")
    with pytest.raises(IntegrityError), migrated.runtime.begin() as conn:
        conn.execute(
            s.delivery_receipt.insert().values(
                session_id=other,
                seq=1,
                recipient="participant",
                kind="initial",
                received_at=db_now(conn),
            )
        )
