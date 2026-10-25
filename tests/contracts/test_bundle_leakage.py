"""INV-0107: the participant-bundle leakage scanner catches every rule and passes clean bundles."""

from __future__ import annotations

from pathlib import Path

import check_bundle_leakage as scanner
import pytest

ROOT = Path(__file__).resolve().parents[2]

POSITIVE = {
    "demo relay host": 'fetch("https://qualtrics-relay.example.com/chatgpt")',
    "onrender host": "https://relay.onrender.com/x",
    "chatgpt endpoint": 'post("/chatgpt")',
    "openai api": "https://api.openai.com/v1",
    "truth key": '{"truth":"The Marina"}',
    "truth member": "if(a===c.truth)",
    "needsTeam": "needsTeam:!0",
    "roleOf": "var roleOf={}",
    "ALL_CLUES": "ALL_CLUES[id]",
    "WORLD global": "var WORLD=buildWorld()",
    "answer key": "answerKey",
    "scoresheet": "inv_scoresheet",
    "INV_CONDITION": "window.INV_CONDITION",
    "DEMO_CONDITION": "demoCondition",
    "PRESSURE_CONFIG": "PRESSURE_CONFIG",
    "EVAL_CONFIG": "EVAL_CONFIG",
    "cond query": "location.search='?cond=low'",
    "condition key": '{"condition":"x"}',
    "arm key": "{arm:'control'}",
    "demo restart": "demoRestart",
    "model family": "gemma3:27b",
    "model runtime": "ollama",
    "provider double": "double:deterministic",
    "live flags": "TEAMMATE_LIVE",
    "agent prompt": "teammateSystem(seat)",
    "automation disclosure": "They were automated accounts",
}
CLEAN = (
    'function App(){const[s,t]=useState("checking");return jsx("main",{className:"panel",'
    'children:"This study is not open"})}fetch("/investigators/api/health");'
    "var truthy=!0;const conditions=[];export{App};"
)


def test_every_rule_has_a_positive_fixture() -> None:
    names = {name for rules in scanner.RULES.values() for name, _ in rules}
    assert names == set(POSITIVE)


@pytest.mark.parametrize("rule", sorted(POSITIVE))
def test_rule_detects_its_fixture(rule: str) -> None:
    findings = scanner.scan_text(POSITIVE[rule], "fixture.js")
    assert rule in {f.rule for f in findings}


def test_clean_code_passes() -> None:
    assert scanner.scan_text(CLEAN, "clean.js") == []


def test_directory_scan(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    (tmp_path / "assets" / "a.js").write_text(CLEAN, encoding="utf-8")
    assert scanner.scan_directory(tmp_path) == []
    (tmp_path / "assets" / "b.js").write_text(POSITIVE["truth key"], encoding="utf-8")
    assert [f.rule for f in scanner.scan_directory(tmp_path)] == ["truth key"]


def test_missing_bundle_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scanner.scan_directory(tmp_path / "absent")


def test_built_frontend_bundle_is_clean() -> None:
    dist = ROOT / "frontend" / "dist"
    if not dist.is_dir():
        pytest.skip("frontend/dist not built (scripts/check.sh builds it first)")
    assert scanner.scan_directory(dist) == []
