"""Structured teammate retrieval (INV-0506; spec §2.1, §7.2, §7.3).

Deterministic: for a requested crate, each teammate's latest held record is the one
with the highest chronological ordinal; the lead is whichever seat's latest is newer.
The candidate set is pinned in a ``structured_retrieval`` knowledge snapshot at
accepted-request time, so the delivery job (WP06) returns exactly the record selected
here — never a later arrival and never fabricated content.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import Connection, func, select

from app.db import schema as s
from app.domain.manifests.schema import SEATS, Seat
from app.services import snapshots


def _seat_latest(
    conn: Connection, session_id: uuid.UUID, seat: Seat, crate: int
) -> Mapping[str, Any] | None:
    row = (
        conn.execute(
            select(s.record)
            .select_from(
                s.seat_holding.join(
                    s.record,
                    (s.record.c.session_id == s.seat_holding.c.session_id)
                    & (s.record.c.record_id == s.seat_holding.c.record_id),
                )
            )
            .where(
                s.seat_holding.c.session_id == session_id,
                s.seat_holding.c.holder == seat,
                s.record.c.crate == crate,
            )
            .order_by(s.record.c.timeline_ordinal.desc())
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row is not None else None


def select_for_crate(
    conn: Connection, session_id: uuid.UUID, crate: int
) -> tuple[Seat, Mapping[str, Any]] | None:
    """The demo rule: newest record across both teammates' holdings, attributed to the
    seat that holds it. None only when neither seat holds anything for the crate."""
    latest: dict[Seat, Mapping[str, Any] | None] = {
        seat: _seat_latest(conn, session_id, seat, crate) for seat in SEATS
    }
    candidates = [(seat, rec) for seat, rec in latest.items() if rec is not None]
    if not candidates:
        return None
    seat, rec = max(candidates, key=lambda item: item[1]["timeline_ordinal"])
    return seat, rec


def seat_latest_for_crate(
    conn: Connection, session_id: uuid.UUID, seat: Seat, crate: int
) -> Mapping[str, Any] | None:
    """One seat's newest held record for a crate (O-1/O-2): under broadcast
    semantics each teammate shares *its own* latest — never the other seat's."""
    return _seat_latest(conn, session_id, seat, crate)


def snapshot_for_seat_request(
    conn: Connection,
    session_id: uuid.UUID,
    seat: Seat,
    crate: int,
    *,
    accepted_event_seq: int,
    parent_command_id: uuid.UUID | None,
) -> uuid.UUID:
    """Pin a single seat's records for the crate (O-1 typed-ask card shares):
    the candidate set is that seat's holdings only, so ``lead_from_manifest``
    re-derives the seat's own latest at delivery time."""
    candidate_ids: list[str] = list(
        conn.execute(
            select(s.seat_holding.c.record_id)
            .select_from(
                s.seat_holding.join(
                    s.record,
                    (s.record.c.session_id == s.seat_holding.c.session_id)
                    & (s.record.c.record_id == s.seat_holding.c.record_id),
                )
            )
            .where(
                s.seat_holding.c.session_id == session_id,
                s.seat_holding.c.holder == seat,
                s.record.c.crate == crate,
            )
            .order_by(s.record.c.timeline_ordinal)
        ).scalars()
    )
    return snapshots.build_snapshot(
        conn,
        session_id,
        seat=seat,
        purpose="structured_retrieval",
        visible_event_seq=accepted_event_seq,
        record_ids=candidate_ids,
        created_event_seq=accepted_event_seq,
        parent_command_id=parent_command_id,
    )


def snapshot_for_request(
    conn: Connection,
    session_id: uuid.UUID,
    crate: int,
    *,
    accepted_event_seq: int,
    parent_command_id: uuid.UUID | None,
) -> uuid.UUID:
    """Pin the authorized candidate set (both seats' records for the crate) for a
    request accepted at ``accepted_event_seq``; the snapshot is attributed to the
    responding seat of the selected lead record."""
    selected = select_for_crate(conn, session_id, crate)
    if selected is None:
        raise ValueError(f"crate {crate}: no teammate holds any record")
    seat, _lead = selected
    candidate_ids: list[str] = list(
        conn.execute(
            select(s.seat_holding.c.record_id)
            .select_from(
                s.seat_holding.join(
                    s.record,
                    (s.record.c.session_id == s.seat_holding.c.session_id)
                    & (s.record.c.record_id == s.seat_holding.c.record_id),
                )
            )
            .where(
                s.seat_holding.c.session_id == session_id,
                s.seat_holding.c.holder != "participant",
                s.record.c.crate == crate,
            )
            .order_by(s.seat_holding.c.holder, s.record.c.timeline_ordinal)
        ).scalars()
    )
    return snapshots.build_snapshot(
        conn,
        session_id,
        seat=seat,
        purpose="structured_retrieval",
        visible_event_seq=accepted_event_seq,
        record_ids=candidate_ids,
        created_event_seq=accepted_event_seq,
        parent_command_id=parent_command_id,
    )


def lead_from_manifest(
    conn: Connection, session_id: uuid.UUID, manifest_rows: list[dict[str, str]]
) -> tuple[Seat, str] | None:
    """Re-derive the selected (seat, record_id) from a pinned snapshot manifest —
    what the delivery job must return. Deterministic over the pinned set."""
    if not manifest_rows:
        return None
    ids = [r["record_id"] for r in manifest_rows]
    rows = (
        conn.execute(
            select(s.record.c.record_id, s.record.c.timeline_ordinal, s.seat_holding.c.holder)
            .select_from(
                s.record.join(
                    s.seat_holding,
                    (s.seat_holding.c.session_id == s.record.c.session_id)
                    & (s.seat_holding.c.record_id == s.record.c.record_id),
                )
            )
            .where(
                s.record.c.session_id == session_id,
                s.record.c.record_id.in_(ids),
                s.seat_holding.c.holder != "participant",
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        return None
    lead = max(rows, key=lambda r: r["timeline_ordinal"])
    return lead["holder"], lead["record_id"]


def delivered_record_ids(conn: Connection, session_id: uuid.UUID) -> set[str]:
    """Records legitimately delivered to the participant: delivered messages carrying a
    record attachment plus delivered shares (spec §7.2 delivery != render != file)."""
    via_message: list[str] = list(
        conn.execute(
            select(s.message.c.record_id).where(
                s.message.c.session_id == session_id,
                s.message.c.record_id.is_not(None),
                s.message.c.status == "delivered",
            )
        ).scalars()
    )
    via_share: list[str] = list(
        conn.execute(
            select(s.share.c.record_id).where(s.share.c.session_id == session_id)
        ).scalars()
    )
    return set(via_message) | set(via_share)


def count_delivered(conn: Connection, session_id: uuid.UUID) -> int:
    return conn.execute(
        select(func.count()).select_from(s.share).where(s.share.c.session_id == session_id)
    ).scalar_one()
