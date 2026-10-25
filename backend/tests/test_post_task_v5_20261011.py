"""UX v8 follow-up #2 content contracts (2026-10-11): post_task.tp_demo@v5 adds
the teammate-direction manipulation check ("After the check-in, my teammates
seemed more rushed.") immediately after tp_post_09, and the successor preview
pair tp_ai_v11/tp_scripted_v13 binds it. The teammate-nature choice item keeps
its own-page last slot and every earlier item stays non-choice."""

from __future__ import annotations

from tests.wp02 import bound, store

NEW_PAIR = ("tp_ai_v11", "tp_scripted_v13")


def test_v5_places_teammate_rushed_right_after_own_rushed() -> None:
    body = store().artifacts["post_task.tp_demo@v5"].artifact.body  # type: ignore[attr-defined]
    ids = [i.item_id for i in body.items]
    assert ids.index("tp_post_11") == ids.index("tp_post_09") + 1
    item = body.items[ids.index("tp_post_11")]
    assert item.text == "After the check-in, my teammates seemed more rushed."
    assert item.skippable is False
    assert item.options is None  # likert — renders on the "A few questions" page


def test_v5_keeps_suspicion_item_last_and_likert_only_earlier() -> None:
    """tp_post_10's own-page placement (UX v5 item 4) survives the insert."""
    body = store().artifacts["post_task.tp_demo@v5"].artifact.body  # type: ignore[attr-defined]
    assert body.items[-1].item_id == "tp_post_10"
    assert all(i.options is None for i in body.items[:-1])
    assert [i.item_id for i in body.items] == [
        "tp_post_01",
        "tp_post_02",
        "tp_post_03",
        "tp_post_04",
        "tp_post_05",
        "tp_post_06",
        "tp_post_07",
        "tp_post_08",
        "tp_post_09",
        "tp_post_11",
        "tp_post_10",
    ]


def test_v4_is_unchanged() -> None:
    """The v4 lineage stays byte-stable for sessions bound to it."""
    body = store().artifacts["post_task.tp_demo@v4"].artifact.body  # type: ignore[attr-defined]
    assert [i.item_id for i in body.items] == [f"tp_post_{i:02d}" for i in range(1, 11)]


def test_successor_pair_binds_post_task_v5() -> None:
    for manifest_id in NEW_PAIR:
        b = bound(manifest_id)
        assert b.artifacts["instruments.post_task"].key == "post_task.tp_demo@v5"


def test_prior_pair_keeps_post_task_v4() -> None:
    for manifest_id in ("tp_ai_v10", "tp_scripted_v12"):
        b = bound(manifest_id)
        assert b.artifacts["instruments.post_task"].key == "post_task.tp_demo@v4"
