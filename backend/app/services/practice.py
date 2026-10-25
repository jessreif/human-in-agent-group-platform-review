"""The fixed practice world for the training stage (INV-0901; spec §9.1).

Training demonstrates the participant-facing surface — sharing a record to the
team, asking the team for intel in chat, plus a preview of the assistant pane —
without ever touching analytic state. UX round 4: no teammate card sits in the
practice case file. 2026-10-10 revision: the participant also practises the
typed ask — the typed ask is the only intel route in nl_only builds and live
logs showed most asks never reaching teammates. The reply is a fixed canned
card (never model text), so the UX-round-4 concern that a practised ask could
tip off the automation does not apply. The boundary is hard:

- the practice world is a *separate, fixed* record set served as read-only
  projection data — it is never inserted into
  ``record``/``file_membership``/``world_snapshot`` and never shares ``record_id`` values
  with any generated task world;
- every practice interaction travels as ``training_step`` command events (the practice
  event namespace) while the session is in ``instructions``/``training`` — stages that
  precede ``ready``, before ``ensure_world`` and any analytic table row can exist.

The copy here is interim reference material pending the D06 freeze, anchored to the
bound ``instruments.training`` material slot (``training.reference``).
"""

from __future__ import annotations

from typing import Any

from app.domain.world import HIDEOUTS

# UX round 4 (item 3): one record to share — a single share is enough practice.
PRACTICE_RECORDS: tuple[dict[str, Any], ...] = (
    {
        "record_id": "PRACTICE-K3-04",
        "crate": 3,
        "stamp": "2 days ago, 2:45 PM",
        "body": "Field team: Crate 3 arriving at Ridgeline Bunker.",
    },
)

# A record already shared into the practice team chat by a teammate — the chat
# preview shows what teammate shares look like during the task (UX round 4:
# the card is *not* planted in the practice case file).
PRACTICE_SHARED: dict[str, Any] = {
    "record_id": "PRACTICE-SHARED-K1",
    "crate": 1,
    "stamp": "Today, 9:10 AM",
    "body": "Teammate shared: Crate 1 sighted near The Marina dock entrance.",
}

# The practice interactions the participant performs (2026-10-10 revision: the
# share plus one typed intel ask — the assistant pane stays a preview).
PRACTICE_ACTIONS: tuple[str, ...] = ("share", "ask")

# Canned practice copy — fixed practice text, never model text.
PRACTICE_REPLIES: dict[str, str] = {
    # UX round 4 (item 7): the assistant pane shows a worked example instead of
    # requiring the participant to type.
    "assistant_question": "What does my file say about Crate 3?",
    "assistant_answer": (
        "In the task, the assistant answers from what is currently in your case file."
    ),
}


def practice_world(*, ask_required: bool = False, ai_assistant: bool = True) -> dict[str, Any]:
    """The participant-facing practice surface payload for the training stage.

    ``ask_required`` (BA-106) is the bound-version gate flag: profiles pinned
    to ``training.reference@v5`` told participants the typed ask was required;
    v6+ gates on the share alone. The client renders required/optional copy
    from this flag — never from the build. ``ai_assistant`` is the deployment
    feature flag: when off, the assistant preview copy is not served at all."""
    return {
        "records": [dict(r) for r in PRACTICE_RECORDS],
        "shared": dict(PRACTICE_SHARED),
        "locations": list(HIDEOUTS),
        "actions": list(PRACTICE_ACTIONS),
        "replies": dict(PRACTICE_REPLIES) if ai_assistant else {},
        "ask_required": ask_required,
        "ai_assistant": ai_assistant,
    }
