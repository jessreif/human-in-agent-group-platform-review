"""Historical Qualtrics import contract (INV-1110, spec §9.2).

If raw historical Qualtrics exports are later supplied, they enter a *separate* source
batch — never merged with server-run sessions, and never silently pooled with new pilot
outcomes (spec §9.2: generator, manipulation, scripting, timing and measurement changes
must all be identified first).

This module implements the contract itself:

- :func:`validate_bundle` checks a submitted import bundle against
  ``investigators.historical_import/1``: original variable names preserved verbatim under
  ``fields``, per-field availability metadata declared, per-row ``row_sha256`` pinning the
  original values, and a source-batch checksum pinning the raw file. Fields that would be
  fabricated (world seeds, render events, item timestamps the old data never had) are
  rejected outright.
- :func:`import_historical` is deliberately a stub: it validates the bundle and reports
  ``not_applicable`` — there is no write path until raw inputs exist, and importing is an
  explicit operational act, not a side effect of validation.

The N=30 narrative report is not a substitute for raw participant records (spec §9.2) and
cannot satisfy this contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

HISTORICAL_SCHEMA = "investigators.historical_import/1"
BATCH_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Fields an import must never assert: they are products of the server pipeline and cannot
# exist in a raw Qualtrics export. Supplying them is fabrication, not preservation.
FORBIDDEN_FIELD_PREFIXES = (
    "seed",
    "event_seq",
    "render_seq",
    "delivered_seq",
    "committed_seq",
    "item_timestamp",
    "rendered_at",
)


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    problems: tuple[str, ...]
    n_rows: int = 0
    n_fields: int = 0


@dataclass(frozen=True)
class ImportResult:
    status: str  # "not_applicable" until raw inputs exist
    reason: str
    validation: ValidationReport | None = None
    import_sha256: str | None = None


def _canon(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")


def validate_bundle(bundle: dict[str, Any]) -> ValidationReport:
    """Structural + checksum validation of one import bundle (schema documented in
    ``docs/HISTORICAL_IMPORT_CONTRACT.md``)."""
    problems: list[str] = []
    if bundle.get("schema") != HISTORICAL_SCHEMA:
        problems.append(f"schema must be {HISTORICAL_SCHEMA!r}, got {bundle.get('schema')!r}")
        return ValidationReport(False, tuple(problems))

    batch = bundle.get("source_batch")
    if not isinstance(batch, dict):
        problems.append("source_batch is required")
        batch = {}
    if not BATCH_ID_RE.match(str(batch.get("batch_id", ""))):
        problems.append("source_batch.batch_id must match the batch-id format")
    if not batch.get("study"):
        problems.append("source_batch.study is required")
    if not batch.get("generator"):
        problems.append(
            "source_batch.generator is required — historical rows must identify their "
            "generator so they are never pooled with server-run sessions"
        )
    digest = str(batch.get("source_file_sha256", ""))
    if not SHA256_RE.match(digest):
        problems.append("source_batch.source_file_sha256 must be a sha256 hex digest")

    declared = bundle.get("fields")
    if not isinstance(declared, dict) or not declared:
        problems.append("fields must be a non-empty map of original variable names")
        declared = {}
    for name, meta in declared.items():
        if not str(name).startswith("inv_"):
            problems.append(f"field {name!r} does not use an original inv_* variable name")
        if not isinstance(meta, dict) or "available" not in meta:
            problems.append(f"field {name!r} lacks an availability flag")
        lowered = str(name).lower()
        if lowered.startswith(FORBIDDEN_FIELD_PREFIXES):
            problems.append(
                f"field {name!r} is a server-side product; a raw import cannot assert it"
            )
    available = {
        name for name, meta in declared.items() if isinstance(meta, dict) and meta.get("available")
    }

    rows = bundle.get("rows")
    if not isinstance(rows, list):
        problems.append("rows must be a list")
        rows = []
    seen_ids: set[str] = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            problems.append(f"row {i}: not an object")
            continue
        rid = row.get("source_row_id")
        if not rid:
            problems.append(f"row {i}: source_row_id is required")
        elif str(rid) in seen_ids:
            problems.append(f"row {i}: duplicate source_row_id {rid!r}")
        else:
            seen_ids.add(str(rid))
        fields = row.get("fields")
        if not isinstance(fields, dict):
            problems.append(f"row {i}: fields must be an object")
            fields = {}
        for name in fields:
            if str(name).lower().startswith(FORBIDDEN_FIELD_PREFIXES):
                problems.append(f"row {i}: field {name!r} cannot be supplied by a raw import")
            elif name not in available:
                problems.append(
                    f"row {i}: field {name!r} was not declared available; unavailable "
                    "fields must be omitted, never invented"
                )
        expected = hashlib.sha256(_canon(fields)).hexdigest()
        if row.get("row_sha256") != expected:
            problems.append(
                f"row {i}: row_sha256 mismatch — original field values must be preserved "
                "byte-identically under the declared checksum"
            )
    return ValidationReport(
        ok=not problems,
        problems=tuple(problems),
        n_rows=len(rows),
        n_fields=len(declared),
    )


def import_historical(bundle: dict[str, Any]) -> ImportResult:
    """Validate ``bundle`` and report — the write path is intentionally absent.

    Status ``not_applicable`` (spec §9.2 / INV-1110): raw Qualtrics inputs have not been
    supplied, so no import executes. When a bundle is supplied and validates, the result
    still reports ``not_applicable`` with the computed import checksum so the operator and
    auditor see exactly what *would* enter — execution stays a separate, explicit act.
    """
    validation = validate_bundle(bundle)
    if not validation.ok:
        return ImportResult(
            status="not_applicable",
            reason=f"bundle failed contract validation: {validation.problems[0]}",
            validation=validation,
        )
    digest = hashlib.sha256(
        _canon({"source_batch": bundle["source_batch"], "rows": bundle["rows"]})
    ).hexdigest()
    return ImportResult(
        status="not_applicable",
        reason=(
            "historical import execution is not applicable until raw Qualtrics inputs are "
            "supplied and reviewed; bundle validated and pinned"
        ),
        validation=validation,
        import_sha256=digest,
    )
