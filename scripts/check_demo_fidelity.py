#!/usr/bin/env python3
"""Report playable-demo text that does not match the time-pressure study materials.

Exits 0 when every difference it knows how to detect is named in
``demos/README.md``. It does not edit the demo.

    python3 scripts/check_demo_fidelity.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "demos" / "template" / "investigators_demo.html").read_text(encoding="utf-8")
NOTES = (ROOT / "demos" / "README.md").read_text(encoding="utf-8")

PRESSURE = "Yikes this is a lot of info"
WORKLOAD = "yikes this is a lot for 5 min"
BOT = "automated account"


def main() -> int:
    problems: list[str] = []
    if PRESSURE.lower() in TEMPLATE.lower():
        print("template contains the pressure-bank remark")
    else:
        print("template does not contain the pressure-bank remark")
        if "workload" not in NOTES.lower():
            problems.append("workload-remark gap is not documented")
    if WORKLOAD.lower() not in TEMPLATE.lower():
        print("template does not contain the expected workload remark string")
    else:
        print("template contains the workload remark string")
    if BOT not in TEMPLATE:
        print("template does not contain the truthful automation line")
        if "canned social" not in NOTES.lower() and "bot" not in NOTES.lower():
            problems.append("bot-question gap is not documented")
    else:
        print("template contains an automation-line string")
    if "PLACEHOLDER-CODE" not in TEMPLATE:
        problems.append("template is missing the completion-code placeholder")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print("documented demo gaps only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
