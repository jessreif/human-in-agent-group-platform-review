"""Telemetry-kind contract (audit M4): the server whitelist and the frontend
``TelemetryKind`` union must list the same kinds — a kind the server does not
whitelist is silently dropped at ingest, which reads as missing data."""

from __future__ import annotations

import re
from pathlib import Path

from app.services.telemetry_ingest import TELEMETRY_KINDS

ROOT = Path(__file__).resolve().parents[2]


def _frontend_kinds() -> set[str]:
    ts = (ROOT / "frontend/src/api/types.ts").read_text(encoding="utf-8")
    ts = re.sub(r"//[^\n]*", "", ts)  # comments may contain ';'
    m = re.search(r"export type TelemetryKind\s*=(.*?);", ts, re.S)
    assert m, "TelemetryKind union not found in types.ts"
    return set(re.findall(r'"(\w+)"', m.group(1)))


def test_frontend_and_server_telemetry_kinds_match() -> None:
    frontend = _frontend_kinds()
    assert frontend == set(TELEMETRY_KINDS), {
        "frontend_only": sorted(frontend - TELEMETRY_KINDS),
        "server_only": sorted(TELEMETRY_KINDS - frontend),
    }
