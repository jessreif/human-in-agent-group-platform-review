"""Batch groups (2026-10-08 four-condition launch): one platform link randomizing across
several member batches, each pinned to its own manifest.

``app.batch_group`` + ``app.batch_group_member`` define the group; ``app.session`` gains a
nullable ``batch_group_id`` naming the group a session entered through. Allocation rows stay
in ``app.allocation`` (keyed by the member batch, with group-level block/slot indices).

Revision ID: 0009_batch_group
Revises: 0008_batch_rename
Create Date: 2026-10-08
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0009_batch_group"
down_revision = "0008_batch_rename"
branch_labels = None
depends_on = None

RUNTIME = "investigators_user"
RESEARCH = "investigators_research"
COMPENSATION = "investigators_compensation"
ROLES = (RUNTIME, RESEARCH, COMPENSATION)
TABLES = ("batch_group", "batch_group_member")


def _load(name: str) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "snapshots" / f"schema_{name}.py"
    spec = importlib.util.spec_from_file_location(f"investigators_schema_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    snap = _load("0009")
    roles = ", ".join(ROLES)
    for name in TABLES:
        snap.metadata.tables[f"app.{name}"].create(bind=op.get_bind(), checkfirst=True)
        op.execute(f"REVOKE ALL ON app.{name} FROM PUBLIC, {roles}")
        op.execute(f"GRANT {', '.join(snap.RUNTIME_READ_WRITE)} ON app.{name} TO {RUNTIME}")
        op.execute(f"GRANT SELECT ON app.{name} TO {RESEARCH}")
    op.execute("ALTER TABLE app.session ADD COLUMN batch_group_id text")
    op.execute(
        "ALTER TABLE app.session ADD CONSTRAINT fk_session_batch_group_id "
        "FOREIGN KEY (batch_group_id) REFERENCES app.batch_group (group_id)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app.session DROP CONSTRAINT IF EXISTS fk_session_batch_group_id")
    op.execute("ALTER TABLE app.session DROP COLUMN IF EXISTS batch_group_id")
    op.execute("DROP TABLE IF EXISTS app.batch_group_member")
    op.execute("DROP TABLE IF EXISTS app.batch_group")
