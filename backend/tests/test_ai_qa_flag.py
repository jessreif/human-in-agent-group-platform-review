"""AI_QA_ENABLED — the optional rules-Q&A page between training and connecting.

Addition flag (a new archetype): when 1, training completion routes through the
``ai_qa`` stage, which offers optional free-text questions about the task rules
answered by the local model through the ``qa_answer`` scheduler job — with a
pinned deterministic fallback whenever the provider is unavailable or returns
unusable text. When 0 (the shipped default), the legacy direct
``training -> scripted_connecting`` edge stays and the stage, command, and page
simply never appear. Questions/answers persist in ``qa_exchange`` and surface
in replay/export like other participant data.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from app.db.tx import RejectionError
from app.domain import stage_graph as sg
from app.domain.manifests.binding import bind
from app.domain.manifests.store import ContentStore
from app.services import lifecycle, qa
from app.settings import Settings

MANIFEST = "tp_ai_v11"


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def test_setting_defaults_off_and_summarizes(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings()
    assert settings.AI_QA_ENABLED is False
    assert make_settings(AI_QA_ENABLED="1").AI_QA_ENABLED is True
    assert settings.describe()["ai_qa_enabled"] is False


def test_stage_graph_keeps_both_training_exits() -> None:
    """The addition edge and the legacy direct edge coexist — the flag picks
    which one a session takes; the graph itself is deployment-neutral."""
    for variant in ("tp_round_robin", "tp_review_first", "pe"):
        targets = sg.legal_targets(variant, "training")
        assert "ai_qa" in targets, variant
        assert "scripted_connecting" in targets, variant
        # ai_qa's only non-terminal edge is connecting — ``proceed`` resolves
        # it unambiguously (exceptional exits union in for every stage).
        forward = sg.legal_targets(variant, "ai_qa") - {
            "withdrawn",
            "technical_stop",
            "abandoned",
        }
        assert forward == frozenset({"scripted_connecting"}), variant


def test_qa_ask_rejected_when_flag_off() -> None:
    """Fail closed like the other removal flags — a stray command (queued
    before a flag flip) must not record free text."""
    with pytest.raises(RejectionError, match="qa_disabled"):
        lifecycle._qa_ask(None, None, {"question": "how do crates work?"}, ai_qa_enabled=False)


def test_qa_ask_rejects_malformed_questions() -> None:
    """Validation precedes any storage: empty, whitespace, non-string and
    over-long questions are refused before the exchange row or job exists."""
    for bad in ("", "   ", None, 42, "x" * (qa.QUESTION_MAX_CHARS + 1)):
        with pytest.raises(RejectionError, match="malformed_question"):
            lifecycle._qa_ask(None, None, {"question": bad}, ai_qa_enabled=True)


def test_proceed_is_legal_on_ai_qa() -> None:
    """Continue is always active — ``proceed`` needs no exchange and no
    question; the stage's single non-terminal edge makes it unambiguous."""
    assert "ai_qa" in lifecycle.COMMAND_STAGES["proceed"]
    assert "ai_qa" in lifecycle.COMMAND_STAGES["qa_ask"]
    assert "qa_ask" not in lifecycle.COMMAND_STAGES.get("ready", frozenset())


def test_clean_question_bounds() -> None:
    assert qa.clean_question("  how many crates? ") == "how many crates?"
    assert qa.clean_question("") is None
    assert qa.clean_question("   ") is None
    assert qa.clean_question("x" * qa.QUESTION_MAX_CHARS) is not None
    assert qa.clean_question("x" * (qa.QUESTION_MAX_CHARS + 1)) is None
    assert qa.clean_question(None) is None


def test_clean_answer_bounds() -> None:
    assert qa.clean_answer("  Three crates. ") == "Three crates."
    assert qa.clean_answer("") is None
    assert qa.clean_answer("   ") is None
    assert qa.clean_answer("x" * (qa.ANSWER_MAX_CHARS + 1)) is None
    assert qa.clean_answer(None) is None


def test_rules_prompt_grounded_in_bound_manifest(store: ContentStore) -> None:
    """The system prompt states only rules the bound session runs — crate
    shape, overlap copy, and flag-aware lines — so the model cannot invent
    mechanics the deployment does not field."""
    bound = bind(store, MANIFEST, mode="live")
    prompt = qa.rules_system_prompt(bound, ai_assistant=True, time_pressure=True)
    assert "ONLY questions about how the task works" in prompt
    assert str(bound.manifest.task.n_crates) in prompt
    assert str(bound.manifest.task.n_assigned) in prompt
    assert "check-in" in prompt  # flag-on keeps the check-in rule line

    stripped = qa.rules_system_prompt(bound, ai_assistant=False, time_pressure=False)
    assert "case assistant" not in stripped
    assert "check-in" not in stripped


def test_rules_prompt_refuses_out_of_scope(store: ContentStore) -> None:
    bound = bind(store, MANIFEST, mode="live")
    prompt = qa.rules_system_prompt(bound)
    # The refusal instruction is the contract: no case facts, no study design,
    # no general knowledge — only task rules.
    assert "only answer questions about how the task works" in prompt.lower()
    assert "never invent" in prompt.lower()


def test_fallback_answer_is_scoped_and_stable() -> None:
    """The deterministic fallback declines gracefully and names only real
    mechanics — it must stay stable (pinned copy) and never leak."""
    assert qa.FALLBACK_ANSWER.strip() == qa.FALLBACK_ANSWER
    assert "only answer questions about how" in qa.FALLBACK_ANSWER.lower()
    assert "Continue" in qa.FALLBACK_ANSWER


def test_exchange_seq_from_count_and_cap(store: ContentStore) -> None:
    """``MAX_EXCHANGES`` bounds provider spend per session — the constant is
    small and positive; the cap check lives in _qa_ask before insert."""
    assert 1 <= qa.MAX_EXCHANGES <= 10
