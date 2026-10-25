"""Docstring contract (audit M5): every function longer than 60 lines in the
services, measurement and export layers — where eligibility, scoring, allocation
and payment live — must say what it does."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend" / "app"
LAYERS = ("services", "measurement", "export")
MAX_UNDOCUMENTED_LINES = 60


def test_long_functions_are_documented() -> None:
    missing = []
    for layer in LAYERS:
        for path in sorted((BACKEND / layer).rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                length = (node.end_lineno or node.lineno) - node.lineno + 1
                if length > MAX_UNDOCUMENTED_LINES and not ast.get_docstring(node):
                    missing.append(f"{path.relative_to(BACKEND)}:{node.lineno} {node.name} ({length} lines)")
    assert not missing, "undocumented long functions:\n" + "\n".join(missing)
