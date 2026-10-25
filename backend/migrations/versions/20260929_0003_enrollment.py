"""Enrollment support: session credentials (hashed) and the one-writer lease (INV-0402/0407),
plus the platform-link identity index used by eligibility checks (INV-0404).

Revision ID: 0003_enrollment
Revises: 0002_records
Create Date: 2026-09-29

Runs as ``investigators_migrator``. ``identity.session_credential`` is created from the frozen
snapshot; ``app.session`` gains ``writer_id``/``writer_lease_until`` by ALTER because the table
exists since 0001 (create_all cannot add columns). Privileges are explicit: the credential
table is identity-side auth material, so research and compensation get no grant — unlike
``platform_link``, which compensation may read.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op
from sqlalchemy import text

revision = "0003_enrollment"
down_revision = "0002_records"
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


def upgrade() -> None:
    snap = _load("0003")
    bind = op.get_bind()
    snap.metadata.create_all(bind=bind, checkfirst=True)

    # Columns on pre-existing tables that create_all cannot add.
    op.execute("ALTER TABLE app.session ADD COLUMN IF NOT EXISTS writer_id UUID")
    op.execute("ALTER TABLE app.session ADD COLUMN IF NOT EXISTS writer_lease_until TIMESTAMPTZ")
    op.execute(
        "ALTER TABLE app.session ADD CONSTRAINT ck_session_writer_lease "
        "CHECK ((writer_id IS NULL) = (writer_lease_until IS NULL))"
    )
    # Repeat-participation eligibility queries (platform, platform_pid); the retention purge
    # index already exists from 0002.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_platform_link_identity "
        "ON identity.platform_link (platform, platform_pid)"
    )

    # confidence_response gains a revision component so a re-rating before the stage closes is
    # a new append-only row (same pattern as answer_revision) instead of a forbidden UPDATE.
    op.execute("ALTER TABLE app.confidence_response DROP CONSTRAINT pk_confidence_response")
    op.execute("ALTER TABLE app.confidence_response ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE app.confidence_response ALTER COLUMN revision DROP DEFAULT")
    op.execute(
        "ALTER TABLE app.confidence_response "
        "ADD CONSTRAINT pk_confidence_response PRIMARY KEY (session_id, crate, revision)"
    )
    op.execute(
        "ALTER TABLE app.confidence_response "
        "ADD CONSTRAINT ck_confidence_response_revision CHECK (revision >= 1)"
    )

    # FKs into command_receipt must be deferrable: handlers write request/answer_revision
    # rows during the command, while the receipt itself lands at the end of the same
    # transaction (same pattern as fk_event_command_receipt from 0001).
    for table, name in (
        ("request", "fk_request_command"),
        ("answer_revision", "fk_answer_revision_command"),
    ):
        op.execute(
            f"ALTER TABLE app.{table} ALTER CONSTRAINT {name} DEFERRABLE INITIALLY DEFERRED"
        )

    # Privileges for the one new table. The bootstrap default gave compensation SELECT on all
    # identity tables; credentials are not payment linkage, so revoke then grant only runtime.
    op.execute(f"REVOKE ALL ON identity.session_credential FROM PUBLIC, {', '.join(ROLES)}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON identity.session_credential TO {RUNTIME}")
    # Eligibility and resume look up an existing link by (platform, platform_pid) and need
    # the owning session_id — column-scoped read for the runtime role, nothing else.
    op.execute(
        f"GRANT SELECT (platform, platform_pid, session_id) ON identity.platform_link TO {RUNTIME}"
    )


def downgrade() -> None:
    for table, name in (
        ("request", "fk_request_command"),
        ("answer_revision", "fk_answer_revision_command"),
    ):
        op.execute(
            f"ALTER TABLE app.{table} ALTER CONSTRAINT {name} NOT DEFERRABLE"
        )
    op.execute("ALTER TABLE app.confidence_response DROP CONSTRAINT pk_confidence_response")
    op.execute(
        "ALTER TABLE app.confidence_response "
        "DROP CONSTRAINT IF EXISTS ck_confidence_response_revision"
    )
    op.execute("ALTER TABLE app.confidence_response DROP COLUMN revision")
    op.execute(
        "ALTER TABLE app.confidence_response "
        "ADD CONSTRAINT pk_confidence_response PRIMARY KEY (session_id, crate)"
    )
    op.execute(
        f"REVOKE SELECT (platform, platform_pid, session_id) "
        f"ON identity.platform_link FROM {RUNTIME}"
    )
    op.execute("DROP INDEX IF EXISTS identity.ix_platform_link_identity")
    op.execute("ALTER TABLE app.session DROP CONSTRAINT IF EXISTS ck_session_writer_lease")
    op.execute("ALTER TABLE app.session DROP COLUMN IF EXISTS writer_lease_until")
    op.execute("ALTER TABLE app.session DROP COLUMN IF EXISTS writer_id")
    op.execute(text("DROP TABLE IF EXISTS identity.session_credential"))
