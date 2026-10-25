"""Helpers shared by the WP02 contract tests (not a test module)."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.domain.manifests.binding import BoundManifest, bind
from app.domain.manifests.publication import publish, write_index
from app.domain.manifests.store import PUBLISHED_INDEX, ContentStore
from app.timing.policy import ResolvedPolicy, resolve_policy

REPO = Path(__file__).resolve().parents[2]
CONTENT = REPO / "content"
FIXTURES = REPO / "tests" / "fixtures" / "reference"
PROFILES = (
    "tp_demo_v1",
    "pe_demo_v1",
    "tp_review_first_v2",
    "pe_pilot_v2",
    # BA-37 consent.reference@v2 rebinds.
    "tp_demo_v2",
    "pe_demo_v2",
    "tp_review_first_v3",
    "pe_pilot_v3",
    # BA-37 correction: consent.reference@v3 fixes '#XXXX-XXXX' transcription.
    "tp_demo_v3",
    "pe_demo_v3",
    "tp_review_first_v4",
    "pe_pilot_v4",
)
SEED = "5eed" * 16
OTHER_SEED = "0ddba11" + "0" * 57


@lru_cache(maxsize=1)
def store() -> ContentStore:
    return ContentStore(CONTENT)


def bound(manifest_id: str, **kwargs: Any) -> BoundManifest:
    kwargs.setdefault("mode", "preview")
    return bind(store(), manifest_id, **kwargs)


def policy(manifest_id: str, arm: str) -> ResolvedPolicy:
    return resolve_policy(bound(manifest_id), arm)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def fixture(name: str) -> Any:
    return load_json(FIXTURES / name)


def profile_data(manifest_id: str) -> dict[str, Any]:
    data: dict[str, Any] = load_json(CONTENT / "profiles" / f"{manifest_id}.json")
    return data


def copy_content(tmp_path: Path) -> Path:
    root = tmp_path / "content"
    shutil.copytree(CONTENT, root)
    return root


def edit_json(path: Path, change: Callable[[Any], None]) -> None:
    data = load_json(path)
    change(data)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def republished_store(root: Path) -> ContentStore:
    """Rebuild the publication index from scratch for an edited scratch copy of content."""
    (root / PUBLISHED_INDEX).unlink()
    index, _ = publish(ContentStore(root), published_on="2026-09-28")
    write_index(root, index)
    return ContentStore(root)
