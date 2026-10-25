#!/usr/bin/env python3
"""Extract reference constants from the two HTML demos without altering them (INV-0003).

Writes tests/fixtures/reference/demo_constants.json: literal arrays/objects/numbers parsed from
the demo JavaScript, the INV_FINISH/embedded-data field lists, payload keys and every logged
event type, each tagged with the demo file's SHA-256. Behavior that lives in code rather than
literals (schedules, delays, seat choices, defects) is documented in the hand-authored
tests/fixtures/reference/behavior_reference.json, whose line anchors are verified by
tests/contracts/test_reference_fixtures.py.

    python3 scripts/extract_demo_reference.py           # (re)write the fixture
    python3 scripts/extract_demo_reference.py --check   # exit 1 if the fixture has drifted

Runs on the host's Python 3.9.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
DEMOS = {
    # The pinned S2 behavioral reference now lives under docs/baseline/reference/
    # under its frozen historical name (bound `debrief.tp_reference` artifacts
    # cite it by path) — demos/template/investigators_demo.html is a playable mock of
    # the current build, not an extraction source (INV-0003, 2026-10-06 move).
    "time_pressure": ROOT / "docs/baseline/reference/investigators_time_pressure_demo.html",
    "peer_evaluation": ROOT / "docs/baseline/reference/investigators_peer_eval_demo.html",
}
OUTPUT = ROOT / "tests" / "fixtures" / "reference" / "demo_constants.json"

CONSTANTS = [
    "HIDEOUTS",
    "CRATE_CONTENTS",
    "N_CRATES",
    "N_ASSIGNED",
    "T_ARRIVE",
    "T_STILL",
    "T_DEPART",
    "T_TRANSIT",
    "GREETINGS",
    "CHECKIN_AT",
    "CHECKIN_COLLECT_MS",
    "PRESSURE_CONFIG",
    "EVAL_CONFIG",
    "VOICE",
    "ACK_P",
    "AI_TIMEOUT_MS",
    "AI_SYSTEM",
    "ITEMS",
    "DEMO_POST_ITEMS",
    "DEMO_ENTRIES",
]


class JsLiteralError(ValueError):
    pass


class JsLiteralParser:
    """Parses the JSON-like subset of JavaScript literals used by the demos."""

    _IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
    _NUMBER = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")

    def __init__(self, text: str, pos: int) -> None:
        self.text = text
        self.pos = pos

    def _skip(self) -> None:
        while self.pos < len(self.text):
            if self.text[self.pos].isspace():
                self.pos += 1
            elif self.text.startswith("//", self.pos):
                self.pos = self.text.index("\n", self.pos)
            elif self.text.startswith("/*", self.pos):
                self.pos = self.text.index("*/", self.pos) + 2
            else:
                return

    def parse(self) -> Any:
        self._skip()
        char = self.text[self.pos]
        if char == "[":
            return self._array()
        if char == "{":
            return self._object()
        if char in "'\"":
            return self._string()
        number = self._NUMBER.match(self.text, self.pos)
        if number:
            self.pos = number.end()
            raw = number.group(0)
            return float(raw) if any(c in raw for c in ".eE") else int(raw)
        ident = self._IDENT.match(self.text, self.pos)
        if ident and ident.group(0) in ("true", "false", "null"):
            self.pos = ident.end()
            return {"true": True, "false": False, "null": None}[ident.group(0)]
        raise JsLiteralError(f"unsupported literal at offset {self.pos}: {self.text[self.pos:self.pos + 30]!r}")

    def _array(self) -> List[Any]:
        self.pos += 1
        items: List[Any] = []
        while True:
            self._skip()
            if self.text[self.pos] == "]":
                self.pos += 1
                return items
            items.append(self.parse())
            self._skip()
            if self.text[self.pos] == ",":
                self.pos += 1

    def _object(self) -> Dict[str, Any]:
        self.pos += 1
        result: Dict[str, Any] = {}
        while True:
            self._skip()
            if self.text[self.pos] == "}":
                self.pos += 1
                return result
            if self.text[self.pos] in "'\"":
                key = self._string()
            else:
                ident = self._IDENT.match(self.text, self.pos)
                if not ident:
                    raise JsLiteralError(f"bad object key at offset {self.pos}")
                key = ident.group(0)
                self.pos = ident.end()
            self._skip()
            if self.text[self.pos] != ":":
                raise JsLiteralError(f"expected ':' at offset {self.pos}")
            self.pos += 1
            result[key] = self.parse()
            self._skip()
            if self.text[self.pos] == ",":
                self.pos += 1

    def _string(self) -> str:
        quote = self.text[self.pos]
        self.pos += 1
        out: List[str] = []
        escapes = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
        while True:
            char = self.text[self.pos]
            if char == "\\":
                nxt = self.text[self.pos + 1]
                if nxt == "u":
                    out.append(chr(int(self.text[self.pos + 2 : self.pos + 6], 16)))
                    self.pos += 6
                    continue
                out.append(escapes.get(nxt, nxt))
                self.pos += 2
                continue
            if char == quote:
                self.pos += 1
                return "".join(out)
            if char == "\n":
                raise JsLiteralError("unterminated string")
            out.append(char)
            self.pos += 1


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def extract_constant(text: str, name: str) -> Tuple[Any, int]:
    match = re.search(rf"\b(?:var|const|let)\s+{name}\s*=\s*", text)
    if not match:
        raise KeyError(name)
    parser = JsLiteralParser(text, match.end())
    value = parser.parse()
    join = re.match(r"\s*\.join\(\s*'((?:\\.|[^'])*)'\s*\)", text[parser.pos :])
    if join and isinstance(value, list):
        separator = JsLiteralParser(f"'{join.group(1)}'", 0).parse()
        value = separator.join(value)
    return value, line_of(text, match.start())


def _block(text: str, start_marker: str, end_marker: str) -> Tuple[str, int]:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end], line_of(text, start)


def finish_fields(text: str, study: str) -> Dict[str, Any]:
    if study == "time_pressure":
        marker, end, payload_marker = (
            "window.INV_FINISH(payload, {",
            "\n      });",
            "var payload = {",
        )
    else:
        marker, end, payload_marker = ("window.__FLAT = {", "\n  };", "const payload = {")
    block, line = _block(text, marker, end)
    fields = re.findall(r"^\s*(inv_\w+)\s*:", block, re.M)
    payload, payload_line = _block(text, payload_marker, "};")
    return {
        "embedded_data_fields": fields,
        "embedded_data_line": line,
        "payload_keys": re.findall(r"^\s*(\w+)\s*:", payload, re.M),
        "payload_line": payload_line,
        "scoresheet_field": "inv_scoresheet" if "ED('inv_scoresheet'" in text else None,
    }


def event_types(text: str) -> List[str]:
    return sorted(set(re.findall(r"\brecord\('([a-z_]+)'", text)))


def build() -> Dict[str, Any]:
    per_demo: Dict[str, Dict[str, Any]] = {}
    sources = {}
    for study, path in DEMOS.items():
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        # Repo-relative path, not basename — the S2 archive shares its basename with
        # the root playable mock, and only the full path resolves to the pinned bytes.
        sources[study] = {
            "file": str(path.relative_to(ROOT)),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        found: Dict[str, Any] = {}
        for name in CONSTANTS:
            try:
                value, line = extract_constant(text, name)
            except KeyError:
                continue
            found[name] = {"value": value, "line": line}
        found["__finish"] = finish_fields(text, study)
        found["__events"] = event_types(text)
        per_demo[study] = found

    shared: Dict[str, Any] = {}
    specific: Dict[str, Dict[str, Any]] = {study: {} for study in DEMOS}
    tp, pe = per_demo["time_pressure"], per_demo["peer_evaluation"]
    for name in CONSTANTS:
        in_tp, in_pe = name in tp, name in pe
        if in_tp and in_pe and tp[name]["value"] == pe[name]["value"]:
            shared[name] = {
                "value": tp[name]["value"],
                "line": {"time_pressure": tp[name]["line"], "peer_evaluation": pe[name]["line"]},
            }
            continue
        for study, table in (("time_pressure", tp), ("peer_evaluation", pe)):
            if name in table:
                specific[study][name] = table[name]
    for study in DEMOS:
        specific[study]["inv_finish"] = per_demo[study]["__finish"]
        specific[study]["logged_event_types"] = per_demo[study]["__events"]
    return {
        "generated_by": "scripts/extract_demo_reference.py",
        "note": "Extracted verbatim from the demo JavaScript; the demos are behavioral references, "
        "not proof of the fielded Qualtrics versions (spec §1.1).",
        "sources": sources,
        "shared": shared,
        "time_pressure": specific["time_pressure"],
        "peer_evaluation": specific["peer_evaluation"],
    }


def render(data: Dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the fixture has drifted")
    args = parser.parse_args(argv)
    rendered = render(build())
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != rendered:
            print(f"DRIFT: {OUTPUT.relative_to(ROOT)} does not match the demos; re-run the extractor")
            return 1
        print(f"OK: {OUTPUT.relative_to(ROOT)} matches the demos")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
