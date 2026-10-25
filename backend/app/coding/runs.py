"""Coding-run persistence (INV-1205; spec §11).

A ``coding_run`` row immutably binds the full provenance of one offline coding pass:
input corpus snapshot + SHA-256, rubric/prompt artifact ids and versions, model and
model digest, parameters, code version and validator version. Results are written once
per ``(run_id, subject_key)`` and never updated; a correction is a *new* run whose rows
carry ``supersedes_*`` pointers into the prior run (the composite FK enforces that the
superseded row exists).

The deterministic path is the only live coder: ``model`` is ``"deterministic"`` and
``model_digest`` NULL. The prompt artifact is always pinned (interface/config point),
but ``coding.prompt@v1`` is dormant until D05 — :mod:`app.coding.llm` refuses any
model-assisted execution.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import Connection, func, insert, null, select, update

from app.coding.types import ResultRow
from app.db import schema as s

RunStatus = Literal["pending", "running", "completed", "failed"]


@dataclass(frozen=True)
class RunSpec:
    """Everything a run row pins. ``corpus`` is the input snapshot document (the
    corpus artifact body or the session-input manifest); ``corpus_sha256`` is its
    canonical content hash."""

    corpus: dict[str, Any]
    corpus_sha256: str
    rubric_artifact_id: str
    rubric_version: int
    prompt_artifact_id: str
    prompt_version: int
    model: str
    model_digest: str | None
    parameters: dict[str, Any]
    code_version: str
    validator_version: str
    created_by: str


class CodingRunError(ValueError):
    pass


def create_run(conn: Connection, spec: RunSpec) -> uuid.UUID:
    """Insert the immutable run row (status ``pending``)."""
    run_id = uuid.uuid4()
    conn.execute(
        insert(s.coding_run).values(
            run_id=run_id,
            corpus=spec.corpus,
            corpus_sha256=spec.corpus_sha256,
            rubric_artifact_id=spec.rubric_artifact_id,
            rubric_version=spec.rubric_version,
            prompt_artifact_id=spec.prompt_artifact_id,
            prompt_version=spec.prompt_version,
            model=spec.model,
            model_digest=spec.model_digest,
            parameters=spec.parameters,
            code_version=spec.code_version,
            validator_version=spec.validator_version,
            status="pending",
            coverage={},
            created_by=spec.created_by,
        )
    )
    return run_id


def start_run(conn: Connection, run_id: uuid.UUID) -> None:
    conn.execute(
        update(s.coding_run)
        .where(s.coding_run.c.run_id == run_id, s.coding_run.c.status == "pending")
        .values(status="running")
    )


def _sql_null(value: Any) -> Any:
    """JSONB binds Python ``None`` as the JSON literal ``'null'``, which is NOT SQL
    NULL and would silently satisfy ``coded_needs_codes``; write real NULLs instead."""
    return null() if value is None else value


def _check_row(row: ResultRow) -> None:
    if row.status == "coded" and not isinstance(row.codes, dict):
        raise CodingRunError("status 'coded' requires a non-null codes object")
    if (row.supersedes_run_id is None) != (row.supersedes_subject_key is None):
        raise CodingRunError("supersedes_run_id/supersedes_subject_key must pair")


def insert_results(conn: Connection, run_id: uuid.UUID, rows: list[ResultRow]) -> int:
    """Append ``coding_result`` rows. PK collision means the subject was already coded
    in this run — that is an error, never an overwrite."""
    inserted = 0
    for row in rows:
        _check_row(row)
        conn.execute(
            insert(s.coding_result).values(
                run_id=run_id,
                subject_key=row.subject_key,
                session_id=row.session_id,
                status=row.status,
                codes=_sql_null(row.codes),
                adjudication=_sql_null(row.adjudication),
                supersedes_run_id=row.supersedes_run_id,
                supersedes_subject_key=row.supersedes_subject_key,
            )
        )
        inserted += 1
    return inserted


def finish_run(
    conn: Connection,
    run_id: uuid.UUID,
    *,
    status: Literal["completed", "failed"],
    expected_subjects: int,
) -> dict[str, Any]:
    """Stamp the run's coverage block and final status. ``coverage`` records the
    denominator honestly: ``complete`` only when every expected subject has a row and
    no row is ``failed``."""
    counts: dict[str, int] = dict(
        conn.execute(
            select(s.coding_result.c.status, func.count())
            .where(s.coding_result.c.run_id == run_id)
            .group_by(s.coding_result.c.status)
        ).all()
    )
    n = sum(counts.values())
    coverage = {
        "expected_subjects": expected_subjects,
        "result_rows": n,
        "by_status": counts,
        "missing_subjects": max(expected_subjects - n, 0),
        "failed_subjects": int(counts.get("failed", 0)),
        "complete": n >= expected_subjects and int(counts.get("failed", 0)) == 0,
    }
    conn.execute(
        update(s.coding_run)
        .where(s.coding_run.c.run_id == run_id)
        .values(
            status=status,
            coverage=coverage,
            finished_at=func.statement_timestamp(),
        )
    )
    return coverage


def record_gate(
    conn: Connection,
    run_id: uuid.UUID,
    *,
    name: str,
    status: Literal["pending", "passed", "failed", "waived"] = "pending",
    thresholds: dict[str, Any],
    measured: dict[str, Any] | None = None,
    denominators: dict[str, Any] | None = None,
    adjudication: dict[str, Any] | None = None,
    decided_by: str | None = None,
) -> uuid.UUID:
    """Record a ``validation_gate`` row for a run. Real held-out human-gold gates are
    WP12b — a dev-corpus evaluation must never be recorded as scientific ``passed``;
    callers pass ``pending``/``failed`` with an honest adjudication until D11."""
    gate_id = uuid.uuid4()
    decided = status != "pending"
    conn.execute(
        insert(s.validation_gate).values(
            gate_id=gate_id,
            run_id=run_id,
            name=name,
            status=status,
            thresholds=thresholds,
            measured=_sql_null(measured),
            denominators=_sql_null(denominators),
            adjudication=_sql_null(adjudication),
            decided_by=decided_by,
            decided_at=func.statement_timestamp() if decided else None,
        )
    )
    return gate_id


def supersede_row(
    conn: Connection,
    *,
    run_id: uuid.UUID,
    subject_key: str,
    supersedes_run_id: uuid.UUID,
    supersedes_subject_key: str,
    status: str,
    codes: dict[str, Any] | None,
    adjudication: dict[str, Any] | None,
    session_id: uuid.UUID | None = None,
) -> None:
    """Insert the correcting row in a *new* run, pointing at the superseded result.
    Both rows stay; nothing is ever overwritten (INV-1205)."""
    conn.execute(
        insert(s.coding_result).values(
            run_id=run_id,
            subject_key=subject_key,
            session_id=session_id,
            status=status,
            codes=_sql_null(codes),
            adjudication=_sql_null(adjudication),
            supersedes_run_id=supersedes_run_id,
            supersedes_subject_key=supersedes_subject_key,
        )
    )
