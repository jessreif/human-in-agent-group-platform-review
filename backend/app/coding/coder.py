"""The deterministic coder (INV-1202). ``DeterministicCoder.code`` is the single pure
entry point: stage-1 surface parse, then stage-2 history/novelty derivation. It never
touches the database and never calls a provider."""

from __future__ import annotations

from dataclasses import replace

from app.coding.history import derive_attachment_novelty, derive_novelty
from app.coding.text import (
    attachment_assertions,
    is_acknowledgment,
    is_question_sentence,
    parse_assertion,
    parse_question,
    parse_solicitation,
    split_sentences,
)
from app.coding.types import (
    CodedAssertion,
    CodedQuestion,
    CodedResult,
    CodedSolicitation,
    HistoryItem,
    Subject,
)

# Channels/actors that carry no participant-authored semantics — excluded by policy,
# not "no codes found".
NOT_CODABLE_ACTORS = frozenset({"system"})
NOT_CODABLE_CHANNELS = frozenset({"system"})


class DeterministicCoder:
    """Codes one message-level subject against its already-visible evidence history."""

    def code(self, subject: Subject, history: tuple[HistoryItem, ...]) -> CodedResult:
        try:
            return self._code(subject, history)
        except Exception as exc:  # noqa: BLE001 - a parser fault is a result, not a crash
            return CodedResult(
                status="failed",
                adjudication={"error": f"{type(exc).__name__}: {exc}"},
            )

    def _code(self, subject: Subject, history: tuple[HistoryItem, ...]) -> CodedResult:
        if subject.actor in NOT_CODABLE_ACTORS or subject.channel in NOT_CODABLE_CHANNELS:
            return CodedResult(
                status="not_coded", adjudication={"reason": "non-codable actor/channel"}
            )
        if not subject.body.strip() and not subject.attachments:
            return CodedResult(status="not_coded", adjudication={"reason": "empty subject"})

        assertions: list[CodedAssertion] = []
        questions: list[CodedQuestion] = []
        solicitations: list[CodedSolicitation] = []
        reasons: list[str] = []

        if subject.attachments:
            # Authenticated attachments bypass semantic matching entirely.
            assertions.extend(attachment_assertions(subject))
            reasons.append("text bypassed: authenticated attachment is authoritative")
        else:
            for sentence in split_sentences(subject.body):
                if is_question_sentence(sentence):
                    question, q_reason = parse_question(sentence)
                    if q_reason:
                        reasons.append(q_reason)
                    if question is not None:
                        questions.append(question)
                else:
                    assertion, a_reason = parse_assertion(
                        sentence, context_crate=subject.context_crate
                    )
                    if a_reason:
                        reasons.append(a_reason)
                    if assertion is not None:
                        assertions.append(assertion)
                solicitation = parse_solicitation(sentence)
                if solicitation is not None:
                    solicitations.append(solicitation)

        ambiguous = [r for r in reasons if not r.startswith("text bypassed")]
        if ambiguous:
            return CodedResult(
                status="abstained",
                adjudication={
                    "reasons": ambiguous,
                    "note": "ambiguous text is never guessed (coding.rubric@v1)",
                },
            )

        derived: list[CodedAssertion] = []
        for assertion in assertions:
            if assertion.novelty == "attachment":
                novelty, novel, lineage = derive_attachment_novelty(assertion, history)
            else:
                novelty, novel, lineage = derive_novelty(assertion, history, subject.actor)
            derived.append(
                replace(
                    assertion,
                    novelty=novelty,
                    novel_to_recipient=novel,
                    lineage=lineage,
                )
            )

        ack_only = (
            not derived and not questions and not solicitations and is_acknowledgment(subject.body)
        )
        return CodedResult(
            status="coded",
            assertions=tuple(derived),
            questions=tuple(questions),
            solicitations=tuple(solicitations),
            acknowledgment_only=ack_only,
        )


CODER = DeterministicCoder()
