#!/usr/bin/env python3
"""Seed and validate docs/IMPLEMENTATION_STATUS.md (INV-0001).

The ledger is hand-maintained after seeding. This tool only:
  --seed   create the ledger from the plan when it does not exist yet (every task not_started;
           no task is ever copied as already passed);
  --sync   append rows for task IDs added to the plan since seeding, keeping existing rows;
  --check  verify every plan task appears exactly once, states are valid, and a task marked
           `complete` cites evidence and is checked in the plan (and vice versa).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
import planlib  # noqa: E402

LEDGER = planlib.ROOT / "docs" / "IMPLEMENTATION_STATUS.md"
STATES = {"not_started", "in_progress", "blocked", "complete"}
HEADER = "| ID | WP | Task | Owner role | Depends on | State | Artifact / evidence | Blocker | Next action |"
ROW_RE = re.compile(r"^\| (INV-\d{4}) \|")


def _short(text: str, words: int = 14) -> str:
    plain = re.sub(r"[*`]", "", text)
    parts = plain.split()
    return " ".join(parts[:words]) + (" …" if len(parts) > words else "")


def _row(task: planlib.Task, wp: planlib.WorkPackage) -> str:
    cells = [task.task_id, wp.wp, _short(task.text), wp.owner, wp.dependencies,
             "not_started", "", "", ""]
    return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"


def parse_rows(text: str) -> Dict[str, List[str]]:
    rows: Dict[str, List[str]] = {}
    for line in text.splitlines():
        if ROW_RE.match(line):
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]
            if cells[0] in rows:
                raise ValueError(f"duplicate ledger row {cells[0]}")
            rows[cells[0]] = cells
    return rows


def seed() -> str:
    lines = ["| " + " | ".join(["---"] * 9) + " |"]
    body = []
    for wp in planlib.work_packages():
        for task in wp.tasks:
            body.append(_row(task, wp))
    return "\n".join([HEADER] + lines + body) + "\n"


def check() -> List[str]:
    problems: List[str] = []
    text = LEDGER.read_text(encoding="utf-8")
    rows = parse_rows(text)
    plan_tasks = {t.task_id: t for wp in planlib.work_packages() for t in wp.tasks}
    for task_id in plan_tasks:
        if task_id not in rows:
            problems.append(f"{task_id} missing from ledger")
    for task_id, cells in rows.items():
        if task_id not in plan_tasks:
            problems.append(f"{task_id} in ledger but not in plan")
            continue
        state = cells[5]
        if state not in STATES:
            problems.append(f"{task_id}: invalid state {state!r}")
        if state == "complete" and not cells[6]:
            problems.append(f"{task_id}: complete without evidence")
        if state == "blocked" and not cells[7]:
            problems.append(f"{task_id}: blocked without a blocker")
        if state == "complete" and not plan_tasks[task_id].checked:
            problems.append(f"{task_id}: complete in ledger but unchecked in plan")
        if plan_tasks[task_id].checked and state != "complete":
            problems.append(f"{task_id}: checked in plan but ledger state is {state}")
    return problems


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--seed", action="store_true")
    group.add_argument("--sync", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.seed:
        if LEDGER.exists():
            print("ledger exists; use --sync")
            return 1
        print(seed(), end="")
        return 0
    if args.sync:
        text = LEDGER.read_text(encoding="utf-8")
        rows = parse_rows(text)
        additions = [_row(t, wp) for wp in planlib.work_packages() for t in wp.tasks if t.task_id not in rows]
        if additions:
            LEDGER.write_text(text.rstrip("\n") + "\n" + "\n".join(additions) + "\n", encoding="utf-8")
        print(f"added {len(additions)} row(s)")
        return 0
    problems = check()
    for problem in problems:
        print(f"FAIL: {problem}")
    if not problems:
        print(f"OK: ledger covers all {len(planlib.all_task_ids())} plan tasks with valid states")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
