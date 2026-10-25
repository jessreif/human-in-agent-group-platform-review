"""INV-0213: warning.one_minute_left v1 as content plus an active-clock timing policy.

These tests cover the contract (artifact, plan, commit decision). Execution by the durable
scheduler across restarts/reconnects, and the persisted active-time ledger, are WP06
(INV-0601/0603/0611) and are not claimed here.
"""

from __future__ import annotations

from app.domain.manifests.artifacts import WarningArtifact
from app.timing.policy import resolve_policy
from app.timing.warnings import WarningDecision, decide_warning_commit, is_due, low_time
from tests.wp02 import PROFILES, bound, fixture, policy, store


def _all_plans() -> list[tuple[str, str, dict[str, object]]]:
    out = []
    for manifest_id in PROFILES:
        b = bound(manifest_id)
        for arm in b.manifest.arms:
            (plan,) = resolve_policy(b, arm).warnings
            out.append((manifest_id, arm, plan.as_dict()))
    return out


def test_warning_due_at_active_300_boundary() -> None:
    (plan,) = policy("tp_demo_v1", "high").warnings
    assert plan.due_active_ms == 300_000
    assert not is_due(plan, 299_999)
    assert is_due(plan, 299_999.999) is False
    assert is_due(plan, 300_000)
    assert decide_warning_commit(
        plan, active_ms=299_999, task_closed=False, already_committed=False
    ) == (WarningDecision.NOT_DUE)
    assert decide_warning_commit(
        plan, active_ms=300_000, task_closed=False, already_committed=False
    ) == (WarningDecision.COMMIT)


def test_warning_identical_across_arms_and_profiles() -> None:
    plans = _all_plans()
    assert len(plans) == 2 * len(PROFILES)
    first = plans[0][2]
    for manifest_id, arm, plan in plans:
        assert plan == first, (manifest_id, arm)
    assert first["text"] == "One minute left."
    assert first["sender"] == "system"
    assert first["logical_job_key"] == "warning.one_minute_left.v1"


def test_warning_is_system_content_not_teammate_voice() -> None:
    artifact = store().artifacts["warning.one_minute_left@v1"].artifact
    assert isinstance(artifact, WarningArtifact)
    assert artifact.body.teammate_voice is False
    assert artifact.body.arm_invariant is True
    assert artifact.body.delivery.per_session == 1
    assert artifact.body.delivery.waits_on == ()  # never waits on chat/model work
    assert set(artifact.body.exposure.receipts) == {"commit", "receive", "render"}


def test_warning_on_active_clock_independent_of_checkin_pause() -> None:
    """The trigger is an active-time instant, so the wall time at which it fires is
    300 s + (total pause length before it). Model of the ledger for illustration; the real
    ledger is INV-0601."""
    (plan,) = policy("tp_review_first_v2", "low").warnings
    assert plan.clock_basis == "active_task"
    artifact = store().artifacts["warning.one_minute_left@v1"].artifact
    assert isinstance(artifact, WarningArtifact)
    assert artifact.body.delivery.pauses_with == "active_task_clock"

    def wall_when_active_reaches(active_ms: int, pause_at_ms: int, pause_len_ms: int) -> int:
        return active_ms + (pause_len_ms if active_ms > pause_at_ms else 0)

    for pause_len in (3_500, 90_000, 119_000):
        wall = wall_when_active_reaches(plan.due_active_ms, 60_000, pause_len)
        assert wall - pause_len == plan.due_active_ms  # always active 300, whatever the pause


def test_review_first_warning_at_interaction_240() -> None:
    v2 = policy("tp_review_first_v2", "high")
    (plan,) = v2.warnings
    assert v2.interaction_ms(plan.due_active_ms) == 240_000
    demo = policy("tp_demo_v1", "high")
    assert demo.interaction_ms(demo.warnings[0].due_active_ms) == 300_000
    pe = policy("pe_pilot_v2", "eval")
    assert pe.warnings[0].due_active_ms == 300_000  # continuous PE clock


def test_warning_suppressed_after_close_and_single_commit() -> None:
    (plan,) = policy("pe_demo_v1", "control").warnings
    decide = decide_warning_commit
    assert decide(plan, active_ms=300_500, task_closed=True, already_committed=False) == (
        WarningDecision.SUPPRESS_TASK_CLOSED
    )
    assert decide(plan, active_ms=300_500, task_closed=False, already_committed=True) == (
        WarningDecision.ALREADY_COMMITTED
    )
    assert plan.when_task_closed == "suppress_and_log"


def test_low_time_state_window() -> None:
    (plan,) = policy("tp_demo_v1", "low").warnings
    assert not low_time(plan, 299_999)
    assert low_time(plan, 300_000)
    assert low_time(plan, 360_000)
    assert (plan.low_time_from_ms, plan.low_time_until_ms) == (300_000, 360_000)


def test_reference_golden_warning_matches_demos() -> None:
    ref = {b["id"]: b for b in fixture("behavior_reference.json")["behaviors"]}["REF-TASK-02"]
    (plan,) = policy("tp_demo_v1", "high").warnings
    assert plan.text == ref["value"]["message"]
    assert plan.due_active_ms == ref["value"]["active_second"] * 1000
    assert (
        plan.low_time_until_ms - plan.low_time_from_ms
        == ref["value"]["low_style_from_remaining_s"] * 1000
    )
    demos = {a["demo"] for a in ref["anchors"]}
    assert demos == {"tp", "pe"}  # both demos carry the same warning
