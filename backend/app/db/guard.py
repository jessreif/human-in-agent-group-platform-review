"""Destructive-test guard (INV-0104, spec §11 "destructive tests ... guard proving it differs
from every live DSN").

A reset is allowed only when ALL of the following hold:
1. ``INVESTIGATORS_TEST_DATABASE_URL`` is set.
2. ``INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS`` is explicitly true.
3. ``APP_MODE`` is not ``live``.
4. The database name contains ``test`` (a naming convention that live databases never use).
5. Its identity (host group, port, database) differs from every known live DSN, including the
   runtime ``DATABASE_URL``, any DSN listed in ``INVESTIGATORS_EXTRA_LIVE_DSNS`` and the
   ``DATABASE_URL`` found in the service ``.env`` when it is readable.
6. After connecting, ``current_database()`` equals the parsed name (no alias/pooler surprise).

Only then may :func:`reset_disposable_schema` drop the Investigators schemas (``app``,
``identity``) and recreate ``public``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db.dsn import DatabaseIdentity, parse_dsn

EXTRA_LIVE_DSNS_VARIABLE = "INVESTIGATORS_EXTRA_LIVE_DSNS"
_TEST_NAME = re.compile(r"test", re.IGNORECASE)
_TRUE = {"1", "true", "yes", "on"}


class UnsafeTestDatabaseError(RuntimeError):
    """The configured test database is not provably disposable. Message is credential-free."""


@dataclass(frozen=True)
class DisposableDatabase:
    dsn: str
    identity: DatabaseIdentity


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUE


def read_env_file_value(path: Path, key: str) -> str | None:
    """Read one ``KEY=value`` from an env file without logging or exporting anything."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            name, _, value = stripped.partition("=")
            if name.strip() == key:
                return value.strip().strip("'\"") or None
    except OSError:
        return None
    return None


def collect_live_dsns(
    environ: dict[str, str] | None = None, env_files: Iterable[Path] = ()
) -> list[str]:
    env = dict(os.environ if environ is None else environ)
    found: list[str] = []
    if env.get("DATABASE_URL"):
        found.append(env["DATABASE_URL"])
    for item in env.get(EXTRA_LIVE_DSNS_VARIABLE, "").split(","):
        if item.strip():
            found.append(item.strip())
    for path in env_files:
        value = read_env_file_value(path, "DATABASE_URL")
        if value:
            found.append(value)
    return found


def assert_disposable(
    test_dsn: str | None,
    *,
    allow_destructive: bool | str | None,
    live_dsns: Iterable[str],
    app_mode: str = "preview",
) -> DisposableDatabase:
    if not test_dsn:
        raise UnsafeTestDatabaseError("INVESTIGATORS_TEST_DATABASE_URL is not set")
    allowed = (
        allow_destructive if isinstance(allow_destructive, bool) else _truthy(allow_destructive)
    )
    if not allowed:
        raise UnsafeTestDatabaseError("INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS is not enabled")
    if app_mode.strip().lower() == "live":
        raise UnsafeTestDatabaseError("destructive database tests are forbidden in live mode")
    try:
        candidate = parse_dsn(test_dsn)
    except ValueError as exc:
        raise UnsafeTestDatabaseError(f"test DSN is invalid: {exc}") from None
    identity = candidate.identity
    if not _TEST_NAME.search(identity.database):
        raise UnsafeTestDatabaseError(
            f"test database name must contain 'test' (got {identity.database!r})"
        )
    live_list = list(live_dsns)
    if not live_list:
        raise UnsafeTestDatabaseError(
            "no live DSN was supplied for comparison; refusing to prove disposability blind"
        )
    for live in live_list:
        try:
            live_identity = parse_dsn(live).identity
        except ValueError:
            raise UnsafeTestDatabaseError("a live DSN could not be parsed for comparison") from None
        if live_identity == identity:
            raise UnsafeTestDatabaseError(f"test database {identity} is the same as a live DSN")
    return DisposableDatabase(dsn=test_dsn, identity=identity)


def verify_connected_database(engine: Engine, expected: DisposableDatabase) -> None:
    with engine.connect() as conn:
        actual: str = conn.execute(text("SELECT current_database()")).scalar_one()
    if actual != expected.identity.database:
        raise UnsafeTestDatabaseError(
            f"connected to {actual!r}, expected {expected.identity.database!r}; refusing reset"
        )


def reset_disposable_schema(engine: Engine, expected: DisposableDatabase) -> None:
    """Drop ``app``/``identity`` and recreate ``public`` in a database proven disposable."""
    verify_connected_database(engine, expected)
    with engine.begin() as conn:
        for schema in ("app", "identity", "public"):
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
