"""Output-side typed-claim validation for assistant responses (INV-1003; spec §7.5-7.6).

The assistant's generated text can never be trusted at face value: naming a permitted
location (a noun that happens to be inside the snapshot) is *not* evidence. This module
parses candidate claims out of the text — which hideout, which crate, asserted/negated,
arrival/still_at/departure/transit — and evaluates each against the pinned snapshot's
record rows via :func:`app.services.claims.evaluate_support`, the same source-backed
semantics the claim ledger uses:

- wrong crate: the cited crate's records don't mention the location -> unsupported;
- stale / wrong time: an *unanchored* presence claim ("is at", "still at", "might be
  at") contradicted by a later pinned record -> unsupported; a claim carrying the
  entries' own timestamp wording is a retelling and is judged against whether the
  event ever happened, not against the newest position;
- transit-as-arrival: only transit evidence exists for the named place -> unsupported
  (but "heading toward X" legitimately matches a transit record's ``toward``);
- negation inversion: ``not the Marina`` parses as a *negated* claim, never a citation;
- fabricated citation: a ``C<n>-<seq>`` reference not inside the pinned manifest;
- answer-key-like output: the text enumerates location claims across several crates.

Nothing here is a substring salvage — the produced claims are typed and the checker only
ever *narrows* what may be shown; support is decided by ``evaluate_support`` alone.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.domain.world import CRATE_CONTENTS, HIDEOUTS
from app.services import claims as claims_mod
from app.services.claims import SupportVerdict

CHECKER_VERSION = "assistant_output_claims_v5"

# A record citation as rendered in prompts/data: "C3-7" (crate-seq). Any C<n>-<seq>-shaped
# token that is not inside the pinned manifest is a fabricated citation (INV-1003).
_CITATION = re.compile(r"\bC\d{1,2}-\d{1,3}\b")
# "crate 3" / "crate #3" / "crate no. 3" — the crate a claim is about. Covers
# the largest world (12 crates, 2026-10-15): claim parsing is not a range gate.
_CRATE_REF = re.compile(
    r"\bcrate\s*(?:#|no\.?|number)?\s*([1-9]|1[0-2]|one|two|three|four|five|six|"
    r"seven|eight|nine|ten|eleven|twelve)\b",
    re.I,
)
# Assertion cues.
_NEGATION = re.compile(
    r"\b(not|n't|never|no longer|isn't|wasn't|hasn't|haven't|didn't|don't)\b", re.I
)
_UNCERTAIN = re.compile(
    r"\b(maybe|perhaps|might|probably|possibly|appears|seems|i think|not sure|"
    r"unclear|unsure|guess)\b",
    re.I,
)
# A denial of evidence — "the records do not contain anything about crate 2". When
# the pinned set does hold entries for the asked crate, that is itself a false claim
# about the file (the mention-level checker only sees location claims, so a vacuous
# answer would otherwise pass unchallenged).
_DENIAL = re.compile(
    r"\b(do(?:es)? not contain|no information|nothing about|no entries|"
    r"no records|no mention|no data|no evidence)\b",
    re.I,
)

# Event-type cues around the location mention.
_TRANSIT = re.compile(
    r"\b(head(?:ing|ed)?\s+(?:toward|for|to)|en\s*route|on (?:its|the) way|"
    r"in transit|moving toward|bound for|toward|between)\b",
    re.I,
)
_DEPARTURE = re.compile(
    r"\b(left|lea(?:ve|ves|ving)|depart(?:s|ed|ure)?|moved (?:out|on|away)|gone from|"
    r"no longer at|offloaded away|taken from|loaded onto|loaded on)\b",
    re.I,
)
_STILL = re.compile(
    r"\b(is|is still|still at|still on site|currently|right now|remains|now at|"
    r"latest|most recent|last seen|most recently)\b",
    re.I,
)
_ARRIVAL = re.compile(
    r"\b(offloaded?|unloaded|arriv(?:e[ds]?|ing)|delivered|dropped off|reached|"
    r"sighted arriving at|spotted arriving at|"
    r"photographed at|reported at|taken to|brought to|ended up at)\b",
    re.I,
)
# "not moved"/"never left" assert continued presence — they are still-at claims, not
# denials. The pinned records themselves use this wording ("tablets have not moved
# from Blackwell Storage"), so a blanket negation match rejected verbatim echoes
# (RCA-2026-10-09). Presence-asserting negations only — "not delivered to X" is a
# genuine denial and stays negated.
_NOT_MOVED = re.compile(
    r"(?:not|n't|never|hasn't|haven't|hadn't|didn't|don't|won't|wouldn't)\s+"
    r"(?:been\s+)?(?:(?:moved|gone|departed|relocated|shifted|transferred|sent off)"
    r"\s+from|left(?!\s+to))\b",
    re.I,
)
# A temporal anchor near a location claim marks it as a retelling rather than a
# current-state assertion — the entries' own stamp wording echoes through summaries.
_TEMPORAL = re.compile(
    r"\b(yesterday|today|tonight|days? ago|weeks? ago|ago|"
    r"\d{1,2}:\d{2}\s*(?:[ap]\.?m\.?)?|this morning|last night|earlier today)\b",
    re.I,
)

# How far around a location mention a cue may sit (characters).
_CUE_BEFORE = 64
_CUE_AFTER = 64
_ANSWER_KEY_CRATE_LIMIT = 1


@dataclass(frozen=True)
class OutputClaim:
    """One typed claim parsed from assistant text (crate may be unresolvable)."""

    crate: int | None
    location_id: str
    event_type: str
    assertion: claims_mod.Assertion
    source_record_ids: tuple[str, ...]
    verdict: str
    historical: bool = False
    evidence_time: str | None = None
    claimed_citations: tuple[str, ...] = ()
    supporting_record_ids: tuple[str, ...] = ()
    role: str | None = None


@dataclass(frozen=True)
class ClaimCheck:
    violations: tuple[str, ...]
    claims: tuple[OutputClaim, ...]
    response_type: str = "claims"


def _dearticled(hideout: str) -> str:
    return re.sub(r"^the\s+", "", hideout.lower())


def _hideout_positions(text: str) -> list[tuple[int, int, str]]:
    """(start, end, canonical hideout) for every hideout mention — same de-articled
    case-insensitive substring semantics as ``engines.hideouts_in``, plus position.
    Every occurrence is returned: a location re-mentioned later in the text is a
    second claim, and skipping it would let unsupported later mentions escape the
    gate entirely (RCA-2026-10-09)."""
    t = text.lower()
    out: list[tuple[int, int, str]] = []
    for h in HIDEOUTS:
        bare = _dearticled(h)
        i = t.find(bare)
        while i >= 0:
            out.append((i, i + len(bare), h))
            i = t.find(bare, i + len(bare))
    out.sort(key=lambda m: m[0])
    return out


def _crate_near(text: str, pos: int, default: int | None) -> int | None:
    """The crate a location claim is about: the nearest ``crate N`` reference (prefer the
    closest preceding one), else the request's own crate."""
    refs = [(m.start(), _number(m.group(1))) for m in _CRATE_REF.finditer(text)]
    if not refs:
        return default
    # prefer the closest preceding ref; else the nearest following one
    before = [r for r in refs if r[0] <= pos]
    if before:
        return before[-1][1]
    return refs[0][1]


def _assertion_at(before: str, after: str) -> claims_mod.Assertion:
    """Whether this specific mention is negated: a negation cue attaches to the mention
    when no *other* hideout name sits between the cue and the mention. Presence-asserting
    negations ("have not moved from X") are exempt — they assert, not deny."""
    tail = before[-_CUE_BEFORE:]
    cue_at = None
    for m in _NEGATION.finditer(tail):
        if _NOT_MOVED.match(tail, m.start()):
            continue
        cue_at = m.start()  # last negation in the preceding window
    if cue_at is not None:
        # another hideout between the cue and this mention owns the negation instead
        later = [m for m in _hideout_positions(tail) if m[0] > cue_at]
        if not later:
            return "negated"
    head = after[:_CUE_AFTER]
    for m in _NEGATION.finditer(head):
        if _NOT_MOVED.match(head, m.start()):
            continue
        # e.g. "the Marina is not where it went" -> negated; but "at the Marina,
        # not the Sawmill" -> the negation belongs to the Sawmill, not the Marina.
        following = _hideout_positions(head[m.end() :])
        if not following:
            return "negated"
        break
    if _UNCERTAIN.search(tail) or _UNCERTAIN.search(head):
        return "uncertain"
    return "asserted"


# Cue patterns in precedence order for equal distances (transit is the most specific
# leg wording, arrival the default when nothing cues at all).
_EVENT_CUES: tuple[tuple[re.Pattern[str], str], ...] = (
    (_TRANSIT, "transit"),
    (_DEPARTURE, "departure"),
    (_STILL, "still_at"),
    (_NOT_MOVED, "still_at"),
    (_ARRIVAL, "arrival"),
)


def _event_type_at(before: str, after: str) -> str:
    """Type the claim by the cue *nearest* the mention. A global pattern precedence
    lets a transit cue in a neighbouring clause mistype a plainly-arrival mention
    ("…heading toward the Old Cannery area … was offloaded at Old Cannery" typed the
    second mention transit and rejected a verbatim echo — RCA-2026-10-09)."""
    tail = before[-_CUE_BEFORE:]
    head = after[:_CUE_AFTER]
    best: tuple[int, int, int, str] | None = None  # (distance, following-penalty, event_type)
    for rank, (pat, etype) in enumerate(_EVENT_CUES):
        for m in pat.finditer(tail):
            cand = (0, len(tail) - m.end(), rank, etype)
            if best is None or cand < best:
                best = cand
        for m in pat.finditer(head):
            cand = (1, m.start(), rank, etype)
            if best is None or cand < best:
                best = cand
    return best[3] if best is not None else "still_at"


def check_assistant_output(
    text: str,
    *,
    crate: int | None,
    records: Sequence[Mapping[str, object]],
) -> ClaimCheck:
    """Parse and gate the assistant's typed claims against the pinned record set.

    ``records`` are the hash-verified rows from the job's snapshot manifest (they carry
    ``record_id``, ``crate``, ``location_id``, ``event_type``, ``timeline_ordinal``).
    Returns the violation labels plus the parsed claims (registered for audit upstream).
    """
    scoped_absence = re.fullmatch(
        r"No entries report crate ([1-9]|1[0-2]) at (.+?)[.]?", text.strip(), re.I
    )
    if scoped_absence:
        subject = int(scoped_absence.group(1))
        place = scoped_absence.group(2).rstrip(".")
        canonical = next((h for h in HIDEOUTS if _dearticled(h) == _dearticled(place)), None)
        if (
            subject == crate
            and canonical
            and not any(
                r["crate"] == subject and canonical in (r["location_id"], r.get("toward"))
                for r in records
            )
        ):
            return ClaimCheck((), (), "verified_absence")
        return ClaimCheck(("false_denial",), (), "verified_absence")
    exact = _exact_excerpts(text, crate=crate, records=records)
    if exact is not None:
        return exact
    manifest_ids = {str(r["record_id"]) for r in records}
    violations: list[str] = []
    parsed: list[OutputClaim] = []

    citations = list(dict.fromkeys(m.group(0) for m in _CITATION.finditer(text)))
    if any(c not in manifest_ids for c in citations):
        violations.append("fabricated_citation")

    mentions = _hideout_positions(text)
    from app.services import engines as _engines

    subjects_text = re.sub(
        r"\b(?:warehouse|traffic) camera\b|\bdrone footage\b", "", text, flags=re.I
    )
    crates_mentioned = {_number(m.group(1)) for m in _CRATE_REF.finditer(text)}
    crates_mentioned.update(
        _engines.ask_items_mentioned(subjects_text, n_crates=len(CRATE_CONTENTS))
    )
    if crate is not None and any(c != crate for c in crates_mentioned):
        violations.append("wrong_subject")
    if len(crates_mentioned) > _ANSWER_KEY_CRATE_LIMIT:
        violations.append("answer_key_like")

    # The crate a claim binds to: the asked crate, else the nearest "crate N" — else,
    # when the reply uses contents vocabulary ("the power tools"), the single crate the
    # fixed world map resolves it to. Without that, a correct contents-named answer is
    # structurally unverifiable and auto-rejects (RCA-2026-10-09).
    default_crate = crate
    if default_crate is None:
        item_crates = _engines.ask_items_mentioned(subjects_text, n_crates=len(CRATE_CONTENTS))
        if len(item_crates) == 1:
            default_crate = item_crates[0]

    denial = _DENIAL.search(text)
    if denial:
        # the crate the denial names, else the asked/resolved one
        denial_crate = _crate_near(text, denial.end(), default_crate)
        if any(denial_crate is None or r["crate"] == denial_crate for r in records):
            violations.append("false_denial")

    for start, end, hideout in mentions:
        # Sentence and coordinated clause boundaries prevent cues crossing assertions.
        left = max(
            [0]
            + [
                m.end()
                for m in re.finditer(
                    r"[.!?;\n]|\band\b(?=\s+(?:crate|headed|heading|is|was))", text[:start], re.I
                )
            ]
        )
        following = re.search(
            r"[.!?;\n]|\band\b(?=\s+(?:crate|headed|heading|is|was))", text[end:], re.I
        )
        right = end + following.start() if following else len(text)
        clause = text[left:right]
        before, after = text[left:start], text[end:right]
        assertion = _assertion_at(before, after)
        event_type = _event_type_at(before, after)
        claim_crate = _crate_near(clause, start - left, default_crate)
        role = None
        if event_type == "transit":
            role = (
                "destination"
                if re.search(r"(?:toward|to|for)\s+(?:the\s+)?$", before, re.I)
                else "origin"
            )
            if re.search(r"\bbetween\b", before, re.I):
                role = "destination" if re.search(r"\band\b", before, re.I) else "origin"
        cited = tuple(dict.fromkeys(m.group(0) for m in _CITATION.finditer(clause)))
        temporal = _TEMPORAL.search(clause)
        matching_stamps = {
            str(r.get("stamp"))
            for r in records
            if r.get("stamp") and _norm(str(r["stamp"])) in _norm(clause)
        }
        evidence_time = next(iter(matching_stamps)) if len(matching_stamps) == 1 else None
        current = bool(
            re.search(
                r"\b(?:right now|currently|is (?:still )?at|are (?:still )?at)\b", clause, re.I
            )
        )
        historical = evidence_time is not None and not current
        if temporal and evidence_time is None:
            violations.append("unsupported_time")
        # Support is evaluated against the whole pinned set for the claimed crate — the
        # strictest reading: any superseding record in the snapshot sinks the claim.
        sources = [
            dict(r) for r in records if claim_crate is not None and r["crate"] == claim_crate
        ]
        claim_basis = {
            "crate": claim_crate,
            "location_id": hideout,
            "event_type": event_type,
            "assertion": assertion,
            "historical": historical,
            "evidence_time": evidence_time,
            "role": role,
        }
        verdict = claims_mod.evaluate_support(claim_basis, sources)
        supporting = tuple(
            str(r["record_id"])
            for r in sources
            if claims_mod.evaluate_support(claim_basis, [r]) is SupportVerdict.SUPPORTED
        )
        if cited and any(c not in supporting for c in cited):
            verdict = SupportVerdict.UNSUPPORTED
            violations.append("citation_mismatch")
        parsed.append(
            OutputClaim(
                crate=claim_crate,
                location_id=hideout,
                event_type=event_type,
                assertion=assertion,
                source_record_ids=tuple(str(r["record_id"]) for r in sources),
                verdict=verdict.value,
                historical=historical,
                evidence_time=evidence_time,
                claimed_citations=cited,
                supporting_record_ids=supporting,
                role=role,
            )
        )
        if assertion == "negated":
            violations.append("negated_claim")
        elif verdict is not SupportVerdict.SUPPORTED:
            violations.append("unsupported_claim")

    # A successful factual response must be wholly inside the response language.
    # A known noun plus a supported claim cannot license arbitrary extra facts.
    if not _response_covered(text, crate=default_crate, records=records):
        violations.append("unvalidated_response")
    # de-dup while preserving order
    return ClaimCheck(
        violations=tuple(dict.fromkeys(violations)),
        claims=tuple(parsed),
    )


def _number(value: str) -> int:
    words = (
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
    )
    return int(value) if value.isdigit() else words.index(value.lower()) + 1


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _response_covered(
    text: str, *, crate: int | None, records: Sequence[Mapping[str, object]]
) -> bool:
    """Finite response contract: exact stamped excerpts or complete simple claims.

    No token blacklist claims to understand arbitrary factual prose. Excerpts license
    the whole source statement, including surveillance roles and nonlocation facts.
    """
    if text.strip().lower().rstrip(".") == "i cannot verify an answer from the saved file":
        return True
    if (
        crate is not None
        and not any(r["crate"] == crate for r in records)
        and _norm(text) == f"there are no entries for crate {crate} in the saved file"
    ):
        return True
    remainder = text
    for r in sorted(records, key=lambda r: len(str(r.get("body", ""))), reverse=True):
        if crate is None or r["crate"] != crate or not r.get("body") or not r.get("stamp"):
            continue
        # The model chooses entries but must quote their stamp and complete body.
        excerpt = f"{r['stamp']} {r['body']}"
        pattern = r"[\s,.]*".join(re.escape(x) for x in re.findall(r"[^\s,.]+", excerpt))
        remainder = re.sub(pattern, "", remainder, flags=re.I)
    places = "|".join(re.escape(_dearticled(h)) for h in HIDEOUTS)
    subject = (
        r"crate\s*(?:#|no\.?|number)?\s*(?:[1-9]|1[0-2]|one|two|three|four|five|six|"
        r"seven|eight|nine|ten|eleven|twelve)"
    )
    verb = (
        r"(?:at|is (?:still )?at|was at|might be at|arrived at|was unloaded at|left|departed|"
        r"is heading toward|is headed toward|is in transit from)"
    )
    citation = (
        r"(?:(?:entry|record|entries)\s+C\d+-\d+(?:\s+and\s+C\d+-\d+)*\s+(?:says?\s+|puts?\s+)?)?"
    )
    for sentence in re.split(r"[.!?\n]+", remainder):
        if not sentence.strip(" ,"):
            continue
        if not re.fullmatch(
            rf"\s*(?:the latest entry(?: in your file)? puts\s+)?{citation}{subject}\s+{verb}"
            rf"\s+(?:the\s+)?(?:{places})\s*",
            sentence,
            re.I,
        ):
            return False
    return True


def _exact_excerpts(
    text: str, *, crate: int | None, records: Sequence[Mapping[str, object]]
) -> ClaimCheck | None:
    """Whole-response matching gives authoritative templates their structured semantics."""
    remaining = text.strip()
    selected: list[Mapping[str, object]] = []
    while remaining:
        found = False
        for record in records:
            if crate != record["crate"] or not record.get("body") or not record.get("stamp"):
                continue
            excerpt = f"{record['stamp']} {record['body']}"
            pattern = r"[\s,.]*".join(re.escape(x) for x in re.findall(r"[^\s,.]+", excerpt))
            match = re.match(pattern + r"(?=[\s.]|$)", remaining, re.I)
            if match:
                remaining = remaining[match.end() :].lstrip(" .\n")
                selected.append(record)
                found = True
                break
        if not found or len(selected) > 3:
            return None
    if not selected:
        return None
    parsed = []
    for record in selected:
        for location, role in (
            (record["location_id"], "origin"),
            (record.get("toward"), "destination"),
        ):
            if not location:
                continue
            parsed.append(
                OutputClaim(
                    crate=crate,
                    location_id=str(location),
                    event_type=str(record["event_type"]),
                    assertion="asserted",
                    source_record_ids=tuple(
                        str(r["record_id"]) for r in records if r["crate"] == crate
                    ),
                    verdict="supported",
                    historical=True,
                    evidence_time=str(record["stamp"]),
                    supporting_record_ids=(str(record["record_id"]),),
                    role=role if record["event_type"] == "transit" else None,
                )
            )
    return ClaimCheck(violations=(), claims=tuple(parsed), response_type="record_excerpts")
