"""Per-worker disposable databases cloned from a sealed template.

Each xdist worker has its own PostgreSQL (see ``scripts/test_backend.sh``).
The session URL points at that server's maintenance database
(``investigators_test_db``). Tests do not share it. Ordinary tests run on
``CREATE DATABASE ... TEMPLATE`` clones, so Alembic and role passwords run
once per server rather than once per test.

Role ``CONNECTION LIMIT``s are cluster-global and tiny (the migrator allows
3). A worker therefore never shares a server with another worker. The one
test that ``DROP ROLE``s uses ``begin_serial``, which removes the templates
on that server first; their grants would otherwise keep the role alive.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlsplit, urlunsplit

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.db.dsn import parse_dsn, sqlalchemy_url
from app.db.engine import make_engine
from app.db.guard import DisposableDatabase, UnsafeTestDatabaseError, assert_disposable
from tests.integration.pg_bootstrap import (
    COMPENSATION,
    MIGRATOR,
    RESEARCH,
    ROLES,
    RUNTIME,
    RoleDsns,
    _with_user,
    apply_bootstrap,
    bootstrap_statements,
)

BACKEND = Path(__file__).resolve().parents[2]
BOOTSTRAP_TEMPLATE = "investigators_test_bootstrap"
MIGRATED_TEMPLATE = "investigators_test_migrated"
_NAME = re.compile(r"^investigators_test_[a-z0-9_]+$")
_MAX_IDENT = 63
_T = TypeVar("_T")


class MissingWorkerDatabase(RuntimeError):
    """An xdist worker was not given its own disposable database URL."""


def select_test_dsn(environ: Mapping[str, str]) -> str | None:
    """Pick the maintenance URL for this process.

    An xdist worker (``PYTEST_XDIST_WORKER=gw0``) must use
    ``INVESTIGATORS_TEST_DATABASE_URL_GW0``. It never falls back to the
    shared URL. A process that is not a worker uses
    ``INVESTIGATORS_TEST_DATABASE_URL``.
    """
    worker = environ.get("PYTEST_XDIST_WORKER", "").strip()
    if worker:
        key = "INVESTIGATORS_TEST_DATABASE_URL_" + worker.upper()
        dsn = environ.get(key, "").strip()
        if not dsn:
            raise MissingWorkerDatabase(key)
        return dsn
    shared = environ.get("INVESTIGATORS_TEST_DATABASE_URL", "").strip()
    return shared or None


def worker_token(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    raw = env.get("PYTEST_XDIST_WORKER", "main").lower()
    token = re.sub(r"[^a-z0-9]", "", raw)
    return token or "main"


def role_passwords(super_password: str) -> dict[str, str]:
    """Stable for one server: derived from that server's superuser password.

    Re-applying bootstrap then sets the same password, so a later test does
    not rotate credentials out from under a connection that is already open.
    """
    if not super_password:
        raise ValueError("superuser password is empty")
    return {
        role: hashlib.sha256(
            f"investigators-test-role\n{super_password}\n{role}".encode()
        ).hexdigest()
        for role in ROLES
    }


def _ident(name: str) -> str:
    if not _NAME.fullmatch(name) or len(name) > _MAX_IDENT:
        raise ValueError("refusing a database name outside the disposable test pattern")
    return name


def checked_drop_name(name: str, maintenance: str) -> str:
    ident = _ident(name)
    if ident == maintenance:
        raise ValueError("refusing to drop the maintenance database")
    return ident


def database_local_statements(database: str) -> list[str]:
    """Grants and per-database role settings. Schemas are copied by TEMPLATE."""
    ident = _ident(database)
    kept: list[str] = []
    for statement in bootstrap_statements(ident):
        upper = statement.upper()
        if " ON DATABASE " in upper or " IN DATABASE " in upper:
            kept.append(statement)
    if not any("GRANT CONNECT" in statement.upper() for statement in kept):
        raise RuntimeError("bootstrap script has no CONNECT grant")
    return kept


def _quiet(exc: SQLAlchemyError) -> str:
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    primary = getattr(diag, "message_primary", None)
    if isinstance(primary, str) and primary.strip():
        return primary.strip()
    return type(exc).__name__


def _execute(conn: Connection, sql: str, params: Mapping[str, str] | None = None) -> None:
    try:
        conn.execute(text(sql), dict(params or {}))
    except SQLAlchemyError as exc:
        raise RuntimeError(_quiet(exc)) from None


def _exists(conn: Connection, name: str) -> bool:
    found = conn.execute(
        text("SELECT 1 FROM pg_database WHERE datname = :name"),
        {"name": name},
    ).scalar()
    return found is not None


def _terminate(conn: Connection, name: str) -> None:
    _execute(
        conn,
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = :name AND pid <> pg_backend_pid()",
        {"name": name},
    )


def _alembic_upgrade(migrator_dsn: str) -> None:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    previous = os.environ.get("INVESTIGATORS_MIGRATION_DATABASE_URL")
    os.environ["INVESTIGATORS_MIGRATION_DATABASE_URL"] = migrator_dsn
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("INVESTIGATORS_MIGRATION_DATABASE_URL", None)
        else:
            os.environ["INVESTIGATORS_MIGRATION_DATABASE_URL"] = previous


class CloneCluster:
    """One disposable server. Templates are sealed; tests run on clones."""

    def __init__(
        self,
        maintenance: DisposableDatabase,
        *,
        live_dsns: list[str],
        allow_destructive: bool | str | None,
        app_mode: str,
        worker: str | None = None,
    ) -> None:
        self.maintenance = maintenance
        parsed = parse_dsn(maintenance.dsn)
        if not parsed.password:
            raise UnsafeTestDatabaseError("test DSN has no password")
        self.role_passwords = role_passwords(parsed.password)
        self._live_dsns = list(live_dsns)
        self._allow = allow_destructive
        self._app_mode = app_mode
        self._worker = worker if worker is not None else worker_token()
        self._seq = 0
        self._lock = threading.RLock()

    def dsn_for(self, database: str) -> str:
        ident = _ident(database)
        parts = urlsplit(self.maintenance.dsn)
        return urlunsplit((parts.scheme, parts.netloc, f"/{ident}", parts.query, parts.fragment))

    def role_dsns(self, database: str) -> RoleDsns:
        dsn = self.dsn_for(database)
        passwords = self.role_passwords
        return RoleDsns(
            superuser=dsn,
            migrator=_with_user(dsn, MIGRATOR, passwords[MIGRATOR]),
            runtime=_with_user(dsn, RUNTIME, passwords[RUNTIME]),
            research=_with_user(dsn, RESEARCH, passwords[RESEARCH]),
            compensation=_with_user(dsn, COMPENSATION, passwords[COMPENSATION]),
        )

    def ensure_templates(self) -> None:
        with self._lock:
            self._ensure_templates()

    def create_empty(self) -> DisposableDatabase:
        """A database with no Investigators schemas. Not a template."""
        with self._lock:
            name = self._next_name("e")
            try:
                self._create_plain(name)
                return self._prove(name)
            except Exception:
                self._drop_quietly(name)
                raise

    def clone_bootstrap(self) -> DisposableDatabase:
        with self._lock:
            return self._clone(BOOTSTRAP_TEMPLATE)

    def clone_migrated(self) -> DisposableDatabase:
        with self._lock:
            return self._clone(MIGRATED_TEMPLATE)

    def drop(self, name: str) -> None:
        with self._lock:
            self._drop(name)

    def begin_serial(self) -> DisposableDatabase:
        """Drop templates on this server so ``DROP ROLE`` can succeed."""
        with self._lock:
            self._drop_all_managed()
            name = self._next_name("s")
            try:
                self._create_plain(name)
                return self._prove(name)
            except Exception:
                self._drop_quietly(name)
                raise

    def finish_serial(self, name: str) -> None:
        with self._lock:
            try:
                self._drop(name)
            finally:
                self._ensure_templates()

    def close(self) -> None:
        with self._lock:
            self._drop(BOOTSTRAP_TEMPLATE)
            self._drop(MIGRATED_TEMPLATE)

    def _next_name(self, kind: str) -> str:
        self._seq += 1
        name = f"investigators_test_{kind}_{self._worker}_{self._seq}"
        return _ident(name)

    def _prove(self, database: str) -> DisposableDatabase:
        proven = assert_disposable(
            self.dsn_for(database),
            allow_destructive=self._allow,
            live_dsns=self._live_dsns,
            app_mode=self._app_mode,
        )
        if proven.identity.database != database:
            raise UnsafeTestDatabaseError("clone identity does not match the requested database")
        return proven

    def _admin_engine(self) -> Engine:
        return create_engine(
            sqlalchemy_url(self.maintenance.dsn),
            isolation_level="AUTOCOMMIT",
            poolclass=NullPool,
            hide_parameters=True,
            connect_args={
                "connect_timeout": 5,
                "application_name": "investigators-test-admin",
            },
        )

    def _with_admin(self, body: Callable[[Connection], _T]) -> _T:
        admin = self._admin_engine()
        try:
            with admin.connect() as conn:
                return body(conn)
        finally:
            admin.dispose()

    def _both_present(self) -> bool:
        def look(conn: Connection) -> bool:
            rows = (
                conn.execute(
                    text(
                        "SELECT datname FROM pg_database WHERE datname IN (:bootstrap, :migrated)"
                    ),
                    {"bootstrap": BOOTSTRAP_TEMPLATE, "migrated": MIGRATED_TEMPLATE},
                )
                .scalars()
                .all()
            )
            return set(rows) == {BOOTSTRAP_TEMPLATE, MIGRATED_TEMPLATE}

        return self._with_admin(look)

    def _ensure_templates(self) -> None:
        if self._both_present():
            return
        try:
            self._drop(BOOTSTRAP_TEMPLATE)
            self._drop(MIGRATED_TEMPLATE)
            self._create_plain(BOOTSTRAP_TEMPLATE)
            self._bootstrap_template()
            self._create_from_template(MIGRATED_TEMPLATE, BOOTSTRAP_TEMPLATE)
            self._apply_database_local(MIGRATED_TEMPLATE)
            self._upgrade(MIGRATED_TEMPLATE)
            self._terminate_name(BOOTSTRAP_TEMPLATE)
            self._terminate_name(MIGRATED_TEMPLATE)
            self._seal(BOOTSTRAP_TEMPLATE)
            self._seal(MIGRATED_TEMPLATE)
        except Exception:
            self._drop_quietly(BOOTSTRAP_TEMPLATE)
            self._drop_quietly(MIGRATED_TEMPLATE)
            raise

    def _bootstrap_template(self) -> None:
        dsn = self.dsn_for(BOOTSTRAP_TEMPLATE)
        engine = make_engine(
            dsn, pool_size=1, max_overflow=0, application_name="investigators-test"
        )
        try:
            apply_bootstrap(engine, dsn, BOOTSTRAP_TEMPLATE, passwords=self.role_passwords)
        finally:
            engine.dispose()
        self._terminate_name(BOOTSTRAP_TEMPLATE)

    def _upgrade(self, database: str) -> None:
        try:
            _alembic_upgrade(self.role_dsns(database).migrator)
        finally:
            self._terminate_name(database)

    def _clone(self, template: str) -> DisposableDatabase:
        self._ensure_templates()
        name = self._next_name("c")
        try:
            self._create_from_template(name, template)
            self._apply_database_local(name)
            return self._prove(name)
        except Exception:
            self._drop_quietly(name)
            raise

    def _create_plain(self, name: str) -> None:
        ident = _ident(name)
        if ident == self.maintenance.identity.database:
            raise ValueError("refusing to recreate the maintenance database")

        def create(conn: Connection) -> None:
            _execute(conn, f'CREATE DATABASE "{ident}"')

        self._with_admin(create)

    def _create_from_template(self, name: str, template: str) -> None:
        ident = _ident(name)
        source = _ident(template)

        def create(conn: Connection) -> None:
            _terminate(conn, source)
            _execute(conn, f'CREATE DATABASE "{ident}" TEMPLATE "{source}" OWNER {MIGRATOR}')

        self._with_admin(create)

    def _apply_database_local(self, name: str) -> None:
        statements = database_local_statements(name)

        def apply(conn: Connection) -> None:
            for statement in statements:
                _execute(conn, statement)

        self._with_admin(apply)

    def _seal(self, name: str) -> None:
        ident = _ident(name)

        def seal(conn: Connection) -> None:
            _terminate(conn, ident)
            _execute(
                conn, f'ALTER DATABASE "{ident}" WITH IS_TEMPLATE true ALLOW_CONNECTIONS false'
            )

        self._with_admin(seal)

    def _terminate_name(self, name: str) -> None:
        ident = _ident(name)

        def run(conn: Connection) -> None:
            _terminate(conn, ident)

        self._with_admin(run)

    def _drop(self, name: str) -> None:
        ident = checked_drop_name(name, self.maintenance.identity.database)
        last: RuntimeError | None = None
        for attempt in range(5):
            try:
                self._drop_once(ident)
            except RuntimeError as exc:
                last = exc
                time.sleep(0.2 * (attempt + 1))
                continue
            return
        if last is not None:
            raise last

    def _drop_quietly(self, name: str) -> None:
        try:
            self._drop(name)
        except (RuntimeError, ValueError):
            return

    def _drop_once(self, ident: str) -> None:
        def attempt(conn: Connection) -> None:
            if not _exists(conn, ident):
                return
            _execute(
                conn, f'ALTER DATABASE "{ident}" WITH IS_TEMPLATE false ALLOW_CONNECTIONS true'
            )
            _terminate(conn, ident)
            _execute(conn, f'DROP DATABASE "{ident}"')

        self._with_admin(attempt)

    def _drop_all_managed(self) -> None:
        maintenance = self.maintenance.identity.database

        def names(conn: Connection) -> list[str]:
            rows = conn.execute(
                text(
                    "SELECT datname FROM pg_database "
                    "WHERE starts_with(datname, 'investigators_test_') "
                    "AND datname <> :maintenance"
                ),
                {"maintenance": maintenance},
            ).scalars()
            return list(rows)

        for name in self._with_admin(names):
            if _NAME.fullmatch(name):
                self._drop(name)
