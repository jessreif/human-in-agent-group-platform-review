"""Planners that turn a resolved policy plus session random streams into realized artifacts
(INV-0203, INV-0207; spec §4.2, §5.1, §7.1 invariant 7).

Each planner uses exactly one named stream and stable keys:

- allocation           ``assignment``  (batch_id, block_index)
- world draws (WP05)   ``world``
- initial file order   ``order``       ("file_order",)
- rating-seat mapping  ``ratings``     ("higher_seat",)
- scripted requests    ``schedules``   ("unassigned_order",), ("assigned_order",), ("seats", ..)
- per-job timing       ``jobs``        (response_class, logical_job_key, source, component)

Because draws are keyed, the arm and the participant's own behavior (how many messages, which
requests) cannot change the world, the schedule, the rating seat, or another job's timing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.manifests.schema import SEATS, Distribution, Seat
from app.domain.rng import RandomStreams
from app.timing.distributions import Draw, sample
from app.timing.mechanisms import SampledComponents
from app.timing.policy import ResolvedClassPolicy, ResolvedPolicy


class PlanningError(ValueError):
    pass


@dataclass(frozen=True)
class RatingSeats:
    values: dict[str, int]
    higher_seat: Seat


def plan_rating_seats(policy: ResolvedPolicy, streams: RandomStreams) -> RatingSeats:
    """TP pressure values: the higher value goes to a seat drawn by an independent fair coin on
    the ``ratings`` stream, never correlated with world or schedule draws (invariant 7)."""
    manipulation = policy.manipulation
    if manipulation.get("kind") != "pressure_values":
        raise PlanningError(f"{policy.manifest_id} has no pressure values")
    low, high = manipulation["teammate_values"]
    higher: Seat = "JT" if streams.coin("ratings", "higher_seat") else "PM"
    lower: Seat = "PM" if higher == "JT" else "JT"
    return RatingSeats(values={higher: high, lower: low}, higher_seat=higher)


@dataclass(frozen=True)
class ScriptedRequestPlan:
    index: int
    at_active_ms: int
    seat: Seat
    crate: int
    target: str


def plan_scripted_requests(
    policy: ResolvedPolicy,
    streams: RandomStreams,
    *,
    assigned: Sequence[int],
    unassigned: Sequence[int],
    own_assigned: Sequence[int] = (),
    held_crates: Mapping[str, frozenset[int]] | None = None,
    seat_assignments: Mapping[Seat, Sequence[int]] | None = None,
) -> tuple[ScriptedRequestPlan, ...]:
    """Demo rule (REF-REQ-01): unassigned targets are distinct crates from a shuffled unassigned
    list, assigned targets come from a shuffled assigned list, seats per the profile policy.

    ``assigned_own`` (2026-10-10 revision): the ask targets one of the
    participant's own-file assigned crates (``own_assigned``) and the asking
    seat must hold no records on it — the earlier ``assigned`` target let a
    seat ask about a team-dependent crate it already held the truth records
    for. ``held_crates`` maps each seat to the crates it holds; when the drawn
    seat covers every candidate, the other seat asks a crate it holds nothing
    on instead."""
    sr = policy.scripted_requests
    times: list[int] = sr["times_ms"]
    seat_plan: list[dict[str, Any]] | None = sr.get("seat_plan")
    if seat_plan is not None:
        # UX v7 (2026-10-11): each slot names its seat and an ordinal into that
        # seat's own dealt assignment — "JT asks about his lowest-# crate". The
        # asks are always on the asker's beat, so no pools or seat draws are
        # consumed. ``seat_assignments`` is dealt in worlds.ensure_world.
        if seat_assignments is None:
            raise PlanningError("seat_plan needs dealt seat assignments")
        seat_plans: list[ScriptedRequestPlan] = []
        for i, (at, slot) in enumerate(zip(times, seat_plan, strict=True)):
            seat: Seat = slot["seat"]
            crates = list(seat_assignments[seat])
            pick = slot["pick"]
            if pick >= len(crates) or pick < -len(crates):
                raise PlanningError(f"seat_plan pick {pick} out of range for {seat}")
            seat_plans.append(ScriptedRequestPlan(i, at, seat, crates[pick], "seat_plan"))
        return tuple(seat_plans)
    targets: list[str] = sr["targets"]
    pools = {
        "unassigned": list(streams.shuffle("schedules", sorted(unassigned), "unassigned_order")),
        "assigned": list(streams.shuffle("schedules", sorted(assigned), "assigned_order")),
        "assigned_own": list(
            streams.shuffle("schedules", sorted(own_assigned), "assigned_own_order")
        ),
    }
    if targets.count("assigned_own") > len(pools["assigned_own"]):
        raise PlanningError("not enough own-file crates for the scripted schedule")
    for kind in ("unassigned", "assigned"):
        if targets.count(kind) > len(pools[kind]):
            raise PlanningError(f"not enough {kind} crates for the scripted schedule")
    if sr["seat_policy"] == "shuffle_fixed_multiset":
        multiset: list[Seat] = list(sr["seat_multiset"])
    else:  # coin_majority_shuffle: the majority seat is itself a fair coin
        majority: Seat = "JT" if streams.coin("schedules", "seats", "majority") else "PM"
        minority: Seat = "PM" if majority == "JT" else "JT"
        n = len(times)
        multiset = [majority] * (n - n // 2) + [minority] * (n // 2)
    seats = streams.shuffle("schedules", multiset, "seats", "order")
    held = held_crates or {}
    used_own: list[int] = []
    plans: list[ScriptedRequestPlan] = []
    for i, (at, target) in enumerate(zip(times, targets, strict=True)):
        seat = seats[i]
        if target == "assigned_own":
            # Zero-hold preference: the asking seat may not hold records on the
            # crate it asks about. Preference order, all in keyed pool order —
            # an unused crate the drawn seat doesn't hold, then an unused crate
            # the OTHER seat doesn't hold (seat swap), then any unused own
            # crate, then the pool head (never a fresh draw).
            zero = [
                c
                for c in pools["assigned_own"]
                if c not in used_own and c not in held.get(seat, frozenset())
            ]
            if zero:
                crate = zero[0]
            else:
                other: Seat = "PM" if seat == "JT" else "JT"
                zero_other = [
                    c
                    for c in pools["assigned_own"]
                    if c not in used_own and c not in held.get(other, frozenset())
                ]
                if zero_other:
                    seat = other
                    crate = zero_other[0]
                else:
                    crate = next(
                        (c for c in pools["assigned_own"] if c not in used_own),
                        pools["assigned_own"][0],
                    )
            used_own.append(crate)
        else:
            cursor_key = "unassigned" if target == "unassigned" else "assigned"
            cursor = sum(1 for p in plans if p.target == target)
            crate = pools[cursor_key][cursor]
        plans.append(ScriptedRequestPlan(i, at, seat, crate, target))
    return tuple(plans)


def plan_allocation_block(
    streams: RandomStreams, arms: Sequence[str], block_size: int, *, batch_id: str, block: int
) -> tuple[str, ...]:
    """One permuted block of 1:1 assignments (spec §5.1). The DB lock and slot reservation are
    WP04 (INV-0403); this only fixes which arm each slot of the block receives."""
    if block_size % len(arms):
        raise PlanningError("block size must be a multiple of the arm count")
    slots = [arm for arm in arms for _ in range(block_size // len(arms))]
    return streams.shuffle("assignment", slots, batch_id, block)


def plan_file_order(streams: RandomStreams, record_ids: Sequence[str]) -> tuple[str, ...]:
    return streams.shuffle("order", list(record_ids), "file_order")


@dataclass(frozen=True)
class JobDraws:
    components: SampledComponents
    draws: dict[str, Draw]


def _sum_draws(
    streams: RandomStreams, dists: Sequence[Distribution], key: tuple[str, ...]
) -> tuple[int, dict[str, Draw]]:
    total = 0
    draws: dict[str, Draw] = {}
    for i, dist in enumerate(dists):
        label = f"delay{i}"

        def source(part: str, lab: str = label) -> float:
            return streams.uniform("jobs", *key, lab, part)

        draw = sample(dist, source)
        draws[label] = draw
        total += draw.ms
    return total, draws


def plan_job_draws(
    policy: ResolvedClassPolicy, streams: RandomStreams, *, logical_job_key: str
) -> JobDraws:
    """Sample every timing component for one logical job, keyed so retries, arms and unrelated
    jobs never change the result (spec §4.2 last paragraph, §6.2 "sampled once")."""
    key = (policy.response_class, logical_job_key, policy.source)
    delay_ms, draws = _sum_draws(streams, policy.delay, key)

    def one(name: str, dist: Distribution | None) -> int:
        if dist is None:
            return 0
        draw = sample(dist, lambda part: streams.uniform("jobs", *key, name, part))
        draws[name] = draw
        return draw.ms

    components = SampledComponents(
        delay_ms=delay_ms,
        platform_ms=one("platform", policy.platform_delay),
        think_ms=one("think", policy.think),
        floor_ms=one("floor", policy.floor),
    )
    return JobDraws(components, draws)


def seat_for_unprompted_ack(policy: ResolvedPolicy, streams: RandomStreams, share_key: str) -> Seat:
    ack = policy.teammates.get("acknowledgments")
    if ack is None:
        raise PlanningError(f"{policy.manifest_id} has no acknowledgment policy")
    weights = ack["unprompted_seat_weights"]
    options: list[tuple[Seat, float]] = [(seat, float(weights[seat])) for seat in SEATS]
    return streams.weighted("jobs", options, "acknowledgment", share_key, "seat")
