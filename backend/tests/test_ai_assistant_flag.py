"""AI_ASSISTANT_ENABLED=0 — the assistant leaves every served surface.

The flag is a projection filter, not a content edit: canonical artifacts and
the comprehension key keep the assistant items so re-enabling the flag — or
replaying a flag-on-era session after the deployment flips — restores the
original shape.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest

from app.db.tx import RejectionError
from app.domain.manifests.store import ContentStore
from app.flags import FeatureFlags
from app.services import dashboard, dashboard_analysis, lifecycle, practice, team_chat
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
        "binding": {"artifacts": {"instruments.post_task": ["post_task.tp_demo@v4", "0" * 64]}},
    }


def test_setting_defaults_on_and_summarizes(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings()
    assert settings.AI_ASSISTANT_ENABLED is True
    assert make_settings(AI_ASSISTANT_ENABLED="0").AI_ASSISTANT_ENABLED is False
    assert settings.describe()["ai_assistant_enabled"] is True


def test_assistant_request_rejected_when_disabled() -> None:
    with pytest.raises(RejectionError, match="assistant_disabled"):
        team_chat.assistant_request(None, None, {}, ai_assistant=False)


def test_practice_world_omits_assistant_replies() -> None:
    off = practice.practice_world(ai_assistant=False)
    assert off["ai_assistant"] is False
    assert off["replies"] == {}
    on = practice.practice_world()
    assert on["ai_assistant"] is True
    assert on["replies"]


def test_instrument_projection_filters_assistant_items() -> None:
    assert lifecycle.instrument_item_visible("tp_post_04", ai_assistant=False) is False
    assert lifecycle.instrument_item_visible("pe_post_04", ai_assistant=False) is False
    assert lifecycle.instrument_item_visible("tp_post_04", ai_assistant=True) is True
    assert lifecycle.instrument_item_visible("tp_post_01", ai_assistant=False) is True


def test_open_ended_source_reliance_drops_assistant_clause() -> None:
    bound_text = (
        "When you weren't sure about a crate, how did you decide which source "
        "to rely on: your case file, the team chat, or the AI assistant?"
    )
    assert (
        lifecycle.instrument_item_text("source_reliance", bound_text, ai_assistant=False)
        == lifecycle.SOURCE_RELIANCE_NO_ASSISTANT_TEXT
    )
    # Enabled, or a reworded artifact that no longer names the assistant: untouched.
    assert (
        lifecycle.instrument_item_text("source_reliance", bound_text, ai_assistant=True)
        == bound_text
    )
    reworded = "When you weren't sure about a crate, what did you rely on?"
    assert (
        lifecycle.instrument_item_text("source_reliance", reworded, ai_assistant=False) == reworded
    )


def test_comprehension_spec_omits_assistant_item() -> None:
    on = dashboard.comprehension_instrument_spec(ai_assistant=True)
    off = dashboard.comprehension_instrument_spec(ai_assistant=False)
    assert "assistant_reads_team" in on["comprehension_check"]["items"]
    assert "assistant_reads_team" not in off["comprehension_check"]["items"]
    assert len(off["comprehension_check"]["items"]) == len(on["comprehension_check"]["items"]) - 1


def test_expected_items_omit_assistant_item_when_disabled(store: ContentStore) -> None:
    sess = _sess(uuid.uuid4())
    on = dashboard.expected_items(sess, store, ai_assistant=True)
    off = dashboard.expected_items(sess, store, ai_assistant=False)
    assert "tp_post_04" in on["post_task.tp_demo"]["items"]
    assert "tp_post_04" not in off["post_task.tp_demo"]["items"]


def _comp_row(sid: uuid.UUID, item_id: str, value: str) -> dict:
    return {
        "session_id": sid,
        "instrument_id": "comprehension_check",
        "item_id": item_id,
        "instrument_version": lifecycle.COMPREHENSION_VERSION,
        "status": "answered",
        "response": {"value": value},
    }


def _post_row(sid: uuid.UUID, item_id: str, status: str = "answered") -> dict:
    return {
        "session_id": sid,
        "instrument_id": "post_task.tp_demo",
        "item_id": item_id,
        "instrument_version": 4,
        "status": status,
        "response": {"value": 4},
    }


def test_flag_off_session_is_not_expected_assistant_items(store: ContentStore) -> None:
    """A session that attempted comprehension without the assistant item is
    provably flag-off — flag-on servers record every rendered answer key — so
    the dashboard must not expect assistant items even while the deployment
    flag is on."""
    sid = uuid.uuid4()
    comp = {sid}
    rows = [
        _comp_row(sid, "partial_intel", "true"),
        _comp_row(sid, "same_crates", "true"),
        _comp_row(sid, "accurate_not_latest", "true"),
        _comp_row(sid, "teammates_may_have_mine", "true"),
        _post_row(sid, "tp_post_01"),
    ]
    cells, _ = dashboard_analysis._item_cells(
        [_sess(sid)], store, {sid: rows}, comprehension_sids=frozenset(comp)
    )
    by_id = {c["item_id"]: c for c in cells}
    assert "assistant_reads_team" not in by_id
    assert "tp_post_04" not in by_id


def test_committed_assistant_row_proves_flag_on_era(store: ContentStore) -> None:
    """A committed assistant item row means the session ran with the flag on —
    it stays expected and reconciled even under a flag-off deployment."""
    sid = uuid.uuid4()
    rows = [_post_row(sid, "tp_post_04")]
    cells, _ = dashboard_analysis._item_cells(
        [_sess(sid)],
        store,
        {sid: rows},
        flags=FeatureFlags(ai_assistant=False),
    )
    by_id = {c["item_id"]: c for c in cells}
    assert by_id["tp_post_04"]["counts"]["answered"] == 1


def test_no_evidence_session_follows_deployment_flag(store: ContentStore) -> None:
    """Sessions too early to carry assistant evidence inherit the deployment
    flag: off → assistant items un-expected; on → expected and absent."""
    sid = uuid.uuid4()
    cells_on, _ = dashboard_analysis._item_cells(
        [_sess(sid)],
        store,
        {},
        flags=FeatureFlags(ai_assistant=True),
    )
    assert {c["item_id"] for c in cells_on} >= {"tp_post_04"}
    cells_off, _ = dashboard_analysis._item_cells(
        [_sess(sid)],
        store,
        {},
        flags=FeatureFlags(ai_assistant=False),
    )
    assert "tp_post_04" not in {c["item_id"] for c in cells_off}
