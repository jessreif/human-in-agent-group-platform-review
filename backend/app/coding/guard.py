"""Analysis guard (INV-1208; spec §9.4/§9.5).

Semantic coding outputs are gated by default. A semantic product is exportable only
when it is bound to exactly one ``coding_run`` whose coverage is complete — every
expected subject has a row and none is ``failed``. Mixed runs, missing runs, missing
coverage and failed subjects all block unless an explicit, auditable override is
recorded (``GuardOverride``), in which case the export carries the override and labels
unavailable outcomes honestly — never as zeros.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Connection, select

from app.db import schema as s


class SemanticGuardError(ValueError):
    """Semantic analysis/export blocked. ``problems`` lists every blocking reason."""


@dataclass(frozen=True)
class GuardOverride:
    """An explicit, auditable bypass. Recorded verbatim in the product."""

    reason: str
    decided_by: str


@dataclass(frozen=True)
class GuardReport:
    run_ids: tuple[uuid.UUID, ...]
    problems: tuple[str, ...]
    override: GuardOverride | None

    @property
    def allowed(self) -> bool:
        return not self.problems or self.override is not None


def check_runs(
    conn: Connection,
    run_ids: list[uuid.UUID],
    *,
    override: GuardOverride | None = None,
) -> GuardReport:
    problems: list[str] = []
    if not run_ids:
        problems.append("no coding_run bound: semantic outcomes are unavailable")
    elif len(set(run_ids)) > 1:
        problems.append("mixed coding runs: semantic outputs must bind exactly one run/snapshot")
    for run_id in set(run_ids):
        run = (
            conn.execute(
                select(
                    s.coding_run.c.status,
                    s.coding_run.c.coverage,
                    s.coding_run.c.code_version,
                ).where(s.coding_run.c.run_id == run_id)
            )
            .mappings()
            .one_or_none()
        )
        if run is None:
            problems.append(f"coding_run {run_id} does not exist")
            continue
        if run["status"] != "completed":
            problems.append(f"coding_run {run_id} status is {run['status']!r}, not completed")
        coverage = dict(run["coverage"] or {})
        if not coverage.get("complete", False):
            problems.append(
                f"coding_run {run_id} coverage incomplete: "
                f"missing={coverage.get('missing_subjects')} "
                f"failed={coverage.get('failed_subjects')}"
            )
    return GuardReport(tuple(sorted(set(run_ids))), tuple(problems), override)


def require(
    conn: Connection, run_ids: list[uuid.UUID], *, override: GuardOverride | None = None
) -> GuardReport:
    report = check_runs(conn, run_ids, override=override)
    if report.problems and report.override is None:
        raise SemanticGuardError("; ".join(report.problems))
    return report
