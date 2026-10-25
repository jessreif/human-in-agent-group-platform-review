"""Batch vocabulary cleanup (BA-103): ``app.pilot_batch`` is renamed ``app.batch``.

The old name claimed every enrollment batch was "the pilot"; the pilot is the
fielded run of the approved study (spec §1, D13) — QA, preview, and test batches
live in this table too and are not it. Constraint names are renamed to match the
naming convention (``pk_batch``, ``ck_batch_*``, ``fk_batch_manifest_id``).

Revision ID: 0008_batch_rename
Revises: 0007_contents_fallback
Create Date: 2026-10-07
"""

from __future__ import annotations

from alembic import op

revision = "0008_batch_rename"
down_revision = "0007_contents_fallback"
branch_labels = None
depends_on = None

NAMES = {
    "pk_pilot_batch": "pk_batch",
    "ck_pilot_batch_mode": "ck_batch_mode",
    "ck_pilot_batch_batch_id_format": "ck_batch_batch_id_format",
    "fk_pilot_batch_manifest_id": "fk_batch_manifest_id",
}


def _rename(old: str, new: str) -> None:
    op.execute(f'ALTER TABLE app.batch RENAME CONSTRAINT "{old}" TO "{new}"')


def upgrade() -> None:
    op.execute("ALTER TABLE app.pilot_batch RENAME TO batch")
    for old, new in NAMES.items():
        _rename(old, new)


def downgrade() -> None:
    for old, new in NAMES.items():
        _rename(new, old)
    op.execute("ALTER TABLE app.batch RENAME TO pilot_batch")
