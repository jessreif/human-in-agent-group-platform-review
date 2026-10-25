"""Workbench response contract (audit H3): the backend ``WorkbenchView`` keys and
the frontend ``WorkbenchResponse`` interface keys must be identical — a renamed
key would otherwise break the participant UI at runtime, not at type-check."""

from __future__ import annotations

import re
from pathlib import Path

from app.services.workbench import WorkbenchView

ROOT = Path(__file__).resolve().parents[2]


def _frontend_keys() -> set[str]:
    ts = (ROOT / "frontend/src/api/types.ts").read_text(encoding="utf-8")
    ts = re.sub(r"//[^\n]*", "", ts)
    m = re.search(r"export interface WorkbenchResponse\s*\{(.*?)\n\}", ts, re.S)
    assert m, "WorkbenchResponse interface not found in types.ts"
    return set(re.findall(r"^\s{2}(\w+)\??\s*:", m.group(1), re.M))


def test_workbench_keys_match_frontend_interface() -> None:
    backend = set(WorkbenchView.__annotations__)
    frontend = _frontend_keys()
    assert backend == frontend, {
        "backend_only": sorted(backend - frontend),
        "frontend_only": sorted(frontend - backend),
    }


def test_data_use_choice_matches_frontend_union() -> None:
    from typing import get_args

    from app.db.vocabulary import DataUseChoice

    ts = (ROOT / "frontend/src/api/types.ts").read_text(encoding="utf-8")
    m = re.search(r"export type DataUseChoice\s*=([^;]*);", ts)
    assert m, "DataUseChoice not found in types.ts"
    assert set(re.findall(r'"(\w+)"', m.group(1))) == set(get_args(DataUseChoice))
