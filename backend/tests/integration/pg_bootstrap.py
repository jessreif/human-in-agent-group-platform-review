"""Recreate the INV-0301 role/schema/privilege layout inside the disposable test database.

The statements come from ``scripts/db/inv0301_bootstrap.sql`` itself (psql meta-commands,
pre-check/verify queries and CREATE DATABASE are skipped; the database name is substituted), so
the tests exercise exactly what the operator applied to the host cluster. The disposable
database is then owned by ``investigators_migrator``, as ``investigators_db`` is in production.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.engine import Engine

REPO = Path(__file__).resolve().parents[3]
BOOTSTRAP_SQL = REPO / "scripts" / "db" / "inv0301_bootstrap.sql"
MIGRATOR = "investigators_migrator"
RUNTIME = "investigators_user"
RESEARCH = "investigators_research"
COMPENSATION = "investigators_compensation"
ROLES = (MIGRATOR, RUNTIME, RESEARCH, COMPENSATION)
_SKIP_PREFIXES = ("SHOW", "SELECT", "CREATE DATABASE")


def bootstrap_statements(database: str) -> list[str]:
    kept: list[str] = []
    for line in BOOTSTRAP_SQL.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--") or stripped.startswith("\\"):
            continue
        kept.append(stripped)
    statements = []
    for raw in " ".join(kept).split(";"):
        statement = " ".join(raw.split())
        if not statement or statement.upper().startswith(_SKIP_PREFIXES):
            continue
        statements.append(statement.replace("investigators_db", database))
    return statements


@dataclass(frozen=True)
class RoleDsns:
    superuser: str
    migrator: str
    runtime: str
    research: str
    compensation: str


def _with_user(dsn: str, user: str, password: str) -> str:
    parts = urlsplit(dsn)
    host = parts.hostname or "localhost"
    netloc = f"{quote(user)}:{quote(password)}@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def apply_bootstrap(
    super_engine: Engine,
    superuser_dsn: str,
    database: str,
    passwords: Mapping[str, str] | None = None,
) -> RoleDsns:
    """Apply INV-0301 roles to ``database``.

    ``passwords``, when given, must name every role. The clone harness passes
    stable per-container passwords so a later test does not rotate credentials
    other connections are already using. Omitting them keeps the one-database
    random rotation used by callers that own the whole cluster for one test.
    """
    if passwords is None:
        chosen = {role: secrets.token_hex(16) for role in ROLES}
    else:
        missing = [role for role in ROLES if not passwords.get(role)]
        if missing:
            raise ValueError(f"passwords missing for {missing}")
        chosen = {role: passwords[role] for role in ROLES}
    passwords = chosen
    with super_engine.begin() as conn:
        existing = {r[0] for r in conn.execute(text("SELECT rolname FROM pg_roles"))}
        for statement in bootstrap_statements(database):
            if statement.upper().startswith("CREATE ROLE"):
                role = statement.split()[2]
                if role in existing:
                    continue
            conn.execute(text(statement))
        for role, password in passwords.items():
            # hex-only random password: safe to inline (utility statements take no binds)
            conn.execute(text(f"ALTER ROLE {role} PASSWORD '{password}'"))
        conn.execute(text(f'ALTER DATABASE "{database}" OWNER TO {MIGRATOR}'))
    return RoleDsns(
        superuser=superuser_dsn,
        migrator=_with_user(superuser_dsn, MIGRATOR, passwords[MIGRATOR]),
        runtime=_with_user(superuser_dsn, RUNTIME, passwords[RUNTIME]),
        research=_with_user(superuser_dsn, RESEARCH, passwords[RESEARCH]),
        compensation=_with_user(superuser_dsn, COMPENSATION, passwords[COMPENSATION]),
    )
