"""Offline regressions for smoke evidence and the single-cell analysis gate."""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]


def _load(name: str, relative: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("budget", [None, float("nan"), 20])
def test_analysis_single_cell_normalizes_missing_budget(budget: float | None) -> None:
    conditions = _load("audit_conditions", "scripts/power_analysis/conditions.py")
    rows = [{"crate_overlap": "some", "n_crates": 8, "intel_budget": budget}]
    expected = 20 if budget == 20 else None
    assert conditions.require_single_condition(rows) == [("some", 8, expected)]


def test_analysis_refuses_mixed_nullable_cells_even_with_condition_spec() -> None:
    conditions = _load("audit_conditions", "scripts/power_analysis/conditions.py")
    rows = [
        {"crate_overlap": "some", "n_crates": 8, "intel_budget": budget} for budget in (20, None)
    ]
    # Execute the actual script's gate without importing its statistical stack
    # or opening researcher exports. The declared spec must not bypass it.
    path = ROOT / "scripts/power_analysis/analyze.py"
    guard = next(
        node
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.Try)
        and any(
            isinstance(child, ast.Name) and child.id == "require_single_condition"
            for child in ast.walk(node)
        )
    )
    namespace = {
        "require_single_condition": conditions.require_single_condition,
        "P": SimpleNamespace(to_dict=lambda orient: rows),
        "cfg": {"condition_spec": {"cells": rows}},
        "sys": sys,
    }
    with pytest.raises(SystemExit, match="condition-aware models are not implemented"):
        exec(  # noqa: S102 - only the gate from this repository's trusted script
            compile(ast.Module(body=[guard], type_ignores=[]), str(path), "exec"), namespace
        )


@pytest.mark.parametrize(
    "failure_phase", ["open", "entry", "close", "close_once", "confirmation", None]
)
def test_smoke_always_reports_setup_and_close_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure_phase: str | None
) -> None:
    smoke = _load("audit_smoke", "scripts/qa_smoke_aiqa_gate.py")
    close_attempts: list[bool] = []
    stages = iter(["training", "scripted_connecting", "ready", "task"])

    class Response:
        def __init__(self, data: dict[str, Any], status: int = 200) -> None:
            self.data, self.status_code, self.text = data, status, json.dumps(data)
            self.headers = smoke.httpx.Headers()
            self.content = self.text.encode()

        def json(self) -> dict[str, Any]:
            return self.data

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

    class Client:
        def __init__(self) -> None:
            self.cookies = {"investigators_admin_csrf": "synthetic"}

        def get(self, path: str, **kwargs: Any) -> Response:
            if path == "/admin/preview-combos":
                return Response({"ai_qa_enabled": False})
            if path == "/workbench":
                return Response({"practice": {"records": [{"record_id": "synthetic-record"}]}})
            stage = next(stages)
            return Response({"session": {"stage": stage, "revision": 1}})

        def post(self, path: str, **kwargs: Any) -> Response:
            if path.endswith("/enrollment"):
                opened = kwargs["json"]["open"]
                if not opened:
                    close_attempts.append(False)
                fails = (opened and failure_phase == "open") or (
                    not opened
                    and (
                        failure_phase == "close"
                        or (failure_phase == "close_once" and len(close_attempts) == 1)
                    )
                )
                state = True if not opened and failure_phase == "confirmation" else opened
                return Response({"enrollment_open": state}, 500 if fails else 200)
            if path == "/entry":
                return Response(
                    {"session_id": "synthetic-smoke"}, 500 if failure_phase == "entry" else 200
                )
            return Response({"accepted": True})

    monkeypatch.setattr(smoke, "OUT", tmp_path)
    monkeypatch.setattr(smoke, "_admin_password", lambda: "synthetic")
    monkeypatch.setattr(smoke.httpx, "Client", lambda **kwargs: Client())
    monkeypatch.setattr(smoke.time, "sleep", lambda seconds: None)
    result = smoke.main()
    evidence = json.loads(next(tmp_path.glob("qa-smoke-aiqa-gate-*.json")).read_text())
    succeeds = failure_phase in (None, "close_once")
    assert result == (0 if succeeds else 1)
    assert evidence["qa_outcome"] is None
    assert bool(evidence["failure"]) is not succeeds
    close_fails = failure_phase in ("close", "confirmation")
    assert evidence["enrollment_closed"] is not close_fails
    assert len(close_attempts) == (3 if close_fails else 2 if failure_phase == "close_once" else 1)
