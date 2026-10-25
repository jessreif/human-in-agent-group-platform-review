"""v15 (2026-10-17): workload relabel + the ``mixed`` teammate-level arm.

tp_ai_ovsome_thin_v9 pairs the both-teammates-heavy arm (``high``, values
[6, 7]) with the ``mixed`` arm (values [2, 6] — one seat heavy, one light, the
high seat drawn by the existing ratings fair coin). Remark copy under ``mixed``
is per-seat-level (``mixed_lines``), resolved at delivery from the check-in's
recorded ``assigned_values``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.manifests.artifacts import PressureRemark, PressureRemarksBody
from app.domain.manifests.binding import bind
from app.domain.manifests.diff import paired_arm_diff
from app.domain.manifests.schema import allocation_arms
from app.domain.manifests.store import ContentStore
from app.domain.rng import DrawLog, RandomStreams
from app.domain.schedules import plan_rating_seats
from app.timing.policy import resolve_policy

V15 = "tp_ai_ovsome_thin_v9"


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def test_v15_condition_set_declares_high_and_mixed(store: ContentStore) -> None:
    m = store.manifests[V15].manifest
    assert m.arms == ("high", "mixed")
    spec = {spec.arm: spec.manipulation.teammate_values for spec in m.condition_set}  # type: ignore[union-attr]
    assert spec == {"high": (6, 7), "mixed": (2, 6)}
    assert allocation_arms(m.study_key, m.arms, time_pressure_enabled=True) == (
        "high",
        "mixed",
    )


def test_mixed_policy_carries_per_level_remark_lines(store: ContentStore) -> None:
    b = bind(store, V15, mode="live")
    mixed = resolve_policy(b, "mixed").manipulation
    assert mixed["teammate_values"] == [2, 6]
    # Under mixed, remark lines are per-speaker-level dicts resolved at delivery.
    assert mixed["remark_lines"] == [
        {
            "high": "yikes this is a lot for 5 min, not sure i'll finish",
            "low": "feels ok actually, think i'll finish early",
        },
        {
            "high": "ugh lucky, mine feels like a lot",
            "low": "actually i'm on track",
        },
        {
            "high": "not done with mine",
            "low": "ok almost done with my 4",
        },
        {
            "high": "i am not!",
            "low": "nice!",
        },
    ]
    high = resolve_policy(b, "high").manipulation
    assert high["teammate_values"] == [6, 7]
    assert high["remark_lines"] == [
        "yikes this is a lot for 5 min, not sure i'll finish",
        "me too, feels like a lot",
        "ah not done with my 4",
        "me either!",
    ]
    # speaker_level pins the order: check-in leads heavy, warning leads light.
    schedule = resolve_policy(b, "mixed").controls["pressure_remarks"]["schedule"]
    assert [e[2] for e in schedule] == ["high", "low", "low", "high"]


def test_mixed_higher_seat_is_a_fair_coin(store: ContentStore) -> None:
    """[2,6] under mixed: the high seat is drawn on the ratings stream — over
    enough seeds both JT and PM carry the 6, so the workload contrast is balanced."""
    p = resolve_policy(bind(store, V15, mode="live"), "mixed")
    higher: set[str] = set()
    for i in range(64):
        seats = plan_rating_seats(p, RandomStreams(f"{i:064x}", DrawLog()))
        assert sorted(seats.values.values()) == [2, 6]
        assert seats.values[seats.higher_seat] == 6
        higher.add(seats.higher_seat)
    assert higher == {"JT", "PM"}


def test_v15_arm_diff_stays_inside_manipulation(store: ContentStore) -> None:
    paths = {d.path for d in paired_arm_diff(bind(store, V15, mode="live"))}
    assert paths == {
        "manipulation.arm.arm",
        "manipulation.arm.teammate_values",
        "manipulation.arm.remark_lines",
    }


def test_mixed_lines_schema_rules() -> None:
    base = {
        "at_active_s": 75,
        "speaker": "first",
        "lines": {"high": "a" * 3},
    }
    PressureRemark(**{**base, "mixed_lines": {"high": "h", "low": "l"}})
    # "mixed" is not a lines key — it cannot express per-seat text.
    with pytest.raises(ValidationError):
        PressureRemark(**{**base, "lines": {"high": "h", "mixed": "m"}})
    # mixed_lines must carry exactly {high, low}.
    with pytest.raises(ValidationError):
        PressureRemark(**{**base, "mixed_lines": {"high": "h"}})
    with pytest.raises(ValidationError):
        PressureRemark(**{**base, "mixed_lines": {"high": "x" * 201, "low": "l"}})


def test_mixed_lines_are_all_or_none_across_remarks() -> None:
    with pytest.raises(ValidationError):
        PressureRemarksBody(
            seat_order="fair_coin_per_session",
            channel="team",
            remarks=(
                PressureRemark(
                    at_active_s=75,
                    speaker="first",
                    lines={"high": "h"},
                    mixed_lines={"high": "h", "low": "l"},
                ),
                PressureRemark(at_active_s=83, speaker="second", lines={"high": "h"}),
            ),
        )


def test_workload_artifacts_are_bound_on_v15(store: ContentStore) -> None:
    b = bind(store, V15, mode="live")
    checkin = b.artifacts["manipulation.copy_ref"].artifact
    assert (checkin.artifact_id, checkin.version) == ("checkin.workload", 2)
    body = checkin.body  # type: ignore[union-attr]
    assert "workload" in body.question.lower()
    assert "Very light" in body.scale_min_label
    assert "Overwhelming" in body.scale_max_label
    post = b.artifacts["instruments.post_task"].artifact
    assert (post.artifact_id, post.version) == ("post_task.tp_demo", 9)
    texts = {i.item_id: i.text for i in post.body.items}  # type: ignore[union-attr]
    assert texts["tp_post_09"] == "After the check-in, I felt like my workload was heavier."
    assert texts["tp_post_11"] == (
        "After the check-in, my teammates seemed to feel like their workloads were heavier."
    )
    assert (
        texts["tp_post_12"]
        == "After the check-in, my teammates seemed to feel differently about their workloads."
    )
