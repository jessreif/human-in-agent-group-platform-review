"""Participant-facing source scope (2026-10-16 scoped-digest work).

``scripts/source_bundle.py --behavior`` produces ``INVESTIGATORS_BEHAVIOR_SOURCE_SHA256`` —
the digest the compatibility key pins. These tests pin the contract:

- the OPS_PATHS denylist is a *closed* classification: every file it excludes must exist and
  every excluded file sits under a declared ops prefix;
- participant-facing (behavior-scope) modules never import an ops-scoped module, except the
  two documented composition-root edges in ``app.main`` (the single process mounts both the
  participant and admin routers). A new edge requires editing this test — that is the
  review gate.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent


def _source_bundle():
    spec = importlib.util.spec_from_file_location(
        "source_bundle", REPO / "scripts" / "source_bundle.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ops_modules(sb) -> set[str]:
    mods = set()
    for prefix in sb.OPS_PATHS:
        if prefix.startswith("backend/app/") and prefix.endswith(".py"):
            mods.add("app." + prefix[len("backend/app/") : -3].replace("/", "."))
    return mods


def test_every_ops_path_exists() -> None:
    """A renamed/deleted ops entry must fail loudly — a stale prefix would silently
    stop exempting the file (fail closed) but a stale test never alerts anyone."""
    sb = _source_bundle()
    for prefix in sb.OPS_PATHS:
        assert (REPO / prefix).exists(), f"OPS_PATHS entry matches nothing: {prefix}"


def test_ops_exclusion_is_exactly_the_denylist() -> None:
    """The behavior manifest differs from the full manifest exactly at declared ops
    prefixes/suffixes — no accidental drift, no overreach."""
    sb = _source_bundle()
    full = {p.relative_to(REPO).as_posix() for p in sb.iter_files()}
    behavior = {p.relative_to(REPO).as_posix() for p in sb.iter_files(behavior_only=True)}
    excluded = full - behavior
    assert excluded, "--behavior must exclude something"
    for rel in excluded:
        assert rel.startswith(sb.OPS_PATHS) or Path(rel).name.endswith(sb.OPS_NAME_SUFFIXES), (
            f"excluded file is not under a declared ops path: {rel}"
        )
    # The headline participant-facing modules are all in scope.
    for rel in (
        "backend/app/api/participant.py",
        "backend/app/services/lifecycle.py",
        "backend/app/services/scheduler.py",
        "backend/app/services/entry.py",
        "backend/app/services/allocation.py",
        "backend/app/services/prompts.py",
        "backend/app/measurement/scoring.py",
        "backend/app/db/schema.py",
        "backend/app/db/export.py",
        "backend/app/export/aggregates.py",
        "frontend/src/App.tsx",
        "docker-compose.yml",
    ):
        assert rel in behavior, f"participant-facing file missing from scope: {rel}"


def test_behavior_modules_never_import_ops_modules() -> None:
    """Ops code is mounted in the same process but must not be behavior-bearing:
    behavior files may never import an ops module. The only permitted edges are
    the composition-root router mounts in ``app.main``."""
    sb = _source_bundle()
    ops = _ops_modules(sb)
    assert "app.api.admin" in ops
    known_edges = {
        ("app.main", "app.api.admin"),
        ("app.main", "app.api.health"),
    }
    found: set[tuple[str, str]] = set()
    for path in sb.iter_files(behavior_only=True):
        rel = path.relative_to(REPO).as_posix()
        if not (rel.startswith("backend/app/") and rel.endswith(".py")):
            continue
        source_mod = "app." + rel[len("backend/app/") : -3].replace("/", ".")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module in ("app.services", "app.api", "app.export", "app.db"):
                    targets = [f"{node.module}.{a.name}" for a in node.names]
                else:
                    targets = [node.module]
            found.update((source_mod, target) for target in targets if target in ops)
    assert found == known_edges
