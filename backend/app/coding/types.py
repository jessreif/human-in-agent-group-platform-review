"""Shared immutable types for the offline semantic coding pipeline (INV-1202, spec §9.4).

Everything here is a frozen value object: stage-1 text coding and stage-2 history
derivation are pure functions over these types. Persistence lives in ``runs.py``;
nothing in this file touches the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

CODE_SCHEMA = "investigators.coding/1"
CODE_VERSION = "inv1202.deterministic/1"
VALIDATOR_VERSION = "inv1209.expected_actual/1"

EventType = Literal["arrival", "still_at", "departure", "transit"]
Polarity = Literal["asserted", "uncertain", "hedged", "negated"]
Stance = Literal["endorse", "question", "contradict", "uncertain", "no_stance"]
Novelty = Literal[
    "first_introduction",
    "self_repetition",
    "other_source_repetition",
    "attachment",
    "unresolved",
]
SpanKind = Literal["crate", "location", "time", "evidence"]
SubjectStatus = Literal["coded", "not_coded", "failed", "abstained"]
HistoryKind = Literal["record", "message"]
HistoryScope = Literal["broadcast", "participant_file"]


@dataclass(frozen=True)
class Span:
    """An exact text span into the subject body, plus its normalized value."""

    kind: SpanKind
    start: int
    end: int
    text: str
    norm: str

    def as_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "norm": self.norm,
        }


@dataclass(frozen=True)
class Attachment:
    """An authenticated record attachment: structured evidence that bypasses
    semantic matching and is authoritative over the message body."""

    record_id: str
    crate: int
    location_id: str | None
    event_type: EventType
    stamp: str | None = None


@dataclass(frozen=True)
class HistoryItem:
    """One already-visible evidence entry: a record reaching the team channel or a
    participant's file, or a prior broadcast message."""

    seq: int
    actor: str
    kind: HistoryKind
    scope: HistoryScope
    record_id: str | None = None
    crate: int | None = None
    location_id: str | None = None
    event_type: EventType | None = None
    body: str | None = None


@dataclass(frozen=True)
class Subject:
    """One codable unit — a delivered message (plus its authenticated attachments)."""

    seq: int
    actor: str
    channel: str
    body: str
    context_crate: int | None = None
    attachments: tuple[Attachment, ...] = ()


@dataclass(frozen=True)
class CodedAssertion:
    crate: int
    location_id: str | None
    event_type: EventType | None
    assertion: Polarity
    stance: Stance
    inference: bool
    ambiguous: bool
    attribution: str | None
    temporal_challenge: bool
    off_crate: bool
    novelty: Novelty
    novel_to_recipient: bool
    lineage: str | None
    spans: tuple[Span, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {
            "crate": self.crate,
            "location_id": self.location_id,
            "event_type": self.event_type,
            "assertion": self.assertion,
            "stance": self.stance,
            "inference": self.inference,
            "ambiguous": self.ambiguous,
            "attribution": self.attribution,
            "temporal_challenge": self.temporal_challenge,
            "off_crate": self.off_crate,
            "novelty": self.novelty,
            "novel_to_recipient": self.novel_to_recipient,
            "lineage": self.lineage,
            "spans": [sp.as_json() for sp in self.spans],
        }


@dataclass(frozen=True)
class CodedQuestion:
    crate: int | None
    location_id: str | None
    spans: tuple[Span, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {
            "crate": self.crate,
            "location_id": self.location_id,
            "spans": [sp.as_json() for sp in self.spans],
        }


@dataclass(frozen=True)
class CodedSolicitation:
    crates: tuple[int, ...]
    spans: tuple[Span, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {
            "crates": list(self.crates),
            "spans": [sp.as_json() for sp in self.spans],
        }


@dataclass(frozen=True)
class CodedResult:
    """The coding outcome for one subject. ``codes`` is the
    ``investigators.coding/1`` payload; ``adjudication`` records abstain/fail reasons."""

    status: SubjectStatus
    assertions: tuple[CodedAssertion, ...] = ()
    questions: tuple[CodedQuestion, ...] = ()
    solicitations: tuple[CodedSolicitation, ...] = ()
    acknowledgment_only: bool = False
    adjudication: dict[str, Any] | None = None

    @property
    def codes(self) -> dict[str, Any] | None:
        """The JSON payload persisted in ``coding_result.codes``. ``None`` for
        abstained/failed/not_coded — an empty-but-coded subject still carries an
        explicit, non-null codes object so empty differs from missing coverage."""
        if self.status != "coded":
            return None
        return {
            "schema": CODE_SCHEMA,
            "assertions": [a.as_json() for a in self.assertions],
            "questions": [q.as_json() for q in self.questions],
            "solicitations": [so.as_json() for so in self.solicitations],
            "acknowledgment_only": self.acknowledgment_only,
        }


@dataclass(frozen=True)
class ResultRow:
    """A persisted coding outcome: one ``coding_result`` row to insert."""

    subject_key: str
    status: SubjectStatus
    codes: dict[str, Any] | None
    adjudication: dict[str, Any] | None = None
    session_id: Any = None
    supersedes_run_id: Any = None
    supersedes_subject_key: str | None = None
