"""Open-ended follow-up stage (UX round 8): TP flows gain ``open_ended`` between
``post_questionnaire`` and ``debrief`` — two sequential free-text questions. The
stage is code-defined (no bound artifact) so the current preview pair runs it
without a new profile version. Widens the stage CHECK constraints on
``app.session``, ``app.stage_transition`` (from/to) and ``app.event``.

Revision ID: 0010_open_ended_stage
Revises: 0009_batch_group
Create Date: 2026-10-11
"""

from __future__ import annotations

from alembic import op

revision = "0010_open_ended_stage"
down_revision = "0009_batch_group"
branch_labels = None
depends_on = None

# Downgrade simply tightens the constraints: a DB carrying open_ended rows
# (event/stage_transition are append-only audit logs — never deleted) fails
# loudly rather than silently rewriting state.
OLD = (
    "'entry', 'consent', 'instructions', 'training', 'scripted_connecting', 'ready', "
    "'solo_review', 'task', 'checkin_collect', 'checkin_wait', 'checkin_reveal', "
    "'answers_locked', 'confidence', 'peer_ratings', 'post_questionnaire', 'debrief', "
    "'data_use_choice', 'completion', 'declined', 'withdrawn', 'technical_stop', 'abandoned'"
)
NEW = OLD.replace(
    "'post_questionnaire', 'debrief'", "'post_questionnaire', 'open_ended', 'debrief'"
)

CONSTRAINTS = (
    ("session", "ck_session_stage", "stage"),
    ("stage_transition", "ck_stage_transition_from_stage", "from_stage"),
    ("stage_transition", "ck_stage_transition_to_stage", "to_stage"),
    ("event", "ck_event_stage", "stage"),
)


def upgrade() -> None:
    for table, name, column in CONSTRAINTS:
        op.execute(f"ALTER TABLE app.{table} DROP CONSTRAINT {name}")
        op.execute(f"ALTER TABLE app.{table} ADD CONSTRAINT {name} CHECK ({column} IN ({NEW}))")


def downgrade() -> None:
    for table, name, column in CONSTRAINTS:
        op.execute(f"ALTER TABLE app.{table} DROP CONSTRAINT {name}")
        op.execute(f"ALTER TABLE app.{table} ADD CONSTRAINT {name} CHECK ({column} IN ({OLD}))")
