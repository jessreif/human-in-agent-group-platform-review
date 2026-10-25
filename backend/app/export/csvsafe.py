"""Formula-safe CSV writing and export checksums (INV-1109, spec §9.2).

Two guarantees, both testable:

- *Formula safety.* A spreadsheet cell whose text begins with ``=``, ``+``, ``-``, ``@``
  (also the Unicode minus ``\u2212`` and a leading tab/CR, which spreadsheets also treat as
  formula/whitespace triggers), or that has leading/trailing whitespace, is prefixed with a
  single quote so it can never be evaluated as a formula or trimmed into one. ``None`` stays
  an empty cell — exports distinguish missing from empty.
- *Integrity.* Every written file's SHA-256 over the exact bytes is returned so the export
  manifest can pin it; :func:`canonical_checksum` pins the in-memory rows identically.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

# Characters a spreadsheet treats as the start of a formula (or that let leading control
# whitespace smuggle one past naive filters).
FORMULA_PREFIXES = ("=", "+", "-", "\u2212", "@", "\t", "\r")
ESCAPE_PREFIX = "'"


def canonicalize(value: Any) -> Any:
    """Deterministic JSON-able form for checksums and CSV cells."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, dict):
        return {str(k): canonicalize(v) for k, v in sorted(value.items(), key=lambda i: str(i[0]))}
    if isinstance(value, (list, tuple)):
        return [canonicalize(v) for v in value]
    return str(value)


def canonical_checksum(rows: list[dict[str, Any]]) -> str:
    """SHA-256 over the canonical JSON of the rows — what the manifest records."""
    payload = json.dumps(canonicalize(rows), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_cell(value: Any) -> str:
    """One CSV cell, escaped so a spreadsheet cannot evaluate it as a formula."""
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        cell = json.dumps(canonicalize(value), sort_keys=True, ensure_ascii=False)
    elif isinstance(value, str):
        cell = value
    else:
        cell = str(canonicalize(value))
    if cell == "":
        return ""
    if cell.startswith(FORMULA_PREFIXES) or cell != cell.strip():
        return ESCAPE_PREFIX + cell
    return cell


def csv_bytes(columns: list[str], rows: list[dict[str, Any]]) -> bytes:
    """UTF-8 CSV bytes with formula-safe cells and a fixed header order."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([safe_cell(row.get(col)) for col in columns])
    return buf.getvalue().encode("utf-8")


# BA-101: export trees hold participant data and must never land world-accessible.
# Modes are set explicitly (not left to the caller's umask) so exports written by the
# API container, a script, or an operator shell all land owner-only-group-readable.
EXPORT_DIR_MODE = 0o750
EXPORT_FILE_MODE = 0o640


def export_dir(path: Path) -> Path:
    """Create ``path`` (and parents) then pin the leaf to the export dir mode.

    Intermediate parents keep the caller/umask default — the writable export root
    is provisioned by ops — but the export directory itself is always owner/group
    only. The mode is applied at creation (``mkdir(mode=…)`` then an explicit
    ``chmod`` to defeat umask) so there is no world-accessible window.
    """
    path.mkdir(parents=True, exist_ok=True, mode=EXPORT_DIR_MODE)
    path.chmod(EXPORT_DIR_MODE)
    return path


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> str:
    """Write ``path`` and return the SHA-256 of the bytes written.

    The file is created atomically at ``EXPORT_FILE_MODE`` via ``os.open`` mode
    bits — there is no write-before-chmod window in which a fresh file is
    world-readable. A pre-existing permissive file is still re-pinned after the
    write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = csv_bytes(columns, rows)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, EXPORT_FILE_MODE)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
    finally:
        os.close(fd)
    os.chmod(path, EXPORT_FILE_MODE)
    return sha256_bytes(data)


def columns_of(rows: list[dict[str, Any]], preferred: list[str] | None = None) -> list[str]:
    """Stable column order: ``preferred`` first (dropping absent names), then the union of
    remaining row keys sorted alphabetically. Empty tables still get their header."""
    seen: list[str] = []
    keys: set[str] = set()
    for row in rows:
        keys.update(row)
    for name in preferred or []:
        if name in keys:
            seen.append(name)
    seen.extend(sorted(keys - set(seen)))
    return list(seen or (preferred or []))
