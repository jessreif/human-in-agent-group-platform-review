"""Relational schema, first change set of WP03 (INV-0302/0303, spec §8.1).

Two PostgreSQL schemas created by the INV-0301 bootstrap and owned by ``investigators_migrator``:

- ``app``       research/runtime tables (runtime role reads/writes, research role reads);
- ``identity``  platform linkage, insert-only for the runtime role, never in research exports.

Rules encoded here:
- every session-owned row carries ``session_id`` with a foreign key to ``app.session``; rows
  that reference other session-owned rows use *composite* keys ``(session_id, ...)`` so a row
  can never point into another session (cross-session integrity by construction);
- append-only tables (``IMMUTABLE_TABLES``) reject UPDATE/DELETE with a trigger, and the
  runtime role is not granted those privileges on them either (migration 0001);
- one logical job per ``(session_id, logical_job_key)``, one receipt per ``command_id``,
  monotonic ``(session_id, seq)`` events, one outbox row per event.

This module is the *current* target metadata for Alembic. Each migration builds from its own
frozen snapshot (``migrations/snapshots/``) so later edits here never change what an old
revision creates; a PostgreSQL test proves the migrated head matches this metadata.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Double,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

APP = "app"
IDENTITY = "identity"
NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=NAMING)

STAGES = (
    "entry",
    "consent",
    "instructions",
    "training",
    "scripted_connecting",
    "ready",
    "solo_review",
    "task",
    "checkin_collect",
    "checkin_wait",
    "checkin_reveal",
    "answers_locked",
    "confidence",
    "peer_ratings",
    "post_questionnaire",
    "debrief",
    "data_use_choice",
    "completion",
    "declined",
    "withdrawn",
    "technical_stop",
    "abandoned",
)
TERMINAL_STAGES = ("declined", "withdrawn", "technical_stop", "abandoned")
MODES = ("preview", "qa", "live")
ARMS = ("high", "low", "eval", "control")
ACTORS = ("participant", "JT", "PM", "assistant", "system", "researcher", "scheduler")
JOB_STATUSES = ("pending", "claimed", "committed", "suppressed", "failed", "cancelled", "expired")
STREAMS = ("assignment", "world", "order", "ratings", "schedules", "jobs")
EVENT_TYPES_RECORD = ("arrival", "still_at", "departure", "transit")


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


def _sha(column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} ~ '^[0-9a-f]{{64}}$'", name=f"{column}_sha256")


def _ts(name: str, *, nullable: bool = False, default: bool = True) -> Column[Any]:
    return Column(
        name,
        DateTime(timezone=True),
        nullable=nullable,
        server_default=text("statement_timestamp()") if default else None,
    )


def _session_fk() -> Column[Any]:
    return Column(
        "session_id",
        UUID(as_uuid=True),
        ForeignKey(f"{APP}.session.session_id", ondelete="RESTRICT"),
        nullable=False,
    )


# ----- manifests and batches -------------------------------------------------------------------

experiment_manifest = Table(
    "experiment_manifest",
    metadata,
    Column("manifest_id", Text, primary_key=True),
    Column("manifest_sha256", Text, nullable=False),
    Column("bundle_sha256", Text, nullable=False),
    Column("schema_version", Text, nullable=False),
    Column("study_key", Text, nullable=False),
    Column("profile_kind", Text, nullable=False),
    Column("material_status", Text, nullable=False),
    Column("content", JSONB, nullable=False),
    Column("artifacts", JSONB, nullable=False),
    _ts("registered_at"),
    _sha("manifest_sha256"),
    _sha("bundle_sha256"),
    schema=APP,
)

pilot_batch = Table(
    "pilot_batch",
    metadata,
    Column("batch_id", Text, primary_key=True),
    Column("study_key", Text, nullable=False),
    Column(
        "manifest_id", Text, ForeignKey(f"{APP}.experiment_manifest.manifest_id"), nullable=False
    ),
    Column("mode", Text, nullable=False),
    Column("enrollment_open", Boolean, nullable=False, server_default=text("false")),
    _ts("created_at"),
    CheckConstraint(_in("mode", MODES), name="mode"),
    CheckConstraint("batch_id ~ '^[a-z0-9][a-z0-9_.-]{1,63}$'", name="batch_id_format"),
    schema=APP,
)

# ----- participants and sessions ---------------------------------------------------------------

participant = Table(
    "participant",
    metadata,
    Column("participant_id", UUID(as_uuid=True), primary_key=True),
    Column("research_pid", Text, nullable=False, unique=True),
    _ts("created_at"),
    schema=APP,
)

session = Table(
    "session",
    metadata,
    Column("session_id", UUID(as_uuid=True), primary_key=True),
    Column(
        "participant_id",
        UUID(as_uuid=True),
        ForeignKey(f"{APP}.participant.participant_id"),
        nullable=False,
    ),
    Column("batch_id", Text, ForeignKey(f"{APP}.pilot_batch.batch_id"), nullable=False),
    Column(
        "manifest_id", Text, ForeignKey(f"{APP}.experiment_manifest.manifest_id"), nullable=False
    ),
    Column("manifest_sha256", Text, nullable=False),
    Column("bundle_sha256", Text, nullable=False),
    Column("binding_sha256", Text, nullable=False),
    Column("binding", JSONB, nullable=False),
    Column("study_key", Text, nullable=False),
    Column("mode", Text, nullable=False),
    Column("timing_mode", Text, nullable=False),
    Column("arm", Text, nullable=True),
    Column("seed_hex", Text, nullable=False),
    Column("build_source_sha256", Text, nullable=False),
    Column("compatibility_key", Text, nullable=True),
    Column("stage", Text, nullable=False, server_default=text("'entry'")),
    Column("revision", BigInteger, nullable=False, server_default=text("0")),
    Column("next_event_seq", BigInteger, nullable=False, server_default=text("1")),
    Column("terminal_reason", Text, nullable=True),
    _ts("created_at"),
    _ts("stage_entered_at"),
    UniqueConstraint("participant_id", "batch_id"),
    CheckConstraint(_in("mode", MODES), name="mode"),
    CheckConstraint(_in("timing_mode", ("faithful", "accelerated_qa")), name="timing_mode"),
    CheckConstraint("mode <> 'live' OR timing_mode = 'faithful'", name="live_is_faithful"),
    CheckConstraint(f"arm IS NULL OR {_in('arm', ARMS)}", name="arm"),
    CheckConstraint(_in("stage", STAGES), name="stage"),
    CheckConstraint(
        f"NOT ({_in('stage', TERMINAL_STAGES)}) OR terminal_reason IS NOT NULL",
        name="terminal_reason",
    ),
    CheckConstraint("revision >= 0", name="revision"),
    CheckConstraint("next_event_seq >= 1", name="next_event_seq"),
    CheckConstraint("seed_hex ~ '^[0-9a-f]{32,128}$'", name="seed_hex"),
    CheckConstraint(
        "compatibility_key IS NULL OR compatibility_key ~ '^ck1:[0-9a-f]{64}$'",
        name="compatibility_key",
    ),
    _sha("manifest_sha256"),
    _sha("bundle_sha256"),
    _sha("binding_sha256"),
    schema=APP,
)

platform_link = Table(
    "platform_link",
    metadata,
    Column("link_id", UUID(as_uuid=True), primary_key=True),
    Column(
        "session_id",
        UUID(as_uuid=True),
        ForeignKey(f"{APP}.session.session_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    ),
    Column("platform", Text, nullable=False),
    Column("platform_pid", Text, nullable=False),
    Column("study_ref", Text, nullable=True),
    Column("session_ref", Text, nullable=True),
    _ts("created_at"),
    _ts("purge_after", nullable=True, default=False),
    CheckConstraint(_in("platform", ("prolific", "connect", "lab")), name="platform"),
    schema=IDENTITY,
)

allocation = Table(
    "allocation",
    metadata,
    Column("allocation_id", UUID(as_uuid=True), primary_key=True),
    Column("batch_id", Text, ForeignKey(f"{APP}.pilot_batch.batch_id"), nullable=False),
    Column("block_index", Integer, nullable=False),
    Column("slot_index", Integer, nullable=False),
    Column("arm", Text, nullable=False),
    Column(
        "session_id",
        UUID(as_uuid=True),
        ForeignKey(f"{APP}.session.session_id"),
        nullable=True,
        unique=True,
    ),
    _ts("reserved_at", nullable=True, default=False),
    _ts("created_at"),
    UniqueConstraint("batch_id", "block_index", "slot_index"),
    CheckConstraint(_in("arm", ARMS), name="arm"),
    CheckConstraint("block_index >= 0 AND slot_index >= 0", name="indices"),
    CheckConstraint("(session_id IS NULL) = (reserved_at IS NULL)", name="reservation"),
    schema=APP,
)

consent_event = Table(
    "consent_event",
    metadata,
    Column("consent_id", UUID(as_uuid=True), primary_key=True),
    _session_fk(),
    Column("text_version", Text, nullable=False),
    Column("action", Text, nullable=False),
    _ts("occurred_at"),
    CheckConstraint(_in("action", ("consented", "declined")), name="action"),
    schema=APP,
)

stage_transition = Table(
    "stage_transition",
    metadata,
    Column("transition_id", UUID(as_uuid=True), primary_key=True),
    _session_fk(),
    Column("from_stage", Text, nullable=False),
    Column("to_stage", Text, nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("reason", Text, nullable=True),
    _ts("occurred_at"),
    CheckConstraint(_in("from_stage", STAGES), name="from_stage"),
    CheckConstraint(_in("to_stage", STAGES), name="to_stage"),
    Index(None, "session_id", "revision"),
    schema=APP,
)

clock_interval = Table(
    "clock_interval",
    metadata,
    Column("interval_id", UUID(as_uuid=True), primary_key=True),
    _session_fk(),
    Column("kind", Text, nullable=False),
    Column("reason", Text, nullable=False),
    _ts("started_at"),
    _ts("ended_at", nullable=True, default=False),
    Column("active_ms_at_start", BigInteger, nullable=False),
    CheckConstraint(_in("kind", ("running", "paused")), name="kind"),
    CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="order"),
    CheckConstraint("active_ms_at_start >= 0", name="active_ms"),
    Index(
        "uq_clock_interval_one_open",
        "session_id",
        unique=True,
        postgresql_where=text("ended_at IS NULL"),
    ),
    schema=APP,
)

draw_log = Table(
    "draw_log",
    metadata,
    _session_fk(),
    Column("stream", Text, nullable=False),
    Column("draw_key", Text, nullable=False),
    Column("value", Double, nullable=False),
    _ts("recorded_at"),
    PrimaryKeyConstraint("session_id", "stream", "draw_key"),
    CheckConstraint(_in("stream", STREAMS), name="stream"),
    CheckConstraint("value >= 0 AND value < 1", name="value"),
    schema=APP,
)

# ----- world, records, holdings, file ----------------------------------------------------------

world_snapshot = Table(
    "world_snapshot",
    metadata,
    Column("world_id", UUID(as_uuid=True), primary_key=True),
    Column(
        "session_id",
        UUID(as_uuid=True),
        ForeignKey(f"{APP}.session.session_id"),
        nullable=False,
        unique=True,
    ),
    Column("generator", Text, nullable=False),
    Column("content", JSONB, nullable=False),
    Column("content_sha256", Text, nullable=False),
    _ts("created_at"),
    UniqueConstraint("session_id", "world_id", name="uq_world_snapshot_session_world"),
    _sha("content_sha256"),
    schema=APP,
)

record = Table(
    "record",
    metadata,
    Column("session_id", UUID(as_uuid=True), nullable=False),
    Column("record_id", Text, nullable=False),
    Column("world_id", UUID(as_uuid=True), nullable=False),
    Column("crate", Integer, nullable=False),
    Column("stamp", Text, nullable=False),
    Column("body", Text, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("timeline_ordinal", Integer, nullable=False),
    Column("content_sha256", Text, nullable=False),
    PrimaryKeyConstraint("session_id", "record_id"),
    ForeignKeyConstraint(
        ["session_id", "world_id"],
        [f"{APP}.world_snapshot.session_id", f"{APP}.world_snapshot.world_id"],
        name="fk_record_session_world",
    ),
    CheckConstraint("crate BETWEEN 1 AND 8", name="crate"),
    CheckConstraint(_in("event_type", EVENT_TYPES_RECORD), name="event_type"),
    _sha("content_sha256"),
    schema=APP,
)

seat_holding = Table(
    "seat_holding",
    metadata,
    Column("session_id", UUID(as_uuid=True), nullable=False),
    Column("record_id", Text, nullable=False),
    Column("holder", Text, nullable=False),
    PrimaryKeyConstraint("session_id", "record_id"),
    ForeignKeyConstraint(
        ["session_id", "record_id"],
        [f"{APP}.record.session_id", f"{APP}.record.record_id"],
        name="fk_seat_holding_record",
    ),
    CheckConstraint(_in("holder", ("participant", "JT", "PM")), name="holder"),
    schema=APP,
)

file_membership = Table(
    "file_membership",
    metadata,
    Column("session_id", UUID(as_uuid=True), nullable=False),
    Column("record_id", Text, nullable=False),
    Column("source", Text, nullable=False),
    _ts("acquired_at"),
    Column("acquired_event_seq", BigInteger, nullable=True),
    PrimaryKeyConstraint("session_id", "record_id"),
    ForeignKeyConstraint(
        ["session_id", "record_id"],
        [f"{APP}.record.session_id", f"{APP}.record.record_id"],
        name="fk_file_membership_record",
    ),
    CheckConstraint(_in("source", ("initial", "added_from_team")), name="source"),
    schema=APP,
)

# ----- commands, events, outbox, jobs ----------------------------------------------------------

command_receipt = Table(
    "command_receipt",
    metadata,
    Column("command_id", UUID(as_uuid=True), primary_key=True),
    _session_fk(),
    Column("kind", Text, nullable=False),
    Column("expected_revision", BigInteger, nullable=False),
    Column("outcome", Text, nullable=False),
    Column("reason", Text, nullable=True),
    Column("committed_revision", BigInteger, nullable=False),
    Column("result", JSONB, nullable=False),
    Column("client_monotonic_ms", BigInteger, nullable=True),
    _ts("received_at"),
    UniqueConstraint("session_id", "command_id", name="uq_command_receipt_session_command"),
    CheckConstraint(_in("outcome", ("accepted", "rejected")), name="outcome"),
    CheckConstraint("outcome = 'accepted' OR reason IS NOT NULL", name="reason"),
    Index(None, "session_id"),
    schema=APP,
)

event = Table(
    "event",
    metadata,
    Column("session_id", UUID(as_uuid=True), nullable=False),
    Column("seq", BigInteger, nullable=False),
    Column("event_id", UUID(as_uuid=True), nullable=False, unique=True),
    Column("schema_version", Integer, nullable=False, server_default=text("1")),
    Column("stage", Text, nullable=False),
    Column("actor", Text, nullable=False),
    Column("type", Text, nullable=False),
    Column("command_id", UUID(as_uuid=True), nullable=True),
    Column("job_id", UUID(as_uuid=True), nullable=True),
    Column("parent_event_id", UUID(as_uuid=True), nullable=True),
    _ts("server_time"),
    Column("active_ms", BigInteger, nullable=True),
    Column("payload", JSONB, nullable=False),
    PrimaryKeyConstraint("session_id", "seq"),
    ForeignKeyConstraint(["session_id"], [f"{APP}.session.session_id"], name="fk_event_session"),
    ForeignKeyConstraint(
        ["session_id", "command_id"],
        [f"{APP}.command_receipt.session_id", f"{APP}.command_receipt.command_id"],
        name="fk_event_command_receipt",
        deferrable=True,
        initially="DEFERRED",
    ),
    CheckConstraint("seq >= 1", name="seq"),
    CheckConstraint(_in("actor", ACTORS), name="actor"),
    CheckConstraint(_in("stage", STAGES), name="stage"),
    CheckConstraint("active_ms IS NULL OR active_ms >= 0", name="active_ms"),
    schema=APP,
)

outbox = Table(
    "outbox",
    metadata,
    Column("outbox_id", BigInteger, Identity(always=True), primary_key=True),
    Column("session_id", UUID(as_uuid=True), nullable=False),
    Column("seq", BigInteger, nullable=False),
    _ts("created_at"),
    _ts("dispatched_at", nullable=True, default=False),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    UniqueConstraint("session_id", "seq"),
    ForeignKeyConstraint(
        ["session_id", "seq"],
        [f"{APP}.event.session_id", f"{APP}.event.seq"],
        name="fk_outbox_event",
    ),
    Index(
        "ix_outbox_undispatched",
        "outbox_id",
        postgresql_where=text("dispatched_at IS NULL"),
    ),
    schema=APP,
)

scheduled_job = Table(
    "scheduled_job",
    metadata,
    Column("job_id", UUID(as_uuid=True), primary_key=True),
    _session_fk(),
    Column("logical_job_key", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("clock_basis", Text, nullable=False),
    Column("due_active_ms", BigInteger, nullable=True),
    Column("due_wall_at", DateTime(timezone=True), nullable=True),
    Column("claimable_at", DateTime(timezone=True), nullable=False),
    Column("parent_event_id", UUID(as_uuid=True), nullable=True),
    Column("precondition", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("payload", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("status", Text, nullable=False, server_default=text("'pending'")),
    Column("lease_owner", Text, nullable=True),
    Column("lease_expires_at", DateTime(timezone=True), nullable=True),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("outcome_reason", Text, nullable=True),
    _ts("created_at"),
    _ts("finished_at", nullable=True, default=False),
    UniqueConstraint("session_id", "logical_job_key"),
    CheckConstraint(_in("clock_basis", ("active_task", "wall")), name="clock_basis"),
    CheckConstraint(
        "(clock_basis = 'active_task' AND due_active_ms IS NOT NULL AND due_active_ms >= 0) "
        "OR (clock_basis = 'wall' AND due_wall_at IS NOT NULL)",
        name="due",
    ),
    CheckConstraint(_in("status", JOB_STATUSES), name="status"),
    CheckConstraint(
        "(status = 'claimed') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
        name="lease",
    ),
    CheckConstraint("(status IN ('pending', 'claimed')) = (finished_at IS NULL)", name="finished"),
    Index(
        "ix_scheduled_job_claimable",
        "claimable_at",
        postgresql_where=text("status IN ('pending', 'claimed')"),
    ),
    schema=APP,
)

IMMUTABLE_TABLES = (
    "experiment_manifest",
    "consent_event",
    "stage_transition",
    "draw_log",
    "world_snapshot",
    "record",
    "command_receipt",
    "event",
)
"""Append-only tables: UPDATE/DELETE rejected by trigger (retention deletion arrives with WP13
as an audited, separately migrated mechanism)."""

# Per-table privileges applied by migration 0001, independent of the bootstrap defaults.
RUNTIME_READ_WRITE = ("SELECT", "INSERT", "UPDATE", "DELETE")
RUNTIME_APPEND_ONLY = ("SELECT", "INSERT")
