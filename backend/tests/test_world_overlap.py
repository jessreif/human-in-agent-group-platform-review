"""2026-10-15 crate-condition experiment: ``WorldSpec`` generator coverage —
legacy byte-identical regression, per-level teammate-assignment dealing, the
thin-file trim, and ``validate_world`` across seeds for every condition."""

from __future__ import annotations

import dataclasses

import pytest

from app.domain.rng import RandomStreams
from app.domain.world import (
    N_ASSIGNED,
    WorldSpec,
    build_world,
    deal_teammate_assignments,
    decisive_record_id,
    validate_world,
    with_intel_budget,
    with_teammate_assignments,
    with_unassigned_coverage,
)

SEEDS = [f"{i:064x}" for i in range(12)]


def _sans_id(world):
    return dataclasses.replace(world, world_id=None)


def test_default_spec_is_byte_identical_to_legacy() -> None:
    """``spec=None`` and ``WorldSpec()`` consume the identical draws — the
    published demo_v1 worlds can never drift."""
    for seed in SEEDS:
        legacy = _sans_id(build_world(RandomStreams(seed)))
        explicit = _sans_id(build_world(RandomStreams(seed), WorldSpec()))
        assert legacy == explicit


def test_overlap_specs_share_legacy_draws_for_crates() -> None:
    """The crate timelines/assignment under ``some`` equal the legacy build —
    only teammate dealing differs (the spec keeps the same ``world`` keys)."""
    for seed in SEEDS[:4]:
        legacy = _sans_id(build_world(RandomStreams(seed)))
        some = _sans_id(build_world(RandomStreams(seed), WorldSpec(overlap="some")))
        assert legacy.crates == some.crates
        assert legacy.assigned == some.assigned
        assert legacy.holdings == some.holdings


def test_none_partitions_twelve_crates() -> None:
    for seed in SEEDS:
        spec = WorldSpec(n_crates=12, overlap="none")
        w = build_world(RandomStreams(seed), spec)
        dealt = deal_teammate_assignments(RandomStreams(seed), spec, w.assigned)
        sets = [set(w.assigned), set(dealt["JT"]), set(dealt["PM"])]
        assert all(len(s) == N_ASSIGNED for s in sets)
        union = set().union(*sets)
        assert len(union) == 12 and union == set(range(1, 13))


def test_full_deals_the_same_assignment_to_every_seat() -> None:
    for seed in SEEDS:
        spec = WorldSpec(n_crates=4, overlap="full")
        w = build_world(RandomStreams(seed), spec)
        assert len(w.crates) == 4 and len(w.assigned) == N_ASSIGNED
        dealt = deal_teammate_assignments(RandomStreams(seed), spec, w.assigned)
        assert dealt["JT"] == dealt["PM"] == w.assigned


def test_some_deals_within_locked_intersection_bounds() -> None:
    """Audit W02: all three pairwise intersections must hold 1-2 crates —
    not merely 'nonzero and unequal'. Seed "0"*64 produced |P∩PM|=|JT∩PM|=3
    under the weaker rule."""
    violators = 0
    for i in range(200):
        seed = f"{i:064x}"
        spec = WorldSpec(n_crates=8, overlap="some")
        w = build_world(RandomStreams(seed), spec)
        dealt = deal_teammate_assignments(RandomStreams(seed), spec, w.assigned)
        pset, jt, pm = set(w.assigned), set(dealt["JT"]), set(dealt["PM"])
        assert len(jt) == len(pm) == N_ASSIGNED
        if not (1 <= len(jt & pset) <= 2 and 1 <= len(pm & pset) <= 2 and 1 <= len(jt & pm) <= 2):
            violators += 1
    assert violators == 0


def test_some_bounds_hold_for_the_audit_seed() -> None:
    spec = WorldSpec(n_crates=8, overlap="some")
    w = build_world(RandomStreams("0" * 64), spec)
    dealt = deal_teammate_assignments(RandomStreams("0" * 64), spec, w.assigned)
    pset, jt, pm = set(w.assigned), set(dealt["JT"]), set(dealt["PM"])
    assert 1 <= len(jt & pset) <= 2
    assert 1 <= len(pm & pset) <= 2
    assert 1 <= len(jt & pm) <= 2


def test_some_fallback_is_deterministic_and_valid() -> None:
    """If rejection sampling ever exhausts its attempts, the fallback is
    seeded, deterministic, and still satisfies all three bounds."""
    spec = WorldSpec(n_crates=8, overlap="some")
    for seed in SEEDS:
        a = deal_teammate_assignments(RandomStreams(seed), spec, (1, 2, 3, 4))
        b = deal_teammate_assignments(RandomStreams(seed), spec, (1, 2, 3, 4))
        assert a == b
        jt, pm, pset = set(a["JT"]), set(a["PM"]), {1, 2, 3, 4}
        assert 1 <= len(jt & pset) <= 2
        assert 1 <= len(pm & pset) <= 2
        assert 1 <= len(jt & pm) <= 2


def test_none_coverage_limits_file_density() -> None:
    """Audit W03: under ``none`` the file covers only the asked-unassigned
    set plus a fill to ≥4 — not all eight unassigned crates."""
    spec = WorldSpec(n_crates=12, overlap="none")
    for seed in SEEDS:
        w = build_world(RandomStreams(seed), spec)
        dealt = deal_teammate_assignments(RandomStreams(seed), spec, w.assigned)
        w = with_teammate_assignments(RandomStreams(seed), w, (), dealt=dealt)
        # emulate the ensure_world coverage step with a 4-crate covered set
        unassigned = [n for n in range(1, 13) if n not in w.assigned]
        w = with_unassigned_coverage(RandomStreams(seed), w, unassigned[:4])
        assert w.covered_unassigned == tuple(unassigned[:4])
        assert validate_world(w) == []
        held_crates = {w.records[rid].crate for rid in w.holdings["participant"]}
        assert held_crates <= set(w.assigned) | set(unassigned[:4])
        # every record still has exactly one holder
        all_ids = [rid for rids in w.holdings.values() for rid in rids]
        assert sorted(all_ids) == sorted(w.records)


@pytest.mark.parametrize(
    "spec",
    [
        WorldSpec(),
        WorldSpec(n_crates=12, overlap="none"),
        WorldSpec(overlap="some"),
        WorldSpec(n_crates=4, overlap="full"),
        WorldSpec(overlap="some", participant_intel_budget=20),
        WorldSpec(n_crates=12, overlap="none", participant_intel_budget=20),
        WorldSpec(n_crates=4, overlap="full", participant_intel_budget=20),
    ],
)
def test_validate_world_passes_every_condition(spec: WorldSpec) -> None:
    for seed in SEEDS[:6]:
        w = build_world(RandomStreams(seed), spec)
        dealt = deal_teammate_assignments(RandomStreams(seed), spec, w.assigned)
        w = with_teammate_assignments(RandomStreams(seed), w, (), dealt=dealt)
        w = with_intel_budget(RandomStreams(seed), w, ())
        assert validate_world(w) == [], f"{spec} seed {seed}: {validate_world(w)}"


@pytest.mark.parametrize(
    "spec",
    [
        WorldSpec(overlap="some", participant_intel_budget=20),
        WorldSpec(n_crates=12, overlap="none", participant_intel_budget=20),
        WorldSpec(n_crates=4, overlap="full", participant_intel_budget=20),
    ],
    ids=["some-8", "none-12", "full-4"],
)
def test_intel_budget_trims_to_the_exact_pinned_count(spec: WorldSpec) -> None:
    """Audit G05/S03: the budget is the *administered* record count — exactly
    20, never 'at most' — and every dropped record redeals to a seat."""
    for seed in SEEDS:
        w = build_world(RandomStreams(seed), spec)
        dealt = deal_teammate_assignments(RandomStreams(seed), spec, w.assigned)
        w = with_teammate_assignments(RandomStreams(seed), w, (), dealt=dealt)
        if spec.overlap == "none":
            unassigned = [n for n in range(1, spec.n_crates + 1) if n not in w.assigned]
            w = with_unassigned_coverage(RandomStreams(seed), w, unassigned[:4])
        else:
            unassigned = [n for n in range(1, spec.n_crates + 1) if n not in w.assigned]
            w = with_unassigned_coverage(RandomStreams(seed), w, unassigned)
        before = len(w.holdings["participant"])
        trimmed = with_intel_budget(RandomStreams(seed), w, ())
        assert len(trimmed.holdings["participant"]) == 20
        assert before > 20
        all_ids = [rid for rids in trimmed.holdings.values() for rid in rids]
        assert sorted(all_ids) == sorted(trimmed.records)
        assert validate_world(trimmed) == []


def test_intel_budget_retains_own_crate_truth_confirmer() -> None:
    """Own-file crates must stay solvable after trimming — the truth-confirming
    entry is among the most-recent kept records."""
    spec = WorldSpec(overlap="some", participant_intel_budget=20)
    for seed in SEEDS:
        w = build_world(RandomStreams(seed), spec)
        trimmed = with_intel_budget(RandomStreams(seed), w, ())
        held = set(trimmed.holdings["participant"])
        for n in trimmed.assigned:
            if trimmed.role_of[n] != "own":
                continue
            assert (
                decisive_record_id(tuple(trimmed.records.values()), trimmed.crates[n - 1].truth, n)
                in held
            )


def test_no_budget_is_a_noop() -> None:
    w = build_world(RandomStreams(SEEDS[0]), WorldSpec(overlap="some"))
    assert with_intel_budget(RandomStreams(SEEDS[0]), w, ()) is w


def test_comprehension_key_flips_same_crates_only_under_full() -> None:
    from app.services.lifecycle import comprehension_key

    for overlap in ("independent", "none", "some"):
        assert comprehension_key(ai_assistant=True, overlap=overlap)["same_crates"] is False
    assert comprehension_key(ai_assistant=True, overlap="full")["same_crates"] is True
    # The assistant flag still removes its item under any condition.
    assert "assistant_reads_team" not in comprehension_key(ai_assistant=False, overlap="full")
