"""Simulated-participant personas.

Each persona sees the same projection the UI serves (``harness.view.View``) and emits
actions — durable command kinds dispatched through the real handlers, plus two
pseudo-actions the runner interprets locally:

* ``_nop`` — take no action this step (idle behavior);
* ``_force_wall`` — let a wall-clock job fire (payload ``{"job": kind}``), used only
  when a persona wants to *trigger* a specific timeout rather than wait for the
  runner's virtual wall clock.

Scenario expectations (what a session of this persona *should* produce) live with the
persona as ``expectations`` — the oracle layer evaluates them against the durable
record after the run.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from app.domain.manifests.binding import BoundManifest
from app.services import lifecycle

from . import utterances
from .view import FileEntry, View

INSTRUMENT_STAGES = {"confidence", "peer_ratings", "post_questionnaire", "open_ended"}
CHECKIN_STAGES = {"checkin_collect", "checkin_wait", "checkin_reveal"}
POST_TASK = INSTRUMENT_STAGES | {"answers_locked", "debrief", "data_use_choice"}


@dataclass(frozen=True)
class Action:
    kind: str  # durable command kind, or "_nop" / "_force_wall"
    payload: dict[str, Any] = field(default_factory=dict)
    revision_offset: int = 0  # stale-revision probing: send a wrong expected_revision
    expect_reject: bool = False
    note: str = ""


NOP = Action("_nop")


class Persona:
    """Base persona: seeded RNG + utterance engine + per-session memory."""

    name = "base"
    step_ms = 15_000  # active-time advance per loop iteration while in task
    answer_at_frac = 0.85  # fraction of budget when assigned answers are submitted

    def __init__(self, seed: int, *, paraphrase: bool = False) -> None:
        self.rng = random.Random(seed)
        self.utt = utterances.UtteranceEngine(seed, use_paraphrase=paraphrase)
        self.mem: dict[str, Any] = {}

    # -- main entry ------------------------------------------------------

    def decide(self, view: View, bound: BoundManifest) -> list[Action]:
        if view.stage in ("connecting", "ready"):
            return []
        if view.stage in CHECKIN_STAGES:
            return self.on_checkin(view, bound)
        if view.stage in POST_TASK or view.stage in ("completion",):
            return self.on_posttask(view, bound)
        if view.stage in ("task", "solo_review"):
            return self.on_task(view, bound)
        return [NOP]

    # -- hooks to override ------------------------------------------------

    def on_ai_qa(self, view: View, bound: BoundManifest) -> list[Action]:  # noqa: ARG002 - persona hook override
        """AI_QA_ENABLED: actions for the optional rules-Q&A page, dispatched
        before the runner's always-available ``proceed``. Default asks nothing —
        the page is optional by contract."""
        return []

    def on_task(self, view: View, bound: BoundManifest) -> list[Action]:
        return self._normal_task(view, bound)

    def on_checkin(self, view: View, bound: BoundManifest) -> list[Action]:  # noqa: ARG002 - persona hook override
        """Default: a real participant responds to the rating and proceeds."""
        if view.stage == "checkin_collect":
            if self.mem.get("checkin_answered"):
                return [NOP]
            self.mem["checkin_answered"] = True
            return [
                Action("pressure_response", {"value": self.rng.randint(4, 8)}),
                NOP,
            ]
        if view.stage == "checkin_reveal":
            if self.mem.get("checkin_proceeded"):
                return [NOP]
            self.mem["checkin_proceeded"] = True
            return [Action("proceed")]
        return [NOP]  # checkin_wait: reveal arrives on its own wall job

    def on_posttask(self, view: View, bound: BoundManifest) -> list[Action]:
        """Shared finisher: instruments, debrief, data-use, completion."""
        stage = view.stage
        if stage == "answers_locked":
            return [Action("proceed")]
        if stage in INSTRUMENT_STAGES:
            return self._instrument_actions(view, bound, stage)
        if stage == "debrief":
            return [Action("debrief_ack")]
        if stage == "data_use_choice":
            if not self.mem.get("data_use_sent"):
                self.mem["data_use_sent"] = True
                return [
                    Action(
                        "data_use_choice",
                        {"choice": self.rng.choice(["use_all", "use_no_free_text"])},
                    )
                ]
            return [Action("complete")]  # data_use_choice -> completion
        return [NOP]

    # -- shared behavior ---------------------------------------------------

    def _normal_task(self, view: View, bound: BoundManifest) -> list[Action]:
        """Baseline participant: greet, interleave asks/chat/shares, submit answers."""
        actions: list[Action] = []
        if view.greeting_allowed and not self.mem.get("greeted"):
            self.mem["greeted"] = True
            seat = self.rng.choice(list(view.greeting_options))
            actions.append(Action("greeting", {"text": view.greeting_options[seat]}, note="greet"))
        actions.extend(self._maybe_team_activity(view, bound))
        actions.extend(self._answers_if_due(view, bound))
        return actions or [NOP]

    def _known_records(self, view: View, crate: int) -> list[FileEntry]:
        """File records this persona may act on (share, answer from) — the whole
        visible file for most personas; channel personas restrict by provenance."""
        return view.file_for(crate)

    def _maybe_team_activity(self, view: View, bound: BoundManifest) -> list[Action]:  # noqa: ARG002 - persona hook override
        if "chat" not in view.channels and "structured_seeking" not in view.channels:
            return []
        roll = self.rng.random()
        # Answer a scripted ask by sharing a matching file record (~55%).
        if view.open_asks and roll < 0.55:
            ask = self.rng.choice(view.open_asks)
            if ask["crate"] is not None:
                matches = self._known_records(view, ask["crate"])
                if matches and "sharing" in view.channels:
                    return [
                        Action(
                            "record_share",
                            {"record_id": str(self.rng.choice(matches).record_id)},
                            note=f"share-for-ask c{ask['crate']}",
                        )
                    ]
            return [
                Action(
                    "social_reply",
                    {"body": self.utt.render(utterances.TEAM_DECLINE_ASK, crate=ask["crate"])},
                )
            ]
        if roll < 0.70 and view.structured_requests:
            # W06: the session world's own crate range — 4/8/12 under the
            # crate-condition experiment; never the legacy constant.
            crate = self.rng.randint(1, view.n_crates)
            return [Action("teammate_request", {"crate": crate}, note=f"ask c{crate}")]
        if roll < 0.85:
            crate = self.rng.randint(1, view.n_crates)
            pool = utterances.TEAM_ASK_FOLLOWUPS if self.mem.get("asked") else utterances.TEAM_ASKS
            self.mem["asked"] = True
            return [
                Action(
                    "social_reply",
                    {"body": self.utt.render(pool, crate=crate)},
                    note=f"typed-ask c{crate}",
                )
            ]
        if roll < 0.95:
            return [Action("social_reply", {"body": self.utt.render(utterances.TEAM_SOCIAL)})]
        return []

    def _answers_if_due(self, view: View, bound: BoundManifest) -> list[Action]:
        """Submit assigned-crate answers once past the persona's answer fraction."""
        from app.timing.policy import resolve_policy

        budget = resolve_policy(bound, view.session_arm or bound.manifest.arms[0]).budget_ms
        threshold = int(budget * self.answer_at_frac)
        if view.active_ms < threshold or not view.assigned_crates:
            return []
        done: set[int] = self.mem.setdefault("answered_crates", set())
        pending = [c for c in view.assigned_crates if c not in done]
        if not pending:
            return []
        crate = pending[0]
        done.add(crate)
        # BA-80: answer from participant-visible evidence only — the hideout(s)
        # named in this crate's record bodies (newest first), else a random pick
        # from the public six-hideout list the answer bar offers.
        from app.domain.world import HIDEOUTS
        from app.services import engines

        named: list[str] = []
        for entry in reversed(self._known_records(view, crate)):
            named = engines.hideouts_in(entry.body)
            if named:
                break
        location = self.rng.choice(named) if named else self.rng.choice(list(HIDEOUTS))
        return [
            Action(
                "answer_update",
                {"crate": crate, "location_id": location},
                note=f"answer c{crate}",
            )
        ]

    def _instrument_actions(self, view: View, bound: BoundManifest, stage: str) -> list[Action]:
        """Answer every declared item once (skip only when skippable and rolled)."""
        items, applies_to = lifecycle.instrument_items(bound, stage)
        if not items:
            return [Action("proceed")]
        done: set[str] = self.mem.setdefault(f"items_{stage}", set())
        actions: list[Action] = []
        for item in items:
            iid = item["item_id"]
            if applies_to == "per_teammate_seat":
                for seat in ("JT", "PM"):
                    key = f"{iid}.{seat}"
                    if key in done:
                        continue
                    done.add(key)
                    actions.append(self._item_action(iid, item, seat=seat))
            elif stage == "confidence":
                for crate in self._answered_crates(view):
                    key = f"{iid}.{crate}"
                    if key in done:
                        continue
                    done.add(key)
                    actions.append(self._item_action(iid, item, crate=crate))
            else:
                if iid in done:
                    continue
                done.add(iid)
                actions.append(self._item_action(iid, item))
        # All items committed this pass or earlier → explicit proceed.
        return actions or [Action("proceed")]

    def _item_action(
        self,
        item_id: str,
        item: dict[str, Any],
        *,
        seat: str | None = None,
        crate: int | None = None,
    ) -> Action:
        payload: dict[str, Any] = {"item_id": item_id}
        if seat is not None:
            payload["seat"] = seat
        if crate is not None:
            payload["crate"] = crate
        if item.get("skippable") and self.rng.random() < 0.1:
            return Action("item_skip", payload, note=f"skip {item_id}")
        payload["value"] = (
            self.rng.choice(item["options"])
            if item.get("options")
            else (
                "Used the file and chat where each seemed more reliable."
                if item.get("free_text")
                else (self.rng.randint(1, 7) if crate is None else self.rng.randint(20, 95))
            )
        )
        return Action("item_response", payload, note=f"rate {item_id}")

    def _answered_crates(self, view: View) -> list[int]:
        return sorted(self.mem.get("answered_crates") or view.assigned_crates)


class NormalPersona(Persona):
    name = "normal"
    expectations = ("terminal:completion", "episodes:no_orphaned")


class AskerPersona(Persona):
    """Every crate, mixed phrasing — structured, broadcast, addressed, follow-ups."""

    name = "asker"
    step_ms = 8_000
    expectations = ("terminal:completion", "structured_response:each_ask")

    def on_task(self, view: View, bound: BoundManifest) -> list[Action]:
        actions = []
        if view.greeting_allowed and not self.mem.get("greeted"):
            self.mem["greeted"] = True
            seat = self.rng.choice(list(view.greeting_options))
            actions.append(Action("greeting", {"text": view.greeting_options[seat]}))
        # W06: exhaustive coverage means the session's full crate range —
        # 1-12 under `none`, 1-4 under `full`, legacy 1-8 otherwise.
        unasked = self.mem.setdefault("unasked", list(range(1, view.n_crates + 1)))
        if unasked and self.rng.random() < 0.8:
            crate = unasked.pop(0)
            style = self.rng.random()
            if style < 0.4 and view.structured_requests:
                actions.append(Action("teammate_request", {"crate": crate}))
            elif style < 0.7:
                addressee: str | None = self.rng.choice([None, "JT", "PM"])
                payload: dict[str, Any] = {
                    "body": self.utt.render(utterances.TEAM_ASKS, crate=crate)
                }
                if addressee:
                    payload["to"] = addressee
                actions.append(Action("social_reply", payload))
            else:
                actions.append(
                    Action(
                        "social_reply",
                        {"body": self.utt.render(utterances.TEAM_ASK_FOLLOWUPS, crate=crate)},
                    )
                )
        else:
            actions.extend(self._maybe_team_activity(view, bound))
        actions.extend(self._answers_if_due(view, bound))
        return actions or [NOP]


class ProberPersona(Persona):
    """Normal participant who also fires automation probes early/mid/late."""

    name = "prober"
    expectations = ("terminal:completion", "probes:all_disclosed")

    PROBE_AT = (0.10, 0.45, 0.80)  # fractions of task budget

    def on_task(self, view: View, bound: BoundManifest) -> list[Action]:
        from app.timing.policy import resolve_policy

        budget = resolve_policy(bound, view.session_arm or bound.manifest.arms[0]).budget_ms
        fired = self.mem.setdefault("probes_fired", 0)
        if fired < len(self.PROBE_AT) and view.active_ms >= budget * self.PROBE_AT[fired]:
            self.mem["probes_fired"] = fired + 1
            # Probes fire unmutated so they hit the full-probe classifier — the
            # near-miss surface is exercised by ordinary mutated traffic instead.
            body = utterances.draw(utterances.PROBES, self.utt.rng)
            return [
                Action(
                    "social_reply",
                    {"body": body},
                    note=f"probe-{fired + 1}",
                )
            ]
        return self._normal_task(view, bound)


class AssistantPokerPersona(Persona):
    """Hammers the assistant: single-crate, contents/cross-crate (AB-15), edge cases."""

    name = "assistant_poker"
    # The answer-key bound itself is the always-on INV-ASSISTANT-BOUNDED invariant
    # (runs for every session) — no scenario token is needed to claim it.
    expectations = ("assistant:no_crash",)

    def on_task(self, view: View, bound: BoundManifest) -> list[Action]:
        if "assistant_prompting" not in view.channels:
            return self._normal_task(view, bound)
        pools = [
            utterances.ASSISTANT_CRATE,
            utterances.ASSISTANT_CONTENTS,
            utterances.ASSISTANT_EDGE,
        ]
        weights = (0.5, 0.35, 0.15)
        pool = self.rng.choices(pools, weights=weights)[0]
        crate = self.rng.choice(view.assigned_crates or [1])
        action = Action(
            "assistant_request",
            {"body": self.utt.render(pool, crate=crate)},
            note="assistant-"
            + (
                "crate"
                if pool is utterances.ASSISTANT_CRATE
                else "contents"
                if pool is utterances.ASSISTANT_CONTENTS
                else "edge"
            ),
        )
        rest = self._answers_if_due(view, bound)
        return [action, *rest]


class IdlerPersona(Persona):
    """Greets, then never acts again — the virtual wall clock must kill the check-in."""

    name = "idler"
    step_ms = 20_000
    expectations = ("terminal:technical_stop", "terminal_reason:checkin_incomplete")

    def on_task(self, view: View, bound: BoundManifest) -> list[Action]:  # noqa: ARG002 - persona hook override
        if view.greeting_allowed and not self.mem.get("greeted"):
            self.mem["greeted"] = True
            seat = self.rng.choice(list(view.greeting_options))
            return [Action("greeting", {"text": view.greeting_options[seat]}), NOP]
        return [NOP]

    def on_checkin(self, view: View, bound: BoundManifest) -> list[Action]:  # noqa: ARG002 - persona hook override
        return [NOP]  # never respond — the inactivity cap is the scenario

    def on_posttask(self, view: View, bound: BoundManifest) -> list[Action]:  # noqa: ARG002 - persona hook override
        return [NOP]


class SharerPersona(Persona):
    """Resolves every scripted ask by sharing a matching file record."""

    name = "sharer"
    expectations = ("terminal:completion", "episodes:participant_share")

    def _maybe_team_activity(self, view: View, bound: BoundManifest) -> list[Action]:
        if view.open_asks and "sharing" in view.channels:
            ask = view.open_asks[0]
            if ask["crate"] is not None:
                matches = self._known_records(view, ask["crate"])
                if matches:
                    return [
                        Action(
                            "record_share",
                            {"record_id": str(self.rng.choice(matches).record_id)},
                            note=f"share-for-ask c{ask['crate']}",
                        )
                    ]
            return [
                Action(
                    "social_reply",
                    {"body": self.utt.render(utterances.TEAM_DECLINE_ASK, crate=ask["crate"])},
                )
            ]
        return super()._maybe_team_activity(view, bound)


class ChaoticPersona(Persona):
    """Normal behavior interleaved with hostile plumbing: stale revisions, double
    submits, malformed payloads, out-of-stage commands."""

    name = "chaotic"
    expectations = ("no_crash", "integrity:only_expected_rejections")

    def _maybe_team_activity(self, view: View, bound: BoundManifest) -> list[Action]:
        chaos = self.rng.random()
        if chaos < 0.06:
            return [
                Action(
                    "social_reply",
                    {"body": self.utt.render(utterances.TEAM_SOCIAL)},
                    revision_offset=-self.rng.randint(1, 3),
                    expect_reject=True,
                    note="stale-revision",
                )
            ]
        if chaos < 0.12:
            body = self.utt.render(utterances.TEAM_SOCIAL)
            dup = Action("social_reply", {"body": body}, note="double-submit")
            return [dup, dup]
        if chaos < 0.18:
            return [
                Action(
                    "teammate_request",
                    {"crate": self.rng.choice([0, 9, -1, 99])},
                    expect_reject=True,
                    note="malformed-crate",
                )
            ]
        if chaos < 0.24:
            kind = self.rng.choice(["pressure_response", "debrief_ack", "data_use_choice"])
            payload = (
                {"value": 5}
                if kind == "pressure_response"
                else ({"choice": "use_all"} if kind == "data_use_choice" else {})
            )
            return [Action(kind, payload, expect_reject=True, note=f"out-of-stage:{kind}")]
        return super()._maybe_team_activity(view, bound)


class DisconnecterPersona(Persona):
    """Drops mid-task (no commands for a window), then resumes and finishes."""

    name = "disconnecter"
    expectations = ("resume:accepted",)

    DISCONNECT_STEPS = 6  # loop iterations of silence (~virtual wall keeps crediting)

    def decide(self, view: View, bound: BoundManifest) -> list[Action]:
        away = self.mem.get("away_steps", 0)
        if self.mem.get("disconnect_started") and away < self.DISCONNECT_STEPS:
            self.mem["away_steps"] = away + 1
            return [NOP]
        if self.mem.get("disconnect_started") and not self.mem.get("resumed"):
            self.mem["resumed"] = True
            return [Action("resume", {}, note="reconnect")]
        return super().decide(view, bound)

    def on_task(self, view: View, bound: BoundManifest) -> list[Action]:
        if not self.mem.get("disconnect_started") and view.active_ms > 100_000:
            self.mem["disconnect_started"] = True
            return [NOP]
        return self._normal_task(view, bound)


class ChannelPersona(Persona):
    """A participant restricted to a declared subset of the three information
    channels — case file, team chat, AI assistant — as a gameplay strategy.

    Channel semantics:

    * **case file** — consults ``source="initial"`` records: the evidence the
      participant starts the task with;
    * **team chat** — sends and reads team-channel traffic (greeting, social
      asks/replies, structured ``teammate_request``, ask declines, ``record_share``)
      and treats team-delivered cards (``added_from_team``) as known evidence —
      they arrive through the channel as a share, so a chat-watching participant
      sees them while a chat-ignoring one does not;
    * **AI assistant** — sends ``assistant_request`` asks (crate-scoped and
      contents questions). The assistant never names hideouts, so an
      assistant-only player guesses locations blind — which is itself the
      interesting measurement.

    Scripted asks from teammates (``open_asks``) go unanswered whenever
    ``use_team_chat`` is off — a real participant ignoring the pane does the same.
    Check-in, instruments, and the post-task spine are unaffected: channel choice
    is about *information seeking*, not study completion.
    """

    use_case_file = False
    use_team_chat = False
    use_assistant = False

    def _normal_task(self, view: View, bound: BoundManifest) -> list[Action]:
        actions: list[Action] = []
        if self.use_team_chat and view.greeting_allowed and not self.mem.get("greeted"):
            self.mem["greeted"] = True
            seat = self.rng.choice(list(view.greeting_options))
            actions.append(Action("greeting", {"text": view.greeting_options[seat]}, note="greet"))
        actions.extend(self._team_activity(view, bound))
        actions.extend(self._assistant_activity(view))
        actions.extend(self._answers_if_due(view, bound))
        return actions or [NOP]

    def _team_activity(self, view: View, bound: BoundManifest) -> list[Action]:
        return self._maybe_team_activity(view, bound) if self.use_team_chat else []

    def _assistant_activity(self, view: View) -> list[Action]:
        if not self.use_assistant or "assistant_prompting" not in view.channels:
            return []
        if self.rng.random() > 0.55:
            return []
        crate = self.rng.choice(view.assigned_crates or [self.rng.randint(1, view.n_crates)])
        pool = (
            utterances.ASSISTANT_CRATE if self.rng.random() < 0.7 else utterances.ASSISTANT_CONTENTS
        )
        return [
            Action(
                "assistant_request",
                {"body": self.utt.render(pool, crate=crate)},
                note="assistant-ask",
            )
        ]

    def _known_records(self, view: View, crate: int) -> list[FileEntry]:
        return [
            e
            for e in view.file_for(crate)
            if (e.source == "initial" and self.use_case_file)
            or (e.source == "added_from_team" and self.use_team_chat)
        ]


class CasefileOnlyPersona(ChannelPersona):
    """Only the starting case file — never speaks, never asks."""

    name = "casefile_only"
    use_case_file = True
    expectations = ("terminal:completion",)


class ChatOnlyPersona(ChannelPersona):
    """Only team chat — answers from teammate-delivered cards or guesses."""

    name = "chat_only"
    use_team_chat = True
    expectations = ("terminal:completion", "structured_response:each_ask")


class AssistantOnlyPersona(ChannelPersona):
    """Only the AI assistant — gets contents, never locations → guesses."""

    name = "assistant_only"
    use_assistant = True
    expectations = ("terminal:completion",)


class CasefileChatPersona(ChannelPersona):
    """Case file + team chat — the classic teammate strategy."""

    name = "casefile_chat"
    use_case_file = True
    use_team_chat = True
    expectations = ("terminal:completion", "structured_response:each_ask")


class CasefileAssistantPersona(ChannelPersona):
    """Case file + AI assistant — reads what it has, asks the bot, silent in chat."""

    name = "casefile_assistant"
    use_case_file = True
    use_assistant = True
    expectations = ("terminal:completion",)


class AllChannelsPersona(ChannelPersona):
    """Case file + team chat + AI assistant — full information seeking."""

    name = "all_channels"
    use_case_file = True
    use_team_chat = True
    use_assistant = True
    expectations = ("terminal:completion", "structured_response:each_ask")


class QaAskerPersona(NormalPersona):
    """AI_QA_ENABLED coverage: asks a rules question on the optional Q&A page,
    then continues — the rest of the run is the normal persona."""

    name = "qa_asker"

    def on_ai_qa(self, view: View, bound: BoundManifest) -> list[Action]:  # noqa: ARG002 - persona hook override
        if self.mem.get("qa_asked"):
            return []
        self.mem["qa_asked"] = True
        return [
            Action(
                "qa_ask",
                {"question": "How do I ask my teammates for intel about a crate?"},
            )
        ]


REGISTRY: dict[str, type[Persona]] = {
    p.name: p
    for p in (
        NormalPersona,
        QaAskerPersona,
        AskerPersona,
        ProberPersona,
        AssistantPokerPersona,
        IdlerPersona,
        SharerPersona,
        ChaoticPersona,
        DisconnecterPersona,
        CasefileOnlyPersona,
        ChatOnlyPersona,
        AssistantOnlyPersona,
        CasefileChatPersona,
        CasefileAssistantPersona,
        AllChannelsPersona,
    )
}


def make_persona(name: str, seed: int, *, paraphrase: bool = False) -> Persona:
    if name not in REGISTRY:
        raise KeyError(f"unknown persona {name!r}; have {sorted(REGISTRY)}")
    return REGISTRY[name](seed, paraphrase=paraphrase)


def mix(spec: str) -> list[str]:
    """Parse ``name:count,name:count`` or a bare comma list into a persona list."""
    out: list[str] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            name, n = part.rsplit(":", 1)
            out.extend([name.strip()] * int(n))
        else:
            out.append(part)
    for name in out:
        if name not in REGISTRY:
            raise KeyError(f"unknown persona {name!r}; have {sorted(REGISTRY)}")
    return out
