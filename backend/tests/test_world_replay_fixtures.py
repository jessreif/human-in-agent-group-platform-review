"""Audit G06: byte-identity oracles against *trusted pre-change* worlds.

Each fixture under ``fixtures/world_replays/`` was extracted read-only from
the production ``world_snapshot``/``draw_log``/``session.seed_hex`` rows of a
participant session generated on 2026-10-08..13 — before the crate-condition
generator shipped. Replaying ``generate_world`` with the recorded
seed/manifest/arm must reproduce the persisted draws exactly and, after the
nondeterministic ``world_id`` mint is normalized, the identical
``content_sha256``. Any silent drift in the refactored pipeline (draw order,
coverage step, trim, file order) fails this oracle — a much stronger
guarantee than self-consistency tests."""

from __future__ import annotations

import dataclasses
import json
import uuid
from pathlib import Path

import pytest

from app.domain.manifests.binding import bind
from app.domain.manifests.canonical import canonical_bytes, sha256_hex
from app.domain.manifests.store import ContentStore
from app.domain.world import validate_world
from app.services.sessions import _key_text
from app.services.worlds import generate_world, world_content

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "world_replays").glob("*.json"))
assert FIXTURES, "world replay fixtures are required"


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_pre_change_world_replays_byte_identically(store: ContentStore, path: Path) -> None:
    fx = json.loads(path.read_text())
    bound = bind(store, fx["manifest_id"], mode="live")

    world, file_order, requests, log = generate_world(fx["seed_hex"], fx["arm"], bound)

    # 1. Every draw key realized now AND persisted then carries the identical
    #    value. Draws are keyed (not sequential), so draws added by features
    #    shipped between the fixture date and now — e.g. the ``assigned_own``
    #    pool (2026-10-10) or ``seat_plan`` (2026-10-11) — appear on one side
    #    only and never perturb a shared draw's value. A *value* difference on
    #    any shared key is the silent-drift signal this oracle exists to catch.
    realized = {(r.stream, _key_text(r)): r.value for r in log.records}
    persisted = {(d["stream"], d["draw_key"]): d["value"] for d in fx["draw_log"]}
    shared = realized.keys() & persisted.keys()
    assert shared, "fixture must share draw keys with the replayed pipeline"
    assert {k: (realized[k], persisted[k]) for k in shared if realized[k] != persisted[k]} == {}
    # The persisted "world"-stream draws must all still be realized — the
    # world build never drops a draw it used to take.
    world_persisted = {k for k in persisted if k[0] == "world"}
    assert world_persisted <= realized.keys()

    # 2. The persisted snapshot content is byte-identical once two known
    #    additive deltas are normalized: the nondeterministic ``world_id``
    #    mint and the crate-condition ``world_params`` block (2026-10-15 —
    #    added to the schema, carries no world content).
    world = dataclasses.replace(world, world_id=uuid.UUID(fx["world_id"]))
    content = world_content(world, file_order, requests)
    assert set(content) - set(fx["content"]) == {"world_params"}
    content.pop("world_params")
    assert sha256_hex(canonical_bytes(content)) == fx["content_sha256"]

    # 3. The replayed world still passes the (now parameterized) validator.
    assert validate_world(world) == []
