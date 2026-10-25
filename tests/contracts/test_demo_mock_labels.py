"""Mock header wording is presentation only; feature configs and URLs stay stable."""

from __future__ import annotations

import json
import re
from pathlib import Path

import build_demo_mocks as mocks

ROOT = Path(__file__).resolve().parents[2]


def test_all_playable_mock_labels_are_neutral(monkeypatch) -> None:
    monkeypatch.setattr(mocks, "completion_code", lambda: "MOCK-CODE")
    template = mocks.TEMPLATE.read_text(encoding="utf-8")
    for variant, settings in mocks.VARIANTS.items():
        arms = (*mocks.ARMS, None) if settings.get("nopress") else mocks.ARMS
        for arm in arms:
            html, script = mocks.render(template, variant, arm)
            match = re.search(r"var DEMO_CFG = (.*?); /\* @DEMO_CFG@ \*/", script)
            assert match is not None
            cfg = json.loads(match.group(1))
            assert "pressure" not in cfg["label"].lower(), (variant, arm)
            assert " arm" not in cfg["label"].lower(), (variant, arm)
            if settings.get("nopress"):
                assert cfg["label"] == settings["label"]
                assert cfg["nopress"] is True
            else:
                assert cfg["label"].endswith(f"variant {arm}")
            # Header cleanup must not change the simulated treatment or feature gates.
            assert cfg["pressure"] == (arm or "high")
            for key, value in settings.items():
                if key != "label":
                    assert cfg[key] == value
            stem = f"investigators_{variant}_{arm}_demo" if arm else f"investigators_{variant}_demo"
            assert f'<script src="{stem}.js"></script>' in html


def test_standalone_mock_header_uses_neutral_fallback() -> None:
    template = (ROOT / "demos" / "template" / "investigators_demo.html").read_text(encoding="utf-8")
    assert 'esc(DEMO_CFG.label || "Investigators task")' in template
    assert 'esc(DEMO_CFG.label || "time-pressure demo")' not in template
