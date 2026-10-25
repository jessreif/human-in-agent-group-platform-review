"""Load, validate and hash the versioned content tree (INV-0201, INV-0206).

Layout under the content root (``investigators/content`` in the source tree, baked into the
backend image at ``/srv/investigators/content``):

- ``profiles/<manifest_id>.json``           experiment manifests
- ``scripts|instruments|policies/<artifact_id>.v<N>.json``  versioned artifacts
- ``profiles/diffs/*.json``                 reviewed profile-to-profile difference records
- ``published/manifests.json``              immutable publication index
- ``schemas/*.schema.json``                 generated JSON Schemas (documentation/contract)

Loading collects *every* problem before failing, so one run reports all rejected fields.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError

from app.domain.manifests.artifacts import ARTIFACT_ADAPTER, Artifact
from app.domain.manifests.canonical import content_hash
from app.domain.manifests.schema import ArtifactRef, Manifest, Strict

CONTENT_DIR_VARIABLE = "INVESTIGATORS_CONTENT_DIR"
ARTIFACT_DIRS = ("scripts", "instruments", "policies", "coding")
PUBLISHED_INDEX = Path("published") / "manifests.json"
_SHA256 = r"^[0-9a-f]{64}$"


class ContentError(ValueError):
    """Content failed validation. ``problems`` lists every rejected file/field."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def format_validation_error(exc: ValidationError, prefix: str) -> list[str]:
    problems = []
    for error in exc.errors(include_url=False, include_context=False):
        loc = ".".join(str(part) for part in error.get("loc", ())) or "<root>"
        kind = error.get("type", "invalid")
        if kind == "literal_error":
            msg = f"unsupported value (not implemented or dormant) - {error.get('msg', '')}"
        elif kind == "extra_forbidden":
            msg = "unsupported field (rejected at bind)"
        else:
            msg = str(error.get("msg", kind)).removeprefix("Value error, ")
        problems.append(f"{prefix}: {loc}: {msg}")
    return problems


class PublishedEntry(Strict):
    manifest_sha256: str = Field(pattern=_SHA256)
    bundle_sha256: str = Field(pattern=_SHA256)
    published_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class PublishedIndex(Strict):
    schema_version: Literal["investigators.published/1"]
    manifests: dict[str, PublishedEntry]


@dataclass(frozen=True)
class StoredArtifact:
    artifact: Artifact
    sha256: str
    path: str

    @property
    def key(self) -> str:
        return self.artifact.key


@dataclass(frozen=True)
class StoredManifest:
    manifest: Manifest
    sha256: str
    path: str


def iter_artifact_refs(manifest: Manifest) -> Iterator[tuple[str, ArtifactRef, str]]:
    """Every artifact slot in a manifest as ``(slot, ref, expected_kind)``."""
    timing = manifest.timing
    yield "timing.scripted_requests.script", timing.scripted_requests.script, "request_script"
    for i, ref in enumerate(timing.warnings):
        yield f"timing.warnings[{i}]", ref, "warning"
    manipulation = manifest.manipulation
    if manipulation.kind == "pressure_checkin":
        yield "manipulation.copy_ref", manipulation.copy_ref, "checkin_copy"
        if manipulation.remarks is not None:
            yield "manipulation.remarks", manipulation.remarks, "pressure_remarks"
    for spec in manifest.condition_set:
        if spec.manipulation.kind == "evaluation_exposure":
            yield (
                f"condition_set.{spec.arm}.manipulation.exposure_copy",
                spec.manipulation.exposure_copy,
                "exposure_copy",
            )
    tm = manifest.teammates
    if tm.voice_bank is not None:
        yield "teammates.voice_bank", tm.voice_bank, "voice_bank"
    yield "teammates.fallback_bank", tm.fallback_bank, "fallback_bank"
    if tm.social_prompt is not None:
        yield "teammates.social_prompt", tm.social_prompt, "prompt"
    yield "assistant.prompt", manifest.assistant.prompt, "prompt"
    inst = manifest.instruments
    for slot in ("consent", "training", "debrief"):
        yield f"instruments.{slot}", getattr(inst, slot), "material_reference"
    yield "instruments.confidence", inst.confidence, "instrument"
    yield "instruments.post_task", inst.post_task, "instrument"
    if inst.peer_ratings is not None:
        yield "instruments.peer_ratings", inst.peer_ratings, "instrument"
    if inst.open_ended is not None:
        yield "instruments.open_ended", inst.open_ended, "instrument"
    yield "outcomes.payment_policy", manifest.outcomes.payment_policy, "payment_policy"


def canonical_manifest_dump(manifest: Manifest) -> dict[str, Any]:
    """The manifest's canonical JSON for hashing. 2026-10-11 schema extension:
    ``seat_plan``/optional ``targets`` are new fields — every pre-seat_plan
    manifest serializes them as ``None``, which would change its published sha.
    Drop the keys when unset so the canonical form of existing manifests is
    byte-stable."""
    dumped = manifest.model_dump(mode="json")
    sr = dumped.get("timing", {}).get("scripted_requests", {})
    for new_key in ("seat_plan", "targets"):
        if sr.get(new_key) is None:
            sr.pop(new_key, None)
    # BA-105 (2026-10-12 schema extension): ``instruments.open_ended`` is a new
    # optional slot — existing manifests serialize it as ``None``, which would
    # change their published sha. Drop the key when unset, as with seat_plan.
    inst = dumped.get("instruments", {})
    if inst.get("open_ended") is None:
        inst.pop("open_ended", None)
    return dumped


def default_content_root() -> Path:
    """``$INVESTIGATORS_CONTENT_DIR``, else the image layout, else the source-tree layout."""
    configured = os.environ.get(CONTENT_DIR_VARIABLE)
    if configured:
        return Path(configured)
    here = Path(__file__).resolve()
    for candidate in (here.parents[3] / "content", here.parents[4] / "content"):
        if (candidate / "profiles").is_dir():
            return candidate
    raise ContentError(["content directory not found; set INVESTIGATORS_CONTENT_DIR"])


class ContentStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        problems: list[str] = []
        self.artifacts: dict[str, StoredArtifact] = {}
        self.manifests: dict[str, StoredManifest] = {}
        for directory in ARTIFACT_DIRS:
            base = root / directory
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*.json")):
                self._load_artifact(path, problems)
        for path in sorted((root / "profiles").glob("*.json")):
            self._load_manifest(path, problems)
        self.published = self._load_published(problems)
        if problems:
            raise ContentError(problems)

    @classmethod
    def load_default(cls) -> ContentStore:
        return cls(default_content_root())

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def _load_artifact(self, path: Path, problems: list[str]) -> None:
        rel = self._rel(path)
        try:
            artifact = ARTIFACT_ADAPTER.validate_json(path.read_bytes())
        except ValidationError as exc:
            problems.extend(format_validation_error(exc, rel))
            return
        expected = f"{artifact.artifact_id}.v{artifact.version}.json"
        if path.name != expected:
            problems.append(f"{rel}: file name must be {expected}")
            return
        if artifact.key in self.artifacts:
            problems.append(f"{rel}: duplicate artifact {artifact.key}")
            return
        sha = content_hash(artifact.model_dump(mode="json"))
        self.artifacts[artifact.key] = StoredArtifact(artifact, sha, rel)

    def _load_manifest(self, path: Path, problems: list[str]) -> None:
        rel = self._rel(path)
        try:
            manifest = Manifest.model_validate_json(path.read_bytes())
        except ValidationError as exc:
            problems.extend(format_validation_error(exc, rel))
            return
        if path.stem != manifest.manifest_id:
            problems.append(f"{rel}: file name must be {manifest.manifest_id}.json")
            return
        sha = content_hash(canonical_manifest_dump(manifest))
        self.manifests[manifest.manifest_id] = StoredManifest(manifest, sha, rel)

    def _load_published(self, problems: list[str]) -> PublishedIndex:
        path = self.root / PUBLISHED_INDEX
        if not path.is_file():
            return PublishedIndex(schema_version="investigators.published/1", manifests={})
        try:
            return PublishedIndex.model_validate_json(path.read_bytes())
        except ValidationError as exc:
            problems.extend(format_validation_error(exc, PUBLISHED_INDEX.as_posix()))
            return PublishedIndex(schema_version="investigators.published/1", manifests={})

    # ----- resolution --------------------------------------------------------------------------
    def resolve_refs(self, manifest: Manifest) -> tuple[dict[str, StoredArtifact], list[str]]:
        resolved: dict[str, StoredArtifact] = {}
        problems: list[str] = []
        for slot, ref, kind in iter_artifact_refs(manifest):
            stored = self.artifacts.get(ref.key)
            if stored is None:
                problems.append(f"{manifest.manifest_id}: {slot} -> {ref.key} does not exist")
                continue
            if stored.artifact.kind != kind:
                problems.append(
                    f"{manifest.manifest_id}: {slot} -> {ref.key} is a "
                    f"{stored.artifact.kind}, expected {kind}"
                )
                continue
            resolved[slot] = stored
        return resolved, problems

    def bundle_sha256(self, manifest_id: str) -> str:
        stored = self.manifests[manifest_id]
        resolved, problems = self.resolve_refs(stored.manifest)
        if problems:
            raise ContentError(problems)
        return bundle_hash(stored.sha256, resolved)


def bundle_hash(manifest_sha256: str, artifacts: Mapping[str, StoredArtifact]) -> str:
    """Content SHA-256 pinned by every enrollment: the materialized manifest plus the exact
    materialized artifacts it binds (spec §4 "content SHA-256")."""
    return content_hash(
        {
            "manifest_sha256": manifest_sha256,
            "artifacts": {slot: [a.key, a.sha256] for slot, a in sorted(artifacts.items())},
        }
    )
