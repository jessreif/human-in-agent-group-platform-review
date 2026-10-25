"""Mixed teammate-level arm for the v15 workload manipulation (owner direction
2026-10-17): the paired time-pressure factor becomes both-seats-high
(``high``) vs one-high/one-low (``mixed``). ``app.session.arm`` and
``app.allocation.arm`` gain the ``mixed`` manifest arm; ``nopress`` stays the
flag-off sentinel.

Revision ID: 0017_mixed_arm
Revises: 0016_behavior_source
Create Date: 2026-10-18
"""

from __future__ import annotations

from alembic import op

revision = "0017_mixed_arm"
down_revision = "0016_behavior_source"
branch_labels = None
depends_on = None

# Downgrade simply tightens the constraints: a DB carrying mixed rows fails
# loudly rather than silently rewriting recorded arms.
OLD = "'high', 'low', 'eval', 'control', 'nopress'"
NEW = "'high', 'low', 'eval', 'control', 'mixed', 'nopress'"


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
