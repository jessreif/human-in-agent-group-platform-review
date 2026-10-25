#!/usr/bin/env python3
"""Pre-launch hard reset: remove EVERY participant session (test/preview data) before the
first real participant, keeping batch, batch-group and manifest definitions.

Approved by the PI on 2026-10-08 after a verified pg_dump backup. This is the one sanctioned
path that physically removes append-only research rows: it runs as the table owner (migrator
DSN), disables the append-only USER triggers only inside its single transaction, and
re-enables them before commit — any failure rolls everything back. Not for use once real
participants have enrolled: it refuses if any live session has a non-test platform ID unless
``--include-non-test`` is passed.

    cd backend && uv run python ../scripts/prelaunch_reset.py            # dry run (counts)
    cd backend && uv run python ../scripts/prelaunch_reset.py --apply    # reset

The DSN is read from ../.env.migrate (INVESTIGATORS_MIGRATION_DATABASE_URL); never printed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine, text  # noqa: E402

from app.db import schema as s  # noqa: E402

TEST_PREFIXES = ("TEST", "E2E", "SMOKE", "XXXXX", "QA_")


def _dsn() -> str:
    for line in (ROOT / ".env.migrate").read_text().splitlines():
        if line.startswith("INVESTIGATORS_MIGRATION_DATABASE_URL="):
            return line.split("=", 1)[1].strip().replace("host.docker.internal", "127.0.0.1")
    raise SystemExit("INVESTIGATORS_MIGRATION_DATABASE_URL missing from .env.migrate")


def session_tables() -> list[str]:
    """Every table owned by or keyed to a session/participant, from the live schema model."""
    out = []
    for t in s.metadata.sorted_tables:
        refs = {fk.column.table.name for c in t.columns for fk in c.foreign_keys}
        if t.name == "admin_audit":
            continue  # handled separately: only session-linked audit rows go
        if t.name in ("session", "participant") or "session_id" in t.c or "session" in refs:
            out.append(f"{t.schema}.{t.name}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--include-non-test", action="store_true")
    args = ap.parse_args()
    tables = session_tables()
    engine = create_engine(_dsn())
    with engine.begin() as conn:
        pids = [
            r[0]
            for r in conn.execute(
                text(
                    "select l.platform_pid from identity.platform_link l "
                    "join app.session s using (session_id) where s.mode = 'live'"
                )
            )
        ]
        real = [p for p in pids if not p.upper().startswith(TEST_PREFIXES)]
        if real and not args.include_non_test:
            print(f"REFUSED: {len(real)} live session(s) with non-test platform IDs")
            return 2
        # Table names come from the schema model (session_tables), never from input.
        counts = {
            t: conn.execute(text(f"select count(*) from {t}")).scalar_one()  # noqa: S608
            for t in tables
        }
        audit = conn.execute(
            text("select count(*) from app.admin_audit where session_id is not null")
        ).scalar_one()
        for t, n in counts.items():
            print(f"{n:>8}  {t}")
        print(f"{audit:>8}  app.admin_audit (session-linked rows only)")
        if not args.apply:
            print("dry run — pass --apply to reset")
            return 0
        immutable = [f"app.{t}" for t in s.IMMUTABLE_TABLES]
        for t in immutable:
            conn.execute(text(f"ALTER TABLE {t} DISABLE TRIGGER USER"))
        conn.execute(text("DELETE FROM app.admin_audit WHERE session_id IS NOT NULL"))
        # Child-first DELETEs (TRUNCATE is refused while admin_audit keeps its FK to
        # session). No cascades: a row in an unlisted referencing table makes a DELETE
        # fail, and the whole transaction rolls back loudly.
        for t in reversed(tables):
            conn.execute(text(f"DELETE FROM {t}"))  # noqa: S608
        # Fire the deferred FK checks now (they must pass) so triggers can be re-enabled.
        conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        for t in immutable:
            conn.execute(text(f"ALTER TABLE {t} ENABLE TRIGGER USER"))
        conn.execute(
            text(
                "INSERT INTO app.admin_audit (audit_id, actor, action, payload) VALUES "
                "(gen_random_uuid(), 'operator:prelaunch_reset', 'prelaunch_reset', "
                "CAST(:p AS jsonb))"
            ),
            {"p": f'{{"sessions_removed": {counts["app.session"]}, "approved": "PI 2026-10-08"}}'},
        )
        left = conn.execute(text("select count(*) from app.session")).scalar_one()
        disabled = conn.execute(
            text(
                "select count(*) from pg_trigger where not tgisinternal and tgenabled = 'D' "
                "and tgrelid::regclass::text like 'app.%'"
            )
        ).scalar_one()
        if left or disabled:
            raise SystemExit(f"post-check failed: sessions={left} disabled_triggers={disabled}")
    print(f"reset complete: removed {counts['app.session']} sessions; triggers re-enabled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
