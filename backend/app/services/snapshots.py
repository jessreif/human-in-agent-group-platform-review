"""Immutable per-call knowledge snapshots (INV-0507; spec §7.5).

Every generation/retrieval attempt that needs model or teammate context gets a durable
``knowledge_snapshot`` row: the authorized record set (IDs + content hashes) fixed at
accept/grant time, the visible event cursor, prior delivered commitments and
outstanding requests, and the causal parent command/job. Later events can never widen
a snapshot — a worker delivering a reply must read exactly these rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Literal

from sqlalchemy import Connection, insert, select

from app.db import schema as s
from app.db.tx import db_now

SnapshotSeat = Literal["JT", "PM", "assistant"]
SnapshotPurpose = Literal["assistant_context", "structured_retrieval", "social_reply", "crosstalk"]


def record_manifest(
    conn: Connection, session_id: uuid.UUID, record_ids: Sequence[str]
) -> list[dict[str, str]]:
    """[(record_id, sha256)] for the authorized set, in the order given."""
    if not record_ids:
        return []
    rows = (
        conn.execute(
            select(s.record).where(
                s.record.c.session_id == session_id,
                s.record.c.record_id.in_(record_ids),
            )
        )
        .mappings()
        .all()
    )
    from app.services.evidence import EvidenceRecord

    by_id = {r["record_id"]: r for r in rows}
    if set(record_ids) != set(by_id):
        raise ValueError("snapshot_record_missing")
    return [
        {
            "record_id": rid,
            "sha256": by_id[rid]["content_sha256"],
            "semantic_sha256": EvidenceRecord.parse(dict(by_id[rid])).digest(),
            "format": "semantic_record_v1",
        }
        for rid in dict.fromkeys(record_ids)
    ]


def build_snapshot(
    conn: Connection,
    session_id: uuid.UUID,
    *,
    seat: SnapshotSeat,
    purpose: SnapshotPurpose,
    visible_event_seq: int,
    record_ids: Sequence[str],
    created_event_seq: int,
    prompt_sha256: str | None = None,
    parent_command_id: uuid.UUID | None = None,
    parent_job_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Pin and persist one snapshot; returns its ``snapshot_id``."""
    agent = (
        conn.execute(
            select(s.agent_state.c.commitments, s.agent_state.c.outstanding_requests).where(
                s.agent_state.c.session_id == session_id, s.agent_state.c.seat == seat
            )
        )
        .mappings()
        .one_or_none()
    )
    open_requests: list[uuid.UUID] = list(
        conn.execute(
            select(s.request_episode.c.request_id).where(
                s.request_episode.c.session_id == session_id,
                s.request_episode.c.closed_at.is_(None),
            )
        ).scalars()
    )
    snapshot_id = uuid.uuid4()
    conn.execute(
        insert(s.knowledge_snapshot).values(
            session_id=session_id,
            snapshot_id=snapshot_id,
            seat=seat,
            purpose=purpose,
            visible_event_seq=visible_event_seq,
            record_manifest=record_manifest(conn, session_id, record_ids),
            commitments=agent["commitments"] if agent else [],
            outstanding_requests=[str(r) for r in open_requests],
            prompt_sha256=prompt_sha256,
            parent_command_id=parent_command_id,
            parent_job_id=parent_job_id,
            created_event_seq=created_event_seq,
            created_at=db_now(conn),
        )
    )
    return snapshot_id
