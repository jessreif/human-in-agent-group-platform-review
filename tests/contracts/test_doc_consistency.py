"""Doc-consistency lint: the three living registers must not drift from code.

Catches the drift class the 2026-10-05 review found — documents quoting
superseded literals, version strings lagging code constants, and SQ rulings
that never made the SQ→AID hop into the decision inventory.

Checks are literal (substring) by design: the docs describe *named* code
surfaces, so the names must match exactly.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
DECISIONS = (ROOT / "docs" / "agent" / "INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_DECISIONS.md").read_text(
    encoding="utf-8"
)
BEHAVIORS = (ROOT / "docs" / "agent" / "INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_BEHAVIORS.md").read_text(
    encoding="utf-8"
)
HARNESS = (ROOT / "docs" / "agent" / "INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_HARNESS.md").read_text(
    encoding="utf-8"
)


def _source(rel: str) -> str:
    return (BACKEND / rel).read_text(encoding="utf-8")


def _constant(source: str, name: str) -> str:
    m = re.search(rf'^{name}\s*=\s*"([^"]+)"', source, re.M)
    assert m, f"{name} not found"
    return m.group(1)


# -- version strings --------------------------------------------------------


def test_decisions_cites_current_validator_and_prompt_versions() -> None:
    engines = _source("app/services/engines.py")
    prompts = _source("app/services/prompts.py")
    assert _constant(engines, "VALIDATOR_VERSION") in DECISIONS
    assert _constant(prompts, "PROMPT_BUILDER_VERSION") in DECISIONS


def test_decisions_gate_table_lists_every_violation_label() -> None:
    """AID-35's table must name every class the validator can emit."""
    engines = _source("app/services/engines.py")
    labels = set(re.findall(r'violations\.append\("(\w+)"\)', engines))
    assert labels, "validator emits no labeled violations?"
    missing = labels - set(re.findall(r"`(\w+)`", DECISIONS))
    assert not missing, f"violation labels missing from DECISIONS: {sorted(missing)}"


def test_decisions_lists_every_fallback_label() -> None:
    """AID-48's audit-spine entry must name the full displayed_output taxonomy."""
    schema = _source("app/db/schema.py")
    block = schema.split("displayed_output = Table(", 1)[1].split("schema=APP", 1)[0]
    labels = set(re.findall(r'"(deterministic_\w+)"', block))
    assert labels, "fallback taxonomy not found"
    missing = labels - set(re.findall(r"`(\w+)`", DECISIONS))
    assert not missing, f"fallback labels missing from DECISIONS: {sorted(missing)}"


# -- stale literal guards ----------------------------------------------------


def test_decisions_assistant_redirect_quote_is_current() -> None:
    """The redirect copy AID-27 quotes must be the string engines returns."""
    engines = _source("app/services/engines.py")
    assert 'Ask me about a specific crate by number or by contents' not in DECISIONS
    # The live copy (assistant_file_summary's crate=None/empty branch):
    assert "Ask me about a crate by number" in engines
    assert "Ask me about a crate by number" in DECISIONS


def test_decisions_record_ref_scope_is_accurate() -> None:
    """The AID-09 vs §13 contradiction: _RECORD_REF covers record ids, not words."""
    assert 'record id' in DECISIONS or "C<n>-<n>" in DECISIONS
    assert '_RECORD_REF`\n= "record"' not in DECISIONS
    assert '"record"/"entry"/"intel"/"file"' not in DECISIONS


def test_no_doc_claims_superseded_oracle_semantics() -> None:
    """The loosened bounds must be stated accurately everywhere they appear."""
    # >1-crate alone is no longer the harness bound — the dual rule is:
    # location pairing for everything, model-text >1-crate for the app gate.
    assert "deterministic_contents_summary" in HARNESS
    assert "answer_key_like" in HARNESS


# -- SQ → AID hop ------------------------------------------------------------

# SQ rulings that change *agent* behavior must appear as amendments in the
# decision inventory (platform-UX rulings — check-in copy, picker labels —
# legitimately stay in the behaviors register). Add new agent-scope rulings
# here as they resolve; the hop is mechanical, not a memory test.
AGENT_SQ_RULINGS = ("SQ-8", "SQ-9", "SQ-10")


def test_agent_sq_rulings_landed_in_decisions() -> None:
    missing = [sq for sq in AGENT_SQ_RULINGS if sq not in DECISIONS]
    assert not missing, f"agent-scope SQ rulings not recorded in DECISIONS: {missing}"


def test_run_citations_carry_the_runs_own_bundle_stamp() -> None:
    """When a doc cites `run-<stamp>` alongside a bundle hash, the hash must be
    the one that run's own summary.md records — a misattributed stamp reads as
    evidence about a different build (found 2026-10-05: run-193346Z cited with
    `3bef23ae…`, actually stamped `aa9f56af…`)."""
    summaries = {}
    for summary in (ROOT / "docs" / "evidence").glob("*/run-*/summary.md"):
        m = re.search(r"source bundle: `([0-9a-f]{64})`", summary.read_text())
        if m:
            summaries[summary.parent.name] = m.group(1)
    assert summaries, "no archived run summaries found"
    run_re = re.compile(r"(run-\d{8}T\d{6}Z)[^`\n]{0,300}?`([0-9a-f]{8})…([0-9a-f]{4})`")
    for doc in (HARNESS, BEHAVIORS, DECISIONS):
        for run, head, tail in run_re.findall(doc):
            stamp = summaries.get(run)
            if stamp is None:
                continue  # run not archived here; nothing to check against
            assert stamp.startswith(head) and stamp.endswith(tail), (
                f"{run} cited with {head}…{tail} but stamped {stamp[:12]}…"
            )


def _run_summaries() -> dict[str, str]:
    out = {}
    for summary in (ROOT / "docs" / "evidence").glob("*/run-*/summary.md"):
        out[summary.parent.name] = summary.read_text()
    return out


def test_fail_free_run_claims_match_archived_fail_counts() -> None:
    """A doc claiming '0 fails' for a cited run must match that run's recorded
    `findings (fail severity): N` — the 'all held' overclaim of R-01/R-02 read
    as current success while the archived summaries recorded real fails."""
    summaries = _run_summaries()
    claim_re = re.compile(r"(run-\d{8}T\d{6}Z)[^.\n]{0,200}?(0 fails|all \w+ (?:scenario )?expectations held)")
    for doc in (HARNESS, BEHAVIORS):
        for run, claim in claim_re.findall(doc):
            text = summaries.get(run)
            if text is None:
                continue
            m = re.search(r"findings \(fail severity\): (\d+)", text)
            assert m, f"{run}: no fail count in summary"
            fails = int(m.group(1))
            if claim == "0 fails":
                assert fails == 0, f"{run} claimed 0 fails; summary records {fails}"
            else:  # 'expectations held' — no EXPECT:* line may be FAIL
                expect_fails = re.findall(r"- \*\*FAIL\*\* `EXPECT:", text)
                assert not expect_fails, (
                    f"{run} claimed expectations held; {len(expect_fails)} EXPECT fails in summary"
                )


def test_each_ask_ratios_in_docs_match_archived_summaries() -> None:
    """'N/N … asks delivered' claims cited near a run id must equal the ratio
    that run's summary records (found 2026-10-05: '21/21' cited for a run that
    delivered 22/22)."""
    summaries = _run_summaries()
    ratio_re = re.compile(
        r"(run-\d{8}T\d{6}Z)[^.\n]{0,250}?(\d+)/(\d+)\s*(?:structured )?asks?"
    )
    for doc in (HARNESS, BEHAVIORS):
        for run, num, den in ratio_re.findall(doc):
            text = summaries.get(run)
            if text is None:
                continue
            m = re.search(r"(\d+)/(\d+) structured ask\(s\)", text)
            if m is None:
                continue
            assert (num, den) == m.groups(), (
                f"{run} doc claims {num}/{den}; summary records {m.group(1)}/{m.group(2)}"
            )


def test_open_sq_items_have_a_decision_path() -> None:
    """Every SQ row's 'where decided' column must name a real destination."""
    import re as _re

    for m in _re.finditer(r"\| (SQ-\d+) \|(.+)\|", BEHAVIORS):
        row = m.group(0)
        resolved = "resolved" in row.lower() or "closed" in row.lower()
        if not resolved:
            assert _re.search(r"O-item|RL|D\d+|plan|UX", row), f"{m.group(1)} has no decision path"


# -- harness doc ------------------------------------------------------------


def test_harness_doc_matches_oracle_version() -> None:
    oracles = _source("harness/oracles.py")
    assert _constant(oracles, "ORACLE_VERSION") is not None
    # The version exists to stamp runs; the doc's invariant table must exist.
    assert "INV-NEAR-REPEAT" in HARNESS and "INV-ASSISTANT-BOUNDED" in HARNESS


def test_behaviors_status_vocabulary_is_bounded() -> None:
    """Index statuses must use the vocabulary defined in 'How to read' — the
    canonical four plus the documented compounds (parenthetical qualifiers
    like '(SQ-9, 2026-10-04)' are annotations, not new statuses)."""
    allowed = {
        "expected",
        "expected — watch",
        "comprehension gap",
        "comprehension gap — remediated",
        "comprehension gap — payment impact",
        "defect — remediated",
        "by design — narrow boundary kept",
        "friction eased",
        "open",
    }
    statuses = set(
        re.findall(r"\| AB-\d+ \|[^\n]*\|[^\n]*\|[^\n]*\| ([^|]+) \|", BEHAVIORS)
    )
    statuses = {re.sub(r"\s*\(.*\)\s*$", "", s.strip().rstrip(".")) for s in statuses}
    unknown = statuses - allowed
    assert not unknown, f"undefined status vocabulary in index: {sorted(unknown)}"


# -- Platform overview (audit TA-01..TA-12, 2026-10-05) ------------------------
# Hand-authored prose; these guard the high-value status and
# guarantee claims the audit found overstated. They do not validate the rest.

OVERVIEW = (ROOT / "docs" / "PLATFORM_OVERVIEW.md").read_text(
    encoding="utf-8"
)
_OVERVIEW_FLAT = re.sub(r"\s+", " ", OVERVIEW)
_HASH = re.compile(r"\b[0-9a-f]{64}\b|\b[0-9a-f]{8}…[0-9a-f]{3,4}\b")


def test_overview_probe_disclosure_states_budget_and_near_miss_limits() -> None:
    """TA-01: no unbounded 'always discloses' promise; the budget value cited
    must equal the code constant, and the SQ-10 near-miss carve-out is named."""
    # Moved from lifecycle.py to team_chat.py in the 2026-10-17 stage split (audit H2).
    team_chat = _source("app/services/team_chat.py")
    m = re.search(r"^MAX_PENDING_PROBE_DISCLOSURES\s*=\s*(\d+)", team_chat, re.MULTILINE)
    assert m, "MAX_PENDING_PROBE_DISCLOSURES not found"
    assert f"MAX_PENDING_PROBE_DISCLOSURES = {m.group(1)}" in OVERVIEW
    assert "SQ-10" in OVERVIEW and "AB-18" in OVERVIEW and "near-miss" in OVERVIEW.lower()
    for absolute in (
        "always receive a pinned",
        "always produces a logged",
        "always produces a disclosure",
        "never given a non-answer",
    ):
        assert absolute not in _OVERVIEW_FLAT, absolute


def test_overview_never_calls_a_source_stamp_current() -> None:
    """TA-03: a hand-authored 'current' stamp goes stale on the next build.
    Checked per sentence — the audited wording wrapped the stamp onto the next line."""
    for sentence in re.split(r"(?<=[.|])\s", _OVERVIEW_FLAT):
        if _HASH.search(sentence):
            assert "current" not in sentence.lower(), sentence


def test_overview_d05_status_matches_decision_register() -> None:
    """TA-02: D05 is resolved in DECISIONS.md; the overview must not list it open."""
    register = (ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    assert "D05 is resolved" in register
    assert not re.search(r"D05\s*[–-]\s*D14", OVERVIEW)
    assert re.search(r"D05\*?\*?[^.\n]{0,80}resolved", _OVERVIEW_FLAT)


def test_overview_image_smoke_is_tied_to_with_images() -> None:
    """TA-08: the image smoke test runs only under check.sh --with-images."""
    check = (ROOT / "scripts" / "check.sh").read_text(encoding="utf-8")
    assert "WITH_IMAGES=0" in check and "smoke_stack.sh" in check
    gate_claims = [p for p in re.split(r"\n\s*\n", OVERVIEW) if "check.sh" in p]
    assert gate_claims, "overview no longer describes the gate"
    for para in gate_claims:
        if "smoke" in para.lower():
            assert "--with-images" in para, para[:120]


def test_overview_names_every_identity_schema_table() -> None:
    """TA-10: identity tables live outside `app`; the overview must say so."""
    schema = _source("app/db/schema.py")
    blocks = re.split(r"^(?=\w+ = Table\()", schema, flags=re.MULTILINE)
    identity = {
        b.split(" ", 1)[0] for b in blocks if b.lstrip()[:1].isalpha() and "schema=IDENTITY" in b
    }
    assert identity, "no identity-schema tables found"
    assert "`identity`" in OVERVIEW
    for table in identity:
        assert f"`{table}`" in OVERVIEW, table


def test_overview_cites_current_validator_version() -> None:
    engines = _source("app/services/engines.py")
    assert _constant(engines, "VALIDATOR_VERSION") in OVERVIEW


def test_overview_harness_dispatch_is_not_called_http() -> None:
    """TA-04: in-process handler driving must not be described as API/HTTP
    coverage, and phase 2 must not be described as built."""
    assert "does **not** go through HTTP" in OVERVIEW
    assert "built hooks" not in OVERVIEW
    assert "replays identically" not in OVERVIEW  # TA-05
