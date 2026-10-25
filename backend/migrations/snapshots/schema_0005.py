"""Relational schema for WP03 (INV-0302-0306, 0309, spec §8.1).

Two PostgreSQL schemas created by the INV-0301 bootstrap and owned by ``investigators_migrator``:

- ``app``       research/runtime tables (runtime role reads/writes, research role reads);
- ``identity``  platform linkage, insert-only for the runtime role, never in research exports.

Rules encoded here:
- every session-owned row carries ``session_id`` with a foreign key to ``app.session``; rows
  that reference other session-owned rows use *composite* keys ``(session_id, ...)`` so a row
  can never point into another session (cross-session integrity by construction);
- append-only tables (``IMMUTABLE_TABLES``) reject UPDATE/DELETE with a trigger, and the
  runtime role is not granted those privileges on them either (the migrations);
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
    # One active writer lease per session (spec §8.2); a second tab is read-only until it
    # performs an explicit takeover, which revokes the incumbent lease.
    Column("writer_id", UUID(as_uuid=True), nullable=True),
    _ts("writer_lease_until", nullable=True, default=False),
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
    CheckConstraint("(writer_id IS NULL) = (writer_lease_until IS NULL)", name="writer_lease"),
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
    # eligibility and payment-reconciliation lookups; retention purge uses ix_platform_link_purge
    Index("ix_platform_link_identity", "platform", "platform_pid"),
    schema=IDENTITY,
)

session_credential = Table(
    "session_credential",
    metadata,
    Column("credential_id", UUID(as_uuid=True), primary_key=True),
    Column(
        "session_id",
        UUID(as_uuid=True),
        ForeignKey(f"{APP}.session.session_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    # Only the SHA-256 of the opaque bearer token is stored (spec §5.1); the raw value exists
    # solely in the participant's HttpOnly cookie.
    Column("credential_sha256", Text, nullable=False, unique=True),
    Column("kind", Text, nullable=False),
    _ts("issued_at"),
    _ts("revoked_at", nullable=True, default=False),
    Column("revoked_reason", Text, nullable=True),
    Column(
        "superseded_by",
        UUID(as_uuid=True),
        ForeignKey(f"{IDENTITY}.session_credential.credential_id"),
        nullable=True,
    ),
    CheckConstraint(_in("kind", ("primary", "recovery")), name="kind"),
    CheckConstraint("(revoked_at IS NULL) = (revoked_reason IS NULL)", name="revocation"),
    _sha("credential_sha256"),
    Index("ix_session_credential_session", "session_id"),
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
    Column("location_id", Text, nullable=True),
    Column("toward", Text, nullable=True),
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
    CheckConstraint("(event_type = 'transit') = (toward IS NOT NULL)", name="ck_record_location"),
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
    Column("revision", BigInteger, nullable=False, server_default=text("0")),
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
    Column("finished_revision", BigInteger, nullable=True),
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
    CheckConstraint(
        "(status IN ('pending', 'claimed')) = (finished_revision IS NULL)", name="finished_rev"
    ),
    Index(
        "ix_scheduled_job_claimable",
        "claimable_at",
        postgresql_where=text("status IN ('pending', 'claimed')"),
    ),
    schema=APP,
)

# ----- communication and delivery (INV-0304) ---------------------------------------------------

MESSAGE_CHANNELS = ("team", "assistant", "system")
MESSAGE_STATUSES = (
    "accepted",
    "scheduled",
    "delivered",
    "suppressed",
    "cancelled",
    "expired",
    "failed",
)
SEATS = ("participant", "JT", "PM")
REQUEST_TARGETS = ("participant", "JT", "PM", "assistant")
EPISODE_STATUSES = (
    "pending",
    "acknowledged",
    "responded",
    "resolved",
    "cancelled",
    "expired",
    "superseded",
)
DELIVERY_KINDS = ("initial", "replay", "resync")

message = Table(
    "message",
    metadata,
    _session_fk(),
    Column("message_id", UUID(as_uuid=True), nullable=False),
    Column("actor", Text, nullable=False),
    Column("channel", Text, nullable=False),
    Column("parent_message_id", UUID(as_uuid=True), nullable=True),
    Column("thread_key", Text, nullable=True),
    Column("crate", Integer, nullable=True),
    Column("record_id", Text, nullable=True),
    Column("request_id", UUID(as_uuid=True), nullable=True),
    Column("body", Text, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'accepted'")),
    Column("accepted_seq", BigInteger, nullable=False),
    Column("scheduled_for_active_ms", BigInteger, nullable=True),
    Column("committed_seq", BigInteger, nullable=True),
    _ts("created_at"),
    _ts("delivered_at", nullable=True, default=False),
    PrimaryKeyConstraint("session_id", "message_id"),
    ForeignKeyConstraint(
        ["session_id", "parent_message_id"],
        [f"{APP}.message.session_id", f"{APP}.message.message_id"],
        name="fk_message_parent",
    ),
    ForeignKeyConstraint(
        ["session_id", "record_id"],
        [f"{APP}.record.session_id", f"{APP}.record.record_id"],
        name="fk_message_record",
    ),
    ForeignKeyConstraint(
        ["session_id", "request_id"],
        [f"{APP}.request.session_id", f"{APP}.request.request_id"],
        name="fk_message_request",
    ),
    CheckConstraint(_in("actor", ACTORS), name="actor"),
    CheckConstraint(_in("channel", MESSAGE_CHANNELS), name="channel"),
    CheckConstraint(_in("status", MESSAGE_STATUSES), name="status"),
    CheckConstraint("crate IS NULL OR crate BETWEEN 1 AND 8", name="crate"),
    CheckConstraint("accepted_seq >= 1", name="accepted_seq"),
    Index("ix_message_session_channel", "session_id", "channel"),
    schema=APP,
)

request = Table(
    "request",
    metadata,
    _session_fk(),
    Column("request_id", UUID(as_uuid=True), nullable=False),
    Column("requester", Text, nullable=False),
    Column("responder", Text, nullable=False),
    Column("crate", Integer, nullable=True),
    Column("target", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("command_id", UUID(as_uuid=True), nullable=True),
    Column("job_id", UUID(as_uuid=True), nullable=True),
    Column("accepted_seq", BigInteger, nullable=False),
    Column("accepted_active_ms", BigInteger, nullable=True),
    _ts("accepted_at"),
    PrimaryKeyConstraint("session_id", "request_id"),
    ForeignKeyConstraint(
        ["session_id", "command_id"],
        [f"{APP}.command_receipt.session_id", f"{APP}.command_receipt.command_id"],
        name="fk_request_command",
        deferrable=True,
        initially="DEFERRED",
    ),
    CheckConstraint(_in("requester", REQUEST_TARGETS), name="requester"),
    CheckConstraint(_in("responder", REQUEST_TARGETS), name="responder"),
    CheckConstraint(_in("kind", ("structured", "free_text")), name="kind"),
    CheckConstraint(_in("target", ("record", "latest", "any_useful", "social")), name="target"),
    CheckConstraint("crate IS NULL OR crate BETWEEN 1 AND 8", name="crate"),
    CheckConstraint("accepted_seq >= 1", name="accepted_seq"),
    Index("ix_request_responder", "session_id", "responder"),
    schema=APP,
)

request_episode = Table(
    "request_episode",
    metadata,
    _session_fk(),
    Column("episode_id", UUID(as_uuid=True), nullable=False),
    Column("request_id", UUID(as_uuid=True), nullable=False),
    Column("status", Text, nullable=False, server_default=text("'pending'")),
    Column("acknowledged_event_seq", BigInteger, nullable=True),
    Column("substantive_event_seq", BigInteger, nullable=True),
    Column("closed_event_seq", BigInteger, nullable=True),
    Column("close_reason", Text, nullable=True),
    Column("pending_ms", BigInteger, nullable=True),
    Column("subsequent_action", Text, nullable=True),
    _ts("opened_at"),
    _ts("closed_at", nullable=True, default=False),
    PrimaryKeyConstraint("session_id", "episode_id"),
    ForeignKeyConstraint(
        ["session_id", "request_id"],
        [f"{APP}.request.session_id", f"{APP}.request.request_id"],
        name="fk_request_episode_request",
    ),
    CheckConstraint(_in("status", EPISODE_STATUSES), name="status"),
    CheckConstraint(
        "(status IN ('pending', 'acknowledged', 'responded')) = (closed_at IS NULL)",
        name="open_closed",
    ),
    CheckConstraint("pending_ms IS NULL OR pending_ms >= 0", name="pending_ms"),
    Index("ix_request_episode_open", "session_id", postgresql_where=text("closed_at IS NULL")),
    schema=APP,
)

share = Table(
    "share",
    metadata,
    _session_fk(),
    Column("share_id", UUID(as_uuid=True), nullable=False),
    Column("sharer", Text, nullable=False),
    Column("record_id", Text, nullable=False),
    Column("recipients", JSONB, nullable=False),
    Column("prompted_by_request_id", UUID(as_uuid=True), nullable=True),
    Column("source", Text, nullable=False),
    Column("delivered_seq", BigInteger, nullable=False),
    _ts("delivered_at"),
    PrimaryKeyConstraint("session_id", "share_id"),
    ForeignKeyConstraint(
        ["session_id", "record_id"],
        [f"{APP}.record.session_id", f"{APP}.record.record_id"],
        name="fk_share_record",
    ),
    ForeignKeyConstraint(
        ["session_id", "prompted_by_request_id"],
        [f"{APP}.request.session_id", f"{APP}.request.request_id"],
        name="fk_share_request",
    ),
    CheckConstraint(_in("sharer", SEATS), name="sharer"),
    CheckConstraint(
        _in("source", ("participant_action", "agent_proactive", "crosstalk")), name="source"
    ),
    CheckConstraint("delivered_seq >= 1", name="delivered_seq"),
    Index("ix_share_session_record", "session_id", "record_id"),
    schema=APP,
)

delivery_receipt = Table(
    "delivery_receipt",
    metadata,
    _session_fk(),
    Column("seq", BigInteger, nullable=False),
    Column("recipient", Text, nullable=False),
    Column("kind", Text, nullable=False),
    _ts("received_at", nullable=True, default=False),
    _ts("rendered_at", nullable=True, default=False),
    Column("client_monotonic_ms", BigInteger, nullable=True),
    PrimaryKeyConstraint("session_id", "seq", "recipient", "kind"),
    ForeignKeyConstraint(
        ["session_id", "seq"],
        [f"{APP}.event.session_id", f"{APP}.event.seq"],
        name="fk_delivery_receipt_event",
    ),
    CheckConstraint(_in("recipient", ("participant",)), name="recipient"),
    CheckConstraint(_in("kind", DELIVERY_KINDS), name="kind"),
    CheckConstraint("received_at IS NOT NULL OR rendered_at IS NOT NULL", name="evidence"),
    schema=APP,
)

# ----- knowledge, claims and model output (INV-0304) -------------------------------------------

SNAPSHOT_PURPOSES = ("assistant_context", "structured_retrieval", "social_reply", "crosstalk")
# WP10a (migration 0005) widens the outcome taxonomy: provider-side malformed bodies are
# ``invalid_response``, a parseable-but-empty body is ``empty`` (both distinct from the
# deterministic gate's ``validation_rejected``), and a provider result that arrives after
# the task surface closed is ``quarantined`` — recorded, never scheduled for delivery.
MODEL_CALL_STATUSES = (
    "pending",
    "running",
    "succeeded",
    "failed",
    "timeout",
    "validation_rejected",
    "fallback",
    "cancelled",
    "empty",
    "invalid_response",
    "quarantined",
)
ASSERTIONS = ("asserted", "uncertain", "hedged", "negated")
SUPPORT_STATUSES = ("unevaluated", "supported", "unsupported", "partial")

knowledge_snapshot = Table(
    "knowledge_snapshot",
    metadata,
    _session_fk(),
    Column("snapshot_id", UUID(as_uuid=True), nullable=False),
    Column("seat", Text, nullable=False),
    Column("purpose", Text, nullable=False),
    Column("visible_event_seq", BigInteger, nullable=False),
    Column("record_manifest", JSONB, nullable=False),
    Column("commitments", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("outstanding_requests", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("prompt_sha256", Text, nullable=True),
    Column("parent_command_id", UUID(as_uuid=True), nullable=True),
    Column("parent_job_id", UUID(as_uuid=True), nullable=True),
    Column("created_event_seq", BigInteger, nullable=False),
    _ts("created_at"),
    PrimaryKeyConstraint("session_id", "snapshot_id"),
    CheckConstraint(_in("seat", ("JT", "PM", "assistant")), name="seat"),
    CheckConstraint(_in("purpose", SNAPSHOT_PURPOSES), name="purpose"),
    CheckConstraint("visible_event_seq >= 0", name="visible_event_seq"),
    CheckConstraint("created_event_seq >= 1", name="created_event_seq"),
    schema=APP,
)

agent_state = Table(
    "agent_state",
    metadata,
    _session_fk(),
    Column("seat", Text, nullable=False),
    Column("commitments", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("outstanding_requests", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("delivered_high_water_seq", BigInteger, nullable=False, server_default=text("0")),
    _ts("updated_at"),
    PrimaryKeyConstraint("session_id", "seat"),
    CheckConstraint(_in("seat", ("JT", "PM", "assistant")), name="seat"),
    CheckConstraint("delivered_high_water_seq >= 0", name="high_water"),
    schema=APP,
)

claim = Table(
    "claim",
    metadata,
    _session_fk(),
    Column("claim_id", UUID(as_uuid=True), nullable=False),
    Column("crate", Integer, nullable=False),
    Column("location_id", Text, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("evidence_time", Text, nullable=True),
    Column("assertion", Text, nullable=False),
    Column("support_status", Text, nullable=False, server_default=text("'unevaluated'")),
    Column("issuer", Text, nullable=False),
    Column("snapshot_id", UUID(as_uuid=True), nullable=True),
    Column("model_call_id", UUID(as_uuid=True), nullable=True),
    Column("context_event_seq", BigInteger, nullable=True),
    Column("revises_claim_id", UUID(as_uuid=True), nullable=True),
    _ts("created_at"),
    PrimaryKeyConstraint("session_id", "claim_id"),
    ForeignKeyConstraint(
        ["session_id", "snapshot_id"],
        [f"{APP}.knowledge_snapshot.session_id", f"{APP}.knowledge_snapshot.snapshot_id"],
        name="fk_claim_snapshot",
    ),
    ForeignKeyConstraint(
        ["session_id", "revises_claim_id"],
        [f"{APP}.claim.session_id", f"{APP}.claim.claim_id"],
        name="fk_claim_revises",
    ),
    CheckConstraint("crate BETWEEN 1 AND 8", name="crate"),
    CheckConstraint(_in("event_type", EVENT_TYPES_RECORD), name="event_type"),
    CheckConstraint(_in("assertion", ASSERTIONS), name="assertion"),
    CheckConstraint(_in("support_status", SUPPORT_STATUSES), name="support_status"),
    CheckConstraint(_in("issuer", (*ACTORS, "coder")), name="issuer"),
    schema=APP,
)

claim_source = Table(
    "claim_source",
    metadata,
    _session_fk(),
    Column("claim_source_id", UUID(as_uuid=True), nullable=False),
    Column("claim_id", UUID(as_uuid=True), nullable=False),
    Column("source_kind", Text, nullable=False),
    Column("record_id", Text, nullable=True),
    Column("event_seq", BigInteger, nullable=True),
    PrimaryKeyConstraint("session_id", "claim_source_id"),
    UniqueConstraint(
        "session_id",
        "claim_id",
        "source_kind",
        "record_id",
        "event_seq",
        name="uq_claim_source_once",
        postgresql_nulls_not_distinct=True,
    ),
    ForeignKeyConstraint(
        ["session_id", "claim_id"],
        [f"{APP}.claim.session_id", f"{APP}.claim.claim_id"],
        name="fk_claim_source_claim",
    ),
    ForeignKeyConstraint(
        ["session_id", "record_id"],
        [f"{APP}.record.session_id", f"{APP}.record.record_id"],
        name="fk_claim_source_record",
    ),
    ForeignKeyConstraint(
        ["session_id", "event_seq"],
        [f"{APP}.event.session_id", f"{APP}.event.seq"],
        name="fk_claim_source_event",
    ),
    CheckConstraint(_in("source_kind", ("record", "event")), name="source_kind"),
    CheckConstraint(
        "(source_kind = 'record' AND record_id IS NOT NULL AND event_seq IS NULL) "
        "OR (source_kind = 'event' AND event_seq IS NOT NULL AND record_id IS NULL)",
        name="one_source",
    ),
    schema=APP,
)

model_call = Table(
    "model_call",
    metadata,
    _session_fk(),
    Column("call_id", UUID(as_uuid=True), nullable=False),
    Column("snapshot_id", UUID(as_uuid=True), nullable=False),
    Column("attempt", Integer, nullable=False, server_default=text("1")),
    Column("parent_call_id", UUID(as_uuid=True), nullable=True),
    Column("provider", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("model_digest", Text, nullable=True),
    Column("parameters", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("prompt_sha256", Text, nullable=False),
    Column("input", JSONB, nullable=False),
    Column("raw_response", Text, nullable=True),
    Column("status", Text, nullable=False, server_default=text("'pending'")),
    Column("validation", JSONB, nullable=True),
    Column("timeout_ms", Integer, nullable=True),
    Column("duration_ms", Integer, nullable=True),
    _ts("requested_at"),
    _ts("finished_at", nullable=True, default=False),
    Column("requested_active_ms", BigInteger, nullable=True),
    PrimaryKeyConstraint("session_id", "call_id"),
    UniqueConstraint("session_id", "snapshot_id", "attempt", name="uq_model_call_attempt"),
    ForeignKeyConstraint(
        ["session_id", "snapshot_id"],
        [f"{APP}.knowledge_snapshot.session_id", f"{APP}.knowledge_snapshot.snapshot_id"],
        name="fk_model_call_snapshot",
    ),
    ForeignKeyConstraint(
        ["session_id", "parent_call_id"],
        [f"{APP}.model_call.session_id", f"{APP}.model_call.call_id"],
        name="fk_model_call_parent",
    ),
    CheckConstraint("attempt >= 1", name="attempt"),
    CheckConstraint(_in("status", MODEL_CALL_STATUSES), name="status"),
    CheckConstraint("timeout_ms IS NULL OR timeout_ms > 0", name="timeout_ms"),
    CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_ms"),
    schema=APP,
)

displayed_output = Table(
    "displayed_output",
    metadata,
    _session_fk(),
    Column("output_id", UUID(as_uuid=True), nullable=False),
    Column("call_id", UUID(as_uuid=True), nullable=True),
    Column("fallback", Text, nullable=False, server_default=text("'none'")),
    Column("shown_text", Text, nullable=False),
    Column("shown_sha256", Text, nullable=False),
    Column("message_id", UUID(as_uuid=True), nullable=True),
    Column("delivered_seq", BigInteger, nullable=False),
    Column("validator_version", Text, nullable=True),
    _ts("delivered_at"),
    PrimaryKeyConstraint("session_id", "output_id"),
    ForeignKeyConstraint(
        ["session_id", "call_id"],
        [f"{APP}.model_call.session_id", f"{APP}.model_call.call_id"],
        name="fk_displayed_output_call",
    ),
    ForeignKeyConstraint(
        ["session_id", "message_id"],
        [f"{APP}.message.session_id", f"{APP}.message.message_id"],
        name="fk_displayed_output_message",
    ),
    ForeignKeyConstraint(
        ["session_id", "delivered_seq"],
        [f"{APP}.event.session_id", f"{APP}.event.seq"],
        name="fk_displayed_output_event",
    ),
    CheckConstraint(
        _in("fallback", ("none", "deterministic_script", "deterministic_file_summary")),
        name="fallback",
    ),
    _sha("shown_sha256"),
    schema=APP,
)

# ----- answers, instruments and outcomes (INV-0305) ----------------------------------------------

answer_revision = Table(
    "answer_revision",
    metadata,
    _session_fk(),
    Column("crate", Integer, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("location_id", Text, nullable=False),
    Column("command_id", UUID(as_uuid=True), nullable=True),
    Column("accepted_seq", BigInteger, nullable=False),
    Column("active_ms", BigInteger, nullable=True),
    _ts("accepted_at"),
    PrimaryKeyConstraint("session_id", "crate", "revision"),
    ForeignKeyConstraint(
        ["session_id", "command_id"],
        [f"{APP}.command_receipt.session_id", f"{APP}.command_receipt.command_id"],
        name="fk_answer_revision_command",
        deferrable=True,
        initially="DEFERRED",
    ),
    CheckConstraint("crate BETWEEN 1 AND 8", name="crate"),
    CheckConstraint("revision >= 1", name="revision"),
    CheckConstraint("accepted_seq >= 1", name="accepted_seq"),
    schema=APP,
)

answer_final = Table(
    "answer_final",
    metadata,
    _session_fk(),
    Column("crate", Integer, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("location_id", Text, nullable=False),
    Column("finalized_seq", BigInteger, nullable=False),
    _ts("finalized_at"),
    PrimaryKeyConstraint("session_id", "crate"),
    ForeignKeyConstraint(
        ["session_id", "crate", "revision"],
        [
            f"{APP}.answer_revision.session_id",
            f"{APP}.answer_revision.crate",
            f"{APP}.answer_revision.revision",
        ],
        name="fk_answer_final_revision",
    ),
    CheckConstraint("crate BETWEEN 1 AND 8", name="crate"),
    CheckConstraint("finalized_seq >= 1", name="finalized_seq"),
    schema=APP,
)

confidence_response = Table(
    "confidence_response",
    metadata,
    _session_fk(),
    Column("crate", Integer, nullable=False),
    # Append-only revision per crate (like answer_revision): the row is the response as
    # recorded; a re-rating inserts revision+1 rather than rewriting history.
    Column("revision", Integer, nullable=False),
    Column("value", Integer, nullable=True),
    Column("status", Text, nullable=False),
    Column("instrument_version", Integer, nullable=False),
    Column("event_seq", BigInteger, nullable=False),
    _ts("recorded_at"),
    PrimaryKeyConstraint("session_id", "crate", "revision"),
    CheckConstraint("crate BETWEEN 1 AND 8", name="crate"),
    CheckConstraint("revision >= 1", name="revision"),
    CheckConstraint("value IS NULL OR value BETWEEN 0 AND 100", name="value"),
    CheckConstraint(_in("status", ("answered", "skipped", "missing")), name="status"),
    CheckConstraint("(status = 'answered') = (value IS NOT NULL)", name="answered_value"),
    CheckConstraint("event_seq >= 1", name="event_seq"),
    schema=APP,
)

manipulation_exposure = Table(
    "manipulation_exposure",
    metadata,
    _session_fk(),
    Column("exposure_id", UUID(as_uuid=True), nullable=False),
    Column("kind", Text, nullable=False),
    Column("artifact_id", Text, nullable=True),
    Column("artifact_version", Integer, nullable=True),
    Column("due_active_ms", BigInteger, nullable=True),
    Column("committed_seq", BigInteger, nullable=True),
    Column("received_seq", BigInteger, nullable=True),
    Column("rendered_seq", BigInteger, nullable=True),
    Column("acknowledged_seq", BigInteger, nullable=True),
    Column("own_rating", Integer, nullable=True),
    Column("assigned_values", JSONB, nullable=True),
    Column("status", Text, nullable=False, server_default=text("'scheduled'")),
    _ts("scheduled_at"),
    PrimaryKeyConstraint("session_id", "exposure_id"),
    CheckConstraint(
        _in(
            "kind",
            (
                "pressure_checkin",
                "one_minute_warning",
                "pe_intro",
                "pe_callout",
                "pe_strip",
                "pe_system_message",
                "peer_intro",
            ),
        ),
        name="kind",
    ),
    CheckConstraint(
        _in("status", ("scheduled", "committed", "rendered", "acknowledged")),
        name="status",
    ),
    CheckConstraint("own_rating IS NULL OR own_rating BETWEEN 0 AND 10", name="own_rating"),
    Index("ix_manipulation_exposure_session_kind", "session_id", "kind"),
    schema=APP,
)

item_response = Table(
    "item_response",
    metadata,
    _session_fk(),
    Column("instrument_id", Text, nullable=False),
    Column("item_id", Text, nullable=False),
    Column("instrument_version", Integer, nullable=False),
    Column("response", JSONB, nullable=True),
    Column("status", Text, nullable=False),
    Column("missing_reason", Text, nullable=True),
    Column("revision_count", Integer, nullable=False, server_default=text("1")),
    Column("first_seq", BigInteger, nullable=False),
    Column("last_seq", BigInteger, nullable=False),
    _ts("recorded_at"),
    _ts("updated_at"),
    PrimaryKeyConstraint("session_id", "instrument_id", "item_id"),
    CheckConstraint(_in("status", ("answered", "skipped", "missing")), name="status"),
    CheckConstraint("status <> 'missing' OR missing_reason IS NOT NULL", name="missing_reason"),
    CheckConstraint("last_seq >= first_seq", name="seq_order"),
    schema=APP,
)

RESULT_STATUSES = ("scored", "technical_stop", "withdrawn", "excluded")
ENTITLEMENT_STATUSES = ("pending", "approved", "paid", "void", "ineligible")
COMPLETION_OUTCOMES = ("completed", "technical_stop", "withdrawn", "declined", "abandoned")
DATA_USE_CHOICES = ("use_all", "use_no_free_text", "withdraw_all")

result = Table(
    "result",
    metadata,
    _session_fk(),
    Column("scoring_version", Text, nullable=False),
    Column("correct_total", Integer, nullable=False),
    Column("correct_teamdep", Integer, nullable=False),
    Column("correct_ownfile", Integer, nullable=False),
    Column("bonus_cents", Integer, nullable=False, server_default=text("0")),
    Column("status", Text, nullable=False),
    Column("counts", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("finalized_seq", BigInteger, nullable=False),
    _ts("finalized_at"),
    PrimaryKeyConstraint("session_id"),
    CheckConstraint(_in("status", RESULT_STATUSES), name="status"),
    CheckConstraint("bonus_cents >= 0", name="bonus_cents"),
    CheckConstraint(
        "correct_total >= 0 AND correct_teamdep >= 0 AND correct_ownfile >= 0",
        name="correct_counts",
    ),
    CheckConstraint("correct_total = correct_teamdep + correct_ownfile", name="correct_sum"),
    CheckConstraint("finalized_seq >= 1", name="finalized_seq"),
    schema=APP,
)

payment_entitlement = Table(
    "payment_entitlement",
    metadata,
    _session_fk(),
    Column("kind", Text, nullable=False),
    Column("cents", Integer, nullable=False),
    Column("policy_artifact_id", Text, nullable=False),
    Column("policy_version", Integer, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'pending'")),
    Column("decided_by", Text, nullable=True),
    _ts("decided_at", nullable=True, default=False),
    _ts("paid_at", nullable=True, default=False),
    Column("paid_ref", Text, nullable=True),
    _ts("created_at"),
    PrimaryKeyConstraint("session_id", "kind"),
    CheckConstraint(_in("kind", ("base", "bonus")), name="kind"),
    CheckConstraint(_in("status", ENTITLEMENT_STATUSES), name="status"),
    CheckConstraint("cents >= 0", name="cents"),
    CheckConstraint("(status = 'paid') = (paid_at IS NOT NULL)", name="paid_consistency"),
    schema=APP,
)

completion_receipt = Table(
    "completion_receipt",
    metadata,
    _session_fk(),
    Column("outcome", Text, nullable=False),
    Column("reconciled", JSONB, nullable=False),
    Column("handoff", JSONB, nullable=True),
    Column("final_event_seq", BigInteger, nullable=False),
    _ts("occurred_at"),
    PrimaryKeyConstraint("session_id"),
    CheckConstraint(_in("outcome", COMPLETION_OUTCOMES), name="outcome"),
    CheckConstraint("final_event_seq >= 1", name="final_event_seq"),
    schema=APP,
)

data_use_choice = Table(
    "data_use_choice",
    metadata,
    _session_fk(),
    Column("choice_seq", Integer, nullable=False),
    Column("choice", Text, nullable=False),
    Column("recorded_seq", BigInteger, nullable=False),
    _ts("recorded_at"),
    PrimaryKeyConstraint("session_id", "choice_seq"),
    CheckConstraint(_in("choice", DATA_USE_CHOICES), name="choice"),
    CheckConstraint("choice_seq >= 1", name="choice_seq"),
    CheckConstraint("recorded_seq >= 1", name="recorded_seq"),
    schema=APP,
)

incident_interval = Table(
    "incident_interval",
    metadata,
    _session_fk(),
    Column("incident_id", UUID(as_uuid=True), nullable=False),
    Column("kind", Text, nullable=False),
    Column("clock_effect", Text, nullable=False),
    _ts("started_at"),
    _ts("ended_at", nullable=True, default=False),
    Column("active_ms_at_start", BigInteger, nullable=True),
    Column("active_ms_at_end", BigInteger, nullable=True),
    Column("notes", Text, nullable=True),
    PrimaryKeyConstraint("session_id", "incident_id"),
    CheckConstraint(
        _in("kind", ("server_outage", "client_disconnect", "worker_restart", "other")),
        name="kind",
    ),
    CheckConstraint(_in("clock_effect", ("paused", "excluded", "counted")), name="clock_effect"),
    CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="order"),
    schema=APP,
)

admin_audit = Table(
    "admin_audit",
    metadata,
    Column("audit_id", UUID(as_uuid=True), primary_key=True),
    Column(
        "session_id",
        UUID(as_uuid=True),
        ForeignKey(f"{APP}.session.session_id"),
        nullable=True,
    ),
    Column("actor", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("payload", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    _ts("occurred_at"),
    Index("ix_admin_audit_session", "session_id"),
    schema=APP,
)

redaction_review = Table(
    "redaction_review",
    metadata,
    Column("review_id", UUID(as_uuid=True), primary_key=True),
    Column(
        "session_id",
        UUID(as_uuid=True),
        ForeignKey(f"{APP}.session.session_id"),
        nullable=True,
    ),
    Column("kind", Text, nullable=False),
    Column("source_ref", Text, nullable=False),
    Column("scrubbed_ref", Text, nullable=True),
    Column("scrubbed_sha256", Text, nullable=True),
    Column("reviewer", Text, nullable=False),
    Column("notes", Text, nullable=True),
    _ts("reviewed_at"),
    Index("ix_redaction_review_session", "session_id"),
    schema=APP,
)

# ----- coding interfaces (INV-0306; immutable constraints arrive with WP12a) --------------------

CODING_RUN_STATUSES = ("pending", "running", "completed", "failed")
CODING_RESULT_STATUSES = ("coded", "not_coded", "failed", "abstained")
GATE_STATUSES = ("pending", "passed", "failed", "waived")

coding_run = Table(
    "coding_run",
    metadata,
    Column("run_id", UUID(as_uuid=True), primary_key=True),
    Column("corpus", JSONB, nullable=False),
    Column("corpus_sha256", Text, nullable=False),
    Column("rubric_artifact_id", Text, nullable=False),
    Column("rubric_version", Integer, nullable=False),
    Column("prompt_artifact_id", Text, nullable=False),
    Column("prompt_version", Integer, nullable=False),
    Column("model", Text, nullable=False),
    Column("model_digest", Text, nullable=True),
    Column("parameters", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("code_version", Text, nullable=False),
    Column("validator_version", Text, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'pending'")),
    Column("coverage", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_by", Text, nullable=False),
    _ts("created_at"),
    _ts("finished_at", nullable=True, default=False),
    CheckConstraint(_in("status", CODING_RUN_STATUSES), name="status"),
    _sha("corpus_sha256"),
    schema=APP,
)

coding_result = Table(
    "coding_result",
    metadata,
    Column("run_id", UUID(as_uuid=True), ForeignKey(f"{APP}.coding_run.run_id"), nullable=False),
    Column("subject_key", Text, nullable=False),
    Column(
        "session_id",
        UUID(as_uuid=True),
        ForeignKey(f"{APP}.session.session_id"),
        nullable=True,
    ),
    Column("status", Text, nullable=False),
    Column("codes", JSONB, nullable=True),
    Column("adjudication", JSONB, nullable=True),
    Column("supersedes_run_id", UUID(as_uuid=True), nullable=True),
    Column("supersedes_subject_key", Text, nullable=True),
    _ts("recorded_at"),
    PrimaryKeyConstraint("run_id", "subject_key"),
    ForeignKeyConstraint(
        ["supersedes_run_id", "supersedes_subject_key"],
        [f"{APP}.coding_result.run_id", f"{APP}.coding_result.subject_key"],
        name="fk_coding_result_supersedes",
    ),
    CheckConstraint(_in("status", CODING_RESULT_STATUSES), name="status"),
    CheckConstraint("status <> 'coded' OR codes IS NOT NULL", name="coded_needs_codes"),
    CheckConstraint(
        "(supersedes_run_id IS NULL) = (supersedes_subject_key IS NULL)", name="supersedes_pair"
    ),
    schema=APP,
)

validation_gate = Table(
    "validation_gate",
    metadata,
    Column("gate_id", UUID(as_uuid=True), primary_key=True),
    Column("run_id", UUID(as_uuid=True), ForeignKey(f"{APP}.coding_run.run_id"), nullable=False),
    Column("name", Text, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'pending'")),
    Column("thresholds", JSONB, nullable=False),
    Column("measured", JSONB, nullable=True),
    Column("denominators", JSONB, nullable=True),
    Column("adjudication", JSONB, nullable=True),
    Column("decided_by", Text, nullable=True),
    _ts("decided_at", nullable=True, default=False),
    _ts("created_at"),
    UniqueConstraint("run_id", "name"),
    CheckConstraint(_in("status", GATE_STATUSES), name="status"),
    schema=APP,
)

# ----- retention and export indices (INV-0309) ---------------------------------------------------

Index(
    "ix_platform_link_purge",
    platform_link.c.purge_after,
    postgresql_where=text("purge_after IS NOT NULL"),
)
Index("ix_session_batch", session.c.batch_id)
Index("ix_event_session_type", event.c.session_id, event.c.type)
Index("ix_result_status", result.c.status)

IMMUTABLE_TABLES = (
    "experiment_manifest",
    "consent_event",
    "stage_transition",
    "draw_log",
    "world_snapshot",
    "record",
    "command_receipt",
    "event",
    "request",
    "share",
    "knowledge_snapshot",
    "claim",
    "claim_source",
    "displayed_output",
    "answer_revision",
    "answer_final",
    "confidence_response",
    "result",
    "completion_receipt",
    "data_use_choice",
    "admin_audit",
    "redaction_review",
)
"""Append-only tables: UPDATE/DELETE rejected by trigger (retention deletion arrives with WP13
as an audited, separately migrated mechanism)."""

# Per-table privileges applied by the migrations, independent of the bootstrap defaults.
RUNTIME_READ_WRITE = ("SELECT", "INSERT", "UPDATE", "DELETE")
RUNTIME_APPEND_ONLY = ("SELECT", "INSERT")
RUNTIME_READ_ONLY = ("SELECT",)
# Offline coding products: WP12a writes them under a role it defines; runtime and research may
# read them (research exports join coding results), neither writes them through this service.
RUNTIME_NO_ACCESS = ("coding_run", "coding_result", "validation_gate")
# Compensation reconciliation reads payment state and the completion handoff; it may mark an
# entitlement paid, nothing else.
COMPENSATION_SELECT = ("payment_entitlement", "completion_receipt", "result")
COMPENSATION_UPDATE_COLUMNS = {"payment_entitlement": ("status", "paid_at", "paid_ref")}
