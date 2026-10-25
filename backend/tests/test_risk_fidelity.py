"""Delivered manipulation-fidelity definitions and the owner-selected pause threshold."""

from __future__ import annotations

from typing import Any

import pytest

from app.measurement.fidelity import fidelity_monitor, teammate_fidelity


def _rows(n_generated: int = 4, n_fallback: int = 1) -> list[dict[str, Any]]:
    sessions = [
        {
            "session_id": sid,
            "manifest_id": "ai",
            "compatibility_key": "same",
            "mode": "live",
            "batch_group_id": "group",
        }
        for sid in ("one", "two")
    ]
    calls, messages, outputs = [], [], []
    for session in sessions:
        sid = session["session_id"]
        for i in range(n_generated + n_fallback):
            calls.append({"session_id": sid, "call_id": i, "status": "succeeded"})
            messages.append(
                {
                    "session_id": sid,
                    "message_id": i,
                    "actor": "JT" if i % 2 else "PM",
                    "channel": "team",
                    "status": "delivered",
                }
            )
            outputs.append(
                {
                    "session_id": sid,
                    "message_id": i,
                    "call_id": i,
                    "fallback": "none" if i < n_generated else "deterministic_script",
                }
            )
    return teammate_fidelity(
        sessions,
        [
            {
                "manifest_id": "ai",
                "content": {"teammates": {"social_generation": "model_with_validated_fallback"}},
            }
        ],
        calls,
        messages,
        outputs,
    )


@pytest.mark.parametrize("generated,fallback,status", [(4, 1, "ok"), (3, 1, "pause_recommended")])
def test_threshold_is_strictly_below_eighty_percent(
    generated: int, fallback: int, status: str
) -> None:
    rows = _rows(generated, fallback)
    assert rows[0]["teammate_generated_reply_share"] == generated / (generated + fallback)
    assert fidelity_monitor(rows)[0]["status"] == status


def test_gate_requires_two_sessions_and_never_pools_builds_or_qa() -> None:
    rows = _rows(0, 5)
    assert fidelity_monitor(rows[:1])[0]["status"] == "insufficient_sessions"
    rows[1]["compatibility_key"] = "different-build"
    assert all(r["status"] == "insufficient_sessions" for r in fidelity_monitor(rows))
    rows[1]["mode"] = "qa"
    assert len(fidelity_monitor(rows)) == 1


def test_counts_actual_deliveries_not_attempts_or_assistant_messages() -> None:
    sessions = [{"session_id": "s", "manifest_id": "ai"}]
    manifests = [
        {
            "manifest_id": "ai",
            "content": {"teammates": {"social_generation": "model_with_validated_fallback"}},
        }
    ]
    calls = [
        {"session_id": "s", "call_id": "good", "status": "succeeded"},
        {"session_id": "s", "call_id": "failed-attempt", "status": "failed"},
        {"session_id": "s", "call_id": "late", "status": "quarantined"},
    ]
    messages = [
        {"session_id": "s", "message_id": mid, "actor": actor, "channel": channel, "status": status}
        for mid, actor, channel, status in [
            ("shown", "JT", "team", "delivered"),
            ("substituted", "PM", "team", "delivered"),
            ("assistant", "assistant", "assistant", "delivered"),
            ("suppressed", "JT", "team", "suppressed"),
            ("scripted-request", "JT", "team", "delivered"),
        ]
    ]
    outputs = [
        {"session_id": "s", "message_id": mid, "call_id": "good", "fallback": fallback}
        for mid, fallback in [
            ("shown", "none"),
            ("shown", "none"),
            ("substituted", "deterministic_script"),
            ("assistant", "none"),
            ("suppressed", "none"),
        ]
    ]
    row = teammate_fidelity(sessions, manifests, calls, messages, outputs)[0]
    assert row["teammate_generated_replies"] == 1
    assert row["teammate_fallback_replies"] == 1
    assert row["teammate_reply_denominator"] == 2
    assert row["teammate_generated_reply_share"] == 0.5


def test_missing_linkage_is_null_and_requests_pause_after_two_sessions() -> None:
    rows = _rows()
    rows[0]["teammate_unknown_replies"] = 1
    assert fidelity_monitor(rows)[0]["status"] == "missing_data"
    row = teammate_fidelity(
        [{"session_id": "s", "manifest_id": "ai"}],
        [
            {
                "manifest_id": "ai",
                "content": {"teammates": {"social_generation": "model_with_validated_fallback"}},
            }
        ],
        [],
        [
            {
                "session_id": "s",
                "message_id": "m",
                "actor": "JT",
                "channel": "team",
                "status": "delivered",
            }
        ],
        [{"session_id": "s", "message_id": "m", "call_id": "missing", "fallback": "none"}],
    )[0]
    assert row["teammate_fidelity_state"] == "missing_data"
    assert row["teammate_generated_reply_share"] is None


def test_no_deliveries_and_scripted_condition_remain_null() -> None:
    rows = teammate_fidelity(
        [{"session_id": "s", "manifest_id": mid} for mid in ("ai", "scripted", "unknown")],
        [
            {"manifest_id": mid, "content": {"teammates": {"social_generation": policy}}}
            for mid, policy in [
                ("ai", "model_with_validated_fallback"),
                ("scripted", "deterministic_only"),
            ]
        ],
        [],
        [],
        [],
    )
    assert [r["teammate_fidelity_state"] for r in rows] == [
        "undefined_denominator",
        "not_applicable",
        "missing_data",
    ]
    assert all(r["teammate_generated_reply_share"] is None for r in rows)
