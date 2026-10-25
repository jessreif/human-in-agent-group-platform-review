"""Disposable PostgreSQL harness (INV-0104).

Enabled only when a test DSN and ``INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS=1``
are set. Live DSNs for the comparison come from ``DATABASE_URL``,
``INVESTIGATORS_EXTRA_LIVE_DSNS`` and the env file named by
``INVESTIGATORS_LIVE_ENV_FILE`` (``scripts/test_backend.sh`` points it at the
real service ``.env`` so the proof compares against the actual runtime DSN
without printing it).

Under pytest-xdist each worker reads ``INVESTIGATORS_TEST_DATABASE_URL_GW<n>``
and refuses to fall back to the shared URL. That URL is the server's
maintenance database. Each test gets its own clone (or, for the reset
probes, its own empty database). One worker is one server, so role
connection limits and ``DROP ROLE`` stay local to that worker.

Set ``INVESTIGATORS_REQUIRE_INTEGRATION=1`` to turn a missing configuration
into a failure instead of a skip (used by ``scripts/check.sh`` so integration
evidence cannot silently vanish).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db.dsn import parse_dsn
from app.db.engine import make_engine
from app.db.guard import (
    DisposableDatabase,
    UnsafeTestDatabaseError,
    assert_disposable,
    collect_live_dsns,
    verify_connected_database,
)
from tests.conftest import ORIGINAL_ENV
from tests.integration.db_clone import (
    BOOTSTRAP_TEMPLATE,
    MIGRATED_TEMPLATE,
    CloneCluster,
    MissingWorkerDatabase,
    select_test_dsn,
)
from tests.integration.pg_bootstrap import RoleDsns, apply_bootstrap

BACKEND = Path(__file__).resolve().parents[2]
_RESERVED = frozenset({BOOTSTRAP_TEMPLATE, MIGRATED_TEMPLATE})


def _live_files() -> list[Path]:
    return [Path(p) for p in [ORIGINAL_ENV.get("INVESTIGATORS_LIVE_ENV_FILE", "")] if p]


@pytest.fixture(scope="session")
def disposable_database() -> DisposableDatabase:
    required = ORIGINAL_ENV.get("INVESTIGATORS_REQUIRE_INTEGRATION") == "1"
    try:
        test_dsn = select_test_dsn(ORIGINAL_ENV)
    except MissingWorkerDatabase as exc:
        pytest.fail(
            "xdist worker has no disposable database "
            f"({exc}). Refusing to share one DSN across workers."
        )
    if not test_dsn:
        if required:
            pytest.fail("INVESTIGATORS_REQUIRE_INTEGRATION=1 but no test database is configured")
        pytest.skip("no disposable test database configured")
    return assert_disposable(
        test_dsn,
        allow_destructive=ORIGINAL_ENV.get("INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS"),
        live_dsns=collect_live_dsns(ORIGINAL_ENV, env_files=_live_files()),
        app_mode=ORIGINAL_ENV.get("APP_MODE", "preview"),
    )


@pytest.fixture(scope="session")
def cluster(disposable_database: DisposableDatabase) -> Iterator[CloneCluster]:
    built = CloneCluster(
        disposable_database,
        live_dsns=collect_live_dsns(ORIGINAL_ENV, env_files=_live_files()),
        allow_destructive=ORIGINAL_ENV.get("INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS"),
        app_mode=ORIGINAL_ENV.get("APP_MODE", "preview"),
    )
    try:
        built.ensure_templates()
        yield built
    finally:
        built.close()


def _small_engine(dsn: str) -> Engine:
    # pool 1 + overflow 1 stays under the migrator and compensation limits (3).
    return make_engine(dsn, pool_size=1, max_overflow=1, application_name="investigators-test")


@dataclass
class MigratedDatabase:
    dsns: RoleDsns
    superuser: Engine
    migrator: Engine
    runtime: Engine
    research: Engine
    compensation: Engine

    def dispose(self) -> None:
        for engine in (
            self.superuser,
            self.migrator,
            self.runtime,
            self.research,
            self.compensation,
        ):
            engine.dispose()


def _assert_private(db: MigratedDatabase, maintenance: str) -> None:
    expected = parse_dsn(db.dsns.superuser).database
    if expected == maintenance or expected in _RESERVED:
        raise UnsafeTestDatabaseError(f"refusing to run a test on {expected}")
    with db.superuser.connect() as conn:
        actual = conn.execute(text("SELECT current_database()")).scalar_one()
    if actual != expected:
        raise UnsafeTestDatabaseError(f"connected to {actual!r}, expected {expected!r}")


def _open_roles(
    dsns: RoleDsns, maintenance: str, superuser: Engine | None = None
) -> MigratedDatabase:
    made: list[Engine] = []
    try:
        if superuser is None:
            superuser = _small_engine(dsns.superuser)
        made.append(superuser)
        migrator = _small_engine(dsns.migrator)
        made.append(migrator)
        runtime = _small_engine(dsns.runtime)
        made.append(runtime)
        research = _small_engine(dsns.research)
        made.append(research)
        compensation = _small_engine(dsns.compensation)
        made.append(compensation)
        db = MigratedDatabase(
            dsns=dsns,
            superuser=superuser,
            migrator=migrator,
            runtime=runtime,
            research=research,
            compensation=compensation,
        )
        _assert_private(db, maintenance)
        return db
    except Exception:
        for engine in made:
            engine.dispose()
        raise


def alembic_config() -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    return config


def run_alembic(
    monkeypatch: pytest.MonkeyPatch, migrator_dsn: str, action: str, target: str
) -> None:
    monkeypatch.setenv("INVESTIGATORS_MIGRATION_DATABASE_URL", migrator_dsn)
    getattr(command, action)(alembic_config(), target)


@pytest.fixture
def empty_database(cluster: CloneCluster) -> Iterator[DisposableDatabase]:
    database = cluster.create_empty()
    try:
        if database.identity.database == cluster.maintenance.identity.database:
            raise UnsafeTestDatabaseError("empty database collided with maintenance")
        if database.identity.database in _RESERVED:
            raise UnsafeTestDatabaseError("empty database collided with a template")
        yield database
    finally:
        cluster.drop(database.identity.database)


@pytest.fixture
def disposable_engine(empty_database: DisposableDatabase) -> Iterator[Engine]:
    # The database is created empty. Resetting it here would hide a clone that
    # was pointed at the wrong name, and it is not required for the probe tests.
    engine = _small_engine(empty_database.dsn)
    try:
        verify_connected_database(engine, empty_database)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def bootstrapped(cluster: CloneCluster) -> Iterator[MigratedDatabase]:
    """Roles and schemas from the bootstrap template, with no migrations."""
    database = cluster.clone_bootstrap()
    db: MigratedDatabase | None = None
    try:
        db = _open_roles(
            cluster.role_dsns(database.identity.database), cluster.maintenance.identity.database
        )
        yield db
    finally:
        if db is not None:
            db.dispose()
        cluster.drop(database.identity.database)


@pytest.fixture
def migrated(cluster: CloneCluster) -> Iterator[MigratedDatabase]:
    """A clone of the already-migrated template. Alembic does not re-run."""
    database = cluster.clone_migrated()
    db: MigratedDatabase | None = None
    try:
        db = _open_roles(
            cluster.role_dsns(database.identity.database), cluster.maintenance.identity.database
        )
        yield db
    finally:
        if db is not None:
            db.dispose()
        cluster.drop(database.identity.database)


@pytest.fixture
def serial_bootstrapped(cluster: CloneCluster) -> Iterator[MigratedDatabase]:
    """A private database on a server whose other databases have been dropped.

    ``DROP ROLE`` fails while any database still grants the role, including a
    sealed template. The fixture rebuilds the templates before it returns.
    """
    database = cluster.begin_serial()
    db: MigratedDatabase | None = None
    superuser: Engine | None = None
    try:
        superuser = _small_engine(database.dsn)
        dsns = apply_bootstrap(
            superuser,
            database.dsn,
            database.identity.database,
            passwords=cluster.role_passwords,
        )
        db = _open_roles(dsns, cluster.maintenance.identity.database, superuser=superuser)
        superuser = None
        yield db
    finally:
        if db is not None:
            db.dispose()
        elif superuser is not None:
            superuser.dispose()
        cluster.finish_serial(database.identity.database)
