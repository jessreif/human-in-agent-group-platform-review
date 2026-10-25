"""INV-0207: separate keyed random streams; participant behavior and arm cannot move draws."""

from __future__ import annotations

from collections import Counter

import pytest

from app.domain.rng import STREAMS, DrawLog, RandomStreams, SeedError, new_seed
from app.domain.schedules import (
    plan_allocation_block,
    plan_file_order,
    plan_job_draws,
    plan_rating_seats,
    plan_scripted_requests,
    seat_for_unprompted_ack,
)
from tests.wp02 import OTHER_SEED, SEED, policy

RECORDS = [f"K{c}-{i:02d}" for c in range(1, 9) for i in range(1, 6)]


def test_draws_are_keyed_and_order_independent() -> None:
    a, b = RandomStreams(SEED), RandomStreams(SEED)
    first = [a.uniform("world", "crate", i) for i in range(8)]
    second = [b.uniform("world", "crate", i) for i in reversed(range(8))][::-1]
    assert first == second
    assert all(0.0 <= v < 1.0 for v in first)


def test_streams_are_independent() -> None:
    s = RandomStreams(SEED)
    values = {stream: s.uniform(stream, "same", "key") for stream in STREAMS}
    assert len(set(values.values())) == len(STREAMS)
    assert RandomStreams(OTHER_SEED).uniform("world", "same", "key") != values["world"]


def _session(seed: str, manifest_id: str, arm: str, extra_social_turns: int) -> dict[str, object]:
    """Simulate the draws a session makes; extra social turns stand for chat volume."""
    p = policy(manifest_id, arm)
    streams = RandomStreams(seed)
    out: dict[str, object] = {}
    # interleave participant-driven jobs before, between and after the stimulus draws
    for i in range(extra_social_turns):
        plan_job_draws(p.class_policy("social_reply", "JT"), streams, logical_job_key=f"reply.{i}")
    out["world"] = [streams.uniform("world", "crate", c, "leg") for c in range(1, 9)]
    out["order"] = plan_file_order(streams, RECORDS)
    for i in range(extra_social_turns):
        plan_job_draws(p.class_policy("social_reply", "PM"), streams, logical_job_key=f"late.{i}")
    out["requests"] = plan_scripted_requests(
        p, streams, assigned=(1, 3, 5, 7), unassigned=(2, 4, 6, 8)
    )
    out["higher_seat"] = (
        plan_rating_seats(p, streams).higher_seat if p.study_key.endswith("time_pressure") else None
    )
    out["structured"] = [
        plan_job_draws(
            p.class_policy("structured_record", seat), streams, logical_job_key=f"req.{k}"
        ).components
        for k in range(3)
        for seat in ("JT", "PM")
    ]
    return out


@pytest.mark.parametrize("manifest_id", ["tp_demo_v1", "tp_review_first_v2"])
def test_chat_volume_and_arm_do_not_change_draws(manifest_id: str) -> None:
    quiet_high = _session(SEED, manifest_id, "high", extra_social_turns=0)
    chatty_low = _session(SEED, manifest_id, "low", extra_social_turns=25)
    assert quiet_high == chatty_low
    assert _session(OTHER_SEED, manifest_id, "high", 0) != quiet_high


def test_pe_arm_does_not_change_draws() -> None:
    assert _session(SEED, "pe_pilot_v2", "eval", 0) == _session(SEED, "pe_pilot_v2", "control", 9)


def test_rating_seat_independent_of_request_sender() -> None:
    """Spec §7.1 invariant 7 at the stream level: the higher-rating seat (ratings stream) is
    independent of scripted-request senders (schedules stream). The world-level joint report
    with decisive holders is INV-0510."""
    for manifest_id in ("tp_demo_v1", "tp_review_first_v2"):
        p = policy(manifest_id, "high")
        joint: Counter[tuple[str, str]] = Counter()
        n = 2000
        for i in range(n):
            streams = RandomStreams(f"{i:064x}")
            higher = plan_rating_seats(p, streams).higher_seat
            first_sender = plan_scripted_requests(
                p, streams, assigned=(1, 3, 5, 7), unassigned=(2, 4, 6, 8)
            )[0].seat
            joint[(higher, first_sender)] += 1
        higher_jt = sum(v for (h, _), v in joint.items() if h == "JT") / n
        assert 0.45 < higher_jt < 0.55, (manifest_id, joint)
        for sender in ("JT", "PM"):
            given = [v for (h, s), v in joint.items() if s == sender]
            with_jt = joint[("JT", sender)] / sum(given)
            assert abs(with_jt - higher_jt) < 0.06, (manifest_id, joint)


def test_rating_values_follow_arm() -> None:
    streams = RandomStreams(SEED)
    high = plan_rating_seats(policy("tp_review_first_v2", "high"), streams)
    low = plan_rating_seats(policy("tp_review_first_v2", "low"), RandomStreams(SEED))
    assert high.higher_seat == low.higher_seat
    assert high.values[high.higher_seat] == 7 and low.values[low.higher_seat] == 3
    assert sorted(high.values.values()) == [6, 7]


def test_allocation_blocks_are_balanced() -> None:
    streams = RandomStreams(SEED)
    counts: Counter[str] = Counter()
    for block in range(200):
        slots = plan_allocation_block(streams, ("high", "low"), 2, batch_id="batch-1", block=block)
        assert sorted(slots) == ["high", "low"]
        counts[slots[0]] += 1
    assert 70 < counts["high"] < 130
    with pytest.raises(ValueError):
        plan_allocation_block(streams, ("high", "low"), 3, batch_id="batch-1", block=0)


def test_unprompted_ack_seat_uses_policy_weights() -> None:
    p = policy("pe_demo_v1", "eval")
    streams = RandomStreams(SEED)
    seats = Counter(seat_for_unprompted_ack(p, streams, f"share.{i}") for i in range(4000))
    assert 0.61 < seats["PM"] / 4000 < 0.69  # reference 0.65 / 0.35 (REF-PE-03)


def test_seed_validation_and_draw_log() -> None:
    with pytest.raises(SeedError):
        RandomStreams("not-hex")
    with pytest.raises(SeedError):
        RandomStreams("abcd")
    log = DrawLog()
    streams = RandomStreams(new_seed(), log)
    with pytest.raises(SeedError):
        streams.uniform("world")
    value = streams.uniform("ratings", "higher_seat")
    assert log.as_json() == [{"stream": "ratings", "key": ["higher_seat"], "value": value}]
    assert log.for_stream("world") == []
