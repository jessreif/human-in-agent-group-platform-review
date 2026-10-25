"""Neutral allocation arm for TIME_PRESSURE_ENABLED=0 (owner direction
2026-10-14): the flag now collapses the high/low arm axis instead of recording
a pressure arm the deployment does not deliver. ``app.session.arm`` and
``app.allocation.arm`` gain the ``nopress`` sentinel — an allocation value,
never a manifest arm (``resolve_policy`` canonicalizes it to the manifest's
first arm; paired arms differ only under ``manipulation.arm``).

Revision ID: 0014_nopress_arm
Revises: 0013_qa_provenance
Create Date: 2026-10-14
"""

from __future__ import annotations

from alembic import op

revision = "0014_nopress_arm"
down_revision = "0013_qa_provenance"
branch_labels = None
depends_on = None

# Downgrade simply tightens the constraints: a DB carrying nopress rows fails
# loudly rather than silently rewriting recorded arms.
OLD = "'high', 'low', 'eval', 'control'"
NEW = f"{OLD}, 'nopress'"


def upgrade() -> None:
    op.execute("ALTER TABLE app.session DROP CONSTRAINT ck_session_arm")
    op.execute(
        f"ALTER TABLE app.session ADD CONSTRAINT ck_session_arm "
        f"CHECK (arm IS NULL OR arm IN ({NEW}))"
    )
    op.execute("ALTER TABLE app.allocation DROP CONSTRAINT ck_allocation_arm")
    op.execute(
        f"ALTER TABLE app.allocation ADD CONSTRAINT ck_allocation_arm "
        f"CHECK (arm IN ({NEW}))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app.session DROP CONSTRAINT ck_session_arm")
    op.execute(
        f"ALTER TABLE app.session ADD CONSTRAINT ck_session_arm "
        f"CHECK (arm IS NULL OR arm IN ({OLD}))"
    )
    op.execute("ALTER TABLE app.allocation DROP CONSTRAINT ck_allocation_arm")
    op.execute(
        f"ALTER TABLE app.allocation ADD CONSTRAINT ck_allocation_arm "
        f"CHECK (arm IN ({OLD}))"
    )
