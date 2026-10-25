#!/usr/bin/env python3
"""Participant-bundle leakage scan (INV-0107, spec §2.3 and §8.3).

Fails if a built participant bundle contains any of:
- the demo relay host or any external model endpoint,
- truth / answer-key / world-internals fields,
- condition or arm selectors and preview-only demo controls,
- agent implementation labels (model names, provider/double identifiers, live-agent flags).

    python3 scripts/check_bundle_leakage.py [DIST_DIR]   (default: frontend/dist)

Patterns are deliberately specific so that library code does not trip them; every pattern has
a positive fixture in tests/contracts/test_bundle_leakage.py. Runs on the host's Python 3.9.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Pattern, Tuple

ROOT = Path(__file__).resolve().parents[1]
SCANNED_SUFFIXES = {".js", ".mjs", ".css", ".html", ".json", ".map", ".txt", ".svg"}

RULES: Dict[str, List[Tuple[str, Pattern[str]]]] = {
    "external_relay": [
        ("demo relay host", re.compile(r"qualtrics-relay\.example\.com", re.I)),
        ("onrender host", re.compile(r"\.onrender\.com", re.I)),
        ("chatgpt endpoint", re.compile(r"/chatgpt\b", re.I)),
        ("openai api", re.compile(r"api\.openai\.com", re.I)),
    ],
    "truth_fields": [
        ("truth key", re.compile(r"""["']?\btruth\b["']?\s*:""")),
        ("truth member", re.compile(r"\.\s*truth\b")),
        ("needsTeam", re.compile(r"\bneedsTeam\b")),
        ("roleOf", re.compile(r"\broleOf\b")),
        ("ALL_CLUES", re.compile(r"\bALL_CLUES\b")),
        ("WORLD global", re.compile(r"\bWORLD\s*[=.\[]")),
        ("answer key", re.compile(r"\b(answer_key|answerKey|correct_answer|hideout_truth)\b")),
        ("scoresheet", re.compile(r"\binv_scoresheet\b")),
    ],
    "condition_selectors": [
        ("INV_CONDITION", re.compile(r"\bINV_CONDITION\b")),
        ("DEMO_CONDITION", re.compile(r"\bDEMO_CONDITION\b|\bdemoCondition\b")),
        ("PRESSURE_CONFIG", re.compile(r"\bPRESSURE_CONFIG\b")),
        ("EVAL_CONFIG", re.compile(r"\bEVAL_CONFIG\b")),
        ("cond query", re.compile(r"[?&]cond=")),
        ("condition key", re.compile(r"""["']condition["']\s*:|\bcondition\s*:\s*["'](high|low|eval|control)["']""")),
        ("arm key", re.compile(r"""["']?\barm["']?\s*:\s*["'](high|low|eval|control)["']""")),
        ("demo restart", re.compile(r"\bdemoRestart\b")),
    ],
    "agent_labels": [
        ("model family", re.compile(r"\b(gemma\d*|llama\d*|qwen\d*|mistral|gpt-[0-9a-z.-]+|claude)\b", re.I)),
        ("model runtime", re.compile(r"\b(ollama|openai|anthropic|vllm)\b", re.I)),
        ("provider double", re.compile(r"double:(deterministic|scripted|failing|delayed)")),
        ("live flags", re.compile(r"\b(TEAMMATE_LIVE|AI_LIVE|AI_ENDPOINT|AI_SYSTEM)\b")),
        ("agent prompt", re.compile(r"\bteammateSystem\b|\bteammate_system\b")),
        ("automation disclosure", re.compile(r"automated account", re.I)),
    ],
}


class Finding(NamedTuple):
    path: str
    category: str
    rule: str
    excerpt: str


def scan_text(text: str, label: str) -> List[Finding]:
    findings = []
    for category, rules in RULES.items():
        for name, pattern in rules:
            for match in pattern.finditer(text):
                start = max(0, match.start() - 30)
                excerpt = text[start : match.end() + 30].replace("\n", " ")
                findings.append(Finding(label, category, name, excerpt))
    return findings


def scan_directory(dist: Path) -> List[Finding]:
    if not dist.is_dir():
        raise FileNotFoundError(f"bundle directory not found: {dist}")
    findings: List[Finding] = []
    files = [p for p in sorted(dist.rglob("*")) if p.is_file() and p.suffix in SCANNED_SUFFIXES]
    if not files:
        raise FileNotFoundError(f"no scannable files in {dist}")
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        findings.extend(scan_text(text, path.relative_to(dist).as_posix()))
    return findings


def main(argv: List[str]) -> int:
    dist = Path(argv[0]) if argv else ROOT / "frontend" / "dist"
    findings = scan_directory(dist)
    count = len([p for p in dist.rglob("*") if p.is_file()])
    if findings:
        for finding in findings:
            print(f"LEAK {finding.category}/{finding.rule} in {finding.path}: ...{finding.excerpt}...")
        print(f"FAIL: {len(findings)} leakage finding(s) in {dist}")
        return 1
    print(f"PASS: no leakage findings in {count} file(s) under {dist}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
