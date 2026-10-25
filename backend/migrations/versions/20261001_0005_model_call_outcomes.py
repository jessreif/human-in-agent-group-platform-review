"""WP10a outcome taxonomy (INV-1005/1006): ``app.model_call`` gains the statuses
``empty`` (provider returned a parseable but empty body), ``invalid_response``
(malformed provider body) and ``quarantined`` (a provider result that committed after
the task surface closed — recorded, never scheduled for delivery).

Revision ID: 0005_model_call_outcomes
Revises: 0004_record_locations
Create Date: 2026-10-01

The check constraint is rebuilt in place; only the enum widens, so existing rows and
the ``uq_model_call_attempt`` retry lineage are untouched.
"""

from __future__ import annotations

from alembic import op

revision = "0005_model_call_outcomes"
down_revision = "0004_record_locations"
branch_labels = None
depends_on = None

_OLD = (
    "'pending','running','succeeded','failed','timeout',"
    "'validation_rejected','fallback','cancelled'"
)
_NEW = _OLD + ",'empty','invalid_response','quarantined'"


def upgrade() -> None:
    op.execute("ALTER TABLE app.model_call DROP CONSTRAINT ck_model_call_status")
    op.execute(
        f"ALTER TABLE app.model_call ADD CONSTRAINT ck_model_call_status CHECK (status IN ({_NEW}))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app.model_call DROP CONSTRAINT ck_model_call_status")
    op.execute(
        f"ALTER TABLE app.model_call ADD CONSTRAINT ck_model_call_status CHECK (status IN ({_OLD}))"
    )
