"""Alembic environment for the Investigators database (INV-0301).

The DSN comes from ``INVESTIGATORS_MIGRATION_DATABASE_URL`` (the privileged
``investigators_migrator`` role). It is deliberately *not* a service setting: the API and worker
containers never receive it (they get ``.env``); ``scripts/migrate.sh`` passes it from the
separate ``.env.migrate`` file. Outside live mode it may fall back to ``DATABASE_URL`` for local
experiments only. It is never logged.

The version table lives in the ``app`` schema; target metadata is ``app.db.schema.metadata``.
"""

from __future__ import annotations

import os
from typing import Any

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db.dsn import sqlalchemy_url
from app.db.schema import metadata
from app.settings import load_settings

config = context.config
target_metadata = metadata
VERSION_SCHEMA = "app"


def _migration_dsn() -> str:
    explicit = os.environ.get("INVESTIGATORS_MIGRATION_DATABASE_URL")
    if explicit:
        return explicit
    settings = load_settings()
    if settings.APP_MODE.value == "live":
        raise RuntimeError("live migrations require INVESTIGATORS_MIGRATION_DATABASE_URL")
    return settings.DATABASE_URL.get_secret_value()


def _options() -> dict[str, Any]:
    return {
        "target_metadata": target_metadata,
        "compare_type": True,
        "include_schemas": True,
        "version_table_schema": VERSION_SCHEMA,
    }


def run_migrations_offline() -> None:
    context.configure(url=sqlalchemy_url(_migration_dsn()), literal_binds=True, **_options())
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = {"sqlalchemy.url": sqlalchemy_url(_migration_dsn())}
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS app")
        connection.commit()
        context.configure(connection=connection, **_options())
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
