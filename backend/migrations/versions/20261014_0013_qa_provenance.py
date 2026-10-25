"""Persist rules-Q&A prompt and effective generation provenance.

Revision ID: 0013_qa_provenance
Revises: 0012_ai_qa
Create Date: 2026-10-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_qa_provenance"
down_revision = "0012_ai_qa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "qa_exchange",
        sa.Column(
            "generation_audit",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema="app",
    )


def downgrade() -> None:
    op.drop_column("qa_exchange", "generation_audit", schema="app")
