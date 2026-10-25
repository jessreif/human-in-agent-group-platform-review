"""FeatureFlags contract (audit C2/H4): defaults, recorded keys, immutability."""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from app.flags import DEFAULT_FLAGS, FeatureFlags
from app.services import instrument_commands, lifecycle, team_chat, training

# The keys (and order) every session_opened payload and export `flag_*` column has
# recorded since 2026-10-15. Changing them breaks historical-export parity.
RECORDED_KEYS = [
    "scripted_teammates_enabled",
    "ai_assistant_enabled",
    "crate_overlap_enabled",
    "crate_number_enabled",
    "time_pressure_enabled",
    "ai_qa_enabled",
    "bonus_payment_enabled",
]


def test_defaults_match_the_historical_per_function_defaults() -> None:
    assert DEFAULT_FLAGS.as_dict() == {k: k != "ai_qa_enabled" for k in RECORDED_KEYS}


def test_as_dict_keeps_the_recorded_key_order() -> None:
    assert list(FeatureFlags(ai_qa=True).as_dict()) == RECORDED_KEYS


def test_flags_are_immutable_and_keyword_only() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_FLAGS.ai_qa = True  # type: ignore[misc]
    with pytest.raises(TypeError):
        FeatureFlags(True)  # type: ignore[misc]
    assert DEFAULT_FLAGS.with_(time_pressure=False).time_pressure is False
    assert DEFAULT_FLAGS.time_pressure is True


def test_settings_flags_mirror_feature_flags_dict() -> None:
    from app.settings import Settings

    settings = Settings.model_construct(
        SCRIPTED_TEAMMATES_ENABLED=False,
        AI_ASSISTANT_ENABLED=False,
        CRATE_OVERLAP_ENABLED=True,
        CRATE_NUMBER_ENABLED=True,
        TIME_PRESSURE_ENABLED=False,
        AI_QA_ENABLED=True,
        BONUS_PAYMENT_ENABLED=False,
    )
    assert settings.feature_flags() == settings.flags().as_dict()
    assert settings.flags() == FeatureFlags(
        scripted_teammates=False,
        ai_assistant=False,
        time_pressure=False,
        ai_qa=True,
        bonus_payment=False,
    )


@pytest.mark.parametrize(
    "fn",
    [
        instrument_commands.item_response,
        lifecycle._pressure,
        lifecycle._proceed,
        lifecycle._qa_ask,
        training.training_step,
        team_chat.assistant_request,
        lifecycle._consent,
    ],
)
def test_command_handlers_take_booleans_by_keyword_only(fn: object) -> None:
    """C2 regression: after the shared (ctx, bound, p) prefix every parameter is
    keyword-only, so a swapped boolean is a TypeError, not a silent mis-record."""
    params = list(inspect.signature(fn).parameters.values())  # type: ignore[arg-type]
    positional = [p.name for p in params if p.kind is p.POSITIONAL_OR_KEYWORD]
    assert len(positional) == 3 and positional[0] == "ctx"
