"""Semantic coding export product (INV-1205/1208; spec §9.4/§9.5).

One ``semantic_coding`` table per ``coding_run``, readable only through the analysis
guard: mixed runs or incomplete coverage block the export by default; an explicit
``GuardOverride`` is written into every row and into the table description. Result
statuses surface as honest ``semantic_outcome`` labels — ``abstained``, ``failed``,
``not_coded`` and missing coverage are never silently converted to zeros.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import Connection, select

from app.coding.guard import GuardOverride, GuardReport, require
from app.db import schema as s
from app.export.study import ExportTable

SEMANTIC_SCHEMA_VERSION = "wp12a-semantic/1"


def _outcome(status: str) -> str:
    return {
        "coded": "coded",
        "not_coded": "not_coded",
        "abstained": "abstained",
        "failed": "failed",
    }.get(status, "unavailable")


def semantic_coding_table(
    conn: Connection,
    run_id: uuid.UUID,
    *,
    session_ids: list[uuid.UUID] | None = None,
    override: GuardOverride | None = None,
) -> tuple[ExportTable, GuardReport]:
    """Build the semantic table for one run under the analysis guard.

    Raises ``SemanticGuardError`` when the run is missing, mixed, unfinished or has
    incomplete coverage — unless an explicit ``GuardOverride`` is supplied, in which
    case the override is written into every row and the table description. Every
    non-coded outcome is labelled honestly (``abstained``/``failed``/``not_coded``/
    ``unavailable``), never zeroed or omitted.
    """
    report = require(conn, [run_id], override=override)
    stmt = (
        select(
            s.coding_result.c.subject_key,
            s.coding_result.c.session_id,
            s.coding_result.c.status,
            s.coding_result.c.codes,
            s.coding_result.c.supersedes_run_id,
            s.coding_result.c.supersedes_subject_key,
            s.coding_result.c.recorded_at,
        )
        .where(s.coding_result.c.run_id == run_id)
        .order_by(s.coding_result.c.subject_key)
    )
    if session_ids is not None:
        stmt = stmt.where(s.coding_result.c.session_id.in_(session_ids))
    rows: list[dict[str, Any]] = []
    for r in conn.execute(stmt).mappings().all():
        codes = r["codes"] or {}
        assertions = codes.get("assertions") or []
        questions = codes.get("questions") or []
        solicitations = codes.get("solicitations") or []
        rows.append(
            {
                "run_id": str(run_id),
                "subject_key": r["subject_key"],
                "session_id": str(r["session_id"]) if r["session_id"] else "",
                "status": r["status"],
                "semantic_outcome": _outcome(r["status"]),
                "n_assertions": len(assertions) if r["status"] == "coded" else "",
                "n_questions": len(questions) if r["status"] == "coded" else "",
                "n_solicitations": len(solicitations) if r["status"] == "coded" else "",
                "codes_json": json.dumps(codes, sort_keys=True) if r["status"] == "coded" else "",
                "supersedes": (
                    f"{r['supersedes_run_id']}:{r['supersedes_subject_key']}"
                    if r["supersedes_run_id"]
                    else ""
                ),
                "recorded_at": r["recorded_at"].isoformat() if r["recorded_at"] else "",
                "guard_override": (f"{override.decided_by}: {override.reason}" if override else ""),
            }
        )
    description = (
        f"Offline semantic coding results for coding_run {run_id} (one run per export; "
        "guard-protected, statuses labelled honestly)"
    )
    if report.problems:
        description += f"; GUARD: {'; '.join(report.problems)}"
    return (
        ExportTable("semantic_coding", rows, description, schema_version=SEMANTIC_SCHEMA_VERSION),
        report,
    )


def run_provenance(conn: Connection, run_id: uuid.UUID) -> dict[str, Any]:
    """The run row's pinned provenance for an export manifest."""
    run = (
        conn.execute(select(s.coding_run).where(s.coding_run.c.run_id == run_id))
        .mappings()
        .one_or_none()
    )
    if run is None:
        return {"run_id": str(run_id), "missing": True}
    return {
        "run_id": str(run_id),
        "corpus_sha256": run["corpus_sha256"],
        "rubric": f"{run['rubric_artifact_id']}@v{run['rubric_version']}",
        "prompt": f"{run['prompt_artifact_id']}@v{run['prompt_version']}",
        "model": run["model"],
        "model_digest": run["model_digest"],
        "parameters": run["parameters"],
        "code_version": run["code_version"],
        "validator_version": run["validator_version"],
        "status": run["status"],
        "coverage": run["coverage"],
        "created_by": run["created_by"],
    }
