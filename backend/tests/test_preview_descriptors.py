"""INV-0209: signed preview descriptors, faithful vs accelerated QA, no URL overrides."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.services.preview import (
    PreviewError,
    guard_entry_overrides,
    issue_descriptor,
    verify_descriptor,
)
from tests.wp02 import SEED, store

KEY = b"k" * 32
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _pins() -> dict[str, str]:
    return {mid: s.sha256 for mid, s in store().manifests.items()}


def _issue(**overrides: Any) -> str:
    args: dict[str, Any] = {
        "manifest_id": "tp_review_first_v2",
        "manifest_sha256": _pins()["tp_review_first_v2"],
        "arm": "low",
        "seed": SEED,
        "mode": "preview",
        "timing_mode": "faithful",
        "now": NOW,
        "lifetime": timedelta(hours=2),
        "issuer": "admin1@university.edu",
    }
    args.update(overrides)
    return issue_descriptor(KEY, **args)


def test_round_trip_grants_non_analytic_preview() -> None:
    grant = verify_descriptor(
        KEY, _issue(), app_mode="preview", now=NOW, current_manifest_sha256=_pins()
    )
    assert grant.descriptor.arm == "low"
    assert grant.descriptor.seed == SEED
    assert not grant.analytic_eligible
    assert not grant.consumes_allocation and not grant.creates_payment


def test_tampering_or_wrong_key_is_rejected() -> None:
    token = _issue()
    payload, sig = token.split(".")
    flipped = payload[:-2] + ("A" if payload[-2] != "A" else "B") + payload[-1]
    for bad in (f"{flipped}.{sig}", f"{payload}.{sig[:-1]}x", "garbage", f"{payload}."):
        with pytest.raises(PreviewError):
            verify_descriptor(
                KEY, bad, app_mode="preview", now=NOW, current_manifest_sha256=_pins()
            )
    with pytest.raises(PreviewError, match="signature"):
        verify_descriptor(
            b"z" * 32, token, app_mode="preview", now=NOW, current_manifest_sha256=_pins()
        )


def test_live_deployments_accept_nonlive_descriptors() -> None:
    # 2026-10-07 amendment: descriptors can never carry mode="live", so a live
    # deployment accepting them can only ever produce analytically-excluded test
    # sessions. Mode-matching still applies to non-live deployments.
    grant = verify_descriptor(
        KEY, _issue(), app_mode="live", now=NOW, current_manifest_sha256=_pins()
    )
    assert grant.descriptor.mode == "preview"
    assert grant.consumes_allocation is False and grant.creates_payment is False


def test_expiry_mode_and_manifest_pin() -> None:
    token = _issue()
    with pytest.raises(PreviewError, match="expired"):
        verify_descriptor(
            KEY,
            token,
            app_mode="preview",
            now=NOW + timedelta(hours=3),
            current_manifest_sha256=_pins(),
        )
    with pytest.raises(PreviewError, match="cannot run"):
        verify_descriptor(KEY, token, app_mode="qa", now=NOW, current_manifest_sha256=_pins())
    stale = dict(_pins(), tp_review_first_v2="0" * 64)
    with pytest.raises(PreviewError, match="different manifest version"):
        verify_descriptor(KEY, token, app_mode="preview", now=NOW, current_manifest_sha256=stale)
    with pytest.raises(PreviewError, match="7 days"):
        _issue(lifetime=timedelta(days=8))


def test_accelerated_timing_is_explicit_qa_only() -> None:
    with pytest.raises(PreviewError, match="qa mode"):
        _issue(timing_mode="accelerated_qa", qa_overlay="qa.accelerated_x10@v1")
    with pytest.raises(PreviewError, match="qa overlay"):
        _issue(mode="qa", timing_mode="accelerated_qa")
    with pytest.raises(PreviewError, match="no qa overlay"):
        _issue(qa_overlay="qa.accelerated_x10@v1")
    token = _issue(mode="qa", timing_mode="accelerated_qa", qa_overlay="qa.accelerated_x10@v1")
    grant = verify_descriptor(KEY, token, app_mode="qa", now=NOW, current_manifest_sha256=_pins())
    assert grant.descriptor.timing_mode == "accelerated_qa"
    assert not grant.analytic_eligible


def test_short_keys_rejected() -> None:
    with pytest.raises(PreviewError, match="32 bytes"):
        issue_descriptor(
            b"short",
            manifest_id="tp_demo_v1",
            manifest_sha256="0" * 64,
            arm="high",
            seed=SEED,
            mode="preview",
            timing_mode="faithful",
            now=NOW,
            lifetime=timedelta(hours=1),
            issuer="x",
        )


def test_url_parameters_cannot_override_condition_content_or_speed() -> None:
    for key in (
        "cond",
        "condition",
        "arm",
        "profile",
        "seed",
        "speed",
        "accelerated",
        "QA",
        "Debug",
    ):
        with pytest.raises(PreviewError, match="override"):
            guard_entry_overrides({key: "x", "PROLIFIC_PID": "abc"})
    guard_entry_overrides({"PROLIFIC_PID": "abc", "STUDY_ID": "s1", "SESSION_ID": "t1"})
