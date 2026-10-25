"""Stage-2 history derivation (INV-1202): recipient-specific novelty and repetition.

Runs *after* message-level coding. A lineage is one underlying evidence fact — an
authenticated record, or the claims of one earlier message — no matter how many
channels repeat it. An assertion joins a lineage when it agrees on crate AND (event
compatibility: same event, or arrival<->still_at restatement) AND, when the assertion
names a location, the same location. Departure/transit never merge with arrival
lineages: chronology is real and "departed" is a different fact than "arrived".

Novelty decisions:

- ``first_introduction`` — no matching lineage.
- ``self_repetition`` / ``other_source_repetition`` — matched lineage's earliest actor
  is the subject's actor vs anyone else's.
- ``attachment`` — authenticated attachment (record authority, text bypassed).
- ``unresolved`` — negated, ambiguous, or stale-on-arrival claims: honest "cannot
  establish", never a guess.

``novel_to_recipient`` is recipient-specific: the recipients are the rest of the team,
so a claim resting only on ``participant_file`` history is still novel to them, while
anything with a broadcast member is not.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.coding.text import message_history_assertions
from app.coding.types import CodedAssertion, EventType, HistoryItem, Novelty

_COMPAT: dict[EventType, frozenset[EventType]] = {
    "arrival": frozenset({"arrival", "still_at"}),
    "still_at": frozenset({"arrival", "still_at"}),
    "departure": frozenset({"departure"}),
    "transit": frozenset({"transit"}),
}


@dataclass(frozen=True)
class _LineageEntry:
    seq: int
    actor: str
    lineage: str
    scope: str
    crate: int
    location_id: str | None
    event_type: EventType


def _entries(history: tuple[HistoryItem, ...]) -> list[_LineageEntry]:
    out: list[_LineageEntry] = []
    for item in history:
        if item.kind == "record" and item.crate is not None and item.event_type is not None:
            out.append(
                _LineageEntry(
                    seq=item.seq,
                    actor=item.actor,
                    lineage=f"record:{item.record_id}",
                    scope=item.scope,
                    crate=item.crate,
                    location_id=item.location_id,
                    event_type=item.event_type,
                )
            )
        elif item.kind == "message":
            for assertion in message_history_assertions(item):
                if assertion.event_type is None:
                    continue
                out.append(
                    _LineageEntry(
                        seq=item.seq,
                        actor=item.actor,
                        lineage=f"msg:{item.seq}",
                        scope=item.scope,
                        crate=assertion.crate,
                        location_id=assertion.location_id,
                        event_type=assertion.event_type,
                    )
                )
    return out


def derive_novelty(
    assertion: CodedAssertion,
    history: tuple[HistoryItem, ...],
    actor: str,
) -> tuple[Novelty, bool, str | None]:
    """Return ``(novelty, novel_to_recipient, lineage_id)`` for one assertion.

    Negated, ambiguous or contested claims are ``unresolved``: the derivation refuses
    to credit them as new evidence (a negation is not evidence of the affirmative, a
    restatement of a superseded record is not novel, and a claim the speaker disputes
    or temporally challenges cannot be credited either).
    """
    if (
        assertion.assertion in ("negated",)
        or assertion.ambiguous
        or assertion.temporal_challenge
        or assertion.stance == "contradict"
    ):
        return "unresolved", False, None
    entries = _entries(history)
    matches = [
        e
        for e in entries
        if e.crate == assertion.crate
        and assertion.event_type is not None
        and assertion.event_type in _COMPAT[e.event_type]
        and (assertion.location_id is None or e.location_id == assertion.location_id)
    ]
    if not matches:
        return "first_introduction", True, None
    # Chronology: a later departure/transit for the same crate at that location makes a
    # restated presence claim stale-on-arrival — unresolved, not repetition.
    if assertion.event_type in ("arrival", "still_at"):
        superseding = [
            e
            for e in entries
            if e.crate == assertion.crate
            and e.location_id == assertion.location_id
            and e.event_type in ("departure", "transit")
            and e.seq > min(m.seq for m in matches)
        ]
        if superseding:
            return "unresolved", False, max(superseding, key=lambda e: e.seq).lineage
    earliest = min(matches, key=lambda e: e.seq)
    broadcast = any(e.scope == "broadcast" for e in matches)
    novelty: Novelty = "self_repetition" if earliest.actor == actor else "other_source_repetition"
    return novelty, not broadcast, earliest.lineage


def derive_attachment_novelty(
    assertion: CodedAssertion,
    history: tuple[HistoryItem, ...],
) -> tuple[Novelty, bool, str | None]:
    """Attachments are authoritative; novelty is decided by the record's prior
    visibility, not by text. A record the team channel already saw is a repetition."""
    record_id = (assertion.lineage or "").removeprefix("record:")
    prior = [item for item in history if item.kind == "record" and item.record_id == record_id]
    if not prior:
        return "attachment", True, assertion.lineage
    broadcast = any(item.scope == "broadcast" for item in prior)
    novelty: Novelty = "other_source_repetition"
    return novelty, not broadcast, assertion.lineage
