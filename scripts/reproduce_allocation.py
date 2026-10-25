#!/usr/bin/env python3
"""Reproduce one block randomization and check it is stable.

    cd backend && python ../scripts/reproduce_allocation.py

The manifest hash below is the published ``tp_ai_v7`` manifest hash.
``block_order`` does not read the database.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.allocation import block_order  # noqa: E402

MANIFEST = "c5b5ef4cf2dd0c194cae9814112d2a54fef09d9d1a0ad80273f25c04bef8198c"
BATCH = "example-block"
ARMS = ("high", "low")


def main() -> int:
    first = block_order(MANIFEST, BATCH, 0, ARMS)
    second = block_order(MANIFEST, BATCH, 0, ARMS)
    if first != second:
        print("allocation differed", file=sys.stderr)
        return 1
    print(" ".join(first))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
