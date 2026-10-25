"""Crate-condition experiment (2026-10-14, audit finding W01): widen the seven
``crate`` CHECK constraints from ``BETWEEN 1 AND 8`` to ``BETWEEN 1 AND 12``.
The no-overlap condition runs a 12-crate world; without this, record inserts
for crates 9–12 fail at session ready and later interactions (messages,
requests, claims, answer revisions/finals, confidence) reject the same range.

Per-session bounds are enforced by the manifest task shape (``task.n_crates``)
upstream of the database — a 4-crate session still cannot address crate 12.
The widened check is the schema-level ceiling, not the session contract.

Revision ID: 0011_crate_bounds_12
Revises: 0010_open_ended_stage
Create Date: 2026-10-14
"""

from __future__ import annotations

from alembic import op

revision = "0011_crate_bounds_12"
down_revision = "0010_open_ended_stage"
branch_labels = None
depends_on = None

# (table, nullable?) — record/claim/answer_*/confidence_response carry a
# non-null crate; message/request allow NULL (non-crate messages/asks).
TABLES = (
    ("record", False),
    ("message", True),
    ("request", True),
    ("claim", False),
    ("answer_revision", False),
    ("answer_final", False),
    ("confidence_response", False),
)


def _check(table: str, nullable: bool, upper: int) -> str:
    prefix = "crate IS NULL OR " if nullable else ""
    return f"{prefix}crate BETWEEN 1 AND {upper}"


def upgrade() -> None:
    for table, nullable in TABLES:
        op.execute(f"ALTER TABLE app.{table} DROP CONSTRAINT ck_{table}_crate")
        op.execute(
            f"ALTER TABLE app.{table} ADD CONSTRAINT ck_{table}_crate "
            f"CHECK ({_check(table, nullable, 12)})"
        )


def downgrade() -> None:
    # Tightening back to 8 fails loudly if any 9–12 rows exist — an honest
    # signal that a new-condition world has already run.
    for table, nullable in TABLES:
        op.execute(f"ALTER TABLE app.{table} DROP CONSTRAINT ck_{table}_crate")
        op.execute(
            f"ALTER TABLE app.{table} ADD CONSTRAINT ck_{table}_crate "
            f"CHECK ({_check(table, nullable, 8)})"
        )
