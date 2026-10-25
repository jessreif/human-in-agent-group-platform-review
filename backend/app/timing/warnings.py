"""In-task warnings (INV-0213, spec §2.1, §4.1, §6.6 "Warnings").

A warning is content (a versioned ``warning`` artifact: text, system sender, no teammate voice)
plus a timing policy: an independent job on the ``active_task`` clock with one logical delivery
per session. This module turns the artifact into a :class:`WarningPlan` and decides, at commit
time, whether the job may commit. The scheduler/worker that runs the job and records receipts
is WP06 (INV-0603/0607/0611); it must call :func:`decide_warning_commit` under the session lock.

Properties that follow from the plan and are tested:
- due exactly at active 300 000 ms (299 999 ms is not due), in every profile and arm;
- defined on the active clock, so a check-in pause of any wall length cannot move it;
- no dependency on chat/model work (``waits_on`` is empty by schema);
- a closed task suppresses and logs it; a committed warning is never committed again.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.manifests.artifacts import WarningArtifact


@dataclass(frozen=True)
class WarningPlan:
    artifact_key: str
    artifact_sha256: str
    logical_job_key: str
    text: str
    sender: str
    clock_basis: str
    due_active_ms: int
    low_time_from_ms: int
    low_time_until_ms: int
    when_task_closed: str
    exposure_record: str
    receipts: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "artifact": self.artifact_key,
            "artifact_sha256": self.artifact_sha256,
            "logical_job_key": self.logical_job_key,
            "text": self.text,
            "sender": self.sender,
            "clock_basis": self.clock_basis,
            "due_active_ms": self.due_active_ms,
            "low_time_ms": [self.low_time_from_ms, self.low_time_until_ms],
            "when_task_closed": self.when_task_closed,
            "exposure_record": self.exposure_record,
            "receipts": list(self.receipts),
        }


class WarningPlanError(ValueError):
    pass


def warning_plan(artifact: WarningArtifact, sha256: str, budget_ms: int) -> WarningPlan:
    body = artifact.body
    due = body.trigger.at_active_s * 1000
    until = body.low_time_state.until_active_s * 1000
    if not 0 < due < budget_ms or until != budget_ms:
        raise WarningPlanError(f"{artifact.key} does not fit the {budget_ms} ms active budget")
    return WarningPlan(
        artifact_key=artifact.key,
        artifact_sha256=sha256,
        logical_job_key=body.delivery.logical_job_key,
        text=body.text,
        sender=body.sender,
        clock_basis=body.trigger.clock_basis,
        due_active_ms=due,
        low_time_from_ms=body.low_time_state.from_active_s * 1000,
        low_time_until_ms=until,
        when_task_closed=body.delivery.when_task_closed,
        exposure_record=body.exposure.record,
        receipts=tuple(body.exposure.receipts),
    )


def is_due(plan: WarningPlan, active_ms: float) -> bool:
    return active_ms >= plan.due_active_ms


def low_time(plan: WarningPlan, active_ms: float) -> bool:
    """Countdown style state: from the trigger through the end of the budget (inclusive)."""
    return plan.low_time_from_ms <= active_ms <= plan.low_time_until_ms


class WarningDecision(StrEnum):
    NOT_DUE = "not_due"
    COMMIT = "commit"
    SUPPRESS_TASK_CLOSED = "suppress_task_closed"
    ALREADY_COMMITTED = "already_committed"


def decide_warning_commit(
    plan: WarningPlan, *, active_ms: float, task_closed: bool, already_committed: bool
) -> WarningDecision:
    """Commit-time authorization, evaluated under the session lock (spec §6.4)."""
    if already_committed:
        return WarningDecision.ALREADY_COMMITTED
    if task_closed:
        return WarningDecision.SUPPRESS_TASK_CLOSED
    if not is_due(plan, active_ms):
        return WarningDecision.NOT_DUE
    return WarningDecision.COMMIT
