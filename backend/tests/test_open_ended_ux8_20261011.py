"""UX round 8 (2026-10-11) + BA-105 (2026-10-12): the open-ended follow-up stage.

Two sequential free-text questions sit between ``post_questionnaire`` and
``debrief`` on TP flows — since BA-105 they are artifact-bound content
(``instruments.open_ended`` → ``open_ended.tp_followup@v1``), not a code-defined
stage. Manifests without the pin have no open_ended stage at all; v10-era rows
keep the legacy instrument id ``open_ended_followup``. PE keeps its direct
post_questionnaire -> debrief edge.
"""

from __future__ import annotations

import pytest

from app.db.schema import STAGES
from app.domain import stage_graph as sg
from app.domain.manifests.artifacts import InstrumentBody
from app.domain.manifests.binding import bind
from app.domain.manifests.store import ContentStore
from app.services import lifecycle


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def test_open_ended_is_a_stage_in_order() -> None:
    i_post, i_oe, i_deb = (
        STAGES.index("post_questionnaire"),
        STAGES.index("open_ended"),
        STAGES.index("debrief"),
    )
    assert i_post < i_oe < i_deb


@pytest.mark.parametrize("variant", ["tp_round_robin", "tp_review_first"])
def test_tp_variants_route_through_open_ended(variant: sg.StageVariant) -> None:
    targets = sg.legal_targets(variant, "post_questionnaire")
    assert "open_ended" in targets
    assert sg.legal_targets(variant, "open_ended") - sg._EXCEPTIONAL == {"debrief"}


def test_pe_keeps_the_direct_debrief_edge() -> None:
    assert "open_ended" not in sg.legal_targets("pe", "post_questionnaire")


def test_open_ended_is_an_instrument_stage() -> None:
    for kind in ("item_response", "item_skip", "proceed"):
        assert "open_ended" in lifecycle.COMMAND_STAGES[kind]


def test_open_ended_artifact_contract(store: ContentStore) -> None:
    """BA-105: the bound artifact carries the wording, item ids and policy."""
    stored = store.artifacts["open_ended.tp_followup@v1"]
    body = stored.artifact.body
    assert isinstance(body, InstrumentBody)
    assert body.applies_to == "session"
    assert body.response.type == "free_text"
    assert body.response.max == 4000
    assert [i.item_id for i in body.items] == ["crate_strategy", "source_reliance"]
    # Both required (server rejects item_skip), no choice options.
    assert all(i.skippable is False and i.options is None for i in body.items)
    # UX feedback v8 wording, verbatim.
    assert body.items[0].text.startswith("There was no single right way")
    assert body.items[1].text.startswith("When you weren't sure about a crate")


def test_instrument_items_resolves_the_bound_artifact(store: ContentStore) -> None:
    bound = bind(store, "tp_ai_v13", mode="qa")
    items, applies_to = lifecycle.instrument_items(bound, "open_ended")
    assert [i["item_id"] for i in items] == ["crate_strategy", "source_reliance"]
    assert all(i["free_text"] and i["max_chars"] == 4000 for i in items)
    assert applies_to == "session"


def test_unbound_manifests_have_no_open_ended_items(store: ContentStore) -> None:
    """BA-105 manifest policy: profiles without the pin (every manifest
    published before the slot existed) expose no open_ended instrument — the
    code no longer injects a code-defined stage."""
    bound = bind(store, "tp_ai_v12", mode="qa")
    assert bound.artifacts.get("instruments.open_ended") is None
    items, applies_to = lifecycle.instrument_items(bound, "open_ended")
    assert items == [] and applies_to is None


def test_advance_targets_are_variant_aware() -> None:
    """post_questionnaire feeds open_ended only when the slot is bound (TP);
    open_ended always feeds debrief. Verified in the integration suite against
    real sessions — here we pin the id surface the advance map consumes."""
    assert lifecycle.OPEN_ENDED_INSTRUMENT_ID == "open_ended_followup"  # legacy (v10)
    assert (
        frozenset(["open_ended_followup", "open_ended.tp_followup"])
        == lifecycle.OPEN_ENDED_INSTRUMENT_IDS
    )
