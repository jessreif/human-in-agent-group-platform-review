"""Harvest real participant utterances into ``lab_corpus.json``.

The authored pools in ``utterances.py`` are team-written — the same self-
reference problem the utterance engine exists to soften. This module pulls
participant-authored chat bodies out of *finished* sessions on a read-only DSN,
classifies each line into the pool it belongs to, normalizes crate digits to
the ``{c}`` placeholder the pools use, dedupes, and merges into the committed
``lab_corpus.json``. The utterance engine then draws from
``authored_pool + lab_pool`` — every run still replays by ``--seed`` because
the corpus is a committed file, not a live query.

Read-only by construction: a single SELECT against the ``research`` role's DSN
(the same DSN the export path uses). Session ids already present in the corpus
are skipped, so harvests are incremental and idempotent.

Usage:
    python -m harness.harvest --dsn "$INVESTIGATORS_RESEARCH_DSN" \
        --out backend/harness/lab_corpus.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine

from app.db import schema as s
from app.services import engines

CORPUS_VERSION = 1
_MAX_LINE = 200

# Pools the corpus feeds — names must match utterances.py constants.
POOL_KEYS = (
    "GREETS",
    "TEAM_ASKS",
    "TEAM_ASK_FOLLOWUPS",
    "TEAM_SOCIAL",
    "TEAM_DECLINE_ASK",
    "ASSISTANT_CRATE",
    "ASSISTANT_CONTENTS",
    "PROBES",
    "NEAR_MISS_PROBES",
)

_GREET = re.compile(r"^\s*(hi|hello|hey|yo|good (morning|afternoon|evening))\b", re.I)
_CRATE_REF = re.compile(r"\bcrate\s*#?\s*([1-8])\b|\b([1-8])\s*$", re.I)
_DIGIT_TO_SLOT = re.compile(r"(\bcrate\s*#?)[1-8]\b", re.I)
_DECLINE = re.compile(r"\b(nothing|don't have|no info|empty|can't help)\b", re.I)
# The server's own render of a structured teammate_request command — a message
# row exists but the participant never typed it; not authored language.
_STRUCTURED_RENDER = re.compile(r"^requesting intel on crate\s*#?\s*[1-8]\.?$", re.I)


def _classify(body: str, channel: str) -> str | None:
    """Map a participant line to its utterance pool (or None → skip)."""
    text = body.strip()
    if not text or len(text) > _MAX_LINE:
        return None
    if channel == "assistant":
        if _CRATE_REF.search(text):
            return "ASSISTANT_CRATE"
        return "ASSISTANT_CONTENTS"
    if _STRUCTURED_RENDER.match(text):
        return None
    if engines.is_automation_probe(text):
        return "PROBES"
    if engines.probe_near_miss(text):
        return "NEAR_MISS_PROBES"
    if _GREET.match(text) and not _CRATE_REF.search(text):
        return "GREETS"
    if _DECLINE.search(text):
        return "TEAM_DECLINE_ASK"
    if _CRATE_REF.search(text):
        return "TEAM_ASKS"
    return "TEAM_SOCIAL"


def _normalize(body: str) -> str:
    """Collapse a concrete crate number into the ``{c}`` placeholder so the
    line stays parameterizable — harvested "crate 3" renders for any crate."""
    text = body.strip()[:_MAX_LINE]
    return _DIGIT_TO_SLOT.sub(r"\g<1>{c}", text)


def load_corpus(path: Path) -> dict[str, Any]:
    if path.exists():
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") == CORPUS_VERSION:
            return data
    return {"version": CORPUS_VERSION, "harvested_at": None, "sessions": [], "pools": {}}


def harvest(engine: Engine, corpus_path: Path) -> dict[str, Any]:
    """Pull participant-authored messages not yet in the corpus and merge."""
    corpus = load_corpus(corpus_path)
    seen_sessions = set(corpus["sessions"])
    pools: dict[str, list[str]] = corpus["pools"]
    for key in POOL_KEYS:
        pools.setdefault(key, [])
    seen_lines = {line for lines in pools.values() for line in lines}

    with engine.connect() as conn:
        rows = conn.execute(
            select(s.message.c.session_id, s.message.c.channel, s.message.c.body)
            .where(
                s.message.c.actor == "participant",
                s.message.c.channel.in_(("team", "assistant")),
                s.message.c.committed_seq.is_not(None),
            )
            .order_by(s.message.c.session_id, s.message.c.committed_seq)
        ).all()

    new_sessions: set[str] = set()
    added = 0
    for session_id, channel, body in rows:
        if str(session_id) in seen_sessions or not body:
            continue
        new_sessions.add(str(session_id))
        pool_key = _classify(body, channel)
        if pool_key is None:
            continue
        line = _normalize(body)
        if line.lower() in {x.lower() for x in seen_lines}:
            continue
        seen_lines.add(line)
        pools[pool_key].append(line)
        added += 1

    corpus["sessions"] = sorted(seen_sessions | new_sessions)
    corpus["added_this_harvest"] = added
    return corpus


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True, help="read-only research DSN")
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "lab_corpus.json")
    args = parser.parse_args(argv)

    corpus = harvest(create_engine(args.dsn), args.out)
    args.out.write_text(json.dumps(corpus, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    total = sum(len(v) for v in corpus["pools"].values())
    print(
        f"wrote {args.out}: {corpus['added_this_harvest']} new lines "
        f"({total} total) across {len(corpus['sessions'])} sessions"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
