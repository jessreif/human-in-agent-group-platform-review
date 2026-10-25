#!/usr/bin/env python3
"""Rebuild one world from a fixed seed and check the rebuild matches.

    cd backend && python ../scripts/regenerate_task.py

Uses ``build_world`` twice from the same seed. Prints a SHA-256 of the
canonical world dump. Does not read a database.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.domain.manifests.canonical import canonical_bytes  # noqa: E402
from app.domain.rng import RandomStreams  # noqa: E402
from app.domain.world import build_world  # noqa: E402

SEED = "ab" * 32


def _dump(seed: str) -> bytes:
    world = build_world(RandomStreams(seed))
    # world_id is uuid4() inside build_world and is not a function of the seed.
    body = {
        "assigned": list(world.assigned),
        "role_of": {str(k): v for k, v in world.role_of.items()},
        "cutoff_of": {str(k): v for k, v in world.cutoff_of.items()},
        "holdings": {k: list(v) for k, v in world.holdings.items()},
        "participant_decisive": {str(k): v for k, v in world.participant_decisive.items()},
    }
    return canonical_bytes(body)


def main() -> int:
    first = _dump(SEED)
    second = _dump(SEED)
    if first != second:
        print("world rebuild differed", file=sys.stderr)
        return 1
    print(hashlib.sha256(first).hexdigest())
    print("world_id is uuid4() and is not part of this hash")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
