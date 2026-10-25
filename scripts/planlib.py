"""Parsing helpers for the pinned plan and specification (used by the trace/ledger generators
and the repository contract tests). Runs on the host's Python 3.9."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "IMPLEMENTATION_PLAN.md"
SPEC = ROOT / "docs" / "DESIGN_SPEC.md"

_DASH = "[\u2013\u2014-]"


@dataclass
class Task:
    task_id: str
    wp: str
    text: str
    checked: bool


@dataclass
class WorkPackage:
    wp: str
    title: str
    owner: str
    dependencies: str
    tasks: List[Task] = field(default_factory=list)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def section(text: str, start_heading: str, end_heading: Optional[str]) -> str:
    start = text.index(start_heading)
    end = text.index(end_heading, start) if end_heading else len(text)
    return text[start:end]


def table_rows(block: str) -> List[List[str]]:
    rows = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|") or re.match(r"^\|[\s:|-]+\|$", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append(cells)
    return rows[1:] if rows else rows  # drop header


def work_packages(plan_text: Optional[str] = None) -> List[WorkPackage]:
    text = plan_text or read(PLAN)
    body = section(text, "## 5. Work packages", "## 6. Gate ownership")
    packages: List[WorkPackage] = []
    for chunk in re.split(r"(?m)^### ", body)[1:]:
        header = chunk.splitlines()[0]
        match = re.match(rf"(WP\d{{2}}[ab]?)\s*{_DASH}\s*(.+)", header)
        if not match:
            continue
        owner = re.search(r"\*\*Owner:\*\*\s*(.+?)\s*\*\*Dependencies:\*\*", chunk, re.S)
        deps = re.search(r"\*\*Dependencies:\*\*\s*(.+?)(?:\s*\*\*Design:\*\*|\n)", chunk, re.S)
        wp = WorkPackage(
            wp=match.group(1),
            title=match.group(2).strip(),
            owner=(owner.group(1).strip().rstrip(".") if owner else ""),
            dependencies=(deps.group(1).strip().rstrip(".") if deps else ""),
        )
        for task in re.finditer(r"(?m)^- \[( |x)\] \*\*(INV-\d{4})\*\*\s*(.+)$", chunk):
            wp.tasks.append(Task(task.group(2), wp.wp, task.group(3).strip(), task.group(1) == "x"))
        packages.append(wp)
    return packages


def all_task_ids(plan_text: Optional[str] = None) -> List[str]:
    return [t.task_id for wp in work_packages(plan_text) for t in wp.tasks]


def expand_refs(cell: str) -> Tuple[List[str], List[str], List[str]]:
    """Expand an owner cell like 'INV-0003, 0203, 0501-0506; D04' into (tasks, decisions, wps)."""
    tasks: List[str] = []
    decisions: List[str] = []
    wps: List[str] = []
    token_re = re.compile(
        rf"INV-\d{{4}}(?:{_DASH}\d{{4}})?|\b\d{{4}}(?:{_DASH}\d{{4}})?\b|D\d{{2}}(?:{_DASH}D?\d{{2}})?|WP\d{{2}}[ab]?"
    )
    for token in token_re.findall(cell):
        if token.startswith("WP"):
            wps.append(token)
            continue
        if token.startswith("D"):
            parts = re.split(_DASH, token)
            lo = int(parts[0][1:])
            hi = int(parts[-1].lstrip("D")) if len(parts) > 1 else lo
            decisions.extend(f"D{n:02d}" for n in range(lo, hi + 1))
            continue
        digits = re.split(_DASH, token.replace("INV-", ""))
        lo = int(digits[0])
        hi = int(digits[-1]) if len(digits) > 1 else lo
        tasks.extend(f"INV-{n:04d}" for n in range(lo, hi + 1))
    return tasks, decisions, wps


def trace_tables(plan_text: Optional[str] = None) -> Dict[str, List[List[str]]]:
    text = plan_text or read(PLAN)
    return {
        "spec": table_rows(section(text, "### 9.1", "### 9.2")),
        "hb": table_rows(section(text, "### 9.2", "### 9.3")),
        "timing": table_rows(section(text, "### 9.3", "## 10.")),
    }


def gate_map(plan_text: Optional[str] = None) -> Dict[str, List[str]]:
    """WP id -> gates, from plan §6."""
    text = plan_text or read(PLAN)
    mapping: Dict[str, List[str]] = {}
    for row in table_rows(section(text, "## 6. Gate ownership", "## 7.")):
        gate_cell, packages = row[0], row[1]
        gate = re.match(r"(G\d+|Semantic-use gate)", gate_cell)
        if not gate:
            continue
        label = "SEM" if gate.group(1).startswith("Semantic") else gate.group(1)
        for wp in re.findall(r"WP\d{2}[ab]?", packages):
            mapping.setdefault(wp, [])
            if label not in mapping[wp]:
                mapping[wp].append(label)
    return mapping


def wp_of(task_id: str) -> str:
    number = int(task_id[4:6])
    return f"WP{number:02d}"


def gates_for(tasks: List[str], wps: List[str], gmap: Dict[str, List[str]]) -> List[str]:
    found: Set[str] = set()
    for wp in wps + [wp_of(t) for t in tasks]:
        candidates = [wp] if wp in gmap else [k for k in gmap if k.startswith(wp)]
        for key in candidates:
            found.update(gmap.get(key, []))
    order = [f"G{n}" for n in range(1, 11)] + ["SEM"]
    return [g for g in order if g in found]


@dataclass
class SpecSection:
    key: str  # e.g. "§6.1", "§6", "Appendix A", "preamble"
    heading: str
    body: str


def spec_sections(spec_text: Optional[str] = None) -> List[SpecSection]:
    text = spec_text or read(SPEC)
    sections: List[SpecSection] = []
    current_key, current_heading, buffer = "preamble", "Preamble (status, revisions, owner decisions)", []
    for line in text.splitlines():
        heading = re.match(r"^(#{2,3})\s+(.+)$", line)
        if heading:
            sections.append(SpecSection(current_key, current_heading, "\n".join(buffer)))
            title = heading.group(2).strip()
            numbered = re.match(r"^(\d+(?:\.\d+)?)\.?\s", title)
            appendix = re.match(r"^Appendix ([A-Z])", title)
            if numbered:
                current_key = f"§{numbered.group(1)}"
            elif appendix:
                current_key = f"Appendix {appendix.group(1)}"
            else:
                current_key = title
            current_heading, buffer = title, []
            continue
        if line.startswith("# "):
            continue
        buffer.append(line)
    sections.append(SpecSection(current_key, current_heading, "\n".join(buffer)))
    return sections


_CLAUSE_WORDS = re.compile(r"\b(must|required|never)\b", re.I)


def clauses(body: str) -> List[str]:
    """Normative units containing must/required/never: table rows, bullets, or sentences."""
    units: List[str] = []
    in_code = False
    paragraph: List[str] = []

    def flush() -> None:
        if paragraph:
            text = " ".join(paragraph).strip()
            for sentence in re.split(r"(?<=[.;])\s+(?=[A-Z*`(\"])", text):
                if _CLAUSE_WORDS.search(sentence):
                    units.append(sentence.strip())
            paragraph.clear()

    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            flush()
            in_code = not in_code
            continue
        if in_code:
            continue
        if not line:
            flush()
            continue
        if line.startswith("|"):
            flush()
            if re.match(r"^\|[\s:|-]+\|$", line):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            text = " — ".join(c for c in cells if c)
            if _CLAUSE_WORDS.search(text):
                units.append(text)
            continue
        if re.match(r"^([-*]|\d+\.)\s", line):
            flush()
            paragraph.append(re.sub(r"^([-*]|\d+\.)\s+", "", line))
            flush()
            continue
        paragraph.append(line)
    flush()
    return units
