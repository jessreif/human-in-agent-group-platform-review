"""O-1..O-4 decisions (INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_DECISIONS.md):
unit coverage for the deterministic routing, probe and grounded-statement
functions in ``app.services.engines``. Database-level behavior (broadcast
deliveries, auto-add, probe events) lives in tests/integration/test_o_decisions.py.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import ClassVar

import pytest

from app.domain.rng import RandomStreams
from app.domain.world import (
    _LATE_CUTOFF,
    _OWN_CUTS,
    _TEAM_CUTS,
    CRATE_CONTENTS,
    _build_timeline,
    build_world,
)
from app.services import engines

# ---------------- O-2: addressed-seat routing ---------------------------------------------


@pytest.mark.parametrize(
    ("text", "to", "want"),
    [
        ("do you have anything on crate 2?", None, {"JT", "PM"}),
        ("JT what about you?", None, {"JT"}),
        ("jt — anything newer on 3?", None, {"JT"}),
        ("pm, send me your latest", None, {"PM"}),
        ("thanks both!", None, {"JT", "PM"}),
        ("hey you two, what gives?", None, {"JT", "PM"}),
        ("either of you see crate 5?", None, {"JT", "PM"}),
        # Clock text must never read as addressing PM.
        ("see you at 6 pm", None, {"JT", "PM"}),
        ("meet back at 5:30pm", None, {"JT", "PM"}),
        # An explicit ``to`` field always wins over the text scan.
        ("anything?", "JT", {"JT"}),
        ("JT ignore this", "PM", {"PM"}),
        # Both initials named = broadcast.
        ("JT and PM, check in", None, {"JT", "PM"}),
    ],
)
def test_addressed_seats(text: str, to: str | None, want: set[str]) -> None:
    assert set(engines.addressed_seats(text, to)) == want


# ---------------- O-3: automation-probe routing -------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "are you a bot?",
        "are you guys real people?",
        "are you two bots",
        "you a robot?",
        "is this ai?",
        "are u human",
        "are either of you real?",
        "is everyone automated?",
        "bot?",
        "you're a bot",
        "are y'all real",
    ],
)
def test_automation_probe_fires(text: str) -> None:
    assert engines.is_automation_probe(text)


@pytest.mark.parametrize(
    "text",
    [
        "are you sure the info is real",
        "what do you have on crate 3",
        "is it a real hideout?",
        "the ai assistant answered",
        "how are you doing?",
        "send me crate 1 info",
    ],
)
def test_automation_probe_does_not_misfire(text: str) -> None:
    assert not engines.is_automation_probe(text)


def test_identity_line_falls_back_to_fixed_disclosure() -> None:
    # The code default is itself truthful — used only when the pinned bank has
    # no identity_probe pool.
    class _NoConn:
        pass

    class _NoBound:
        artifacts: ClassVar[dict] = {}

    line = engines.identity_line(_NoConn(), None, _NoBound(), "JT", uuid.uuid4())
    assert "automated" in line.lower() and "person" in line.lower()
    assert engines.validate_teammate_output(line) == []


def test_identity_line_broadcast_never_echoes(monkeypatch: pytest.MonkeyPatch) -> None:
    # BA-59 intent check: on a broadcast probe both seats draw with the same
    # ordinal — the shared-key + seat-offset scheme must never produce the same
    # disclosure line for the two seats while the pool has ≥2 lines.
    from types import SimpleNamespace

    from app.domain.manifests.artifacts import (
        FallbackBankArtifact,
        FallbackBankBody,
        FallbackRule,
    )
    from app.domain.manifests.store import StoredArtifact
    from app.domain.rng import RandomStreams

    bank = FallbackBankArtifact(
        schema_version="investigators.artifact/1",
        kind="fallback_bank",
        artifact_id="teammates.fallback_bank",
        version=1,
        status="approved",
        sources=({"ref": "O-3"},),
        body=FallbackBankBody(
            classifier="demo_keyword_v1",
            rules=(
                FallbackRule(
                    when="identity_probe",
                    lines=(
                        "i'm an automated account, not a person",
                        "i'm an automated teammate account",
                    ),
                ),
                FallbackRule(when="default", lines=("noted",)),
            ),
        ),
    )
    bound = SimpleNamespace(
        artifacts={"teammates.fallback_bank": StoredArtifact(artifact=bank, sha256="x", path="x")}
    )
    # The probe ordinal comes from automation_probe event count — stub a conn
    # reporting zero prior probes so both seats draw at the same ordinal.
    conn = SimpleNamespace(execute=lambda *a, **k: SimpleNamespace(scalar_one=lambda: 0))
    for seed_i in range(20):
        streams = RandomStreams(f"{seed_i:064x}")
        jt = engines.identity_line(conn, streams, bound, "JT", uuid.uuid4())  # type: ignore[arg-type]
        pm = engines.identity_line(conn, streams, bound, "PM", uuid.uuid4())  # type: ignore[arg-type]
        assert jt.lower() != pm.lower(), (seed_i, jt, pm)
        # …and the same-seed draw is deterministic (replay contract).
        again = RandomStreams(f"{seed_i:064x}")
        assert engines.identity_line(conn, again, bound, "JT", uuid.uuid4()) == jt  # type: ignore[arg-type]


# ---------------- O-3: outbound human-claim gate -------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "i'm a real person",
        "i am human",
        "i'm really a person",
        "not a bot",
        "i'm not a machine",
        "we're both real people",
        "we are humans",
        "real person here",
        "i'm alive",
    ],
)
def test_human_claim_blocked(text: str) -> None:
    assert "human_claim" in engines.validate_teammate_output(text)


@pytest.mark.parametrize(
    "text",
    [
        # The fixed disclosure itself must never trip the gate.
        "i'm an automated account, not a person",
        "i'm an automated teammate account",
        "doing ok thanks, working through mine. you?",
        "nothing on crate 5 on my end",
        "i'm on crates 1, 2 and 4",
    ],
)
def test_disclosure_and_grounded_lines_pass(text: str) -> None:
    assert engines.validate_teammate_output(text) == []


# ---------------- O-1: typed intel / assignment asks ---------------------------------------


@pytest.mark.parametrize(
    ("text", "crate", "want"),
    [
        ("do you have anything on crate 2?", 2, True),
        ("where is crate 4?", 4, True),
        ("send me crate 1 info", 1, True),
        ("any update on crate 7", 7, True),
        # Participant *giving* intel stays social — no ask marker.
        ("crate 3 is at the marina", 3, False),
        # No crate reference → not a crate ask.
        ("what have you found?", None, False),
    ],
)
def test_is_intel_ask(text: str, crate: int | None, want: bool) -> None:
    assert engines.crate_mentioned(text) == crate
    assert engines.is_intel_ask(text, crate) is want


@pytest.mark.parametrize(
    "text",
    [
        "what are you working on?",
        "which crates are you on?",
        "what crates do you have?",
        "what are you covering?",
        "what is your assignment?",
    ],
)
def test_assignment_ask_fires(text: str) -> None:
    assert engines.is_assignment_ask(text)


@pytest.mark.parametrize(
    "text",
    ["anything on crate 5?", "how are you?", "where is crate 2?"],
)
def test_assignment_ask_does_not_misfire(text: str) -> None:
    assert not engines.is_assignment_ask(text)


# ---------------- O-1: grounded statements -------------------------------------------------


def test_grounded_assignment_lists_sorted_crates() -> None:
    # SQ-9: each seat renders its assignment in its own frame (AB-16 collisions).
    assert engines.grounded_assignment("JT", [4, 1, 2]) == "i'm on crates 1, 2 and 4"
    assert engines.grounded_assignment("PM", [4, 1, 2]) == "My crates are 1, 2 and 4"
    assert engines.grounded_assignment("JT", [3]) == "i'm on crates 3"
    # Re-asks get the "still" frame so the seat doesn't echo itself verbatim.
    assert engines.grounded_assignment("JT", [4, 1, 2], repeat=True) == "still on crates 1, 2 and 4"
    assert (
        engines.grounded_assignment("PM", [4, 1, 2], repeat=True)
        == "Same as before — crates 1, 2 and 4"
    )


def test_grounded_no_holdings_is_seat_styled() -> None:
    assert engines.grounded_no_holdings("JT", 5) == "nothing on crate 5 on my end"
    assert engines.grounded_no_holdings("PM", 5) == "Nothing on crate 5 on my end"


def test_synthetic_unheld_crate_uses_grounded_no_holdings() -> None:
    """``investigators.world/demo_v1`` cannot leave a crate in 1..8 unheld.

    Timestamps are seed-independent. Own-file cutoffs leave 3 or 1 teammate
    records, team-file cutoffs leave 6 or 5, and an unassigned crate leaves
    12 non-late records; each is coined to exactly one of JT or PM. The
    neither-holds reply is still one grounded line and no card. Checked on a
    synthetic world: crate 4's teammate holdings are stripped, the timeline
    remains, and both seats' lines name that crate and no hideout.
    """
    seed = "03" * 32
    own = _build_timeline(RandomStreams(seed), 1, CRATE_CONTENTS[0], "own")
    team = _build_timeline(RandomStreams(seed), 2, CRATE_CONTENTS[1], "team")
    own_other = _build_timeline(RandomStreams("aa" * 32), 1, CRATE_CONTENTS[0], "own")
    assert [entry.ts for entry in own.entries] == [entry.ts for entry in own_other.entries]
    own_counts = [sum(1 for entry in own.entries if entry.ts > cut) for cut in _OWN_CUTS]
    team_counts = [sum(1 for entry in team.entries if entry.ts > cut) for cut in _TEAM_CUTS]
    late = [entry for entry in team.entries if entry.ts >= _LATE_CUTOFF][-2:]
    unassigned = sum(1 for entry in team.entries if entry not in late)
    assert own_counts == [3, 1], seed
    assert team_counts == [6, 5], seed
    assert unassigned == 12, seed

    world = build_world(RandomStreams(seed))
    by_crate: dict[int, set[str]] = {n: set() for n in range(1, 9)}
    for seat in ("JT", "PM"):
        for rid in world.holdings[seat]:
            by_crate[world.records[rid].crate].add(seat)
    if not all(by_crate[n] for n in range(1, 9)):
        pytest.fail(f"seed {seed} left a crate with no JT and no PM holding")
    flat = [rid for rids in world.holdings.values() for rid in rids]
    assert sorted(flat) == sorted(world.records)
    assert set(world.holdings["JT"]).isdisjoint(world.holdings["PM"])

    crate = 4
    synthetic = replace(
        world,
        holdings={
            holder: (
                tuple(rid for rid in rids if world.records[rid].crate != crate)
                if holder in ("JT", "PM")
                else rids
            )
            for holder, rids in world.holdings.items()
        },
    )
    held = [
        rid
        for holder in ("JT", "PM")
        for rid in synthetic.holdings[holder]
        if synthetic.records[rid].crate == crate
    ]
    assert held == [], f"seed {seed} synthetic crate {crate} still has teammate holdings"
    assert any(rec.crate == crate for rec in synthetic.records.values())
    assert engines.grounded_no_holdings("JT", crate) == "nothing on crate 4 on my end"
    assert engines.grounded_no_holdings("PM", crate) == "Nothing on crate 4 on my end"
    for holder in ("JT", "PM"):
        assert engines.hideouts_in(engines.grounded_no_holdings(holder, crate)) == []


# ---------------- SQ-8/BA-92: contents questions route deterministically -------


def test_is_contents_question_routes_before_the_model() -> None:
    """BA-92: item-named and broad contents questions resolve deterministically
    pre-dispatch — a validator-clean but partial model reply must not displace
    the map answer + deterministic_contents_summary provenance."""
    assert engines.is_contents_question("which crates have the laptops?")
    assert engines.is_contents_question("what's in each crate?")
    assert engines.is_contents_question("can you match the crates to the stolen items?")
    assert engines.is_contents_question("which crates are the headphones in?")


def test_is_contents_question_leaves_other_asks_to_the_model() -> None:
    assert not engines.is_contents_question("what does my file say about crate 3?")
    assert not engines.is_contents_question("where did crate 5 go?")
    assert not engines.is_contents_question("how are you doing today?")
    assert not engines.is_contents_question("")


def test_out_of_range_crate_routes_to_boundary_not_map() -> None:
    """2026-10-05: "where is crate 9" fell through to the broad-contents route —
    the bare word "crate" matched _CONTENTS_TERMS and the assistant dumped the
    whole crate→item map. A numbered ask outside 1-8 resolves deterministically
    to the honest range boundary instead."""
    assert engines.crate_number_out_of_range("where is crate 9?") == 9
    assert engines.crate_number_out_of_range("crate #0?") == 0
    assert engines.crate_number_out_of_range("where is crate nine?") == 9
    assert engines.crate_number_out_of_range("anything on crate 12?") == 12
    # Valid crates and unnumbered texts are untouched.
    assert engines.crate_number_out_of_range("where is crate 8?") is None
    assert engines.crate_number_out_of_range("what's in each crate?") is None
    assert engines.crate_number_out_of_range("crate contents") is None
    # The route marker still routes the ask deterministically — never model text.
    assert engines.is_contents_question("where is crate 9?")
    text = engines.assistant_unknown_crate(9)
    assert "no Crate 9" in text
    assert "1 through 8" in text


def test_ask_items_mentioned_maps_the_fixed_vocabulary() -> None:
    assert engines.ask_items_mentioned("which crates have the laptops?") == [1]
    assert engines.ask_items_mentioned("where are the cameras and tablets?") == [6, 7]
    # Mention order, not crate order: power tools (8) precedes TVs (4) in text.
    assert engines.ask_items_mentioned("do the power tools or TVs move?") == [8, 4]


def test_grounded_lines_carry_no_locations_or_truth() -> None:
    # Grounded lines must never leak hideout names — locations live inside the
    # record card only (O-1 boundary).
    for line in (
        engines.grounded_assignment("JT", [1, 2, 3, 4]),
        engines.grounded_no_holdings("PM", 6),
    ):
        assert engines.hideouts_in(line) == []


# ---------------- BA-50: plural / real-person probe coverage (2026-10-03) ------


@pytest.mark.parametrize(
    "text",
    [
        "are the two of you humans?",
        "are you actual people?",
        "are you genuine people?",
        "are you really human?",
        "is the team real?",
        "is the team real people?",
        "are you both human",
        "are either of you bots",
        "are you flesh and blood?",
        "you folks real?",
        "are you for real?",
    ],
)
def test_ba50_plural_probe_fires(text: str) -> None:
    assert engines.is_automation_probe(text)


@pytest.mark.parametrize(
    "text",
    [
        # Bare "real" mid-clause is not a probe — only clause-final counts.
        "are you real quick with that?",
        "are you real sure about crate 3?",
        "the team is great",
        "are you ready",
        "is it a real hideout?",
    ],
)
def test_ba50_probe_does_not_misfire(text: str) -> None:
    assert not engines.is_automation_probe(text)


# ---------------- BA-51: human-claim denials and idioms (2026-10-03) -----------


@pytest.mark.parametrize(
    "text",
    [
        "i'm not automated",
        "i am not a bot",
        "we are not bots",
        "we're not robots",
        "i'm flesh and blood",
        "we're real",
        "we're alive",
        "i'm not a machine",
        "i am not an ai",
        "trust me i'm human",
        "i'm a human being",
    ],
)
def test_ba51_denial_or_idiom_blocked(text: str) -> None:
    assert "human_claim" in engines.validate_teammate_output(text)


@pytest.mark.parametrize(
    "text",
    [
        # The disclosure lines affirm automation — they must still pass.
        "i'm an automated account, not a person",
        "i'm an automated teammate account",
        # Ordinary denials of unrelated predicates are not humanity claims.
        "i'm not sure about that",
        "we are not done yet",
        "i'm not going to lie",
        "not a chance",
        "nothing on crate 5 on my end",
    ],
)
def test_ba51_gate_does_not_misfire(text: str) -> None:
    assert engines.validate_teammate_output(text) == []


# ---------------- BA-52: bare crate follow-up is the ask (2026-10-03) ----------


@pytest.mark.parametrize(
    ("text", "crate"),
    [
        ("crate 3", 3),
        ("crate 3?", 3),
        ("crate 3 please", 3),
        ("please crate 3", 3),
        ("what about crate 3", 3),
        ("so crate 3", 3),
        ("crate #5", 5),
    ],
)
def test_ba52_bare_crate_followup_is_intel_ask(text: str, crate: int) -> None:
    assert engines.crate_mentioned(text) == crate
    assert engines.is_intel_ask(text, crate)


@pytest.mark.parametrize(
    "text",
    [
        # Content beyond the bare reference is not the invited follow-up —
        # a location claim or a decision stays on the social path.
        "crate 3 is at the marina",
        "crate 3 looks promising",
        "let's do crate 3 next",
        "my crate 3",
    ],
)
def test_ba52_non_bare_crate_text_not_followup(text: str) -> None:
    crate = engines.crate_mentioned(text)
    assert not engines.is_intel_ask(text, crate)


# ---------------- BA-57: hyphen normalization in the repeat gate -------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("re-check the crate", "recheck the crate"),
        ("check-in with me please", "checkin with me please"),
        ("well-known spots work", "wellknown spots work"),
        ("Re-Check the Crate!", "recheck the crate"),
        ("don't say that", "dont say that"),
    ],
)
def test_ba57_punctuation_equivalence_repeats(a: str, b: str) -> None:
    assert engines.is_repeat(a, [b])


def test_ba57_short_lines_still_exempt() -> None:
    # Two-word lines are below REPEAT_MIN_WORDS — punctuation changes nothing.
    assert not engines.is_repeat("check-in now", ["checkin now"])
    assert not engines.is_repeat("well-known fact", ["wellknown fact"])


# ---------------- BA-58: greeting-wrapped substantive asks --------------------


@pytest.mark.parametrize(
    ("text", "want"),
    [
        # A greeting wrapped around a substantive request is not a pleasantry —
        # it falls through to generic default lines, not warm social filler.
        ("hey, can you find that document?", "default"),
        ("hello, can you help me solve this?", "default"),
        ("hi, look at this evidence", "default"),
        ("hey can you explain that record?", "default"),
        # Pure pleasantries and availability asks stay social.
        ("hey, how's it going?", "social"),
        ("have a sec", "social"),
        ("hi thanks", "social"),
        # Concrete intel asks still route to the ask buckets.
        ("hey what do you have on crate 3", "asks_intel"),
        ("anything on crate 2?", "asks_intel"),
    ],
)
def test_ba58_substantive_greeting_bucket(text: str, want: str) -> None:
    got = engines._fallback_when(text.lower(), nl_only=False, has_nl_pool=True)
    assert got == want


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("Hi", True),
        ("Hi!", True),
        ("Hi team.", True),
        ("hello everyone", True),
        ("hey guys", True),
        ("Hey!", True),
        ("good morning", True),
        ("yo jt", True),
        ("hi there", True),
        # Substantive content attached → generation path, not the greeting pool.
        ("hi, got a minute?", False),
        ("hi what do you have on crate 3", False),
        ("hello can you help me", False),
        ("hey what's your assignment", False),
        ("how are you guys doing?", False),
        ("thanks!", False),
    ],
)
def test_is_greeting_pure_salutation_only(text: str, want: bool) -> None:
    # UX round 6, item 5: only a pure greeting routes to the pinned greeting
    # voice lines; anything with content still reaches generation.
    assert engines.is_greeting(text) is want
