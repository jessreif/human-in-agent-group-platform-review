"""TIME_PRESSURE_ENABLED=0 — the pressure manipulation leaves every surface.

All-modes removal flag (the AI_ASSISTANT_ENABLED archetype): when 0, the
check-in pop-up, teammate pressure remarks, the one-minute warning, low-time
styling, and the pressure follow-up items are gone everywhere — preview, QA,
and live. It is a projection/scheduling filter, not a content edit: canonical
artifacts keep the pressure items and ``manipulation.copy_ref`` so re-enabling
the flag — or replaying a flag-on-era session — restores the original shape.
The neutral task clock and deadline are untouched, and the bare
``solo_review_end`` job replaces the check-in's role as the review-first
``solo_review -> task`` gate.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest

from app.db.tx import RejectionError
from app.domain import stage_graph as sg
from app.domain.manifests.store import ContentStore
from app.flags import FeatureFlags
from app.services import dashboard, dashboard_analysis, lifecycle
from app.settings import Settings


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _sess(sid: uuid.UUID) -> dict:
    return {
        "session_id": sid,
        "study_key": "investigators_time_pressure",
        "compatibility_key": "ck:test",
        "mode": "live",
        "timing_mode": "faithful",
        "binding": {"artifacts": {"instruments.post_task": ["post_task.tp_demo@v5", "0" * 64]}},
    }


def test_setting_defaults_on_and_summarizes(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings()
    assert settings.TIME_PRESSURE_ENABLED is True
    assert make_settings(TIME_PRESSURE_ENABLED="0").TIME_PRESSURE_ENABLED is False
    assert settings.describe()["time_pressure_enabled"] is True


def test_pressure_command_rejected_when_disabled() -> None:
    """pressure_response/pressure_skip fail closed under the flag — a stray
    command (e.g. queued before a flag flip) must not record manipulation data."""
    with pytest.raises(RejectionError, match="time_pressure_disabled"):
        lifecycle._pressure(None, None, {}, skip=False, time_pressure=False)
    with pytest.raises(RejectionError, match="time_pressure_disabled"):
        lifecycle._pressure(None, None, {}, skip=True, time_pressure=False)


def test_instrument_projection_filters_pressure_items() -> None:
    assert (
        lifecycle.instrument_item_visible("tp_post_09", ai_assistant=True, time_pressure=False)
        is False
    )
    assert (
        lifecycle.instrument_item_visible("tp_post_11", ai_assistant=True, time_pressure=False)
        is False
    )
    assert (
        lifecycle.instrument_item_visible("tp_post_09", ai_assistant=True, time_pressure=True)
        is True
    )
    assert (
        lifecycle.instrument_item_visible("tp_post_01", ai_assistant=True, time_pressure=False)
        is True
    )


def test_expected_items_omit_pressure_items_when_disabled(store: ContentStore) -> None:
    sess = _sess(uuid.uuid4())
    on = dashboard.expected_items(sess, store, time_pressure=True)
    off = dashboard.expected_items(sess, store, time_pressure=False)
    assert {"tp_post_09", "tp_post_11"} <= set(on["post_task.tp_demo"]["items"])
    # tp_post_09 asks whether the check-in made the participant feel rushed;
    # tp_post_11 asks whether the teammates seemed rushed — both presuppose
    # the manipulation, so flag-off serves neither.
    assert "tp_post_09" not in off["post_task.tp_demo"]["items"]
    assert "tp_post_11" not in off["post_task.tp_demo"]["items"]


def _post_row(sid: uuid.UUID, item_id: str, status: str = "answered") -> dict:
    return {
        "session_id": sid,
        "instrument_id": "post_task.tp_demo",
        "item_id": item_id,
        "instrument_version": 5,
        "status": status,
        "response": {"value": 4},
    }


def test_flag_off_era_session_is_not_expected_pressure_items(store: ContentStore) -> None:
    """A session that completed post_questionnaire without pressure-item rows
    is provably flag-off — flag-on servers require every served item answered
    or skipped before the stage advances — so the dashboard must not expect
    the items even while the deployment flag is on."""
    sid = uuid.uuid4()
    rows = [_post_row(sid, "tp_post_01")]
    visited = {sid: frozenset({"post_questionnaire", "open_ended", "debrief"})}
    cells, _ = dashboard_analysis._item_cells(
        [_sess(sid)],
        store,
        {sid: rows},
        visited_by_sid=visited,
        flags=FeatureFlags(time_pressure=True),
    )
    assert "tp_post_09" not in {c["item_id"] for c in cells}
    assert "tp_post_11" not in {c["item_id"] for c in cells}


def test_committed_pressure_row_proves_flag_on_era(store: ContentStore) -> None:
    """A committed pressure item row means the session ran with the flag on —
    it stays expected and reconciled even under a flag-off deployment."""
    sid = uuid.uuid4()
    rows = [_post_row(sid, "tp_post_09")]
    cells, _ = dashboard_analysis._item_cells(
        [_sess(sid)],
        store,
        {sid: rows},
        flags=FeatureFlags(time_pressure=False),
    )
    by_id = {c["item_id"]: c for c in cells}
    assert by_id["tp_post_09"]["counts"]["answered"] == 1


def test_checkin_stages_prove_flag_on_era(store: ContentStore) -> None:
    """A check-in stage visit proves the session ran flag-on."""
    sid = uuid.uuid4()
    visited = {sid: frozenset({"task", "checkin_collect", "checkin_wait"})}
    cells, _ = dashboard_analysis._item_cells(
        [_sess(sid)],
        store,
        {},
        visited_by_sid=visited,
        flags=FeatureFlags(time_pressure=False),
    )
    assert "tp_post_09" in {c["item_id"] for c in cells}


def test_no_evidence_session_follows_deployment_flag(store: ContentStore) -> None:
    """Sessions too early to carry pressure evidence inherit the deployment
    flag: off → pressure items un-expected; on → expected and absent."""
    sid = uuid.uuid4()
    cells_on, _ = dashboard_analysis._item_cells(
        [_sess(sid)],
        store,
        {},
        flags=FeatureFlags(time_pressure=True),
    )
    assert "tp_post_09" in {c["item_id"] for c in cells_on}
    cells_off, _ = dashboard_analysis._item_cells(
        [_sess(sid)],
        store,
        {},
        flags=FeatureFlags(time_pressure=False),
    )
    assert "tp_post_09" not in {c["item_id"] for c in cells_off}


def test_pressure_job_kinds_cover_every_manipulation_job() -> None:
    """Every scheduled kind that exists only for the manipulation is in the
    suppression set — a kind added later and missed here would leak."""
    from app.services import scheduler

    assert (
        frozenset({"checkin", "checkin_reveal", "checkin_inactivity", "warning", "pressure_remark"})
        == scheduler.PRESSURE_JOB_KINDS
    )
    # The neutral replacements and the task clock are never pressure jobs.
    for kind in ("deadline", "task_open", "solo_review_end", "greeting_prompt"):
        assert kind not in scheduler.PRESSURE_JOB_KINDS


def test_solo_review_task_edge_exists_for_flag_off_review_first() -> None:
    """TIME_PRESSURE_ENABLED=0 review-first sessions exit solo_review via the
    neutral solo_review_end job — the edge must be legal."""
    assert "task" in sg.legal_targets("tp_review_first", "solo_review")
    # Flag-on path unchanged: the check-in loop still gates the review window.
    assert "checkin_collect" in sg.legal_targets("tp_review_first", "solo_review")


def test_allocation_arms_collapse_to_nopress_cell() -> None:
    """TIME_PRESSURE_ENABLED=0 retires the high/low arm axis from minting and
    allocation (owner direction 2026-10-14): TP draws the single ``nopress``
    cell — the study no longer pretends to randomize a manipulation it does
    not deliver. Peer-eval arms are a different axis and stay untouched."""
    from app.domain.manifests.schema import NO_PRESSURE_ARM, allocation_arms

    tp_declared = ("high", "low")
    assert allocation_arms(
        "investigators_time_pressure", tp_declared, time_pressure_enabled=False
    ) == (NO_PRESSURE_ARM,)
    assert allocation_arms(
        "investigators_time_pressure", tp_declared, time_pressure_enabled=True
    ) == (
        "high",
        "low",
    )
    # v15: a manifest declaring high/mixed allocates its own pair — the
    # deployment-level set never overrides what the bound manifest declares.
    assert allocation_arms(
        "investigators_time_pressure", ("high", "mixed"), time_pressure_enabled=True
    ) == (
        "high",
        "mixed",
    )
    assert allocation_arms(
        "investigators_peer_eval", ("eval", "control"), time_pressure_enabled=False
    ) == (
        "eval",
        "control",
    )


def test_nopress_arm_resolves_neutral_policy(store: ContentStore) -> None:
    """Neutral allocations retain mechanics but never inherit a paired treatment."""
    from app.domain.manifests.binding import bind
    from app.domain.manifests.schema import NO_PRESSURE_ARM
    from app.timing.policy import PolicyError, resolve_policy

    bound = bind(store, "tp_ai_v11", mode="qa")
    neutral = resolve_policy(bound, NO_PRESSURE_ARM)
    canonical = resolve_policy(bound, "high")
    assert neutral.budget_ms == canonical.budget_ms
    assert neutral.classes == canonical.classes
    assert neutral.phases == canonical.phases
    assert neutral.arm == NO_PRESSURE_ARM
    assert neutral.manipulation == {"arm": NO_PRESSURE_ARM, "kind": "none"}
    assert neutral.checkin is None
    assert neutral.warnings == ()
    assert "pressure_checkin" not in neutral.controls
    assert "pressure_remarks" not in neutral.controls
    with pytest.raises(PolicyError):
        resolve_policy(bound, "bogus")
    with pytest.raises(PolicyError):
        resolve_policy(bind(store, "pe_demo_nl_v3", mode="qa"), NO_PRESSURE_ARM)


@pytest.mark.parametrize("arm", ["nopress", "high", "low", None])
@pytest.mark.parametrize("enabled", [False, True])
def test_effective_pressure_requires_non_neutral_arm(arm: object, enabled: bool) -> None:
    from app.timing.policy import time_pressure_for_arm

    assert time_pressure_for_arm(arm, enabled=enabled) is (enabled and arm != "nopress")


def test_neutral_pending_session_does_not_inherit_pressure_era(store: ContentStore) -> None:
    sid = uuid.uuid4()
    sess = {**_sess(sid), "arm": "nopress"}
    cells, values = dashboard_analysis._item_cells(
        [sess],
        store,
        {},
        flags=FeatureFlags(time_pressure=True),
    )
    assert not {"tp_post_09", "tp_post_11"} & {c["item_id"] for c in cells}
    registry = dashboard_analysis._scalar_registry(
        [sess],
        store,
        values,
        flags=FeatureFlags(time_pressure=True),
    )
    assert not any(key.endswith((".tp_post_09", ".tp_post_11")) for key in registry)
    # Actual recorded exposure remains evidence, even on a neutral-labelled row.
    observed, _ = dashboard_analysis._item_cells(
        [sess],
        store,
        {sid: [_post_row(sid, "tp_post_09")]},
        flags=FeatureFlags(time_pressure=True),
    )
    assert any(c["item_id"] == "tp_post_09" for c in observed)
