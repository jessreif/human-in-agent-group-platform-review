"""Comprehension-check contract: the two-language item list must not drift.

The comprehension gate (2026-10-10) defines its canonical items in
``app.services.lifecycle.COMPREHENSION_ITEMS`` (id + verbatim text + answer
key); the participant UI mirrors the ids and texts in
``frontend/src/components/StageScreens.tsx`` ``COMPREHENSION_ITEMS`` and the
analysis layer registers one first-attempt-miss scalar per item. This test is
the only thing connecting the three surfaces — it fails loudly on drift
rather than letting the survey documentation publish stale wording.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

from app.services.dashboard_analysis import _SCALARS  # noqa: E402
from app.services.lifecycle import COMPREHENSION_ITEMS, COMPREHENSION_KEY  # noqa: E402


def _frontend_items() -> dict[str, str]:
    tsx = (ROOT / "frontend/src/components/StageScreens.tsx").read_text(encoding="utf-8")
    m = re.search(r"COMPREHENSION_ITEMS[^=]*=\s*\[(.*?)\];", tsx, re.S)
    assert m, "COMPREHENSION_ITEMS array not found in StageScreens.tsx"
    return dict(
        re.findall(r'id:\s*"(\w+)",\s*text:\s*"((?:[^"\\]|\\.)*)"', m.group(1))
    )


def test_every_backend_item_is_mirrored_verbatim_in_the_frontend() -> None:
    frontend = _frontend_items()
    for item in COMPREHENSION_ITEMS:
        iid = item["item_id"]
        assert iid in frontend, f"{iid} missing from StageScreens.tsx"
        assert frontend[iid] == item["text"], f"{iid} text drifted"
        assert COMPREHENSION_KEY[iid] is item["correct"]


def test_frontend_has_no_extra_comprehension_items() -> None:
    frontend = _frontend_items()
    backend_ids = {i["item_id"] for i in COMPREHENSION_ITEMS}
    extra = set(frontend) - backend_ids
    assert not extra, f"frontend-only comprehension items: {sorted(extra)}"


def test_every_item_has_a_first_miss_scalar() -> None:
    for item in COMPREHENSION_ITEMS:
        key = f"training.comprehension_miss_{item['item_id']}"
        assert key in _SCALARS, f"{key} missing from the scalar registry"
        assert _SCALARS[key]["unit"] == "share"


def _demo_items() -> dict[str, tuple[str, bool]]:
    """The playable mock's ``COMP_ITEMS`` (audit M4, 2026-10-17). ``want`` is a
    literal, or ``OVERLAP === "full"`` for ``same_crates`` — evaluated here at the
    default (independent) overlap, which is how the backend key is defined."""
    html = (ROOT / "demos" / "template" / "investigators_demo.html").read_text(encoding="utf-8")
    m = re.search(r"var COMP_ITEMS\s*=\s*\[(.*?)\]\.filter", html, re.S)
    assert m, "COMP_ITEMS array not found in investigators_demo.html"
    out: dict[str, tuple[str, bool]] = {}
    for iid, text, want in re.findall(
        r'\{id:\s*"(\w+)",\s*text:\s*"((?:[^"\\]|\\.)*)",\s*want:\s*([^}]+?)\s*\}', m.group(1)
    ):
        out[iid] = (text, want == "true")
    return out


def test_demo_mock_mirrors_backend_comprehension_items() -> None:
    """The mock is the owner's pre-test sniff surface (UI playbook P9): a drifted
    item list would misrepresent the fielded gate."""
    demo = _demo_items()
    backend = {i["item_id"]: i for i in COMPREHENSION_ITEMS}
    assert set(demo) == set(backend), (sorted(set(demo) ^ set(backend)))
    for iid, (text, want) in demo.items():
        assert text == backend[iid]["text"], f"{iid} text drifted in the demo"
        assert want is COMPREHENSION_KEY[iid], f"{iid} answer key drifted in the demo"
