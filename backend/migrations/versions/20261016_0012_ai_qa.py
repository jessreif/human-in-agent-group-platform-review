"""AI_QA_ENABLED (2026-10-16): optional rules-Q&A page between the
comprehension check and connecting.

- New ``ai_qa`` stage widens the stage CHECK constraints on ``app.session``,
  ``app.stage_transition`` (from/to) and ``app.event``.
- New ``app.qa_exchange`` table stores each participant question and the
  answering result (local-model answer or deterministic fallback) with
  provenance — participant free text, so the research export treats it like
  other free-text tables.

Revision ID: 0012_ai_qa
Revises: 0011_crate_bounds_12
Create Date: 2026-10-16
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0012_ai_qa"
down_revision = "0011_crate_bounds_12"
branch_labels = None
depends_on = None

RUNTIME = "investigators_user"
RESEARCH = "investigators_research"
ROLES = (RUNTIME, RESEARCH, "investigators_compensation")

# Downgrade simply tightens the constraints: a DB carrying ai_qa rows
# (event/stage_transition are append-only audit logs — never deleted) fails
# loudly rather than silently rewriting state.
OLD = (
    "'entry', 'consent', 'instructions', 'training', 'scripted_connecting', 'ready', "
    "'solo_review', 'task', 'checkin_collect', 'checkin_wait', 'checkin_reveal', "
    "'answers_locked', 'confidence', 'peer_ratings', 'post_questionnaire', 'open_ended', "
    "'debrief', 'data_use_choice', 'completion', 'declined', 'withdrawn', 'technical_stop', "
    "'abandoned'"
)
NEW = OLD.replace("'training', 'scripted_connecting'", "'training', 'ai_qa', 'scripted_connecting'")

CONSTRAINTS = (
    ("session", "ck_session_stage", "stage"),
    ("stage_transition", "ck_stage_transition_from_stage", "from_stage"),
    ("stage_transition", "ck_stage_transition_to_stage", "to_stage"),
    ("event", "ck_event_stage", "stage"),
)


def _load(name: str) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "snapshots" / f"schema_{name}.py"
    spec = importlib.util.spec_from_file_location(f"investigators_schema_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    for table, name, column in CONSTRAINTS:
        op.execute(f"ALTER TABLE app.{table} DROP CONSTRAINT {name}")
        op.execute(f"ALTER TABLE app.{table} ADD CONSTRAINT {name} CHECK ({column} IN ({NEW}))")
    snap = _load("0012")
    roles = ", ".join(ROLES)
    snap.metadata.tables["app.qa_exchange"].create(bind=op.get_bind(), checkfirst=True)
    op.execute(f"REVOKE ALL ON app.qa_exchange FROM PUBLIC, {roles}")
    op.execute(f"GRANT {', '.join(snap.RUNTIME_READ_WRITE)} ON app.qa_exchange TO {RUNTIME}")
    op.execute(f"GRANT SELECT ON app.qa_exchange TO {RESEARCH}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.qa_exchange")
    for table, name, column in CONSTRAINTS:
        op.execute(f"ALTER TABLE app.{table} DROP CONSTRAINT {name}")
        op.execute(f"ALTER TABLE app.{table} ADD CONSTRAINT {name} CHECK ({column} IN ({OLD}))")
