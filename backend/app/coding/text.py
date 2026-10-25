"""Stage-1 deterministic message coding (INV-1202; ``coding.rubric@v1``).

Pure, rules-based surface parsing: sentence splitting, exact crate/location/time/evidence
spans, event lexemes, negation/uncertainty polarity, stance and attribution. Anything
that cannot be resolved without guessing is marked ambiguous — the caller turns that
into ``abstained``. Novelty/repetition is *not* decided here; that is stage 2
(:mod:`app.coding.history`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.coding.types import (
    CodedAssertion,
    CodedQuestion,
    CodedSolicitation,
    EventType,
    HistoryItem,
    Polarity,
    Span,
    Stance,
    Subject,
)
from app.domain.world import HIDEOUTS

# ----- lexicon (all patterns match case-insensitively on the original text) ---------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_CRATE = re.compile(r"\bcrate\s*#?\s*(\d{1,2})\b", re.IGNORECASE)
_NEGATED_EVENT = re.compile(
    r"\b(?:did\s+not|didn't|does\s+not|doesn't|has\s+not|hasn't|have\s+not|haven't|never)"
    r"\s+(?:\w+\s+){0,2}?"
    r"(arrive[sd]?|arriving|reach(?:ed|es)?|land(?:ed|s)?|depart(?:ed|s)?|leave|leaves|left|"
    r"head(?:ed|s|ing)?|move[sd]?|unload(?:ed|s)?)\b",
    re.IGNORECASE,
)
_NEG_BARE = re.compile(r"\b(?:isn't|wasn't|not\s+(?:at|there|really))\b", re.IGNORECASE)
_HEDGE = re.compile(r"\b(?:i think|i believe|i guess|not sure|unsure|pretty sure)\b", re.IGNORECASE)
_UNCERTAIN = re.compile(
    r"\b(?:might|maybe|probably|perhaps|possibly|supposedly|apparently|"
    r"seems?|looks like|heard|rumou?r(?:ed)?)\b",
    re.IGNORECASE,
)
_ATTRIBUTION = re.compile(
    r"\b(JT|PM)\s+(?:says|said|claims?|thinks?|reported|disagrees?)\b|\bper\s+(JT|PM)\b"
)
_REPORTED = re.compile(r"\b(?:says|said|claims?|reported)\b", re.IGNORECASE)
_THIRD_PARTY_DISAGREE = re.compile(r"\bbut\s+(JT|PM)\s+disagrees?\b", re.IGNORECASE)
_CHALLENGE = re.compile(
    r"\b(?:but|however|actually|stale|old|out[- ]of[- ]date|wrong|not right|outdated)\b",
    re.IGNORECASE,
)
_ENDORSE = re.compile(
    r"\b(?:correct|confirm(?:ed|s)?|agree[ds]?|freshest|fresh|newest|latest|"
    r"up[- ]to[- ]date|the fresh one|checks out)\b",
    re.IGNORECASE,
)
_TEMPORAL_CHALLENGE = re.compile(
    r"\b(?:old|stale|out[- ]of[- ]date|outdated|superseded)\b",
    re.IGNORECASE,
)
_INFERENCE = re.compile(
    r"\b(?:because|therefore|that's why|means that|implies|if\b.{0,80}\bthen\b)\b",
    re.IGNORECASE,
)
_QUESTION_LEAD = re.compile(
    r"^\s*(?:did|does|do|is|are|was|were|has|have|can|could|should|what|which|where|"
    r"who|whom|when|why|how|any|anything|anyone)\b",
    re.IGNORECASE,
)
_SOLICIT = re.compile(
    r"\b(?:send\s+me|share\s+(?:with\s+me|it|me)|give\s+me|pass\s+(?:along|over|it)|"
    r"hand\s+over|whatever|anything\s+(?:useful|new|else|on)|any\s+(?:news|info|intel|"
    r"updates?|records?|useful)|what\s+do\s+you\s+have|do\s+you\s+have\s+any|"
    r"got\s+anything|anything\s+for\s+my|useful\s+for)\b",
    re.IGNORECASE,
)
_ACK = re.compile(
    r"^(?:\s*(?:thanks|thank\s+you|thx|ok(?:ay)?|got\s+it|noted|makes\s+sense|"
    r"good\s+to\s+know|yes|yep|yeah|right|sounds\s+good|appreciated|nice|cool|sure|"
    r"all\s+good(?:\s+here)?|doing\s+fine|you\?|fyi|aha|wow|lol)[,!.\s]*)+$",
    re.IGNORECASE,
)
_TIME = re.compile(
    r"\b(?:yesterday|today|tonight|this\s+morning|this\s+afternoon|this\s+evening|"
    r"earlier\s+today|earlier|just\s+now|now|an?\s+hour\s+ago|"
    r"\d+\s+(?:hours?|minutes?|days?)\s+ago|\d{1,2}:\d{2}\s*(?:am|pm)?|"
    r"this\s+week|last\s+night)\b",
    re.IGNORECASE,
)
_EVIDENCE_NOUN = re.compile(
    r"\b(?:record|card|photo|report|update|tip|sighting|ping)\b", re.IGNORECASE
)
# Domain-referential phrasing without a resolvable crate/location makes a question
# ambiguous rather than social ("the one near the dock").
_DOMAIN_REF = re.compile(
    r"\b(?:the\s+one|that\s+one|this\s+one|near\s+the|the\s+dock|dock|record|card|"
    r"crate|hideout|there)\b",
    re.IGNORECASE,
)

# Event lexemes, checked in this order (departure/transit before still_at/arrival so
# "left" is never read as presence; "still at" before the bare-locative fallback).
_EVENT_LEXEMES: tuple[tuple[EventType, re.Pattern[str]], ...] = (
    (
        "departure",
        re.compile(
            r"\b(?:depart(?:ed|s|ing)?|left|leav(?:es|ing)|loaded\s+onto|picked\s+up|"
            r"no\s+longer\s+at|gone\s+from|moved\s+(?:out|off)|pulled\s+out)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "transit",
        re.compile(
            r"\b(?:head(?:ed|ing|s)?|en\s*route|in\s+transit|on\s+its\s+way|"
            r"bound\s+for|toward|between\b.+\band\b)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "arrival",
        re.compile(
            r"\b(?:arrive[sd]?|arriving|reach(?:ed|es)?|land(?:ed|s)?|offloaded|"
            r"unloaded|dropped\s+off|loading\s+dock|made\s+it\s+to|got\s+to)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "still_at",
        re.compile(
            r"\b(?:still\s+at|still\s+on\s+site|remains?\s+at|staying\s+at|held\s+at|"
            r"hasn't\s+moved|have\s+not\s+moved|has\s+not\s+moved|is\s+at|was\s+at|"
            r"is\s+still|was\s+still|on\s+site|sitting\s+at)\b",
            re.IGNORECASE,
        ),
    ),
)
_LOCATIVE = re.compile(r"\b(?:at|in|near)\b", re.IGNORECASE)


def _location_lexicon() -> tuple[tuple[re.Pattern[str], str], ...]:
    """Canonical hideout names plus their suffixes ("Marina" -> "The Marina")."""
    entries: list[tuple[str, str]] = []
    for hideout in HIDEOUTS:
        entries.append((hideout, hideout))
        suffix = hideout.removeprefix("The ")
        if suffix != hideout:
            entries.append((suffix, hideout))
    entries.sort(key=lambda e: len(e[0]), reverse=True)
    return tuple(
        (re.compile(r"\b" + re.escape(text) + r"\b", re.IGNORECASE), canon)
        for text, canon in entries
    )


_LOCATIONS = _location_lexicon()


@dataclass(frozen=True)
class ParsedSentence:
    start: int
    end: int
    text: str


def split_sentences(body: str) -> tuple[ParsedSentence, ...]:
    out: list[ParsedSentence] = []
    cursor = 0
    for part in _SENTENCE_SPLIT.split(body):
        idx = body.find(part, cursor)
        if idx < 0 or not part.strip():
            continue
        out.append(ParsedSentence(idx, idx + len(part), part))
        cursor = idx + len(part)
    return tuple(out)


def _crate_spans(sentence: ParsedSentence) -> tuple[Span, ...]:
    return tuple(
        Span("crate", sentence.start + m.start(), sentence.start + m.end(), m.group(0), m.group(1))
        for m in _CRATE.finditer(sentence.text)
    )


def _location_spans(sentence: ParsedSentence) -> tuple[Span, ...]:
    spans: list[Span] = []
    for pattern, canon in _LOCATIONS:
        spans.extend(
            Span(
                "location", sentence.start + m.start(), sentence.start + m.end(), m.group(0), canon
            )
            for m in pattern.finditer(sentence.text)
        )
    # overlapping alternatives (hideout vs its suffix): keep the longest first.
    spans.sort(key=lambda sp: (sp.start, -(sp.end - sp.start)))
    kept: list[Span] = []
    for sp in spans:
        if all(sp.start >= k.end or sp.end <= k.start for k in kept):
            kept.append(sp)
    return tuple(sorted(kept, key=lambda sp: sp.start))


def _time_spans(sentence: ParsedSentence) -> tuple[Span, ...]:
    return tuple(
        Span(
            "time",
            sentence.start + m.start(),
            sentence.start + m.end(),
            m.group(0),
            m.group(0).lower(),
        )
        for m in _TIME.finditer(sentence.text)
    )


def _evidence_noun_spans(sentence: ParsedSentence) -> tuple[Span, ...]:
    return tuple(
        Span(
            "evidence",
            sentence.start + m.start(),
            sentence.start + m.end(),
            m.group(0),
            m.group(0).lower(),
        )
        for m in _EVIDENCE_NOUN.finditer(sentence.text)
    )


def _classify_event(text: str) -> tuple[EventType, re.Match[str]] | None:
    for event_type, pattern in _EVENT_LEXEMES:
        m = pattern.search(text)
        if m:
            return event_type, m
    return None


_DEPART_VERB = re.compile(r"^(?:depart|leave|left|head|move|unload)", re.IGNORECASE)


def is_question_sentence(sentence: ParsedSentence) -> bool:
    """Aux-inversion/wh-lead sentences are interrogatives — they may carry an evidence
    hypothesis but never a location assertion."""
    return bool(_QUESTION_LEAD.match(sentence.text))


def parse_assertion(
    sentence: ParsedSentence,
    *,
    context_crate: int | None,
) -> tuple[CodedAssertion | None, str | None]:
    """Parse one declarative sentence into a ``CodedAssertion`` shell (novelty is left
    for stage 2). Returns ``(assertion, ambiguous_reason)``; ``(None, None)`` means the
    sentence carries no location claim at all."""
    text = sentence.text
    crates = _crate_spans(sentence)
    if not crates:
        return None, None
    if len(crates) > 1:
        return None, "multiple crates in one clause"
    crate_no = int(crates[0].norm)
    if crate_no < 1 or crate_no > 12:
        return None, f"crate {crate_no} outside 1-12"

    locations = _location_spans(sentence)
    distinct = {sp.norm for sp in locations}
    if len(distinct) > 1 and not re.search(r"\bbetween\b.+\band\b", text, re.IGNORECASE):
        return None, "conflicting locations in one clause"
    location = locations[0].norm if locations else None

    neg = _NEGATED_EVENT.search(text)
    evidence_start: int | None = None
    evidence_floor = 0
    if neg is not None:
        event_type: EventType | None = (
            "departure" if _DEPART_VERB.match(neg.group(1)) else "arrival"
        )
        polarity: Polarity = "negated"
        evidence_start, evidence_floor = neg.start(), neg.end()
    else:
        classified = _classify_event(text)
        if classified is not None:
            event_type, match = classified
            evidence_start, evidence_floor = match.start(), match.end()
            polarity = "negated" if _NEG_BARE.search(text) else "asserted"
        elif location is not None and _LOCATIVE.search(text):
            loc = _LOCATIVE.search(text)
            assert loc is not None
            event_type, polarity = "still_at", "asserted"
            evidence_start, evidence_floor = loc.start(), loc.end()
        elif location is not None and _EVIDENCE_NOUN.search(text):
            # "the Marina card" — evidence attesting presence, not a move.
            noun = _EVIDENCE_NOUN.search(text)
            assert noun is not None
            event_type, polarity = "still_at", "asserted"
            evidence_start, evidence_floor = noun.start(), noun.end()
        elif location is not None:
            return None, "crate+location with no state lexeme"
        else:
            return None, None  # referential crate mention, no claim

    if polarity == "asserted":
        if _HEDGE.search(text):
            polarity = "hedged"
        elif _UNCERTAIN.search(text):
            polarity = "uncertain"

    attribution = None
    m = _ATTRIBUTION.search(text)
    if m:
        attribution = m.group(1) or m.group(2)
    disagree = _THIRD_PARTY_DISAGREE.search(text)
    if disagree:
        attribution = attribution or disagree.group(1)
    if attribution and _REPORTED.search(text) and polarity == "asserted":
        polarity = "uncertain"

    temporal_challenge = bool(_TEMPORAL_CHALLENGE.search(text))
    stance: Stance = "no_stance"
    if disagree:
        stance = "uncertain"
    elif temporal_challenge or (attribution and _CHALLENGE.search(text)):
        stance = "contradict"
    elif _ENDORSE.search(text):
        stance = "endorse"
    if text.rstrip().endswith("?") and stance in ("no_stance", "endorse"):
        stance = "question"

    spans: list[Span] = [
        crates[0],
        *locations,
        *_time_spans(sentence),
        *_evidence_noun_spans(sentence),
    ]
    if evidence_start is not None:
        # the evidence span covers the event phrase through the last location mention;
        # trailing time/stance qualifiers keep their own spans
        end = max([evidence_floor] + [sp.end - sentence.start for sp in locations])
        end = min(end, len(text.rstrip().rstrip(".!?,;:")))
        evidence = text[evidence_start:end]
        spans.append(
            Span(
                "evidence",
                sentence.start + evidence_start,
                sentence.start + end,
                evidence,
                evidence.lower(),
            )
        )
    return (
        CodedAssertion(
            crate=crate_no,
            location_id=location,
            event_type=event_type,
            assertion=polarity,
            stance=stance,
            inference=bool(_INFERENCE.search(text)),
            ambiguous=False,
            attribution=attribution,
            temporal_challenge=temporal_challenge,
            off_crate=context_crate is not None and crate_no != context_crate,
            novelty="unresolved",
            novel_to_recipient=False,
            lineage=None,
            spans=tuple(sorted(spans, key=lambda sp: sp.start)),
        ),
        None,
    )


def parse_question(sentence: ParsedSentence) -> tuple[CodedQuestion | None, str | None]:
    """Parse one question into an evidence hypothesis. ``(None, None)`` when the
    question has no crate-domain content; ``(None, reason)`` when it has domain
    referents that cannot be resolved without guessing."""
    text = sentence.text
    if not (text.rstrip().endswith("?") or _QUESTION_LEAD.match(text)):
        return None, None
    crates = _crate_spans(sentence)
    locations = _location_spans(sentence)
    if not crates and not locations:
        if _DOMAIN_REF.search(text):
            return None, "question about an unresolvable referent"
        return None, None
    return (
        CodedQuestion(
            crate=int(crates[0].norm) if crates else None,
            location_id=locations[0].norm if locations else None,
            spans=tuple([*crates, *locations]),
        ),
        None,
    )


def parse_solicitation(sentence: ParsedSentence) -> CodedSolicitation | None:
    if not _SOLICIT.search(sentence.text):
        return None
    crates = tuple(sorted({int(sp.norm) for sp in _crate_spans(sentence)}))
    return CodedSolicitation(
        crates=crates,
        spans=(*_crate_spans(sentence), *_location_spans(sentence)),
    )


def is_acknowledgment(body: str) -> bool:
    return bool(_ACK.match(body.strip()))


def attachment_assertions(subject: Subject) -> tuple[CodedAssertion, ...]:
    """Authenticated attachments bypass semantic matching: assertions are taken
    verbatim from the record's structured fields; the body is not matched. An
    evidence span is emitted only when the body literally names the record id."""
    out: list[CodedAssertion] = []
    for att in subject.attachments:
        spans: list[Span] = []
        idx = subject.body.find(att.record_id)
        if idx >= 0:
            spans.append(
                Span("evidence", idx, idx + len(att.record_id), att.record_id, att.record_id)
            )
        out.append(
            CodedAssertion(
                crate=att.crate,
                location_id=att.location_id,
                event_type=att.event_type,
                assertion="asserted",
                stance="no_stance",
                inference=False,
                ambiguous=False,
                attribution=None,
                temporal_challenge=False,
                off_crate=(
                    subject.context_crate is not None and att.crate != subject.context_crate
                ),
                novelty="attachment",
                novel_to_recipient=False,
                lineage=f"record:{att.record_id}",
                spans=tuple(spans),
            )
        )
    return tuple(out)


def message_history_assertions(item: HistoryItem) -> tuple[CodedAssertion, ...]:
    """Stage-1 only: parse a prior broadcast message's body into assertion shells so
    its claims join evidence lineages (multi-channel repetition of one record stays
    one lineage)."""
    if not item.body:
        return ()
    out: list[CodedAssertion] = []
    for sentence in split_sentences(item.body):
        if is_question_sentence(sentence):
            continue
        assertion, _reason = parse_assertion(sentence, context_crate=None)
        if assertion is not None:
            out.append(assertion)
    return tuple(out)
