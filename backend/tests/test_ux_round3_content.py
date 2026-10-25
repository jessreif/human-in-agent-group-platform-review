"""UX round 3 content contracts: the FR-1 interaction-mode profiles and the required
own pressure rating (UX item 5), plus hash stability for every earlier publication."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.domain.manifests import cli
from app.domain.manifests.diff import check_paired_arms, profile_diff
from app.domain.manifests.schema import Manifest
from app.services import engines
from tests.wp02 import bound, profile_data, store

ROUND3 = ("tp_demo_v4", "pe_demo_v4", "tp_review_first_v5", "pe_pilot_v5")
NL_ONLY = ("tp_demo_nl_v1", "pe_demo_nl_v1")
# O-1..O-3 re-issues (fallback_bank v3) — same interaction declarations as round 3.
O_DECISIONS = (
    "tp_demo_v5",
    "pe_demo_v5",
    "tp_review_first_v6",
    "pe_pilot_v6",
    "tp_demo_nl_v2",
    "pe_demo_nl_v2",
)
# UX round 4 re-issues (UX_Feedback_v4) — also carry the interaction declaration.
ROUND4 = (
    "tp_demo_v6",
    "pe_demo_v6",
    "tp_review_first_v7",
    "pe_pilot_v7",
    "tp_demo_nl_v3",
    "pe_demo_nl_v3",
)
# 2026-10-15 crate-condition experiment — tp_ai clones carry nl_only too.
CRATE_CONDITION = (
    "tp_ai_ovnone_v1",
    "tp_ai_ovsome_v1",
    "tp_ai_ovfull_v1",
    "tp_ai_ovsome_thin_v1",
    "tp_ai_ovnone_v2",
    "tp_ai_ovsome_v2",
    "tp_ai_ovfull_v2",
    "tp_ai_ovsome_thin_v2",
    "tp_ai_ovnone_thin_v1",
    "tp_ai_ovfull_thin_v1",
    "tp_ai_ovnone_thin_v2",
    "tp_ai_ovfull_thin_v2",
)
STUDENT_UX = (
    "tp_ai_v14",
    "tp_ai_ovnone_v3",
    "tp_ai_ovsome_v3",
    "tp_ai_ovfull_v3",
    "tp_ai_ovnone_thin_v3",
    "tp_ai_ovsome_thin_v3",
    "tp_ai_ovfull_thin_v3",
)
# v15 (2026-10-17): workload relabel + the mixed teammate-level arm.
V15_WORKLOAD = (
    "tp_ai_ovsome_thin_v4",
    "tp_ai_ovsome_thin_v5",
    "tp_ai_ovsome_thin_v6",
    "tp_ai_ovsome_thin_v7",
    "tp_ai_ovsome_thin_v8",
)


@pytest.mark.parametrize("manifest_id", ROUND3 + NL_ONLY)
def test_round3_profiles_declare_their_mode_and_keep_arms_identical(manifest_id: str) -> None:
    b = bound(manifest_id)
    want = "nl_only" if manifest_id in NL_ONLY else "structured_and_nl"
    assert b.manifest.interaction_mode == want
    # The flag never differs between arms (only manipulation.arm.* may).
    assert all("interaction" not in e.path for e in check_paired_arms(b))


@pytest.mark.parametrize("manifest_id", NL_ONLY)
def test_nl_only_never_opens_structured_seeking(manifest_id: str) -> None:
    for phase in bound(manifest_id).manifest.channels.phases:
        assert "structured_seeking" not in phase.enabled


def test_nl_only_cannot_open_structured_seeking_by_construction() -> None:
    data = profile_data("tp_demo_v3")
    data["interaction"] = {"mode": "nl_only"}
    with pytest.raises(ValidationError, match="nl_only profiles may not open structured_seeking"):
        Manifest.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(("left", "right"), cli.PROFILE_VARIANTS)
def test_variants_differ_only_in_the_interaction_mode(left: str, right: str) -> None:
    paths = {e.path for e in profile_diff(bound(left), bound(right))}
    assert paths == {"behavior.controls.interaction_mode", "behavior.channels"}


def test_pre_round3_profiles_hash_exactly_as_published() -> None:
    """``interaction`` is omitted from the dump when unset, so adding the field changed no
    earlier manifest hash (publication verification would fail otherwise)."""
    for manifest_id in cli.PROFILES:
        if (
            manifest_id
            in ROUND3 + NL_ONLY + O_DECISIONS + ROUND4 + CRATE_CONDITION + STUDENT_UX + V15_WORKLOAD
        ):
            continue
        m = store().manifests[manifest_id].manifest
        assert m.interaction is None
        assert "interaction" not in m.model_dump(mode="json")
        assert m.interaction_mode == "structured_and_nl"


@pytest.mark.parametrize("manifest_id", ["tp_demo_v4", "tp_review_first_v5", "tp_demo_nl_v1"])
def test_round3_tp_profiles_require_the_own_rating(manifest_id: str) -> None:
    checkin = bound(manifest_id).manifest.timing.checkin
    assert checkin is not None and checkin.own_rating_skippable is False


@pytest.mark.parametrize("artifact", ["fallbacks.demo@v2", "fallbacks.neutral@v2"])
def test_round3_fallback_lines_pass_the_output_gate(artifact: str) -> None:
    body = store().artifacts[artifact].artifact.body  # type: ignore[attr-defined]
    for rule in body.rules:
        for line in rule.lines:
            assert engines.validate_teammate_output(line) == [], (rule.when, line)


def test_neutral_bank_stays_pressure_neutral() -> None:
    body = store().artifacts["fallbacks.neutral@v2"].artifact.body  # type: ignore[attr-defined]
    for rule in body.rules:
        for line in rule.lines:
            assert not engines._URGENCY.search(line)
            for word in ("busy", "stuck", "swamped", "buried", "time"):
                assert word not in line.lower(), line
