"""2026-10-10 ask-routing revision (unit pieces).

Live-log review of the v8 fielding showed typed intel asks dying at the
classifier (multi-crate text resolved at most one crate, bare-location shapes
none) and the scripted 130 s ask targeting team-dependent crates the asking
seat already held the truth for. Covered here:

- ``crates_referenced`` / ``wants_intel_ask`` — multi-crate fan-out extraction,
  the cap's input set, and the hold-the-line cases (statements stay social).
- ``unresolved_intel_ask`` — intel-flavoured text naming no crate routes to the
  deterministic "name the crate" invite, not generation.
- ``plan_scripted_requests`` ``assigned_own`` — the asking seat never holds
  records on the crate it asks about, with a seat swap and a last-resort pick
  when holdings make the clean choice impossible.
"""

from __future__ import annotations

import pytest

from app.domain.manifests.binding import bind
from app.domain.rng import RandomStreams
from app.domain.schedules import PlanningError, plan_scripted_requests
from app.services import engines
from app.timing.policy import resolve_policy
from tests.wp02 import SEED, copy_content, edit_json, policy, republished_store

# ----- crates_referenced ------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("3 cannery. 4 blackwell, 6 ridgeline, 7 sawmill?", (3, 4, 6, 7)),
        ("what about crate 5?", (5,)),
        ("crate 5 and #7", (5, 7)),
        ("where are crates 2, 4 and 6?", (2, 4, 6)),
        # deduped, mention order
        ("crate 3 then crate 3 again", (3,)),
        # clock text is not a crate reference
        ("dropped it at 5:30 pm", ()),
        ("crate 2 at 5:30 pm", (2,)),
        # first-person possession clauses are not references
        ("i have 3, do you have 5?", (5,)),
        ("we got 4", ()),
        ("ok cool", ()),
        ("9 crates?", ()),  # out of range digit never resolves
    ],
)
def test_crates_referenced(text: str, want: tuple[int, ...]) -> None:
    assert engines.crates_referenced(text) == want


# ----- wants_intel_ask ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "want"),
    [
        # the observed lost ask (913b38af): location guesses with a trailing "?"
        ("3 cannery. 4 blackwell, 6 ridgeline, 7 sawmill?", True),
        ("where is crate 3, 4 and 6?", True),
        ("any intel on crates 2 and 5?", True),
        ("what about 3 and 5?", True),
        ("7 sawmill?", True),  # single crate + bare "?"
        ("do you have anything on crate 4?", True),
        # holdings statements never fan out — strong markers only
        ("i don't have just 2, 5, 6, 7", False),
        ("i have crates 3 and 5", False),
        ("crates 2, 4, 6, 8 are mine", False),
        ("ok cool", False),
    ],
)
def test_wants_intel_ask(text: str, want: bool) -> None:
    assert engines.wants_intel_ask(text, engines.crates_referenced(text)) is want


# ----- unresolved_intel_ask -----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("any updates?", True),
        ("did you hear anything?", True),
        ("any news on your end?", True),
        ("got anything for me?", True),
        # a named crate is never "unresolved" — it fans out or asks directly
        ("any updates on crate 3?", False),
        ("crate 3?", False),
        # statements and pleasantries stay social
        ("i have crate 3", False),
        ("hello there", False),
        ("thanks!", False),
        ("sounds good", False),
    ],
)
def test_unresolved_intel_ask(text: str, want: bool) -> None:
    assert engines.unresolved_intel_ask(text) is want


# ----- assigned_own scripted target -------------------------------------------

ASSIGNED = (1, 3, 5, 7)
UNASSIGNED = (2, 4, 6, 8)
OWN = (1, 5)


def _plans(seed: str, held: dict[str, frozenset[int]], policy_id: str = "tp_ai_v9"):
    p = policy(policy_id, "high")
    return plan_scripted_requests(
        p,
        RandomStreams(seed),
        assigned=ASSIGNED,
        unassigned=UNASSIGNED,
        own_assigned=OWN,
        held_crates=held,
    )


def test_new_profiles_bind_assigned_own_for_the_middle_ask() -> None:
    for manifest_id in ("tp_ai_v9", "tp_scripted_v11"):
        p = policy(manifest_id, "high")
        assert p.scripted_requests["targets"] == ["unassigned", "assigned_own", "unassigned"]
        assert p.scripted_requests["times_ms"] == [45_000, 130_000, 210_000]


def test_assigned_own_never_targets_a_crate_the_asker_holds() -> None:
    held = {"JT": frozenset({1, 3}), "PM": frozenset({5, 7})}
    for i in range(200):
        plans = _plans(f"{i:064x}", held)
        ask = next(pl for pl in plans if pl.target == "assigned_own")
        assert ask.crate in OWN
        assert ask.crate not in held[ask.seat]


def test_assigned_own_swaps_to_the_seat_that_holds_nothing() -> None:
    # JT holds records on both own-file crates — every assigned_own ask must
    # land on PM regardless of the drawn seat (the coin favours neither path).
    held = {"JT": frozenset({1, 5}), "PM": frozenset({3, 7})}
    for i in range(120):
        plans = _plans(f"{i:064x}", held)
        ask = next(pl for pl in plans if pl.target == "assigned_own")
        assert ask.seat == "PM"
        assert ask.crate in OWN


def test_assigned_own_last_resort_when_both_seats_cover() -> None:
    # Both seats hold something on every own crate — the ask still plans (the
    # world cannot satisfy "nobody holds it"), kept in keyed pool order.
    held = {"JT": frozenset({1, 5}), "PM": frozenset({1, 5})}
    for i in range(60):
        plans = _plans(f"{i:064x}", held)
        ask = next(pl for pl in plans if pl.target == "assigned_own")
        assert ask.crate in OWN


def test_assigned_own_refuses_when_the_own_pool_is_too_small(tmp_path) -> None:
    root = copy_content(tmp_path / "own")
    edit_json(
        root / "profiles" / "tp_ai_v9.json",
        lambda d: d["timing"]["scripted_requests"].update(
            targets=["assigned_own", "assigned_own", "unassigned"]
        ),
    )
    b = bind(republished_store(root), "tp_ai_v9", mode="preview")
    p = resolve_policy(b, "high")
    with pytest.raises(PlanningError):
        plan_scripted_requests(
            p,
            RandomStreams(SEED),
            assigned=ASSIGNED,
            unassigned=UNASSIGNED,
            own_assigned=(1,),
            held_crates={},
        )


def test_legacy_profiles_keep_the_plain_assigned_target() -> None:
    # The published v8/v10 fielding profiles are immutable: they keep
    # ``assigned`` semantics exactly (team-dependent targets stay reachable).
    for manifest_id in ("tp_ai_v8", "tp_scripted_v10"):
        p = policy(manifest_id, "high")
        assert p.scripted_requests["targets"] == ["unassigned", "assigned", "unassigned"]


# ----- successor-profile bindings ----------------------------------------------


def test_tp_ai_v9_binds_the_revised_social_prompt_and_training() -> None:
    from app.domain.manifests.store import ContentStore

    b = bind(ContentStore.load_default(), "tp_ai_v9", mode="preview")
    assert b.artifacts["teammates.social_prompt"].key == "prompt.social_demo@v4"
    assert b.artifacts["instruments.training"].key == "training.reference@v5"
    # consent/payment/debrief pins ride unchanged from v8.
    assert b.artifacts["instruments.consent"].key == "consent.reference@v8"
    assert b.artifacts["instruments.debrief"].key == "debrief.tp_reference@v3"


def test_tp_scripted_v11_keeps_deterministic_teammates() -> None:
    from app.domain.manifests.store import ContentStore

    b = bind(ContentStore.load_default(), "tp_scripted_v11", mode="preview")
    assert b.manifest.teammates.social_generation == "deterministic_only"
    assert b.artifacts["instruments.training"].key == "training.reference@v5"


def test_social_prompt_v4_drops_the_nonexistent_request_affordance() -> None:
    from app.domain.manifests.store import ContentStore

    art = ContentStore.load_default().artifacts["prompt.social_demo@v4"]
    text = art.artifact.body.text  # type: ignore[union-attr]
    # Never instructs the participant to "send a request" — the typed ask IS
    # the request in nl_only.
    assert "send you a request" not in text
    assert "send me a request" not in text
    # and instructs the teammate to invite a single-crate ask instead of
    # absorbing the message.
    assert "ask which crate they mean" in text
