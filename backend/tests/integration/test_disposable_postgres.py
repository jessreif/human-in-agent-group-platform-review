from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.db.dsn import parse_dsn
from app.db.engine import expected_schema_head, make_engine, probe_database
from app.db.guard import DisposableDatabase, UnsafeTestDatabaseError, reset_disposable_schema
from app.main import create_app
from app.settings import Settings
from app.timing.clock import VirtualClock
from app.workers import main as worker
from tests.integration.conftest import MigratedDatabase
from tests.integration.db_clone import BOOTSTRAP_TEMPLATE, MIGRATED_TEMPLATE, CloneCluster

pytestmark = pytest.mark.integration


def test_connected_database_is_the_guarded_one(
    disposable_engine: Engine, empty_database: DisposableDatabase
) -> None:
    with disposable_engine.connect() as conn:
        assert conn.execute(text("SELECT current_database()")).scalar_one() == (
            empty_database.identity.database
        )
    assert empty_database.identity.database not in {
        "investigators_test_db",
        BOOTSTRAP_TEMPLATE,
        MIGRATED_TEMPLATE,
    }


def test_reset_really_drops_state(
    disposable_engine: Engine, empty_database: DisposableDatabase
) -> None:
    with disposable_engine.begin() as conn:
        conn.execute(text("CREATE TABLE leftover (id int)"))
    reset_disposable_schema(disposable_engine, empty_database)
    with disposable_engine.connect() as conn:
        assert not inspect(conn).has_table("leftover")


def test_reset_refuses_when_connected_name_differs(disposable_engine: Engine) -> None:
    from app.db.dsn import DatabaseIdentity

    wrong = DisposableDatabase(dsn="unused", identity=DatabaseIdentity("x", 1, "some_other_test"))
    with pytest.raises(UnsafeTestDatabaseError, match="refusing reset"):
        reset_disposable_schema(disposable_engine, wrong)


def test_probe_reports_uninitialized_schema(disposable_engine: Engine) -> None:
    probe = probe_database(disposable_engine)
    assert probe.reachable is True
    assert probe.schema_revision == "uninitialized"


def test_api_ready_against_disposable_database(
    disposable_engine: Engine,
    empty_database: DisposableDatabase,
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(DATABASE_URL=empty_database.dsn)
    app = create_app(settings, clock=VirtualClock(), engine=disposable_engine, configure_logs=False)
    with TestClient(app) as client:
        response = client.get("/ready")
    # The build ships migrations; an un-migrated database is reachable but not ready.
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {
            "database": "ok",
            "schema": {"current": "uninitialized", "expected_head": expected_schema_head()},
        },
    }


def test_migrated_clone_is_not_the_maintenance_database(
    migrated: MigratedDatabase, disposable_database: DisposableDatabase
) -> None:
    name = parse_dsn(migrated.dsns.runtime).database
    with migrated.runtime.connect() as conn:
        actual = conn.execute(text("SELECT current_database()")).scalar_one()
    assert actual == name
    assert actual != disposable_database.identity.database
    assert actual not in {BOOTSTRAP_TEMPLATE, MIGRATED_TEMPLATE}


def test_clones_do_not_share_rows(cluster: CloneCluster) -> None:
    first = cluster.clone_migrated()
    second: DisposableDatabase | None = None
    left: Engine | None = None
    right: Engine | None = None
    try:
        second = cluster.clone_migrated()
        left = make_engine(
            first.dsn, pool_size=1, max_overflow=0, application_name="investigators-test"
        )
        right = make_engine(
            second.dsn, pool_size=1, max_overflow=0, application_name="investigators-test"
        )
        with left.begin() as conn:
            conn.execute(text("CREATE TABLE public.leak_probe (id int)"))
            conn.execute(text("INSERT INTO public.leak_probe VALUES (1)"))
        with right.connect() as conn:
            assert not inspect(conn).has_table("leak_probe", schema="public")
    finally:
        if left is not None:
            left.dispose()
        if right is not None:
            right.dispose()
        cluster.drop(first.identity.database)
        if second is not None:
            cluster.drop(second.identity.database)


def test_worker_sees_database(
    disposable_database: DisposableDatabase,
    make_settings: Callable[..., Settings],
    tmp_path: Path,
) -> None:
    state = worker.run(
        make_settings(DATABASE_URL=disposable_database.dsn),
        stop=threading.Event(),
        clock=VirtualClock(),
        heartbeat_path=tmp_path / "hb.json",
        poll_s=0,
        max_beats=1,
    )
    assert state.database_reachable is True


def _managed_datnames(cluster: CloneCluster) -> set[str]:
    engine = make_engine(
        cluster.maintenance.dsn,
        pool_size=1,
        max_overflow=0,
        application_name="investigators-test",
    )
    try:
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    text(
                        "SELECT datname FROM pg_database "
                        "WHERE starts_with(datname, 'investigators_test_')"
                    )
                )
                .scalars()
                .all()
            )
        return set(rows)
    finally:
        engine.dispose()


def test_failed_clone_leaves_templates_and_does_not_rotate_passwords(
    cluster: CloneCluster, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A body failure and a failure after CREATE DATABASE both drop the clone.

    Role passwords stay the container's original map. The live-DSN refusal
    lives in tests/test_dsn_guard.py and is not re-proved with a real .env here.
    """
    passwords = dict(cluster.role_passwords)
    created = cluster.clone_migrated()
    try:
        raise RuntimeError("clone test body failed")
    except RuntimeError:
        cluster.drop(created.identity.database)
    assert cluster.role_passwords == passwords

    def fail_after_create(self: CloneCluster, database: str) -> DisposableDatabase:
        raise RuntimeError("fixture failed after CREATE DATABASE")

    monkeypatch.setattr(CloneCluster, "_prove", fail_after_create)
    with pytest.raises(RuntimeError, match="after CREATE DATABASE"):
        cluster.clone_migrated()
    assert cluster.role_passwords == passwords
    assert _managed_datnames(cluster) == {
        cluster.maintenance.identity.database,
        BOOTSTRAP_TEMPLATE,
        MIGRATED_TEMPLATE,
    }
