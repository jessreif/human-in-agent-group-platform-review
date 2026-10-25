"""Research records: messages/requests/shares/episodes, delivery receipts, knowledge snapshots,
agent state, claims, model calls and displayed output; answer revisions/finals, confidence,
manipulation exposures, item responses, results, entitlements, completion, data-use, incidents,
admin audit and redaction; coding-run/result/gate interfaces (constraints mature in WP12a).

Revision ID: 0002_records
Revises: 0001_core
Create Date: 2026-09-29

Runs as ``investigators_migrator`` (INV-0301). Builds from the frozen snapshot
``migrations/snapshots/schema_0002.py``; only tables absent after 0001 are created, and only
the new append-only tables get mutation triggers. Privileges are set per table here rather
than relying on the bootstrap's default privileges, so the result is the same wherever the
migration runs.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0002_records"
down_revision = "0001_core"
branch_labels = None
depends_on = None

RUNTIME = "investigators_user"
RESEARCH = "investigators_research"
COMPENSATION = "investigators_compensation"
ROLES = (RUNTIME, RESEARCH, COMPENSATION)


def _load(name: str) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "snapshots" / f"schema_{name}.py"
    spec = importlib.util.spec_from_file_location(f"investigators_schema_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _new_tables(snap: ModuleType) -> list[str]:
    """Tables this revision owns: in snapshot 0002 but not in the frozen 0001 snapshot."""
    prior = {t.name for t in _load("0001").metadata.sorted_tables}
    return [t.name for t in snap.metadata.sorted_tables if t.name not in prior]


def upgrade() -> None:
    snap = _load("0002")
    bind = op.get_bind()
    new_tables = set(_new_tables(snap))
    # create_all is checkfirst: pre-existing 0001 tables are untouched; new tables arrive with
    # their own indexes.
    snap.metadata.create_all(bind=bind)

    # The event log records the session revision it commits under so replay can reconstruct
    # revision exactly (job finishes bump the revision without writing a command receipt).
    op.execute(
        "ALTER TABLE app.event ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 0"
    )
    op.execute("ALTER TABLE app.scheduled_job ADD COLUMN IF NOT EXISTS finished_revision BIGINT")
    op.execute(
        "ALTER TABLE app.scheduled_job ADD CONSTRAINT ck_scheduled_job_finished_rev "
        "CHECK ((status IN ('pending', 'claimed')) = (finished_revision IS NULL))"
    )
    # Indexes that target tables created by 0001 (create_all skips existing tables).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_platform_link_purge "
        "ON identity.platform_link (purge_after) WHERE purge_after IS NOT NULL"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_session_batch ON app.session (batch_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_event_session_type ON app.event (session_id, type)"
    )

    for table in sorted(new_tables & set(snap.IMMUTABLE_TABLES)):
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
        if table.name not in new_tables:
            continue
        qualified = f"{table.schema}.{table.name}"
        op.execute(f"REVOKE ALL ON {qualified} FROM PUBLIC, {roles}")
        if table.name in snap.RUNTIME_NO_ACCESS:
            op.execute(f"GRANT SELECT ON {qualified} TO {RESEARCH}")
            continue
        privs = (
            snap.RUNTIME_APPEND_ONLY
            if table.name in snap.IMMUTABLE_TABLES
            else snap.RUNTIME_READ_WRITE
        )
        op.execute(f"GRANT {', '.join(privs)} ON {qualified} TO {RUNTIME}")
        op.execute(f"GRANT SELECT ON {qualified} TO {RESEARCH}")
        if table.name in snap.COMPENSATION_SELECT:
            op.execute(f"GRANT SELECT ON {qualified} TO {COMPENSATION}")
        if table.name in snap.COMPENSATION_UPDATE_COLUMNS:
            cols = ", ".join(snap.COMPENSATION_UPDATE_COLUMNS[table.name])
            op.execute(f"GRANT UPDATE ({cols}) ON {qualified} TO {COMPENSATION}")


def downgrade() -> None:
    snap = _load("0002")
    bind = op.get_bind()
    new_tables = set(_new_tables(snap))
    for table in sorted(new_tables & set(snap.IMMUTABLE_TABLES)):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON app.{table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON app.{table}")
    for table in reversed(snap.metadata.sorted_tables):
        if table.name in new_tables:
            table.drop(bind=bind, checkfirst=True)
    op.execute("DROP INDEX IF EXISTS identity.ix_platform_link_purge")
    op.execute("DROP INDEX IF EXISTS app.ix_session_batch")
    op.execute("DROP INDEX IF EXISTS app.ix_event_session_type")
