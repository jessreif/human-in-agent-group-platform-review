#!/usr/bin/env python3
"""Privileged retention runner (INV-1306/1307; spec §11).

The admin API's retention endpoints plan and record actions, but the schema deliberately
keeps physical identity deletion away from the runtime role (``identity.platform_link``
is INSERT/SELECT-only for ``investigators_user``). This script is the privileged path:
run it with the migrator DSN (the table owner) — never the runtime DSN.

    cd backend && uv run python ../scripts/retention_purge.py \
        --dsn "$INVESTIGATORS_MIGRATION_DATABASE_URL" --apply

What it does, all inside one audited transaction:

- ``purge_due_links`` — delete ``identity.platform_link`` rows whose ``purge_after``
  (the S5 study-close + 48 h schedule set by ``POST /admin/retention/schedule``) has
  passed;
- ``reapply_tombstones`` — after a restore, re-delete every session whose recorded
  ``deletion_tombstone`` still has restorable rows (tombstones live in append-only
  ``admin_audit``, so they survive the backup that carried the data back);
- ``--session-id`` — re-run ``apply_deletion`` for named sessions (privileged tables
  still report ``blocked`` under a role without the grant; append-only research tables
  await the separately-migrated mechanism named in ``app.db.schema``).

Defaults are safe: ``--dry-run`` is implied unless ``--apply`` is passed, and every call
writes ``admin_audit`` rows identifying the operator (``--actor``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.engine import make_engine
from app.services import retention


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dsn",
        default=None,
        help="privileged DSN (default: $INVESTIGATORS_MIGRATION_DATABASE_URL). The "
        "runtime DSN cannot delete identity rows and will report blocked_privileged.",
    )
    ap.add_argument("--actor", default="ops:retention_purge", help="audit actor label")
    ap.add_argument("--apply", action="store_true", help="actually delete (default dry-run)")
    ap.add_argument(
        "--study-close-at",
        default=None,
        help="ISO timestamp of study close: also sets purge_after on unscheduled links",
    )
    ap.add_argument(
        "--session-id",
        action="append",
        default=[],
        type=uuid.UUID,
        help="re-apply the deletion tombstone for this session (repeatable)",
    )
    args = ap.parse_args(argv)

    dsn = args.dsn or os.environ.get("INVESTIGATORS_MIGRATION_DATABASE_URL")
    if not dsn:
        print("ERROR: pass --dsn or set INVESTIGATORS_MIGRATION_DATABASE_URL", file=sys.stderr)
        return 2
    engine = make_engine(dsn)
    try:
        with engine.begin() as conn:
            report: dict[str, object] = {"apply": args.apply, "actor": args.actor}
            if args.study_close_at:
                closed = datetime.fromisoformat(args.study_close_at)
                if closed.tzinfo is None:
                    closed = closed.replace(tzinfo=UTC)
                report["schedule"] = retention.schedule_platform_purge(
                    conn, study_close_at=closed, actor=args.actor
                )
            report["purge"] = retention.purge_due_links(
                conn, actor=args.actor, dry_run=not args.apply
            )
            report["tombstones_reapplied"] = retention.reapply_tombstones(conn, actor=args.actor)
            for sid in args.session_id:
                report[f"session:{sid}"] = retention.apply_deletion(
                    conn,
                    sid,
                    actor=args.actor,
                    reason="cli_retention",
                    scope="cli",
                    dry_run=not args.apply,
                )
    finally:
        engine.dispose()
    print(json.dumps(report, indent=2, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
