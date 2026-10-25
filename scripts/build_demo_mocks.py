#!/usr/bin/env python3
"""Stamp the per-variant playable mock walkthroughs (UX v6 follow-up).

    python3 scripts/build_demo_mocks.py          # write frontend/public/demos/*.html
    python3 scripts/build_demo_mocks.py --check  # fail if stale

Each variant is the same playable mock (``investigators_demo.html``)
with its ``/* @DEMO_CFG@ */`` line rewritten — solo review on/off, typed-chat vs
structured ask controls, AI vs scripted teammate replies, and check-in arm. The
admin test-links page links one file per mintable combo so a tester can walk the
screens before minting a live preview session. Every variant shows a ``skip →``
tester shortcut next to the task timer (mock-only, dashed outline) that runs the
same ``closeTask()`` path as the clock expiring — if the check-in overlay is open
it is resumed first — so testers can reach the post-task screens without waiting
out the five-minute budget.

Variants (names are the public URLs under ``/investigators/demos/``):

    nosolo_nl_*         — the fielded AI-teammate builds (tp_ai_v8):
                          typed chat only, model-written replies
    nosolo_nl_scripted_*— the fielded scripted-teammate builds
                          (tp_scripted_v10): typed chat + deterministic pool
    nosolo_structured_* — archived reference (structured asks + AI teammates)
    nosolo_non-nl_*     — archived reference (ask picker + deterministic pool)
    ovnone_*            — crate-condition experiment, no overlap: 12 crates,
                          disjoint four-crate assignments
    ovsome_*            — some overlap: 8 crates, partially overlapping
                          assignments
    ovfull_*            — full overlap: 4 crates, every seat works the same
                          assignment
    ovsome_thin_*       — some overlap + thin case file (reduced own intel)
    ovnone_thin_*       — no overlap + thin case file
    ovfull_thin_*       — full overlap + thin case file
    *_noassist_*        — each variant again with ``ai=false``: the
                          AI_ASSISTANT_ENABLED=0 surface (no assistant pane,
                          practice page, gate item or questionnaire items).
                          The Links page points at these while the deployment
                          flag is off.
    *_nopress_*         — each variant again with ``nopress=true``: the
                          TIME_PRESSURE_ENABLED=0 surface (no check-in
                          pop-up, pressure remarks, one-minute warning,
                          low-time styling or check-in questionnaire items;
                          the countdown stays a neutral timer). The Links
                          page points at these while the flag is off — and
                          since flag-off allocates the single ``nopress`` cell,
                          each nopress variant also ships an arm-neutral
                          ``investigators_<variant>_demo.html`` (no
                          ``_{high,low}`` segment) for the Links page.
    *_aiqa_*            — each variant again with ``aiqa=true``: the
                          AI_QA_ENABLED=1 surface (an optional rules-Q&A page
                          between the comprehension check and connecting).
                          The Links page points at these while the flag is on.

CSP note: Apache answers ``/investigators/`` with ``script-src 'self'``, which
blocks inline ``<script>`` — so each variant ships as a thin ``_demo.html``
shell plus an external ``_demo.js`` carrying the config and the stage machine.
The source ``investigators_demo.html`` keeps its inline script so
it still opens standalone from ``file://``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "demos" / "template" / "investigators_demo.html"
OUT_DIR = ROOT / "frontend" / "public" / "demos"
MARK = "/* @DEMO_CFG@ */"

VARIANTS: dict[str, dict[str, object]] = {
    "nosolo_nl": {
        "solo": False,
        "interaction": "nl",
        "scripted": False,
        "label": "no solo review · AI teammates · typed chat",
    },
    "nosolo_nl_scripted": {
        "solo": False,
        "interaction": "nl",
        "scripted": True,
        "label": "no solo review · scripted teammates · typed chat",
    },
    "nosolo_structured": {
        "solo": False,
        "interaction": "structured",
        "scripted": False,
        "label": "no solo review · AI teammates · structured asks",
    },
    "nosolo_non-nl": {
        "solo": False,
        "interaction": "structured",
        "scripted": True,
        "label": "no solo review · scripted teammates · ask picker",
    },
    # 2026-10-15 crate-condition experiment (CRATE_OVERLAP_ENABLED /
    # CRATE_NUMBER_ENABLED gated profiles): the overlap axis reshapes the mock
    # world — crate count, assignment, intel economy and condition copy. All
    # four are typed-chat AI-teammate builds like the fielded tp_ai family; the
    # Links page names them ``ov<level>[_thin]``.
    "ovnone": {
        "solo": False,
        "interaction": "nl",
        "scripted": False,
        "overlap": "none",
        "label": "no overlap · 12 crates · AI teammates · typed chat",
    },
    "ovsome": {
        "solo": False,
        "interaction": "nl",
        "scripted": False,
        "overlap": "some",
        "label": "some overlap · 8 crates · AI teammates · typed chat",
    },
    "ovfull": {
        "solo": False,
        "interaction": "nl",
        "scripted": False,
        "overlap": "full",
        "label": "full overlap · 4 crates · AI teammates · typed chat",
    },
    "ovsome_thin": {
        "solo": False,
        "interaction": "nl",
        "scripted": False,
        "overlap": "some",
        "thin": True,
        "label": "some overlap · 8 crates · thin case file · AI teammates · typed chat",
    },
    # Audit S02: the thin treatment applies at every overlap level — the
    # tp_ai_ovnone_thin/tp_ai_ovfull_thin profiles need previewable mocks too.
    "ovnone_thin": {
        "solo": False,
        "interaction": "nl",
        "scripted": False,
        "overlap": "none",
        "thin": True,
        "label": "no overlap · 12 crates · thin case file · AI teammates · typed chat",
    },
    "ovfull_thin": {
        "solo": False,
        "interaction": "nl",
        "scripted": False,
        "overlap": "full",
        "thin": True,
        "label": "full overlap · 4 crates · thin case file · AI teammates · typed chat",
    },
}

# AI_ASSISTANT_ENABLED=0 variants: same mock with ``ai=false`` — the template
# drops the assistant pane, its practice page, its comprehension item and the
# assistant questionnaire items under that flag. Named ``<variant>_noassist``
# to match the Links-page demo_url the backend emits while the flag is off.
for _v, _cfg in list(VARIANTS.items()):
    VARIANTS[f"{_v}_noassist"] = {
        **_cfg,
        "ai": False,
        "label": f"{_cfg['label']} · no AI assistant",
    }

# TIME_PRESSURE_ENABLED=0 variants: same mock with ``nopress=true`` — the
# template skips the check-in pop-up, pressure remarks, "One minute left"
# warning, low-time styling and the check-in questionnaire items, and the
# debrief drops the ratings disclosure. Named ``<variant>[_noassist]_nopress``
# to match the Links-page demo_url suffix order the backend emits (``_noassist``
# first, then ``_nopress``) while the flags are off.
for _v, _cfg in list(VARIANTS.items()):
    VARIANTS[f"{_v}_nopress"] = {
        **_cfg,
        "nopress": True,
    }

# AI_QA_ENABLED=1 variants: same mock with ``aiqa=true`` — the template
# inserts the optional rules-Q&A page between the comprehension check and
# connecting. Named ``<variant>[_noassist][_nopress]_aiqa`` to match the
# Links-page demo_url suffix order the backend emits while the flag is on.
for _v, _cfg in list(VARIANTS.items()):
    VARIANTS[f"{_v}_aiqa"] = {
        **_cfg,
        "aiqa": True,
        "label": f"{_cfg['label']} · AI Q&A",
    }

# BONUS_PAYMENT_ENABLED=0 variants: same mock with ``nobonus=true`` — the
# template drops every accuracy-bonus mention (consent compensation sentence,
# case-background clause, gameplay bonus line, debrief disclosure). Named
# ``<variant>[_noassist][_nopress][_aiqa]_nobonus`` to match the Links-page
# demo_url suffix order the backend emits while the flag is off.
for _v, _cfg in list(VARIANTS.items()):
    VARIANTS[f"{_v}_nobonus"] = {
        **_cfg,
        "nobonus": True,
        "label": f"{_cfg['label']} · no bonus",
    }

ARMS = ("high", "low", "mixed")  # v15: manifests may declare the mixed (high+low seats) arm


SCRIPT_RE = re.compile(r"<script>\n([\s\S]*?)</script>")


def completion_code() -> str:
    """Stamp an obvious placeholder completion code.

    The function does not read ``.env`` and does not embed the fielded handoff
    code.
    """
    return "PLACEHOLDER-CODE"


def render(template: str, variant: str, arm: str | None) -> tuple[str, str]:
    """Return (html, js) — the shell links the js; CSP forbids inline script.

    ``arm=None`` renders the arm-neutral file for a ``nopress`` variant: under
    TIME_PRESSURE_ENABLED=0 the study allocates the single ``nopress`` cell, so
    the Links page points at ``investigators_<variant>_demo.html`` with no
    high/low segment."""
    cfg = dict(VARIANTS[variant])
    cfg["pressure"] = arm or "high"
    cfg.setdefault("ai", True)
    if arm and not cfg.get("nopress"):
        cfg["label"] = f"{cfg['label']} · variant {arm}"
    cfg["code"] = completion_code()
    line = f"var DEMO_CFG = {json.dumps(cfg, ensure_ascii=False)}; {MARK}"

    m = SCRIPT_RE.search(template)
    if not m:
        raise SystemExit("template is missing its inline <script> block")
    script, n = re.subn(
        r"var DEMO_CFG = .*? /\* @DEMO_CFG@ \*/", lambda _: line, m.group(1), count=1
    )
    if n != 1:
        raise SystemExit("template is missing the @DEMO_CFG@ line")

    stem = f"investigators_{variant}_{arm}_demo" if arm else f"investigators_{variant}_demo"
    html = template[: m.start()] + f'<script src="{stem}.js"></script>' + template[m.end() :]
    return html, script


def build() -> dict[str, str]:
    template = TEMPLATE.read_text(encoding="utf-8")
    files: dict[str, str] = {}
    for v in VARIANTS:
        for a in ARMS:
            html, js = render(template, v, a)
            stem = f"investigators_{v}_{a}_demo"
            files[f"{stem}.html"] = html
            files[f"{stem}.js"] = js
        if VARIANTS[v].get("nopress"):
            html, js = render(template, v, None)
            stem = f"investigators_{v}_demo"
            files[f"{stem}.html"] = html
            files[f"{stem}.js"] = js
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    files = build()
    if args.check:
        stale = [
            name
            for name, body in files.items()
            if not (OUT_DIR / name).exists() or (OUT_DIR / name).read_text() != body
        ]
        stale += [
            p.name
            for p in OUT_DIR.glob("investigators_*_demo.*")
            if p.name not in files
        ] if OUT_DIR.is_dir() else []
        if stale:
            print(f"FAIL: stale demo mocks: {stale} — run scripts/build_demo_mocks.py")
            return 1
        print(f"OK: {len(files)} demo mocks current")
        return 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for p in OUT_DIR.glob("investigators_*_demo.*"):
        if p.name not in files:
            p.unlink()
            print(f"removed stale {p.name}")
    for name, body in files.items():
        (OUT_DIR / name).write_text(body, encoding="utf-8")
        print(f"wrote {OUT_DIR / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
