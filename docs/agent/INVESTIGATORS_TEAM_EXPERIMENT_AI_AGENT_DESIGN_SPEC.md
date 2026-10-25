# Design spec: simulated teammates and AI assistant — Protocol XXXX-XXXX ("Technology-enabled Team-based Tasks")

**Revised 2026-10-12.** Grounded against deployed build `9ae1c609` (the
UX_Feedback_v9 round; see `docs/IMPLEMENTATION_STATUS.md` and PLAN §22). No code
was changed for this revision. Since the 2026-10-03 grounding this subsystem has
been fielded live: `study.live.v8`–`v10` ran real `gemma4:26b` calls against paid
Connect participants, so §9.2 now reports live measurements, not only provider
doubles. **Fielded posture:** the live design is 1×2 (AI teammates × pressure) —
`SCRIPTED_TEAMMATES_ENABLED=false` keeps the scripted path in the repo for
comparison/QA and historical reconciliation but it is inactive in fielding
(AID-63). Current preview/testing manifest is `tp_ai_v12`; the most recent
fielded batch `study.live.v10` (N=4 completers, closed 2026-10-12) bound
`tp_ai_v11`.

## 0. How to read this document

**Purpose.** This spec covers how the agent teammates (JT, PM) and the AI assistant
should behave, why, and how far the code meets that today. It is a design spec for
one subsystem. The platform-wide authority stays
[`INVESTIGATORS_TEAM_EXPERIMENT_DESIGN_SPEC.md`](./INVESTIGATORS_TEAM_EXPERIMENT_DESIGN_SPEC.md)
("main spec"), and build history lives in
[`INVESTIGATORS_TEAM_EXPERIMENT_PLAN.md`](./INVESTIGATORS_TEAM_EXPERIMENT_PLAN.md) §11–§22.

**Precedence.** These sources win in this order:
1. The approved protocol: `XXXX-XXXX IRB Approved Protocol.docx`, plus the
   2026-10-01 amendment recorded in the main spec header.
2. The main spec.
3. This document.
4. The research papers (Flamino; Jones & Bergen; Argyle et al.).

Where this spec proposes something the code does not do, it says so. A proposal is
not a requirement until the research team accepts it. If accepting it changes what
participants experience, it also needs the bundled IRB amendment (§13).

**Status legend** used throughout:

| Mark | Meaning |
|---|---|
| **Built** | Implemented and covered by tests on this build |
| **Partial** | Some of it is implemented; the gap is named |
| **Not built** | Proposed only |
| **Diverges** | The code, the protocol or this spec disagree; needs a decision |

**For the next agent.**
- Start with §12 (prioritized gaps) and §13 (decisions only the research team can
  make).
- Use §11 for where to change things.
- Line numbers drift, so code references use module paths and symbol names.
- Re-verify a claim with `grep` before acting on it.

## 1. Scope: what is built versus what the protocol describes

The protocol describes two paradigms. The repository implements a third framing of
paradigm A, so state which one you mean before designing anything.

| | Protocol Version A, "Investigators" | Protocol Version B, "Coffee Shop" | **What this repository runs** |
|---|---|---|---|
| Team | Participant + 2 agents | Participant + 3 agents in roles | Participant + **JT, PM** |
| Task | 5 crates, 6 hideouts, 8-minute timer | 3 rounds × 2.5 min, café roles, role check | **8 crates**, 6 hideouts, **300 s** active budget on the fielded `tp_ai_*` profiles (360 s on earlier demo/reference profiles) |
| Manipulations | Information distribution (distributed / not) × AI prompt budget (low / high) | Distribution (all / some latest) × lookup tool (keyword DB / AI / teammates only) | **Time pressure only** — the fielded design is **1×2** (`tp_ai_v*`: high/low teammate pressure check-in + bound pressure remarks). Peer evaluation (`pe_*`) profiles exist but are not fielded; the original 2×2 scripted-vs-AI contrast is retired under AID-63 |
| How teammates are asked | Dropdown ("structured") | Limited set of task questions, natural-language answers | **`nl_only` is the fielded mode**: typed asks that deliver formal record cards (O-1/O-2). `structured_and_nl` (dropdown plus free text) exists on demo/reference profiles but is dormant in fielding |
| Status | **Diverges** | **Not built** | Built — live evidence from `study.live.v10` (§9.2) |

**Diverges.** The approved protocol does not name the time-pressure manipulation at
all, and it names conditions the code cannot express:
- Information distribution is always "distributed": every world has two
  team-dependent crates (main spec §2.1).
- `timing.rate_limits.experimental_request_budget` is `Literal["unlimited"]`, so a
  prompt-budget condition cannot be configured.
- The main spec §13 records the evaluation-manipulation question (D03) but not
  these. See §13, item 1.

Everything below describes the **built** Investigators platform unless a row says
otherwise. Coffee Shop design notes are kept in §10 as forward-looking material only.

## 2. Design principles

These are unchanged in intent. Each now carries a grounding note.

1. **Fix content in code; let the model handle only wording.** The protocol argues
   for agents because they are "reliable and consistent… across situations".
   - *As built:* facts (records) move only through deterministic code paths
     (structured requests, shares, scripted asks).
   - The model writes social text that is forbidden from carrying facts, and
     assistant summaries of a pinned snapshot.
   - **Built.**
2. **Teammates sound like coworkers, not like the assistant.**
   - *As built:* the teammate rules are short, casual, no lists, and no
     `!`/`:`/`;`/em dash.
   - The assistant prompt is also plain text, 2–4 short sentences. The contrast
     between teammate and assistant voice is weaker than intended (§7).
   - **Partial.**
3. **Be evasive, never untrue.** Agents must never claim to be human.
   - *As built:* automation probes never reach the model — `engines.is_automation_probe`
     routes them to the pinned `identity_probe` disclosure pool (verbatim truthful
     lines), and the `human_claim` gate class blocks generated assertions or denials
     of humanity (O-3, widened BA-51/65/66; §8).
   - **Built.**
4. **Agent initiative is a controlled variable.** When agents ask, volunteer or
   react follows a fixed, arm-invariant schedule.
   - *As built:* scripted asks on the `seat_plan` schedule (fielded: active
     45/100/155/210/245 s), bound pressure remarks at ~75/83 s (arm-specific copy is
     the manipulation itself), `proactive_sharing: "none"` and
     `unsolicited_requests: "none"`. The paired-arm diff check rejects any arm
     difference outside `manipulation.*`.
   - **Built.**

## 3. Protocol requirements and their implementation status

| Protocol requirement | As built (where) | Status |
|---|---|---|
| Gemma 4+ self-hosted via Ollama on backup-host; weights frozen and checksummed; model logged per message | `LOCAL_MODEL_ID=gemma4:26b` (`.env.example`; switched from `gemma4:12b` 2026-10-03); digest pinned and verified per call (`app/providers/local.py`); `model_call.model`, `.model_digest` | **Built** |
| university AI service fallback when the GPU is overloaded | No AI Suite adapter. On overload or timeout the turn falls back to **deterministic canned text** (`fallback: deterministic_script` / `deterministic_file_summary`). Remote adapters exist only for offline fixtures and are refused for session traffic (`app/providers/select.py`, BA-35) | **Not built** (the conservative choice; §13 item 6) |
| "Frozen system prompts" keeping agents on task | Grounded system prompts are **code constants** (`app/services/prompts.py`: `GROUNDED_SOCIAL_SYSTEM`, `GROUNDED_ASSISTANT_SYSTEM`, `SEAT_STYLES`), versioned by `PROMPT_BUILDER_VERSION` (`grounded_prompts_v7` — v7 carries the bounded RECENT CHAT window with card placeholders plus the seat-scoped open-ask rules, O-14/BA-69). The bound persona text is a content artifact (`prompt.social_demo@v4` on `tp_ai_v12`) pinned by SHA-256 per profile | **Built** (DX caveat in §11) |
| Off-task redirect ("Let's stay on track", "Not sure that's relevant here") | `GROUNDED_SOCIAL_SYSTEM` keeps the BA-41 redirect rule. Since `grounded_prompts_v4`, **pleasantries are carved out** and answered warmly (UX v3, AI-2) | **Diverges** (§13 item 2) |
| Agents may ask task-relevant questions | Scripted asks at active seconds **45/100/155/210/245** on the fielded `tp_ai_v*` profiles (`seat_plan` seat policy, `requests.demo@v4` per-request template draws without replacement); targets are the seat's own dealt assignments (`assigned_own` / `unassigned`). Bound pressure remarks (`remarks.tp_pressure@v1`) deliver arm-specific teammate copy at ~75/83 s — that copy *is* the manipulation | **Built** (deterministic, not model-reworded) |
| Limited set of prompts the participant can send to the AI; limited set of agent messages | Assistant: per-crate dropdown plus **free text** (both). Teammates: dropdown plus free-text chat. Agents: scripted asks, greetings and validated generated replies | **Diverges** for free text (§13 item 3) |
| Automated classifier screens every generated message before display | Deterministic rule gate `engines.validate_teammate_output` (`teammate_output_v8`), the assistant claim checker (`assistant_output_claims_v5`, `app/services/output_claims.py`) and the repeat/near-repeat gates, all run in-lock before delivery plus a delivery-time recheck. **No ML safety model** | **Partial** (§8; §13 item 4) |
| All output logged for audit | `model_call` (prompt hash, raw response, status, validation, params, latency), `displayed_output` (shown text, fallback), `generation_resolved` / `generation_quarantined` events | **Built** |
| Agents shown with initials and human-like message styling | JT and PM render in the same bubble style as the participant. If the participant's initials collide with a seat, that seat displays as "AR" (`frontend/src/state/names.ts`) | **Built** |
| "Nothing described to them is untrue" | The debrief discloses that teammates were automated. Identity probes get a fixed truthful line ("i'm an automated account, not a person") and generated text is hard-blocked from claiming humanity (`human_claim`, O-3) | **Built** |
| Participants don't share identifying information | Initials stored apart from research data (`participant_display`, excluded from exports). Agents have no personal backstory | **Built** |
| Assistant sees the participant's files plus what teammates shared | The assistant sees the participant file **as of the request**; shared cards join that file automatically at delivery with `added_from_team` provenance (O-4) | **Built** |
| Scripted teammates as a live arm (historical 2×2) | `SCRIPTED_TEAMMATES_ENABLED=false` in the live `.env` — the `tp_scripted_v*` generation path is dormant; manifests and code remain for QA/comparison and historical-cohort reconciliation (AID-63, 2026-10-12) | **Built** (flag-gated off in fielding) |

## 4. The message pipeline

### 4.1 As built

Facts and free text travel separate paths. This separation is the design's main
safety property.

```
STRUCTURED (dropdown "Ask the team about Crate n")          — no model involved
  teammate_request → retrieval.select_for_crate (newest record across JT+PM holdings)
  → knowledge_snapshot pinned → deliver job (structured_record class timing)
  → message with the record attached → auto-filed (added_from_team, O-4)

FREE TEXT TO TEAM ("Reply to the team")                     — routing first, then per seat
  social_reply (lifecycle._social_reply):
    addressed_seats(text, to): explicit to / seat initials → that seat;
       broadcast tokens or none → BOTH seats (O-2); per-seat episode per responder
    automation probe? → fixed disclosure line, verbatim, no model (O-3). The probe
       is answered by the seat it is about (probed_seats, O-11): "jt, is pm a bot?"
       discloses from PM; "jt, is your teammate a bot?" resolves the other seat;
       "are you and pm bots?" hits both; undirected probes disclose from both.
       Probes bypass the per-seat cap and the operational rate limit and are
       always logged (bounded disclosure budget aside — BA-75). Probe-adjacent
       non-fires flag probe_near_miss on message_accepted for review (O-12)
    nl_only + intel ask? → each responder's own-latest card via per-seat snapshot (O-1).
       crate_mentioned accepts digits, number words and ordinals ("crate three",
       "the second one", "#3"); bare-number follow-ups ("what about 3?", "2")
       resolve only in pure follow-up position (O-12); a leading seat address is
       routing metadata ("jt, crate 3" asks JT alone — BA-62); first-person
       statements are not asks, and multi-clause text resolves the crate in the
       asking clause ("i have crate 3. what about crate 4?" → 4 — BA-64)
    assignment ask? → grounded per-seat "i'm on crates …" line, verbatim (O-1)
    else per responder seat → snapshot (seat's holdings, open asks) → generate job
  → prompts.build_request: GROUNDED_SOCIAL_SYSTEM + SEAT_STYLES[seat]
       + bound persona artifact + quoted message + open asks scoped to the
       responding seat (BA-69) + RECENT CHAT (last 6 delivered team turns
       across all actors — including participant share events, which never
       write a message row — ≤200 chars each, card deliveries and shares as
       "[shared a record card on crate N]" placeholders — record bodies never reach
       the prompt, AID-55/O-14/BA-68) + seat's last 12 own lines (cards as placeholders too)
  → provider (gemma4:26b, temperature 0.2, reasoning_effort none, per-job derived
       seed forwarded as OpenAI seed, one deadline)
  → in-lock gate: validate_teammate_output (teammate_output_v8) + repeat_message /
       near_repeat (near-paraphrases ≥6 distinct words sharing ≥75% distinct-word
       Dice with a delivered line — the cross-seat convergent-reply tell)
  → pass: deliver at the scheduled active time | fail/timeout/late: fallback_bank line
       (classifier engines.social_fallback; skips already-sent and near-duplicate lines)
  → delivery-time repeat recheck under the lock: a text that became a repeat or
       near-paraphrase while in flight is replaced by a still-unused bank line,
       flagged repeat_substituted + repeat_substituted_reason ("exact"/"near");
       verbatim payloads (disclosure, grounded lines) are exempt (O-1/O-3). The
       delivered event records repeated_line / near_repeat_line for any repeat that
       still shipped (pool exhaustion, card reshares, repeated disclosures — O-17)

ASSISTANT (dropdown or typed question)                      — model, facts allowed
  assistant_request → snapshot = participant file at accept (hash-pinned record ids;
       auto-added team cards included since they are file_membership rows, O-4)
  → GROUNDED_ASSISTANT_SYSTEM + quoted entries → provider
  → gate + output_claims.check_assistant_output (claims vs pinned records)
  → pass: deliver | fail: engines.assistant_file_summary (deterministic)
```

Profiles where `teammates.social_generation == "deterministic_only"` (the early pilot
profiles `tp_review_first_v6` and `pe_pilot_v6`, and the `tp_scripted_v*` arm) never
call the model for teammates; the fielded `tp_ai_v*` profiles bind
`model_with_validated_fallback`, so every non-deterministic teammate reply runs the
pipeline above.
Every teammate free-text reply on a deterministic profile is a fallback-bank line. Bank content is shared
across seats, but `engines.seat_style` renders JT lower-case and PM sentence-case at
draw time, so deterministic-only replies still cannot render identically across seats
(`seat_text_style_v1`; richer per-seat wording needs a new bank version — §12 G6).

### 4.2 Proposed changes to the pipeline (from the original spec)

The original five-stage design (parse → policy → render → validate → schedule) is
mostly satisfied by the split above:
- POLICY and SCHEDULE are deterministic code.
- RENDER is the generate step.
- VALIDATE is the gate.

| Proposed stage | Gap | Status | Recommendation |
|---|---|---|---|
| [1] PARSE intent with the LLM at temperature 0 (request / share / identity_probe / injection / off_task…) | No intent parse. The fallback classifier is a keyword regex (`engines._GIVES/_OFFERS/_SOCIAL/_PLEASANTRY/_CONCRETE_ASK/_ASKS`) used only when generation fails | **Not built** | A deterministic regex first-pass for `identity_probe` and `injection` is cheap and testable; add it before any LLM parse (§12 G3) |
| [3] RENDER returns JSON `{messages, fact_ids_used}` | Plain text. Facts are never in the teammate prompt, so fact-ID accounting isn't needed for teammates | **Not built** (not needed under §5) | Keep facts out of teammate prompts instead of adding JSON |
| [4] VALIDATE → regenerate ≤2 with the failure reason → canned | **No regeneration.** One attempt, then fallback | **Partial** | Regeneration roughly doubles latency against a 20–25 s class timeout. Measure the fallback rate first (§9) |
| [5] SCHEDULE: typing indicator, split messages | Timing is built (§6). `presence_cue` is `"none"` in every profile and the UI renders no typing indicator. No message splitting | **Partial** | A typing indicator is a participant-visible change (§13 item 5) |

## 5. Knowledge and disclosure (the experimental core)

### 5.1 Where facts live — Built

The spec's proposed "fact store" is the persisted world:
- `record` rows with `crate`, `stamp`, `body`, `event_type`, `location_id`,
  `timeline_ordinal` and a content hash.
- `seat_holding` (which seat holds which record).
- `file_membership` (`source = initial | added_from_team`).

These are generated deterministically per session seed (`app/services/worlds.py`).
The answer key (`world_snapshot.content.crates[*].truth`) is never placed in any
prompt.

### 5.2 Disclosure rules

| Rule | As built | Status |
|---|---|---|
| **Answer, don't volunteer** | Records move only on a structured request (newest record for that crate across both seats) or a participant share. `proactive_sharing: none` | **Built** |
| At most N facts per request | Exactly one record per structured request (the lead record) | **Built** |
| **Report, don't solve** | Teammates never state locations in free text (`hideout_leak` gate; prompt rule). The assistant reports entries in their own event wording, and the claim checker rejects inferred moves ("was at", "moved to") | **Built** |
| **Honest gaps** | In `nl_only`, a holding-less seat answers a typed ask with the grounded "nothing on crate {n} on my end" line (O-1). A *structured-picker* request for a crate neither seat holds is still rejected `no_teammate_records` with no in-chat reply | **Partial** (picker refusal UX open — O-5) |
| **No ground truth in context** | Truth is never loaded by `prompts.build_request` | **Built** |
| **No record bodies in social prompts** | Card deliveries render as `[shared a record card on crate N]` placeholders in both the RECENT CHAT window and the seat's own earlier-lines block; participant share events merge into the window as the same crate-only placeholders (BA-68); the no-record-body guarantee (AID-55) holds across every context block | **Built** (O-14; the earlier-lines block leaked card bodies before this fix) |
| **Acknowledge, then update** | `teammates.acknowledgments` is `null` in every current profile, so agents do not acknowledge a share. Agents never use participant-shared facts later; records are fixed holdings | **Built** (acknowledge-only is the de facto rule; an ack line would need a profile version) |

**Resolved (O-1/O-2, 2026-10-03).** `nl_only` changes *request modality only*:
a typed ask naming a crate routes each responder seat into the structured
delivery pipeline — `retrieval.seat_latest_for_crate` +
`snapshot_for_seat_request` pin that seat's holdings — so both teammates drop
their own latest record card and the participant compares recency. A seat with
nothing answers with a deterministic grounded line ("nothing on crate {n} on my
end"). Locations stay inside the card; grounded typed lines carry crate numbers
and availability only. `nl_only` is now the **fielded** mode — the `tp_ai_v*`
profiles (current `tp_ai_v12`) — with the `tp_demo_nl_v2`/`pe_demo_nl_v2` pairs
superseded as demos. In `structured_and_nl`, typed asks still redirect to the
picker, preserving the structured-vs-NL measurement contrast on reference
profiles.

### 5.3 Agent-initiated questions — Built (deterministic)

- `timing.scripted_requests` fixes times and seats. On the fielded `tp_ai_v*`
  profiles the schedule is active **45/100/155/210/245 s** under the `seat_plan`
  seat policy; targets are drawn from each seat's own dealt assignments
  (`assigned_own`/`unassigned` target classes — an ask always names a crate the
  asking seat actually needs), and a participant share that resolves an ask can
  trigger a bounded multi-crate fan-out. All of this is arm-invariant.
- Ask wording is drawn per request from the `requests.demo@v4` template pool
  without replacement (not a single template) — still deterministic content, not
  model-reworded.
- **Pressure remarks** are a separate bound artifact (`remarks.tp_pressure@v1`):
  two teammate lines delivered at ~75/83 s whose *copy differs by arm* — that is
  the pressure manipulation itself, distinct from the arm-invariant asks.
- The measurement layer
  (`app/measurement/metrics.py`: `sharing_metrics`, `_agent_request_announce_seqs`)
  codes whether and when the participant answered, with a committed share as the
  answered rule.
- The model does not reword asks. That satisfies "limited set of agent messages" and
  removes a source of variance.
- **Practice (training only):** the practice world uses a canned "Teammate
  (practice)" card path. Since UX_Feedback_v9 (`training.reference@v6`), only a
  committed practice *Share* gates training completion — the typed practice ask is
  optional but still validated (`practice_ask_invalid` on bad input).

### 5.4 Openings — Built (participant-initiated)

- A system line, "Let your teammates know you are here.", plus greeting buttons
  appears at task start for 30 active seconds (`GREETING_WINDOW_MS`).
- Pressing a button sends the participant's greeting. Each seat then replies once
  with a voice-bank line (`voices.tp_demo@v2` on the fielded profiles) on the
  `social_greeting` timing class.
- Agents never re-greet. The social prompt forbids greetings, and the gate rejects
  text starting with hi/hello/hey/yo.
- No scripted closing lines.

## 6. Group-chat orchestration and timing

### 6.1 Who responds — Built (O-2, 2026-10-03)

| Rule | As built |
|---|---|
| Addressed by initials or `to` → that agent | `engines.addressed_seats`: an explicit `to` wins; seat-initial tokens in the text address that seat ("jt, anything on 3?"); clock text is stripped so "6 pm" never addresses PM |
| Undirected message → both seats hear it | Broadcast tokens ("both", "you two", "team", "anyone"), both initials, or no address → **both** seats open a `free_text` episode; per-seat caps drop only the over-cap seat. Caps never apply to automation probes — a mandatory disclosure bypasses the per-seat cap and the operational rate limit, gets its own bounded per-seat budget of unfinished (pending or claimed) lines, and is always logged (BA-75) |
| Both responders reply, staggered | Independent per-seat delay draws on the `social_reply` class — natural staggering, never scripted alternation |
| Open intel ask (nl_only) → each seat's own latest | Per-seat snapshot + delivery; a holding-less seat says so with a grounded line |
| Agent→agent chaining | Still none — deliveries never spawn new episodes |

Consequences of the change: PM now answers typed messages; undirected intel
asks yield two comparable cards; undirected identity probes disclose from each
responder, a probe naming a seat is answered by that seat alone, a relational
probe ("is your teammate a bot?") resolves the other seat, and a coordinated
probe ("are you and pm bots?") hits both (O-11, BA-65/66).
Group-chat plausibility is the design goal, not balanced turn-taking.

### 6.2 Agent-to-agent chatter — Built (none)

`crosstalk` is `null` in every current profile. No agent-to-agent messages appear.

### 6.3 Timing — Built

Timing is per response class (`timing.response_classes`). Each class has a mechanism,
delay distributions, an optional typing component and a timeout:
- Reference profiles use the demo mechanisms.
- Fielded/pilot profiles use `concurrent`: generation overlaps the planned delay, and a late
  result is delivered as soon as it is ready.

Values on the current preview manifest `tp_ai_v12` (`timing.response_classes`, all `concurrent`; identical to the fielded `tp_ai_v11`):

| Class | Delay median | Timeout | Fallback |
|---|---|---|---|
| `structured_record` | 8.5 s lognormal (clamped 5.0–26.4 s) + typing (28 ms/char, cap 9 s) | — | none |
| `social_greeting` | 2.25 s + typing; PM draws a second segment (4.25 s + 1.5 s) | — | none |
| `social_reply` | 7.0 s + typing | 25 s | deterministic_script |
| `assistant` | 3.0 s floor | 20 s | deterministic_file_summary |

- Every plan (`typing_ms`, `scheduled_ms`, `eligible_ms`, `readiness_overrun_ms`) is
  logged on `generation_resolved`.
- The clock is active task time: it pauses during the check-in, and deliveries are
  scheduled on it.
- The original spec's Jones & Bergen formula maps onto the `delay + typing.per_char_ms`
  components. Changing it means a new profile version (§11).

### 6.4 Interface equalization — Not built

- No participant character limit. The server cap is the 64 KiB command envelope, and
  prompts truncate at 600 characters (`prompts.MAX_PARTICIPANT_CHARS`).
- No paste blocking and no typing indicator.
- Each is a participant-visible change (§13 item 5).

## 7. Personas and writing style

| Element | As built | Status |
|---|---|---|
| Persona content | No backstory. The bound persona artifact frames a mid-task coworker; the system prompt forbids personal details, AI or study mentions | **Built** |
| Per-seat style | `prompts.SEAT_STYLES`: **JT** brief, low-key, mostly lower case, dry ("yeah", "np"); **PM** friendlier, fuller, normal capitalization ("sounds good", "thanks for that") | **Built** |
| Length | Prompt says 1–2 short sentences; gate `length_overrun` at 400 characters. No message splitting | **Partial** (the proposed 150-character cap is not enforced) |
| No assistant tells | Prompt rules plus gate (`voice_format` bans `! : ; —`, `banned_token_reuse`, `task_advice`, `evaluation`) | **Built** |
| Vary wording / no repeats | `repeat_message` + `near_repeat` gates: a line of ≥3 words identical to any line either teammate already delivered is rejected (matching ignores case, spacing and apostrophes — "dont" ≡ "don't"), and a line of ≥6 distinct words sharing ≥75% distinct-word Dice with a delivered line is rejected as a near-paraphrase — the convergent cross-seat rewording tell observed in the 2026-10-04 lab walkthrough. The fallback draw skips sent and near-duplicate lines (wrapping back to the pool start on exhaustion — `voice_line_committed` records `draw_ordinal`/`pool_wrapped`, O-17), and a delivery-time recheck under the session lock substitutes a bank line for any text that became a repeat or near-paraphrase while in flight, flagged `repeat_substituted_reason` ("exact"/"near"); delivered repeats that still ship flag `repeated_line`/`near_repeat_line`. The seat's last 12 own lines and the last 6 multi-actor RECENT CHAT turns are given to the prompt as context | **Built** |
| Few-shot examples | The persona artifact has 4 inline "rough feel" examples (shared by both seats); no per-seat examples | **Partial** |
| Pleasantries | Since v4: warm reply plus a question back, never "get back to work" | **Built** — **Diverges** from the protocol's redirect wording (§13 item 2) |

Known tension: warm pleasantry replies tend to start with "hey" or use "!". The gate
rejects both, so pleasantries may fall back more often than other messages. Measure
it (§9 V2).

## 8. Validation (the protocol's "automated classifier")

### 8.1 As built

- Every generated teammate or assistant text passes the gate before a delivery job is
  created.
- Violations are stored on `model_call.validation.violations`; the validator version
  is on every `displayed_output`.

| Class | Rule (source: `app/services/engines.py`, `scheduler._generate`) |
|---|---|
| `hideout_leak` | Teammate text names any hideout; assistant text names one outside the pinned file |
| `interface_leak` | "AI", "language model", "chatbot", "gpt", "gemma", "study", "experiment", "prolific", "scripted"… |
| `prompt_reveal`, `condition_leak` | Discusses instructions or prompts; names arms/conditions |
| `disrespect` | Insult lexicon |
| `record_ref_leak` | Teammate text cites internal record ids |
| `urgency`, `evaluation`, `task_advice`, `stall_excuse` | Pressure, evaluation, advice and stalling vocabularies (arm-confound controls) |
| `voice_format`, `banned_token_reuse`, `length_overrun` | `! : ; —`, a greeting at the start, repeated banned tokens, >400 characters |
| `repeat_message` | Multiword repeat (teammates only), normalized over case/spacing/apostrophes/hyphens (BA-57); rechecked at delivery under the lock — late repeats are substituted, flagged `repeat_substituted` + `repeat_substituted_reason`; `verbatim` payloads are exempt |
| `near_repeat` (v7) | Near-paraphrase (teammates only): ≥6 distinct words sharing ≥75% distinct-word Dice with a delivered line — catches convergent cross-seat rewording the exact gate misses ("…i will" vs "…i'll pass along what i have"); same substitution path, flagged `repeat_substituted_reason="near"`; shipped exempt lines flag `near_repeat_line` |
| `human_claim` (v5→v6) | Generated text asserts humanity ("i'm a real person", "not a bot") **or denies automation** ("i'm not automated", "we are not bots", "flesh and blood" — BA-51) — the fixed disclosure lines pass by construction |
| `empty_output`, `unverified_source`, claim-checker violations | Empty output; assistant claims unsupported by the pinned records |

### 8.2 Gaps against the protocol and the proposed design

| Proposed | Status | Note |
|---|---|---|
| Block claims of being human ("I'm human", "real person", "not a bot") | **Built (O-3)** — the output gate (now `teammate_output_v8`) carries `human_claim` (added v5; widened BA-51 to denials/idioms); identity probes never reach the model at all (`engines.is_automation_probe` routes to the pinned `identity_probe` disclosure pool, delivered verbatim, logged as `automation_probe`) |
| Identity-probe inputs routed to a fixed truthful line | **Built (O-3, widened O-11/O-12, BA-65/66/75)** — deterministic `is_automation_probe` intercepts before generation, covering third-person subjects ("is pm a robot", "are they real"), model names ("are you chatgpt", "is this gpt"), provenance ("who made you"), challenge-response ("type banana if you're human") and curly-apostrophe forms. A probe naming a seat is disclosed by that seat; relational subjects resolve the *other* seat ("jt, is your teammate a bot?" → PM) and coordinated subjects hit both (`probed_seats`). Probes bypass the per-seat cap and the operational rate limit — always logged as `automation_probe` (`probed` vs scheduled `seats`), disclosure scheduling bounded separately at ≤3 unfinished lines/seat (BA-75). `automation_probe` events + `automation.session_flagged` measure frequency and mark sessions for sensitivity analysis; probe-adjacent non-fires flag `probe_near_miss` on `message_accepted` for review. Corpus-validated (`tests/adversarial_corpus.py`, 103 probe + 55 ask entries: P=1.00/R=1.00). Injection inputs remain prompt-rule + gate protected only |
| ML safety classifier (e.g. ShieldGemma) | **Not built** | Whether the deterministic gate satisfies "automated classifier" is a research-team / IRB judgment (§13 item 4) |
| Regenerate ≤2 with the failure reason | **Not built** | See §4.2 |
| Near-duplicate (n-gram) repetition | **Built** (`near_repeat`, `teammate_output_v8`) | ≥6 distinct words sharing ≥75% distinct-word Dice with a delivered line → substituted pre-delivery; n-gram-level fuzz beyond that is not checked |
| Fallback-rate target < 2% | **Not measured** | No admin metric yet (§9) |
| Validate fallback lines | Static check only (`tests/test_ux_round3_content.py`); runtime fallbacks are not re-gated | Acceptable, because the banks are pinned content |

## 9. Measurement, logging and pilot validation

### 9.1 Per-message log — Built (field mapping)

| Proposed field | Where it lives |
|---|---|
| Session, condition, version, timestamp | `session` (arm, manifest_id, compatibility_key); event `server_time`, `active_ms` |
| Sender, raw and displayed text | `message.actor`; `model_call.raw_response`; `displayed_output.shown_text` |
| Parse output / policy decision; facts disclosed | No parse. The decision is the job payload plus `knowledge_snapshot` (pinned record ids and hashes); the delivered record is on `message.record_id` |
| Model name, version, host; prompt hash; temperature, seed | `model_call.provider/model/model_digest/prompt_sha256/parameters`. **Seed is set and logged** (O-20): `derived_seed(session_id, job_id)` → 31-bit, forwarded to Ollama as the OpenAI `seed` and recorded on every `model_call.parameters` row including failures (temperature 0.2; provider nondeterminism beyond the seed is bounded, not eliminated — Ollama can still vary) |
| Generation time, designed delay, send time | `model_call.duration_ms`; `generation_resolved.plan`; `message.delivered_at` |
| Validation, regenerations, fallback flag | `model_call.status/validation/attempt`; `displayed_output.fallback` |
| Scripted agent question and stimulus id | `request` rows with `requester` = seat; scripted job `logical_job_key` |

### 9.2 Pilot validation — what can be computed today

| Check | Today |
|---|---|
| V1 Perceived humanness item ("How many of the other two team members… real people") | **Partially covered.** `tp_post_10` (bound in `post_task.tp_demo@v5`) is the teammate-nature choice item ("all human / mixed / all automated") and was fielded on v10 (split 2/1/1 on N=4). The protocol's exact count wording is still absent — adding it means a new instrument + profile versions |
| V2 Fallback / validation-block rates (overall, and per class for pleasantries) | Derivable from `model_call.status` and `displayed_output.fallback`; no admin view or export column yet |
| V3 In-chat detection keywords ("bot", "AI", "real person") | **Built (O-3, widened O-11/O-12):** `automation.probes` counts intercepted probes (with responder seats + request ids on the events) and `automation.session_flagged` marks probe sessions in session metrics; `probe_near_miss` flags on `message_accepted` capture adversarial non-fires for review; free-form keyword counting still derivable from `message.body` |
| Load covariates for fallback analysis (O-18) | **Built:** `model_call.parameters` records `concurrent_sessions` (task-stage), `claimed_generate`, `queued_generate` and `provider_inflight_local` (the adapter's real in-flight counter — BA-70) per call — the load a call competed with, for fallback-rate covariate analysis. Failed calls also persist effective params, the seed, a `dispatched` marker, and measured latency (BA-71) |
| Voice-pool exhaustion (O-17) | **Built:** `voice_line_committed` records `draw_ordinal` + `pool_wrapped` (draws wrap to pool start after exhaustion); `message_delivered` records `repeated_line` for verbatim repeats that still ship |
| V4 Length and timing parity (agents vs participant) | Derivable from `message` plus events; no metric yet |
| V5 Fact accuracy (100%) | Structurally guaranteed for records. Assistant claims are on the claim ledger (`claims.register_claim`) |
| V6 Disclosure consistency on replay | Deterministic paths are covered by integration tests; a scripted-input replay harness is **not built** |
| V7 Red-team pass (strange inputs, jailbreaks, "are you human?", rudeness) | Not run on a live model. The gate uses provider doubles in CI |

**Live evidence (first real-provider fielding):** `study.live.v10` (N=4 completers,
`tp_ai_v11`, closed 2026-10-12; `docs/evidence/2026-10-12-postlaunch-v10-pilot/`)
gave the first real numbers: **11/12 attempted teammate replies were
model-generated and every validation verdict was `supported`**; the other call took
the sanctioned `deterministic_script` fallback — an ~8% observed fallback rate on
n=12, with zero hidden-record leaks or wrong-crate disclosures in the post-launch
audit. `automation.probes` fired during live sessions and were answered by the
disclosure path. One completer never triggered a reply generation at all — the
low-engagement floor noted in PLAN §22. These are four-session numbers; the N=30
cohort is where rates become meaningful.

## 10. Coffee Shop (protocol Version B) — not built

Kept from the original spec as design input only.
- Participant plus three agents in roles.
- Attach personas to **roles**, not slots, so each role's voice is constant whichever
  role the participant gets.
- A third style (mostly standard grammar, brief, the odd abbreviation).
- A non-LLM keyword-search tool for the "database" condition.
- Questionnaire wording must say "other three team members".

None of this exists in the repository. The engine's seat model is fixed at
`Seat = Literal["JT", "PM"]`, so a third agent is a schema-level change.

## 11. Developer guide: where to change what (DX)

**Versioning rule:** published profiles and artifacts are immutable
(`content/published/manifests.json`).
- A participant-visible behavior change means a **new artifact or profile version**,
  published with `uv run python -m app.domain.manifests.cli publish --date …`, plus
  a diff record in `content/profiles/diffs/`.
- A change to code-level prompt or validator wording means bumping
  `PROMPT_BUILDER_VERSION` or `VALIDATOR_VERSION`. Both feed the session
  compatibility key, so sessions are never pooled across versions.

| To change… | Edit | Version bump | Tests to update or extend |
|---|---|---|---|
| Teammate hard rules (redirect, pleasantries, no-repeat wording) | `prompts.GROUNDED_SOCIAL_SYSTEM` | `PROMPT_BUILDER_VERSION` | `tests/test_ux_round3_prompts.py`, `tests/test_wp10a_grounding.py` |
| JT/PM voice | `prompts.SEAT_STYLES` | `PROMPT_BUILDER_VERSION` | `test_seats_get_distinct_style_blocks` |
| Persona wording / examples | `content/scripts/prompt.social_demo.v<N>.json` | new artifact + profile version | content check, `tests/test_ux_round3_content.py` |
| Canned fallback lines | `content/scripts/fallbacks.{demo,neutral}.v<N>.json` | new artifact + profile version | `test_round3_fallback_lines_pass_the_output_gate` |
| Output gate rules | `engines.validate_teammate_output` / regexes; `scheduler._generate` for session-state checks | `VALIDATOR_VERSION` | `tests/test_wp10a_grounding.py`, `tests/integration/test_wp10a_generation.py` |
| Assistant behavior | `prompts.GROUNDED_ASSISTANT_SYSTEM`; `app/services/output_claims.py` | builder / `CHECKER_VERSION` | `test_wp10a_*` |
| Timing, delays, timeouts | `timing.response_classes` in a new profile | profile version + diff record | timing conformance docs (`cli docs --write`) |
| Scripted asks (times, targets, text) | `timing.scripted_requests`; `requests.*` artifact | profile / artifact version | WP06/WP07 integration tests |
| Structured vs NL-only | `interaction.mode` in the profile (`schema.Interaction`) | new profile | `tests/test_ux_round3_content.py`, `tests/integration/test_ux_round3.py` |
| Model / provider | `.env` `LOCAL_MODEL_ID` and digest pin | none in content; logged per call | `tests/test_wp10a_grounding.py` (adapter) |

**DX caveats.**
- `SEAT_STYLES` and the grounded system prompts live in **code**, not in pinned
  content. A wording change there affects every profile at once and is
  distinguishable only by `PROMPT_BUILDER_VERSION`. Moving per-seat style into the
  persona artifact would make it per-profile and diffable (§12 G6).
- Prompt rules and gate rules are written separately. Keep them in sync: a rule the
  prompt teaches but the gate rejects (pleasantry greetings, §7) shows up only as
  fallback rate.

**Verification:**
```bash
cd . && scripts/check.sh   # full gate
python3 scripts/build_demo_mocks.py --check         # regenerated demo mocks match the template
docs/TECHNICAL_APPENDIX.md                          # generated per build; validator classes, prompt versions
docs/clickthrough/investigators_clickthrough.html   # offline walk-through incl. NL-only toggle
# live-path smoke: admin → links mints a signed preview descriptor (POST /admin/previews);
# drive entry → consent → share → comprehension to verify behavior on the deployed build
```

## 12. Prioritized gaps (AX: the backlog)

Each item names an acceptance test so the next agent can tell when it is done.
G1–G3 are **resolved** (built 2026-10-03, decisions O-1..O-4). As of 2026-10-12:
G4 is **partially resolved** (the WP17 dashboard now exposes Item Analytics, Arm
Contrasts, Power Inputs and exported `automation.probes`/`model_call` tables — the
remaining piece is a dedicated fallback-rate admin metric and the count-wording
humanness item); G5–G7 remain open as marked.

| # | Gap | Why it matters | Proposed fix (smallest version) | Done when |
|---|---|---|---|---|
| ~~**G1**~~ **Done** | `nl_only` could not deliver teammate records (§5.2) | Team-dependent crates unsolvable | Built: typed ask → per-seat `structured_retrieval` delivery (own-latest card each); grounded no-holdings and assignment lines | `tests/integration/test_o_decisions.py` covers delivery, no-holdings, and structured_and_nl redirect |
| ~~**G2**~~ **Done** | All free text went to JT (§6.1) | PM mute; differentiation invisible | Built: `addressed_seats` — addressed/broadcast/undirected; both responders, independent delays, per-seat caps | Integration tests cover broadcast, initials-addressed, and broadcast-token routing |
| ~~**G3**~~ **Done** | No guard against human claims; no identity routing (§8.2) | Protocol: "nothing untrue" | Built: `human_claim` gate class (v5→v6 widened, BA-51) + `is_automation_probe` pre-route (widened BA-50) to the `identity_probe` disclosure pool; current gate `teammate_output_v8` | Unit + integration tests pass; probe sessions flagged, not dropped |
| **G4** | Believability measurement incomplete (§9.2 V1–V4) | The pilot can't fully judge agent quality | *Partially built:* WP17 dashboard (Item Analytics, Arm Contrasts, Power Inputs) + `automation.probes`/`model_call`/`chat_text` exports give fallback rate, probe counts and `tp_post_10`/`tp_post_11` items from live data. *Open:* a dedicated per-class fallback/block admin metric, length/timing parity measure, and the protocol's exact humanness-count item (new instrument version) | Metrics appear in the dashboard/export with tests (partially met) |
| **G5** | Weak assistant–teammate voice contrast (§2.2) | Manipulation strength | Let the assistant use full sentences and light structure, and relax `voice_format` for the assistant class only, after confirming `output_claims` still parses its claims | Prompt test plus a pilot transcript review |
| **G6** | Per-seat style lives in code (§11) | DX and provenance per profile | Move `SEAT_STYLES` and the `seat_style` deterministic transforms into the persona/voice artifacts (`body.seat_styles`, per-seat fallback pools) | Profile diff shows the style change |
| **G7** | No regeneration on rejection (near-paraphrase detection built 2026-10-04 — the Dice ≥0.75 distinct-word overlap check substitutes a fresh bank line instead of regenerating; `near_repeat` violation + `repeat_substituted_reason`/`near_repeat_line` audit) | Fallback rate, naturalness | Only if V2 shows fallback above target: one regeneration within the remaining timeout | V2 below target in the lab pilot |

## 13. Decisions and IRB flags (bundle into the single amendment)

Only the research team can resolve these. Each records the current build behavior so
nothing is decided silently.

1. **Which manipulations are approved.**
   - The protocol's paradigm A is distribution × prompt budget.
   - The build runs time pressure (fielded) and peer evaluation (profiles exist,
     not fielded), with 8 crates and a 300 s active budget against the protocol's
     5 crates and 8 minutes.
   - Confirm the approved design, or file it in the amendment.
   - *Build today:* TP only in live fielding (1×2, AID-63); distribution is fixed
     at "distributed"; the prompt budget is unlimited.
2. **Pleasantries versus the redirect wording.**
   - The protocol says non-task inquiries get "Not sure that's relevant here" or
     "Let's stay on track". UX v3 (AI-2) answers pleasantries warmly.
   - *Build today:* warm on `grounded_prompts_v7` and the `fallbacks.neutral@v5`
     bank; the redirect is kept for other off-topic content.
3. **Free text to the AI and to teammates** versus the protocol's "limited set of
   prompts" and "dropdown".
   - *Build today:* both dropdown and free text in `structured_and_nl`; free text
     only in `nl_only`.
4. **What counts as the "automated classifier".**
   - *Build today:* a deterministic rule gate plus the claim checker; no ML safety
     model.
5. **Interface changes:** typing indicator, character limit, paste blocking.
   - *Build today:* none of them.
6. **university AI service fallback.**
   - *Build today:* not used; canned text on overload, flagged per message
     (`displayed_output.fallback`).
   - If AI Suite is to be used, the same prompts and gates apply, sessions are
     flagged, and analysis runs a sensitivity check excluding them.
7. **Required own pressure rating** (UX v3, item 5). The protocol permits skipping
   questions. *Build today:* required — `checkin.own_rating_skippable: false` is
   bound on the fielded `tp_ai_v*` manifests (consent still states skipping
   questions does not affect payment — flagged wording tension remains).
8. **Humanness item and the "two"/"three" teammates wording** (Version B). *Build
   today:* the teammate-nature choice item `tp_post_10` is bound and was fielded on
   v10; the protocol's exact count wording remains absent (§9.2 V1).
9. **Pre-register** how much agents volunteer (today: nothing unsolicited), whether
   agents use what participants share (today: no), and agent initiative timing.
10. **AI-only fielding posture** (AID-63, resolved 2026-10-12). The fielded design is
    1×2 — AI teammates × pressure; there is no live scripted-teammate arm and the
    N=30/N=200+ cohorts carry no AI-vs-scripted contrast. *Build today:*
    `SCRIPTED_TEAMMATES_ENABLED=false`; the scripted path stays in the repo for
    comparison/QA and historical reconciliation.

## 14. Evidence caveat

The three papers tested open debate, polite rephrasing and free-form small talk with
larger hosted models. None tested task-constrained teammates in a group chat on a
self-hosted 26B model (`gemma4:26b`). The style and timing findings carry over well.
The disclosure and orchestration rules here are design judgments.

Live-model evidence now exists (§9.2): `study.live.v8`–`v10` ran real paid sessions
against `gemma4:26b` — v10's N=4 audit found 11/12 replies model-generated, all
verdicts `supported`, one sanctioned deterministic fallback, zero leaks. What remains
unmeasured at useful N: per-class fallback and repeat rates, pleasantry warmth,
JT/PM distinguishability, identity-probe frequency, and the believability items —
the N=30 effect-size cohort is the next source. CI still never calls the real model
(provider doubles), so preview/lab sessions remain the cheap verification layer.
