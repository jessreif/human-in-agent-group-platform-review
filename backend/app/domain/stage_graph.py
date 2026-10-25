"""Legal stage graph (INV-0405, spec §5.2).

Three variants derive from the bound manifest, not from anything a client can supply:

- ``tp_round_robin``: the TP baseline/demo flow — ``ready -> task``, then the
  ``task -> checkin_collect -> checkin_wait -> checkin_reveal -> task`` loop, ending
  ``task -> answers_locked``.
- ``tp_review_first``: ``ready -> solo_review``, then the same check-in loop and close.
- ``pe``: ``ready -> task -> answers_locked`` (no check-in loop, no solo review) and the
  ``peer_ratings`` stage between ``confidence`` and ``post_questionnaire``.

Only PE runs ``peer_ratings`` (spec §5.2: "[peer_ratings for both PE arms]").

Exceptional exits: ``declined`` is reachable only from ``consent`` (declining consent); after
consent, participant exit is ``withdrawn``. ``technical_stop`` and ``abandoned`` are reachable
from every non-terminal stage. ``completion`` is the successful end and is not in
``TERMINAL_STAGES`` — it carries no terminal reason.

Guards beyond the graph edges (stage prerequisites, spec §5.1/§8.2):

- entering ``instructions`` requires an assigned arm — assignment precedes the first
  condition-specific instruction;
- entering ``instructions`` also requires a ``consented`` consent event;
- entering ``completion`` requires a recorded ``data_use_choice``;
- any terminal stage requires a non-null ``terminal_reason`` (the schema enforces this too,
  so a violation fails loudly rather than silently).
"""

from __future__ import annotations

from typing import Literal

from app.db.schema import STAGES, TERMINAL_STAGES

Stage = Literal[
    "entry",
    "consent",
    "instructions",
    "training",
    "ai_qa",
    "scripted_connecting",
    "ready",
    "solo_review",
    "task",
    "checkin_collect",
    "checkin_wait",
    "checkin_reveal",
    "answers_locked",
    "confidence",
    "peer_ratings",
    "post_questionnaire",
    "open_ended",
    "debrief",
    "data_use_choice",
    "completion",
    "declined",
    "withdrawn",
    "technical_stop",
    "abandoned",
]

StageVariant = Literal["tp_round_robin", "tp_review_first", "pe"]


class IllegalTransitionError(ValueError):
    pass


# Successful-path edges shared by all variants, before variant branches.
_SHARED_EDGES: dict[str, frozenset[str]] = {
    "entry": frozenset({"consent"}),
    "consent": frozenset({"instructions", "declined"}),
    "instructions": frozenset({"training"}),
    # AI_QA_ENABLED=1 routes training completion through the optional rules-Q&A
    # page; the direct edge stays for flag-off sessions. ``ai_qa`` has a single
    # non-terminal target so the always-active Continue resolves ``proceed``.
    "training": frozenset({"scripted_connecting", "ai_qa"}),
    "ai_qa": frozenset({"scripted_connecting"}),
    "scripted_connecting": frozenset({"ready"}),
    # TIME_PRESSURE_ENABLED=0 flag-flip rescue: a session wedged mid-check-in
    # when the flag drops gets carried back to the task by the suppressing
    # dispatch (the checkin_inactivity kill is itself a pressure job).
    "checkin_collect": frozenset({"checkin_wait", "task"}),
    "checkin_wait": frozenset({"checkin_reveal", "task"}),
    "checkin_reveal": frozenset({"task"}),
    "answers_locked": frozenset({"confidence"}),
    "peer_ratings": frozenset({"post_questionnaire"}),
    "post_questionnaire": frozenset({"debrief"}),
    "open_ended": frozenset({"debrief"}),
    "debrief": frozenset({"data_use_choice"}),
    "data_use_choice": frozenset({"completion"}),
}

_VARIANT_EDGES: dict[StageVariant, dict[str, frozenset[str]]] = {
    "tp_round_robin": {
        "ready": frozenset({"task"}),
        "task": frozenset({"checkin_collect", "answers_locked"}),
        "solo_review": frozenset(),
        "confidence": frozenset({"post_questionnaire"}),
        # UX round 8: the two open-ended follow-ups sit between the post-task
        # questionnaire and debrief on TP flows only — PE keeps the direct
        # post_questionnaire -> debrief edge from _SHARED_EDGES.
        "post_questionnaire": frozenset({"open_ended"}),
    },
    "tp_review_first": {
        "ready": frozenset({"solo_review"}),
        # TIME_PRESSURE_ENABLED=0: with the check-in suppressed, the neutral
        # solo_review_end job carries solo_review -> task at the phase boundary
        # (flag-on sessions still exit via the check-in loop).
        "solo_review": frozenset({"checkin_collect", "task"}),
        "task": frozenset({"checkin_collect", "answers_locked"}),
        "confidence": frozenset({"post_questionnaire"}),
        "post_questionnaire": frozenset({"open_ended"}),
    },
    "pe": {
        "ready": frozenset({"task"}),
        "task": frozenset({"answers_locked"}),
        "solo_review": frozenset(),
        "checkin_collect": frozenset(),
        "checkin_wait": frozenset(),
        "checkin_reveal": frozenset(),
        "confidence": frozenset({"peer_ratings"}),
    },
}

# Every non-terminal stage may end in these; the success stages close to completion only.
_EXCEPTIONAL = frozenset({"withdrawn", "technical_stop", "abandoned"})

EDGES: dict[StageVariant, dict[str, frozenset[str]]] = {}
for _variant, _extra in _VARIANT_EDGES.items():
    merged = {stage: set(targets) for stage, targets in _SHARED_EDGES.items()}
    for stage, targets in _extra.items():
        merged.setdefault(stage, set()).update(targets)
    for stage in STAGES:
        if stage not in ("completion", *TERMINAL_STAGES):
            merged.setdefault(stage, set()).update(_EXCEPTIONAL)
    EDGES[_variant] = {s: frozenset(t) for s, t in merged.items()}
# completion and the four exceptional stages have no outgoing edges.
for _variant in EDGES:
    for _terminal in ("completion", *TERMINAL_STAGES):
        EDGES[_variant].setdefault(_terminal, frozenset())

# Reasons a terminal stage may carry. Free text is allowed for operator notes, but these are
# the authored values the UI/scheduler use.
TERMINAL_REASONS: dict[str, frozenset[str]] = {
    "declined": frozenset({"consent_declined"}),
    "withdrawn": frozenset({"participant_withdrawal"}),
    "technical_stop": frozenset(
        {"checkin_incomplete", "provider_outage", "operator_stop", "schedule_integrity_failure"}
    ),
    "abandoned": frozenset({"heartbeat_timeout", "session_expired"}),
}


def variant_for(study_key: str, first_channel_phase: str | None) -> StageVariant:
    """Map a bound manifest to its stage variant (spec §5.2 three flows)."""
    if study_key == "investigators_peer_eval":
        return "pe"
    if study_key == "investigators_time_pressure":
        return "tp_review_first" if first_channel_phase == "solo_review" else "tp_round_robin"
    raise IllegalTransitionError(f"no stage-graph variant for study_key {study_key!r}")


def legal_targets(variant: StageVariant, stage: str) -> frozenset[str]:
    if stage not in STAGES:
        raise IllegalTransitionError(f"unknown stage {stage!r}")
    return EDGES[variant].get(stage, frozenset())


def is_terminal(stage: str) -> bool:
    return stage in TERMINAL_STAGES


def check_transition(
    variant: StageVariant,
    from_stage: str,
    to_stage: str,
    *,
    terminal_reason: str | None = None,
    consent_action: str | None = None,
    arm_assigned: bool = False,
    data_use_choice_recorded: bool = False,
) -> None:
    """Raise :class:`IllegalTransitionError` unless the move is legal for the variant and its
    prerequisites hold. ``terminal_reason`` must be supplied iff ``to_stage`` is terminal."""
    targets = legal_targets(variant, from_stage)
    if to_stage not in targets:
        raise IllegalTransitionError(
            f"{variant}: {from_stage} -> {to_stage} is not a legal transition"
        )
    if to_stage == "instructions":
        if consent_action != "consented":
            raise IllegalTransitionError("instructions requires a consented consent event")
        if not arm_assigned:
            raise IllegalTransitionError("assignment must precede condition-specific instructions")
    if to_stage == "completion" and not data_use_choice_recorded:
        raise IllegalTransitionError("completion requires a recorded data-use choice")
    if is_terminal(to_stage):
        if terminal_reason is None:
            raise IllegalTransitionError(f"terminal stage {to_stage} requires a terminal_reason")
    elif terminal_reason is not None:
        raise IllegalTransitionError(f"non-terminal stage {to_stage} cannot carry terminal_reason")
