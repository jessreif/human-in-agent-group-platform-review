"""Paired-arm and profile-to-profile diffs over *resolved* policies (INV-0208, INV-0211).

Diffs compare what the runtime would execute (``ResolvedPolicy.as_dict()``), not manifest
text, so a difference that the resolver ignores cannot hide and a difference it acts on cannot
be missed. Lists are compared as whole values at their path.

- Paired arms: the only permitted differences are under ``manipulation.arm``.
- Reference-to-v2: every difference must be listed in a reviewed diff record
  (``content/profiles/diffs/<from>__<to>.json``); the actual diff must equal the declared set.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from app.domain.manifests.binding import BoundManifest
from app.domain.manifests.schema import DecisionId, Strict
from app.timing.policy import resolve_policy

ALLOWED_ARM_PREFIX = "manipulation.arm"
IGNORED_PROFILE_PATHS = frozenset({"behavior.manifest_id"})


@dataclass(frozen=True)
class DiffEntry:
    path: str
    left: Any
    right: Any


def json_diff(left: Any, right: Any, path: str = "") -> list[DiffEntry]:
    if isinstance(left, dict) and isinstance(right, dict):
        out: list[DiffEntry] = []
        for key in sorted(set(left) | set(right)):
            sub = f"{path}.{key}" if path else str(key)
            if key not in left or key not in right:
                out.append(DiffEntry(sub, left.get(key), right.get(key)))
            else:
                out.extend(json_diff(left[key], right[key], sub))
        return out
    if left != right:
        return [DiffEntry(path, left, right)]
    return []


class ArmDiffError(ValueError):
    pass


def paired_arm_diff(bound: BoundManifest) -> list[DiffEntry]:
    first, second = bound.manifest.arms
    return json_diff(
        resolve_policy(bound, first).as_dict(), resolve_policy(bound, second).as_dict()
    )


def check_paired_arms(bound: BoundManifest) -> list[DiffEntry]:
    """Return the (permitted) arm differences; raise if anything else differs."""
    entries = paired_arm_diff(bound)
    illegal = [e.path for e in entries if not e.path.startswith(ALLOWED_ARM_PREFIX)]
    if illegal:
        raise ArmDiffError(f"{bound.manifest_id}: unintended arm differences {illegal}")
    if not entries:
        raise ArmDiffError(f"{bound.manifest_id}: arms are identical; no manipulation")
    return entries


def profile_diff(left: BoundManifest, right: BoundManifest) -> list[DiffEntry]:
    """Behavior differences between two profiles of the same study (arm-independent)."""
    if left.manifest.study_key != right.manifest.study_key:
        raise ValueError("profile diffs compare profiles of the same study")
    l_policy = resolve_policy(left, left.manifest.arms[0]).as_dict()["behavior"]
    r_policy = resolve_policy(right, right.manifest.arms[0]).as_dict()["behavior"]
    return [
        e
        for e in json_diff({"behavior": l_policy}, {"behavior": r_policy})
        if e.path not in IGNORED_PROFILE_PATHS
    ]


class DeclaredDifference(Strict):
    path: str = Field(min_length=1)
    kind: Literal["timing_mechanism", "schedule", "channels", "content", "policy", "manipulation"]
    rationale: str = Field(min_length=1)


class DiffRecord(Strict):
    schema_version: Literal["investigators.profile_diff/1"]
    from_manifest: str
    to_manifest: str
    status: Literal["proposed_unapproved", "approved"]
    decisions: tuple[DecisionId, ...]
    differences: tuple[DeclaredDifference, ...]


def load_diff_record(content_root: Path, left: str, right: str) -> DiffRecord:
    path = content_root / "profiles" / "diffs" / f"{left}__{right}.json"
    return DiffRecord.model_validate_json(path.read_bytes())


def check_declared_diff(record: DiffRecord, actual: list[DiffEntry]) -> list[str]:
    declared = {d.path for d in record.differences}
    found = {e.path for e in actual}
    problems = [f"undeclared difference {p}" for p in sorted(found - declared)]
    problems += [f"declared difference not present {p}" for p in sorted(declared - found)]
    return problems


def render_value(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return text if len(text) <= 160 else text[:157] + "..."
