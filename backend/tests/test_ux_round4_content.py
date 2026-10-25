"""UX round 4 content contracts (UX_Feedback_v4, 2026-10-05): the re-issued
profiles bind the varied request script, the diversified fallback social pool,
and the pre-debrief teammate-nature choice item — and the new artifacts pass the
same output gates as every earlier bank."""

from __future__ import annotations

import itertools

import pytest

from app.services import engines
from tests.wp02 import bound, store

ROUND4 = (
    "tp_demo_v6",
    "pe_demo_v6",
    "tp_review_first_v7",
    "pe_pilot_v7",
    "tp_demo_nl_v3",
    "pe_demo_nl_v3",
)

GREETING_BUTTONS = {"Hello!", "Hi!", "Hey!"}


@pytest.mark.parametrize("manifest_id", ROUND4)
def test_round4_profiles_bind_the_ux4_artifacts(manifest_id: str) -> None:
    b = bound(manifest_id)
    arts = b.artifacts
    assert arts["timing.scripted_requests.script"].key == "requests.demo@v2"
    assert arts["instruments.post_task"].key.endswith("@v2")
    fb = arts["teammates.fallback_bank"].key
    assert fb in ("fallbacks.demo@v4", "fallbacks.neutral@v4")


def test_request_script_v2_varies_and_always_names_the_crate() -> None:
    body = store().artifacts["requests.demo@v2"].artifact.body  # type: ignore[attr-defined]
    assert body.selection == "uniform_per_request"
    assert len(body.templates) >= 3, "v2 must actually vary the phrasing"
    for t in body.templates:
        assert "{n}" in t, t
        for n in (3, 7):
            rendered = t.replace("{n}", str(n))
            assert engines.validate_teammate_output(rendered) == [], rendered


@pytest.mark.parametrize("artifact", ["fallbacks.demo@v4", "fallbacks.neutral@v4"])
def test_round4_fallback_lines_pass_the_output_gate(artifact: str) -> None:
    body = store().artifacts[artifact].artifact.body  # type: ignore[attr-defined]
    for rule in body.rules:
        for line in rule.lines:
            assert engines.validate_teammate_output(line) == [], (rule.when, line)


@pytest.mark.parametrize("artifact", ["fallbacks.demo@v4", "fallbacks.neutral@v4"])
def test_social_pool_lines_are_not_near_paraphrases(artifact: str) -> None:
    """Item 13: same-question social replies converged on paraphrases. The v4 pool is
    topically distinct — no pair may trip the near-repeat guard (Dice ≥ 0.6)."""
    body = store().artifacts[artifact].artifact.body  # type: ignore[attr-defined]
    social = [line for r in body.rules if r.when == "social" for line in r.lines]
    assert len(social) >= 4
    for a, b in itertools.combinations(social, 2):
        assert not engines.is_near_repeat(b, [a]), (a, b)


def test_neutral_bank_v4_stays_pressure_neutral() -> None:
    body = store().artifacts["fallbacks.neutral@v4"].artifact.body  # type: ignore[attr-defined]
    for rule in body.rules:
        for line in rule.lines:
            assert not engines._URGENCY.search(line)
            for word in ("busy", "stuck", "swamped", "buried", "time"):
                assert word not in line.lower(), line


@pytest.mark.parametrize("artifact", ["post_task.tp_demo@v2", "post_task.pe_demo@v2"])
def test_pre_debrief_suspicion_item_is_the_last_item(artifact: str) -> None:
    """Item 14: the teammate-nature question sits last in post_task — immediately
    before debrief — with the four fixed choices."""
    body = store().artifacts[artifact].artifact.body  # type: ignore[attr-defined]
    last = body.items[-1]
    assert (
        last.text == "Thinking about the other two members of your team, which best describes them?"
    )
    assert list(last.options or ()) == [
        "All were human",
        "Some were human, some were automated",
        "All were automated",
        "I am not sure",
    ]
    # No earlier item may be a choice item — the rest of the questionnaire is unchanged.
    assert all(i.options is None for i in body.items[:-1])


def test_greeting_pools_contain_only_the_button_lines() -> None:
    """Item 8: greeting replies are drawn verbatim from the voice pools, so every
    pool entry must itself be one of the offered greeting buttons."""
    for manifest_id in ROUND4:
        b = bound(manifest_id)
        vb = b.artifacts["teammates.voice_bank"].artifact
        for seat in ("JT", "PM"):
            pool = vb.body.seats[seat].greetings  # type: ignore[attr-defined]
            assert pool, f"{manifest_id}/{seat} has no greeting pool"
            for line in pool:
                assert line in GREETING_BUTTONS, (manifest_id, seat, line)
