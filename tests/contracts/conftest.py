"""Repository-level contract tests (INV-0107). Run with the backend environment:

    uv run --project backend --frozen pytest tests/contracts
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT / "scripts", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
