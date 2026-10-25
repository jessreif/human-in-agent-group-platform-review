"""INV-0301/0302/0308 on real PostgreSQL: migrations as the migrator role, per-role privileges,
append-only tables, cross-session integrity and draw persistence."""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db import schema as s
from app.db.engine import expected_schema_head, probe_database
from app.domain.rng import DrawLog, RandomStreams
from app.domain.schedules import plan_file_order, plan_rating_seats
from app.main import create_app
from app.services.sessions import (
    DrawMismatchError,
    ManifestMismatchError,
    create_session,
    record_draws,
    register_manifest,
)
from app.settings import Settings
from app.timing.clock import VirtualClock
from app.timing.policy import resolve_policy
from tests.integration.conftest import MigratedDatabase, run_alembic
from tests.integration.pg_bootstrap import bootstrap_statements
from tests.wp02 import SEED, bound

# No module-level integration mark. Every case except
# ``test_bootstrap_statements_come_from_the_operator_script`` opens a database.
# That case only parses the operator script. Do not put the module marker back.


def _new_session(db: MigratedDatabase, manifest_id: str = "tp_demo_v1") -> uuid.UUID:
    """One preview batch per manifest (a batch is pinned to exactly one manifest and mode)."""
    return create_session(
        db.runtime,
        bound=bound(manifest_id),
        batch_id=f"preview-{manifest_id.replace('_', '-')}",
        research_pid=f"r-{uuid.uuid4().hex[:12]}",
        seed_hex=SEED,
        build_source_sha256="unset",
    )


def _denied(engine: object, sql: str, params: dict[str, object] | None = None) -> None:
    from sqlalchemy.engine import Engine

    assert isinstance(engine, Engine)
    with pytest.raises(DBAPIError) as info, engine.begin() as conn:
        conn.execute(text(sql), params or {})
    assert getattr(info.value.orig, "sqlstate", None) in {"42501", "25006"}, info.value


def test_bootstrap_statements_come_from_the_operator_script() -> None:
    statements = bootstrap_statements("x_test_db")
    assert any(st.startswith("CREATE ROLE investigators_migrator") for st in statements)
    assert any("ALTER DEFAULT PRIVILEGES" in st for st in statements)
    assert not any(
        st.upper().startswith(("SELECT", "SHOW", "CREATE DATABASE")) for st in statements
    )
    assert all("investigators_db" not in st for st in statements)


@pytest.mark.integration
def test_migrated_head_matches_current_metadata(migrated: MigratedDatabase) -> None:
    # Reflect from a connection whose default schema is not ``app`` (the migrator's search_path
    # starts with app, which Alembic would treat as the unnamed default schema).
    with migrated.superuser.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM app.alembic_version")).scalar_one()
        assert version == expected_schema_head()
        ctx = MigrationContext.configure(
            conn,
            opts={
                "include_schemas": True,
                "compare_type": True,
                "include_name": lambda name, type_, _parent: (
                    (type_ != "schema" or name in {"app", "identity"}) and name != "alembic_version"
                ),
            },
        )
        assert compare_metadata(ctx, s.metadata) == []
        tables = set(inspect(conn).get_table_names(schema="app"))
    assert {t.name for t in s.metadata.sorted_tables if t.schema == "app"} <= tables


@pytest.mark.integration
def test_downgrade_to_base_and_upgrade_again(
    migrated: MigratedDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_alembic(monkeypatch, migrated.dsns.migrator, "downgrade", "base")
    with migrated.migrator.connect() as conn:
        assert set(inspect(conn).get_table_names(schema="app")) == {"alembic_version"}
        assert inspect(conn).get_table_names(schema="identity") == []
    run_alembic(monkeypatch, migrated.dsns.migrator, "upgrade", "head")
    assert probe_database(migrated.runtime).schema_revision == expected_schema_head()


@pytest.mark.integration
@pytest.mark.cluster_serial
def test_migration_refuses_without_roles(
    serial_bootstrapped: MigratedDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The upgrade checks for the INV-0301 roles before creating anything.

    Templates on this worker are dropped first so a sealed database cannot
    keep the role alive. The fixture recreates the role and both templates
    before it returns.
    """
    with serial_bootstrapped.superuser.begin() as conn:
        conn.execute(text("DROP OWNED BY investigators_compensation"))
        conn.execute(text("DROP ROLE investigators_compensation"))
    with pytest.raises(RuntimeError, match="roles missing"):
        run_alembic(monkeypatch, serial_bootstrapped.dsns.migrator, "upgrade", "head")
    with serial_bootstrapped.migrator.connect() as conn:
        assert "session" not in inspect(conn).get_table_names(schema="app")


@pytest.mark.integration
def test_runtime_role_privileges(migrated: MigratedDatabase) -> None:
    session_id = _new_session(migrated)
    with migrated.runtime.begin() as conn:
        conn.execute(
            s.platform_link.insert().values(
                link_id=uuid.uuid4(), session_id=session_id, platform="prolific", platform_pid="P1"
            )
        )
    # insert-only identity: cannot read platform IDs back
    # runtime may read the identity columns needed for eligibility/resume, nothing else
    with migrated.runtime.connect() as conn:
        conn.execute(text("SELECT platform, platform_pid, session_id FROM identity.platform_link"))
    _denied(migrated.runtime, "SELECT study_ref FROM identity.platform_link")
    _denied(migrated.runtime, "SELECT link_id FROM identity.platform_link")
    _denied(migrated.runtime, "SELECT * FROM identity.platform_link")
    # append-only tables: UPDATE/DELETE privileges are not granted to the runtime role
    _denied(migrated.runtime, "UPDATE app.experiment_manifest SET study_key = 'x'")
    _denied(migrated.runtime, "DELETE FROM app.experiment_manifest")
    _denied(migrated.runtime, "TRUNCATE app.event")
    # no DDL
    _denied(migrated.runtime, "CREATE TABLE app.sneaky (id int)")
    # mutable runtime tables stay writable
    with migrated.runtime.begin() as conn:
        conn.execute(
            text("UPDATE app.session SET terminal_reason = NULL WHERE session_id = :sid"),
            {"sid": session_id},
        )


@pytest.mark.integration
def test_research_and_compensation_roles(migrated: MigratedDatabase) -> None:
    session_id = _new_session(migrated)
    with migrated.runtime.begin() as conn:
        conn.execute(
            s.platform_link.insert().values(
                link_id=uuid.uuid4(), session_id=session_id, platform="connect", platform_pid="C1"
            )
        )
    with migrated.research.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM app.session")).scalar_one() == 1
    _denied(migrated.research, "UPDATE app.session SET terminal_reason = 'x'")
    _denied(migrated.research, "SELECT * FROM identity.platform_link")
    with migrated.compensation.connect() as conn:
        assert conn.execute(
            text("SELECT platform_pid FROM identity.platform_link")
        ).scalar_one() == ("C1")
    _denied(migrated.compensation, "SELECT * FROM app.session")
    _denied(
        migrated.compensation,
        "INSERT INTO identity.platform_link (link_id) VALUES (gen_random_uuid())",
    )


@pytest.mark.integration
def test_append_only_trigger_blocks_even_the_owner(migrated: MigratedDatabase) -> None:
    _new_session(migrated)
    with pytest.raises(DBAPIError, match="append-only"), migrated.migrator.begin() as conn:
        conn.execute(text("UPDATE app.experiment_manifest SET study_key = 'tampered'"))
    with pytest.raises(DBAPIError, match="append-only"), migrated.migrator.begin() as conn:
        conn.execute(text("DELETE FROM app.experiment_manifest"))


@pytest.mark.integration
def test_cross_session_references_are_impossible(migrated: MigratedDatabase) -> None:
    a, b = _new_session(migrated), _new_session(migrated, "pe_demo_v1")
    world_a = uuid.uuid4()
    with migrated.runtime.begin() as conn:
        conn.execute(
            s.world_snapshot.insert().values(
                world_id=world_a, session_id=a, generator="g", content={}, content_sha256="0" * 64
            )
        )
    with pytest.raises(IntegrityError), migrated.runtime.begin() as conn:
        conn.execute(
            s.record.insert().values(
                session_id=b,
                record_id="K1-01",
                world_id=world_a,  # A's world under B's session
                crate=1,
                stamp="Yesterday, 9:00 AM",
                body="x",
                event_type="arrival",
                timeline_ordinal=1,
                content_sha256="0" * 64,
            )
        )
    with pytest.raises(IntegrityError), migrated.runtime.begin() as conn:
        conn.execute(
            s.file_membership.insert().values(session_id=b, record_id="K1-01", source="initial")
        )


@pytest.mark.integration
def test_manifest_registration_is_immutable(migrated: MigratedDatabase) -> None:
    b = bound("pe_pilot_v2")
    with migrated.runtime.begin() as conn:
        register_manifest(conn, b)
        register_manifest(conn, b)  # identical: no-op
    forged = dataclasses.replace(b, bundle_sha256="f" * 64)
    with pytest.raises(ManifestMismatchError), migrated.runtime.begin() as conn:
        register_manifest(conn, forged)


@pytest.mark.integration
def test_session_pins_binding_and_hashes(migrated: MigratedDatabase) -> None:
    session_id = _new_session(migrated, "tp_review_first_v2")
    b = bound("tp_review_first_v2")
    with migrated.runtime.connect() as conn:
        row = (
            conn.execute(s.session.select().where(s.session.c.session_id == session_id))
            .mappings()
            .one()
        )
    assert row["stage"] == "entry" and row["revision"] == 0 and row["arm"] is None
    assert row["binding_sha256"] == b.binding_sha256
    assert row["binding"] == b.binding_record()
    assert (row["manifest_sha256"], row["bundle_sha256"]) == (b.manifest_sha256, b.bundle_sha256)


@pytest.mark.integration
def test_realized_draws_persist_and_never_change(migrated: MigratedDatabase) -> None:
    session_id = _new_session(migrated)
    log = DrawLog()
    streams = RandomStreams(SEED, log)
    plan_file_order(streams, [f"K{i}" for i in range(10)])
    plan_rating_seats(resolve_policy(bound("tp_demo_v1"), "high"), streams)
    with migrated.runtime.begin() as conn:
        assert record_draws(conn, session_id, log.records) == len(log.records)
    with migrated.runtime.begin() as conn:
        assert record_draws(conn, session_id, log.records) == 0  # replay is a no-op
    tampered = [dataclasses.replace(log.records[0], value=0.123456)]
    with pytest.raises(DrawMismatchError), migrated.runtime.begin() as conn:
        record_draws(conn, session_id, tampered)
    _denied(migrated.runtime, "UPDATE app.draw_log SET value = 0.5")


@pytest.mark.integration
def test_api_ready_after_migration(
    migrated: MigratedDatabase, make_settings: Callable[..., Settings]
) -> None:
    app = create_app(
        make_settings(DATABASE_URL=migrated.dsns.runtime),
        clock=VirtualClock(),
        engine=migrated.runtime,
        configure_logs=False,
    )
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["checks"]["schema"] == {
        "current": expected_schema_head(),
        "expected_head": expected_schema_head(),
    }
