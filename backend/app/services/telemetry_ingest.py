"""Client telemetry ingest: the kind allowlist, bounded batch normalization and
delivery-receipt recording (audit H5; moved out of ``app/api/participant.py``).
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection

from app.db import schema as s

# Bounded telemetry vocabulary (spec §8.2 / INV-0807): the exact client-side
# taxonomy — unknown kinds are dropped, never recorded under a different name.
# The file_* / share_attempt kinds carry ids/indexes/timings only (no content),
# same doctrine as the rest of the vocabulary.
TELEMETRY_KINDS = frozenset(
    {
        "receive",
        "render",
        "render_first_final",
        "visibility",
        "focus",
        "blur",
        "item_start",
        "item_end",
        "file_meta",
        "file_record_view",
        "file_record_dwell",
        "file_scroll_summary",
        "file_reorder",
        "file_order_final",
        "share_attempt",
        # 2026-10-16 chat read-side (cb-1.11): throttled chat-pane scrolls —
        # geometry only, no content. Pairs with the frontend TelemetryKind.
        "chat_scroll",
        # 2026-10-18 answer-bar visibility (zero-answer cohort hypothesis):
        # IntersectionObserver bucket + viewport geometry, no content.
        "submit_bar_view",
    }
)
_TELEMETRY_BATCH_MAX = 100
_TELEMETRY_DATA_MAX_CHARS = 4000


def telemetry_items(body: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Normalize a single post or a ``{kind: "batch", data: {events: [...]}}``
    envelope into bounded telemetry items."""
    raw: Any
    if body.get("kind") == "batch":
        data = body.get("data")
        raw = data.get("events") if isinstance(data, Mapping) else None
    else:
        raw = [body]
    if not isinstance(raw, list):
        return []
    items: list[Mapping[str, Any]] = []
    for item in raw[:_TELEMETRY_BATCH_MAX]:
        if (
            not isinstance(item, Mapping)
            or not isinstance(item.get("kind"), str)
            or item.get("kind") not in TELEMETRY_KINDS
        ):
            continue
        data = item.get("data")
        if data is not None and not isinstance(data, Mapping):
            data = None
        data_map = dict(data) if isinstance(data, Mapping) else {}
        try:
            if len(json.dumps(data_map, default=str)) > _TELEMETRY_DATA_MAX_CHARS:
                data_map = {}
        except (TypeError, ValueError):
            data_map = {}
        # Per-load clock identity (audit F06): client_ms is comparable only
        # within an epoch. Bounded string passthrough — anything else → None.
        epoch = item.get("client_epoch")
        items.append(
            {
                "kind": item["kind"],
                "client_ms": bounded_client_ms(item.get("client_monotonic_ms")),
                "client_epoch": epoch if isinstance(epoch, str) and 0 < len(epoch) <= 64 else None,
                "data": data_map,
            }
        )
    return items


def bounded_client_ms(value: Any) -> int | None:
    """A client clock is only useful inside int8/float-precision range (BA-49).

    Huge ints and non-finite floats would blow up the ``bigint`` bind downstream —
    an out-of-range value degrades to ``None``, never a 500."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if -(2**63) <= value <= 2**63 - 1 else None
    if isinstance(value, float) and math.isfinite(value):
        # Same int8 bound as the int branch — int(1e30) overflows bigint (BA-49).
        v = int(value)
        return v if -(2**63) <= v <= 2**63 - 1 else None
    return None


def record_delivery_receipt(
    conn: Connection,
    session_id: uuid.UUID,
    *,
    kind: str,
    data: Mapping[str, Any],
    client_ms: int | None,
    telemetry_seq: int,
    now: Any,
) -> None:
    """BA-05: turn a receive/render telemetry item into a durable ``delivery_receipt``
    row and advance ``manipulation_exposure`` receipt seqs where the referenced event
    is a committed exposure surface. Missing/unresolvable references stay missing
    (spec: a render receipt is availability evidence; missing receipts remain missing).
    """
    seq: int | None = None
    raw_seq = data.get("seq")
    if isinstance(raw_seq, bool):
        seq = None
    elif isinstance(raw_seq, int):
        # int8 range check BEFORE the bind — an out-of-range int surfaces as a
        # driver DataError, i.e. a 500 (BA-49).
        seq = raw_seq if -(2**63) <= raw_seq <= 2**63 - 1 else None
    elif isinstance(raw_seq, float) and math.isfinite(raw_seq):
        v = int(raw_seq)
        seq = v if -(2**63) <= v <= 2**63 - 1 else None
    elif isinstance(data.get("message_id"), str):
        # Render receipts name every painted row. Synthetic ids such as
        # ``share-<seq>`` are not ``message.message_id`` UUIDs; binding one
        # is a DataError that 500s the whole batch and drops the file and
        # share events sitting in it (BA-49). Unresolvable ids stay missing.
        try:
            message_id = uuid.UUID(data["message_id"])
        except (ValueError, TypeError, AttributeError):
            message_id = None
        if message_id is not None:
            seq = conn.execute(
                select(s.message.c.committed_seq).where(
                    s.message.c.session_id == session_id,
                    s.message.c.message_id == message_id,
                )
            ).scalar_one_or_none()
        else:
            # Own shared-intel cards use a synthetic UI id. Link only a
            # verified durable share in this session, never an arbitrary id.
            share_id = re.fullmatch(r"share-([1-9][0-9]{0,18})", data["message_id"])
            if share_id is not None and int(share_id[1]) <= 2**63 - 1:
                seq = conn.execute(
                    select(s.share.c.delivered_seq)
                    .where(
                        s.share.c.session_id == session_id,
                        s.share.c.delivered_seq == int(share_id[1]),
                    )
                    .limit(1)
                ).scalar_one_or_none()
    if seq is None or seq <= 0:
        return
    delivery_kind = data.get("delivery_kind")
    # Membership test only on a hashable str — an array/object ``delivery_kind``
    # would raise TypeError on ``in frozenset`` and surface as a 500 (BA-49).
    if not isinstance(delivery_kind, str) or delivery_kind not in s.DELIVERY_KINDS:
        delivery_kind = "replay" if data.get("replayed") else "initial"
    received = kind == "receive"
    rendered = kind in ("render", "render_first_final")
    existing = (
        conn.execute(
            select(s.delivery_receipt.c.received_at, s.delivery_receipt.c.rendered_at).where(
                s.delivery_receipt.c.session_id == session_id,
                s.delivery_receipt.c.seq == seq,
                s.delivery_receipt.c.recipient == "participant",
                s.delivery_receipt.c.kind == delivery_kind,
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing is None:
        conn.execute(
            insert(s.delivery_receipt).values(
                session_id=session_id,
                seq=seq,
                recipient="participant",
                kind=delivery_kind,
                received_at=now if received else None,
                rendered_at=now if rendered else None,
                client_monotonic_ms=client_ms,
            )
        )
    else:
        conn.execute(
            update(s.delivery_receipt)
            .where(
                s.delivery_receipt.c.session_id == session_id,
                s.delivery_receipt.c.seq == seq,
                s.delivery_receipt.c.recipient == "participant",
                s.delivery_receipt.c.kind == delivery_kind,
            )
            .values(
                received_at=(
                    now if received and existing["received_at"] is None else existing["received_at"]
                ),
                rendered_at=(
                    now if rendered and existing["rendered_at"] is None else existing["rendered_at"]
                ),
                client_monotonic_ms=client_ms,
            )
        )
    # Exposure receipt advancement: the referenced event seq is the exposure's
    # committed surface. ``received_seq``/``rendered_seq`` point at the telemetry
    # event evidencing the receipt — never fabricated when no receipt arrives.
    if received:
        conn.execute(
            update(s.manipulation_exposure)
            .where(
                s.manipulation_exposure.c.session_id == session_id,
                s.manipulation_exposure.c.committed_seq == seq,
                s.manipulation_exposure.c.received_seq.is_(None),
            )
            .values(received_seq=telemetry_seq)
        )
    if rendered:
        conn.execute(
            update(s.manipulation_exposure)
            .where(
                s.manipulation_exposure.c.session_id == session_id,
                s.manipulation_exposure.c.committed_seq == seq,
                s.manipulation_exposure.c.rendered_seq.is_(None),
            )
            .values(rendered_seq=telemetry_seq, status="rendered")
        )
