#!/usr/bin/env python3
"""Generate the machine-maintained part of docs/REQUIREMENT_TRACE.md (INV-0007).

Sources (pinned by hash in docs/baseline/SOURCE_CHECKSUMS.sha256):
- plan §9.1-9.3 tables: spec section / HB-xx / Timing Fx-Px -> owning tasks and decisions;
- plan §6: work package -> design gates (used as the planned verification for each row);
- the specification: every sentence, bullet or table row containing must/required/never.

Each clause inherits the owners of its spec section (or, for a parent section without its own
row, the union of its subsections). Anything left without an owner or a verification is listed
under "Unmapped" and makes --check fail: an unmapped clause is a plan defect.

    python3 scripts/build_requirement_trace.py           # rewrite the generated block
    python3 scripts/build_requirement_trace.py --check   # exit 1 if stale or unmapped
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import planlib  # noqa: E402

TRACE = planlib.ROOT / "docs" / "REQUIREMENT_TRACE.md"
BEGIN = "<!-- BEGIN GENERATED: scripts/build_requirement_trace.py -->"
END = "<!-- END GENERATED -->"
PREAMBLE_OWNERS = (["INV-0002", "INV-0007"], [], [])  # restated normatively in §3.1/§11
# Plan §9.1 maps some spec sections only to plan sections (no task). Each supplement below is a
# proposed plan §9.1 amendment, printed in section F so it is reviewed rather than hidden.
SUPPLEMENTS = {
    "§14": (
        "INV-1501–1511",
        "Definition of done is demonstrated by integrated release evidence for G1-G10 (WP15); "
        "plan §9.1 currently lists only 'Plan §2, §2.3, §8'.",
    ),
}


def _key_of(first_cell: str) -> str:
    appendix = re.match(r"Appendix ([A-Z])", first_cell)
    if appendix:
        return f"Appendix {appendix.group(1)}"
    match = re.match(r"(§\d+(?:\.\d+)?)", first_cell)
    return match.group(1) if match else first_cell


def _fmt(tasks: List[str], decisions: List[str], extra: str = "") -> str:
    parts = sorted(set(tasks)) + sorted(set(decisions))
    text = ", ".join(parts)
    return f"{text}; {extra}" if extra and text else (text or extra)


def _esc(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def build() -> Tuple[str, List[str]]:
    plan_text = planlib.read(planlib.PLAN)
    spec_text = planlib.read(planlib.SPEC)
    tables = planlib.trace_tables(plan_text)
    gmap = planlib.gate_map(plan_text)
    known_tasks = set(planlib.all_task_ids(plan_text))
    unmapped: List[str] = []

    owners: Dict[str, Tuple[List[str], List[str], List[str]]] = {}
    labels: Dict[str, str] = {}
    for row in tables["spec"]:
        key = _key_of(row[0])
        owners[key] = planlib.expand_refs(row[1])
        labels[key] = row[1]

    def owners_for(key: str) -> Tuple[List[str], List[str], List[str]]:
        if key in SUPPLEMENTS:
            base = owners.get(key, ([], [], []))
            extra = planlib.expand_refs(SUPPLEMENTS[key][0])
            return base[0] + extra[0], base[1] + extra[1], base[2] + extra[2]
        if key in owners:
            return owners[key]
        if key == "preamble":
            return PREAMBLE_OWNERS
        tasks: Set[str] = set()
        decisions: Set[str] = set()
        wps: Set[str] = set()
        for other, (t, d, w) in owners.items():
            if other.startswith(key + "."):
                tasks.update(t)
                decisions.update(d)
                wps.update(w)
        return sorted(tasks), sorted(decisions), sorted(wps)

    for key, (tasks, _, _) in owners.items():
        for task in tasks:
            if task not in known_tasks:
                unmapped.append(f"{key}: references unknown task {task}")

    out: List[str] = [BEGIN, ""]
    spec_hash = hashlib.sha256(spec_text.encode("utf-8")).hexdigest()
    # Only the plan sections this generator reads, so checkbox ticks do not stale the trace.
    plan_parts = planlib.section(plan_text, "## 6. Gate ownership", "## 7.") + planlib.section(
        plan_text, "## 9. Requirement traceability", "## 10."
    )
    plan_hash = hashlib.sha256(plan_parts.encode("utf-8")).hexdigest()
    out.append(f"Generated from spec `{spec_hash[:16]}…` and plan §6+§9 `{plan_hash[:16]}…`. "
               "Verification column = design gates of the owning work packages (plan §6); the "
               "evidence itself is attached per release under INV-1501. `planned` means no "
               "evidence exists yet.")
    out.append("")

    out.append("### A. Specification sections")
    out.append("")
    out.append("| Spec section | Owning tasks / decisions (plan §9.1) | Planned verification | Evidence |")
    out.append("|---|---|---|---|")
    for row in tables["spec"]:
        tasks, decisions, wps = planlib.expand_refs(row[1])
        gates = planlib.gates_for(tasks, wps, gmap)
        verification = ", ".join(gates) if gates else ("decision record (DECISIONS.md)" if decisions else "plan structure (§2, §8)")
        out.append(f"| {_esc(row[0])} | {_esc(row[1])} | {verification} | planned |")
    out.append("")

    for title, name in (("B. Hidden-bench recommendations (plan §9.2)", "hb"), ("C. Timing recommendations (plan §9.3)", "timing")):
        out.append(f"### {title}")
        out.append("")
        out.append("| Recommendation | Owning tasks | Planned verification | Evidence |")
        out.append("|---|---|---|---|")
        for row in tables[name]:
            tasks, decisions, wps = planlib.expand_refs(row[1])
            gates = planlib.gates_for(tasks, wps, gmap)
            if "Deferred" in row[1]:
                verification = "deferred (plan §8); activation must be rejected"
            else:
                verification = ", ".join(gates) or ("decision record" if decisions else "")
            if not verification:
                unmapped.append(f"{name}: {row[0]} has no verification")
            for task in tasks:
                if task not in known_tasks:
                    unmapped.append(f"{name}: {row[0]} references unknown task {task}")
            out.append(f"| {_esc(row[0])} | {_esc(row[1])} | {verification} | planned |")
        out.append("")

    out.append("### D. Normative clauses (must / required / never)")
    out.append("")
    out.append("Clause IDs are positional (`CL-<section>-<n>`) and regenerate when the spec changes; "
               "cite the clause text, not only the ID, in evidence.")
    out.append("")
    out.append("| Clause | Spec section | Clause text | Owning tasks / decisions | Planned verification |")
    out.append("|---|---|---|---|---|")
    total = 0
    for sec in planlib.spec_sections(spec_text):
        units = planlib.clauses(sec.body)
        if not units:
            continue
        tasks, decisions, wps = owners_for(sec.key)
        gates = planlib.gates_for(tasks, wps, gmap)
        verification = ", ".join(gates) if gates else ("decision record (DECISIONS.md)" if decisions else "")
        if sec.key == "preamble":
            verification = "restated in §3.1/§11; tracked by INV-0002/INV-0007"
        owner_text = _fmt(tasks, decisions)
        slug = sec.key.replace("§", "S").replace("Appendix ", "App").replace(" ", "")
        for index, unit in enumerate(units, start=1):
            total += 1
            if not owner_text or not verification:
                unmapped.append(f"{sec.key} clause {index}: {unit[:80]}")
            out.append(f"| CL-{slug}-{index:02d} | {sec.key} | {_esc(unit)} | {owner_text or 'UNMAPPED'} | {verification or 'UNMAPPED'} |")
    out.append("")
    out.append(f"Normative clauses traced: {total}.")
    out.append("")
    out.append("### E. Unmapped (plan defects)")
    out.append("")
    out.extend([f"- {item}" for item in unmapped] or ["None."])
    out.append("")
    out.append("### F. Supplemental mappings (proposed plan §9.1 amendments)")
    out.append("")
    for key, (refs, reason) in SUPPLEMENTS.items():
        out.append(f"- {key} -> {refs}: {reason}")
    out.append("")
    out.append(END)
    return "\n".join(out), unmapped


def splice(existing: str, generated: str) -> str:
    if BEGIN in existing and END in existing:
        head = existing[: existing.index(BEGIN)]
        tail = existing[existing.index(END) + len(END) :]
        return head + generated + tail
    return existing.rstrip("\n") + "\n\n" + generated + "\n"


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    generated, unmapped = build()
    existing = TRACE.read_text(encoding="utf-8")
    updated = splice(existing, generated)
    if args.check:
        problems = []
        if updated != existing:
            problems.append("generated block is stale; re-run scripts/build_requirement_trace.py")
        if unmapped:
            problems.append(f"{len(unmapped)} unmapped item(s)")
        for problem in problems:
            print(f"FAIL: {problem}")
        if not problems:
            print("OK: requirement trace is current and fully mapped")
        return 1 if problems else 0
    TRACE.write_text(updated, encoding="utf-8")
    print(f"wrote {TRACE.relative_to(planlib.ROOT)} ({len(unmapped)} unmapped)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
