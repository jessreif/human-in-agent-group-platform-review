"""2026-10-15 crate-condition experiment, disposable-DB acceptance tests.

Each audit acceptance gets a named test:

- W01: a 12-crate ``none`` world persists under the migrated crate bounds and
  ``record.crate``/``request.crate`` accept values 9-12.
- W05: a ``full``-overlap harness run passes truthful comprehension (the
  runner raises if the comprehension submit leaves the session in
  ``training``) and reaches completion.
- W06: participant request traffic stays inside the session's crate range —
  ≤4 under ``full`` and reaching ≥9 under ``none``.
- S02/S03: every thin profile completes with exactly 20 initial-file records.
- W03: ``none`` coverage is bounded — ``covered_unassigned`` covers every
  asked unassigned crate but not all eight.
- E01: ``participant_wide`` exports the administered condition factors.
- S01: ``task_shape_for_session`` resolves the administered shape from the
  persisted snapshot.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db import schema as s
from app.domain.manifests.store import ContentStore
from app.export.study import _participant_wide
from app.services import adminviews
from app.services.worlds import task_shape_for_session
from harness.cli import _StubProvider
from harness.personas import AskerPersona, NormalPersona
from harness.runner import Runner

from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration


def _runner(db: MigratedDatabase) -> Runner:
    return Runner(db, ContentStore.load_default(), provider=_StubProvider())


def _run(db: MigratedDatabase, manifest_id: str, persona=None) -> uuid.UUID:
    runner = _runner(db)
    batch = f"crate-cond.{manifest_id}"
    runner.seed_batch(manifest_id, batch)
    report = runner.run_session(manifest_id, batch, persona or NormalPersona(7), seed=7)
    assert report.stage == "completion", report.findings
    return uuid.UUID(report.session_id)


def _world_params(db: MigratedDatabase, sid: uuid.UUID) -> dict:
    with db.runtime.connect() as conn:
        content = conn.execute(
            select(s.world_snapshot.c.content).where(s.world_snapshot.c.session_id == sid)
        ).scalar_one()
    return dict(content["world_params"])


def _wide(db: MigratedDatabase, sid: uuid.UUID) -> dict:
    with db.runtime.connect() as conn:
        session = dict(
            conn.execute(select(s.session).where(s.session.c.session_id == sid)).mappings().one()
        )
        return _participant_wide(conn, [session])[0]


def _file_count(db: MigratedDatabase, sid: uuid.UUID) -> int:
    with db.runtime.connect() as conn:
        return len(
            conn.execute(
                select(s.file_membership.c.record_id).where(
                    s.file_membership.c.session_id == sid,
                    s.file_membership.c.source == "initial",
                )
            ).all()
        )


def _request_crates(db: MigratedDatabase, sid: uuid.UUID) -> list[int]:
    with db.runtime.connect() as conn:
        return [
            r
            for r in conn.execute(
                select(s.request.c.crate).where(
                    s.request.c.session_id == sid,
                    s.request.c.requester == "participant",
                    s.request.c.crate.isnot(None),
                )
            ).scalars()
        ]


def test_none_world_persists_twelve_crates_and_exports_factors(
    migrated: MigratedDatabase,
) -> None:
    """W01 + W03 + E01 + S01: the migrated bounds accept crates 9-12, coverage
    is bounded to the asked set plus fill, and the export emits the factors."""
    sid = _run(migrated, "tp_ai_ovnone_v1")
    params = _world_params(migrated, sid)
    assert params["n_crates"] == 12
    assert params["crate_overlap"] == "none"
    assert params["participant_intel_budget"] is None
    covered = set(params["covered_unassigned"])
    with migrated.runtime.connect() as conn:
        content = conn.execute(
            select(s.world_snapshot.c.content).where(s.world_snapshot.c.session_id == sid)
        ).scalar_one()
        crate_nums = {
            r
            for r in conn.execute(
                select(s.record.c.crate).where(s.record.c.session_id == sid)
            ).scalars()
        }
    assigned = set(content["assigned"])
    assert crate_nums and max(crate_nums) == 12  # migrated bounds hold rows 9-12
    replay = adminviews.replay(migrated.runtime, sid, include_restricted=False, store=None)
    assert replay is not None
    assert replay["participant"]["pages"]["task_shape"] == {
        "n_crates": 12,
        "n_assigned": 4,
        "crate_overlap": "none",
        "intel_budget": None,
    }
    unassigned = set(range(1, 13)) - assigned
    assert covered <= unassigned
    assert 4 <= len(covered) <= 7  # bounded: not all eight (audit W03)
    for n in content["requested_crates"]:
        if n not in assigned:
            assert n in covered  # every asked unassigned crate stays covered
    # snapshot-first resolver returns the administered shape (audit S01)
    assert task_shape_for_session(content, None) == {
        "n_crates": 12,
        "n_assigned": 4,
        "crate_overlap": "none",
        "intel_budget": None,
    }
    wide = _wide(migrated, sid)
    assert wide["crate_overlap"] == "none"
    assert wide["n_crates"] == 12
    assert wide["intel_budget"] is None
    assert _file_count(migrated, sid) > 20


def test_full_truthful_comprehension_and_bounded_requests(
    migrated: MigratedDatabase,
) -> None:
    """W05 + W06: the condition-aware key (same_crates → True under ``full``)
    advances the real gate — reaching completion proves it — and every
    participant request targets a crate inside the 4-crate world."""
    sid = _run(migrated, "tp_ai_ovfull_v1", persona=AskerPersona(7))
    params = _world_params(migrated, sid)
    assert params["n_crates"] == 4 and params["crate_overlap"] == "full"
    crates = _request_crates(migrated, sid)
    assert crates and max(crates) <= 4
    wide = _wide(migrated, sid)
    assert wide["crate_overlap"] == "full" and wide["n_crates"] == 4


def test_none_asker_reaches_crates_nine_through_twelve(
    migrated: MigratedDatabase,
) -> None:
    """W06: typed asks parse against the 12-crate session world — crates
    9-12 are reachable request targets, not out-of-range."""
    sid = _run(migrated, "tp_ai_ovnone_v1", persona=AskerPersona(7))
    crates = set(_request_crates(migrated, sid))
    assert crates and max(crates) >= 9
    assert crates <= set(range(1, 13))


@pytest.mark.parametrize(
    "manifest_id",
    [
        "tp_ai_ovnone_thin_v1",
        "tp_ai_ovsome_thin_v1",
        "tp_ai_ovfull_thin_v1",
    ],
)
def test_thin_profiles_complete_with_exactly_twenty_records(
    migrated: MigratedDatabase, manifest_id: str
) -> None:
    """S02 + S03 + E01: all three thin shapes bind, generate, complete, and
    administer exactly the pinned 20-record file — never 'at most'."""
    sid = _run(migrated, manifest_id)
    assert _world_params(migrated, sid)["participant_intel_budget"] == 20
    assert _file_count(migrated, sid) == 20
    wide = _wide(migrated, sid)
    assert wide["intel_budget"] == 20
    assert wide["crate_overlap"] == manifest_id.split("_")[2].replace("ov", "")
