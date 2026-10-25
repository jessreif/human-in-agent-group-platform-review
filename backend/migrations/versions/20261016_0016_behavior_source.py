"""Participant-facing-scoped digest (2026-10-16): ``session.behavior_source_sha256``.

The compatibility key's build component switches from the whole-repo release checksum
(``build_source_sha256``) to the participant-facing-scoped digest
(``scripts/source_bundle.py --behavior`` → ``INVESTIGATORS_BEHAVIOR_SOURCE_SHA256``), so
dashboard/export/admin/test churn can no longer split an analytic partition. The new column
carries that scoped digest per session; ``build_source_sha256`` stays for release provenance.

Backfill: the v12-era builds below fielded sessions before the scoped digest existed. Their
participant-facing behavior is recorded as equivalent to this build's — every code delta
between those trees and this one is either admin/export/dashboard/test-scoped or the
behavior-neutral scoped-digest plumbing itself (verified against the deployed per-file
manifests and the pinned ``generation_policy_pinned`` payloads, which match modulo the
embedded build field). Those sessions adopt this image's
``INVESTIGATORS_BEHAVIOR_SOURCE_SHA256``. Sessions on any other build keep NULL: fail
closed — they pool only under their strict legacy compatibility keys.

Revision ID: 0016_behavior_source
Revises: 0015_completion_targets
Create Date: 2026-10-16
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op

revision = "0016_behavior_source"
down_revision = "0015_completion_targets"
branch_labels = None
depends_on = None

# v12-era release digests whose participant-facing code is equivalent to this build's.
BEHAVIOR_EQUIVALENT_BUILDS = (
    "04b212ceb502cbef135cc483092feefcfc5a76e67ea721091e9019e6de9caa08",
    "3565ee60db0beb145b76bced624df1c14c7682e2ba69d4b4d754d874554205ac",
)


def upgrade() -> None:
    op.execute("ALTER TABLE app.session ADD COLUMN behavior_source_sha256 TEXT")
    behavior = os.environ.get("INVESTIGATORS_BEHAVIOR_SOURCE_SHA256", "")
    if len(behavior) == 64:
        stmt = sa.text(
            "UPDATE app.session SET behavior_source_sha256 = :behavior "
            "WHERE build_source_sha256 IN :builds"
        ).bindparams(sa.bindparam("builds", expanding=True))
        op.get_bind().execute(stmt, {"behavior": behavior, "builds": list(BEHAVIOR_EQUIVALENT_BUILDS)})


def downgrade() -> None:
    op.execute("ALTER TABLE app.session DROP COLUMN behavior_source_sha256")
