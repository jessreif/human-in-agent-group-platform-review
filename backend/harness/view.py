"""Bot-facing session projection: what the simulated participant can see and do.

Read-only queries over the durable state — the same tables the participant UI and
exports read from — plus the bound manifest/policy so a persona can only attempt
actions the channels actually permit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine

from app.db import schema as s
from app.domain.manifests.artifacts import VoiceBankBody
from app.domain.manifests.binding import BoundManifest
from app.domain.manifests.store import ContentStore
from app.timing.policy import resolve_policy

TERMINAL = {"completion", "withdrawn", "excluded", "abandoned", "technical_stop"}


@dataclass(frozen=True)
class ChatLine:
    seq: int
    actor: str
    body: str
    crate: int | None
    record_id: str | None


@dataclass(frozen=True)
class FileEntry:
    """Participant-visible file fields only — mirrors the /workbench file payload
    (worlds.py): record_id, crate, stamp, body, event_type, source, shared_by.
    ``record.location_id`` is deliberately absent: it is a research field the UI
    never shows, and personas must not answer from hidden truth (BA-80)."""

    record_id: str
    crate: int
    stamp: str
    body: str
    event_type: str
    source: str
    shared_by: str | None


@dataclass
class View:
    stage: str
    revision: int
    active_ms: int
    session_arm: str | None
    channels: frozenset[str]
    structured_requests: bool
    chat: list[ChatLine] = field(default_factory=list)
    file: list[FileEntry] = field(default_factory=list)
    assigned_crates: list[int] = field(default_factory=list)
    # 2026-10-15 audit W06: the session world's total crate count — personas
    # bound their request targets to it (4/8/12 under overlap_v1; 8 legacy).
    n_crates: int = 8
    open_asks: list[dict[str, Any]] = field(default_factory=list)  # scripted asks → participant
    greeting_options: dict[str, str] = field(default_factory=dict)
    greeting_allowed: bool = False

    @property
    def done(self) -> bool:
        return self.stage in TERMINAL

    def file_for(self, crate: int) -> list[FileEntry]:
        return [e for e in self.file if e.crate == crate]


def participant_view(
    engine: Engine, store: ContentStore, session_id: uuid.UUID, bound: BoundManifest | None
) -> View:
    """Snapshot of session state exactly as the bot may observe it."""
    with engine.connect() as conn:
        session_row = (
            conn.execute(select(s.session).where(s.session.c.session_id == session_id))
            .mappings()
            .one()
        )
        stage, revision, arm = (
            session_row["stage"],
            session_row["revision"],
            session_row["arm"],
        )
        from app.services import activetime

        active_ms = activetime.active_ms(
            conn, session_id, conn.execute(text("SELECT clock_timestamp()")).scalar_one()
        )

        visible = (s.message.c.committed_seq.is_not(None)) | (
            (s.message.c.actor == "participant") & (s.message.c.status == "accepted")
        )
        ordering = func.coalesce(s.message.c.committed_seq, s.message.c.accepted_seq)

        def _lines(channel: str) -> list[ChatLine]:
            return [
                ChatLine(
                    seq=int(r.ordering),
                    actor=r.actor,
                    body=r.body or "",
                    crate=r.crate,
                    record_id=str(r.record_id) if r.record_id else None,
                )
                for r in conn.execute(
                    select(
                        ordering.label("ordering"),
                        s.message.c.actor,
                        s.message.c.body,
                        s.message.c.crate,
                        s.message.c.record_id,
                    )
                    .where(
                        s.message.c.session_id == session_id,
                        s.message.c.channel == channel,
                        visible,
                    )
                    .order_by(ordering)
                )
            ]

        chat = _lines("team")
        assistant = _lines("assistant")
        # BA-79: record is keyed by (session_id, record_id) — the join must carry
        # both columns or a multi-session batch can pull another session's rows.
        file_rows = conn.execute(
            select(
                s.file_membership.c.record_id,
                s.record.c.crate,
                s.record.c.stamp,
                s.record.c.body,
                s.record.c.event_type,
                s.file_membership.c.source,
            )
            .select_from(
                s.file_membership.join(
                    s.record,
                    (s.record.c.session_id == s.file_membership.c.session_id)
                    & (s.record.c.record_id == s.file_membership.c.record_id),
                )
            )
            .where(
                s.file_membership.c.session_id == session_id,
                s.file_membership.c.source.in_(("initial", "added_from_team")),
            )
            .order_by(s.record.c.timeline_ordinal)
        ).all()
        # "Shared by {seat}" provenance — same rule as /workbench (worlds.py):
        # teammate shares only, newest delivery wins.
        shared_by = {
            str(r["record_id"]): str(r["sharer"])
            for r in conn.execute(
                select(s.share.c.record_id, s.share.c.sharer)
                .where(
                    s.share.c.session_id == session_id,
                    s.share.c.sharer != "participant",
                )
                .order_by(s.share.c.delivered_seq.desc())
            ).mappings()
        }
        assigned = conn.execute(
            text(
                "SELECT a.crate FROM app.world_snapshot ws, "
                "jsonb_array_elements_text(ws.content->'assigned') a(crate) "
                "WHERE ws.session_id = :sid"
            ),
            {"sid": session_id},
        ).all()
        n_crates_row = conn.execute(
            text(
                "SELECT COALESCE(content->'world_params'->>'n_crates', "
                "jsonb_array_length(content->'crates')::text) AS n "
                "FROM app.world_snapshot WHERE session_id = :sid"
            ),
            {"sid": session_id},
        ).first()
        open_asks = [
            {"episode_id": str(r.episode_id), "crate": r.crate, "seat": r.requester}
            for r in conn.execute(
                select(
                    s.request_episode.c.episode_id,
                    s.request.c.crate,
                    s.request.c.requester,
                )
                .join(s.request, s.request.c.request_id == s.request_episode.c.request_id)
                .where(
                    s.request_episode.c.session_id == session_id,
                    s.request_episode.c.closed_at.is_(None),
                    s.request.c.responder == "participant",
                )
            )
        ]

    if bound is None:
        from app.services import lifecycle

        with engine.connect() as conn:
            bound = lifecycle.bound_for_session(conn, store, dict(session_row))
    policy = resolve_policy(bound, arm or bound.manifest.arms[0])
    channels = frozenset(policy.channels_open(active_ms))
    # interaction_mode is a string ("structured_and_nl" | "nl_only") — structured
    # requests exist only in the former.
    structured = bound.manifest.interaction_mode != "nl_only"
    voice = bound.artifacts.get("teammates.voice_bank")
    greetings: dict[str, str] = {}
    if voice is not None:
        for seat in ("JT", "PM"):
            body = voice.artifact.body
            pool = body.seats.get(seat) if isinstance(body, VoiceBankBody) else None
            if pool is not None and getattr(pool, "greetings", None):
                greetings[seat] = pool.greetings[0]
    greeting_allowed = stage == "task" and active_ms < 30_000 and bool(greetings)
    return View(
        stage=stage,
        revision=revision,
        active_ms=active_ms,
        session_arm=arm,
        channels=channels,
        structured_requests=bool(structured),
        chat=chat + assistant,
        file=[
            FileEntry(
                record_id=str(r.record_id),
                crate=r.crate,
                stamp=r.stamp,
                body=r.body,
                event_type=r.event_type,
                source=r.source,
                shared_by=shared_by.get(str(r.record_id)),
            )
            for r in file_rows
        ],
        assigned_crates=sorted(int(r[0]) for r in assigned),
        n_crates=int(n_crates_row[0]) if n_crates_row is not None else 8,
        open_asks=open_asks,
        greeting_options=greetings,
        greeting_allowed=greeting_allowed,
    )
