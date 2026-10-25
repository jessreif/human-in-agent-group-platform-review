"""INV-0101: declared module layout exists and nothing imports another live app's modules."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

APP = Path(__file__).parents[1] / "app"
DECLARED = ("api", "domain", "services", "timing", "workers", "providers", "measurement", "db")
ALLOWED_THIRD_PARTY = {
    "fastapi",
    "starlette",
    "pydantic",
    "pydantic_settings",
    "sqlalchemy",
    "alembic",
    "prometheus_client",
    "uvicorn",
    "psycopg",
    # Event-stream reads and the poll sleep run on AnyIO's worker threads
    # (abandon-on-cancel). AnyIO is Starlette's runtime, already in uv.lock.
    "anyio",
}


def _modules() -> list[Path]:
    return sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)


def test_declared_packages_exist() -> None:
    for name in DECLARED:
        assert (APP / name / "__init__.py").is_file(), name


def test_single_factory_and_separate_worker_entry_points() -> None:
    factories = [p for p in _modules() if "def create_app(" in p.read_text(encoding="utf-8")]
    assert [p.relative_to(APP).as_posix() for p in factories] == ["main.py"]
    assert "def main(" in (APP / "workers" / "main.py").read_text(encoding="utf-8")


def test_imports_are_stdlib_approved_dependencies_or_app() -> None:
    offenders = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                if top == "app" or top in sys.stdlib_module_names or top in ALLOWED_THIRD_PARTY:
                    continue
                offenders.append(f"{path.relative_to(APP)}: {name}")
    assert offenders == []


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def test_no_sys_path_manipulation_or_estate_paths() -> None:
    """Code (not documentation) must not reach into the estate filesystem or sys.path."""
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "path":
                assert not (isinstance(node.value, ast.Name) and node.value.id == "sys"), path
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings:
                    continue
                assert "study_admin_common" not in node.value, path
                assert "/opt/" not in node.value, path
