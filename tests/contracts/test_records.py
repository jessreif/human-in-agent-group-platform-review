"""INV-0001/0002/0007: execution records are complete and consistent with the pinned inputs."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import build_requirement_trace as trace
import build_status_ledger as ledger
import planlib

ROOT = Path(__file__).resolve().parents[2]
PINS = ROOT / "docs" / "baseline" / "SOURCE_CHECKSUMS.sha256"


def _pins() -> dict[str, str]:
    entries = {}
    for line in PINS.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        entries[name] = digest
    return entries


def test_pinned_inputs_match_disk() -> None:
    # Some pinned planning inputs (protocol documents, ingress snippet, files on
    # other hosts) are not distributed with the repository. Check every pin
    # whose file is present; the design spec and reference demos always are.
    checked = 0
    for name, digest in _pins().items():
        path = Path(name) if name.startswith("/") else ROOT / name
        if not path.exists():
            continue
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name
        checked += 1
    assert checked, "no pinned input is present"


def test_plan_header_pins_agree_with_pin_file() -> None:
    header = planlib.section(planlib.read(planlib.PLAN), "**Pinned input snapshot", "## 1.")
    header_hashes = set(re.findall(r"`([0-9a-f]{64})`", header))
    assert header_hashes and header_hashes <= set(_pins().values())


def test_baseline_readme_abbreviations_match_pins() -> None:
    lines = (ROOT / "docs" / "baseline" / "README.md").read_text(encoding="utf-8").splitlines()
    readme = "\n".join(line for line in lines if "not pinned" not in line)
    pins = set(_pins().values())
    for head, tail in re.findall(r"`([0-9a-f]{8})…([0-9a-f]{4})`", readme):
        assert any(p.startswith(head) and p.endswith(tail) for p in pins), (head, tail)


def test_requirement_trace_is_current_and_fully_mapped() -> None:
    generated, unmapped = trace.build()
    existing = trace.TRACE.read_text(encoding="utf-8")
    assert unmapped == []
    assert trace.splice(existing, generated) == existing, "re-run scripts/build_requirement_trace.py"


def test_every_numbered_spec_section_has_owners() -> None:
    rows = {trace._key_of(row[0]) for row in planlib.trace_tables()["spec"]}
    for sec in planlib.spec_sections():
        if not sec.key.startswith("§") and not sec.key.startswith("Appendix"):
            continue
        covered = (
            sec.key in rows
            or sec.key in trace.SUPPLEMENTS
            or any(r.startswith(sec.key + ".") for r in rows)
        )
        assert covered, sec.key


def test_status_ledger_covers_every_task() -> None:
    assert ledger.check() == []
    assert len(planlib.all_task_ids()) == len(set(planlib.all_task_ids()))
