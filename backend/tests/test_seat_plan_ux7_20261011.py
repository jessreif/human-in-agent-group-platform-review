"""UX v7 (2026-10-11) seat_plan schedule — unit pieces.

Prof. feedback v7 replaced the targets/seat-policy schedule with an explicit
``seat_plan``: teammate assignments are dealt upfront (same keyed
``seat_assign`` shuffle the legacy fill uses) and each scheduled ask names its
seat plus an ordinal into that seat's own dealt crates — "JT asks about his
lowest-# crate". Covered here:

- ``ScriptedRequests`` schema rules for ``seat_policy == "seat_plan"``.
- ``plan_scripted_requests`` ordinal resolution (0 = lowest, -1 = highest).
- ``deal_teammate_assignments`` / ``with_teammate_assignments`` dealt path —
  every ask lands on a crate the asking seat is actually assigned.
- The v10/v12 preview pair's real manifests across seeds: five asks, seat
  pattern JT PM JT JT PM, asks always on the asker's beat.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.manifests.binding import bind
from app.domain.manifests.store import ContentStore
from app.domain.rng import RandomStreams
from app.domain.schedules import PlanningError, plan_scripted_requests
from app.domain.world import (
    build_world,
    deal_teammate_assignments,
    validate_world,
    with_teammate_assignments,
)
from app.timing.policy import ResolvedPolicy
from tests.wp02 import copy_content, edit_json, policy, republished_store

SEAT_PLAN = [
    {"seat": "JT", "pick": 0},
    {"seat": "PM", "pick": 0},
    {"seat": "JT", "pick": 1},
    {"seat": "JT", "pick": 2},
    {"seat": "PM", "pick": -1},
]
TIMES_MS = [45_000, 100_000, 155_000, 210_000, 245_000]


def _policy(manifest_id: str, arm: str = "high") -> ResolvedPolicy:
    return policy(manifest_id, arm)


# ----- schema rules -----------------------------------------------------------


SCRIPT = {"artifact_id": "requests.demo", "version": 4}


def _sr(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "script": SCRIPT,
        "times_active_s": [45, 100],
        "seat_policy": "seat_plan",
        "seat_plan": [{"seat": "JT", "pick": 0}, {"seat": "PM", "pick": 0}],
        "status": "approved",
    }
    base.update(over)
    for k in ("seat_plan", "targets", "seat_multiset"):
        if over.get(k) is None and k in over:
            base.pop(k, None)
    return base


def test_seat_plan_schema_rules(tmp_path) -> None:
    """seat_plan pairs only with seat_policy 'seat_plan', one slot per time,
    and carries the seats — targets/seat_multiset must be omitted."""
    root = copy_content(tmp_path / "sp")
    edit_json(
        root / "profiles" / "tp_ai_v10.json",
        lambda d: d["timing"].__setitem__("scripted_requests", _sr()),
    )
    store = republished_store(root)
    assert (
        bind(store, "tp_ai_v10", mode="preview").manifest.timing.scripted_requests.seat_plan
        is not None
    )

    bads = (
        _sr(seat_plan=None),  # missing seat_plan
        _sr(seat_plan=[{"seat": "JT", "pick": 0}]),  # one slot short
        _sr(targets=["assigned", "assigned"]),  # targets present alongside
        _sr(seat_multiset=["JT", "PM"]),  # seat_multiset alongside
        _sr(
            seat_policy="shuffle_fixed_multiset",
            targets=["assigned", "assigned"],
            seat_multiset=["JT", "PM"],
        ),  # seat_plan under a legacy policy
    )
    for j, bad in enumerate(bads):
        root2 = copy_content(tmp_path / f"bad{j}")
        edit_json(
            root2 / "profiles" / "tp_ai_v10.json",
            lambda d, b=bad: d["timing"].__setitem__("scripted_requests", b),
        )
        with pytest.raises((ValueError, ValidationError)):
            bind(republished_store(root2), "tp_ai_v10", mode="preview")


# ----- planner ordinal resolution ---------------------------------------------


def _dealt() -> dict[str, tuple[int, ...]]:
    return {"JT": (1, 4, 6, 8), "PM": (2, 3, 5, 7)}


def test_seat_plan_resolves_ordinals_off_the_seat_deal() -> None:
    p = _policy("tp_ai_v10")
    dealt = _dealt()
    plans = plan_scripted_requests(
        p,
        RandomStreams("aa" * 32),
        assigned=(1, 3, 5, 7),
        unassigned=(2, 4, 6, 8),
        seat_assignments=dealt,
    )
    assert [pl.at_active_ms for pl in plans] == TIMES_MS
    assert [pl.seat for pl in plans] == ["JT", "PM", "JT", "JT", "PM"]
    assert [pl.crate for pl in plans] == [
        dealt["JT"][0],
        dealt["PM"][0],
        dealt["JT"][1],
        dealt["JT"][2],
        dealt["PM"][-1],
    ]
    assert all(pl.target == "seat_plan" for pl in plans)


def test_seat_plan_needs_dealt_assignments() -> None:
    p = _policy("tp_ai_v10")
    with pytest.raises(PlanningError, match="seat assignments"):
        plan_scripted_requests(
            p, RandomStreams("aa" * 32), assigned=(1, 3, 5, 7), unassigned=(2, 4, 6, 8)
        )


def test_seat_plan_consumes_no_schedule_draws() -> None:
    """Under seat_plan the asks are pinned by the profile — the seed only moves
    the dealt assignments, never which slot asks what."""
    from app.domain.rng import DrawLog

    log = DrawLog()
    streams = RandomStreams("aa" * 32, log)
    plan_scripted_requests(
        _policy("tp_ai_v10"),
        streams,
        assigned=(1, 3, 5, 7),
        unassigned=(2, 4, 6, 8),
        seat_assignments=_dealt(),
    )
    assert log.for_stream("schedules") == []


# ----- dealt assignments -------------------------------------------------------


def test_deal_uses_the_same_keyed_draw_as_the_legacy_fill() -> None:
    streams = RandomStreams("aa" * 32)
    dealt = deal_teammate_assignments(streams)
    assert sorted(dealt) == ["JT", "PM"]
    assert all(len(v) == 4 and tuple(sorted(v)) == v for v in dealt.values())
    # Replaying the same seed reproduces the same deal (keyed stream).
    assert deal_teammate_assignments(RandomStreams("aa" * 32)) == dealt


def test_with_teammate_assignments_dealt_is_authoritative() -> None:
    streams = RandomStreams("aa" * 32)
    w = build_world(streams)
    dealt = deal_teammate_assignments(streams)
    asked = [("JT", dealt["JT"][0]), ("PM", dealt["PM"][0])]
    w2 = with_teammate_assignments(streams, w, asked, dealt=dealt)
    assert {s: list(v) for s, v in w2.teammate_assignments.items()} == {
        s: list(v) for s, v in dealt.items()
    }
    assert validate_world(w2) == []


def test_with_teammate_assignments_dealt_rejects_off_deal_asks() -> None:
    streams = RandomStreams("aa" * 32)
    w = build_world(streams)
    dealt = deal_teammate_assignments(streams)
    off_deal = next(n for n in range(1, 9) if n not in dealt["JT"])
    with pytest.raises(ValueError, match="not in its deal"):
        with_teammate_assignments(streams, w, [("JT", off_deal)], dealt=dealt)


# ----- the v10/v12 manifests across seeds --------------------------------------


@pytest.mark.parametrize("manifest_id", ["tp_ai_v10", "tp_scripted_v12"])
def test_seat_plan_profiles_ask_only_on_the_askers_beat(manifest_id: str) -> None:
    """UX v7 acceptance: over hundreds of worlds every scripted ask targets a
    crate the asking seat was dealt, in the PDF's seat/timing pattern."""
    for i in range(300):
        seed = f"{i:064x}"
        streams = RandomStreams(seed)
        w = build_world(streams)
        dealt = deal_teammate_assignments(streams)
        plans = plan_scripted_requests(
            _policy(manifest_id),
            streams,
            assigned=w.assigned,
            unassigned=[n for n in range(1, 9) if n not in w.assigned],
            seat_assignments=dealt,
        )
        assert [pl.at_active_ms for pl in plans] == TIMES_MS
        assert [pl.seat for pl in plans] == ["JT", "PM", "JT", "JT", "PM"]
        for pl, slot in zip(plans, SEAT_PLAN, strict=True):
            assert pl.crate == dealt[pl.seat][slot["pick"]]
        w2 = with_teammate_assignments(
            streams, w, [(pl.seat, pl.crate) for pl in plans], dealt=dealt
        )
        assert validate_world(w2) == [], f"{manifest_id} seed {i}"


def test_v10_v12_pin_their_schedule_and_artifacts() -> None:
    store = ContentStore.load_default()
    for manifest_id in ("tp_ai_v10", "tp_scripted_v12"):
        b = bind(store, manifest_id, mode="preview")
        sr = b.manifest.timing.scripted_requests
        assert sr.seat_policy == "seat_plan"
        assert sr.targets is None and sr.seat_multiset is None
        assert list(sr.times_active_s) == [45, 100, 155, 210, 245]
        assert [(s.seat, s.pick) for s in sr.seat_plan or ()] == [
            ("JT", 0),
            ("PM", 0),
            ("JT", 1),
            ("JT", 2),
            ("PM", -1),
        ]
        # successor pair keeps the v9/v11 artifact pins.
        assert b.artifacts["instruments.training"].key == "training.reference@v5"
        assert b.artifacts["instruments.consent"].key == "consent.reference@v8"
        assert b.artifacts["instruments.debrief"].key == "debrief.tp_reference@v3"
    ai = bind(store, "tp_ai_v10", mode="preview")
    assert ai.artifacts["teammates.social_prompt"].key == "prompt.social_demo@v4"


def test_legacy_profiles_keep_targets_mode() -> None:
    """v9/v11 stay on the targets/multiset schedule — seat_plan never applied
    retroactively to a published manifest."""
    for manifest_id in ("tp_ai_v9", "tp_scripted_v11"):
        sr = _policy(manifest_id).scripted_requests
        assert sr["seat_plan"] is None
        assert sr["targets"] == ["unassigned", "assigned_own", "unassigned"]
        assert sr["times_ms"] == [45_000, 130_000, 210_000]
