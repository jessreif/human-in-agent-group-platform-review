"""Choice-item → binary-item expansion in ``dashboard_analysis._item_cells``.

The post-task suspicion item (``tp_post_10``, "which best describes them?") is a
single categorical response; for analysis it must appear as separate binary
items — one per declared option — while the parent row keeps its raw category
distribution.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.domain.manifests.artifacts import InstrumentBody
from app.domain.manifests.store import ContentStore
from app.services import dashboard_analysis
from app.services.lifecycle import LEGACY_OPEN_ENDED_MAX_CHARS


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


def _row(sid: uuid.UUID, item_id: str, status: str, value: object = None) -> dict:
    return {
        "session_id": sid,
        "instrument_id": "post_task.tp_demo",
        "item_id": item_id,
        "instrument_version": 4,
        "status": status,
        "response": None if value is None else {"value": value},
    }


def test_choice_item_emits_one_binary_row_per_option(store: ContentStore) -> None:
    sid = uuid.uuid4()
    cells, nums = dashboard_analysis._item_cells(
        [_sess(sid)],
        store,
        {sid: [_row(sid, "tp_post_10", "answered", "All were automated")]},
    )
    by_id = {c["item_id"]: c for c in cells}
    # Parent row keeps the raw category distribution.
    assert by_id["tp_post_10"]["counts"]["answered"] == 1
    assert by_id["tp_post_10"]["categories"] == {"All were automated": 1}
    # Four derived binary items, one per declared option.
    expected = {
        "tp_post_10@all_were_human": 0.0,
        "tp_post_10@some_were_human_some_were_automated": 0.0,
        "tp_post_10@all_were_automated": 1.0,
        "tp_post_10@i_am_not_sure": 0.0,
    }
    for bid, want in expected.items():
        cell = by_id[bid]
        assert cell["counts"]["answered"] == 1
        assert cell["derived_from"] == "tp_post_10"
        assert cell["numeric"]["n"] == 1
        assert cell["numeric"]["mean"] == want
        assert nums[sid][f"item.post_task.tp_demo.{bid}"] == want


def test_binary_items_inherit_missing_and_invalid_states(store: ContentStore) -> None:
    sid = uuid.uuid4()
    cells, _ = dashboard_analysis._item_cells(
        [_sess(sid)],
        store,
        {sid: [_row(sid, "tp_post_10", "answered", "not a declared option")]},
    )
    by_id = {c["item_id"]: c for c in cells}
    # An undeclared value is invalid on the parent AND on every binary item —
    # never silently counted as a zero.
    assert by_id["tp_post_10"]["counts"]["invalid"] == 1
    for bid in by_id:
        if bid.startswith("tp_post_10@"):
            assert by_id[bid]["counts"]["invalid"] == 1
            assert "numeric" not in by_id[bid]

    # No response row at all → absent propagates, no fabricated zeros.
    cells2, _ = dashboard_analysis._item_cells([_sess(sid)], store, {})
    by_id2 = {c["item_id"]: c for c in cells2}
    assert by_id2["tp_post_10"]["counts"]["absent"] == 1
    for bid in by_id2:
        if bid.startswith("tp_post_10@"):
            assert by_id2[bid]["counts"]["absent"] == 1
            assert "numeric" not in by_id2[bid]


def test_numeric_items_unchanged_by_expansion(store: ContentStore) -> None:
    sid = uuid.uuid4()
    cells, nums = dashboard_analysis._item_cells(
        [_sess(sid)],
        store,
        {sid: [_row(sid, "tp_post_01", "answered", 6)]},
    )
    by_id = {c["item_id"]: c for c in cells}
    assert by_id["tp_post_01"]["numeric"]["mean"] == 6.0
    assert nums[sid]["item.post_task.tp_demo.tp_post_01"] == 6.0
    # No derived rows for a numeric item.
    assert not any(iid.startswith("tp_post_01@") for iid in by_id)


def _oe_row(sid: uuid.UUID, item_id: str, status: str, value: object = None) -> dict:
    return {
        "session_id": sid,
        "instrument_id": "open_ended_followup",
        "item_id": item_id,
        "instrument_version": 1,
        "status": status,
        "response": None if value is None else {"value": value, "seat": None},
    }


def test_open_ended_visited_items_count_only_no_text_surfaces(
    store: ContentStore,
) -> None:
    """UX round 8: open_ended free text counts as answered/skipped but never
    enters numeric or category surfaces — the prose is withheld participant
    text, on the dashboard exactly as in the export."""
    sid = uuid.uuid4()
    visited = {sid: frozenset({"entry", "open_ended"})}
    cells, nums = dashboard_analysis._item_cells(
        [_sess(sid)],
        store,
        {
            sid: [
                _oe_row(sid, "crate_strategy", "answered", "I ranked each crate by recency."),
                _oe_row(sid, "source_reliance", "skipped"),
            ]
        },
        visited,
    )
    oe = {c["item_id"]: c for c in cells if c["instrument_id"] == "open_ended_followup"}
    assert set(oe) == {"crate_strategy", "source_reliance"}
    assert oe["crate_strategy"]["counts"]["answered"] == 1
    assert oe["source_reliance"]["counts"]["skipped"] == 1
    for cell in oe.values():
        assert "numeric" not in cell
        assert "categories" not in cell
    # Free text never registers as an item-derived scalar.
    assert not any(k.startswith("item.open_ended_followup") for k in nums.get(sid, {}))


def test_open_ended_unvisited_rows_unexpected_no_absent_debt(
    store: ContentStore,
) -> None:
    """A session that never entered the stage owes nothing: a stray row counts
    ``unexpected``, and the instrument creates no absent debt."""
    sid = uuid.uuid4()
    cells, _ = dashboard_analysis._item_cells(
        [_sess(sid)],
        store,
        {sid: [_oe_row(sid, "crate_strategy", "answered", "text")]},
        {sid: frozenset({"entry", "post_questionnaire"})},
    )
    oe = [c for c in cells if c["instrument_id"] == "open_ended_followup"]
    assert len(oe) == 1
    assert oe[0]["counts"]["unexpected"] == 1
    assert oe[0]["counts"]["absent"] == 0


_OPEN_ENDED_PROSE = "I ranked each crate by recency."
_OPEN_ENDED_LIMIT = 4000


def _open_ended_session(sid: uuid.UUID, store: ContentStore, *, cohort: str) -> dict:
    sess = _sess(sid)
    artifacts = dict(sess["binding"]["artifacts"])
    if cohort == "current":
        stored = store.artifacts["open_ended.tp_followup@v1"]
        artifacts["instruments.open_ended"] = [stored.key, stored.sha256]
    else:
        assert cohort == "legacy"
        artifacts.pop("instruments.open_ended", None)
    sess["binding"] = {"artifacts": artifacts}
    return sess


@pytest.mark.parametrize(
    ("cohort", "instrument_id"),
    [
        ("legacy", "open_ended_followup"),
        ("current", "open_ended.tp_followup"),
    ],
)
def test_open_ended_cohort_reconciles_answered_invalid_absent_unexpected(
    store: ContentStore, cohort: str, instrument_id: str
) -> None:
    """Legacy ``open_ended_followup`` and published ``open_ended.tp_followup``.

    Dropping the current artifact from ``expected_items`` turns the current
    cohort's answered/absent rows into unexpected and fails this parameter.
    The legacy parameter has no ``instruments.open_ended`` binding, so it still
    resolves ``open_ended_instrument_spec()`` and does not depend on that slot.
    """
    if cohort == "current":
        assert "tp_ai_v13" in store.published.manifests
        ref = store.manifests["tp_ai_v13"].manifest.instruments.open_ended
        assert ref is not None
        assert ref.artifact_id == instrument_id
        assert ref.version == 1
        assert ref.key == "open_ended.tp_followup@v1"
        body = store.artifacts[ref.key].artifact.body
        assert isinstance(body, InstrumentBody)
        assert body.response.max == _OPEN_ENDED_LIMIT
        assert [item.item_id for item in body.items] == ["crate_strategy", "source_reliance"]
    else:
        assert instrument_id == "open_ended_followup"
        assert LEGACY_OPEN_ENDED_MAX_CHARS == _OPEN_ENDED_LIMIT

    at_limit = "y" * _OPEN_ENDED_LIMIT
    over_limit = "y" * (_OPEN_ENDED_LIMIT + 1)
    answered_sid, invalid_sid, absent_sid, stray_sid = (uuid.uuid4() for _ in range(4))
    sessions = [
        _open_ended_session(sid, store, cohort=cohort)
        for sid in (answered_sid, invalid_sid, absent_sid, stray_sid)
    ]
    if cohort == "legacy":
        assert all("instruments.open_ended" not in s["binding"]["artifacts"] for s in sessions)
    else:
        assert all(
            s["binding"]["artifacts"]["instruments.open_ended"][0] == "open_ended.tp_followup@v1"
            for s in sessions
        )

    def row(sid: uuid.UUID, item_id: str, status: str, value: object = None) -> dict:
        return {
            "session_id": sid,
            "instrument_id": instrument_id,
            "item_id": item_id,
            "instrument_version": 1,
            "status": status,
            "response": None if value is None else {"value": value, "seat": None},
        }

    visited = {
        answered_sid: frozenset({"open_ended"}),
        invalid_sid: frozenset({"open_ended"}),
        absent_sid: frozenset({"open_ended"}),
        stray_sid: frozenset({"entry"}),
    }
    cells, nums = dashboard_analysis._item_cells(
        sessions,
        store,
        {
            answered_sid: [
                row(answered_sid, "crate_strategy", "answered", _OPEN_ENDED_PROSE),
                row(answered_sid, "source_reliance", "answered", "   "),
            ],
            invalid_sid: [
                row(invalid_sid, "crate_strategy", "answered", over_limit),
                row(invalid_sid, "source_reliance", "answered", at_limit),
            ],
            stray_sid: [row(stray_sid, "crate_strategy", "answered", _OPEN_ENDED_PROSE)],
        },
        visited,
    )
    oe = [c for c in cells if c["instrument_id"] == instrument_id]
    assert sorted(c["item_id"] for c in oe) == ["crate_strategy", "source_reliance"]
    by_id = {c["item_id"]: c for c in oe}
    assert by_id["crate_strategy"]["counts"] == {
        "answered": 1,
        "skipped": 0,
        "missing": 0,
        "absent": 1,
        "invalid": 1,
        "unexpected": 1,
    }
    assert by_id["source_reliance"]["counts"] == {
        "answered": 1,
        "skipped": 0,
        "missing": 0,
        "absent": 1,
        "invalid": 1,
        "unexpected": 0,
    }
    assert by_id["crate_strategy"]["sessions_expected"] == 3
    assert by_id["source_reliance"]["sessions_expected"] == 3
    for cell in oe:
        assert "numeric" not in cell
        assert "categories" not in cell
    rendered = json.dumps(cells)
    assert _OPEN_ENDED_PROSE not in rendered
    assert at_limit not in rendered
    assert over_limit not in rendered
    assert not any(
        key.startswith(f"item.{instrument_id}") for values in nums.values() for key in values
    )
    if cohort == "legacy":
        assert not any(c["instrument_id"] == "open_ended.tp_followup" for c in cells)


def test_open_ended_blank_or_nonstring_is_invalid(store: ContentStore) -> None:
    sid = uuid.uuid4()
    visited = {sid: frozenset({"open_ended"})}
    cells, _ = dashboard_analysis._item_cells(
        [_sess(sid)],
        store,
        {
            sid: [
                _oe_row(sid, "crate_strategy", "answered", "   "),
                _oe_row(sid, "source_reliance", "answered", 42),
            ]
        },
        visited,
    )
    oe = {c["item_id"]: c for c in cells if c["instrument_id"] == "open_ended_followup"}
    assert oe["crate_strategy"]["counts"]["invalid"] == 1
    assert oe["source_reliance"]["counts"]["invalid"] == 1
    for cell in oe.values():
        assert "numeric" not in cell and "categories" not in cell


def test_comprehension_firsts_uses_earliest_attempt_only() -> None:
    """The first-attempt missed list is the seq-earliest row — retries that
    pass later never rewrite what the participant missed first."""
    a, b = uuid.uuid4(), uuid.uuid4()
    rows = [
        (a, ["same_crates", "assistant_reads_team"]),
        (a, ["same_crates"]),
        (a, []),
        (b, []),
    ]
    firsts = dashboard_analysis._comprehension_firsts(rows)
    assert firsts[a] == ["same_crates", "assistant_reads_team"]
    assert firsts[b] == []
    # Non-list payloads degrade to an empty list rather than fabricating misses.
    c = uuid.uuid4()
    assert dashboard_analysis._comprehension_firsts([(c, None)])[c] == []


def _comp_row(sid: uuid.UUID, item_id: str, value: object) -> dict:
    """The row shape ``analysis()`` synthesizes from first-attempt answers."""
    return {
        "session_id": sid,
        "instrument_id": "comprehension_check",
        "item_id": item_id,
        "instrument_version": 1,
        "status": "answered",
        "response": {"value": value},
    }


def test_comprehension_items_render_choice_cells_and_binaries(
    store: ContentStore,
) -> None:
    """The five gate items reconcile as true/false choice items: categorical
    first-attempt distribution on the parent row plus one derived binary row
    per option — the same surface tp_post_10 gets."""
    sid = uuid.uuid4()
    comp = {sid}
    cells, nums = dashboard_analysis._item_cells(
        [_sess(sid)],
        store,
        {
            sid: [
                _comp_row(sid, "partial_intel", "true"),
                _comp_row(sid, "same_crates", "true"),  # wrong — but the cell shows the answer
                _comp_row(sid, "accurate_not_latest", "true"),
                _comp_row(sid, "teammates_may_have_mine", "true"),
                _comp_row(sid, "assistant_reads_team", "false"),
            ]
        },
        comprehension_sids=comp,
    )
    comp_cells = [c for c in cells if c["instrument_id"] == "comprehension_check"]
    by_id = {c["item_id"]: c for c in comp_cells}
    for iid in (
        "partial_intel",
        "same_crates",
        "accurate_not_latest",
        "teammates_may_have_mine",
        "assistant_reads_team",
    ):
        assert by_id[iid]["counts"]["answered"] == 1
        assert by_id[iid]["sessions_expected"] == 1
    assert by_id["same_crates"]["categories"] == {"true": 1}
    assert by_id["partial_intel"]["categories"] == {"true": 1}
    # Derived binaries give the per-option first-attempt share.
    assert by_id["same_crates@true"]["numeric"]["mean"] == 1.0
    assert by_id["same_crates@false"]["numeric"]["mean"] == 0.0
    assert nums[sid]["item.comprehension_check.same_crates@true"] == 1.0


def test_comprehension_items_absent_for_never_attempted(
    store: ContentStore,
) -> None:
    """Sessions with no comprehension event owe nothing — pre-gate sessions
    and pre-check participants get no cells at all."""
    sid = uuid.uuid4()
    cells, _ = dashboard_analysis._item_cells(
        [_sess(sid)], store, {sid: [_comp_row(sid, "partial_intel", "true")]}
    )
    comp = [c for c in cells if c["instrument_id"] == "comprehension_check"]
    # No expected set → a stray row counts unexpected, no absent debt.
    assert len(comp) == 1 and comp[0]["counts"]["unexpected"] == 1


def test_comprehension_nonbool_answer_is_invalid(store: ContentStore) -> None:
    sid = uuid.uuid4()
    cells, _ = dashboard_analysis._item_cells(
        [_sess(sid)],
        store,
        {sid: [_comp_row(sid, "partial_intel", "maybe")]},
        comprehension_sids={sid},
    )
    comp = {c["item_id"]: c for c in cells if c["instrument_id"] == "comprehension_check"}
    assert comp["partial_intel"]["counts"]["invalid"] == 1
    assert "categories" not in comp["partial_intel"]
