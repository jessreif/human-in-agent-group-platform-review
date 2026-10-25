"""Engine factory and readiness probes (INV-0105).

Probe results never carry exception text to callers: driver errors can contain host names or
credentials. Details are logged server-side through the redacting logger instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.db.dsn import safe_label, sqlalchemy_url

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
UNINITIALIZED = "uninitialized"
VERSION_SCHEMA = "app"
UNKNOWN = "unknown"


def make_engine(
    dsn: str,
    *,
    connect_timeout_seconds: float = 2.0,
    pool_size: int = 5,
    max_overflow: int = 5,
    application_name: str = "investigators",
) -> Engine:
    return create_engine(
        sqlalchemy_url(dsn),
        pool_pre_ping=True,
        hide_parameters=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_recycle=1800,
        connect_args={
            "connect_timeout": max(1, int(connect_timeout_seconds)),
            "application_name": application_name,
        },
    )


@dataclass(frozen=True)
class DatabaseProbe:
    reachable: bool
    schema_revision: str


def probe_database(engine: Engine) -> DatabaseProbe:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            if inspect(conn).has_table("alembic_version", schema=VERSION_SCHEMA):
                row = conn.execute(
                    text(f"SELECT version_num FROM {VERSION_SCHEMA}.alembic_version")  # noqa: S608
                ).first()
                revision = row[0] if row else UNINITIALIZED
            else:
                revision = UNINITIALIZED
        return DatabaseProbe(reachable=True, schema_revision=str(revision))
    except SQLAlchemyError as exc:
        logger.warning(
            "database probe failed",
            extra={"database": safe_label(str(engine.url)), "error_type": type(exc).__name__},
        )
        return DatabaseProbe(reachable=False, schema_revision=UNKNOWN)


def expected_schema_head() -> str | None:
    """The single Alembic head shipped with this build, or ``None`` before WP03 migrations."""
    versions = MIGRATIONS_DIR / "versions"
    if not versions.is_dir() or not any(versions.glob("*.py")):
        return None
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"expected exactly one migration head, found {len(heads)}")
    return heads[0]
