"""Participant display initials (UX round 2, item 10): ``app.participant_display``
stores the initials a participant enters on the background page so the UI can name
them consistently.

Revision ID: 0006_participant_display
Revises: 0005_model_call_outcomes
Create Date: 2026-10-02

The table is session-owned but identity-adjacent: the runtime role gets full
read/write, but the research and compensation roles get no grant — initials are a
display convenience, never research data (``export_session`` excludes the table, and
tombstone purge deletes its rows via ``DELETABLE_SESSION_TABLES``).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0006_participant_display"
down_revision = "0005_model_call_outcomes"
branch_labels = None
depends_on = None

RUNTIME = "investigators_user"
RESEARCH = "investigators_research"
COMPENSATION = "investigators_compensation"
ROLES = (RUNTIME, RESEARCH, COMPENSATION)


def _load(name: str) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "snapshots" / f"schema_{name}.py"
    spec = importlib.util.spec_from_file_location(f"investigators_schema_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    snap = _load("0006")
    table = snap.metadata.tables["app.participant_display"]
    table.create(bind=op.get_bind(), checkfirst=True)
    roles = ", ".join(ROLES)
    op.execute(f"REVOKE ALL ON app.participant_display FROM PUBLIC, {roles}")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON app.participant_display TO " + RUNTIME
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.participant_display")
