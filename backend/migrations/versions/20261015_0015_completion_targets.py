"""Optional completion targets for new live batch groups.

Revision ID: 0015_completion_targets
Revises: 0014_nopress_arm
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0015_completion_targets"
down_revision = "0014_nopress_arm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "batch_group", sa.Column("enrollment_policy", JSONB(), nullable=True), schema="app"
    )
    op.create_check_constraint(
        op.f("ck_batch_group_enrollment_policy"),
        "batch_group",
        "enrollment_policy IS NULL OR "
        "(mode = 'live' AND jsonb_typeof(enrollment_policy) = 'object')",
        schema="app",
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS "
        "(SELECT 1 FROM app.batch_group WHERE enrollment_policy IS NOT NULL) THEN "
        "RAISE EXCEPTION 'Cannot discard configured enrollment policies'; END IF; END $$"
    )
    op.drop_constraint(
        op.f("ck_batch_group_enrollment_policy"), "batch_group", schema="app", type_="check"
    )
    op.drop_column("batch_group", "enrollment_policy", schema="app")
