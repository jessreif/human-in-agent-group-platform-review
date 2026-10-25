"""Build coding inputs from the committed ``SessionRows`` projection (INV-1202).

History is assembled from authoritative structures only — record shares to the team
channel (broadcast), file memberships (participant_file), and earlier delivered
messages — never from parsing. Messages carrying ``record_id`` produce an
``Attachment`` whose fields come straight from the record row, so authenticated
evidence bypasses semantic matching by construction.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.coding.types import (
    CODE_VERSION,
    VALIDATOR_VERSION,
    Attachment,
    HistoryItem,
    HistoryScope,
    ResultRow,
    Subject,
)
from app.measurement.rows import SessionRows, load_session_rows


@dataclass(frozen=True)
class SessionSubject:
    """A codable unit bound to its provenance: the durable message row."""

    subject_key: str  # "<session_id>:msg:<message_id>"
    session_id: uuid.UUID
    subject: Subject
    history: tuple[HistoryItem, ...]


def _attachment(record: Mapping[str, Any]) -> Attachment:
    return Attachment(
        record_id=str(record["record_id"]),
        crate=int(record["crate"]),
        location_id=None if record.get("location_id") is None else str(record["location_id"]),
        event_type=record["event_type"],
        stamp=None if record.get("stamp") is None else str(record["stamp"]),
    )


def session_subjects(rows: SessionRows) -> tuple[SessionSubject, ...]:
    """Order-deterministic coding units for one session.

    ``history`` for each message contains every share/file-membership/message that
    committed *before* it — a single message can never establish its own novelty.
    """
    records = {str(r["record_id"]): r for r in rows.records}
    file_seq = {
        str(f["record_id"]): int(f["acquired_event_seq"] or 0) for f in rows.file_memberships
    }
    timeline: list[HistoryItem] = []
    for share in rows.shares:
        record = records.get(str(share["record_id"]))
        if record is None:
            continue
        scope: HistoryScope = (
            "broadcast"
            if [r for r in share.get("recipients") or () if r != share["sharer"]]
            else "participant_file"
        )
        timeline.append(
            HistoryItem(
                seq=int(share["delivered_seq"]),
                actor=str(share["sharer"]),
                kind="record",
                scope=scope,
                record_id=str(record["record_id"]),
                crate=int(record["crate"]),
                location_id=(
                    None if record.get("location_id") is None else str(record["location_id"])
                ),
                event_type=record["event_type"],
            )
        )
    for member in rows.file_memberships:
        record = records.get(str(member["record_id"]))
        if record is None:
            continue
        timeline.append(
            HistoryItem(
                seq=file_seq[str(member["record_id"])],
                actor="participant",
                kind="record",
                scope="participant_file",
                record_id=str(record["record_id"]),
                crate=int(record["crate"]),
                location_id=(
                    None if record.get("location_id") is None else str(record["location_id"])
                ),
                event_type=record["event_type"],
            )
        )
    for msg in rows.messages:
        if msg.get("status") != "delivered":
            continue
        timeline.append(
            HistoryItem(
                seq=int(msg["committed_seq"] or msg["accepted_seq"]),
                actor=str(msg["actor"]),
                kind="message",
                scope="broadcast",
                record_id=None if msg.get("record_id") is None else str(msg["record_id"]),
                body=str(msg["body"]),
            )
        )
    timeline.sort(key=lambda h: h.seq)

    out: list[SessionSubject] = []
    for msg in rows.messages:
        if msg.get("status") != "delivered":
            continue
        seq = int(msg["committed_seq"] or msg["accepted_seq"])
        attachments: list[Attachment] = []
        if msg.get("record_id") is not None:
            record = records.get(str(msg["record_id"]))
            if record is not None:
                attachments.append(_attachment(record))
        out.append(
            SessionSubject(
                subject_key=f"{rows.session_id}:msg:{msg['message_id']}",
                session_id=rows.session_id,
                subject=Subject(
                    seq=seq,
                    actor=str(msg["actor"]),
                    channel=str(msg["channel"]),
                    body=str(msg["body"]),
                    context_crate=None if msg.get("crate") is None else int(msg["crate"]),
                    attachments=tuple(attachments),
                ),
                history=tuple(h for h in timeline if h.seq < seq),
            )
        )
    return tuple(out)


def persist_session_run(
    conn: Any,
    store: Any,
    session_ids: list[uuid.UUID],
    *,
    rubric_key: str = "coding.rubric@v1",
    prompt_key: str = "coding.prompt@v1",
    created_by: str,
) -> uuid.UUID:
    """Code every delivered message in the given sessions and persist one immutable
    ``coding_run`` + ``coding_result`` rows (INV-1205). The corpus snapshot binds the
    session id list + each row's manifest/compatibility identity; ``model`` is
    ``deterministic`` (the model path stays dormant pending D05)."""
    from sqlalchemy import select

    from app.coding.coder import CODER
    from app.coding.runs import RunSpec, create_run, finish_run, insert_results, start_run
    from app.db import schema as s
    from app.domain.manifests.canonical import content_hash

    rubric = store.artifacts[rubric_key]
    prompt = store.artifacts[prompt_key]
    sessions_meta = []
    for sid in session_ids:
        row = (
            conn.execute(
                select(
                    s.session.c.manifest_id,
                    s.session.c.manifest_sha256,
                    s.session.c.compatibility_key,
                ).where(s.session.c.session_id == sid)
            )
            .mappings()
            .one_or_none()
        )
        sessions_meta.append(
            {
                "session_id": str(sid),
                "manifest_id": None if row is None else row["manifest_id"],
                "manifest_sha256": None if row is None else row["manifest_sha256"],
                "compatibility_key": None if row is None else row["compatibility_key"],
            }
        )
    corpus_doc = {
        "kind": "session_messages",
        "sessions": sessions_meta,
        "selection": {"session_ids": [str(i) for i in session_ids]},
    }
    run_id = create_run(
        conn,
        RunSpec(
            corpus=corpus_doc,
            corpus_sha256=content_hash(corpus_doc),
            rubric_artifact_id=rubric.artifact.artifact_id,
            rubric_version=rubric.artifact.version,
            prompt_artifact_id=prompt.artifact.artifact_id,
            prompt_version=prompt.artifact.version,
            model="deterministic",
            model_digest=None,
            parameters={},
            code_version=CODE_VERSION,
            validator_version=VALIDATOR_VERSION,
            created_by=created_by,
        ),
    )
    start_run(conn, run_id)
    rows: list[ResultRow] = []
    for sid in session_ids:
        for view in session_subjects(load_session_rows(conn, sid)):
            result = CODER.code(view.subject, view.history)
            rows.append(
                ResultRow(
                    subject_key=view.subject_key,
                    status=result.status,
                    codes=result.codes,
                    adjudication=result.adjudication,
                    session_id=view.session_id,
                )
            )
    insert_results(conn, run_id, rows)
    finish_run(conn, run_id, status="completed", expected_subjects=len(rows))
    return run_id
