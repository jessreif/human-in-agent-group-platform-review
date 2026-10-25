"""1:1 randomized-block allocation under a DB lock (INV-0403, spec §5.1).

Within one ``study_key x batch`` every block contains each arm exactly once, in a
deterministically shuffled order. The shuffle is keyed by HMAC over
``manifest_sha256 | batch_id | block_index`` — it is batch-level randomness, reproducible at
replay without any session state and independent of arrival order. The session's own
``assignment`` stream remains free for session-level draws; the realized assignment is
recorded in the session's ``draw_log`` for audit.

The whole decision happens while holding ``SELECT ... FOR UPDATE`` on the batch row, so
concurrent entries can never split a slot. The reservation (``allocation.session_id``) and
``session.arm``/``compatibility_key`` are written in the caller's transaction; once committed
they are never rewritten — reloads, dropouts and failed comprehension never reassign.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.engine import Connection

from app.db import schema as s
from app.domain.compat import UNRESOLVED_MODEL, compatibility_key
from app.domain.manifests.binding import BoundManifest
from app.domain.manifests.schema import allocation_arms
from app.domain.rng import DrawRecord
from app.services import enrollment
from app.services.sessions import record_draws
from app.timing.policy import resolve_policy

_DOMAIN = b"investigators.allocation/1"


class AllocationError(ValueError):
    pass


def _draw(key: bytes, *parts: object) -> float:
    digest = hmac.new(key, "|".join(str(p) for p in parts).encode(), hashlib.sha256).digest()
    return (int.from_bytes(digest[:8], "big") >> 11) / float(1 << 53)


def block_order(
    manifest_sha256: str, batch_id: str, block_index: int, arms: Sequence[str]
) -> tuple[str, ...]:
    """The arm order for one block: a keyed Fisher-Yates over the study's arms."""
    key = hmac.new(
        bytes.fromhex(manifest_sha256),
        _DOMAIN + b"|" + batch_id.encode(),
        hashlib.sha256,
    ).digest()
    order = list(arms)
    for i in range(len(order) - 1, 0, -1):
        j = int(_draw(key, block_index, "swap", i) * (i + 1))
        order[i], order[j] = order[j], order[i]
    return tuple(order)


def _next_slot(
    conn: Connection,
    batch_ids: Sequence[str],
    key_hex: str,
    scope_id: str,
    labels: Sequence[str],
) -> tuple[int, int]:
    """Continue only a matching, contiguous last block; otherwise start a fresh one.

    The caller holds the batch/group lock. Historical block numbers are never
    reinterpreted using today's cell count. A configuration change abandons a
    partial incompatible block, without rewriting its reservations.
    """
    last: int | None = conn.execute(
        select(func.max(s.allocation.c.block_index)).where(s.allocation.c.batch_id.in_(batch_ids))
    ).scalar_one()
    if last is None:
        return 0, 0
    rows = conn.execute(
        select(s.allocation.c.batch_id, s.allocation.c.arm, s.allocation.c.slot_index)
        .where(
            s.allocation.c.batch_id.in_(batch_ids),
            s.allocation.c.block_index == last,
        )
        .order_by(s.allocation.c.slot_index)
    ).all()
    order = block_order(key_hex, scope_id, last, labels)
    if len(rows) < len(order) and all(
        r.slot_index == i and f"{r.batch_id}|{r.arm}" == order[i] for i, r in enumerate(rows)
    ):
        return last, len(rows)
    return last + 1, 0


def allocate(
    conn: Connection,
    *,
    session_id: uuid.UUID,
    bound: BoundManifest,
    batch_id: str,
    behavior_source_sha256: str,
    now: datetime,
    provider_model_digest: str = UNRESOLVED_MODEL,
    time_pressure_enabled: bool = True,
) -> str:
    """Reserve the next slot in the batch and pin the session's arm + compatibility key.

    Must be called with the session already inserted (FK target). Returns the assigned arm."""
    batch = (
        conn.execute(select(s.batch).where(s.batch.c.batch_id == batch_id).with_for_update())
        .mappings()
        .one_or_none()
    )
    if batch is None:
        raise AllocationError(f"unknown batch {batch_id!r}")
    if not batch["enrollment_open"]:
        raise AllocationError("batch_closed")
    if batch["manifest_id"] != bound.manifest_id or batch["mode"] != bound.mode:
        raise AllocationError("batch_manifest_mismatch")

    arms = allocation_arms(
        bound.manifest.study_key,
        bound.manifest.arms,
        time_pressure_enabled=time_pressure_enabled,
    )
    block_index, slot_index = _next_slot(
        conn,
        [batch_id],
        bound.manifest_sha256,
        batch_id,
        [f"{batch_id}|{arm}" for arm in arms],
    )
    key_seed = hmac.new(
        bytes.fromhex(bound.manifest_sha256),
        _DOMAIN + b"|" + batch_id.encode(),
        hashlib.sha256,
    ).digest()
    arm = block_order(bound.manifest_sha256, batch_id, block_index, arms)[slot_index]
    realized_draw = _draw(key_seed, block_index, "slot", slot_index)

    _reserve(
        conn,
        session_id=session_id,
        bound=bound,
        batch_id=batch_id,
        block_index=block_index,
        slot_index=slot_index,
        arm=arm,
        draw=DrawRecord(
            "assignment",
            ("batch", batch_id, "block", block_index, "slot", slot_index),
            realized_draw,
        ),
        behavior_source_sha256=behavior_source_sha256,
        now=now,
        provider_model_digest=provider_model_digest,
    )
    return arm


def _reserve(
    conn: Connection,
    *,
    session_id: uuid.UUID,
    bound: BoundManifest,
    batch_id: str,
    block_index: int,
    slot_index: int,
    arm: str,
    draw: DrawRecord,
    behavior_source_sha256: str,
    now: datetime,
    provider_model_digest: str,
) -> None:
    """Write the allocation row, pin arm + compatibility key, and log the realized draw."""
    conn.execute(
        s.allocation.insert().values(
            allocation_id=uuid.uuid4(),
            batch_id=batch_id,
            block_index=block_index,
            slot_index=slot_index,
            arm=arm,
            session_id=session_id,
            reserved_at=now,
        )
    )
    policy = resolve_policy(bound, arm)
    key = compatibility_key(
        bound,
        policy,
        batch_id=batch_id,
        behavior_source_sha256=behavior_source_sha256,
        provider_model_digest=provider_model_digest,
    ).key
    conn.execute(
        update(s.session)
        .where(s.session.c.session_id == session_id)
        .values(arm=arm, compatibility_key=key)
    )
    record_draws(conn, session_id, [draw])


@dataclass(frozen=True)
class GroupCell:
    batch_id: str
    manifest_id: str
    arm: str


def group_cells(
    conn: Connection, group_id: str, *, time_pressure_enabled: bool = True
) -> tuple[list[GroupCell], dict[str, str]]:
    """The group's cells in canonical order (member position, then study arm order), plus
    each member manifest's pinned sha256 (the block-order key material)."""
    rows = conn.execute(
        select(s.batch.c.batch_id, s.batch.c.manifest_id, s.batch.c.study_key)
        .select_from(
            s.batch_group_member.join(
                s.batch, s.batch_group_member.c.batch_id == s.batch.c.batch_id
            )
        )
        .where(s.batch_group_member.c.group_id == group_id)
        .order_by(s.batch_group_member.c.position)
    ).all()
    manifest_rows = conn.execute(
        select(
            s.experiment_manifest.c.manifest_id,
            s.experiment_manifest.c.manifest_sha256,
            s.experiment_manifest.c.content,
        ).where(s.experiment_manifest.c.manifest_id.in_([r.manifest_id for r in rows]))
    ).all()
    shas = {r.manifest_id: r.manifest_sha256 for r in manifest_rows}
    declared = {
        r.manifest_id: tuple(spec["arm"] for spec in r.content["condition_set"])
        for r in manifest_rows
    }
    cells = [
        GroupCell(r.batch_id, r.manifest_id, arm)
        for r in rows
        for arm in allocation_arms(
            r.study_key,
            declared[r.manifest_id],
            time_pressure_enabled=time_pressure_enabled,
        )
    ]
    return cells, shas


def group_key_hex(group_id: str, cells: Sequence[GroupCell], shas: dict[str, str]) -> str:
    material = "|".join([group_id, *(f"{c.batch_id}:{shas[c.manifest_id]}" for c in cells)])
    return hashlib.sha256(material.encode()).hexdigest()


def allocate_group(
    conn: Connection,
    *,
    session_id: uuid.UUID,
    group_id: str,
    rebind: Callable[[GroupCell], BoundManifest],
    behavior_source_sha256: str,
    now: datetime,
    provider_model_digest: str = UNRESOLVED_MODEL,
    time_pressure_enabled: bool = True,
) -> tuple[GroupCell, BoundManifest]:
    """Reserve the next slot of a batch group: permuted blocks over every (member batch x
    arm) cell, so each complete block of ``len(cells)`` consents holds each cell exactly once.

    Runs under ``SELECT ... FOR UPDATE`` on the group row. ``rebind`` moves the session onto
    the chosen cell's member batch + manifest (same transaction) and returns its binding; the
    allocation row then lands in that member batch with the group-level block/slot indices
    (unique per group, so unique per member batch)."""
    group = (
        conn.execute(
            select(s.batch_group).where(s.batch_group.c.group_id == group_id).with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if group is None:
        raise AllocationError(f"unknown batch group {group_id!r}")
    if not group["enrollment_open"]:
        raise AllocationError("batch_closed")
    cells, shas = group_cells(conn, group_id, time_pressure_enabled=time_pressure_enabled)
    if not cells or any(c.manifest_id not in shas for c in cells):
        raise AllocationError("batch_group_unconfigured")
    key_hex = group_key_hex(group_id, cells, shas)
    labels = [f"{c.batch_id}|{c.arm}" for c in cells]
    try:
        policy = enrollment.load_policy(group)
        if policy is not None:
            enrollment.validate_cells(policy, cells)
            linkage = conn.execute(
                select(s.platform_link.c.platform, s.platform_link.c.platform_pid).where(
                    s.platform_link.c.session_id == session_id
                )
            ).one_or_none()
            if linkage is None:
                raise enrollment.EnrollmentError("platform_not_allowed")
            enrollment.validate_linkage(policy, linkage.platform, linkage.platform_pid)
            capacity = enrollment.progress(conn, group_id, policy)
            available = [c for c in capacity if c.available > 0]
            if not available:
                raise enrollment.EnrollmentError("batch_at_capacity")
            least_occupied = min(c.occupied for c in available)
            eligible = [
                f"{c.cell.batch_id}|{c.cell.arm}" for c in available if c.occupied == least_occupied
            ]
            # One immutable reservation per quota decision. A replacement gets a NEW
            # block number; no dropout's historical block/slot is reused or rewritten.
            last: int | None = conn.execute(
                select(func.max(s.allocation.c.block_index)).where(
                    s.allocation.c.batch_id.in_([c.batch_id for c in cells])
                )
            ).scalar_one()
            block_index, slot_index = (0 if last is None else last + 1), 0
            label = block_order(key_hex, group_id, block_index, eligible)[0]
            draw_context: tuple[str | int, ...] = (
                "group",
                group_id,
                "quota_block",
                block_index,
                "eligible",
                *eligible,
            )
        else:
            block_index, slot_index = _next_slot(
                conn, sorted({c.batch_id for c in cells}), key_hex, group_id, labels
            )
            label = block_order(key_hex, group_id, block_index, labels)[slot_index]
            draw_context = ("group", group_id, "block", block_index, "slot", slot_index)
    except enrollment.EnrollmentError as exc:
        raise AllocationError(str(exc)) from exc
    cell = cells[labels.index(label)]
    key_seed = hmac.new(
        bytes.fromhex(key_hex), _DOMAIN + b"|" + group_id.encode(), hashlib.sha256
    ).digest()
    bound = rebind(cell)
    _reserve(
        conn,
        session_id=session_id,
        bound=bound,
        batch_id=cell.batch_id,
        block_index=block_index,
        slot_index=slot_index,
        arm=cell.arm,
        draw=DrawRecord(
            "assignment",
            draw_context,
            _draw(key_seed, block_index, "slot", slot_index),
        ),
        behavior_source_sha256=behavior_source_sha256,
        now=now,
        provider_model_digest=provider_model_digest,
    )
    return cell, bound
