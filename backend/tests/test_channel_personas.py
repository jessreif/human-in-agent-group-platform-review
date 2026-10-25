"""Unit tests for the channel-isolation personas (casefile_only, chat_only,
assistant_only, casefile_chat, casefile_assistant, all_channels).

Each persona sees the real ``View`` projection but acts only through its
declared channels — the tests assert *action kinds* stay inside the channel and
*answer evidence* stays inside the records that channel could have delivered:

* ``source="initial"`` counts as case-file information;
* ``source="added_from_team"`` counts as chat information (it arrives as a
  teammate share in the channel);
* the assistant never names hideouts, so assistant-only answers are guesses.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from harness.personas import (
    Action,
    AllChannelsPersona,
    AssistantOnlyPersona,
    CasefileAssistantPersona,
    CasefileChatPersona,
    CasefileOnlyPersona,
    ChatOnlyPersona,
    make_persona,
)
from harness.view import FileEntry, View

TEAM_KINDS = {"greeting", "social_reply", "teammate_request", "record_share"}
ASSISTANT_KINDS = {"assistant_request"}

CHANNELS = frozenset({"chat", "sharing", "structured_seeking", "assistant_prompting"})
BUDGET = SimpleNamespace(budget_ms=360_000)


def _record(rid: str, crate: int, source: str, hideout: str) -> FileEntry:
    return FileEntry(
        record_id=rid,
        crate=crate,
        stamp="10:00",
        body=f"witness saw the goods moved toward {hideout}",
        event_type="record",
        source=source,
        shared_by="JT" if source == "added_from_team" else None,
    )


def _view(**over) -> View:
    base = dict(
        stage="task",
        revision=5,
        active_ms=120_000,
        session_arm="high",
        channels=CHANNELS,
        structured_requests=True,
        file=[
            _record("init1", 1, "initial", "The Sawmill"),
            _record("init4", 4, "initial", "Old Cannery"),
            _record("team4", 4, "added_from_team", "Ridgeline Bunker"),
        ],
        assigned_crates=[1, 4],
        open_asks=[{"episode_id": "e1", "crate": 4, "seat": "JT"}],
        greeting_options={"JT": "hey", "PM": "hello"},
        greeting_allowed=True,
    )
    base.update(over)
    return View(**base)


@pytest.fixture(autouse=True)
def _fixed_budget():
    with patch("app.timing.policy.resolve_policy", return_value=BUDGET):
        yield


def _kinds(actions: list[Action]) -> set[str]:
    return {a.kind for a in actions if not a.kind.startswith("_")}


def _act(persona, view: View, steps: int = 60) -> list[Action]:
    out: list[Action] = []
    for _ in range(steps):
        out.extend(persona.on_task(view, None))
    return out


class TestChannelIsolation:
    """Each persona's emitted command kinds stay inside its channels."""

    @pytest.mark.parametrize(
        "cls,allowed",
        [
            (CasefileOnlyPersona, {"answer_update"}),
            (ChatOnlyPersona, TEAM_KINDS | {"answer_update"}),
            (AssistantOnlyPersona, ASSISTANT_KINDS | {"answer_update"}),
            (CasefileChatPersona, TEAM_KINDS | {"answer_update"}),
            (CasefileAssistantPersona, ASSISTANT_KINDS | {"answer_update"}),
            (
                AllChannelsPersona,
                TEAM_KINDS | ASSISTANT_KINDS | {"answer_update"},
            ),
        ],
    )
    def test_emitted_kinds_stay_in_channel(self, cls, allowed):
        persona = cls(seed=7)
        kinds = _kinds(_act(persona, _view(active_ms=320_000)))
        assert kinds <= allowed, f"{cls.name} escaped its channels: {kinds - allowed}"

    def test_casefile_only_never_greets(self):
        persona = CasefileOnlyPersona(seed=3)
        kinds = _kinds(_act(persona, _view()))
        assert "greeting" not in kinds

    def test_chat_only_uses_team_and_never_assistant(self):
        persona = ChatOnlyPersona(seed=11)
        kinds = _kinds(_act(persona, _view()))
        assert kinds & TEAM_KINDS  # it does use chat
        assert not kinds & ASSISTANT_KINDS

    def test_assistant_only_uses_assistant_and_never_team(self):
        persona = AssistantOnlyPersona(seed=13)
        kinds = _kinds(_act(persona, _view()))
        assert "assistant_request" in kinds
        assert not kinds & TEAM_KINDS

    def test_all_channels_uses_all_three(self):
        persona = AllChannelsPersona(seed=17)
        kinds = _kinds(_act(persona, _view(), steps=80))
        assert kinds & TEAM_KINDS
        assert kinds & ASSISTANT_KINDS


class TestEvidenceFiltering:
    """_known_records attributes team-delivered cards to the chat channel."""

    def test_known_records_by_channel(self):
        view = _view()
        assert {r.record_id for r in CasefileOnlyPersona(seed=1)._known_records(view, 4)} == {
            "init4"
        }
        assert {r.record_id for r in ChatOnlyPersona(seed=1)._known_records(view, 4)} == {"team4"}
        assert AssistantOnlyPersona(seed=1)._known_records(view, 4) == []
        assert {r.record_id for r in AllChannelsPersona(seed=1)._known_records(view, 4)} == {
            "init4",
            "team4",
        }

    def test_chat_only_answers_from_delivered_evidence(self):
        """Crate 4's delivered record names Ridgeline Bunker; the initial one
        names Old Cannery — a chat-only player can only pick the delivered one."""
        persona = ChatOnlyPersona(seed=5)
        view = _view(active_ms=320_000)  # past the 0.85 answer fraction
        seen = {
            (a.payload["crate"], a.payload["location_id"])
            for a in _act(persona, view)
            if a.kind == "answer_update"
        }
        assert (4, "Ridgeline Bunker") in seen
        assert (4, "Old Cannery") not in seen

    def test_casefile_only_answers_from_initial_evidence(self):
        """The same crate's initial record names Old Cannery — the delivered
        card's Ridgeline Bunker is chat information this persona never saw."""
        persona = CasefileOnlyPersona(seed=5)
        view = _view(active_ms=320_000)
        seen = {
            (a.payload["crate"], a.payload["location_id"])
            for a in _act(persona, view)
            if a.kind == "answer_update"
        }
        assert (4, "Old Cannery") in seen
        assert (4, "Ridgeline Bunker") not in seen

    def test_share_for_ask_only_uses_known_records(self):
        """Answering a scripted ask: chat-only may share the delivered card;
        casefile-only cannot share at all (sharing is a chat action)."""
        view = _view()
        chat = ChatOnlyPersona(seed=2)
        shares = [
            a
            for a in _act(chat, view, steps=80)
            if a.kind == "record_share" and a.payload["record_id"] == "team4"
        ]
        assert shares, "chat-only never shared the delivered card for the open ask"
        case = CasefileOnlyPersona(seed=2)
        assert not [a for a in _act(case, view, steps=80) if a.kind == "record_share"]


class TestRegistry:
    def test_all_six_channel_personas_registered(self):
        for name in (
            "casefile_only",
            "chat_only",
            "assistant_only",
            "casefile_chat",
            "casefile_assistant",
            "all_channels",
        ):
            assert type(make_persona(name, 1)).__name__ != "Persona"

    def test_mix_parses_channel_names(self):
        from harness.personas import mix

        assert mix("casefile_only:2,chat_only") == [
            "casefile_only",
            "casefile_only",
            "chat_only",
        ]

    def test_casefile_assistant_is_silent_in_chat_but_asks(self):
        persona = CasefileAssistantPersona(seed=23)
        kinds = _kinds(_act(persona, _view()))
        assert "assistant_request" in kinds
        assert not kinds & TEAM_KINDS
