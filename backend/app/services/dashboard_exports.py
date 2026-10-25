"""Study Admin Dashboard — export catalog (WP17 Phase A, §3.9/§7).

``GET /admin/exports`` lists previously published researcher exports by reading
the ``manifest.json`` sidecars that ``export.study.write_export`` (and the
sharing/payment paths) write under ``ADMIN_EXPORT_DIR``. Listing is defensive:

- unset/readonly output dir → ``status: unavailable`` / ``summary_only``;
- bounded count (200 manifests, 64 KB each) — a directory of junk never wedges
  the admin surface;
- non-directory children, symlinks and path traversal are skipped, not followed;
- malformed, undecodable or oversized manifests are listed as ``invalid`` rows,
  never fatal — one bad entry cannot fail the whole catalog (DA-22);
- compensation products are *never* listed for the researcher role (DA-23):
  ``kind='payment'`` manifests count into ``restricted_entries`` only;
- status is ``published`` when the manifest carries the versioned
  ``investigators.export_manifest/1`` schema marker or a stored
  ``manifest_sha256``; recognizable older manifests are ``legacy``;
- ``manifest_file_sha256`` is the SHA-256 of the manifest bytes as read — an
  integrity anchor computed at listing time, independent of any stored field.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

MAX_MANIFESTS = 200
MAX_MANIFEST_BYTES = 64 * 1024

# Schema marker written by export.study.write_export — its presence means the
# manifest completed the writer's full CSV-then-manifest publication sequence.
_PUBLISHED_SCHEMA = "investigators.export_manifest/1"

# Researcher-visible product kinds (DA-23). ``payment``/``compensation`` and any
# unrecognized kind are excluded from the listing entirely — kind, table names
# and filters of a restricted product never enter the researcher response.
_PUBLISHED_KINDS = frozenset({"research_study", "sharing"})

# Fields a listing row may carry — anything else in a manifest never leaves the
# filesystem (the manifest is trusted metadata, not a passthrough).
_FIELDS = (
    "schema",
    "kind",
    "snapshot_time",
    "snapshot_isolation",
    "schema_version",
    "metric_version",
    "filters",
    "manifest_sha256",
)


def _table_rows(tables: Any) -> list[dict[str, Any]] | None:
    """Normalize the ``tables`` map; ``None`` marks a malformed manifest (DA-22)."""
    if not isinstance(tables, dict):
        return None
    rows = []
    for name, t in sorted(tables.items()):
        if not isinstance(name, str):
            return None
        entry: dict[str, Any] = {"name": name}
        if isinstance(t, dict):
            entry["row_count"] = t.get("row_count")
            entry["file"] = t.get("file")
        rows.append(entry)
    return rows[:50]


def catalog(export_dir: str | None) -> dict[str, Any]:
    """List published export manifests under ``export_dir`` (from settings)."""
    if not export_dir:
        return {
            "status": "unavailable",
            "reason": "ADMIN_EXPORT_DIR not configured",
            "exports": [],
        }
    root = Path(export_dir)
    if not root.is_dir():
        return {
            "status": "unavailable",
            "reason": f"export directory not readable: {root.name}",
            "exports": [],
        }
    rows: list[dict[str, Any]] = []
    skipped = 0
    restricted = 0
    try:
        children = sorted(root.iterdir(), key=lambda p: p.name, reverse=True)
    except OSError:
        return {
            "status": "unavailable",
            "reason": "export directory not listable",
            "exports": [],
        }
    for child in children:
        if len(rows) + skipped + restricted >= MAX_MANIFESTS:
            break
        try:
            # is_dir() follows symlinks — check the raw dirent first so a
            # symlinked path is skipped rather than read (§7.4).
            if child.is_symlink() or not child.is_dir():
                skipped += 1
                continue
            manifest_path = child / "manifest.json"
            if not manifest_path.is_file() or manifest_path.is_symlink():
                skipped += 1
                continue
            st = manifest_path.stat()
            if st.st_size > MAX_MANIFEST_BYTES:
                rows.append(
                    {"dir": child.name, "status": "invalid", "reason": "manifest_too_large"}
                )
                continue
            raw = manifest_path.read_bytes()
            file_sha = hashlib.sha256(raw).hexdigest()
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict) or "tables" not in data:
                rows.append(
                    {"dir": child.name, "status": "invalid", "reason": "manifest_malformed"}
                )
                continue
            kind = data.get("kind")
            if kind not in _PUBLISHED_KINDS:
                # Restricted (payment/compensation) or unrecognized product —
                # counted, never listed (DA-23).
                restricted += 1
                continue
            table_rows = _table_rows(data["tables"])
            if table_rows is None:
                rows.append(
                    {"dir": child.name, "status": "invalid", "reason": "manifest_malformed"}
                )
                continue
            published = bool(data.get("manifest_sha256")) or data.get("schema") == (
                _PUBLISHED_SCHEMA
            )
            row: dict[str, Any] = {
                "dir": child.name,
                "status": "published" if published else "legacy",
                "manifest_file_sha256": file_sha,
                "tables": table_rows,
            }
            for f in _FIELDS:
                row[f] = data.get(f)
            rows.append(row)
        except (OSError, ValueError, TypeError, AttributeError):
            # json.JSONDecodeError and UnicodeDecodeError are ValueError
            # subclasses; schema/type surprises degrade this one entry, never
            # the whole request (DA-22).
            rows.append({"dir": child.name, "status": "invalid", "reason": "manifest_unreadable"})
    return {
        "status": "ok" if rows or not (skipped or restricted) else "empty",
        "export_dir_configured": True,
        "writable": os.access(root, os.W_OK),
        "exports": rows,
        "skipped_entries": skipped,
        "restricted_entries": restricted,
        "bounded": MAX_MANIFESTS,
    }
