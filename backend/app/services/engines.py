"""Delivery-side teammate/assistant engines (WP06; spec §6.3-6.5, §7.2-7.4).

Everything here runs under the session lock at *commit* time. Text never appears earlier than
its job's declared due point, and the pools are deterministic: voice lines come from the
pinned ``voice_bank`` artifact ordered by a single per-(seat, kind) shuffle on the ``voices``
key space of the ``jobs`` stream (the demo's "no repeat until exhausted"), and fallback lines
come from the pinned ``fallback_bank`` classifier — never improvised, never model text past
its validation step.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Literal, NamedTuple, cast

from sqlalchemy import Connection, func, insert, literal, or_, select, update

from app.db import schema as s
from app.db.tx import EventRef, TxContext
from app.domain.manifests.artifacts import (
    ExposureCopyArtifact,
    FallbackBankArtifact,
    VoiceBankArtifact,
)
from app.domain.manifests.binding import BoundManifest
from app.domain.manifests.schema import Seat
from app.domain.manifests.store import StoredArtifact
from app.domain.rng import RandomStreams
from app.domain.world import CRATE_CONTENTS, N_CRATES
from app.timing.policy import resolve_policy

VoiceKind = Literal["greetings", "thanks", "no_news", "after_share"]


# ---------------- deterministic seat styling (AI-3) -----------------------------------------
#
# Bank content is shared across seats (published artifacts are hash-pinned, so per-seat
# wording needs a new artifact version). The deterministic seat style is applied at draw
# time instead: JT renders lower-case/dry, PM renders normal capitalization — the two
# seats can never deliver the identical string, and the transform cannot introduce any
# banned punctuation or vocabulary. Mirrors ``prompts.SEAT_STYLES``; model-generated text
# is styled by the prompt and passes through unchanged.
SEAT_TEXT_STYLE_VERSION = "seat_text_style_v1"

_SENTENCE_START = re.compile(r"(^|[.!?]\s+)([a-z])")


def seat_style(seat: str, line: str) -> str:
    if seat == "PM":
        return _SENTENCE_START.sub(lambda m: m.group(1) + m.group(2).upper(), line)
    return line.lower()


# ---------------- voice lines ------------------------------------------------------------


def _voice_pool(bound: BoundManifest, seat: Seat, kind: VoiceKind) -> tuple[str, ...]:
    stored = bound.artifacts.get("teammates.voice_bank")
    if stored is None or not isinstance(stored.artifact, VoiceBankArtifact):
        return ()
    return tuple(getattr(stored.artifact.body.seats[seat], kind))


def _used_voice_count(conn: Connection, session_id: uuid.UUID, seat: Seat, kind: str) -> int:
    return conn.execute(
        select(func.count())
        .select_from(s.event)
        .where(
            s.event.c.session_id == session_id,
            s.event.c.type == "voice_line_committed",
            s.event.c.actor == seat,
            s.event.c.payload["voice_kind"].astext == kind,
        )
    ).scalar_one()


class VoiceDraw(NamedTuple):
    """A voice-pool draw with its pool bookkeeping (O-17): ``draw_ordinal`` is the
    0-based consumption count for (seat, kind); ``pool_wrapped`` marks the draw
    that went past the pool's end under ``no_repeat_until_exhausted`` — the
    point where the pool starts re-sending earlier lines."""

    text: str
    draw_ordinal: int
    pool_wrapped: bool


def voice_line(
    conn: Connection,
    streams: RandomStreams,
    bound: BoundManifest,
    seat: Seat,
    kind: VoiceKind,
    session_id: uuid.UUID,
    *,
    verbatim: bool = False,
    avoid: Iterable[str] = (),
) -> VoiceDraw | None:
    """The next line for (seat, kind) under the artifact's repeat policy.

    ``no_repeat_until_exhausted``: one seeded shuffle of the pool per session, consumed in
    order; the draw is keyed once, so prior uses are counted from committed events instead of
    re-drawn. Draws past the pool end wrap into the same shuffled order — documented
    repeat semantics, now recorded per draw (O-17).

    ``verbatim`` skips the seat-text style transform — used where the delivered
    line must match participant-facing copy exactly (UX round 4, item 8: greeting
    replies must read like the greeting buttons offered to the participant —
    ``Hi!``/``Hello!``/``Hey!`` — not the lower-cased seat variant ``hi!``).
    ``avoid`` excludes pool entries already delivered this session (normalized
    word match) so the two seats cannot greet with the identical line; if every
    entry is avoided the seeded pick stands (a same-line greeting beats no reply).
    """
    pool = _voice_pool(bound, seat, kind)
    if not pool:
        return None
    stored = bound.artifacts["teammates.voice_bank"].artifact
    assert isinstance(stored, VoiceBankArtifact)
    n = _used_voice_count(conn, session_id, seat, kind)
    wrapped = n >= len(pool)
    avoid_words = {_line_words(a) for a in avoid}
    if stored.body.repeat_policy == "no_repeat_until_exhausted":
        order = streams.shuffle("jobs", range(len(pool)), "voice", seat, kind)
        picked = order[n % len(pool)]
        for k in range(len(pool)):
            cand = order[(n + k) % len(pool)]
            if _line_words(pool[cand]) not in avoid_words:
                picked = cand
                break
        text = pool[picked]
        return VoiceDraw(text if verbatim else seat_style(seat, text), n, wrapped)
    idx = int(streams.uniform("jobs", "voice", seat, kind, n) * len(pool))
    for k in range(len(pool)):
        cand = (idx + k) % len(pool)
        if _line_words(pool[cand]) not in avoid_words:
            idx = cand
            break
    text = pool[idx]
    return VoiceDraw(text if verbatim else seat_style(seat, text), n, wrapped)


def _placeholder(text: str, *, crate: int | None, you: str) -> str:
    return text.replace("{n}", "" if crate is None else str(crate)).replace("{you}", you).strip()


# ---------------- deterministic fallbacks --------------------------------------------------

_GIVES = re.compile(r"( is at | was at |spotted|seen at|moved to|located)", re.I)
_OFFERS = re.compile(
    r"(can i help|how can i|need anything|want me to|i can send|let me know if|helpful)",
    re.I,
)
_SOCIAL = re.compile(
    r"(how are you|hi\b|hey\b|hello|thanks|thank you|good luck|ok\b|okay|sure|nice|cool)",
    re.I,
)
_ASKS = re.compile(
    r"(crate|intel|info|information|anything|recent|newer|update|know|have|send|share|where)",
    re.I,
)
# AI-2: a pleasantry gets a warm social reply even when it carries incidental ask-words
# ("how's it going, do you have a sec?", "hi, got a minute?") — only a concrete intel
# ask outranks it. Greetings and availability checks count as pleasantries; vague
# ask-words ("have", "know") inside them do not outrank the greeting.
_PLEASANTRY = re.compile(
    r"(how are you|how're you|how r u|how's it going|how is it going|how are things|"
    r"how you doing|how are you doing|you doing ok|nice to meet|good to meet|"
    r"good morning|good afternoon|good evening|good luck|hope you're|hope you are|"
    r"\bhi\b|\bhello\b|\bhey\b|\byo\b|hiya|howdy|you there|anyone (?:there|around)|"
    r"(?:have|got) a (?:sec|second|minute|moment))",
    re.I,
)
# A *concrete* intel ask still routes to the request-redirect lines: crate references,
# interrogatives, transfer verbs and intel nouns — but not "have a sec/anything"-style
# availability wording ("have" only counts with a determiner object).
_CONCRETE_ASK = re.compile(
    r"\bcrate\b|\bwhere\b|\bwhich\b|\bsend\b|\bshar(?:e|ed|ing)\b|\bintel\b|"
    r"\binfo\b|\bnews\b|\bupdate[sd]?\b|\bpass\b|\bcheck\b|\blocation\b|"
    r"\bhave (?:any|some|the|your|our|new|more)\b",
    re.I,
)
# BA-58: a greeting wrapped around a *substantive* request ("hey, can you find
# that document?", "hello, can you help me solve this?") is not a pleasantry —
# a warm social line would be a non-answer. Not a concrete intel ask either,
# so it falls through to the generic default lines. Availability wording
# ("have a sec") is deliberately absent here: it stays a pleasantry.
_SUBSTANTIVE_ASK = re.compile(
    r"\bfind\b|\bhelp\b|\bsolve\b|\bfigure\b|\bwork(?:ing)?\s+out\b|\bcrack\b|"
    r"\blocate\b|\bsearch\b|\blook\s+for\b|\bdocument\b|\bevidence\b|"
    r"\banswer\b|\blook\s+at\b|\bmove\s+on\b|\bexplain\b|\bwalk\b",
    re.I,
)

# UX round 6, item 5: a *pure* greeting ("hi", "hey team!", "hello everyone") —
# greeting words plus an optional collective address, nothing else. These route to
# the pinned greeting voice lines instead of generation: the teammate voice
# contract rejects salutation openers, so generated replies to greetings were
# reliably validation-rejected and the session fell back to unrelated status
# lines ("made some progress on one of mine").
_PURE_GREETING = re.compile(
    r"^\s*(?:hi|hello|hey|yo|hiya|howdy|good\s+(?:morning|afternoon|evening))"
    r"[\s!.,?]*"
    r"(?:(?:team|guys|everyone|everybody|y'?all|folks|jt|pm|both|there)[\s!.,?]*)*"
    r"$",
    re.I,
)


def is_greeting(text: str) -> bool:
    """True iff ``text`` is a pure salutation — no substantive content attached."""
    return bool(_PURE_GREETING.match(text.strip()))


_FallbackWhen = Literal[
    "gives_location", "offers_help", "social", "asks_intel", "asks_intel_nl", "default"
]


def _fallback_when(t: str, *, nl_only: bool, has_nl_pool: bool) -> _FallbackWhen:
    """The deterministic ``when`` bucket for a participant message — a pure
    function of the lowercased text so it is unit-testable without a session."""
    gives, offers = bool(_GIVES.search(t)), bool(_OFFERS.search(t))
    social, asks = bool(_SOCIAL.search(t)), bool(_ASKS.search(t))
    if gives:
        return "gives_location"
    if _PLEASANTRY.search(t) and not (_CONCRETE_ASK.search(t) or _SUBSTANTIVE_ASK.search(t)):
        return "social"
    if offers:
        return "offers_help"
    if social and not asks and not _SUBSTANTIVE_ASK.search(t):
        return "social"
    if asks:
        # O-1: in nl_only a typed ask is the *mechanism* — the line invites a
        # crate-numbered typed ask rather than pointing at a hidden picker.
        return "asks_intel_nl" if nl_only and has_nl_pool else "asks_intel"
    return "default"


def seat_workload_rating(
    conn: Connection, session_id: uuid.UUID, seat: str
) -> tuple[int | None, str | None]:
    """The seat's scripted workload rating from the recorded check-in exposure —
    (value, "high"|"low") by the >=5 heavy threshold (mixed {2,6} splits, high
    {6,7} reads heavy, low {2,3} reads light); (None, None) when no exposure row
    exists (nopress, pre-check-in)."""
    row = (
        conn.execute(
            select(s.manipulation_exposure.c.assigned_values)
            .where(
                s.manipulation_exposure.c.session_id == session_id,
                s.manipulation_exposure.c.kind == "pressure_checkin",
            )
            .limit(1)
        )
        .mappings()
        .first()
    )
    if row is None:
        return None, None
    assigned = dict(row["assigned_values"] or {})
    v = assigned.get(seat)
    if v is None:
        return None, None
    return int(v), "high" if int(v) >= 5 else "low"


def social_fallback(
    conn: Connection,
    streams: RandomStreams,
    bound: BoundManifest,
    seat: Seat,
    participant_text: str,
    session_id: uuid.UUID,
    *,
    nl_only: bool = False,
    force_when: _FallbackWhen | None = None,
    avoid: Iterable[str] = (),
) -> str:
    """The demo's ``teammateFallback`` classifier over the pinned ``fallback_bank`` rules.

    Deterministic: the category is a pure function of the participant text, and the line is a
    keyed ``jobs`` draw at the message's key so a retry can never re-roll a different line.
    In ``nl_only`` (O-1) intel asks route to the ``asks_intel_nl`` pool — the
    picker-redirect wording would be false where no picker exists. ``force_when``
    (BA-63) lets a caller that already classified the message pick the pool —
    the structured-mode intel-ask redirect must use ``asks_intel`` wording even
    when the new ask forms ("what about 3?") don't trip the word scan. ``avoid``
    adds same-turn sibling lines to the repeat check — the two seats' simultaneous
    draws happen before either delivers, so ``delivered_teammate_lines`` alone
    cannot stop them from echoing each other (UX round 6, items 6/8).
    """
    stored = bound.artifacts.get("teammates.fallback_bank")
    if stored is None or not isinstance(stored.artifact, FallbackBankArtifact):
        return "noted"
    rules = {r.when: r.lines for r in stored.artifact.body.rules}
    when = force_when or _fallback_when(
        participant_text.lower(), nl_only=nl_only, has_nl_pool="asks_intel_nl" in rules
    )
    # v15: level-keyed pools — the speaker's recorded check-in level picks
    # ``<when>_high``/``<when>_low`` when the bank carries them (nopress and
    # pre-check-in sessions have no exposure row and keep the base pool).
    _v, _lvl = seat_workload_rating(conn, session_id, seat)
    lines = (rules.get(f"{when}_{_lvl}") if _lvl else None) or rules.get(when) or rules["default"]
    n = _used_voice_count(conn, session_id, seat, "fallback")
    idx = int(streams.uniform("jobs", "voice", seat, "fallback", n) * len(lines))
    # AI-1: never re-send a multiword line already delivered in this session. The draw
    # stays keyed (deterministic); collisions rotate to the next unused line, then to
    # the short default acknowledgements, which are exempt from the repeat rule.
    prior = [*delivered_teammate_lines(conn, session_id), *avoid]
    for candidate in [*lines[idx:], *lines[:idx], *rules["default"]]:
        if not is_repeat(candidate, prior) and not is_near_repeat(candidate, prior):
            return seat_style(seat, candidate)
    return seat_style(seat, lines[idx])


# ---------------- repeat guard (AI-1) ------------------------------------------------------

_WORD = re.compile(r"[a-z0-9']+")
# One- and two-word acknowledgements ("thanks", "got it") may recur; anything longer
# must never be sent twice by the teammates in one session.
REPEAT_MIN_WORDS = 3


def _line_words(text: str) -> tuple[str, ...]:
    # Punctuation-equivalence (BA-57): curly and straight apostrophes normalize,
    # then drop — "dont" ≡ "don't"; hyphenated compounds join —
    # "re-check" ≡ "recheck". A teammate line must not evade the gate with punctuation.
    normalized = text.lower().replace("\u2019", "'").replace("\u2018", "'").replace("-", "")
    return tuple(w for w in (t.replace("'", "") for t in _WORD.findall(normalized)) if w)


def is_repeat(text: str, prior: Iterable[str]) -> bool:
    """True iff ``text`` is a multiword line identical (case, punctuation and spacing
    aside) to one already delivered."""
    words = _line_words(text)
    if len(words) < REPEAT_MIN_WORDS:
        return False
    return any(_line_words(p) == words for p in prior)


# Near-duplicate guard: two teammate lines that share most of their vocabulary read as
# one scripted reply with light rewording — the believability tell seen when both seats
# converge on "send me a request and i will/i'll pass along what i have". Short lines
# stay exempt like exact repeats; only high-overlap lines are near-repeats.
NEAR_REPEAT_MIN_WORDS = 6
NEAR_REPEAT_DICE = 0.75


def is_near_repeat(text: str, prior: Iterable[str]) -> bool:
    """True iff ``text`` shares most of its distinct vocabulary with a delivered line —
    a Dice coefficient ≥ ``NEAR_REPEAT_DICE`` on normalized word sets, for lines of at
    least ``NEAR_REPEAT_MIN_WORDS`` distinct words. Exacts are near-repeats too (Dice
    1.0), so callers may use either check."""
    words = set(_line_words(text))
    if len(words) < NEAR_REPEAT_MIN_WORDS:
        return False
    for p in prior:
        pw = set(_line_words(p))
        if pw and 2 * len(words & pw) >= NEAR_REPEAT_DICE * (len(words) + len(pw)):
            return True
    return False


def delivered_teammate_lines(conn: Connection, session_id: uuid.UUID) -> list[str]:
    """Every team-chat line JT or PM has delivered in this session (either seat — a
    teammate must not echo the other one verbatim either)."""
    bodies: list[str] = list(
        conn.execute(
            select(s.message.c.body).where(
                s.message.c.session_id == session_id,
                s.message.c.actor.in_(("JT", "PM")),
                s.message.c.channel == "team",
                s.message.c.committed_seq.is_not(None),
            )
        ).scalars()
    )
    return [str(b) for b in bodies]


# O-12: crate references accept digits, number words, and ordinals ("crate
# three", "crate 4th"), plus "#N" shorthand — all unambiguous in this paradigm.
# 2026-10-15: mention vocabulary covers the largest world (12 crates);
# validity against the session's crate count is a per-call ``n_crates`` bound.
_CRATE_NUM_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "1st": 1,
    "2nd": 2,
    "3rd": 3,
    "4th": 4,
    "5th": 5,
    "6th": 6,
    "7th": 7,
    "8th": 8,
    "9th": 9,
    "10th": 10,
    "11th": 11,
    "12th": 12,
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
}
_CRATE_NUM = (
    r"(?:[1-9]|1[0-2]|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"1st|2nd|3rd|4th|5th|6th|7th|8th|9th|10th|11th|12th|"
    r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth)"
)


def _crate_number(token: str) -> int:
    token = token.strip().lower()
    return _CRATE_NUM_WORDS.get(token, int(token) if token.isdigit() else 0)


_CRATE_MENTION = re.compile(
    r"\bcrate\s*(?:number|no\.?|#)?\s*(" + _CRATE_NUM + r")\b|#\s*([1-9]|1[0-2])\b",
    re.IGNORECASE,
)

# Number tokens beyond the session's crate count — a ``crate N`` / ``#N``
# reference whose N names no real crate ("where is crate 9" in an 8-crate
# world). These must route to a deterministic boundary answer: without an
# out-of-range check the bare word "crate" trips ``_CONTENTS_TERMS`` and the
# assistant answers with the whole crate→item map (observed 2026-10-05).
_CRATE_NUM_WORDS_ANY: dict[str, int] = {
    **_CRATE_NUM_WORDS,
    "zero": 0,
    "zeroth": 0,
    "nine": 9,
    "ninth": 9,
    "9th": 9,
    "ten": 10,
    "tenth": 10,
    "10th": 10,
    "eleven": 11,
    "eleventh": 11,
    "11th": 11,
    "twelve": 12,
    "twelfth": 12,
    "12th": 12,
    "thirteen": 13,
    "thirteenth": 13,
    "13th": 13,
    "fourteen": 14,
    "fourteenth": 14,
    "14th": 14,
    "fifteen": 15,
    "fifteenth": 15,
    "15th": 15,
    "sixteen": 16,
    "sixteenth": 16,
    "16th": 16,
    "seventeen": 17,
    "seventeenth": 17,
    "17th": 17,
    "eighteen": 18,
    "eighteenth": 18,
    "18th": 18,
    "nineteen": 19,
    "nineteenth": 19,
    "19th": 19,
    "twenty": 20,
    "twentieth": 20,
    "20th": 20,
}
_CRATE_ANY_NUM = re.compile(
    r"\bcrate\s*(?:number|no\.?|#)?\s*([a-z0-9]+)\b|#\s*(\d+)\b",
    re.IGNORECASE,
)


def crate_number_out_of_range(text: str, n_crates: int = N_CRATES) -> int | None:
    """A ``crate N`` / ``#N`` reference whose N names no real crate — the world
    runs ``1..n_crates``. Unparseable tokens ("crate contents") are
    not numbered asks and return None; so do in-range numbers."""
    match = _CRATE_ANY_NUM.search(text)
    if match is None:
        return None
    token = (match.group(1) or match.group(2)).lower()
    n = _CRATE_NUM_WORDS_ANY.get(token)
    if n is None:
        n = int(token) if token.isdigit() else None
    if n is None or 1 <= n <= n_crates:
        return None
    return n


def assistant_unknown_crate(crate: int, n_crates: int = N_CRATES) -> str:
    """Deterministic boundary for a numbered ask outside the world's crates —
    the honest range plus the usual redirect, never a map dump."""
    return (
        f"There's no Crate {crate} — the team tracks Crates 1 through "
        f"{n_crates}. Ask me about a crate by number, or ask which "
        f"crate holds an item (like 'which crate has the laptops?')."
    )


def crate_mentioned(text: str, n_crates: int = N_CRATES) -> int | None:
    """First explicit ``crate N`` reference (1..``n_crates``) in participant
    free text — the deterministic assistant fallback scopes its file summary
    by it so a typed "where is crate 4?" answers instead of deflecting."""
    match = _CRATE_MENTION.search(text)
    if not match:
        return None
    n = _crate_number(match.group(1) or match.group(2))
    return n if 1 <= n <= n_crates else None


_CLAUSE_SPLIT = re.compile(r"[.!?;:,\n]+")


def ask_crate_mentioned(text: str, n_crates: int = N_CRATES) -> int | None:
    """The crate a multi-clause message actually *asks* about (BA-64): prefer a
    crate reference inside a clause carrying ask/question wording — "i have
    crate 3. what about crate 4?" asks about 4, not the first mention 3.
    Falls back to the first explicit mention when no clause both asks and
    names a crate. First-person clauses are stripped before the ask-word
    scan so a bare statement never counts as the asking clause."""
    first = crate_mentioned(text, n_crates)
    if first is None:
        return None
    for clause in reversed(_CLAUSE_SPLIT.split(text)):
        if not _NL_INTEL_ASK.search(_FIRST_PERSON_POSSESS.sub(" ", clause)):
            continue
        mention = _CRATE_MENTION.search(clause)
        if mention:
            n = _crate_number(mention.group(1) or mention.group(2))
            if 1 <= n <= n_crates:
                return n
    return first


# ---------------- typed-message routing (O-2, 2026-10-03) --------------------------------
#
# Undirected team chat is heard by BOTH seats; explicit addressing (a seat's
# initials in the text, or the command's ``to`` field) routes to that seat only.
# Detection is a deterministic token scan — auditable, no model call.

_CLOCK_PM = re.compile(r"\b\d{1,2}(?::\d{2})?\s*pm\b", re.IGNORECASE)
_SEAT_TOKEN = re.compile(r"\b(jt|pm)\b", re.IGNORECASE)
_BROADCAST = re.compile(
    r"\b(both|either of you|you two|you guys|you all|y'?all|team|everyone|anyone)\b",
    re.IGNORECASE,
)


def addressed_seats(text: str, explicit_to: str | None) -> frozenset[Seat]:
    """The seat set a typed team message is directed at (O-2): an explicit ``to``
    wins; otherwise seat initials in the text address that seat — unless a
    broadcast token or both initials appear. Undirected text returns both seats.
    Clock text ("6 pm") is stripped so it never reads as addressing PM."""
    if explicit_to in ("JT", "PM"):
        return frozenset({cast(Seat, explicit_to)})
    scanned = _CLOCK_PM.sub(" ", text)
    seats = {cast(Seat, m.group(1).upper()) for m in _SEAT_TOKEN.finditer(scanned)}
    if len(seats) == 1 and not _BROADCAST.search(scanned):
        return frozenset(seats)
    return frozenset(("JT", "PM"))


# A typed ask that should produce a card share in ``nl_only`` (O-1): an explicit
# crate reference combined with an interrogative or ask marker. A bare
# informative "crate 2 is at the marina" (participant *giving* intel) stays
# social — it carries no ask marker.
_NL_INTEL_ASK = re.compile(
    r"\b(where|what|which|whats|what's|anything|any|got|have|newer|new|recent|latest|"
    r"info|intel|update|news|send|share|pass|check|see|hear|know|find|track)\b|\?",
    re.IGNORECASE,
)
# BA-52: the ``asks_intel_nl`` fallback invites "name the crate" — a reply that
# is *only* a crate reference plus politeness filler ("crate 3", "crate 3
# please", "please, crate 3?") IS the follow-up ask and must not fall through to
# social generation. O-12: the crate word itself is optional here — "what about
# 3?", "#5", "anything on the third one", "2" are the same follow-up. A message
# carrying anything else stays on the normal path.
_BARE_CRATE_ASK = re.compile(
    r"^\s*(?:(?:please|pls|plz|hey|hi|ok|okay|so|and|the|on|about|for|of|"
    r"what|how|any|anything|some|you|guys|team|got|have|is|there|send|share|"
    r"check|news|updates?|info|intel|me)\b[\s,]*)*"
    r"(?:crate\s*(?:number|no\.?|#)?\s*|#\s*)?"
    r"(" + _CRATE_NUM + r")(?:\s+one)?\s*"
    r"(?:(?:please|pls|plz|thanks|thank you|ty|one|crates?)\b[\s,]*)*\??\s*$",
    re.IGNORECASE,
)
# BA-62: a leading seat address ("jt, crate 3") is routing, not ask content —
# strip it (and clock text, so a bare "2 pm" never becomes crate 2) before the
# bare-ask shape check. Mid-text seat tokens stay disqualifying.
_LEADING_SEAT_ADDR = re.compile(r"^\s*(?:(?:jt|pm)\b[\s,]*)+", re.IGNORECASE)
# BA-64: a first-person possession clause ("i have crate 3", "we got news on
# crate 3") is a statement, not an intel ask. The clause is stripped up to the
# first sentence boundary before ask-word scanning — "i have 3, do you?" still
# counts because "do you … ?" survives.
_FIRST_PERSON_POSSESS = re.compile(
    r"\b(?:i|we)\s+(?:have|got|found|see|saw)\b[^,.?!:;]*", re.IGNORECASE
)


def _bare_ask_match(text: str) -> re.Match[str] | None:
    """Match ``_BARE_CRATE_ASK`` after removing leading seat addresses and clock
    text (BA-62) — the ask itself must still be pure filler + one number."""
    scanned = _LEADING_SEAT_ADDR.sub("", _CLOCK_PM.sub(" ", text))
    return _BARE_CRATE_ASK.match(scanned)


def followup_crate_mentioned(text: str, n_crates: int = N_CRATES) -> int | None:
    """The crate number in a bare-number follow-up (O-12): "what about 3?",
    "#5 please", "the second one" — messages that are only filler + a number
    (BA-62: an optional leading seat address doesn't count as content).
    Separate from ``crate_mentioned`` so bare digits never scope the assistant's
    file summary without ask context."""
    match = _bare_ask_match(text)
    if match is None:
        return None
    n = _crate_number(match.group(1))
    return n if 1 <= n <= n_crates else None


def assistant_scope(
    text: str, structured: int | None = None, n_crates: int = N_CRATES
) -> int | None:
    """Single scope resolver used at acceptance and by legacy payload readers."""
    resolved = ask_crate_mentioned(text, n_crates) or followup_crate_mentioned(text, n_crates)
    if structured is not None and resolved is not None and structured != resolved:
        raise ValueError("conflicting_assistant_scope")
    return structured if structured is not None else resolved


def is_intel_ask(text: str, crate: int | None) -> bool:
    """True when the text is an intel ask about ``crate`` — a crate reference
    plus ask/question wording, or a bare crate follow-up (BA-52) — in
    ``nl_only`` this routes to a card share instead of a social redirect.
    First-person possession statements ("i have crate 3") are not asks
    (BA-64)."""
    if crate is None:
        return False
    ask_words = _NL_INTEL_ASK.search(_FIRST_PERSON_POSSESS.sub(" ", text))
    return bool(ask_words or _bare_ask_match(text))


# ---------------- multi-crate fan-out (2026-10-10 revision) --------------------------------
#
# Typed asks were lost to the classifier whenever the message named more than
# one crate ("where is crate 3, 4 and 6?") or named a crate without the
# ``crate`` keyword ("7 sawmill?"): ``ask_crate_mentioned`` resolved a single
# crate — or none — and the message fell through to social generation, which
# answered with a polite acknowledgement that hid the miss. The helpers below
# recover every crate the text references and decide when the whole set fans
# out to one card share per crate (capped so a pasted list cannot flood the
# chat).

_BARE_DIGIT = re.compile(r"\b([1-9]|1[0-2])\b")
FANOUT_CAP = 4

# The ask-marker set for multi-crate messages and bare-shape asks is narrower
# than ``_NL_INTEL_ASK``: possession verbs ("have", "got") stay out so "i don't
# have just 2, 5, 6, 7" never fans a holdings statement into four requests.
# A bare "?" counts — "3 cannery. 4 blackwell, 6 ridgeline, 7 sawmill?" is the
# observed real message that produced only acknowledgements.
_STRONG_INTEL_ASK = re.compile(
    r"\b(where|what|which|whats|what's|anything|any|newer|new|recent|latest|"
    r"info|intel|updates?|news|send|share|pass|check|tell|find|track|hear)\b|\?",
    re.IGNORECASE,
)


def crates_referenced(text: str, n_crates: int = N_CRATES) -> tuple[int, ...]:
    """Every crate the text names, in mention order: explicit ``crate N`` /
    ``#N`` references plus bare digits in range. Clock text ("5:30 pm") and
    first-person possession clauses ("i have 3") are stripped first — a bare
    digit inside an explicit ``crate N`` match is not counted twice."""
    scan = _CLOCK_PM.sub(" ", _FIRST_PERSON_POSSESS.sub(" ", text))
    hits: list[tuple[int, int]] = []
    spans = [m.span() for m in _CRATE_MENTION.finditer(scan)]
    for m in _CRATE_MENTION.finditer(scan):
        hits.append((m.start(), _crate_number(m.group(1) or m.group(2))))
    for m in _BARE_DIGIT.finditer(scan):
        if any(a <= m.start() < b for a, b in spans):
            continue
        hits.append((m.start(), int(m.group(1))))
    out: list[int] = []
    for _, n in sorted(hits):
        if n not in out and 1 <= n <= n_crates:
            out.append(n)
    return tuple(out)


def wants_intel_ask(text: str, crates: tuple[int, ...]) -> bool:
    """Whether a message that names ``crates`` is an intel ask to fan out over.
    A single crate keeps the established ``is_intel_ask`` semantics plus a
    bare "?" (so "7 sawmill?" asks); multi-crate messages need a strong ask
    marker — never bare possession verbs — so holdings statements stay
    social."""
    if not crates:
        return False
    stripped = _FIRST_PERSON_POSSESS.sub(" ", text)
    if len(crates) == 1 and is_intel_ask(text, crates[0]):
        return True
    return bool(_STRONG_INTEL_ASK.search(stripped) or _bare_ask_match(text))


def unresolved_intel_ask(text: str, n_crates: int = N_CRATES) -> bool:
    """An intel-flavoured message naming no crate ("any updates?", "did you
    hear anything?", "any news?") — it cannot produce a card share, so it
    routes to the deterministic ``asks_intel_nl`` "name the crate" invite
    instead of generation: the reply invites a single-crate ask rather than
    absorbing the message with an acknowledgement. ``_CONCRETE_ASK`` joins the
    ``_ASKS`` bucket because its nouns (news, intel, info, location) and verbs
    (share, send, pass, check) are intel-shaped even where the legacy word set
    is silent; a bare "?" alone does not count."""
    if crates_referenced(text, n_crates):
        return False
    # Possession clauses are statements, not asks — strip before scanning so
    # "i have crate 3" can never read as intel-seeking.
    stripped = _FIRST_PERSON_POSSESS.sub(" ", text).strip()
    if not stripped:
        return False
    if _CONCRETE_ASK.search(stripped):
        return True
    when = _fallback_when(stripped.lower(), nl_only=True, has_nl_pool=True)
    return when == "asks_intel_nl"


# "What are you working on?" — an assignment ask grounds a truthful per-seat
# statement rendered from the seat's own assignments (O-1).
_ASSIGNMENT_ASK = re.compile(
    r"\bwhat (?:crates?|are you|you (?:on|working|covering|assigned|got|have))|"
    r"\bwhich crates?\b|\b(?:your|y'?all'?s)\s+(?:crates?|assignments?)\b|"
    r"\bworking on\b|\bcovering\b|\bassigned\b",
    re.IGNORECASE,
)


def is_assignment_ask(text: str, n_crates: int = N_CRATES) -> bool:
    return bool(_ASSIGNMENT_ASK.search(text)) and crate_mentioned(text, n_crates) is None


# ---------------- automation-probe routing (O-3, 2026-10-03) ------------------------------
#
# "Are you a bot / real / human?" gets a fixed truthful line, never model text —
# the highest-stakes utterance in the paradigm must be identical every time.
# The classifier anchors on an interrogative or assertion addressed at the
# agent(s); "are you sure the info is real" does not fire.

# BA-50: bare "real" counts as an identity word only at the end of the message —
# "are you real?" probes, "are you real quick" does not. Plural/phrasal subjects
# ("the two of you", "you folks") and "actual(ly)"/"genuinely" modifiers probe too.
_PROBE_IDENTITY = (
    r"(?:bots?|robots?|chatbots?|ai\b|ais\b|humans?|human\s+beings?|flesh\s+and\s+blood|"
    r"real\s+(?:people|persons?|humans?|ones|typists?|deal)|peoples?|persons?|"
    r"automated|machines?|computers?|programs?|scripts?|chatgpt|gpt-?\w*|llms?|"
    r"language\s+models?|gemma|real(?=\s*[?.!]*\s*$))"
)
# O-11: third-person probes name a seat ("is pm a robot"), a pronoun standing
# for a teammate ("are they real", "is he human"), or a relational noun ("is
# your teammate a bot") — the disclosure belongs to the probed-about seat.
_PROBE_THIRD = (
    r"(?:jt|pm|he|she|they|them|him|her|either of (?:you|them)|both of (?:you|them)|"
    r"the other (?:guy|one|teammate|person)|"
    r"your (?:partner|teammate|colleague|buddy|coworker|friend)s?|"
    r"these (?:guys|people|folks)|those (?:guys|people|folks))"
)
_PROBE = re.compile(
    r"\b(?:are|r)\s+(?:you|u|ya|you (?:two|guys|both|all|folks)|"
    r"the (?:two|three|both) of you|either of you|both of you|y['\u2019]?all)\s+"
    r"(?:a\s+|an\s+|actually\s+|actual\s+|really\s+|genuin(?:e|ely)\s+|just\s+|even\s+|"
    r"for real\s+)*" + _PROBE_IDENTITY + r"\b|"
    r"\byou(?:['\u2019]re| are)\s+(?:a\s+|an\s+)?(?:bots?|robots?|ai\b|machines?|automated|"
    r"not\s+(?:a\s+)?(?:human|person|real))\b|"
    r"\byou\s+(?:(?:folks|guys|two|both|all)\s+)?(?:a\s+)?(?:bots?|robots?|humans?|ai\b|real)\s*\?|"
    r"\byou\s+for\s+real\b|"
    r"\b(?:is|are)\s+(?:this|these|everyone|everybody|all of you|the team|it)\s+"
    r"(?:here\s+|there\s+)?"
    r"(?:(?:a\s+|an\s+)?(?:bots?|robots?|ai\b|ais\b|automated|humans?|chatbots?|chatgpt|"
    r"gpt-?\w*|llms?|language\s+models?)\b|real(?:\s+(?:people|persons?|humans?|ones))?\s*[?.!]?\s*$)|"
    r"\b(?:is|are|was|were)\s+" + _PROBE_THIRD + r"\s+"
    r"(?:a\s+|an\s+|actually\s+|actual\s+|really\s+|just\s+|even\s+)*" + _PROBE_IDENTITY + r"\b|"
    r"\b(?:who|what)\s+(?:made|built|created|programmed|coded|wrote|designed|developed|"
    r"runs?|controls?|operates?|owns?|trained)\s+(?:you|u|ya|jt|pm|them|this|these)\b|"
    r"\b(?:if|prove|show)\b[\w\s'\u2019,.?!]{0,50}?\b(?:you['\u2019]?re|you are|youre|u r)\s+"
    r"(?:a\s+|an\s+)?(?:humans?|real|alive|persons?|peoples?|a\s+person|"
    r"not\s+(?:a\s+|an\s+)?(?:bots?|robots?|ai\b|machines?|automated))|"
    r"\b(?:u|you)\s+a\s+(?:bots?|robots?|ai\b|machines?)\b|"
    r"\byou\s+(?:guys|two|both|all)\s+(?:bots?|robots?|humans?|ai\b|ais\b)\b|"
    r"\b(?:bots?|ai|real people|real humans|a person|a human)\s*\?\s*$",
    re.IGNORECASE,
)
# A seat initial appearing in the third-person probe slot ("is pm a robot") —
# the probed-about seat, not the addressee.
_PROBE_SEAT_REF = re.compile(
    r"\b(?:is|are|was|were)\s+(jt|pm)\s+"
    r"(?:a\s+|an\s+|actually\s+|actual\s+|really\s+|just\s+|even\s+)*" + _PROBE_IDENTITY,
    re.IGNORECASE,
)
# Tokens that make a message probe-adjacent without classifying — recorded as
# near-miss flags on the message event for adversarial review (O-12).
_PROBE_NEAR_MISS = re.compile(
    r"\b(bots?|robots?|chatbots?|chatgpt|gpt-?\w*|llms?|language\s+models?|gemma|"
    r"automated|a\.?i\.?|humans?|real|alive|person|people|instructions?|"
    r"programmed|beep|boop)\b",
    re.IGNORECASE,
)


def is_automation_probe(text: str) -> bool:
    """True when the participant asks/asserts whether a teammate is human or
    automated — routes to the fixed disclosure line instead of generation."""
    return bool(_PROBE.search(text.strip()))


# BA-65/66: probe subjects that name seats indirectly — a singular relational
# noun ("is your teammate a bot?", "the other guy"), a plural/collective
# subject ("are your teammates bots", "these guys", "either of them"), or a
# coordination ("are you and pm bots?").
_PROBE_RELATIONAL_SING = re.compile(
    r"\b(?:is|are|was|were)\s+(?:your (?:partner|teammate|colleague|buddy|coworker|"
    r"friend)|the other (?:guy|one|teammate|person))\b",
    re.IGNORECASE,
)
_PROBE_COLLECTIVE_SUBJ = re.compile(
    r"\b(?:is|are|was|were)\s+(?:your (?:partners|teammates|colleagues|buddies|"
    r"coworkers|friends)|these (?:guys|people|folks)|those (?:guys|people|folks)|"
    r"either of (?:you|them)|both of (?:you|them))\b|"
    r"\byou\s+and\s+(?:jt|pm)\b|\b(?:jt|pm)\s+and\s+you\b",
    re.IGNORECASE,
)


def probed_seats(text: str, default: frozenset[Seat]) -> frozenset[Seat]:
    """Which seats a probe is *about* (O-11): a third-person named seat
    discloses for itself rather than the addressee answering for it — "jt, is pm
    a bot?" is PM's disclosure to make. A singular relational subject resolves
    against a uniquely addressed seat (BA-65: "jt, is your teammate a bot?" is
    PM's); coordinated and collective subjects are about both seats (BA-66).
    You-addressed probes keep the addressed seats."""
    stripped = _CLOCK_PM.sub(" ", text)
    if _PROBE_COLLECTIVE_SUBJ.search(stripped):
        return frozenset(("JT", "PM"))
    refs = {cast(Seat, m.group(1).upper()) for m in _PROBE_SEAT_REF.finditer(stripped)}
    if refs:
        return frozenset(refs)
    if _PROBE_RELATIONAL_SING.search(stripped) and len(default) == 1:
        (seat,) = default
        return frozenset({"PM" if seat == "JT" else "JT"})
    return default


def probe_near_miss(text: str) -> bool:
    """Probe-adjacent without classifying (O-12): carries identity/automation
    tokens or interrogation vocabulary but no firing pattern — flagged on the
    message event so adversarial review can inspect them without guessing."""
    return not is_automation_probe(text) and bool(_PROBE_NEAR_MISS.search(text))


# ---------------- grounded teammate statements (O-1) -------------------------------------
#
# Deterministic lines rendered from a seat's own holdings/assignments — truthful
# by construction, so they bypass the text gate the same way record shares do.
# Used when a typed ask reaches a seat with nothing to share, and for
# assignment asks; never for locations (those stay inside the record card).

GROUNDED_STATEMENT_VERSION = "grounded_statement_v1"


def grounded_no_holdings(seat: str, crate: int) -> str:
    """ "nothing on crate {n} on my end" — truthful when the seat holds no records
    for the crate."""
    return seat_style(seat, f"nothing on crate {crate} on my end")


# SQ-9: each seat states its assignment in its own frame — identical list order but
# different wording, so the two seats' answers can't read copy-pasted (AB-16).
_ASSIGNMENT_FRAMES = {
    "JT": "i'm on crates {listed}",
    "PM": "my crates are {listed}",
}
# Re-asking gets a "still" variant — a human repeats the fact, not the sentence.
_ASSIGNMENT_REPEAT_FRAMES = {
    "JT": "still on crates {listed}",
    "PM": "same as before — crates {listed}",
}
_ASSIGNMENT_PREFIXES = ("i'm on crates", "still on crates", "my crates are", "same as before")


def grounded_assignment(seat: str, crates: list[int], *, repeat: bool = False) -> str:
    """ "i'm on crates 1, 2 and 4" — the seat's own assigned crates, in a per-seat
    frame; `repeat` softens a re-ask ("still on crates…")."""
    ordered = sorted(crates)
    if len(ordered) > 1:
        listed = ", ".join(str(c) for c in ordered[:-1]) + f" and {ordered[-1]}"
    else:
        listed = str(ordered[0])
    frames = _ASSIGNMENT_REPEAT_FRAMES if repeat else _ASSIGNMENT_FRAMES
    frame = frames.get(seat, "i'm on crates {listed}" if not repeat else "still on crates {listed}")
    return seat_style(seat, frame.format(listed=listed))


def assignment_answers_delivered(conn: Connection, session_id: uuid.UUID, seat: str) -> int:
    """Committed team messages plus in-flight grounded-assignment jobs from `seat`
    — used to pick the repeat frame on re-asks. Rapid-fire asks can all schedule
    before the first reply commits, so pending deliver jobs count too."""
    committed = int(
        conn.execute(
            select(func.count())
            .select_from(s.message)
            .where(s.message.c.session_id == session_id)
            .where(s.message.c.actor == seat)
            .where(s.message.c.channel == "team")
            .where(s.message.c.committed_seq.is_not(None))
            .where(or_(*[s.message.c.body.ilike(f"{p}%") for p in _ASSIGNMENT_PREFIXES]))
        ).scalar_one()
    )
    pending = int(
        conn.execute(
            select(func.count())
            .select_from(s.scheduled_job)
            .where(s.scheduled_job.c.session_id == session_id)
            .where(s.scheduled_job.c.status.in_(("pending", "claimed")))
            .where(s.scheduled_job.c.payload["grounded"].astext == "assignment")
            .where(s.scheduled_job.c.payload["seat"].astext == seat)
        ).scalar_one()
    )
    return committed + pending


def identity_line(
    conn: Connection,
    streams: RandomStreams,
    bound: BoundManifest,
    seat: Seat,
    session_id: uuid.UUID,
) -> str:
    """The fixed disclosure for automation probes (O-3): the pinned bank's
    ``identity_probe`` pool when present (versioned participant copy), else the
    code default. Always truthful, always deterministic — never model text."""
    stored = bound.artifacts.get("teammates.fallback_bank")
    if stored is not None and isinstance(stored.artifact, FallbackBankArtifact):
        for rule in stored.artifact.body.rules:
            if rule.when == "identity_probe" and rule.lines:
                # BA-59/O-17: disclosure deliveries never write
                # ``voice_line_committed``, so a voice-kind ordinal never
                # advances — use the probe ordinal instead (the event is
                # appended after the draw, so both seats of one broadcast
                # share ``n``). The shared seat-agnostic draw + per-seat
                # offset then guarantees the two seats cannot echo each
                # other while the pool has ≥2 lines, and successive probes
                # get a fresh pair rather than one frozen line each.
                n = conn.execute(
                    select(func.count())
                    .select_from(s.event)
                    .where(
                        s.event.c.session_id == session_id,
                        s.event.c.type == "automation_probe",
                    )
                ).scalar_one()
                raw = int(streams.uniform("jobs", "voice", "identity", n) * len(rule.lines))
                idx = (raw + (1 if seat == "PM" else 0)) % len(rule.lines)
                return seat_style(seat, rule.lines[idx])
    return seat_style(seat, "i'm an automated account, not a person")


# SQ-8: item vocabulary → crate, over the fixed ``CRATE_CONTENTS`` world
# structure. "which crates are the laptops in?" resolves crate 1 deterministically —
# no model call, and the answer names crate→item only (never locations, so the
# strict ``answer_key_like`` bound on generated text is untouched).
_ITEM_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\blaptops?\b", re.IGNORECASE), 1),
    (re.compile(r"\bsmart[\s-]?watch(?:es)?\b", re.IGNORECASE), 2),
    (re.compile(r"\b(?:gaming\s+)?consoles?\b", re.IGNORECASE), 3),
    (re.compile(r"\bt\.?v\.?s?\b|\btelevisions?\b", re.IGNORECASE), 4),
    (re.compile(r"\bheadphones?\b|\bearbuds?\b|\bearphones?\b", re.IGNORECASE), 5),
    (re.compile(r"\bcameras?\b", re.IGNORECASE), 6),
    (re.compile(r"\btablets?\b", re.IGNORECASE), 7),
    (re.compile(r"\b(?:power\s+)?tools?\b", re.IGNORECASE), 8),
    (re.compile(r"\bsneakers?\b|\btrainers?\b", re.IGNORECASE), 9),
    (re.compile(r"\bdrones?\b|\bquadcopters?\b", re.IGNORECASE), 10),
    (re.compile(r"\be-?bikes?\b|\belectric\s+bikes?\b", re.IGNORECASE), 11),
    (re.compile(r"\bVR\s?headsets?\b|\bvirtual\s+reality\b", re.IGNORECASE), 12),
)

_CONTENTS_TERMS = re.compile(
    r"\bcrates?\b|\bstolen\b|\bobjects?\b|\bitems?\b|\bcontents?\b|\bgoods\b|\bmerchandise\b",
    re.IGNORECASE,
)


def ask_items_mentioned(text: str, n_crates: int = N_CRATES) -> list[int]:
    """The crates whose *contents* a question names, in mention order (SQ-8)."""
    hits: list[tuple[int, int]] = []
    for pattern, crate in _ITEM_PATTERNS:
        if crate > n_crates:
            continue
        m = pattern.search(text)
        if m is not None:
            hits.append((m.start(), crate))
    return [crate for _, crate in sorted(hits)]


def asks_crate_contents(text: str) -> bool:
    """A crate/item question with no crate number and no item vocabulary —
    "what stolen objects are in which crates?" — gets the full crate→item map."""
    return bool(_CONTENTS_TERMS.search(text))


def is_contents_question(text: str, n_crates: int = N_CRATES) -> bool:
    """BA-92: a text that resolves deterministically *before* model dispatch —
    item-named or broad contents questions with no crate number, plus numbered
    asks outside the world's crates (an honest boundary answer, never model
    text). Callers check the structured `crate` field separately; this is the
    text-side test, so a valid-but-partial model reply can't displace the
    deterministic answer and its provenance."""
    return ask_crate_mentioned(text, n_crates) is None and (
        crate_number_out_of_range(text, n_crates) is not None
        or bool(ask_items_mentioned(text, n_crates))
        or asks_crate_contents(text)
    )


def assistant_contents_summary(
    conn: Connection,
    session_id: uuid.UUID,
    crates: list[int],
    *,
    record_ids: set[str] | None = None,
) -> str:
    """SQ-8 deterministic contents→crate answer: which crate holds each named item
    (fixed world structure, no location claims — the answer-key bound is about
    locations). One resolved crate also gets the usual per-crate file summary."""
    if len(crates) == 1:
        crate = crates[0]
        item = CRATE_CONTENTS[crate - 1]
        return (
            f"The {item} are in Crate {crate}. "
            f"{assistant_file_summary(conn, session_id, crate, record_ids=record_ids)}"
        )
    lines = "; ".join(f"Crate {c} — {CRATE_CONTENTS[c - 1]}" for c in crates)
    return f"{lines}. Ask me about one crate for what's in your file."


def assistant_file_summary(
    conn: Connection,
    session_id: uuid.UUID,
    crate: int | None,
    *,
    record_ids: set[str] | None = None,
) -> str:
    """The demo's ``analyzeCrate`` deterministic assistant fallback
    (``deterministic_file_summary``): summarise the participant-file entries for the crate,
    oldest to newest, then name the most recent *named location* in the file — facts from
    rows only, never world truth. ``record_ids`` pins the summary to the accept-time
    knowledge snapshot's authorized set (BA-93) — later file arrivals stay invisible."""
    scope = s.record.c.record_id.in_(record_ids) if record_ids is not None else literal(True)
    file_rows = conn.execute(
        select(s.record.c.record_id, s.record.c.stamp, s.record.c.body)
        .select_from(
            s.file_membership.join(
                s.record,
                (s.record.c.session_id == s.file_membership.c.session_id)
                & (s.record.c.record_id == s.file_membership.c.record_id),
            )
        )
        .where(
            s.file_membership.c.session_id == session_id,
            s.record.c.crate == crate if crate is not None else literal(True),
            scope,
        )
        .order_by(s.record.c.timeline_ordinal)
    ).all()
    if crate is None or not file_rows:
        return (
            "Ask me about a crate by number, or ask which crate holds an item "
            "(like 'which crate has the laptops?')."
        )
    plural = "y" if len(file_rows) == 1 else "ies"
    header = f"Crate {crate}, {len(file_rows)} entr{plural} in your file, oldest to newest."
    bullets = "; ".join(f"{r.stamp}: {r.body}" for r in file_rows)
    named = conn.execute(
        select(s.record.c.stamp, s.record.c.location_id)
        .select_from(
            s.file_membership.join(
                s.record,
                (s.record.c.session_id == s.file_membership.c.session_id)
                & (s.record.c.record_id == s.file_membership.c.record_id),
            )
        )
        .where(
            s.file_membership.c.session_id == session_id,
            s.record.c.crate == crate,
            s.record.c.location_id.is_not(None),
            s.record.c.event_type.in_(("arrival", "still_at")),
            scope,
        )
        .order_by(s.record.c.timeline_ordinal.desc())
        .limit(1)
    ).first()
    tail = (
        f" Most recent location named in your file: {named.location_id} ({named.stamp})."
        if named is not None
        else ""
    )
    latest = conn.execute(
        select(s.record.c.event_type, s.record.c.location_id, s.record.c.toward)
        .where(
            s.record.c.session_id == session_id,
            s.record.c.record_id.in_([r.record_id for r in file_rows]),
        )
        .order_by(s.record.c.timeline_ordinal.desc())
        .limit(1)
    ).first()
    if latest is not None and latest.event_type in ("departure", "transit"):
        movement = f"left {latest.location_id}"
        if latest.event_type == "transit":
            movement += f" heading toward {latest.toward}"
        tail += f" Later evidence says it {movement}. Current location is unconfirmed."
    return f"{header} {bullets}.{tail}"


# ---------------- committed delivery -----------------------------------------------------


def deliver_message(
    ctx: TxContext,
    *,
    actor: str,
    channel: str,
    body: str,
    crate: int | None = None,
    record_id: str | None = None,
    request_id: uuid.UUID | None = None,
    parent_message_id: uuid.UUID | None = None,
    message_id: uuid.UUID | None = None,
    scheduled_for_active_ms: int | None = None,
    voice_kind: str | None = None,
    voice_draw: VoiceDraw | None = None,
    active_ms: int | None = None,
    repeat_substituted: bool = False,
    repeat_substituted_reason: str | None = None,
) -> tuple[uuid.UUID, EventRef]:
    """Commit one teammate/assistant/system message: the durable ``message`` row (status
    ``delivered``), a ``voice_line_committed`` bookkeeping event when the line consumed a
    voice pool slot, and the participant-visible ``message_delivered`` event."""
    # O-17: whether the outgoing teammate line repeats a line already delivered
    # in this session — the authoritative pool-wrap/exhaustion signal, computed
    # under the session lock at commit.
    delivered_prior = delivered_teammate_lines(ctx.conn, ctx.session_id)
    repeated_line = actor in _MEMORY_SEATS and is_repeat(body, delivered_prior)
    near_repeat_line = (
        actor in _MEMORY_SEATS and not repeated_line and is_near_repeat(body, delivered_prior)
    )
    mid = message_id or uuid.uuid4()
    seq = ctx.append_event(
        "message_delivered",
        actor=actor,
        active_ms=active_ms,
        payload={
            "message_id": str(mid),
            "actor": actor,
            "channel": channel,
            "body": body,
            "crate": crate,
            "record_id": record_id,
            "request_id": None if request_id is None else str(request_id),
            "scheduled_for_active_ms": scheduled_for_active_ms,
            # AI-1 delivery-time recheck: the shown text is a deterministic-script
            # substitute because the committed text repeated an earlier line.
            # ``repeat_substituted_reason`` distinguishes "exact" from "near"
            # (near-paraphrase) triggers for the audit record.
            "repeat_substituted": repeat_substituted or None,
            "repeat_substituted_reason": repeat_substituted_reason,
            "repeated_line": repeated_line or None,
            # Near-duplicate audit: the outgoing line shares most vocabulary with an
            # already-delivered teammate line without matching it exactly — shipped
            # near-paraphrases (exempt verbatim lines, or anything the substitute
            # path let through). Exact repeats flag `repeated_line` instead.
            "near_repeat_line": near_repeat_line or None,
        },
    )
    ctx.conn.execute(
        insert(s.message).values(
            session_id=ctx.session_id,
            message_id=mid,
            actor=actor,
            channel=channel,
            parent_message_id=parent_message_id,
            crate=crate,
            record_id=record_id,
            request_id=request_id,
            body=body,
            status="delivered",
            accepted_seq=seq.seq,
            scheduled_for_active_ms=scheduled_for_active_ms,
            committed_seq=seq.seq,
            created_at=ctx.now,
            delivered_at=ctx.now,
        )
    )
    if voice_kind is not None:
        ctx.append_event(
            "voice_line_committed",
            actor=actor,
            payload={
                "voice_kind": voice_kind,
                "message_id": str(mid),
                # O-17 pool bookkeeping: the draw position and whether it went
                # past the pool's end (wrap → earlier lines start repeating).
                "draw_ordinal": None if voice_draw is None else voice_draw.draw_ordinal,
                "pool_wrapped": None if voice_draw is None else voice_draw.pool_wrapped,
            },
        )
    if actor in _MEMORY_SEATS:
        note_delivery(ctx.conn, ctx.session_id, actor, seq.seq)
    return mid, seq


# ---------------- PE exposure surfaces and receipts (INV-0904; spec §5.3) -------------------

_PE_SURFACE_FIELD = {
    "pe_intro": "intro",
    "pe_callout": "callout",
    "pe_strip": "strip",
    "pe_system_message": "system_message",
    "peer_intro": "peer_intro",
}


def pe_exposure_stored(bound: BoundManifest, arm: str | None) -> StoredArtifact | None:
    """The bound exposure-copy artifact for the session's arm — resolved through the
    canonical ``resolve_policy`` path (BA-16: manipulation surfaces must not be read
    off the manifest directly). None for TP manifests or unassigned arms."""
    if arm is None:
        return None
    policy = resolve_policy(bound, arm)
    if policy.manipulation.get("kind") != "evaluation_exposure":
        return None
    ref = policy.manipulation.get("exposure_copy")
    if not ref:
        return None
    key, sha = ref
    stored = next((a for a in bound.artifacts.values() if a.key == key), None)
    if stored is None or stored.sha256 != sha:
        return None
    if not isinstance(stored.artifact, ExposureCopyArtifact):
        return None
    return stored


def pe_surface_texts(bound: BoundManifest, arm: str | None) -> dict[str, str]:
    """Declared-surface name -> copy actually served for the arm. Empty surfaces (the
    control arm's deliberately blank strip and system message) are omitted entirely —
    the client renders nothing and no receipt exists."""
    stored = pe_exposure_stored(bound, arm)
    if stored is None or not isinstance(stored.artifact, ExposureCopyArtifact):
        return {}
    body = stored.artifact.body
    assert arm is not None  # pe_exposure_stored returns None for unassigned arms
    declared = set(resolve_policy(bound, arm).controls.get("evaluation_surfaces") or [])
    texts = {
        "intro": body.intro,
        "callout": body.callout,
        "strip": body.strip,
        "system_message": body.system_message,
        "peer_intro": body.peer_intro,
    }
    return {surface: text for surface, text in texts.items() if surface in declared and text}


def commit_pe_exposures(ctx: TxContext, bound: BoundManifest, kinds: Iterable[str]) -> list[str]:
    """Commit one ``manipulation_exposure`` receipt (status ``committed``, with an
    internal ``exposure_committed`` event) per PE surface being served right now —
    the durable record that the assigned copy was presented (spec §8.1 receipts)."""
    texts = pe_surface_texts(bound, ctx.session["arm"])
    stored = pe_exposure_stored(bound, ctx.session["arm"])
    if stored is None:
        return []
    artifact_id, version = stored.key.rsplit("@v", 1)
    committed: list[str] = []
    for kind in kinds:
        if _PE_SURFACE_FIELD[kind] not in texts:
            continue
        seq = ctx.append_event("exposure_committed", actor="system", payload={"surface": kind})
        ctx.conn.execute(
            insert(s.manipulation_exposure).values(
                session_id=ctx.session_id,
                exposure_id=uuid.uuid4(),
                kind=kind,
                artifact_id=artifact_id,
                artifact_version=int(version),
                committed_seq=seq.seq,
                status="committed",
            )
        )
        committed.append(kind)
    return committed


# ---------------- bounded agent memory (INV-0704; spec §8.1) ---------------------------------

# ``agent_state`` is the auditable, bounded mirror of what each agent seat owes and has done:
# ``outstanding_requests`` = open request episodes the seat is a party to; ``commitments`` =
# how those episodes resolved; ``delivered_high_water_seq`` = the highest committed delivery
# seq for the seat, which makes "did I already reply?" decidable after a worker restart
# instead of relying on in-process memory.
_MEMORY_SEATS = frozenset({"JT", "PM", "assistant"})
_COMMITMENT_CAP = 64
_OUTSTANDING_CAP = 32


def _agent_row(
    conn: Connection, session_id: uuid.UUID, seat: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    row = (
        conn.execute(
            select(
                s.agent_state.c.commitments,
                s.agent_state.c.outstanding_requests,
                s.agent_state.c.delivered_high_water_seq,
            )
            .where(
                s.agent_state.c.session_id == session_id,
                s.agent_state.c.seat == seat,
            )
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return [], [], 0
    return (
        [dict(c) for c in row["commitments"]],
        [dict(o) for o in row["outstanding_requests"]],
        int(row["delivered_high_water_seq"]),
    )


def _write_agent(
    conn: Connection,
    session_id: uuid.UUID,
    seat: str,
    *,
    commitments: list[dict[str, Any]],
    outstanding: list[dict[str, Any]],
    high_water: int,
) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = (
        pg_insert(s.agent_state)
        .values(
            session_id=session_id,
            seat=seat,
            commitments=commitments[-_COMMITMENT_CAP:],
            outstanding_requests=outstanding[-_OUTSTANDING_CAP:],
            delivered_high_water_seq=high_water,
        )
        .on_conflict_do_update(
            index_elements=["session_id", "seat"],
            set_={
                "commitments": commitments[-_COMMITMENT_CAP:],
                "outstanding_requests": outstanding[-_OUTSTANDING_CAP:],
                "delivered_high_water_seq": high_water,
                "updated_at": func.now(),
            },
        )
    )
    conn.execute(stmt)


def note_request(
    conn: Connection,
    session_id: uuid.UUID,
    *,
    request_id: uuid.UUID,
    requester: str,
    responder: str,
    crate: int | None,
    at_active_ms: int,
) -> None:
    """Register a new request episode in every agent seat that is a party to it — the
    requester's pending ask and the responder's owed response. Idempotent on ``request_id``
    so a retried command can never double-count."""
    entry = {
        "request_id": str(request_id),
        "crate": crate,
        "at_active_ms": at_active_ms,
    }
    for seat, role in ((requester, "requester"), (responder, "responder")):
        if seat not in _MEMORY_SEATS:
            continue
        commitments, outstanding, hwm = _agent_row(conn, session_id, seat)
        if not any(o["request_id"] == entry["request_id"] for o in outstanding):
            outstanding.append({**entry, "role": role})
        _write_agent(
            conn,
            session_id,
            seat,
            commitments=commitments,
            outstanding=outstanding,
            high_water=hwm,
        )


def resolve_request(
    conn: Connection,
    session_id: uuid.UUID,
    *,
    request_id: uuid.UUID,
    resolution: str,
    seq: int,
    at_active_ms: int | None,
) -> None:
    """Move a request out of ``outstanding_requests`` into ``commitments`` for every agent
    seat tracking it. The resolution record carries the closing event seq so the ledger is
    joinable; a second call for the same request is a no-op (recovery-safe)."""
    rid = str(request_id)
    requester, responder = conn.execute(
        select(s.request.c.requester, s.request.c.responder).where(
            s.request.c.session_id == session_id,
            s.request.c.request_id == request_id,
        )
    ).one()
    for seat in (requester, responder):
        if seat not in _MEMORY_SEATS:
            continue
        commitments, outstanding, hwm = _agent_row(conn, session_id, seat)
        if any(c.get("request_id") == rid for c in commitments):
            continue
        outstanding = [o for o in outstanding if o["request_id"] != rid]
        commitments.append(
            {
                "request_id": rid,
                "resolution": resolution,
                "resolved_seq": seq,
                "at_active_ms": at_active_ms,
            }
        )
        _write_agent(
            conn,
            session_id,
            seat,
            commitments=commitments,
            outstanding=outstanding,
            high_water=hwm,
        )


def expire_open_episodes(
    conn: Connection,
    session_id: uuid.UUID,
    *,
    seq: int,
    at: datetime,
    at_active_ms: int | None,
    reason: str,
) -> int:
    """Close every still-open request episode at a freeze/terminal boundary
    (INV-0704 semantics, extended by AB-19): once the surface can never be answered
    again, pending episodes expire explicitly with a close reason rather than
    lingering as false "outstanding" state. Returns the number closed."""
    rows = conn.execute(
        select(s.request_episode.c.request_id).where(
            s.request_episode.c.session_id == session_id,
            s.request_episode.c.closed_at.is_(None),
        )
    ).all()
    for (rid,) in rows:
        conn.execute(
            update(s.request_episode)
            .where(
                s.request_episode.c.session_id == session_id,
                s.request_episode.c.request_id == rid,
            )
            .values(
                status="expired",
                closed_event_seq=seq,
                closed_at=at,
                close_reason=reason,
            )
        )
        resolve_request(
            conn,
            session_id,
            request_id=rid,
            resolution="expired",
            seq=seq,
            at_active_ms=at_active_ms,
        )
    return len(rows)


def note_delivery(conn: Connection, session_id: uuid.UUID, seat: str, seq: int) -> None:
    """Advance the seat's delivered high-water mark (monotonic; used to detect a delivery
    that already committed when a job is retried after restart)."""
    commitments, outstanding, hwm = _agent_row(conn, session_id, seat)
    if seq <= hwm:
        return
    _write_agent(
        conn,
        session_id,
        seat,
        commitments=commitments,
        outstanding=outstanding,
        high_water=seq,
    )


# ---------------- generated-output validation (INV-0703/1003/1004; spec §7.3/§7.7) ---------------

# The deterministic gate every model-produced teammate/assistant text must pass before it can
# be scheduled for delivery. A violation records ``model_call.validation`` and falls back to
# the pinned deterministic banks — generated text can never widen the visible world, leak the
# interface, or introduce off-policy urgency/evaluation/advice (spec §7.7).
#
# ``teammate_output_v2`` (WP10a) adds four injection/abuse classes on top of the v1 checks,
# which are unchanged: ``prompt_reveal`` (the output discusses/echoes instructions or
# prompt-extraction attempts), ``condition_leak`` (experiment arms/conditions/group
# assignment — INV-1004 condition-key probing), ``disrespect`` (insults; INV-1007), and
# ``record_ref_leak`` (teammate text citing internal record ids — for the assistant class
# citations are allowed and instead validated semantically by ``output_claims``).
#
# ``teammate_output_v3`` (UX round 3, AI-1) adds ``repeat_message`` for teammate classes:
# a multiword line identical to one already delivered in the session is rejected (the
# check needs session state, so the scheduler applies it — see ``is_repeat``).
#
# ``teammate_output_v4`` (post-E22 audit) makes ``repeat_message`` punctuation-insensitive
# ("dont …" ≡ "don't …") and adds the delivery-time recheck: a teammate text that became
# a repeat while still in flight is replaced by a still-unused deterministic-script line
# under the session lock, flagged ``repeat_substituted`` on the delivered event.
#
# ``teammate_output_v5`` (O-3, 2026-10-03) adds ``human_claim``: generated text may never
# assert or imply the agent is a person ("i'm a real person", "not a bot"). Direct probes
# are intercepted before generation and answered with the fixed disclosure line; this
# class covers unprovoked claims the probe router never sees.
#
# ``teammate_output_v6`` (BA-51, 2026-10-03) widens ``human_claim`` coverage to denials
# of automation ("i'm not automated", "we are not bots") and idioms ("flesh and blood",
# "we're real") — claims that previously passed the gate could falsely deny automation.
#
# ``teammate_output_v8`` (2026-10-04, lab walkthrough) adds ``near_repeat`` for teammate
# classes: a line sharing most of its distinct vocabulary (Dice ≥ ``NEAR_REPEAT_DICE``,
# ≥ ``NEAR_REPEAT_MIN_WORDS`` distinct words) with an already-delivered teammate line is
# rejected — cross-seat convergent near-paraphrases read as one scripted reply. Like
# ``repeat_message`` the check needs session state, so the scheduler applies it (see
# ``is_near_repeat``); the delivery-time recheck substitutes a bank line for text that
# became a near-repeat in flight, and shipped near-paraphrases flag
# ``near_repeat_line`` on ``message_delivered``.
VALIDATOR_VERSION = "teammate_output_v8"

_URGENCY = re.compile(
    r"\b(hurry|urgent|asap|right now|deadline|running out|no time|time left|"
    r"hurry up|faster|quick(?:ly)? before)\b",
    re.I,
)
_EVALUATION = re.compile(
    r"\b(good job|well done|nice work|you(?:'re| are) (?:wrong|mistaken|right|correct|"
    r"good|bad|slow|fast)|that'?s (?:wrong|incorrect|correct|right)|incorrect|"
    r"you should have|nice try|impressive|sloppy)\b",
    re.I,
)
_TASK_ADVICE = re.compile(
    r"\b(you should|you need to|you have to|you must|try to|make sure you|"
    r"focus on|prioriti[sz]e|answer (?:all|your)|double.?check your)\b",
    re.I,
)
_STALL_EXCUSE = re.compile(
    r"\b(one sec|one moment|hold on|hang on|give me a (?:sec|second|minute|moment)|"
    r"gimme a|brb|be right back|just a (?:sec|second|minute|moment)|"
    r"my (?:wifi|internet|connection)|lagging|loading(?!\s+dock))\b",
    re.I,
)
_INTERFACE_LEAK = re.compile(
    r"\b(ai\b|language model|chatbot|gpt|gemma|qwen|llm|this study|the study|"
    r"experiment|research|survey|prolific|algorithm|software|program|scripted|"
    r"interface|system message)\b",
    re.I,
)
_GREETING_START = re.compile(r"^\s*(hi|hello|hey|dear|yo)\b", re.I)
_VOICE_FORMAT = re.compile("[!:;\\u2014\\u2013]")
# Time-of-day colons ("6:00 PM") are quoted record data, not prose punctuation — the
# pinned prompts require timestamp wording, so a bare `:` ban would reject every
# compliant answer. Strip digit-colon-digit before the punctuation check.
_TIMESTAMP_COLON = re.compile(r"(?<=\d):(?=\d)")
# INV-1003/1004 (v2): the output must never engage with prompt-extraction attempts or
# describe its own instructions, and never leak experimental-condition vocabulary.
_PROMPT_REVEAL = re.compile(
    r"\b(system prompt|my (?:prompt|instructions|directives|rules)|"
    r"the (?:prompt|instructions|rules) (?:say|said|i)|"
    r"ignore (?:all |any |your |previous |prior )?(?:instructions|prompts?|rules)|"
    r"reveal (?:your|the|my) (?:prompt|instructions|rules))\b",
    re.I,
)
_CONDITION_LEAK = re.compile(
    r"\b(experiment condition|study arm|control group|treatment group|"
    r"(?:high|low)[ -]pressure condition|randomly assigned|"
    r"assigned (?:to )?(?:a|the) (?:condition|arm|group)|baseline condition)\b",
    re.I,
)
_DISRESPECT = re.compile(
    r"\b(stupid|idiot|dumb|moron|useless|pathetic|shut up|hate you|loser|"
    r"jerk|creep|incompetent)\b",
    re.I,
)
_RECORD_REF = re.compile(r"\bC\d{1,2}-\d{1,3}\b")
# O-3 (v5) + BA-51 (v6): no generated text may claim or imply the speaker is
# human — including by *denying* automation ("i'm not automated", "we are not
# bots") or by idiom ("flesh and blood"). The fixed disclosure lines ("i'm an
# automated account, not a person") affirm automation rather than denying it,
# so they still pass; they are produced outside generation anyway.
_HUMAN_CLAIM = re.compile(
    r"\b(i'?m|i am|im)\s+(?:a\s+|an\s+|actually\s+|really\s+|just\s+|totally\s+)*"
    r"(?:real\s+|actual\s+|living\s+)?(?:person|human|woman|man|guy|girl|people|being)\b|"
    r"\bi'?m\s+(?:real|human|alive|a real one)\b|"
    r"\b(?:not|ain't|aint)\s+(?:a|an)\s+(?:bot|robot|ai|machine|computer)\b|"
    r"\b(?:i|we)(?:'m|'?re|\s+am|\s+are)?\s+not\s+(?:a\s+|an\s+|any\s+)?"
    r"(?:bots?|robots?|ais?|automated|machines?|computers?|programs?|scripts?)\b|"
    r"\bflesh\s+and\s+blood\b|"
    r"\breal\s+person\s+(?:here|typing|talking)\b|"
    r"\bwe(?:'re|\s+are)\s+(?:both\s+)?(?:real\s+|actual\s+)?(?:people|humans|persons)\b|"
    r"\bwe(?:'re|\s+are)\s+(?:both\s+)?real\b|"
    r"\bwe(?:'re|\s+are)\s+(?:both\s+)?alive\b",
    re.IGNORECASE,
)
_PROMPT_BANNED_TOKENS = (
    "share control",
    "request button",
    "control",
    "interface",
    "system",
    "file",
)
_MAX_GENERATED_CHARS = 400


def hideouts_in(text: str) -> list[str]:
    """The demo's ``hideoutsIn``: which hideout names appear in the text (de-articled,
    case-insensitive substring match)."""
    from app.domain.world import HIDEOUTS

    t = text.lower()
    return [h for h in HIDEOUTS if re.sub(r"^the\s+", "", h.lower()) in t]


def validate_teammate_output(
    text: str, *, allowed_locations: frozenset[str] | None = None
) -> list[str]:
    """Audit one generated teammate text; returns the violation labels (empty = deliverable).

    ``allowed_locations`` is the set of location names legitimately inside the pinned
    snapshot — for the assistant class these are the participant-file locations; teammate
    classes pass ``None`` so *any* hideout name is a leak."""
    violations: list[str] = []
    named = set(hideouts_in(text))
    leaked = named if allowed_locations is None else (named - set(allowed_locations))
    if leaked:
        violations.append("hideout_leak")
    if _INTERFACE_LEAK.search(text):
        violations.append("interface_leak")
    if _PROMPT_REVEAL.search(text):
        violations.append("prompt_reveal")
    if _CONDITION_LEAK.search(text):
        violations.append("condition_leak")
    if _DISRESPECT.search(text):
        violations.append("disrespect")
    if _HUMAN_CLAIM.search(text):
        violations.append("human_claim")
    if allowed_locations is None and _RECORD_REF.search(text):
        # Teammate classes never cite internal record ids; the assistant's citations are
        # checked semantically against the pinned manifest by output_claims instead.
        violations.append("record_ref_leak")
    if _URGENCY.search(text):
        violations.append("urgency")
    if _EVALUATION.search(text):
        violations.append("evaluation")
    if _TASK_ADVICE.search(text):
        violations.append("task_advice")
    if _STALL_EXCUSE.search(text):
        violations.append("stall_excuse")
    if allowed_locations is None and (
        _GREETING_START.search(text) or _VOICE_FORMAT.search(_TIMESTAMP_COLON.sub("", text))
    ):
        violations.append("voice_format")
    for token in _PROMPT_BANNED_TOKENS:
        if len(re.findall(rf"\b{re.escape(token)}\b", text, re.I)) > 1:
            violations.append("banned_token_reuse")
            break
    if len(text) > (_MAX_GENERATED_CHARS if allowed_locations is None else 900):
        violations.append("length_overrun")
    return violations


def file_locations(
    conn: Connection, session_id: uuid.UUID, record_ids: list[str]
) -> frozenset[str]:
    """Location names legitimately visible inside a pinned snapshot's record set (used to
    bound the assistant's allowed references)."""
    if not record_ids:
        return frozenset()
    rows = conn.execute(
        select(s.record.c.location_id)
        .where(
            s.record.c.session_id == session_id,
            s.record.c.record_id.in_(record_ids),
            s.record.c.location_id.is_not(None),
        )
        .distinct()
    ).all()
    return frozenset(str(r[0]) for r in rows)
