"""WP07 behavioral fidelity unit tests (INV-0702/0703/0705/0708).

Covers the deterministic output validator, the demo-vs-neutral fallback confinement, the
28 s PE crosstalk relation constant, and per-seat policy equivalence across arms (the
paired-arm fidelity checks that need no database). Integration coverage for the connecting
sequence, agent memory, draw persistence and golden ordering lives in
``tests/integration/test_wp07_engines.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain.manifests.binding import BindError, bind
from app.domain.world import HIDEOUTS
from app.services import engines, scheduler
from app.timing.policy import resolve_policy
from tests.wp02 import (
    CONTENT,
    PROFILES,
    copy_content,
    edit_json,
    republished_store,
)
from tests.wp02 import (
    bound as _bound,
)

# ----- INV-0703: generated-output validator ----------------------------------------------------


def test_hideouts_in_matches_demo_semantics() -> None:
    for h in HIDEOUTS:
        assert h in engines.hideouts_in(f"i think crate 3 is at {h}")
        # de-articled substring match, case-insensitive (demo `hideoutsIn`)
        bare = h.lower().removeprefix("the ")
        assert engines.hideouts_in(f"seen at {bare}") == [h]
    assert engines.hideouts_in("no locations here") == []


@pytest.mark.parametrize(
    ("text", "violation"),
    [
        ("crate 2 is at the Marina", "hideout_leak"),
        ("hurry up, time is running out", "urgency"),
        ("good job, that is correct", "evaluation"),
        ("you should focus on crate 4 first", "task_advice"),
        ("one sec, my wifi is lagging", "stall_excuse"),
        ("as an ai i cannot say", "interface_leak"),
        ("what do you think of the study", "interface_leak"),
        ("hi! sure thing", "voice_format"),
        ("that helps: noted", "voice_format"),
        ("x" * 401, "length_overrun"),
        ("check the file then the file again", "banned_token_reuse"),
    ],
)
def test_validator_rejects_off_policy_text(text: str, violation: str) -> None:
    violations = engines.validate_teammate_output(text)
    assert violation in violations


@pytest.mark.parametrize(
    "text",
    [
        "oh nice, that helps, noting it",
        "not off the top of my head, send me a request for 2 and i will pass along what i have",
        "same here. i am stuck on one of mine if anything recent turns up in yours",
        "np",
        "doing fine, bit swamped",
    ],
)
def test_validator_accepts_on_policy_text(text: str) -> None:
    assert engines.validate_teammate_output(text) == []


def test_assistant_allowed_locations_bound_output() -> None:
    # The assistant may name locations that are inside the pinned file snapshot — anything
    # else is a leak even though the word is a real hideout (allowed locations are the
    # canonical HIDEOUTS names stored on ``record.location_id``).
    allowed = frozenset({"The Marina"})
    assert (
        engines.validate_teammate_output("crate 3 is at the Marina", allowed_locations=allowed)
        == []
    )
    violations = engines.validate_teammate_output(
        "crate 3 is at the Marina, crate 4 at the Sawmill", allowed_locations=allowed
    )
    assert "hideout_leak" in violations


# ----- INV-0705: fallback-bank confinement -----------------------------------------------------


def _load_artifact(path: str) -> dict:
    return json.loads((CONTENT / path).read_text())


def test_pilot_profiles_bind_neutral_fallback_bank() -> None:
    for manifest_id in PROFILES:
        data = json.loads((CONTENT / "profiles" / f"{manifest_id}.json").read_text())
        ref = data["teammates"]["fallback_bank"]["artifact_id"]
        if data["profile_kind"] == "proposed_pilot":
            assert ref == "fallbacks.neutral", manifest_id
        else:
            assert ref == "fallbacks.demo", manifest_id


def test_demo_busy_language_absent_from_neutral_bank() -> None:
    demo = _load_artifact("scripts/fallbacks.demo.v1.json")["body"]
    neutral = _load_artifact("scripts/fallbacks.neutral.v1.json")["body"]
    # Neutral wording may reuse genuinely neutral lines; the confinement is of the
    # pressure-confounding busy/stuck wording the spec flags (§7.3).
    busy_markers = ("swamped", "stuck", "deep in my own", "distracted")
    neutral_lines = {line for r in neutral["rules"] for line in r["lines"]}
    demo_busy = {
        line
        for r in demo["rules"]
        for line in r["lines"]
        if any(m in line.lower() for m in busy_markers)
    }
    assert demo_busy, "expected the demo bank to still carry its historical busy lines"
    assert demo_busy.isdisjoint(neutral_lines)
    for line in neutral_lines:
        assert not any(m in line.lower() for m in busy_markers), line


def test_binding_rejects_demo_fallback_bank_on_pilot(tmp_path: Path) -> None:
    root = copy_content(tmp_path)
    edit_json(
        root / "profiles" / "pe_pilot_v2.json",
        lambda m: m["teammates"].update(
            fallback_bank={"artifact_id": "fallbacks.demo", "version": 1}
        ),
    )
    with pytest.raises(BindError, match="demo fallback bank"):
        bind(republished_store(root), "pe_pilot_v2", mode="preview")


# ----- INV-0702: crosstalk relation + draw persistence surface ---------------------------------


def test_pe_crosstalk_is_28_seconds() -> None:
    assert scheduler.CROSSTALK_AFTER_REQUEST_MS == 28_000
    for manifest_id in ("pe_demo_v1", "pe_pilot_v2"):
        bound = _bound(manifest_id)
        teammates = resolve_policy(bound, bound.manifest.arms[0]).teammates
        assert teammates["crosstalk"] is not None


def test_tp_has_no_acknowledgment_or_crosstalk() -> None:
    # INV-0708: TP profiles carry no per-seat ack/crosstalk policy at all, so rating-seat
    # assignment cannot interact with them.
    for manifest_id in ("tp_demo_v1", "tp_review_first_v2"):
        for arm in ("high", "low"):
            tm = resolve_policy(_bound(manifest_id), arm).teammates
            assert tm.get("acknowledgments") is None
            assert tm.get("crosstalk") is None


# ----- INV-0708: paired-arm seat symmetry --------------------------------------------------------


def test_class_policies_identical_across_arms() -> None:
    """INV-0706/0708 + spec §7.7: every per-(class, seat) timing policy must be identical
    between arms — no hidden behavioral speed-up for high pressure (INV-0705)."""
    for manifest_id in PROFILES:
        bound = _bound(manifest_id)
        arms = bound.manifest.arms
        assert len(arms) == 2
        by_arm = {arm: resolve_policy(bound, arm) for arm in arms}
        keys = set(by_arm[arms[0]].classes)
        assert keys == set(by_arm[arms[1]].classes)
        for key in keys:
            a, b = by_arm[arms[0]].classes[key], by_arm[arms[1]].classes[key]
            assert a == b, (manifest_id, key)


def test_teammate_policies_identical_across_arms() -> None:
    for manifest_id in PROFILES:
        bound = _bound(manifest_id)
        arms = bound.manifest.arms
        t0 = resolve_policy(bound, arms[0]).teammates
        t1 = resolve_policy(bound, arms[1]).teammates
        assert t0 == t1, manifest_id


def test_pe_acknowledgment_probabilities_match_reference() -> None:
    for manifest_id in ("pe_demo_v1", "pe_pilot_v2"):
        ack = resolve_policy(_bound(manifest_id), _bound(manifest_id).manifest.arms[0]).teammates[
            "acknowledgments"
        ]
        assert ack["probabilities"]["JT"] == {"prompted": 0.55, "unprompted": 0.25}
        assert ack["probabilities"]["PM"] == {"prompted": 0.9, "unprompted": 0.6}
        assert ack["unprompted_seat_weights"] == {"JT": 0.35, "PM": 0.65}


# ----- O-17: voice-pool wrap bookkeeping -------------------------------------------------------


def test_voice_pool_wrap_flags_draws_past_the_pool_end(monkeypatch) -> None:
    """O-17: under no_repeat_until_exhausted a draw past the pool's end replays
    the same shuffled order — flagged ``pool_wrapped`` per draw, never silent."""
    import uuid

    from app.domain.rng import RandomStreams
    from tests.wp02 import SEED

    bound = _bound("pe_demo_v5")
    pool = engines._voice_pool(bound, "JT", "thanks")
    assert pool, "expected a JT thanks pool"
    counts = iter(range(len(pool) + 2))
    monkeypatch.setattr(engines, "_used_voice_count", lambda *a, **k: next(counts))
    draws = [
        engines.voice_line(None, RandomStreams(SEED), bound, "JT", "thanks", uuid.uuid4())
        for _ in range(len(pool) + 2)
    ]
    assert [d.draw_ordinal for d in draws] == list(range(len(pool) + 2))
    assert [d.pool_wrapped for d in draws] == [False] * len(pool) + [True, True]
    # wrapped draws replay earlier lines from the same shuffled order
    assert draws[len(pool)].text in {d.text for d in draws[: len(pool)]}
