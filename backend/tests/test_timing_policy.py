"""WP02 timing policy: INV-0205 resolver, INV-0211 mechanisms, INV-0203 schedules."""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

import pytest

from app.domain.manifests.binding import bind
from app.domain.manifests.schema import Fixed, Lognormal, Truncation
from app.domain.rng import RandomStreams
from app.domain.schedules import plan_job_draws, plan_scripted_requests
from app.timing.distributions import lognormal_mu, sample
from app.timing.mechanisms import Generation, SampledComponents, plan_schedule
from app.timing.policy import PolicyError, resolve_policy
from tests.wp02 import (
    SEED,
    bound,
    copy_content,
    edit_json,
    policy,
    profile_data,
    republished_store,
)

ASSIGNED = (1, 3, 5, 7)
UNASSIGNED = (2, 4, 6, 8)


def test_resolver_is_single_source_for_classes_and_channels() -> None:
    tp = policy("tp_demo_v1", "high")
    assert tp.class_policy("structured_record", "JT").mechanism == "demo_structured_delay_length"
    assert tp.class_policy("social_reply", "PM").mechanism == "demo_social_additive"
    assert tp.class_policy("assistant", "assistant").mechanism == "demo_assistant_floor"
    assert tp.class_policy("structured_record", "PM").presence_cue == "none"
    with pytest.raises(PolicyError, match="not supported"):
        tp.class_policy("acknowledgment", "JT")  # TP has no acknowledgment class
    with pytest.raises(PolicyError):
        tp.class_policy("assistant", "JT")
    pe = policy("pe_pilot_v2", "eval")
    assert pe.class_policy("acknowledgment", "PM").mechanism == "concurrent"
    assert all(p.clock_basis == "active_task" for p in pe.classes.values())
    for manifest_id in ("tp_review_first_v2", "pe_pilot_v2"):
        b = bound(manifest_id)
        assert {p.mechanism for p in resolve_policy(b, b.manifest.arms[0]).classes.values()} == {
            "concurrent"
        }


def test_changed_supported_field_changes_policy_irrelevant_field_does_not(tmp_path: Any) -> None:
    original = policy("tp_review_first_v2", "high")

    root = copy_content(tmp_path / "title")
    edit_json(root / "profiles" / "tp_review_first_v2.json", lambda d: d.update(title="Renamed"))
    renamed = resolve_policy(
        bind(republished_store(root), "tp_review_first_v2", mode="preview"), "high"
    )
    assert renamed.behavior_policy_hash == original.behavior_policy_hash

    root = copy_content(tmp_path / "typing")

    def slower_typing(d: dict[str, Any]) -> None:
        d["timing"]["response_classes"]["structured_record"]["typing"]["per_char_ms"] = 40

    edit_json(root / "profiles" / "tp_review_first_v2.json", slower_typing)
    changed = resolve_policy(
        bind(republished_store(root), "tp_review_first_v2", mode="preview"), "high"
    )
    assert changed.behavior_policy_hash != original.behavior_policy_hash
    assert changed.class_policy("structured_record", "JT").typing.per_char_ms == 40  # type: ignore[union-attr]
    plan_a = plan_schedule(
        original.class_policy("structured_record", "JT"), 0, SampledComponents(9000), body_chars=100
    )
    plan_b = plan_schedule(
        changed.class_policy("structured_record", "JT"), 0, SampledComponents(9000), body_chars=100
    )
    assert (plan_a.eligible_ms, plan_b.eligible_ms) == (11800, 13000)


def test_concurrent_generation_overlaps_wait() -> None:
    assistant = policy("tp_review_first_v2", "high").class_policy("assistant", "assistant")
    wait = SampledComponents(delay_ms=9000)
    fast = plan_schedule(assistant, 0, wait, Generation(ready_ms=3000, body_chars=200))
    slow = plan_schedule(assistant, 0, wait, Generation(ready_ms=12000, body_chars=200))
    assert (fast.scheduled_ms, fast.eligible_ms, fast.readiness_overrun_ms) == (9000, 9000, 0)
    assert (slow.scheduled_ms, slow.eligible_ms, slow.readiness_overrun_ms) == (9000, 12000, 3000)
    thinking = plan_schedule(
        assistant,
        1000,
        SampledComponents(delay_ms=4000, platform_ms=500, think_ms=1500),
        Generation(2000),
    )
    assert thinking.scheduled_ms == 7000  # grant + platform + think + delay, no typing class


def test_demo_social_additive_adds_model_latency() -> None:
    ref = policy("tp_demo_v1", "low").class_policy("social_reply", "JT")
    v2 = policy("tp_review_first_v2", "low").class_policy("social_reply", "JT")
    gen = Generation(ready_ms=3000, body_chars=20)  # typing 20 * 35 = 700 ms
    assert plan_schedule(ref, 0, SampledComponents(7000), gen).eligible_ms == 3000 + 7000 + 700
    assert plan_schedule(v2, 0, SampledComponents(7000), gen).eligible_ms == 7000 + 700
    long_text = Generation(ready_ms=1000, body_chars=1000)
    assert plan_schedule(v2, 0, SampledComponents(7000), long_text).typing_ms == 8000  # capped


def test_demo_assistant_floor_not_additive() -> None:
    ref = policy("pe_demo_v1", "eval").class_policy("assistant", "assistant")
    floor = SampledComponents(delay_ms=0, floor_ms=3000)
    assert plan_schedule(ref, 0, floor, Generation(1000)).eligible_ms == 3000
    assert plan_schedule(ref, 0, floor, Generation(5000)).eligible_ms == 5000


def test_generation_timeout_resolves_to_fallback() -> None:
    v2 = policy("pe_pilot_v2", "control").class_policy("assistant", "assistant")
    missing = plan_schedule(v2, 10_000, SampledComponents(3000), None)
    assert missing.fallback_reason == "timeout"
    assert missing.eligible_ms == 10_000 + 20_000
    late = plan_schedule(v2, 0, SampledComponents(3000), Generation(25_000))
    assert late.fallback_reason == "timeout" and late.effective_ready_ms == 20_000
    failed = plan_schedule(v2, 0, SampledComponents(3000), Generation(1500, failed=True))
    assert failed.fallback_reason == "generation_failed" and failed.eligible_ms == 3000
    structured = policy("pe_pilot_v2", "control").class_policy("structured_record", "JT")
    with pytest.raises(ValueError, match="no generation step"):
        plan_schedule(structured, 0, SampledComponents(3000), Generation(100))


def _source(values: dict[str, float]) -> Any:
    return lambda label: values[label]


def test_clamp_and_reject_semantics() -> None:
    clamp = Lognormal(
        family="lognormal",
        location="median",
        location_s=8.5,
        sigma=0.45,
        truncation=Truncation(method="clamp", lo_s=5.0, hi_s=26.4),
    )
    huge = sample(clamp, _source({"u1": 1 - 1e-12, "u2": 0.0}))  # z ~ +7.4
    assert huge.seconds == 26.4 and huge.truncated and huge.raw_seconds > 26.4
    tiny = sample(clamp, _source({"u1": 1 - 1e-12, "u2": 0.5}))  # z ~ -7.4
    assert tiny.seconds == 5.0 and tiny.truncated
    reject = Lognormal(
        family="lognormal",
        location="median",
        location_s=8.5,
        sigma=0.45,
        truncation=Truncation(
            method="reject", lo_s=5.0, hi_s=26.4, max_attempts=3, on_exhaustion="clamp"
        ),
    )
    draw = sample(reject, _source({"a1.u1": 1 - 1e-12, "a1.u2": 0.0, "a2.u1": 0.3, "a2.u2": 0.25}))
    assert draw.attempts == 2 and not draw.truncated and draw.seconds == pytest.approx(8.5)
    exhausted = sample(
        reject,
        _source(
            {
                f"a{i}.{u}": (1 - 1e-12 if u == "u1" else 0.0)
                for i in (1, 2, 3)
                for u in ("u1", "u2")
            }
        ),
    )
    assert exhausted.method == "reject_exhausted_clamp" and exhausted.seconds == 26.4
    assert sample(Fixed(family="fixed", seconds=28.0), _source({})).ms == 28_000


def test_mean_vs_median_location() -> None:
    zero_z = {"u1": 0.5, "u2": 0.25}  # cos(pi/2) = 0 -> z = 0 -> value = exp(mu)
    trunc = Truncation(method="clamp", lo_s=0.1, hi_s=100.0)
    median = Lognormal(
        family="lognormal", location="median", location_s=8.5, sigma=0.45, truncation=trunc
    )
    mean = Lognormal(
        family="lognormal", location="mean", location_s=8.5, sigma=0.45, truncation=trunc
    )
    assert lognormal_mu(median) == pytest.approx(math.log(8.5))
    assert lognormal_mu(mean) == pytest.approx(math.log(8.5) - 0.45**2 / 2)
    assert sample(median, _source(zero_z)).seconds == pytest.approx(8.5)
    assert sample(mean, _source(zero_z)).seconds == pytest.approx(8.5 * math.exp(-(0.45**2) / 2))


def test_seat_overrides_resolve_per_seat() -> None:
    for manifest_id, arm in (("tp_demo_v1", "high"), ("pe_pilot_v2", "eval")):
        p = policy(manifest_id, arm)
        jt, pm = p.class_policy("social_greeting", "JT"), p.class_policy("social_greeting", "PM")
        assert len(jt.delay) == 1 and len(pm.delay) == 2
        assert (
            p.class_policy("structured_record", "JT").delay
            == p.class_policy("structured_record", "PM").delay
        )


def test_review_first_channels_closed_during_review() -> None:
    v2 = policy("tp_review_first_v2", "high")
    closed = v2.channels_open(59_999)
    assert closed == {"file_work", "answer_drafting"}
    assert {
        "structured_seeking",
        "sharing",
        "social_reply",
        "assistant_prompting",
    } <= v2.channels_open(60_000)
    assert v2.channels_open(360_000) == frozenset()
    demo = policy("tp_demo_v1", "high")
    assert "assistant_prompting" in demo.channels_open(0)
    assert v2.interaction_ms(300_000) == 240_000
    assert demo.interaction_ms(300_000) == 300_000
    assert v2.interaction_ms(80_000) == 20_000  # first proposed request at interaction 20 s


def test_scripted_schedules_baseline_and_review_first() -> None:
    demo = policy("tp_demo_v1", "high")
    v2 = policy("tp_review_first_v2", "high")
    assert demo.scripted_requests["times_ms"] == [40_000, 150_000, 240_000]
    assert v2.scripted_requests["times_ms"] == [80_000, 180_000, 270_000]
    assert [v2.interaction_ms(t) for t in v2.scripted_requests["times_ms"]] == [
        20_000,
        120_000,
        210_000,
    ]
    majorities = Counter()
    for i in range(60):
        seed = f"{i:064x}"
        for p in (demo, v2):
            plans = plan_scripted_requests(
                p, RandomStreams(seed), assigned=ASSIGNED, unassigned=UNASSIGNED
            )
            assert [pl.target for pl in plans] == ["unassigned", "assigned", "unassigned"]
            unassigned = [pl.crate for pl in plans if pl.target == "unassigned"]
            assert len(set(unassigned)) == 2 and set(unassigned) <= set(UNASSIGNED)
            assert len(set(UNASSIGNED) - set(unassigned)) == 2  # two never requested (DEF-01)
            assert next(pl for pl in plans if pl.target == "assigned").crate in ASSIGNED
            seats = Counter(pl.seat for pl in plans)
            if p is demo:
                assert seats == Counter({"JT": 2, "PM": 1})
            else:
                majorities[seats.most_common(1)[0][0]] += 1
    assert set(majorities) == {"JT", "PM"}


def test_controls_resolved_from_manifest() -> None:
    for manifest_id in ("tp_demo_v1", "pe_pilot_v2"):
        data = profile_data(manifest_id)
        b = bound(manifest_id)
        controls = resolve_policy(b, b.manifest.arms[0]).controls
        for key in (
            "ordering",
            "deadline_structure",
            "early_submission",
            "trajectory",
            "visibility",
        ):
            assert controls[key] == data["timing"][key]
        assert controls["concurrency"] == data["timing"]["concurrency"]
        assert controls["rate_limits"] == data["timing"]["rate_limits"]
        assert controls["concurrency"]["over_cap"] == "reject_visible"


def test_job_draws_are_stable_and_keyed() -> None:
    p = policy("tp_demo_v1", "high").class_policy("social_greeting", "PM")
    a = plan_job_draws(p, RandomStreams(SEED), logical_job_key="greeting.1")
    b = plan_job_draws(p, RandomStreams(SEED), logical_job_key="greeting.1")
    c = plan_job_draws(p, RandomStreams(SEED), logical_job_key="greeting.2")
    assert a.components == b.components and a.components != c.components
    assert set(a.draws) == {"delay0", "delay1"}
    assert 3500 + 1000 <= a.components.delay_ms <= 11000 + 4400
    assert json.dumps([d.ms for d in a.draws.values()])  # persisted as plain numbers
