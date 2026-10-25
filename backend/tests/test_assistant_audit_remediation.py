"""Nonvacuous positive and adversarial contracts for the rejection audit."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.world import CRATE_CONTENTS, T_ARRIVE, T_DEPART, T_STILL, T_TRANSIT
from app.providers.base import ProviderError, ProviderErrorKind
from app.providers.local import LocalChatProvider
from app.services import engines, output_claims, prompts
from app.services.assistant_monitor import summarize
from app.services.evidence import EvidenceRecord
from tests.test_wp10a_grounding import DIGEST, MODEL, _ok_body, _provider, _rec, _request

TEMPLATES = [
    (kind, template)
    for kind, templates in (
        ("arrival", T_ARRIVE),
        ("still_at", T_STILL),
        ("departure", T_DEPART),
        ("transit", T_TRANSIT),
    )
    for template in templates
]


@pytest.mark.parametrize(("kind", "template"), TEMPLATES)
def test_all_authoritative_templates_have_exact_positive_coverage(kind: str, template: str) -> None:
    record = {
        **_rec("C3-1", 3, "The Marina", kind),
        "body": template.format(n=3, c=CRATE_CONTENTS[2], h="The Marina", to="Old Cannery"),
        "toward": "Old Cannery" if kind == "transit" else None,
    }
    text = f"{record['stamp']} {record['body']}"
    check = output_claims.check_assistant_output(text, crate=3, records=[record])
    assert not check.violations and check.claims
    assert all(c.event_type == kind and c.evidence_time == record["stamp"] for c in check.claims)
    assert not engines.validate_teammate_output(
        text, allowed_locations=frozenset({"The Marina", "Old Cannery"})
    )
    for mutated in (
        text.replace("2:00", "11:59"),
        text.replace("Marina", "Gotham Depot"),
        text + " There are ninety records.",
    ):
        assert output_claims.check_assistant_output(mutated, crate=3, records=[record]).violations


@pytest.mark.parametrize(
    "text",
    [
        "Today crate 3 is still at The Marina.",
        "Crate 3 arrived at The Marina today at 11:59 PM.",
        "Crate 3 is at Gotham Depot.",
        "I cannot find any entries for crate 3.",
        "There are exactly ninety records for crate 3.",
        "Crate 4 is at The Sawmill.",
        "The tablets are still at The Marina.",
        "Crate three is at The Marina and crate four is at The Sawmill.",
        "Crate 3 has not moved to The Marina.",
        "Crate 3 was not transferred to The Marina.",
        "Crate 3 is at The Marina and headed toward Old Cannery.",
    ],
)
def test_reported_adversarial_assertions_reject(text: str) -> None:
    records = [
        _rec("C3-1", 3, "The Marina", "arrival", 1),
        _rec("C3-2", 3, "The Marina", "departure", 2),
        {**_rec("C3-3", 3, "The Marina", "transit", 3), "toward": "Old Cannery"},
        _rec("C4-1", 4, "The Sawmill", "still_at", 1),
    ]
    assert output_claims.check_assistant_output(text, crate=3, records=records).violations


def test_citations_are_local_and_idempotent() -> None:
    records = [_rec("C3-1", 3, "The Marina"), _rec("C4-1", 4, "The Sawmill")]
    for citations in ("C3-1", "C3-1 and C3-1"):
        checked = output_claims.check_assistant_output(
            f"Entries {citations} say crate 3 is at The Marina.", crate=3, records=records
        )
        assert not checked.violations
        assert checked.claims[0].claimed_citations == ("C3-1",)
    wrong = output_claims.check_assistant_output(
        "Entry C4-1 says crate 3 is at The Marina.", crate=3, records=records
    )
    assert "citation_mismatch" in wrong.violations
    assert wrong.claims[0].verdict == "unsupported"


@pytest.mark.parametrize("text", ["What about 3?", "the third one", "#3 please", "crate three"])
def test_normalized_scope(text: str) -> None:
    assert engines.assistant_scope(text) == 3
    assert engines.assistant_scope("meet at 3:00 PM") is None
    with pytest.raises(ValueError):
        engines.assistant_scope("crate 4", 3)


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        b"[]",
        b'{"choices":42}',
        b"[" * 2000,
        _ok_body("bad\x00text"),
        _ok_body("bad\ud800text"),
    ],
)
def test_invalid_completion_has_stable_taxonomy(raw: bytes) -> None:
    with pytest.raises(ProviderError) as error:
        _provider(body=raw).generate(_request(), timeout_s=5)
    assert error.value.kind == ProviderErrorKind.INVALID_RESPONSE


@pytest.mark.parametrize("raw", [b"\xff", b'{"models":42}', b"[" * 2000])
def test_invalid_listing_has_stable_taxonomy(raw: bytes) -> None:
    provider = LocalChatProvider(
        "http://127.0.0.1:11434",
        model=MODEL,
        model_digest=DIGEST,
        get_transport=lambda url, timeout: (200, raw),
    )
    with pytest.raises(ProviderError) as error:
        provider.served_model_digest()
    assert error.value.kind == ProviderErrorKind.INVALID_RESPONSE


def test_semantic_hash_detects_body_and_destination_drift() -> None:
    record = {**_rec("C3-1", 3, "The Marina", "transit"), "toward": "Old Cannery"}
    manifest = [
        {
            "record_id": "C3-1",
            "sha256": record["content_sha256"],
            "format": "semantic_record_v1",
            "semantic_sha256": EvidenceRecord.parse(record).digest(),
        }
    ]
    for field, value in (("body", "changed"), ("toward", "The Sawmill"), ("crate", 4)):
        with pytest.raises(prompts.PromptBuildError):
            prompts.resolve_manifest_records(manifest, [{**record, field: value}])


def test_monitor_distinguishes_failure_delivery_and_deterministic_routes() -> None:
    now = datetime.now(UTC)
    base = dict(
        deterministic=False,
        attempts=1,
        statuses=["validation_rejected"],
        violations=["unsupported_claim"],
        delivered=True,
        generated=False,
        rendered=True,
        terminal=False,
        accepted_at=now - timedelta(seconds=200),
    )
    rejected = summarize([base] * 7, now)
    assert rejected["acceptance"] == 0 and rejected["delivered"] == 7
    assert "assistant_generation_degraded" in rejected["alerts"]
    deterministic = summarize([{**base, "deterministic": True, "statuses": []}], now)
    assert deterministic["acceptance"] is None and deterministic["intended_provider"] == 0
    lost = summarize([{**base, "statuses": ["succeeded"], "delivered": False}], now)
    assert "assistant_delivery_stalled" in lost["alerts"]
    assert "generated_output_not_delivered" in lost["alerts"]
    assert json.dumps(rejected)


def test_monitor_marks_partitions_against_deployed_build() -> None:
    # model_call rows are immutable history: the monitor must label which
    # partitions were recorded on the deployed build so superseded incident
    # alerts are never read as live failures.
    from app.services.assistant_monitor import _mark_current

    superseded = {
        "build": "a" * 64,
        "profile": "tp_ai_v3",
        "alerts": ["assistant_generation_degraded"],
    }
    current = {"build": "b" * 64, "profile": "tp_ai_v6", "alerts": []}
    marked = _mark_current([dict(superseded), dict(current)], "b" * 64)
    assert marked[0]["build"] == "b" * 64 and marked[0]["current"] is True
    assert marked[1]["build"] == "a" * 64 and marked[1]["current"] is False
    # Without a deployed-build identity nothing may claim currentness.
    assert _mark_current([dict(current)], None)[0]["current"] is False


def test_release_gate_refuses_partial_or_vacuous_evidence() -> None:
    from copy import deepcopy
    from itertools import product
    from pathlib import Path

    from app.services.assistant_release import validate_evidence
    from app.services.generation_policy import identity

    panel = json.loads(
        (Path(__file__).resolve().parents[2] / "release/assistant-panel-v1.json").read_text()
    )
    build, model = "a" * 64, "model@sha256:" + "b" * 64
    rows = []
    for profile, arm, seed, probe in product(
        panel["profiles"], panel["arms"], panel["seeds"], panel["probes"]
    ):
        route = probe.get("route", panel["profiles"][profile])
        rows.append(
            dict(
                profile=profile,
                arm=arm,
                seed=seed,
                probe=probe["id"],
                route=route,
                scope=probe["scope"],
                adjudication="correct",
                displayed_output_id="display",
                message_id="message",
                render_receipt="receipt",
                snapshot_id="snapshot",
                authorized_record_ids=["C1-1"],
                cohort="preview",
                unexplained_gaps=0,
                status="succeeded",
                fallback="none",
                model=model,
                call_id="call" if route == "generated" else None,
                supported_claims=1,
                violations=[],
                rendered_record_ids=["C1-1"],
                provider_dispatched=route == "generated",
            )
        )
    evidence = dict(
        panel_version=panel["version"],
        build=build,
        model=model,
        policy=identity(build, model),
        results=rows,
        regressions=dict.fromkeys(panel["required_regressions"], "pass"),
    )
    assert not validate_evidence(panel, evidence)
    for field, value in (
        ("supported_claims", 0),
        ("displayed_output_id", None),
        ("violations", ["unsupported_claim"]),
        ("model", "wrong-model"),
    ):
        broken = deepcopy(evidence)
        broken["results"][0][field] = value
        assert validate_evidence(panel, broken)
    evidence["results"].pop()
    assert validate_evidence(panel, evidence)


def test_sealed_v1_evidence_validates_only_against_its_own_panel() -> None:
    """BA-108: the archived 2026-10-10 bundle is pinned to the v6/v8 profile
    set it was collected under. It must validate untouched against the panel
    sealed next to it, and fail against any other panel (the v2 successor or
    a tampered build)."""
    from copy import deepcopy
    from pathlib import Path

    from app.services.assistant_release import validate_evidence

    release = Path(__file__).resolve().parents[2] / "release"
    evidence = json.loads((release / "evidence/assistant-panel-v1-2026-10-10.json").read_text())
    sealed_panel = json.loads(
        (release / "evidence/assistant-panel-v1-2026-10-10.panel.json").read_text()
    )
    assert sealed_panel["version"] == evidence["panel_version"]
    assert not validate_evidence(sealed_panel, evidence)

    # The AI-only successor panel must not validate the historical bundle.
    v2_panel = json.loads((release / "assistant-panel-v2.json").read_text())
    assert v2_panel["version"] != evidence["panel_version"]
    assert validate_evidence(v2_panel, evidence)

    # A wrong build/model identity fails even against the right panel.
    malformed = deepcopy(evidence)
    malformed["build"] = "not-a-sha256"
    assert "release_identity" in validate_evidence(sealed_panel, malformed)
    wrong_policy = deepcopy(evidence)
    wrong_policy["policy"] = dict(evidence["policy"], build="f" * 64)
    assert "policy_identity" in validate_evidence(sealed_panel, wrong_policy)


def test_sensitive_driver_detail_never_enters_failure_logs() -> None:
    import logging

    from sqlalchemy.exc import IntegrityError

    from app.logging_config import RedactingJsonFormatter

    marker = "SYNTHETIC_PARTICIPANT_FREE_TEXT"
    try:
        raise IntegrityError("insert raw_response", {"raw_response": marker}, RuntimeError(marker))
    except IntegrityError:
        import sys

        record = logging.LogRecord(
            "test", logging.ERROR, __file__, 1, "job failed", (), sys.exc_info()
        )
        formatted = RedactingJsonFormatter("test").format(record)
    assert marker not in formatted
    assert "IntegrityError" in formatted


def test_specific_arrival_requires_arrival_evidence() -> None:
    records = [_rec("C3-1", 3, "The Marina", "still_at")]
    checked = output_claims.check_assistant_output(
        "Crate 3 arrived at The Marina.", crate=3, records=records
    )
    assert "unsupported_claim" in checked.violations


def test_scoped_absence_is_not_a_global_denial() -> None:
    records = [_rec("C3-1", 3, "The Marina")]
    assert not output_claims.check_assistant_output(
        "No entries report crate 3 at Old Cannery.", crate=3, records=records
    ).violations
    assert output_claims.check_assistant_output(
        "No entries report crate 3 at The Marina.", crate=3, records=records
    ).violations


def test_pinned_generation_policy_refuses_drift() -> None:
    from copy import deepcopy

    from app.services.generation_policy import identity, matches

    # Behavior-era pin: expected.build is the session's behavior digest.
    expected = identity("beh-a", "model-a@sha256:abc")
    assert matches(
        expected,
        behavior="beh-a",
        model="model-a@sha256:abc",
        session_behavior="beh-a",
        session_build="rel-a",
    )
    for behavior, model, session_behavior, session_build in (
        # different running behavior digest — a real participant-facing change
        ("beh-b", "model-a@sha256:abc", "beh-a", "rel-a"),
        # session pinned under a different behavior digest
        ("beh-a", "model-a@sha256:abc", "beh-b", "rel-a"),
        # model drift
        ("beh-a", "model-a@sha256:def", "beh-a", "rel-a"),
        ("beh-a", "other@sha256:abc", "beh-a", "rel-a"),
        # pre-digest sessions (NULL column) never match — fail closed
        ("beh-a", "model-a@sha256:abc", None, "rel-a"),
    ):
        assert not matches(
            expected,
            behavior=behavior,
            model=model,
            session_behavior=session_behavior,
            session_build=session_build,
        )
    altered = deepcopy(expected)
    altered["inference"]["temperature"] = 0.7
    assert not matches(
        altered,
        behavior="beh-a",
        model="model-a@sha256:abc",
        session_behavior="beh-a",
        session_build="rel-a",
    )


def test_pinned_generation_policy_legacy_build_pin() -> None:
    """Rows pinned before the scoped digest carry the legacy release checksum in the
    policy payload; after the 0016 backfill their ``behavior_source_sha256`` equals the
    current digest, so an ops-only redeploy no longer refuses their jobs."""
    from app.services.generation_policy import identity, matches

    legacy = identity("rel-a", "model-a@sha256:abc")
    assert matches(
        legacy,
        behavior="beh-a",
        model="model-a@sha256:abc",
        session_behavior="beh-a",  # backfilled behavior digest
        session_build="rel-a",  # stored legacy release checksum — the pin token
    )
