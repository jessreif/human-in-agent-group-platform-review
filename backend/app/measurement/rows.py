"""Session row loading for the measurement layer (INV-1102..1105; spec §9).

``SessionRows`` is an immutable, in-memory projection of every committed record the
measures need. ``load_session_rows`` is the only place this package issues SQL; every
measure function consumes the projection, so measures are pure, deterministic, and
trivially testable with synthetic row dicts — and can never write to the database by
construction.

All queries are scoped by ``session_id`` (composite keys make cross-session reads
impossible) and ordered by the tables' natural keys so downstream output is stable.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Connection, select

from app.db import schema as s


def _rows(
    conn: Connection, table: Any, session_id: uuid.UUID, *order: Any
) -> tuple[dict[str, Any], ...]:
    stmt = select(table).where(table.c.session_id == session_id)
    if order:
        stmt = stmt.order_by(*order)
    return tuple(dict(r) for r in conn.execute(stmt).mappings().all())


@dataclass(frozen=True)
class SessionRows:
    """Committed-record projection for one session. All fields are immutable."""

    session_id: uuid.UUID
    session: Mapping[str, Any] | None = None
    world_content: Mapping[str, Any] | None = None
    command_receipts: tuple[Mapping[str, Any], ...] = ()
    events: tuple[Mapping[str, Any], ...] = ()
    requests: tuple[Mapping[str, Any], ...] = ()
    request_episodes: tuple[Mapping[str, Any], ...] = ()
    shares: tuple[Mapping[str, Any], ...] = ()
    messages: tuple[Mapping[str, Any], ...] = ()
    records: tuple[Mapping[str, Any], ...] = ()
    delivery_receipts: tuple[Mapping[str, Any], ...] = ()
    answer_revisions: tuple[Mapping[str, Any], ...] = ()
    answer_finals: tuple[Mapping[str, Any], ...] = ()
    confidence_responses: tuple[Mapping[str, Any], ...] = ()
    manipulation_exposures: tuple[Mapping[str, Any], ...] = ()
    item_responses: tuple[Mapping[str, Any], ...] = ()
    claims: tuple[Mapping[str, Any], ...] = ()
    claim_sources: tuple[Mapping[str, Any], ...] = ()
    clock_intervals: tuple[Mapping[str, Any], ...] = ()
    file_memberships: tuple[Mapping[str, Any], ...] = ()
    seat_holdings: tuple[Mapping[str, Any], ...] = ()
    knowledge_snapshots: tuple[Mapping[str, Any], ...] = ()
    model_calls: tuple[Mapping[str, Any], ...] = ()
    displayed_outputs: tuple[Mapping[str, Any], ...] = ()
    scheduled_jobs: tuple[Mapping[str, Any], ...] = ()
    incident_intervals: tuple[Mapping[str, Any], ...] = ()

    # ----- derived lookups (computed once, deterministic) ---------------------------------

    _event_by_seq: Mapping[int, Mapping[str, Any]] = field(default_factory=dict, repr=False)

    def event_at(self, seq: int | None) -> Mapping[str, Any] | None:
        """The committed event at ``seq`` (sequence numbers are per-session, gapless)."""
        if seq is None:
            return None
        return self._event_by_seq.get(int(seq))

    def event_active_ms(self, seq: int | None) -> int | None:
        """Active-time stamp of the event at ``seq``; None when the event or its stamp is
        absent (missing stays missing — never 0)."""
        ev = self.event_at(seq)
        if ev is None:
            return None
        ms = ev.get("active_ms")
        return None if ms is None else int(ms)

    def event_time(self, seq: int | None) -> Any:
        ev = self.event_at(seq)
        return None if ev is None else ev.get("server_time")

    @property
    def last_event_seq(self) -> int:
        """Highest committed event sequence — the consistent observation horizon."""
        return max((int(e["seq"]) for e in self.events), default=0)

    @property
    def active_end_ms(self) -> int | None:
        """Active elapsed at the observation horizon: the latest event active stamp, or
        the final closed interval's frozen elapsed when no event carries one. None when
        the session never had an active clock."""
        stamps = [int(e["active_ms"]) for e in self.events if e.get("active_ms") is not None]
        stamps.extend(int(i["active_ms_at_start"]) for i in self.clock_intervals)
        return max(stamps) if stamps else None

    # ----- world-content accessors --------------------------------------------------------

    @property
    def assigned_crates(self) -> tuple[int, ...]:
        if self.world_content is None:
            return ()
        return tuple(int(c) for c in self.world_content.get("assigned", ()))

    @property
    def role_of(self) -> dict[int, str]:
        if self.world_content is None:
            return {}
        return {int(k): str(v) for k, v in self.world_content.get("role_of", {}).items()}

    @property
    def truth(self) -> dict[int, str]:
        """crate -> true hideout (restricted: research products only, spec §9.2)."""
        if self.world_content is None:
            return {}
        return {int(c["num"]): str(c["truth"]) for c in self.world_content.get("crates", ())}

    @property
    def decisive_records(self) -> dict[int, str]:
        """team-dependent assigned crate -> its earliest truth-confirming record id."""
        if self.world_content is None:
            return {}
        return {
            int(k): str(v) for k, v in self.world_content.get("participant_decisive", {}).items()
        }


def load_session_rows(conn: Connection, session_id: uuid.UUID) -> SessionRows:
    """Load every committed record needed by the measures for one session, ordered
    deterministically. Read-only: SELECTs only."""
    session = (
        conn.execute(select(s.session).where(s.session.c.session_id == session_id))
        .mappings()
        .one_or_none()
    )
    world = (
        conn.execute(
            select(s.world_snapshot.c.content).where(s.world_snapshot.c.session_id == session_id)
        )
        .mappings()
        .one_or_none()
    )
    events = _rows(conn, s.event, session_id, s.event.c.seq)
    return SessionRows(
        session_id=session_id,
        session=None if session is None else dict(session),
        world_content=None if world is None else dict(world["content"]),
        command_receipts=_rows(
            conn, s.command_receipt, session_id, s.command_receipt.c.received_at
        ),
        events=events,
        requests=_rows(conn, s.request, session_id, s.request.c.accepted_seq),
        request_episodes=_rows(conn, s.request_episode, session_id, s.request_episode.c.opened_at),
        shares=_rows(conn, s.share, session_id, s.share.c.delivered_seq),
        messages=_rows(conn, s.message, session_id, s.message.c.accepted_seq),
        records=_rows(conn, s.record, session_id, s.record.c.record_id),
        delivery_receipts=_rows(conn, s.delivery_receipt, session_id, s.delivery_receipt.c.seq),
        answer_revisions=_rows(
            conn,
            s.answer_revision,
            session_id,
            s.answer_revision.c.crate,
            s.answer_revision.c.revision,
        ),
        answer_finals=_rows(conn, s.answer_final, session_id, s.answer_final.c.crate),
        confidence_responses=_rows(
            conn,
            s.confidence_response,
            session_id,
            s.confidence_response.c.crate,
            s.confidence_response.c.revision,
        ),
        manipulation_exposures=_rows(
            conn, s.manipulation_exposure, session_id, s.manipulation_exposure.c.scheduled_at
        ),
        item_responses=_rows(
            conn,
            s.item_response,
            session_id,
            s.item_response.c.instrument_id,
            s.item_response.c.item_id,
        ),
        claims=_rows(conn, s.claim, session_id, s.claim.c.created_at),
        claim_sources=_rows(conn, s.claim_source, session_id, s.claim_source.c.claim_source_id),
        clock_intervals=_rows(conn, s.clock_interval, session_id, s.clock_interval.c.started_at),
        file_memberships=_rows(conn, s.file_membership, session_id, s.file_membership.c.record_id),
        seat_holdings=_rows(conn, s.seat_holding, session_id, s.seat_holding.c.record_id),
        knowledge_snapshots=_rows(
            conn,
            s.knowledge_snapshot,
            session_id,
            s.knowledge_snapshot.c.created_event_seq,
        ),
        model_calls=_rows(conn, s.model_call, session_id, s.model_call.c.requested_at),
        displayed_outputs=_rows(
            conn, s.displayed_output, session_id, s.displayed_output.c.delivered_seq
        ),
        scheduled_jobs=_rows(conn, s.scheduled_job, session_id, s.scheduled_job.c.created_at),
        incident_intervals=_rows(
            conn, s.incident_interval, session_id, s.incident_interval.c.started_at
        ),
        _event_by_seq={int(e["seq"]): e for e in events},
    )
