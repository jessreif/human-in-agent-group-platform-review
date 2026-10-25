"""Structured record locations: ``app.record`` gains ``location_id`` and ``toward``
(INV-0501/0508).

Revision ID: 0004_record_locations
Revises: 0003_enrollment
Create Date: 2026-09-29

Runs as ``investigators_migrator``. Spec §7.1 forbids recovering truth from prose, so
the hideout a timeline event concerns (``location_id``; for transit the origin) and
the transit destination hint (``toward``) are first-class columns, populated by the
world generator. Existing rows get NULLs — the only possible existing rows are test
rows; live sessions had no records before WP05.
"""

from __future__ import annotations

from alembic import op

revision = "0004_record_locations"
down_revision = "0003_enrollment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.record ADD COLUMN location_id TEXT")
    op.execute("ALTER TABLE app.record ADD COLUMN toward TEXT")
    op.execute(
        "ALTER TABLE app.record ADD CONSTRAINT ck_record_location "
        "CHECK ((event_type = 'transit') = (toward IS NOT NULL))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app.record DROP CONSTRAINT IF EXISTS ck_record_location")
    op.execute("ALTER TABLE app.record DROP COLUMN IF EXISTS toward")
    op.execute("ALTER TABLE app.record DROP COLUMN IF EXISTS location_id")
