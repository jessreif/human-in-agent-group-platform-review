"""INV-0501/0502/0503/0509/0510: world generator determinism, chronology oracle,
invariant validation across seeds, and the invariant-7 independence report data."""

from __future__ import annotations

import hashlib
from collections import Counter

import pytest

from app.domain.rng import RandomStreams
from app.domain.world import (
    HIDEOUTS,
    LocationState,
    build_world,
    location_report,
    validate_world,
    with_teammate_assignments,
)

SEED = "cd" * 32


def _world(seed: str = SEED):
    return build_world(RandomStreams(seed))


def test_same_seed_same_world() -> None:
    a, b = _world(), _world()
    assert a.assigned == b.assigned
    assert a.role_of == b.role_of and a.cutoff_of == b.cutoff_of
    assert a.holdings == b.holdings
    assert all(x.entries == y.entries for x, y in zip(a.crates, b.crates, strict=True))


def test_different_seed_different_world() -> None:
    a = _world()
    other = build_world(RandomStreams("ef" * 32))
    assert a.assigned != other.assigned or a.holdings != other.holdings


def test_world_shape_and_roles() -> None:
    w = _world()
    assert len(w.crates) == 8 and len(w.assigned) == 4
    assert w.assigned == tuple(sorted(w.assigned))
    roles = [w.role_of[n] for n in w.assigned]
    assert roles.count("own") == 2 and roles.count("team") == 2
    for c in w.crates:
        assert len(set(c.walk)) == 3 and set(c.walk) <= set(HIDEOUTS)
        assert c.truth == c.walk[2]
    # holdings partition every record exactly once
    all_ids = [rid for rids in w.holdings.values() for rid in rids]
    assert sorted(all_ids) == sorted(w.records)


def test_own_file_crates_solvable_team_crates_not() -> None:
    w = _world()
    held_p = set(w.holdings["participant"])
    for n in w.assigned:
        truth = w.truth_of(n)
        confirmers = {
            e.record_id
            for e in w.crates[n - 1].entries
            if e.hideout == truth and e.event_type in ("arrival", "still_at")
        }
        if w.role_of[n] == "own":
            assert confirmers & held_p, f"own crate {n} not solvable from own file"
        else:
            assert not (confirmers & held_p), f"team crate {n} truth leaked to participant"
            held_team = set(w.holdings["JT"]) | set(w.holdings["PM"])
            assert confirmers & held_team


def test_unassigned_crates_give_two_useful_records() -> None:
    w = _world()
    held_p = set(w.holdings["participant"])
    for c in w.crates:
        if c.num in w.assigned:
            continue
        mine = [e for e in c.entries if e.record_id in held_p]
        late = [e for e in c.entries if e.ts >= 720][-2:]
        assert mine == late


def test_timeline_typed_events_not_prose() -> None:
    w = _world()
    for c in w.crates:
        types = {e.event_type for e in c.entries}
        assert {"arrival", "departure", "transit"} <= types
        assert all(e.toward is not None for e in c.entries if e.event_type == "transit")
        assert all(e.toward is None for e in c.entries if e.event_type != "transit")


def test_oracle_distinguishes_event_types() -> None:
    w = _world()
    c = w.crates[0]
    # at the end, every crate is confirmed at truth
    rep = location_report(c.entries)
    assert rep is not None and rep.state == LocationState.CONFIRMED_AT and rep.hideout == c.truth
    # First departure: left the place named by that entry; not yet in transit.
    depart = next(e for e in c.entries if e.event_type == "departure")
    rep_mid = location_report(c.entries, at_ts=depart.ts)
    assert rep_mid is not None
    assert rep_mid.state == LocationState.DEPARTED
    assert rep_mid.hideout == depart.hideout == c.walk[0]
    assert rep_mid.toward is None
    assert rep_mid.evidence_ts == depart.ts
    assert rep_mid.evidence_record_id == depart.record_id
    # First transit is a heading, never a confirmed arrival at either end.
    transit = next(e for e in c.entries if e.event_type == "transit")
    rep_t = location_report(c.entries, at_ts=transit.ts)
    assert rep_t is not None
    assert rep_t.state == LocationState.IN_TRANSIT
    assert rep_t.hideout == transit.hideout == c.walk[0]
    assert rep_t.toward == c.walk[1] == transit.toward
    assert rep_t.evidence_ts == transit.ts
    assert rep_t.evidence_record_id == transit.record_id


def test_decisive_record_is_earliest_confirmation() -> None:
    w = _world()
    for n, rid in w.participant_decisive.items():
        e = w.records[rid]
        assert e.hideout == w.truth_of(n) and e.event_type in ("arrival", "still_at")
        earlier = [
            x
            for x in w.crates[n - 1].entries
            if x.ts < e.ts
            and x.hideout == w.truth_of(n)
            and x.event_type in ("arrival", "still_at")
        ]
        assert not earlier


def test_teammate_assignments_cover_requested_crates() -> None:
    streams = RandomStreams(SEED)
    w = build_world(streams)
    requested = [("JT", 2), ("PM", 5), ("JT", 7)]
    w2 = with_teammate_assignments(streams, w, requested)
    assert set(w2.teammate_assignments["JT"]) >= {2, 7}
    assert 5 in w2.teammate_assignments["PM"]
    assert all(len(v) == 4 for v in w2.teammate_assignments.values())
    assert validate_world(w2) == []


def _world_sweep_seed(i: int) -> str:
    """256-bit hex seed, a pure function of the case index.

    SHA-256 is the length ``RandomStreams`` already accepts (``new_seed`` is 32
    bytes; the floor is 16). The same string rebuilds the same world.
    """
    return hashlib.sha256(f"investigators-world-sweep-v1:{i}".encode()).hexdigest()


@pytest.mark.parametrize("i", range(1000))
def test_one_thousand_seeded_worlds_validate(i: int) -> None:
    seed = _world_sweep_seed(i)
    streams = RandomStreams(seed)
    w = build_world(streams)
    w = with_teammate_assignments(
        streams, w, [("JT", w.assigned[0]), ("PM", (set(range(1, 9)) - set(w.assigned)).pop())]
    )
    assert validate_world(w) == [], f"seed {seed}"


def test_invariant7_seat_holder_independence() -> None:
    """Across seeds, the seat holding each decisive team-dependent record must be ~50/50
    and uncorrelated with the (independent) ratings seat draw — INV-0510 data for D02."""
    holders: Counter[str] = Counter()
    joint: Counter[tuple[str, str]] = Counter()
    n = 1000
    for i in range(n):
        streams = RandomStreams(f"{i:064x}")
        w = build_world(streams)
        rating_seat = "JT" if streams.coin("ratings", "higher_seat") else "PM"
        for _n, rid in w.participant_decisive.items():
            holder = next(s for s in ("JT", "PM") if rid in w.holdings[s])
            holders[holder] += 1
            joint[(holder, rating_seat)] += 1
    total = sum(holders.values())
    assert holders["JT"] / total == pytest.approx(0.5, abs=0.06)
    for holder in ("JT", "PM"):
        same = joint[(holder, holder)] / holders[holder]
        assert same == pytest.approx(0.5, abs=0.08)


def test_golden_seed_hand_audit() -> None:
    """Pinned golden case (seed 'aa'*32): any generator drift changes these values and
    forces a hand re-audit (INV-0509 hand-audited case)."""
    w = build_world(RandomStreams("aa" * 32))
    assert w.assigned == (4, 5, 6, 7)
    assert {n: w.role_of[n] for n in w.assigned} == {
        4: "own",
        5: "team",
        6: "team",
        7: "own",
    }
    assert {c.num: c.truth for c in w.crates} == {
        1: "The Sawmill",
        2: "Fairground Sheds",
        3: "Blackwell Storage",
        4: "Fairground Sheds",
        5: "Fairground Sheds",
        6: "The Marina",
        7: "Old Cannery",
        8: "Old Cannery",
    }
    assert w.participant_decisive == {5: "C5-12", 6: "C6-12"}
    # audit: the decisive records are teammates' entries describing truth arrival
    e5 = w.records["C5-12"]
    assert e5.event_type == "arrival" and e5.hideout == "Fairground Sheds"
    assert "C5-12" in (set(w.holdings["JT"]) | set(w.holdings["PM"]))
    assert "C5-12" not in set(w.holdings["participant"])
