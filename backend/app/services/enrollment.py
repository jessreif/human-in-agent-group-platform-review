"""Optional completion targets for newly created live batch groups.

The policy's cells, algorithm, platform and ``test_only`` flag are immutable once
the group is minted. The per-cell completer target may only *increase*, and only
through ``adminops.raise_enrollment_target`` (owner direction 2026-10-17, after
the v14 wave top-offs): never edit ``enrollment_policy`` with SQL.

An allocation is never recycled. Capacity is derived from durable outcomes instead:
retained-data completers and nonterminal allocated sessions hold a place; terminal
dropouts do not. Allocation and completion serialize on the SAME group row, always
after the caller's own session lock. Reads never lock another participant's session.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import exists, insert, select, update
from sqlalchemy.engine import Connection

from app.db import schema as s
from app.db.vocabulary import ELIGIBLE_DATA_USE

if TYPE_CHECKING:
    from app.services.allocation import GroupCell


class EnrollmentError(ValueError):
    pass


class EnrollmentTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: Literal["prolific", "connect", "lab"]
    target_completers_per_cell: int = Field(strict=True, ge=1, le=10000)
    test_only: bool = Field(default=False, strict=True)


class PolicyCell(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: str
    manifest_id: str
    arm: str

    @property
    def key(self) -> tuple[str, str]:
        return self.batch_id, self.arm


class EnrollmentPolicy(EnrollmentTarget):
    version: Literal[1] = 1
    algorithm: Literal["least_occupied_random_tiebreak_v1"] = "least_occupied_random_tiebreak_v1"
    cells: tuple[PolicyCell, ...] = Field(min_length=2, max_length=32)

    @model_validator(mode="after")
    def unique_cells(self) -> EnrollmentPolicy:
        if len({c.key for c in self.cells}) != len(self.cells):
            raise ValueError("duplicate policy cell")
        return self


def make_policy(target: EnrollmentTarget, cells: Sequence[GroupCell]) -> EnrollmentPolicy:
    return EnrollmentPolicy(
        platform=target.platform,
        target_completers_per_cell=target.target_completers_per_cell,
        test_only=target.test_only,
        cells=tuple(
            PolicyCell(batch_id=c.batch_id, manifest_id=c.manifest_id, arm=c.arm) for c in cells
        ),
    )


def load_policy(group: Mapping[Any, Any]) -> EnrollmentPolicy | None:
    raw = group.get("enrollment_policy")
    if raw is None:
        return None
    try:
        policy = EnrollmentPolicy.model_validate(raw)
    except ValueError as exc:
        raise EnrollmentError("quota_policy_invalid") from exc
    if group["mode"] != "live":
        raise EnrollmentError("quota_policy_invalid")
    return policy


def validate_cells(policy: EnrollmentPolicy, cells: Sequence[GroupCell]) -> None:
    actual = [(c.batch_id, c.manifest_id, c.arm) for c in cells]
    if actual != [(c.batch_id, c.manifest_id, c.arm) for c in policy.cells]:
        raise EnrollmentError("quota_configuration_changed")


def validate_linkage(policy: EnrollmentPolicy, platform: str, pid: str) -> None:
    if platform != policy.platform:
        raise EnrollmentError("platform_not_allowed")
    if policy.test_only and not pid.startswith("TEST_"):
        raise EnrollmentError("test_only_batch")
    if not policy.test_only and pid.startswith("TEST_"):
        raise EnrollmentError("test_identity_not_allowed")


def is_test_group(conn: Connection, group_id: str | None) -> bool:
    if group_id is None:
        return False
    group = (
        conn.execute(select(s.batch_group).where(s.batch_group.c.group_id == group_id))
        .mappings()
        .one()
    )
    policy = load_policy(group)
    return policy is not None and policy.test_only


@dataclass(frozen=True)
class CellProgress:
    cell: PolicyCell
    target: int
    completed: int
    active: int

    @property
    def occupied(self) -> int:
        return self.completed + self.active

    @property
    def available(self) -> int:
        return max(0, self.target - self.occupied)

    def as_dict(self) -> dict[str, int]:
        return {
            "target": self.target,
            "completed": self.completed,
            "active": self.active,
            "available": self.available,
        }


def progress(
    conn: Connection,
    group_id: str,
    policy: EnrollmentPolicy,
    *,
    completing_session_id: uuid.UUID | None = None,
) -> list[CellProgress]:
    """Count retained quantitative-data completions, not accuracy or payout decisions.

    The completion handler has inserted its receipt but may not yet have flushed its
    stage. Only that locked session may be projected as complete before commit.
    """
    latest_choice = (
        select(s.data_use_choice.c.choice)
        .where(s.data_use_choice.c.session_id == s.session.c.session_id)
        .order_by(s.data_use_choice.c.choice_seq.desc())
        .limit(1)
        .correlate(s.session)
        .scalar_subquery()
    )
    deleted = exists(
        select(s.admin_audit.c.audit_id).where(
            s.admin_audit.c.session_id == s.session.c.session_id,
            s.admin_audit.c.action == "deletion_tombstone",
        )
    )
    counts = {c.key: [0, 0] for c in policy.cells}
    rows = conn.execute(
        select(
            s.allocation.c.batch_id,
            s.allocation.c.arm,
            s.session.c.session_id,
            s.session.c.stage,
            s.completion_receipt.c.outcome,
            s.result.c.status,
            latest_choice.label("choice"),
            deleted.label("deleted"),
        )
        .select_from(
            s.allocation.join(s.session, s.allocation.c.session_id == s.session.c.session_id)
            .outerjoin(s.result, s.result.c.session_id == s.session.c.session_id)
            .outerjoin(
                s.completion_receipt,
                s.completion_receipt.c.session_id == s.session.c.session_id,
            )
        )
        .where(s.session.c.batch_group_id == group_id, s.session.c.mode == "live")
    )
    for row in rows:
        key = row.batch_id, row.arm
        if key not in counts:
            raise EnrollmentError("quota_configuration_changed")
        finished = row.stage == "completion" or row.session_id == completing_session_id
        valid = (
            finished
            and row.outcome == "completed"
            and row.status == "scored"
            and row.choice in ELIGIBLE_DATA_USE
            and not row.deleted
        )
        if valid:
            counts[key][0] += 1
        elif not finished and row.stage not in s.TERMINAL_STAGES and not row.deleted:
            counts[key][1] += 1
    return [
        CellProgress(c, policy.target_completers_per_cell, *counts[c.key]) for c in policy.cells
    ]


def close_if_complete(
    conn: Connection, group_id: str | None, completing_session_id: uuid.UUID
) -> None:
    if group_id is None:
        return
    group = (
        conn.execute(
            select(s.batch_group)
            .where(
                s.batch_group.c.group_id == group_id,
                s.batch_group.c.enrollment_policy.is_not(None),
            )
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if group is None:
        return
    policy = load_policy(group)
    assert policy is not None
    cells = progress(conn, group_id, policy, completing_session_id=completing_session_id)
    if group["enrollment_open"] and all(c.completed >= c.target for c in cells):
        conn.execute(
            update(s.batch_group)
            .where(s.batch_group.c.group_id == group_id)
            .values(enrollment_open=False)
        )
        conn.execute(
            insert(s.admin_audit).values(
                audit_id=uuid.uuid4(),
                actor="system",
                action="quota_target_reached",
                payload={
                    "group_id": group_id,
                    "target_completers_per_cell": policy.target_completers_per_cell,
                    "completed": sum(c.completed for c in cells),
                },
            )
        )
