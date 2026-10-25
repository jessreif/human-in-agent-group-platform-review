"""Core persistence: manifests, sessions, identity link, allocation, consent/stage/clock history,
draw log, world/records/holdings/file membership, command receipts, events, outbox, jobs.

Revision ID: 0001_core
Revises: (none)
Create Date: 2026-09-29

Runs as ``investigators_migrator`` (INV-0301). Tables are created from the frozen snapshot
``migrations/snapshots/schema_0001.py`` so later model edits never change this revision.
Privileges are set per table here rather than relying on the bootstrap's default privileges,
so the result is the same wherever the migration runs. Requires the INV-0301 roles.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None

RUNTIME = "investigators_user"
RESEARCH = "investigators_research"
COMPENSATION = "investigators_compensation"
ROLES = (RUNTIME, RESEARCH, COMPENSATION)


def _snapshot() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "snapshots" / "schema_0001.py"
    spec = importlib.util.spec_from_file_location("investigators_schema_0001", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _require_roles() -> None:
    bind = op.get_bind()
    found = {
        row[0]
        for row in bind.exec_driver_sql(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY(%(names)s)", {"names": list(ROLES)}
        )
    }
    missing = sorted(set(ROLES) - found)
    if missing:
        raise RuntimeError(f"INV-0301 roles missing: {missing}; run scripts/db/inv0301_bootstrap.sql")


def upgrade() -> None:
    _require_roles()
    snap = _snapshot()
    bind = op.get_bind()
    op.execute("CREATE SCHEMA IF NOT EXISTS app")
    op.execute("CREATE SCHEMA IF NOT EXISTS identity")
    snap.metadata.create_all(bind=bind)

    op.execute(
        """
        CREATE FUNCTION app.forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'append-only table %.%: % is not allowed', TG_TABLE_SCHEMA,
            TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
        END $$
        """
    )
    for table in snap.IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON app.{table} "
            "FOR EACH ROW EXECUTE FUNCTION app.forbid_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON app.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION app.forbid_mutation()"
        )

    roles = ", ".join(ROLES)
    for table in snap.metadata.sorted_tables:
        qualified = f"{table.schema}.{table.name}"
        op.execute(f"REVOKE ALL ON {qualified} FROM PUBLIC, {roles}")
        if table.schema == snap.APP:
            privs = (
                snap.RUNTIME_APPEND_ONLY
                if table.name in snap.IMMUTABLE_TABLES
                else snap.RUNTIME_READ_WRITE
            )
            op.execute(f"GRANT {', '.join(privs)} ON {qualified} TO {RUNTIME}")
            op.execute(f"GRANT SELECT ON {qualified} TO {RESEARCH}")
        else:  # identity: runtime may only insert; compensation may only read
            op.execute(f"GRANT INSERT ON {qualified} TO {RUNTIME}")
            op.execute(f"GRANT SELECT ON {qualified} TO {COMPENSATION}")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE app.outbox_outbox_id_seq TO {RUNTIME}")
    op.execute(f"REVOKE ALL ON app.alembic_version FROM PUBLIC, {roles}")
    op.execute(f"GRANT SELECT ON app.alembic_version TO {RUNTIME}, {RESEARCH}")
    op.execute(f"REVOKE ALL ON FUNCTION app.forbid_mutation() FROM PUBLIC")


def downgrade() -> None:
    snap = _snapshot()
    for table in snap.IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON app.{table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON app.{table}")
    snap.metadata.drop_all(bind=op.get_bind())
    op.execute("DROP FUNCTION IF EXISTS app.forbid_mutation()")
