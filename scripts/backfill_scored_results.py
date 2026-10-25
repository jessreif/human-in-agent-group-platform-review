#!/usr/bin/env python3
"""Backfill scored ``app.result`` rows for sessions whose answers were frozen before the
deadline handler learned to persist scores.

A session is a candidate when it has ``answer_final`` rows (its task answers were frozen)
but no ``result`` row. The score is recomputed with the canonical ``scoring.score_session``
and inserted with ``status='scored'`` and the freeze's ``finalized_seq``. Idempotent:
sessions that already carry a result row are skipped; running twice is a no-op. Dry-run by
default — pass ``--apply`` to write.

    cd backend && uv run python ../scripts/backfill_scored_results.py            # dry run
    cd backend && uv run python ../scripts/backfill_scored_results.py --apply    # write

The DSN is read from ../.env.migrate (INVESTIGATORS_MIGRATION_DATABASE_URL); never printed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine, func, select  # noqa: E402

from app.db import schema as s  # noqa: E402
from app.measurement import scoring  # noqa: E402


def _dsn() -> str:
    for line in (ROOT / ".env.migrate").read_text().splitlines():
        if line.startswith("INVESTIGATORS_MIGRATION_DATABASE_URL="):
            return line.split("=", 1)[1].strip().replace("host.docker.internal", "127.0.0.1")
    raise SystemExit("INVESTIGATORS_MIGRATION_DATABASE_URL missing from .env.migrate")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    engine = create_engine(_dsn())
    with engine.begin() as conn:
        # Freeze seq/at come from the answer_final rows themselves — the same values the
        # deadline handler stamps when it writes the result inline.
        candidates = conn.execute(
            select(
                s.answer_final.c.session_id,
                func.max(s.answer_final.c.finalized_seq).label("finalized_seq"),
                func.max(s.answer_final.c.finalized_at).label("finalized_at"),
            )
            .where(
                ~s.answer_final.c.session_id.in_(select(s.result.c.session_id))
            )
            .group_by(s.answer_final.c.session_id)
        ).all()
        print(f"candidates (frozen answers, no result row): {len(candidates)}")
        for row in candidates:
            sid = row.session_id
            score = scoring.score_session(conn, sid)
            print(
                f"  {str(sid)[:8]} correct_total={score.correct_total.numerator} "
                f"teamdep={score.correct_teamdep.numerator} "
                f"ownfile={score.correct_ownfile.numerator} "
                f"answered={score.answered_count} missing={score.missing_count}"
            )
            if args.apply:
                conn.execute(
                    s.result.insert().values(
                        session_id=sid,
                        scoring_version=score.scoring_version,
                        status="scored",
                        correct_total=score.correct_total.numerator or 0,
                        correct_teamdep=score.correct_teamdep.numerator or 0,
                        correct_ownfile=score.correct_ownfile.numerator or 0,
                        counts=score.as_dict(),
                        finalized_seq=row.finalized_seq,
                        finalized_at=row.finalized_at,
                    )
                )
        if not args.apply:
            print("dry run — pass --apply to write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
