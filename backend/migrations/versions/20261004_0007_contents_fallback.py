"""Contents-summary fallback tag (SQ-8): the assistant's deterministic
crate→contents answers are a distinct provenance class from file summaries —
they answer from the manifest's fixed crate→item map, not the participant's
file. ``app.displayed_output.fallback`` gains ``deterministic_contents_summary``.

Revision ID: 0007_contents_fallback
Revises: 0006_participant_display
Create Date: 2026-10-04
"""

from __future__ import annotations

from alembic import op

revision = "0007_contents_fallback"
down_revision = "0006_participant_display"
branch_labels = None
depends_on = None

CONSTRAINT = "ck_displayed_output_fallback"
OLD = "'none', 'deterministic_script', 'deterministic_file_summary'"
NEW = OLD + ", 'deterministic_contents_summary'"


def upgrade() -> None:
    op.execute(f"ALTER TABLE app.displayed_output DROP CONSTRAINT {CONSTRAINT}")
    op.execute(
        f"ALTER TABLE app.displayed_output ADD CONSTRAINT {CONSTRAINT} CHECK (fallback IN ({NEW}))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM app.displayed_output WHERE fallback = 'deterministic_contents_summary'")
    op.execute(f"ALTER TABLE app.displayed_output DROP CONSTRAINT {CONSTRAINT}")
    op.execute(
        f"ALTER TABLE app.displayed_output ADD CONSTRAINT {CONSTRAINT} CHECK (fallback IN ({OLD}))"
    )
