#!/usr/bin/env python3
"""Offline study export to CSV + manifest.json (INV-1106/1109; spec §9.2).

Writes one directory of formula-safe UTF-8 CSVs plus a ``manifest.json`` pinning the
snapshot time, filters, schema/metric versions, row counts and per-table checksums. The
whole export is read inside one REPEATABLE READ transaction — a consistent snapshot of
committed data.

Run from ``backend/`` (the app package is imported from there)::

    cd backend && uv run python ../scripts/export_study.py \\
        --out /tmp/export-s2 --batch-id live-s2-001 --kind research

``--kind research`` (default) must be pointed at the research/runtime DSN and never reads
``identity.*``. ``--kind payment`` must be pointed at the ``investigators_compensation``
DSN — it is the only product that joins ``identity.platform_link``, and its table set is a
fixed allowlist. ``--sharing`` additionally writes the identity-free sharing bundle
(all cells verbatim — 2026-10-16 owner direction: no exportable file
carries ``withheld_unreviewed``).

The DSN comes from ``--dsn`` or ``DATABASE_URL``; it is never printed to the manifest
(credentials would leak). The manifest records only which product ran.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.engine import make_engine
from app.export.csvsafe import EXPORT_FILE_MODE, columns_of, export_dir, write_csv
from app.export.restricted import bundle_checksums, payment_export, sharing_bundle
from app.export.study import export_study, write_export


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", default=None, help="database DSN (default: $DATABASE_URL)")
    ap.add_argument("--out", type=Path, required=True, help="output directory")
    ap.add_argument(
        "--kind",
        choices=("research", "payment"),
        default="research",
        help="research = identity-free committed-data export; payment = restricted "
        "compensation export (requires the investigators_compensation DSN)",
    )
    ap.add_argument("--batch-id", default=None)
    ap.add_argument("--study-key", default=None)
    ap.add_argument("--session-id", action="append", default=[], type=uuid.UUID)
    ap.add_argument(
        "--sharing",
        action="store_true",
        help="also write the identity-free sharing bundle (research kind only)",
    )
    args = ap.parse_args(argv)

    import os

    dsn = args.dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        ap.error("no DSN: pass --dsn or set DATABASE_URL")
    session_ids = args.session_id or None
    engine = make_engine(dsn)
    try:
        if args.kind == "payment":
            if args.batch_id:
                print(
                    "note: --batch-id is not usable by the payment export — the "
                    "compensation role cannot read app.session; pass --session-id "
                    "instead",
                    file=sys.stderr,
                )
            out = payment_export(engine, session_ids=session_ids)
            export_dir(args.out)
            manifest_tables: dict[str, Any] = {}
            for name, rows in sorted(out.items()):
                cols = columns_of(rows)
                sha = write_csv(args.out / f"{name}.csv", cols, rows)
                manifest_tables[name] = {
                    "file": f"{name}.csv",
                    "columns": cols,
                    "row_count": len(rows),
                    "file_sha256": sha,
                }
            manifest = {
                "schema": "investigators.export_manifest/1",
                "requirement": "INV-1109",
                "kind": "payment",
                "scope": "linkage + entitlement + outcome only (compensation role)",
                "filters": {
                    "batch_id": args.batch_id,
                    "session_ids": [str(i) for i in session_ids or []],
                },
                "tables": manifest_tables,
            }
            payment_manifest = args.out / "manifest.json"
            payment_manifest.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            payment_manifest.chmod(EXPORT_FILE_MODE)
            print(f"wrote payment export to {args.out} ({len(manifest_tables)} tables)")
            return 0

        flag_env = {
            "scripted_teammates_enabled": "SCRIPTED_TEAMMATES_ENABLED",
            "ai_assistant_enabled": "AI_ASSISTANT_ENABLED",
            "crate_overlap_enabled": "CRATE_OVERLAP_ENABLED",
            "crate_number_enabled": "CRATE_NUMBER_ENABLED",
            "time_pressure_enabled": "TIME_PRESSURE_ENABLED",
            "ai_qa_enabled": "AI_QA_ENABLED",
        }
        feature_flags = {
            name: os.environ[env].strip().lower() in ("1", "true", "yes", "on")
            for name, env in flag_env.items()
            if env in os.environ
        }
        export = export_study(
            engine,
            batch_id=args.batch_id,
            study_key=args.study_key,
            session_ids=session_ids,
            feature_flags=feature_flags or None,
        )
        manifest = write_export(export, args.out)
        if args.sharing:
            sids = [r["session_id"] for r in export.tables["session"].rows]
            with engine.connect() as conn:
                conn = conn.execution_options(isolation_level="REPEATABLE READ")
                # 2026-10-16: cells emit verbatim — redaction reviews no longer gate.
                bundle = sharing_bundle(conn, session_ids=sids)
            for name, checksum in bundle_checksums(bundle).items():
                table = bundle.tables[name]
                cols = columns_of(table.rows)
                sha = write_csv(args.out / f"sharing_{name}.csv", cols, table.rows)
                manifest["tables"][f"sharing_{name}"] = {
                    "file": f"sharing_{name}.csv",
                    "columns": cols,
                    "row_count": len(table.rows),
                    "file_sha256": sha,
                    "rows_sha256": checksum,
                    "identity_free": True,
                }
            research_manifest = args.out / "manifest.json"
            research_manifest.write_text(
                json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            research_manifest.chmod(EXPORT_FILE_MODE)
        print(f"wrote research export to {args.out} ({len(manifest['tables'])} tables)")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
