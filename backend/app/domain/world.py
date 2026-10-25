"""Deterministic world generation: the server port of the demos' ``buildWorld``
(INV-0501/0502/0503; spec §2.1, §7.1).

Every ``Math.random()`` in the demo maps to a keyed draw on the ``world`` stream, so
a session seed plus stable draw keys reproduces the identical world - and replay never
needs this module to re-run (WP03 persists the full snapshot).

World model (spec §7.1; defaults — ``WorldSpec`` overrides crate count, overlap,
and the participant intel budget for the 2026-10-15 crate-condition experiment):

- 8 crates, 6 hideouts; 4 crates assigned to the participant in ascending order -
  exactly two ``own`` (solvable from the initial file) and two ``team`` (decisive
  evidence held only by teammates after the participant's cutoff).
- Each crate walks three distinct hideouts (leg A -> leg B -> truth) with typed
  timeline events: ``arrival``/``still_at``/``departure``/``transit``. Truth is the
  last walk leg, never recovered from prose.
- Own-file crates complete their final move yesterday, before any own-file cutoff;
  team-dependent crates move this morning, after every team-dependent cutoff.
- Unassigned crates give the participant their last two late entries (useful to a
  teammate, useless to the participant); the rest split between JT and PM by coin.
- JT/PM each receive an internally consistent four-crate assignment with permitted
  overlap (a specified completion of the demo model - spec §7.1 last paragraph):
  the crates a seat scripted-requests are theirs, the rest fill from a seeded
  shuffle. Teammate assignments never appear in participant payloads.

The chronology oracle (:func:`location_report`) distinguishes confirmed
arrival/still-at evidence from departures and transit: a departure revokes the
confirmed location and transit names only a heading, not a place.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Literal, cast

from app.domain.manifests.schema import SEATS, Seat
from app.domain.rng import RandomStreams

GENERATOR_ID = "investigators.world/demo_v1"
GENERATOR_OVERLAP_ID = "investigators.world/overlap_v1"

HIDEOUTS: tuple[str, ...] = (
    "The Sawmill",
    "The Marina",
    "Blackwell Storage",
    "Old Cannery",
    "Fairground Sheds",
    "Ridgeline Bunker",
)
# 2026-10-15: extended to 12 for the no-overlap condition; the first eight are
# pinned — legacy ``demo_v1`` worlds index crates 1-8 and must stay identical.
CRATE_CONTENTS: tuple[str, ...] = (
    "laptops",
    "smart watches",
    "gaming consoles",
    "TVs",
    "headphones",
    "cameras",
    "tablets",
    "power tools",
    "sneakers",
    "drones",
    "e-bikes",
    "VR headsets",
)
N_CRATES = 8
N_ASSIGNED = 4
N_TEAMMATE_CRATES = 4  # spec §7.1 completion: each teammate is also assigned four

OverlapLevel = Literal["independent", "none", "some", "full"]


@dataclass(frozen=True)
class WorldSpec:
    """Shape of a generated world (2026-10-15 crate-condition experiment).

    ``independent`` + ``n_crates=8`` reproduces the legacy ``demo_v1`` world
    byte-identically; the overlap levels deal teammate assignments under
    explicit partition/partial/full constraints, and
    ``participant_intel_budget`` trims the participant's file to a thin-file
    count once scripted requests are planned."""

    n_crates: int = N_CRATES
    overlap: OverlapLevel = "independent"
    participant_intel_budget: int | None = None
    generator: str = GENERATOR_ID


T_ARRIVE = (
    "Field team: Crate {n} arriving at {h}.",
    "Surveillance photo: Crate {n} at the loading dock of {h}.",
    "Field team: {c} unloaded at {h}.",
    "Warehouse camera confirms Crate {n} offloaded at {h}.",
)
T_STILL = (
    "Informant: {c} still on site at {h}.",
    "Field team: Crate {n} ({c}) at {h}, confirmed by walk-by.",
    "Warehouse camera: {c} still at {h}.",
    "Anonymous tip: {c} have not moved from {h}.",
    "Field team: activity at {h}, Crate {n} still on site.",
)
T_DEPART = (
    "Traffic camera: a van leaves {h} headed east, Crate {n} believed aboard.",
    "Field team: a pickup departs {h} with the {c} crate aboard.",
    "Drone footage: Crate {n} ({c}) is loaded onto a vehicle at {h}.",
)
T_TRANSIT = (
    "Traffic camera: vehicle carrying the {c} leaves {h} heading toward the {to} area.",
    "GPS ping: vehicle linked to Crate {n} between {h} and {to}.",
)

EventType = Literal["arrival", "still_at", "departure", "transit"]
CrateRole = Literal["own", "team", "other"]


def _mins(day: int, hour: int, minute: int) -> int:
    return day * 1440 + hour * 60 + minute


def _stamp(day: int, hour: int, minute: int) -> str:
    ap = "AM" if hour < 12 else "PM"
    h = hour % 12 or 12
    label = "Today" if day == 0 else "Yesterday" if day == -1 else f"{-day} days ago"
    return f"{label}, {h}:{minute:02d} {ap}"


def _fill(template: str, n: int, c: str, h: str, to: str | None = None) -> str:
    return (
        template.replace("{n}", str(n))
        .replace("{c}", c)
        .replace("{h}", h)
        .replace("{to}", (to or "").replace("The ", ""))
    )


@dataclass(frozen=True)
class TimelineEntry:
    record_id: str
    crate: int
    ts: int  # minutes relative to "today" (day 0), the demo's clock model
    stamp: str
    body: str
    event_type: EventType
    hideout: str  # place named by the event (transit: origin)
    toward: str | None  # transit only: destination hint
    leg: int
    ordinal: int  # chronological position within the crate's timeline


@dataclass(frozen=True)
class CrateWorld:
    num: int
    contents: str
    truth: str
    walk: tuple[str, str, str]
    role: CrateRole
    entries: tuple[TimelineEntry, ...]


@dataclass(frozen=True)
class World:
    world_id: uuid.UUID
    spec: WorldSpec
    assigned: tuple[int, ...]  # ascending
    role_of: Mapping[int, CrateRole]
    cutoff_of: Mapping[int, int]  # assigned crates only
    crates: tuple[CrateWorld, ...]
    records: Mapping[str, TimelineEntry]
    holdings: Mapping[str, tuple[str, ...]]  # "participant" | Seat -> record_ids
    teammate_assignments: Mapping[Seat, tuple[int, ...]]
    requested_crates: tuple[int, ...]
    never_requested: tuple[int, ...]  # unassigned crates no scripted request targets
    participant_decisive: Mapping[int, str]  # assigned crate -> decisive record_id
    trimmed_from_file: tuple[str, ...] = ()  # thin-file condition: redealt record_ids
    # no-overlap condition: the unassigned crates whose last-two-late records
    # stay in the participant file (D4/audit W03). Empty = the legacy rule
    # (every unassigned crate covered).
    covered_unassigned: tuple[int, ...] = ()

    def truth_of(self, crate: int) -> str:
        return self.crates[crate - 1].truth


# ----- generator ---------------------------------------------------------------------------------


def _pick(streams: RandomStreams, options: Sequence[str], *key: str | int) -> str:
    return options[int(streams.uniform("world", *key) * len(options))]


def _build_timeline(
    streams: RandomStreams, num: int, contents: str, role: Literal["own", "team"]
) -> CrateWorld:
    """One crate's three-leg walk. 'own' finishes the final move yesterday; 'team' (also
    used for unassigned crates) holds at leg B until this morning (demo buildTimeline)."""
    walk = streams.shuffle("world", HIDEOUTS, "walk", num)[:3]
    truth = walk[2]
    out: list[TimelineEntry] = []

    seq = 0

    def add(
        day: int,
        hour: int,
        minute: int,
        templates: Sequence[str],
        event_type: EventType,
        hideout: str,
        leg: int,
        toward: str | None = None,
    ) -> None:
        nonlocal seq
        seq += 1
        body = _fill(_pick(streams, templates, "tpl", num, seq), num, contents, hideout, toward)
        out.append(
            TimelineEntry(
                record_id=f"C{num}-{seq}",
                crate=num,
                ts=_mins(day, hour, minute),
                stamp=_stamp(day, hour, minute),
                body=body,
                event_type=event_type,
                hideout=hideout,
                toward=toward,
                leg=leg,
                ordinal=seq,
            )
        )

    a, b = walk[0], walk[1]
    # leg A: three and two days ago
    add(-3, 9, 0, T_ARRIVE, "arrival", a, 0)
    add(-3, 17, 0, T_STILL, "still_at", a, 0)
    add(-2, 10, 0, T_STILL, "still_at", a, 0)
    add(-2, 19, 0, T_DEPART, "departure", a, 0)
    # leg B
    add(-1, 6, 30, T_TRANSIT, "transit", a, 1, toward=b)
    add(-1, 8, 15, T_ARRIVE, "arrival", b, 1)
    if role == "own":
        # the final move completes yesterday so the participant's file can carry it
        add(-1, 11, 0, T_DEPART, "departure", b, 2)
        add(-1, 12, 30, T_TRANSIT, "transit", b, 2, toward=truth)
        add(-1, 14, 0, T_ARRIVE, "arrival", truth, 2)
        add(-1, 18, 0, T_STILL, "still_at", truth, 2)
        add(0, 9, 30, T_STILL, "still_at", truth, 3)
        add(0, 13, 0, T_STILL, "still_at", truth, 3)
        add(0, 16, 30, T_STILL, "still_at", truth, 3)
    else:
        # stays at leg B through this morning, then moves to truth
        add(-1, 13, 0, T_STILL, "still_at", b, 1)
        add(-1, 19, 0, T_STILL, "still_at", b, 1)
        add(0, 8, 45, T_STILL, "still_at", b, 1)
        add(0, 11, 30, T_DEPART, "departure", b, 2)
        add(0, 12, 15, T_TRANSIT, "transit", b, 2, toward=truth)
        add(0, 14, 0, T_ARRIVE, "arrival", truth, 2)
        add(0, 16, 0, T_STILL, "still_at", truth, 2)
        add(0, 17, 30, T_STILL, "still_at", truth, 2)
    entries = tuple(sorted(out, key=lambda e: e.ts))
    return CrateWorld(num, contents, truth, (a, b, truth), role, entries)


_LATE_CUTOFF = _mins(0, 12, 0)  # demo: unassigned crates give the last two ts >= noon entries
_OWN_CUTS = (_mins(-1, 20, 0), _mins(0, 15, 0))  # yesterday 8 PM / today 3 PM
_TEAM_CUTS = (_mins(-1, 20, 0), _mins(0, 9, 30))  # yesterday 8 PM / today 9:30 AM


def build_world(streams: RandomStreams, spec: WorldSpec | None = None) -> World:
    """The demo ``buildWorld`` port. Draw order is fixed by construction - every draw is
    keyed, so callers cannot perturb the result by consuming draws elsewhere.

    ``spec`` (2026-10-15): ``overlap_v1`` builds carry the condition shape —
    the legacy ``demo_v1`` call (``spec=None``) produces the identical 8-crate
    world it always did."""
    if spec is None:
        spec = WorldSpec()
    assigned = tuple(
        sorted(streams.shuffle("world", range(1, spec.n_crates + 1), "assigned")[:N_ASSIGNED])
    )
    roles = streams.shuffle("world", ["own", "own", "team", "team"], "roles")
    own_cuts = streams.shuffle("world", _OWN_CUTS, "own_cuts")
    team_cuts = streams.shuffle("world", _TEAM_CUTS, "team_cuts")
    role_of: dict[int, CrateRole] = {}
    cutoff_of: dict[int, int] = {}
    oi = ti = 0
    for i, n in enumerate(assigned):
        role_of[n] = cast(CrateRole, roles[i])
        if roles[i] == "own":
            cutoff_of[n] = own_cuts[oi]
            oi += 1
        else:
            cutoff_of[n] = team_cuts[ti]
            ti += 1
    for n in range(1, spec.n_crates + 1):
        if n not in role_of:
            role_of[n] = "other"

    crates = tuple(
        _build_timeline(streams, n, CRATE_CONTENTS[n - 1], "own" if role_of[n] == "own" else "team")
        for n in range(1, spec.n_crates + 1)
    )
    records = {e.record_id: e for c in crates for e in c.entries}
    holdings: dict[str, list[str]] = {"participant": [], "JT": [], "PM": []}
    for crate in crates:
        if crate.num in cutoff_of:
            cut = cutoff_of[crate.num]
            for e in crate.entries:
                if e.ts <= cut:
                    holdings["participant"].append(e.record_id)
                else:
                    seat: Seat = "JT" if streams.coin("world", "seat", e.record_id) else "PM"
                    holdings[seat].append(e.record_id)
        else:
            late = [e for e in crate.entries if e.ts >= _LATE_CUTOFF][-2:]
            for e in crate.entries:
                if e in late:
                    holdings["participant"].append(e.record_id)
                else:
                    seat = "JT" if streams.coin("world", "seat", e.record_id) else "PM"
                    holdings[seat].append(e.record_id)
    decisive = {
        n: decisive_record_id(tuple(records.values()), crates[n - 1].truth, n)
        for n in assigned
        if role_of[n] == "team"
    }
    return World(
        world_id=uuid.uuid4(),
        spec=spec,
        assigned=assigned,
        role_of=role_of,
        cutoff_of=cutoff_of,
        crates=crates,
        records=records,
        holdings={k: tuple(v) for k, v in holdings.items()},
        teammate_assignments={},  # filled by with_teammate_assignments once requests are planned
        requested_crates=(),
        never_requested=(),
        participant_decisive=decisive,
    )


def deal_teammate_assignments(
    streams: RandomStreams,
    spec: WorldSpec | None = None,
    assigned: Sequence[int] = (),
) -> dict[Seat, tuple[int, ...]]:
    """UX v7 ``seat_plan`` builds: deal each seat's four-crate assignment before
    planning so the scripted asks pick ordinals off the seat's own beat. Uses the
    same keyed ``seat_assign`` shuffle the legacy fill uses, so the dealt set
    consumes the identical ``world``-stream draw (no extra seed coupling).

    Overlap levels (2026-10-15): ``independent`` keeps the legacy per-seat
    shuffle; ``none`` partitions the 12 crates so the three assignments are
    pairwise disjoint; ``full`` deals every seat the same four crates;
    ``some`` keyed-rejection-samples both seats' four-sets until every
    pairwise intersection holds 1-2 crates (D3 — participant↔JT,
    participant↔PM, and JT↔PM), falling back to a bounded-valid template."""
    if spec is None:
        spec = WorldSpec(n_crates=N_CRATES)
    crates = [n for n in range(1, spec.n_crates + 1)]
    pset = frozenset(assigned)
    if spec.overlap == "full":
        shared = tuple(sorted(pset))
        return {seat: shared for seat in SEATS}
    if spec.overlap == "none":
        # Partition: the crates the participant was not dealt (12 - 4 = 8) split
        # 4/4 between the seats — all three assignments pairwise disjoint.
        pool = [n for n in crates if n not in pset]
        deal = streams.shuffle("world", pool, "seat_assign", "partition")
        return {
            "JT": tuple(sorted(deal[:N_TEAMMATE_CRATES])),
            "PM": tuple(sorted(deal[N_TEAMMATE_CRATES:])),
        }
    if spec.overlap == "some":
        # Locked bounds (D3): each of the three pairwise intersections must
        # hold 1-2 crates — |P∩JT|, |P∩PM|, and |JT∩PM|. The bounds imply
        # pairwise-distinct assignments (an equal pair would intersect at 4).
        for attempt in range(64):
            dealt = {
                seat: tuple(
                    sorted(
                        streams.shuffle("world", crates, "seat_assign", seat, "some", attempt)[
                            :N_TEAMMATE_CRATES
                        ]
                    )
                )
                for seat in SEATS
            }
            jt, pm = set(dealt["JT"]), set(dealt["PM"])
            if 1 <= len(jt & pset) <= 2 and 1 <= len(pm & pset) <= 2 and 1 <= len(jt & pm) <= 2:
                return dealt
        # Deterministic fallback preserving the dealt participant assignment
        # (audit W02): JT = {p1,p2,f1,f2}, PM = {p2,p3,f2,f3} →
        # |P∩JT| = |P∩PM| = |JT∩PM| = 2 on every seed.
        p = sorted(assigned)
        free = [n for n in crates if n not in pset]
        return {
            "JT": tuple(sorted([p[0], p[1], free[0], free[1]])),
            "PM": tuple(sorted([p[1], p[2], free[1], free[2]])),
        }
    return {
        seat: tuple(
            sorted(streams.shuffle("world", crates, "seat_assign", seat)[:N_TEAMMATE_CRATES])
        )
        for seat in SEATS
    }


def with_teammate_assignments(
    streams: RandomStreams,
    world: World,
    requested: Sequence[tuple[Seat, int]],
    *,
    dealt: Mapping[Seat, tuple[int, ...]] | None = None,
) -> World:
    """Attach the specified-completion teammate assignments (spec §7.1 last paragraph):
    each seat is assigned four crates - every crate it scripted-requests plus a seeded
    fill - with permitted overlap and no participant visibility. ``requested`` is the
    (seat, crate) list from :func:`schedules.plan_scripted_requests`.

    ``dealt`` (UX v7 seat_plan builds): the seat assignments were dealt before
    planning — the asks were drawn FROM each seat's own list, so the dealt set
    is authoritative and every requested crate is already on it."""
    if dealt is not None:
        assignments = {seat: sorted(dealt[seat]) for seat in SEATS}
        for seat, crate in requested:
            if crate not in assignments[seat]:
                raise ValueError(f"seat_plan asked {seat} for crate {crate} not in its deal")
    else:
        assignments = {seat: [] for seat in SEATS}
        for seat, crate in requested:
            if crate not in assignments[seat]:
                assignments[seat].append(crate)
        for seat in SEATS:
            fill = streams.shuffle(
                "world",
                [n for n in range(1, world.spec.n_crates + 1)],
                "seat_assign",
                seat,
            )
            for n in fill:
                if len(assignments[seat]) >= N_TEAMMATE_CRATES:
                    break
                if n not in assignments[seat]:
                    assignments[seat].append(n)
            assignments[seat].sort()
    requested_crates = tuple(dict.fromkeys(c for _, c in requested))
    never = tuple(
        n
        for n in range(1, world.spec.n_crates + 1)
        if n not in world.assigned and n not in requested_crates
    )
    return replace(
        world,
        teammate_assignments={s: tuple(v) for s, v in assignments.items()},
        requested_crates=requested_crates,
        never_requested=never,
    )


def with_unassigned_coverage(
    streams: RandomStreams,
    world: World,
    covered: Sequence[int],
) -> World:
    """No-overlap condition (D4): limit which unassigned crates the participant
    holds intel on. Records the participant holds on uncovered unassigned
    crates are redealt to seats by keyed coin — every record keeps exactly
    one holder and the file shrinks to the designed coverage.

    ``covered`` must be a subset of the unassigned crates; it should include
    every unassigned crate a scripted ask targets (the ``ensure_world`` order
    guarantees that) so asks stay answerable."""
    covered_set = frozenset(covered)
    unassigned = [n for n in range(1, world.spec.n_crates + 1) if n not in world.assigned]
    if covered_set == frozenset(unassigned):
        return replace(world, covered_unassigned=tuple(sorted(covered_set)))
    if not covered_set <= frozenset(unassigned):
        raise ValueError("covered_unassigned must be unassigned crates")
    holdings: dict[str, list[str]] = {k: list(v) for k, v in world.holdings.items()}
    kept: list[str] = []
    for rid in holdings["participant"]:
        crate = world.records[rid].crate
        if crate in world.assigned or crate in covered_set:
            kept.append(rid)
        else:
            seat: Seat = "JT" if streams.coin("world", "seat", "coverage", rid) else "PM"
            holdings[seat].append(rid)
    holdings["participant"] = kept
    return replace(
        world,
        holdings={k: tuple(v) for k, v in holdings.items()},
        covered_unassigned=tuple(sorted(covered_set)),
    )


def with_intel_budget(
    streams: RandomStreams,
    world: World,
    requested: Sequence[int],
) -> World:
    """Thin-file condition (``participant_intel_budget``): trim the participant's
    file down to the pinned count and redeal the dropped records to seats.

    Keep rule — the most recent held records per assigned crate plus single
    records on unassigned (teammate) crates, preferring the crates scripted
    asks target so the participant can still answer them. Full-overlap worlds
    have no unassigned crates, so the whole budget lands on the four assigned
    crates. Keeping the most-recent entries retains every own-file
    truth-confirmer (the own-crate confirming entries are late by
    construction) and never leaks a team-crate confirmer (none is held).

    Dropped records are redealt to seats by the same keyed coin the initial
    deal uses, so every record still has exactly one holder."""
    budget = world.spec.participant_intel_budget
    if budget is None:
        return world
    held = list(world.holdings["participant"])
    if len(held) <= budget:
        return world

    held_entries = [world.records[rid] for rid in held]
    # Under ``none`` the coverage finalization already limited the file to
    # ``covered_unassigned`` — singles can only land on crates the participant
    # still holds records on.
    unassigned = (
        list(world.covered_unassigned)
        if world.covered_unassigned
        else [n for n in range(1, world.spec.n_crates + 1) if n not in world.assigned]
    )

    if world.spec.overlap == "full" or not unassigned:
        # Everything lands on the assigned crates: budget/4 most recent per crate.
        per_crate = max(1, budget // N_ASSIGNED)
        keep_ids: list[str] = []
        for n in world.assigned:
            mine = sorted((e for e in held_entries if e.crate == n), key=lambda e: e.ts)
            keep_ids.extend(e.record_id for e in mine[-per_crate:])
    else:
        # Reserve ``singles`` slots for teammate-crate coverage, preferring the
        # unassigned crates scripted asks target (ask order preserved).
        singles = min(len(unassigned), max(0, budget - N_ASSIGNED * 4))
        covered: list[int] = []
        for n in requested:
            if n in unassigned and n not in covered:
                covered.append(n)
            if len(covered) >= singles:
                break
        for n in unassigned:
            if len(covered) >= singles:
                break
            if n not in covered:
                covered.append(n)
        per_crate = (budget - singles) // N_ASSIGNED
        keep_ids = []
        for n in world.assigned:
            mine = sorted((e for e in held_entries if e.crate == n), key=lambda e: e.ts)
            keep_ids.extend(e.record_id for e in mine[-per_crate:])
        for n in covered[:singles]:
            mine = sorted((e for e in held_entries if e.crate == n), key=lambda e: e.ts)
            if mine:
                keep_ids.append(mine[-1].record_id)

    keep = set(keep_ids)
    dropped = [rid for rid in held if rid not in keep]
    holdings: dict[str, list[str]] = {k: list(v) for k, v in world.holdings.items()}
    holdings["participant"] = [rid for rid in held if rid in keep]
    for rid in dropped:
        seat: Seat = "JT" if streams.coin("world", "seat", "trim", rid) else "PM"
        holdings[seat].append(rid)
    return replace(
        world,
        holdings={k: tuple(v) for k, v in holdings.items()},
        trimmed_from_file=tuple(dropped),
    )


# ----- chronology oracle (INV-0503) ---------------------------------------------------------------


class LocationState(StrEnum):
    CONFIRMED_AT = "confirmed_at"  # last word: arrival or still_at
    DEPARTED = "departed"  # left the last confirmed place; whereabouts unknown
    IN_TRANSIT = "in_transit"  # between two named places; neither is confirmed


@dataclass(frozen=True)
class LocationReport:
    state: LocationState
    hideout: str  # confirmed place (DEPARTED: the place left; IN_TRANSIT: the origin)
    toward: str | None
    evidence_ts: int
    evidence_record_id: str


def location_report(
    entries: Sequence[TimelineEntry], at_ts: int | None = None
) -> LocationReport | None:
    """Latest location the evidence supports at ``at_ts`` (or overall), with the event
    type kept explicit: departures/transit never count as confirmed arrivals."""
    seen = [
        e
        for e in sorted(entries, key=lambda e: (e.ts, e.ordinal))
        if at_ts is None or e.ts <= at_ts
    ]
    report: LocationReport | None = None
    for e in seen:
        if e.event_type in ("arrival", "still_at"):
            report = LocationReport(LocationState.CONFIRMED_AT, e.hideout, None, e.ts, e.record_id)
        elif e.event_type == "departure":
            if report is not None and report.hideout == e.hideout:
                report = LocationReport(LocationState.DEPARTED, e.hideout, None, e.ts, e.record_id)
        else:  # transit: a heading, not a place
            if report is not None:
                report = LocationReport(
                    LocationState.IN_TRANSIT, e.hideout, e.toward, e.ts, e.record_id
                )
    return report


def decisive_record_id(entries: Sequence[TimelineEntry], truth: str, crate: int) -> str:
    """The earliest record confirming the crate at its truth hideout."""
    candidates = [
        e
        for e in entries
        if e.crate == crate and e.hideout == truth and e.event_type in ("arrival", "still_at")
    ]
    if not candidates:
        raise ValueError(f"crate {crate}: no record confirms truth {truth!r}")
    return min(candidates, key=lambda e: (e.ts, e.ordinal)).record_id


# #


def validate_world(world: World) -> list[str]:
    """Return the list of invariant violations (empty = valid). INV-0509 runs this over
    sampled seeds; a non-empty result anywhere fails the world."""
    problems: list[str] = []
    n_crates = world.spec.n_crates
    # 1. shape
    if len(world.crates) != n_crates or len(world.assigned) != N_ASSIGNED:
        problems.append("crate/assignment counts wrong")
    if len(set(world.assigned)) != N_ASSIGNED or world.assigned != tuple(sorted(world.assigned)):
        problems.append("assigned crates are not four distinct ascending IDs")
    roles = [world.role_of[n] for n in world.assigned]
    if roles.count("own") != 2 or roles.count("team") != 2:
        problems.append("assigned roles are not two own-file + two team-dependent")
    # 2/4. solvability
    held_by_p = set(world.holdings["participant"])
    held_by_team = set(world.holdings["JT"]) | set(world.holdings["PM"])
    for n in world.assigned:
        truth = world.truth_of(n)
        confirmers = {
            e.record_id
            for e in world.crates[n - 1].entries
            if e.hideout == truth and e.event_type in ("arrival", "still_at")
        }
        if world.role_of[n] == "own":
            if not (confirmers & held_by_p):
                problems.append(f"own-file crate {n}: no truth-confirming record in own file")
        else:
            if confirmers & held_by_p:
                problems.append(f"team crate {n}: truth leaked into the participant's file")
            if not (confirmers & held_by_team):
                problems.append(f"team crate {n}: no teammate holds truth-confirming records")
            if n not in world.participant_decisive:
                problems.append(f"team crate {n}: decisive record not identified")
            # the structured-request route exists (a teammate holds a confirming record
            # the retrieval rule can return - validated end-to-end in INV-0506 tests)
    # 3. internal consistency
    for c in world.crates:
        if c.truth != c.walk[2] or len(set(c.walk)) != 3:
            problems.append(f"crate {c.num}: walk/truth inconsistent")
        if [e.ts for e in c.entries] != sorted(e.ts for e in c.entries):
            problems.append(f"crate {c.num}: timeline out of order")
        rep = location_report(c.entries)
        if rep is None or rep.state is not LocationState.CONFIRMED_AT or rep.hideout != c.truth:
            problems.append(f"crate {c.num}: final chronology does not confirm truth")
        for e in c.entries:
            if e.event_type == "transit" and e.toward != c.walk[e.leg]:
                problems.append(f"crate {c.num}/{e.record_id}: transit target inconsistent")
    # 5. unassigned crates: participant holds exactly the last two late entries
    # each — under ``none`` only on the covered set (audit W03); the thin-file
    # trim supersedes this rule entirely.
    if world.spec.participant_intel_budget is None:
        covered = (
            frozenset(world.covered_unassigned)
            if world.covered_unassigned
            else frozenset(n for n in range(1, world.spec.n_crates + 1) if n not in world.assigned)
        )
        for c in world.crates:
            if c.num in world.assigned:
                continue
            mine = [e for e in c.entries if e.record_id in held_by_p]
            late = [e for e in c.entries if e.ts >= _LATE_CUTOFF][-2:]
            if c.num in covered:
                if mine != late:
                    problems.append(
                        f"unassigned crate {c.num}: participant holdings are not "
                        "the two latest entries"
                    )
            elif mine:
                problems.append(
                    f"uncovered unassigned crate {c.num}: participant holds "
                    f"{len(mine)} records outside the covered set"
                )
        # The covered set must include every unassigned crate a scripted ask
        # targets (asks stay answerable).
        for n in world.requested_crates:
            if n not in world.assigned and n not in covered:
                problems.append(f"asked unassigned crate {n} is outside the covered set")
    else:
        if len(held_by_p) > world.spec.participant_intel_budget:
            problems.append(
                f"thin file exceeds budget: {len(held_by_p)} > "
                f"{world.spec.participant_intel_budget}"
            )
        # every dropped record must still have exactly one (seat) holder
        for rid in world.trimmed_from_file:
            if rid in held_by_p:
                problems.append(f"trimmed record {rid} still in participant file")
    # scripted-request bookkeeping (only meaningful once assignments are attached)
    if world.teammate_assignments:
        for seat in SEATS:
            if len(world.teammate_assignments[seat]) != N_TEAMMATE_CRATES:
                problems.append(f"{seat}: teammate assignment is not four crates")
        pset = frozenset(world.assigned)
        jt = frozenset(world.teammate_assignments["JT"])
        pm = frozenset(world.teammate_assignments["PM"])
        if world.spec.overlap == "none":
            if jt & pm or jt & pset or pm & pset:
                problems.append("no-overlap condition: assignments are not pairwise disjoint")
        elif world.spec.overlap == "full":
            if jt != pset or pm != pset:
                problems.append("full-overlap condition: teammate assignments differ")
        elif world.spec.overlap == "some":
            # Locked bounds (D3/audit W02): all three pairwise intersections
            # must be 1-2 crates — not merely "nonzero and unequal".
            for label, pair in (
                ("P∩JT", pset & jt),
                ("P∩PM", pset & pm),
                ("JT∩PM", jt & pm),
            ):
                if not 1 <= len(pair) <= 2:
                    problems.append(f"some-overlap condition: |{label}| = {len(pair)} outside 1-2")
        for n in world.never_requested:
            if n in world.assigned or n in world.requested_crates:
                problems.append(f"crate {n}: never-requested subset mis-derived")
    # every record has exactly one holder (the thin-file redeal preserves this)
    all_holders = [rid for rids in world.holdings.values() for rid in rids]
    if len(all_holders) != len(set(all_holders)) or set(all_holders) != set(world.records):
        problems.append("record holdings are not a partition of all records")
    return problems
