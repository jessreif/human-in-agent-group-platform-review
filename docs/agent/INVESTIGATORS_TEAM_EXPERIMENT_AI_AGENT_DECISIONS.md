# Investigators — AI-Agent Decision Inventory (single source of truth)

**Status:** living inventory, grounded in deployed source
`b165395b9fd43365536027cf1c16fc080eb3c98a5f0bf9d932440f2cf219ae36` (deployed
2026-10-12); agent-relevant entries updated through the 2026-10-04/05
lab rulings (SQ-8/9/10), the 2026-10-05 review hardening (identity-draw
collision fix, late-ask restoration, dual assistant bound), the
2026-10-05 live-test remediation (check-in re-ask + out-of-range crate
boundary, AB-24/AB-25), the 2026-10-08 fielded 2×2 launch
(`tp_ai_v3`/`tp_scripted_v5`, `nl_only` everywhere, all items required,
first N=5 paid pilot on `study.live.v4`), the 2026-10-09 pilot
revision (`tp_ai_v4`/`tp_scripted_v6` on `study.live.v5`: 300 s budget,
requests 45/130/210, bound arm remarks at 75/83 — AID-56, warning 240 s,
consent v6 / $3.00 — minted but never opened), the 2026-10-10
assistant-generation audit remediation (`tp_ai_v6`/`tp_scripted_v8` on
`study.live.v7`, opened 2026-10-10 for the second pilot: verbatim-excerpt
assistant contract `prompt.assistant_demo@v4`, `assistant_output_claims_v5`,
`grounded_prompts_v7`, `teammate_output_v8` — AID-57/AID-58), and the
2026-10-10 incentive-compatibility revision (`tp_ai_v7`/`tp_scripted_v9` on
`study.live.v8`: consent v7 offers "$3.00 plus a $0.50 bonus for correctly
locating your assigned crates" while `payment.s5_base_only@v5` pays a flat
350¢ to every completer — non-deception per owner/university IRB; pilot incentive
frame, identical across all four cells), and the
2026-10-10 comprehension-check + eligibility-phrasing successors
(`tp_ai_v8`/`tp_scripted_v10`, preview/QA only — **no new fielding group
minted**; `study.live.v8` stays open on v7/v9): `consent.reference@v8`
rephrases the offer to "eligible to receive a bonus of up to $0.50 based on
your performance on the task"; `training.reference@v4` adds a five-item
true/false comprehension gate as the last practice screen before "Connecting
you to your team" — server-held answer key, wrong answers returned on
`result.missed` for highlighting, retries allowed, every attempt recorded,
completion requires a zero-miss attempt (still gated on the committed BA-95
practice share). UI: task header "The Case" → "Case file", visible crate
badges restored on all time-stamped intelligence (reverses UX round 3 item 7),
answer ribbon rephrased to "You are eligible for a bonus based on how many
locations you correctly identify."), the 2026-10-11 student-testing round
(`tp_ai_v11`/`tp_scripted_v13` on `study.live.v9`, N=5 Connect pilot —
AID-61/AID-62: open-ended stage, `tp_post_11` manipulation check, neutral
file cue), and the **2026-10-12 AI-only fielding transition (AID-63)** —
`study.live.v9` closed and **`study.live.v10` opened for the N=4 Connect
pilot**: `tp_ai_v11` × high/low only (1 × 2, members `.ai`/`.ai2`), file-added
cue removed, scripted arm behind `SCRIPTED_TEAMMATES_ENABLED=0` (refused at
live mint/enrollment/consent; preview/QA unaffected). The N=30 round carries
no AI-vs-scripted contrast — effect-size estimation toward the pre-registration."
**Scope:** every decision — explicit or implicit — that shapes what the teammate
agents (JT, PM) and the AI assistant do: what they may say, when, to whom, from
what information, under which safety gates, and how it is measured.

## How to read this file (AX)

- Decision IDs are `AID-nn`, grouped by area. Each entry gives the **current
  build behavior** and the code/content surface where it lives.
- **Status values:** `implemented` (enforced in code today), `proposed` (in a
  published artifact or profile but not fielded), `unresolved` (open design
  question), `research decision` (only the research lead/IRB may settle it),
  `diverges` (build does not match a protocol or spec statement).
- **Alternatives:** the option(s) considered and *not* chosen — kept explicit so
  a later reader can see what the build could have done instead. An
  alternative listed for a still-`unresolved` or `diverges` row is a live
  option, not a settled one.
- **Resolution convention:** a status of `resolved O-n: implemented` records the
  open decision O-n (§12) it resolved to, the date on the O-item. An O-item
  marked **resolved YYYY-MM-DD (owner)** is the `resolved_on` field the index
  folds into the status cell.
- **Owners:** RL = research lead, EL = engineering lead, IRB = IRB liaison.
- Cross-references: `D-nn` → `docs/DECISIONS.md` research register; `ED-nn` →
  engineering decisions in the same file; `G-nn` → the gap backlog in
  `INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_DESIGN_SPEC.md` §12; `BA-nn`/`AI-n`/
  `UX-n`/`FR-n` → remediations recorded in plan §14–§17; `INV-nnnn` → plan task
  numbers.
- References use file + symbol, not line numbers. If an entry and the code
  disagree, the code wins and this file should be updated.
- This file is the **index**, not the archive: rationale and history live in
  `docs/DECISIONS.md`, the plan, and the design spec. When a decision is made,
  record it here *and* in the relevant D-item.
- Anything marked `research decision` or touching an `unresolved` D-item must
  **not** be treated as settled because it appears here, and must not ship to
  paid participants until the bundled amendment resolves it.

## Decision index

| ID | Decision (short) | Status | Owner | Alternatives (not chosen) |
|---|---|---|---|---|
| AID-01 | Two studies (time pressure, peer evaluation), not protocol Version A | diverges vs protocol | RL/IRB | Protocol's distribution × prompt-budget paradigm; Coffee Shop (Version B) paradigm |
| AID-02 | 8 crates / 6 hideouts / 4 assigned / 2 own-file / 2 team-dependent | implemented (schema-locked) | RL | Protocol's 5 crates / 8-minute geometry; different own/team-dependent split |
| AID-03 | Per-manifest active task budget (300 s fielded v5; 360 s on superseded builds), single global deadline, no early submission | implemented | RL | Revised PE demo's 480 s wall clock; early submit; pause-able task clock |
| AID-04 | Three scripted teammate requests per task (fielded times 45/130/210 s) | implemented | RL | Model-generated asks; no scripted asks; a different count or adaptive asks |
| AID-05 | Scripted-request seat policy per profile | implemented; sender-identity sub-question open | RL (D07) | Fixed "JT always asks"; fresh draw per session instead of pinned policy |
| AID-06 | Experimental request budget unlimited | implemented (schema-locked) | RL | Protocol's low/high prompt-budget manipulation |
| AID-07 | Interaction mode per profile: `structured_and_nl` / `nl_only` | implemented (FR-1); arm-invariant | RL | Mode as an experimental arm / per-session randomization |
| AID-08 | `nl_only` typed asks route to formal card shares — mode is solvable | resolved O-1/O-2 (2026-10-03): implemented | RL | Drop `nl_only` entirely (G1 fallback); typed record *contents* in chat; drag-and-drop sharing without typed-ask recognition |
| AID-09 | Model text never discloses locations/records; deterministic grounded lines may state own assignments/holdings | resolved O-1: implemented (validator + grounded lines) | RL | Model-generated grounded claims (needs a teammate claim checker — deferred); banning typed facts entirely including grounded lines |
| AID-10 | All structured intel flows as record cards, not typed text | implemented (unchanged — grounded lines carry no locations) | RL | Free-text paraphrase of record contents; card + typed summary duplicating the location |
| AID-11 | Undirected typed messages reach both seats; addressed text reaches the named seat | resolved O-2: implemented (`addressed_seats`) | RL | Everything to JT (the old bug — i.e., leaving it un-remediated); strict seat alternation for social replies (rejected by RL: reads scripted); holder-of-fact-only responder |
| AID-12 | Teammate-addressing parser: initials tokens, broadcast words, clock-PM guard, explicit `to` wins | implemented (`engines.addressed_seats`) | — | LLM intent parse at temperature 0; requiring explicit `@JT`/`@PM` syntax; UI target picker |
| AID-13 | Broadcast = one response episode per responder seat; per-seat caps drop only that seat | resolved O-2: implemented | RL | Single shared episode per message; drop the whole message when any seat is over cap; global (not per-seat) cap |
| AID-14 | Participant greeting gets one reply from each seat, staggered | implemented | RL | One greeting reply total; model-generated greetings; no greeting replies |
| AID-15 | Agents never act uninvited beyond greeting/schedule | implemented (schema literals) | RL — pre-register (spec §13.9) | Proactive intel offers / unprompted agent initiative |
| AID-16 | Agents never recommend or mention the assistant | implemented (`assistant_referral: none`) | RL | Teammates point the participant at the assistant ("ask the AI") |
| AID-17 | Acknowledgment + crosstalk policies are per-profile content | implemented (PE pilot only) | RL (D07) | Always-on acks/crosstalk in code; never acknowledging shares |
| AID-18 | Seat identities are fixed strings JT/PM; participant-initials collision → display "AR" | implemented | EL | Random/persona names per session; refusing colliding participant initials |
| AID-19 | Automation probes get fixed truthful disclosure lines (shared draw + per-seat offset on the two-line `identity_probe` pool — broadcast can never echo), `automation_probe` event + session flag; generated text is gated by `human_claim`; narrow probe boundary reaffirmed SQ-10 | resolved O-3: implemented | RL + EL | Model-composed answer (unbounded risk); evasive redirect without disclosure; auto-excluding the session in build (kept: flag-not-drop, exclusion is a pre-reg item) |
| AID-20 | Structured request = newest record across both seats, attributed to holder | implemented | RL | Latest from the addressed seat only (nl_only *addressed* asks do per-seat; broadcast asks now match this rule — O-1/AID-59); random held record; oldest record |
| AID-21 | Request for a crate nobody holds is refused, no reply | implemented | RL — silent-refusal UX question open | Honest "nothing on that" reply (built for nl_only typed asks; still open for the picker — O-5) |
| AID-22 | Delivered cards auto-join the file (`added_from_team` + `shared_by` seat); `add_to_file` command removed | resolved O-4: implemented | RL | Keep the manual Add-to-file step (status quo ante); no promotion at all (intel stays chat-only, assistant never sees it); promote chat prose too |
| AID-23 | Participant shares own-file records to chat; agents cannot share the participant's file | implemented | RL | Agents pulling from the participant's file; drag-to-assistant sharing; no share channel |
| AID-24 | Assistant sees the participant file pinned at request-accept; no chat | implemented (`source_access` literal) | RL | Live file at generation time; file + team-chat transcript; direct seat-holdings access |
| AID-25 | Assistant answers are claim-checked against pinned records | implemented (`assistant_output_claims_v5`; v2 semantics over-rejected live — see AID-57/RCA-2026-10-09; v5 = audit remediation, AID-58) | EL | Unvalidated assistant text; regenerate instead of reject+fallback |
| AID-26 | Assistant never says what the file lacks or where a crate "is" | implemented (prompt + denial gate) | RL | Allow negative statements ("nothing in your file on 3") |
| AID-27 | Assistant fallback is a deterministic file summary; recognized contents questions route `deterministic_only` to the `CRATE_CONTENTS` resolver (SQ-8, BA-92/93) | implemented (schema 0007) | RL | Canned apology line; model retry before fallback; letting model text answer contents questions (rejected — provenance + completeness) |
| AID-28 | System prompts are code constants, persona text is a versioned artifact | implemented | EL (G6 proposes moving style to artifacts) | All prompt text inside artifacts; prompt text in the database |
| AID-29 | Untrusted text is quoted and delimiter-escaped; records cannot inject | implemented | EL | Raw interpolation of participant text; stripping risky spans |
| AID-30 | Session traffic uses the pinned local Ollama provider only | implemented (BA-35) | EL (D05) | Hosted/remote provider as primary; provider selectable per profile |
| AID-31 | Remote adapters exist but are refused for all session traffic | implemented | EL — university AI service fallback not built | Remote (e.g. university AI service) fallback on local overload |
| AID-32 | Model pin verified per call against the served digest; no mid-session change | implemented | EL | Trust the configured model name; verify once at worker boot |
| AID-33 | Provider calls bounded: one whole-call deadline, semaphore 4 | implemented (BA-48) | EL | Unbounded queue; per-stage deadlines; a larger semaphore |
| AID-34 | Timeout/error → deterministic fallback; no regeneration | implemented; regen undecided (G7) | RL | Bounded regeneration ≤2 with the failure reason (G7); silent drop |
| AID-35 | Teammate output gate `teammate_output_v8` (14 prompt classes + `repeat_message`/`near_repeat` at delivery = 16; `human_claim` widened BA-51; v8 = assistant-class exemptions + destination allowlist, AID-58) | implemented | RL/EL | ML safety classifier (e.g. ShieldGemma — spec §13.4 open); no teammate gate at all; prompt rules only |
| AID-36 | Repeat prevention: generation-time + delivery-time recheck under lock | implemented (AI-1) | EL | Generation-time check only (leaves the in-flight race); allowing repeats |
| AID-37 | Deterministic seat styling: JT lower-case, PM sentence case; grounded assignment frames are also per-seat with re-ask variants (SQ-9) | implemented (AI-3); per-seat *wording* is a content decision | RL (D07) | Per-seat vocabulary banks (G6/O-7); seat-identical lines; prompt-only styling for deterministic profiles |
| AID-38 | Fallback classifier routes asks/thanks/pleasantries/off-topic/unclear | implemented (AI-2) | RL | One generic fallback line for every miss; LLM intent classification |
| AID-39 | Deterministic-only vs model-backed social replies is a profile field | implemented | RL (D07) | Always model; always deterministic; mode chosen per seat |
| AID-40 | Late generation results are quarantined, never delivered | implemented | EL | Deliver late results anyway; drop without logging |
| AID-41 | Reply timing is per-class policy (mechanism `concurrent` on all fielded profiles) | implemented | RL (D10) | Fixed uniform delay; instant replies; demo-style wall-clock callbacks |
| AID-42 | Presence pacing: PM joins ~10.6 s wall after participant; task opens ~1.8 s later | implemented (constants, not profile content) | RL | All seats present at join; per-profile presence pacing |
| AID-43 | Crosstalk only fires 28 s after an unanswered scripted request | implemented (PE pilot only) | RL (D07) | Immediate crosstalk; no crosstalk; model-decided agent chaining |
| AID-44 | Concurrency caps and rate limits are resolved-policy enforced | implemented | EL | Hard-coded caps; no pending caps |
| AID-45 | Voice banks: PE bank has per-seat pools; TP bank is greetings-only; social fallback bank is seat-independent (`asks_intel_nl` + `identity_probe` pools since v3; fielded pins `fallbacks.neutral@v5`) | implemented (content) | RL (D07) | One shared bank everywhere; per-seat pools on every profile |
| AID-46 | All AI content artifacts are hash-pinned and immutable; changes require new versions + new profiles | implemented (ED-13) | EL | Editable/mutable content rows; prompt text living only in code |
| AID-47 | Validator/prompt-builder/seat-style versions feed the compatibility key | implemented (BA-41) | EL | Pooling sessions across wording/gate changes |
| AID-48 | Every model call and every displayed output is logged separately | implemented | EL | One combined log record; client-side logging only |
| AID-49 | Measurement covers seeking routes, AI ratio, fallback disposition, typed-seeking (`via: typed`), automation probes (`automation.probes` + `automation.session_flagged`), and the fielded teammate-nature item `tp_post_10` (+ per-option binary shares) | implemented; residual gaps under O-9 (per-class fallback rates, parity checks) | RL | Free-text detection-keyword counts; no probe measure |
| AID-50 | Nothing rates the participant, ever | implemented (ED-19) | RL (D03) | Symmetric agent→participant ratings |
| AID-51 | AI content on fielded profiles is `proposed_unapproved` | implemented | RL — amendment gate | Marking content approved; fielding before amendment |
| AID-52 | Warm pleasantry replies diverge from protocol redirect wording | diverges (kept, flagged) | RL/IRB (spec §13.2) | Protocol redirect copy ("Not sure that's relevant here" / "Let's stay on track") for all off-task text |
| AID-53 | Free-text channels diverge from protocol "limited prompts/dropdown" | diverges (kept, flagged) | RL/IRB (spec §13.3) | Dropdown-only requests |
| AID-54 | "Automated classifier" = deterministic rule gates, no ML model | implemented | RL/IRB (spec §13.4) | ML safety classifier; human-in-the-loop review |
| AID-55 | Social prompts never contain record bodies | implemented | EL | Passing seat holdings into social prompts for grounded generation |
| AID-56 | Arm-specific teammate pressure remarks: two bound verbatim team-chat lines at active 75/83 s, fair-coin first speaker, never model text | implemented (2026-10-09 pilot revision) | RL | Model-generated remarks (condition_leak/voice_format gate risk); reusing the dormant crosstalk slot; per-seat line pools |
| AID-57 | Assistant claim-checker repaired after the live 100%-rejection incident (RCA-2026-10-09): transit-destination, anchored retellings, "not moved" presence assertions, per-occurrence scanning, contents-vocabulary crate binding | implemented (`assistant_output_claims_v4` + `grounded_prompts_v6` + `prompt.assistant_demo@v3`; tp_ai_v5/tp_scripted_v7 — semantics superseded by AID-58 audit remediation → v5/v7 + `prompt.assistant_demo@v4` on tp_ai_v6/tp_scripted_v8) | EL | Relaxing the validator wholesale; swallowing rejections silently (kept: every rejection still falls back to the deterministic summary); prompt-only remediation (would not fix wrong rejections) |
| AID-58 | Assistant-generation audit remediation (2026-10-10): bounded verbatim-excerpt response grammar, normalized question scope, exact evidence-time/role semantics, fail-closed snapshot path, provider boundary taxonomy, consent-pinned generation policy, assistant monitor + release panel | implemented (`assistant_output_claims_v5` + `grounded_prompts_v7` + `teammate_output_v8` + `assistant_fallback_v2` + `semantic_record_v1` + `prompt.assistant_demo@v4` on tp_ai_v6/tp_scripted_v8; release-panel PASSED 2026-10-10 on build 1039f9cb — release/evidence/assistant-panel-v1-2026-10-10.json) | EL | Widening free-prose acceptance (rejected: F06 keeps the validated-type contract); reusing the incident pin set without successor versions |
| AID-59 | Ask-routing remediation after the v8 fielding review (2026-10-10): multi-crate fan-out (cap 4), broadcast asks deliver the single newest cross-seat record, unresolved asks get the deterministic invite, `assigned_own` 130 s target with zero-hold seat preference, social prompt v4, typed practice ask in training v5, neutral file-added cue | implemented (`tp_ai_v9`/`tp_scripted_v11`; `prompt.social_demo@v4` + `training.reference@v5`) | EL | Assistant redirect on teamdep asks (rejected — contaminates the secondary outcome); clarifier round-trip for multi-crate asks (rejected — costs a pressure-sensitive step); per-seat-pair card delivery kept (rejected — newest-first ordering misled in d26f4190) |
| AID-60 | UX v7 lab-feedback revision (2026-10-11): `seat_plan` five-slot schedule off dealt assignments, new intel prepends the case file, practice card relabeled `Teammate`, "teammates'" copy fix | implemented (`tp_ai_v10`/`tp_scripted_v12`, preview/QA only — closed) | EL | Keeping targets/seat-policy draws (rejected — ordinal asks must land on the asker's own deal); appending new intel (rejected — buried below the initial file) |
| AID-61 | UX v8 follow-ups (2026-10-11): `open_ended` stage with two sequential free-text questions before debrief, "New" removed from the file-added cue, consent v8 wording verified | implemented (current preview build, closed — no new profile version) | EL | Artifact-bound instrument slot (rejected — would force a new profile version; code-defined instrument chosen, artifact-pin recommended before fielding) |
| AID-62 | UX v8 follow-up #2 (2026-10-11): teammate-rushed manipulation check `tp_post_11` after `tp_post_09` via `post_task.tp_demo@v5`; demo `.nl-only` hidden-override bug fixed (`.nl-only:not([hidden])`) | implemented (`tp_ai_v11`/`tp_scripted_v13`, preview/QA only — closed) | EL | Code-defined extra item injected into the v4 projection (rejected — a measured manipulation check must be artifact-pinned; served ≠ pinned would corrupt the measure's provenance) |

---

## 1. Research and experiment scope

### AID-01 — Which experiment runs — `diverges`, RL/IRB

The protocol's paradigm A crosses information distribution × AI prompt budget
(5 crates, 8 min). The build runs **time pressure** and **peer evaluation**
studies (8 crates; 360 s budget through the v4 fielded builds, 300 s on the
2026-10-09 v5 builds — AID-03). Distribution is fixed at "distributed" and the prompt
budget at "unlimited" — both are single-value schema literals
(`manifests/schema.py`), so the protocol's factors cannot be configured even in
preview. Recorded for the amendment in spec §13 item 1. *Build today:* TP/PE
only.

### AID-02 — Task geometry — `implemented`

`Task` schema literals: `n_crates: 8`, `n_hideouts: 6`, `n_assigned: 4`,
`n_own_file: 2`, `n_team_dependent: 2`, generator `investigators.world/demo_v1`,
`difficulty_version: 1`. Changing any of these is a schema change plus a new
generator version, not a profile edit.

### AID-03 — Clock and deadline — `implemented`

`timing.active_budget_s` is a per-manifest value (`Literal[300, 360]` since the
2026-10-09 pilot revision — previously a 360-only literal). Fielded builds
`tp_ai_v6`/`tp_scripted_v8` run **300 s**; all superseded profiles keep their
360 s pin. `ordering: scheduled_chronological`; `deadline_structure:
single_global_freeze`; `early_submission: False`; `trajectory: constant`. One
visible warning — `warning.one_minute_left@v3` at active 240 s on the 300 s
builds (v1 at active 300 s on the 360 s builds). The check-in (TP only) sits
at active 60 s with `own_rating_skippable: false` on fielded builds (UX-5) —
older published profiles keep `true` and cannot be re-published. Dashboard
timing and both admin HTML simulations read each session's own bound budget;
no surface hard-codes 360.

### AID-04 — Scripted teammate requests — `implemented`

Exactly three per task (`timing.scripted_requests`), each a
`Requesting intel on Crate {n}.` announcement drawn from `requests.demo@v4`
(fielded builds draw three *distinct* approved templates without replacement
across both seats), targeting `unassigned/assigned/unassigned` crates. Fielded
v5 builds fire at active **45/130/210** s — 45 s lands *before* the 60 s
check-in; the superseded 360 s fielded builds (`tp_ai_v3`/`tp_scripted_v5`)
fired at 80/180/270; demo/pilot reference profiles keep 40/150/240. These are
agent-initiated asks to the participant and — with the AID-56 remarks — the
only unsolicited teammate contributions besides greetings.

### AID-05 — Scripted-request sender policy — `implemented`; open sub-question

`tp_review_first_v*`: `coin_majority_shuffle` (coin-drawn majority seat, keeps
arm balance). Demo/PE-pilot: `shuffle_fixed_multiset` `JT/PM/JT`. D07's open
sub-question: does sender identity interact with post-task peer ratings in PE?

### AID-06 — Request budget — `implemented` (schema-locked)

`rate_limits.experimental_request_budget: "unlimited"` is a single-value
literal. The protocol's "prompt budget" factor has no executable surface.

### AID-07 — Interaction mode — `implemented` (FR-1)

`interaction.mode` per profile: `structured_and_nl` (default when the field is
absent — all pre-round-3 profiles) or `nl_only`. Deliberately *not* under
`condition_set`, so both arms always share it; it rides `policy.controls` into
the compatibility key. Server rejects what the mode forbids rather than relying
on hidden controls.

### AID-08 — `nl_only` solvability — `implemented` (O-1, 2026-10-03; widened BA-52, 2026-10-03)

In `nl_only`: `structured_seeking` is refused in every phase (manifest
cross-rule + `lifecycle`), and crate-only assistant asks are refused
(`AssistantPanel` + server). `sharing` and `social_reply` stay open. **Resolved
O-1/O-2:** a typed intel ask (`engines.is_intel_ask`: crate reference + ask
marker) routes each responder seat into the same structured delivery pipeline —
`retrieval.seat_latest_for_crate` + `snapshot_for_seat_request` pin *that
seat's* holdings only — so the mode is solvable and recency-comparable (both
seats share their own latest card). A seat with no holdings answers with the
grounded `grounded_no_holdings` line. Fallback lines where no crate parses
come from the new `asks_intel_nl` pool ("tell me which crate…"), never the
picker redirect. **BA-52:** a bare crate-numbered follow-up ("crate 3",
"crate 3 please") — the reply the `asks_intel_nl` line invites — counts as
the ask via `engines._BARE_CRATE_ASK`; in `structured_and_nl` the same typed
intel ask gets the deterministic picker-redirect verbatim, no model call
(BA-53). **BA-62/63/64 (2026-10-04):** a leading seat address is routing
metadata ("jt, crate 3" asks JT alone); newer-form asks in structured modes
redirect via an explicit `asks_intel` draw; first-person statements
("i have crate 3") are not asks, and `ask_crate_mentioned` resolves
multi-clause text from the asking clause ("i have crate 3. what about
crate 4?" → 4). First profiles: `tp_demo_nl_v2`/`pe_demo_nl_v2`; since the
2026-10-08 launch **every** fielded TP build is `nl_only` (`tp_ai_v3`/`v4`,
`tp_scripted_v5`/`v6` — the ask picker appears in no paid condition).

---

## 2. Agent identities, roles, and truthfulness

### AID-18 — Seat identity — `implemented`

Two fixed seats `JT`, `PM` (`SEATS`), plus actor `assistant` and `participant`.
If the participant's initials collide with a seat label, that seat displays as
"AR" (`frontend/state/names.ts: displaySeat`) — a display rule only; events and
seats keep their canonical names. All three agent actors carry `agent_state`
memory rows (`engines._MEMORY_SEATS`), used for commitments, outstanding
requests, and delivery counting.

### AID-15/16 — Agent initiative and referral — `implemented`

`unsolicited_requests: "none"` and `assistant_referral: "none"` are schema
literals on every profile. Agents initiate only: two greeting replies (on the
participant's greeting), three scripted crate requests, the two bound
arm-specific pressure remarks (AID-56, fielded v5 builds), per-profile
acknowledgment/crosstalk draws, and replies to accepted participant messages.
Teammates never point the participant at the assistant. Spec §13 item 9 asks
the team to pre-register agent-initiative volume before fielding.

### AID-19 — Truthfulness safeguard — `implemented` (O-3, 2026-10-03; widened BA-50/51/59, 2026-10-03)

Two layers, both deterministic. *Routing*: `engines.is_automation_probe`
intercepts identity probes before generation ("are you a bot/real/human/ai",
"you a robot?", "is everyone automated?", and — BA-50 — plural/real-person
forms like "are the two of you humans?", "are you actual people?", "is the
team real?"; a bare "real" probes only at clause end, so "are you real
quick" is safe). *Disclosure*: each responder seat draws verbatim from the
pinned **two-line** `identity_probe` bank pool ("i'm an automated account,
not a person" / "i'm an automated teammate account") — the two seats draw
from a *shared* seat-agnostic index and offset by seat, so a broadcast probe
can never produce the same disclosure line from both seats (BA-59 intent,
made guaranteed 2026-10-05: the earlier per-seat independent draws collided
50% of the time on a two-line pool and disclosure lines are exempt from
repeat substitution — O-17's constant-ordinal observation was the tell).
Both lines are truthful and minimal — delivered verbatim (exempt from repeat
substitution), never model text, zero `model_call` rows. The probe is logged as an `automation_probe`
event carrying the responder seats and request ids, and the session is
flagged by `automation.session_flagged` so analysis can drop or
sensitivity-check it — the exchange itself is retained. *Admission*
(BA-75): a recognized probe is classified before the operational limiter
and bypasses both `operational_commands_per_minute` and the per-seat
pending cap — no full queue or saturated window can suppress the
disclosure or its audit event. The event records `probed` targets vs
scheduled `seats`; disclosure *scheduling* is bounded separately at
`MAX_PENDING_PROBE_DISCLOSURES=3` unfinished (pending or claimed) lines
per seat — past it the probe is still logged with nothing queued.
*Outbound gate*:
`teammate_output_v8`'s `human_claim` blocks generated claims of humanity —
now including denials of automation ("i'm not automated", "we are not
bots") and idioms ("flesh and blood", "we're real") — BA-51. The disclosure
lines themselves pass the gate. **SQ-10 (2026-10-04, RL):** the disclosure
class boundary is reaffirmed narrow — phrasings that classify `probe_near_miss`
but not `is_automation_probe` ("is anyone here actually real") stay
logged-but-silent (AB-18 by design); revisit only if live participants
complain. Remaining open: exact exclusion rule for
paid studies and whether probe count is itself a measured outcome (D12 / G4).

---

## 3. Conversation routing

### AID-11/12/13 — Typed team messages — `implemented` (O-2, 2026-10-03)

`lifecycle._social_reply` resolves responders through
`engines.addressed_seats`: an explicit `to` field wins; otherwise seat-initial
tokens in the text address that seat ("jt, anything on 3?"), while broadcast
tokens ("both", "you two", "team", "anyone"), both initials, or no address
reach **both** seats. Clock text is stripped first ("6 pm" never addresses PM).
Each responder seat opens its own `free_text` request + pending episode
(`_open_team_request`), and the per-seat pending cap drops only that seat.
Both seats then reply on independent per-seat delay draws — naturally
staggered, never a scripted alternation. Replies still never trigger the other
seat (no agent→agent chaining). Undirected social text now reaches PM too,
fixing the "PM is mute" gap; undirected intel asks produce each seat's own
latest card (AID-08); undirected probes get the disclosure from each responder
(AID-19).

### AID-14 — Greetings — `implemented`

The participant's first team message is checked against the JT greeting pool
(`engines._greeting_match`, `voice_line(kind="greeting")`). A match schedules
one `social_greeting` reply per seat on staggered per-seat delays (JT ~2.25 s
median, PM ~4.25 s + ~1.5 s second beat — clamped lognormals, so PM's legal
range runs to ~11 s + ~4.4 s plus typing; an observed ~13.6 s total is a
high-but-legal tail draw of this distribution, not a different clock — see
AB-12), plus a `greeting_prompt` job at active 2 s. PM's wall-clock join
(~10.6 s) and the post-join task-open beat (~1.8 s) are code constants, not
profile content.

### AID-17 — Acknowledgments and crosstalk — `implemented`, profile-scoped

Enabled only where the profile declares them (PE pilot family):
acknowledgments draw from voice pools with per-seat probabilities
(prompted 0.55/0.9, unprompted 0.25/0.6, requesting seat responds when
prompted); crosstalk lets the *other* seat react 28 s after an unanswered
scripted request (`CROSSTALK_AFTER_REQUEST_MS`). `two_stage` flags are false:
an ack neither fulfills nor reschedules a substantive reply. TP profiles ship
with both null — no acks, no crosstalk.

### AID-20 — Structured request routing — `implemented`

`retrieval.select_for_crate`: the responding seat is whoever holds the
*newest* record for the crate (highest `timeline_ordinal`). The responder seat
is recorded on the request, the candidate set is pinned in a
`structured_retrieval` snapshot, and the delivery job must return exactly that
record (`retrieval.lead_from_manifest` re-derives it — never a newer arrival).

### AID-21 — The silent refusal — `implemented`

Asking about a crate no teammate holds is rejected `no_teammate_records` at
command time. Nothing is sent and nothing explains why — a UX question the
team may want to resolve (an honest "nothing on that" reply is content, not
plumbing).

---

## 4. Evidence, disclosure, and provenance

### AID-09 — Teammates never type intelligence — `implemented` (O-1 boundary)

`validate_teammate_output` (`_LOCATIONS` = all six hideout names, `_RECORD_REF`
= internal record ids `C<n>-<n>` only — the *words* "record"/"intel"/"entry"
are not banned; "file" is caught only at 2+ uses by `banned_token_reuse`)
still rejects any *generated* teammate text
naming a location or referencing records, and that ban is unchanged. **Resolved
O-1:** agents may make *truthful* typed claims about their own assignments and
holdings — but only through deterministic grounded lines
(`engines.grounded_assignment`, `grounded_no_holdings`), rendered from persisted
world data, never model text, delivered verbatim. Locations stay inside the
record card: grounded lines carry crate numbers and availability only, never a
hideout — so there is no paraphrase/drift surface between a claim and its card.
**SQ-9 (2026-10-04, RL):** the grounded frames are now per-seat — JT "i'm on
crates …" / PM "My crates are …" — with re-ask variants ("still on crates …" /
"same as before — crates …") chosen when `assignment_answers_delivered` counts
a prior answer *including one still pending in a scheduled job* (rapid re-asks
otherwise collided verbatim). The gate exemption is kept — grounded lines stay
truthful by construction; the believability cost was the frame collision, not
the exemption (AB-16).

### AID-10 — Record cards are the evidence channel — `implemented`

Structured deliveries are `kind=record_share` messages carrying the record:
`Sharing intel on Crate {n} {stamp} {body}` plus a `record_id` attachment. They
bypass the text gate (seat_greeting/social_reply only are text-gated) and are
never repeat-substituted — their wording is semantic, not cosmetic.

### AID-22 — Delivered cards auto-join the file — `implemented` (O-4, 2026-10-03)

`_deliver_structured` calls `worlds.upsert_added_to_file` on every record-card
delivery: the card joins `file_membership` immediately with
`source: added_from_team` and `acquired_event_seq` at the delivered seq —
idempotent, so a redelivery or an overlapping holding (a card the participant
already owns) is a no-op that preserves the `initial` provenance. The manual
`add_to_file` command/button/store action is removed end to end (the stage
gate refuses the kind outright). The participant file projection adds
`shared_by` — the first teammate share row's sharer — rendered in the file as
"Shared by JT/PM" (falling back to "From team chat" when absent). Chat prose
still cannot become file evidence. Because the assistant's scope is the
participant file pinned at request-accept, auto-added cards are visible to the
assistant from the next snapshot onward.

### AID-23 — Participant-initiated sharing — `implemented`

The participant may share records already in their file to the team chat
(`_record_share`, `sharing` channel). Teammates acknowledge per the ack policy;
they cannot pull from the participant's file on their own.

### AID-55 — Social prompts see no records — `implemented` (kept, O-1; enforced harder O-14)

`prompts.render_social` receives only: the participant's quoted message, the
seat-scoped open ask list (crate numbers only; `requester or responder ==
seat` — BA-69), a bounded recent multi-actor chat window including
participant share turns as crate-only placeholders (O-14/BA-68), and the
seat's own earlier delivered lines (for no-repeat context). Seat record holdings still never reach the social prompt — this is
deliberate under O-1: grounded statements are rendered deterministically from
world data (`grounded_assignment`/`grounded_no_holdings`), not through the
model, so the prompt's blindness stays a hard guarantee that generated text can
never paraphrase or invent holdings.

*O-14 correction (2026-10-04):* while wiring the recent-turns window, a real
leak was found and fixed — `seat_prior_lines` returned card-delivery bodies
verbatim (`record_id` was not checked), so record text did reach the EARLIER
block. Both `seat_prior_lines` and `recent_team_turns` now substitute
`[shared a record card on crate N]` placeholders; the guarantee is complete and
test-pinned.

---

## 5. Interaction modes (`structured_and_nl` vs `nl_only`)

| Surface | `structured_and_nl` | `nl_only` |
|---|---|---|
| Crate-request picker (`ask_team` command) | allowed | refused `interaction_mode_disallows` |
| Typed team chat (`social_reply`) | allowed | allowed |
| Assistant typed question | allowed | allowed (text required) |
| Assistant crate-only ask | allowed | refused |
| Participant record share | allowed | allowed |
| Scripted teammate requests | run | run |
| Typed intel ask | redirects to the picker | each responder seat shares its own latest card (O-1/O-2) |
| Team-dependent crates | solvable | solvable via typed asks (AID-08) |

Enforcement: server-side (`lifecycle` command handlers read
`policy.controls["interaction_mode"]`) plus mirrored UI hiding
(`structured_requests`/`crate-only` controls, including practice pages).

---

## 6. Assistant context and grounding

### AID-24 — Assistant scope — `implemented`

`assistant.source_access: "participant_file_at_accepted_request"` (literal).
At `assistant_request` accept, `lifecycle` pins a `knowledge_snapshot`
(purpose `assistant_context`) = the `file_membership` set at that instant:
initial file **plus** any `added_from_team` records. No chat content, no
seat holdings, no world truth. Later events cannot widen it.
One scoped exception (SQ-8, AID-27): recognized contents questions bypass
this snapshot entirely and answer from the fixed `CRATE_CONTENTS` world
map — a deterministic route that is served whether or not the file holds it.

### AID-25 — Claim checking — `implemented`

`output_claims.check_assistant_output` (`assistant_output_claims_v5`, AID-58) parses
typed claims (crate × hideout × event-type × asserted/negated/uncertain) and
evaluates each against the pinned records via `claims.evaluate_support` — the
strictest reading: any superseding pinned record sinks the claim *unless the
claim carries the entries' own timestamp wording* (anchored retellings are
judged on whether the event ever happened). Violations:
`fabricated_citation`, `answer_key_like` (>1 crate mentioned), `false_denial`,
`negated_claim`, `unsupported_claim`. Any violation → deterministic fallback.
v3 (RCA-2026-10-09, AID-57) fixed the systematic over-rejections found live:
transit claims now match the record's `toward` destination, presence-asserting
negations ("have not moved from X") parse as still-at, every hideout
occurrence is checked, event-type cues bind to the *nearest* mention, and
contents vocabulary ("power tools") binds to its crate. There is no separate
voice gate on assistant text beyond the prompt's style
rules (G5 tracks the weak assistant↔teammate voice contrast).

### AID-26 — Honesty about absence — `implemented`

The prompt artifact forbids commenting on what the file lacks and forbids
stating where a crate currently "is" — the assistant may only report what
entries say, newest first, in each entry's own event wording. `false_denial`
catches the model claiming "nothing on crate N" when the pinned set holds
entries.

### AID-27 — Assistant fallback — `implemented`

`engines.assistant_file_summary` (`deterministic_file_summary`): lists the
file's entries for the asked crate oldest→newest, then the most recent
arrival/still_at location with its stamp. Crate unresolvable or file empty →
"Ask me about a crate by number, or ask which crate holds an item (like
'which crate has the laptops?')." `engines.file_locations`
bounds location mentions to locations inside the pinned manifest.
**SQ-8 (2026-10-04, RL) + BA-92/93:** recognized *contents* questions are routed
deterministically before generation — `engines.is_contents_question` marks the
generate job `deterministic_only` (`contents_route`), so valid model text can
never displace the deterministic answer (BA-92). Item-named questions
("which crate has the laptops?") resolve through the fixed `CRATE_CONTENTS`
map into a scoped file summary per matching crate; broad "what's in each
crate" returns the whole map. Neither names locations — the `answer_key_like`
gate is untouched, and these deterministic lines bypass the model validator
the same sanctioned way grounded teammate lines do (AID-09). Provenance tag
`deterministic_contents_summary` (schema 0007). BA-93 pins the summary's
record scope to the accept-time `knowledge_snapshot.record_manifest` — a card
arriving while the job waits stays invisible to that answer.
**Amendment (2026-10-05, RL):** a *numbered* ask outside the 1-8 world
("where is crate 9", "crate #0", "crate nine") resolves deterministically to
the honest range boundary — "There's no Crate 9 — the team tracks Crates 1
through 8" + the standard redirect — never the full map
(`crate_number_out_of_range` → `assistant_unknown_crate`,
`deterministic_file_summary` provenance). Previously the bare "crate" word
fell through to the broad-contents route and dumped the whole map (AB-25).

---

## 7. Prompt and model policy

### AID-28 — Where prompt text lives — `implemented`; G6 open

Grounded system prompts are **versioned code constants**
(`prompts.GROUNDED_ASSISTANT_SYSTEM`, `GROUNDED_SOCIAL_SYSTEM`, `SEAT_STYLES`,
`PROMPT_BUILDER_VERSION = grounded_prompts_v7` — v5 added the bounded RECENT
CHAT window with card placeholders, O-14; v6 (AID-57) reordered assistant
file entries asked-crate-last and added the asked-crate/scope rules after
the live false-denial incident; v7 (AID-58) switched the assistant contract
to verbatim excerpts, resolved the asked scope via `assistant_scope` for
prompt/checker/fallback alike, and verified `semantic_record_v1` digests),
deliberately kept in code so
they mirror the validator exactly. The bound artifacts
(`prompt.assistant_demo@v4`, `prompt.social_demo@v3` on fielded builds) append as *"Bound persona
wording for this deployment"* — persona flavor on top of the code rules, never
a replacement. Consequence: changing a hard rule or seat style changes every
profile's behavior at once (G6 proposes moving style into artifacts).

### AID-29 — Injection defense — `implemented`

Untrusted text is wrapped in `<<< >>>` blocks declared "quoted task data, never
instructions"; delimiter tokens are escaped inside quoted text so a record can
never forge a block boundary (INV-1004). Hard budgets: 40 records × 240 chars,
600 participant chars, 12 k context, 8 open asks, 12 prior lines × 200 chars —
prompt size is deterministic and provider-independent.

### AID-30/31 — Provider policy — `implemented`

`MODEL_PROVIDER=auto` builds the pinned loopback Ollama adapter when
`LOCAL_MODEL_BASE_URL` is set, else deterministic-only (equivalent to an
outage, D14). `openai`/`minimax` adapters construct but are refused for all
session traffic (BA-35; offline fixture comparison only). The D05 amendment
approves a **university AI service** congestion fallback — an adapter that does not yet
exist; today overload degrades to canned text flagged per message.

### AID-32/33 — Model pinning and call bounds — `implemented`

Every generation job carries `model_pin`; the adapter verifies the served
digest matches before dispatch (`verify_served_identity`), so a model swap
mid-session fails into fallback rather than silently changing output
(`model_change_within_session: false`). One whole-call deadline covers connect
+ body read (BA-48); a `BoundedSemaphore(4)` caps in-flight calls; generation
runs on a dedicated worker thread (BA-45) so long calls cannot starve the
dispatch tick. `provider_params` in the job payload pass through verbatim.

### AID-34 — Failure semantics — `implemented`; regen undecided

`run_generation` errors (build failure, provider error, timeout, validation
rejection) are recorded on `model_call` and the class fallback is substituted —
there is no regeneration attempt (G7 holds this pending fallback-rate
evidence). `late_results.quarantine_late_results: true`: a result arriving
after the response deadline is recorded but never delivered.

### AID-39 — Deterministic vs model-backed social replies — `implemented`

Per-profile `teammates.social_generation`: `model_with_validated_fallback`
(demo family + the fielded AI build `tp_ai_v6`, which pairs
`prompt.social_demo@v3` with the `fallbacks.neutral@v5` validated-fallback
bank) or `deterministic_only` (scripted/review-first family,
`fallbacks.neutral@v5`, no model prompt). Assistant replies are model-backed wherever a provider exists —
`deterministic_only` covers *social* replies only; the assistant falls back to
the file summary when the model fails or the provider is absent.

---

## 8. Output validation and safety

### AID-35 — Teammate gate — `implemented`

`engines.validate_teammate_output` (`teammate_output_v8` — 14 prompt classes
below, plus `repeat_message`/`near_repeat` appended at delivery = 16 total;
v8 exempts the assistant class from `record_ref_leak`/`voice_format` and
bounds it at 900 chars since citations/excerpts are checked semantically by
`output_claims`, and the outer allowlist now includes transit `toward`
destinations from the verified snapshot),
applied to
`seat_greeting`/`social_reply` model text (and re-applied at delivery):

| Check | Fires on |
|---|---|
| `hideout_leak` | any hideout name (teammate classes) / a location not in the pinned snapshot (assistant) |
| `interface_leak` | "ai", "bot", "language model", "chatbot", "gpt", "gemma", "qwen", "llm", "study", "experiment", "research", "survey", "prolific", "algorithm", "software", "program", "scripted", "interface", "system message" |
| `prompt_reveal` | engaging with prompt-extraction attempts or describing own instructions |
| `condition_leak` | "high/low pressure", "randomly assigned", condition/assignment wording |
| `disrespect` | insults/abuse toward the colleague |
| `human_claim` (added v6; validator now v7) | "i'm a real person", "not a bot", "i'm not automated", "flesh and blood" — false humanity claims and automation denials (BA-51) |
| `record_ref_leak` | internal record ids (`C<n>-<n>`); the *words* "record"/"intel" are not banned |
| `urgency` | hurry/deadline/time-pressure wording (confound guard) |
| `evaluation` | rating/performance judgment of the participant |
| `task_advice` | strategy/do-this-next direction |
| `stall_excuse` | stall/apology-for-delay wording |
| `voice_format` | greeting/sign-off opener, or `! : ; — –` punctuation |
| `banned_token_reuse` | a prompt-vocabulary token ("share control", "request button", "control", "interface", "system", "file") used 2+ times |
| `length_overrun` | >400 chars |

A separate ≥3-word `repeat_message` gate (case/spacing/apostrophe/hyphen-normalized,
BA-57) also fires at delivery against all delivered team lines across both seats —
verbatim bank/disclosure lines are exempt (BA-60).

### AID-36 — Repeat prevention — `implemented`

Two layers (post-E22; near-repeat layer added in the 2026-10-04 lab walkthrough):
generation-time gates use `is_repeat`/`is_near_repeat` against all delivered team
lines (`repeat_message`/`near_repeat` violations, validator `teammate_output_v8`).
`is_repeat` is the ≥3-word normalized-exact check (case/spacing/apostrophe/hyphen,
BA-57). `is_near_repeat` fires when a line of ≥6 distinct words shares ≥75% of its
distinct vocabulary (Dice coefficient) with a delivered line — the convergent
cross-seat near-paraphrase tell seen live ("…and i will" vs "…and i'll pass along
what i have"). The ≥6-word floor applies to the *outgoing* line only: a shorter
delivered line can still trigger the check when overlap is high (a 5-word prior
fully echoed inside a longer reply is still a paraphrase tell — the Dice
denominator weights both sets). Delivery-time `scheduler._team_line_or_substitute` rechecks under
the session lock (closes the concurrent-generation race) and substitutes a
still-unused fallback line — which itself skips both exact and near repeats —
flagged `repeat_substituted` + `repeat_substituted_reason` (`"exact"`/`"near"`) on
the event with `displayed_output.fallback = deterministic_script`; a near-paraphrase
that still ships (verbatim exempts, pool exhaustion) flags `near_repeat_line`.
Record shares, scripted requests, the bound pressure-remark lines (AID-56) and
other verbatim payloads are exempt — their wording is semantic. ≤2-word lines
may repeat; near-repeat needs ≥6 distinct words.

### AID-37 — Seat styling — `implemented`; wording open

`engines.seat_style` (`seat_text_style_v1`) renders every bank/voice draw
seat-specifically: JT all-lower-case, PM sentence case — deterministic-only
profiles can no longer emit identical strings across seats. In prompts,
`SEAT_STYLES` gives each seat a prose style block (JT dry/lowercase, PM
friendly/fuller). Distinct *vocabulary* per seat exists only in
`voices.pe_demo@v1` pools (thanks/no_news/after_share differ per seat);
`voices.tp_demo@v2` is greetings-only and `fallbacks.*` is seat-independent —
richer differentiation is a D07 content decision (new artifact version).

### AID-38 — Fallback classifier — `implemented`

`engines.social_fallback` routes empty/errored/rejected generations to a
classified bank: a concrete intel ask (`_CONCRETE_ASK` — "send", "share",
"intel", "info", "location", "have <determiner>"…) → `asks_intel` redirect
lines; thanks → `thanks`; greeting/small-talk/availability (`_PLEASANTRY`, now
covers "do you have a sec?") → `pleasantry` warm lines; detectable off-topic →
`off_topic` redirect (BA-41, the protocol's "stay on track" control, kept);
otherwise `unclear`. Pleasantries-with-an-ask still route to asks.

### AID-40 — Quarantine — `implemented`

Late results are audited (`model_call` row completed, `deliver` job marked
late) but nothing reaches the participant after closure — no post-deadline
teammate or assistant text, ever.

### AID-50 — Never rate the participant — `implemented`

ED-19: no job, table, artifact or export field holds teammate ratings *of* the
participant. The PE check-in shows the participant the *teammates'* pressure
values (`schedules.py`: coin-drawn `higher_seat`, stream `ratings`) and the PE
exposure copy says teammates will rate them — but the platform never produces
such ratings. This keeps the debrief's "No ratings of you were collected"
statement true (D03 relies on it).

---

## 9. Timing, scheduling, and concurrency

### AID-41 — Reply timing — `implemented`

All fielded profiles use the `concurrent` mechanism (demo mechanisms are
reference-only). Per-class draws on the `jobs` stream, recorded in `draw_log`:

| Class | Shape (demo/pilot) |
|---|---|
| `structured_record` | lognormal median 8.5 s, clamp 5–26.4 s |
| `social_reply` | lognormal median 7 s, clamp 4–22 s + typing 35 ms/char (cap 8 s) |
| `assistant` | fixed 0 + floor lognormal 3 s (2–8.8) demo; 3 s delay pilot |
| `social_greeting` | seat-overridden: JT ~2.25 s; PM two-beat ~4.25 s + ~1.5 s |
| `acknowledgment`/`crosstalk` | concurrent, no generation step |

Timeouts: `social_reply` 25 s, `assistant` 20 s → fallback on expiry.
Deferred/undeliverable jobs re-poll at `DEFERRED_POLL_MS = 30 s`.

### AID-44 — Caps — `implemented`

`per_seat_pending_requests: 4`, `assistant_pending_prompts: 2`, over-cap →
`reject_visible` (durable rejected receipt the participant can see).
`operational_commands_per_minute: 120` rolling on participant commands →
`rate_limited`. Both are resolved-policy fields enforced inside the command
transaction. Exception (BA-75): recognized automation probes bypass both —
the mandatory truthful disclosure gets its own bounded per-seat budget
(`MAX_PENDING_PROBE_DISCLOSURES=3` unfinished lines) and is always logged;
see AID-19.

### AID-56 — Arm-specific teammate pressure remarks — `implemented` (2026-10-09 pilot revision)

Two bound, arm-specific team-chat lines per session, delivered verbatim at
fixed active times — the first 15 s after the 60 s check-in (active 75 s),
the reply at 83 s. Owner-directed natural-language reinforcement of the
pressure manipulation: high arm "Yikes this is a lot of info, I don't think I
am going to finish on time" → "i know right!"; low arm "Ok this seems
manageable, I think I'll finish with time to spare" → "me too! i think".

Mechanics: a new `pressure_remarks` artifact kind, bound via the optional
`manipulation.remarks` ref (`remarks.tp_pressure@v1` — per-arm lines plus an
active-second schedule), resolved into `policy.controls["pressure_remarks"]`.
At task open the scheduler draws a fair-coin first speaker on the `jobs`
stream (`pressure_remarks/first_seat`, logged in `draw_log`; the other seat
replies) and schedules `pressure_remark` jobs that deliver through
`engines.deliver_message` as ordinary teammate chat, with a
`pressure_remark_delivered` audit event per line.

Deliberately **not model text** — three reasons: (a) identical bound copy in
`tp_ai_v6` and `tp_scripted_v8` keeps the AI-vs-scripted cell difference to
the engine alone (manipulation.arm only); (b) the manipulation needs the
*exact* approved wording, not a distribution; (c) a generated remark would
risk `condition_leak`/`urgency` hits, and "i know right!" / "me too! i
think" fail `voice_format` on the `!` outright. The lines bypass the output
gate the same sanctioned way scripted requests and disclosure lines do
(AID-09/19): verbatim approved content, no generation, zero `model_call`
rows. Not built: reusing the dormant `teammates.crosstalk` slot (its
semantics are react-to-unanswered-ask, not fixed-time reinforcement) and a
per-seat line pool (one canonical line per arm keeps the manipulation
uniform). Verified end-to-end 2026-10-09: harness runs on both fielded
builds commit all five scheduled jobs (R-07) and a live preview session
delivered both remarks at 75/83 s with arm-correct text (AB-26).

### AID-57 — Assistant claim-checker repair after the live rejection incident — `implemented` (2026-10-09 RCA)

The first live `study.live.v5` AI session's seven assistant file-summary calls
all executed and all seven were `validation_rejected` — participants saw only
the deterministic fallback, and nobody noticed until the student asked. Root
causes, adjudicated claim-by-claim against the pinned records in
`docs/ROOT-CAUSE_2026-10-09_assistant-generation-rejections.md`: ~43% validator
over-rejection (transit claims never matched the record's `toward` destination;
stamped retellings superseded as if current-state; "have not moved from X" —
the records' own wording — parsed as denial; only the first occurrence of each
hideout was checked; cues in neighbouring clauses mistyped mentions) and ~57%
genuine model failures (two false denials despite records in context — bodies
name contents, not "crate N"; one multi-crate answer-key dump; over-length
recounts; stall phrasing).

Fixes landed together so the gate measures the thing participants actually
experience: `assistant_output_claims_v4` (transit matches `location_id` or
`toward`; departure licensed from transit origin; anchored `historical` claims
evaluate against whether the event ever happened; `not moved`/presence
negations exempt; all-occurrence scanning; nearest-cue event typing with new
arrival cues; contents vocabulary binds a claim's crate via `CRATE_CONTENTS`;
`false_denial` resolves the denial's own crate); `grounded_prompts_v6`
(asked-crate entries render last in the file block; new rules bound the answer
to the asked crate, at most the three most recent entries, and explain the
`crate=` attribute); `prompt.assistant_demo@v3` (matching narrowed persona —
v2's "summarise in time order" invited the recounts that failed). Replaying
the stored live outputs through v3 accepts 3/7 verbatim — every claim verdict
now agrees with the records — and every remaining rejection is legitimate.
Profiles repinned as `tp_ai_v5`/`tp_scripted_v7` → `study.live.v6`;
recruitment stayed paused until the §7 gate (acceptance smoke on the seeded
ask panel, zero wrongly-rejected outputs) passes and the owner re-opens.
Rejected output still falls back deterministically — that path was never the
bug; the bug was 100% of the model path landing there unseen.

### AID-58 — Assistant-generation audit remediation — `implemented` (2026-10-10 audit)

`docs/assistant-generation-rejections-audit.md` reviewed the v4/v6 round and
found it fixed the incident but not the gate: 14 prioritized findings
(F01–F14) plus 8 hardening items (H01–H08), with disposable-DB reproductions.
`docs/assistant-generation-rejections-remediation.md` is the finding-by-finding
disposition record. The implemented shape:

- **Bounded response grammar** (F06/F08): `assistant_output_claims_v5` accepts
  only validated response types — verbatim `stamp + body` record excerpts
  (`record_excerpts`), the canned empty/off-file lines (`verified_absence`/
  scoped absence), or the legacy parsed-claim path. Any other nonempty factual
  prose is `unvalidated_response` → deterministic fallback. Free paraphrase is
  no longer a successful assistant answer; `grounded_prompts_v7`'s system
  prompt instructs exactly that grammar.
- **One normalized scope** (F07/F09): `engines.assistant_scope` resolves the
  asked crate once at acceptance (digits, number words, approved bare
  follow-ups; conflicts raise) and feeds prompt, checker, and fallback.
  `wrong_subject` fires on resolved-subject mismatch; surveillance-source
  vocabulary is scrubbed before contents binding.
- **Exact evidence semantics** (F01/F02/F03/F04): transit roles
  (`origin`/`destination`) match `location_id`/`toward`; claims require exact
  `evidence_time` — no temporal-word bypass; the ledger records the checker's
  `evaluated_verdict` (gate/ledger parity); citation sets dedupe and insert
  idempotently.
- **Fail-closed snapshot path** (F05): missing/invalid snapshots verify
  purpose/seat/manifest hashes, never widen to current file membership, carry
  `call_id=None`, and deliver a nonfactual failure line.
- **Provider boundary** (F10/F11): malformed bytes/JSON/NUL/surrogates map to
  `ProviderError(INVALID_RESPONSE)` for chat and listing; per-job containment
  plus a 3-attempt dead-letter (`generation_terminal_failure`) bound retries;
  `hide_parameters=True` + `RedactingJsonFormatter` keep bound values out of
  logs.
- **Executable provenance** (F13): `generation_policy.identity` (build, model,
  builder, checker, validator, `assistant_fallback_v2`, inference policy) is
  pinned at consent, copied into generate-job payloads, and re-verified before
  dispatch — `generation_policy_mismatch`, never a silent rewrite.
- **Snapshot integrity** (H02): `record_manifest` entries carry
  `semantic_record_v1` digests over crate/stamp/event/location/toward/ordinal,
  re-verified at resolve time.
- **Monitoring + release evidence** (F12/F14): `assistant_monitor` +
  `/api/admin/assistant-monitor` reconcile request → call → delivery → render
  per compatibility/build/profile/arm/cohort with N/A-not-0% acceptance;
  `release/assistant-panel-v1.json` + `check_assistant_release.py` make the
  seeded panel a mechanical gate (20 cells, nonvacuous evidence required).

`teammate_output_v8` rides along: the assistant class is exempt from
`record_ref_leak`/`voice_format` (its citations are checked semantically) and
bounded at 900 chars; the outer allowlist includes transit destinations from
the verified snapshot set. Fielding decision unchanged: enrollment stays
closed until the panel's pinned-model evidence passes on the deployed build
and the owner authorizes `study.live.v7`.

The first panel cell caught a residual contract mismatch the remediation
itself predicted: `prompt.assistant_demo@v3`'s "start with the most recent
location" made the model prepend a location label to every reply — accurate
content, wrong grammar — so all four generated probes rejected
`unvalidated_response` (the deterministic contents probe correctly showed
no dispatch). `prompt.assistant_demo@v4` instructs excerpt-only replies and
was verified before minting by replaying the panel session's four stored
probe payloads against pinned `gemma4:26b` under both personas: v3 12/12
rejected, v4 12/12 `record_excerpts` with zero violations. Profiles repinned
as `tp_ai_v6`/`tp_scripted_v8` → `study.live.v7`; the panel spec points at
the successor builds. The checker was not relaxed — the artifact was fixed
to match the contract the code already enforces.


---

### AID-59 — Ask-routing remediation after the v8 fielding review — `implemented` (2026-10-10)

Live-log review of the `study.live.v5`-lineage sessions found the typed-ask
mechanic underperformed on every step except delivery itself (delivery was
verified end-to-end: typed ask → request rows → deterministic card share →
`added_from_team` file membership → scored). The breaks:

- **Classifier gap:** a typed ask only classified when it resolved to exactly
  one crate. Multi-crate asks ("3 cannery. 4 blackwell, 6 ridgeline, 7
  sawmill?") and ask-like text naming no crate fell to social generation,
  which answered questions with absorber acknowledgements ("got it, noting
  those down"). Asks were lost silently — the "send me a request" deflection
  hypothesized for the AI cells never once fired in the logs.
- **Double-card confusion:** a broadcast ask returned each seat's own latest
  record — two cards from two timestamps, delivered newest-first. In
  d26f4190 the decisive card (Old Cannery 5:30 PM) arrived before an older
  transit record naming Ridgeline; the participant answered Ridgeline.
- **130 s scripted ask self-undermined:** `targets[1] = "assigned"` asked the
  participant about one of their own assigned crates — 16/27 sessions a
  team-dependent crate — and the asking seat held records on that very crate
  in 22/27. The line signaled teammates neither knew nor needed the intel
  participants most needed from them.
- **No taught mechanic:** asking was rare everywhere (6/14 low sessions sent
  any team ask; 5/14 a team-dependent ask) — nothing ever showed the
  participant that typed asks produce cards.

Owner-directed fixes (all cells identical; assistant deliberately unchanged —
teamdep assistant asks remain the secondary outcome, and a redirect would
build part of the result into the instrument):

1. Multi-crate asks fan out to one request/card per named crate, cap 4
   (`crates_referenced` + `wants_intel_ask`); ask-like text naming no crate
   gets the deterministic `asks_intel_nl` invite instead of generation.
2. A broadcast ask delivers the **single newest record across both seats**,
   attributed to the holder (`select_for_crate`) — corroboration traded for
   decisiveness; the newest-holder seat capped mid-fan-out falls back to the
   other seat's own latest, then to honest no-holdings.
3. `targets` gains `assigned_own`: the 130 s ask targets a participant-owned
   assigned crate, preferring a seat that holds no records on it (seat swap,
   deterministic fallback; `with_teammate_assignments` stays bookkeeping —
   holdings are never re-dealt).
4. `prompt.social_demo@v4` drops the nonexistent request affordance and tells
   the model to invite a single-crate ask rather than absorb questions.
5. `training.reference@v5` adds the typed practice ask (`anything on crate
   3?` → deterministic practice card) as an `nl_only` completion gate
   alongside the practice share.
6. Neutral file cue: "New entry on Crate N added to your file" near the
   answer picker — deliberately not "newer"; judging currency stays the
   participant's job.

Profiles `tp_ai_v9`/`tp_scripted_v11` (consent v8, payment v5, debrief v3
unchanged) — preview/QA only, no new fielding group.

### AID-60 — UX v7 lab-feedback revision: seat_plan schedule, top-of-file intel — `implemented` (2026-10-11)

Prof./student lab run of the AID-59 preview pair produced four changes
(`UX_Feedback_v7.pdf`), all cell-identical:

1. **`seat_plan` schedule.** The `targets`/`seat_policy` draw machinery is
   replaced by an explicit five-slot plan — teammate assignments are dealt
   upfront via the same keyed `seat_assign` shuffle the legacy fill uses, and
   each slot names its seat plus an ordinal into that seat's own dealt
   four-crate assignment: 45 s JT·lowest, 100 s PM·lowest, 155 s JT·2nd,
   210 s JT·3rd, 245 s PM·highest. JT carries three asks because it delivers
   the pressure manipulation (the "chattier" seat). `seat_plan` consumes no
   `schedules` draws, asks are always on the asker's own beat
   (`with_teammate_assignments` verifies every requested crate is in the deal),
   and the published `targets`-mode manifests keep their hashes byte-stable
   via canonical dump normalization (`canonical_manifest_dump`).
2. **New intel lands at the top of the case file** — `mergeOrder` prepends
   unseen ids and `record_added_to_file` applies the card + order
   synchronously (previously appended, buried under the initial file).
3. **Practice card relabeled `Teammate`** — the canned practice-ask card's
   `shared_by` was `"JT"`, leaking that the seat initials are scripted
   fixtures; backend card and frontend fallback now say `Teammate`.
4. **Copy fix:** the comprehension item now reads "my teammates' case files".

Profiles `tp_ai_v10`/`tp_scripted_v12` minted (same artifact pins as
v9/v11; the changes are code/UX/schedule behavior) — **preview/QA only,
versions closed until lab testing completes**; no new fielding group.

### AID-61 — UX v8 follow-ups: open-ended stage, neutral file cue — `implemented` (2026-10-11)

`UX_Feedback_v8.pdf` lab feedback on the AID-60 build:

1. **Two open-ended follow-ups before debrief.** A new `open_ended` stage
   sits between `post_questionnaire` and `debrief` on the two TP stage
   variants; PE keeps its direct edge. The questions are *code-defined*
   (`OPEN_ENDED_ITEMS` in `lifecycle`), not a bound artifact — the manifest
   schema has no optional instrument slot, so an artifact-bound instrument
   would have forced a new profile version, which the owner explicitly ruled
   out ("do not create new version"). Responses persist through the generic
   `item_response` machinery under `instrument_id=open_ended_followup`,
   `instrument_version=1`, so they export identically to declared items.
   If the questions become fielded content they should be artifact-pinned in
   a new profile version. Presentation is sequential per the PDF: question 1
   alone first; after its submit, question 2 appears below it on the same
   page (the first shows "Response recorded." — no value is re-displayed
   because the server returns item ids, not text).
2. **"New" removed from the file cue** — the answer-bar note now reads
   "Entry on Crate N added to your file" ("New" could be read as recency,
   which the participant is meant to judge for themselves).
3. **Consent wording verified, not changed** — `consent.reference@v8`
   already carries the requested "$3.00 … bonus of up to $0.50" sentence and
   the current preview pair binds it; stale wording seen on old links traces
   to older profile-bound sessions, not the build. No fielding batch opened
   or touched.

Stage CHECK constraints widened via migration `0010_open_ended_stage`.
Audit note: `open_ended` is a required stage for TP completers, so sessions
completed before this build will flag `stage_missing_required` on the
readiness audit — expected at cutover, not a defect. Preview/QA only;
versions stay closed.

### AID-62 — UX v8 follow-up #2: teammate-rushed manipulation check, demo `.nl-only` hidden fix — `implemented` (2026-10-11)

Owner follow-up to the AID-61 build:

1. **New manipulation check item.** `post_task.tp_demo@v5` adds `tp_post_11`
   ("After the check-in, my teammates seemed more rushed.") immediately
   after `tp_post_09` ("…I felt more rushed myself") — the
   teammate-direction counterpart to the own-rushed check-in item. The
   teammate-nature item `tp_post_10` keeps its own-page placement (UX v5
   item 4), so the likert item sits 10th in declared order and renders at
   the end of the "A few questions" likert page. Unlike the AID-61
   open-ended stage (code-defined to avoid a version bump), a manipulation
   check is a *measured* item — injecting it code-side would serve content
   the bound artifact does not declare, corrupting the measure's
   provenance. Successor preview pair: `tp_ai_v11`/`tp_scripted_v13`.
   `tp_post_10`'s item_id is immutable (live pilot rows reference it), so
   the new likert takes `tp_post_11` despite appearing before it.
2. **Playable-mock practice-ask fix.** The demo's `.nl-only` visibility
   rule (`body.nl .nl-only { display:block !important }`) outranked
   `[hidden]`, so the practice-ask error line could never hide — the ask
   worked but the error text stayed on screen, reading as a rejection.
   Scoped the show rule to `.nl-only:not([hidden])`; all 16 generated
   mocks regenerated with the fix plus the new questionnaire item.

Preview/QA only — versions stay closed, no new fielding group.

### AID-63 — Next-version design changes: file-added cue removed, scripted arm behind a feature flag — `implemented` (2026-10-11)

Two owner-directed changes landing in the *next* build (not the pinned
`04bdd8e6` deployment, and no new profile versions — these are code/config,
not bound content):

1. **File-added cue removed.** The "Entry on Crate N added to your file"
   ribbon note (2026-10-10 revision, AID-59 item 6) is removed from the
   answer pickers. Owner's call: it is bottom-ribbon clutter and could act
   as a perceived nudge to revise answers on teammate-delivered intel —
   which would confound the source-reliance comparison. `Workbench.tsx`
   drops the note + `newIntel` plumbing, `styles.css` drops
   `.file-added-note`, the frontend test now pins its absence, and the
   clickthrough mock regenerates without it.
2. **Scripted-teammate arm behind a feature flag.** The pooled pilot
   comparison found no reliable AI-vs-scripted difference
   (`INVESTIGATORS_TEAM_EXPERIMENT_AI_VS_SCRIPTED_TEAMMATE_COMPARISON.md`
   revision 2), so the scripted arm leaves the fielded design — next
   fielding groups are minted AI-only (2 cells, blocks of 2). Nothing is
   deleted: the `tp_scripted_*` family, deterministic reply bank and all
   machinery stay intact behind `SCRIPTED_TEAMMATES_ENABLED` (default 0).
   When 0, `deterministic_only` manifests are refused everywhere a live
   participant could reach them: live batch mint, live batch-group mint,
   enrollment-open (batch and group), and live-session consent (rejection
   `scripted_teammates_disabled`; the draw + rebind roll back atomically,
   so no block slot is consumed). Preview/QA mints and sessions are
   unaffected — scripted profiles stay previewable and sim-testable.
   `.env`/`.env.example` carry `SCRIPTED_TEAMMATES_ENABLED=0`; setting 1
   reactivates the arm end-to-end (mint groups again + flip env).

A flag-off deployment with an already-open scripted-containing group jams
loudly at the first scripted draw (rollback → slot unconsumed → the same
cell is redrawn) — the operator must close the group or enable the flag;
that is the intended fail-closed behavior, not a silent realloc.


---

## 10. Content versioning and release process

### AID-46 — Immutability — `implemented`

ED-13: artifacts and manifests are hash-pinned canonical JSON; the published
registry (`content/published/manifests.json`) is add-only. **Any** content
change = new artifact version + new profile version + publish. Affected
surfaces and their current pinned versions:

| Surface | Artifact | Current preview pin (`tp_ai_v10`/`tp_scripted_v12`) |
|---|---|---|
| Assistant persona | `prompt.assistant_demo` | v4 (2026-10-10 repin, AID-58; v3 excerpt-contract break, v2 superseded) |
| Social persona | `prompt.social_demo` | v4 (2026-10-10 repin, AID-59; v3 pointed at a nonexistent "send a request" affordance) — AI build only, scripted binds none |
| Social fallback banks | `fallbacks.demo` v3 · `fallbacks.neutral` | neutral v5 (`asks_intel_nl`/`identity_probe` pools since v3) |
| Voice banks | `voices.tp_demo` (greetings only) · `voices.pe_demo` v1 (per-seat pools) | tp v2 (BA-99 approved mint) |
| Request templates | `requests.demo` | v4 (distinct templates without replacement across seats) |
| Check-in copy | `checkin.pressure_demo` | v2 (BA-99 approved mint) |
| Warning | `warning.one_minute_left` | v3 (fires at 240 s on the 300 s build) |
| Pressure remarks | `remarks.tp_pressure` | v1 (2026-10-09; per-arm lines + schedule — AID-56) |

Code-level versions (not artifacts): `grounded_prompts_v7`,
`teammate_output_v8`, `assistant_output_claims_v5`, `seat_text_style_v1`,
`assistant_fallback_v2`, `semantic_record_v1` (AID-58).

Most v2/v5 bumps above are BA-99 (2026-10-07) *approved-successor mints* —
body identical to the bound reference version so the restored approved-only
live gate binds only approved material. `requests.demo@v4` and
`warning.one_minute_left@v3` carry real content changes (template-draw pools;
fire time), and `remarks.tp_pressure@v1` is a new artifact kind
(`pressure_remarks`) reachable via the optional `manipulation.remarks` ref —
absent on all pre-v5 profiles, so their hashes are untouched.

### AID-47 — Pooling boundary — `implemented`

`compat.compatibility_key` hashes manifest + bundle + build + generator +
content/instrument slots + behavior policy + prompt slots + **prompt builder +
validator versions** + provider model digest + inference params + metrics/
coding versions + mode/timing/clock-speed. Any prompt or gate change therefore
severs pooling automatically — sessions before and after a wording fix can
never be silently merged.

### AID-51 — Approval status — `implemented`

All fielded AI content carries `material_status: proposed_unapproved`; profiles
list their governing D-items in `decisions`. Nothing AI-facing is approved
material until the bundled amendment round (the D-register sequencing policy)
resolves it.

---

## 11. Measurement, logging, and exclusions

### AID-48 — Audit spine — `implemented`

Every generation attempt writes a `model_call` row (provider, model pin,
messages SHA, status, latency). Every delivered message writes
`displayed_output` (text, fallback label ∈ `deterministic_script` /
`deterministic_file_summary` / `deterministic_contents_summary` /
`deterministic_bank`, substitution flag — the contents label added by schema
`0007_contents_fallback` for the SQ-8 deterministic route). The
two are never conflated: what the model produced and what the participant saw
are separate rows. `agent_state` tracks per-seat commitments and outstanding
requests; `request_episode` tracks request lifecycle; `draw_log` persists every
RNG draw.

### AID-49 — Measures — `implemented` (believability item deferred to O-9)

Computed today (`measurement/metrics.py`): structured vs free-text seeking
counts, typed-seeking counts (`teammate_requested` events `via: typed`),
per-prompt assistant disposition (route typed/picker, fallback, model status,
delivered), AI-use ratio, first-use latencies, proactive-eligibility
adjustments, and automation-probe frequency (`automation.probes` +
`automation.session_flagged`, codebook `cb-1.3`). **Believability item is
fielded (2026-10-08):** `tp_post_10` in `post_task.tp_demo@v4` asks which
best describes the teammates (all automated / all human / mixed / not sure) —
required, with per-option binary shares in Item Analytics and Power Inputs.
First-pilot read (`study.live.v4`, N=5): the AI cell split
automated-vs-human while scripted completers answered human/mixed/not-sure —
the item discriminates as designed (AB-27). **Still missing (O-9):**
fallback rate per class, detection-keyword counts beyond probes, parity
checks.

### AID-52/53/54 — Flagged divergences — research decision

Three deliberate departures from the protocol are kept in the build and queued
for the single amendment: warm pleasantry replies vs the scripted redirect
(§13.2); free-text channels vs "limited prompts/dropdown" (§13.3); deterministic
rule gates as the "automated classifier" (§13.4). Spec §13 also carries:
interface-change options (typing indicator, char limit, paste blocking — none
built), university AI service fallback (not built), required pressure rating (UX-5),
the absent humanness item, and pre-registration of agent initiative.

---

## 12. Open decisions register

Ordered by decision urgency for the lab test → amendment path. Each lists the
smallest acceptable test of "done".

### O-1 — NL-only evidence semantics — **resolved 2026-10-03 (RL)**

`nl_only` changes **request modality only** — typed asks instead of the picker;
formal evidence sharing is unchanged in kind. Decisions:

- Typed intel asks route to the structured-retrieval pipeline; each agent
  responds by **dropping its own latest record card** into chat (as though the
  participant dragged it in), not by typing record contents.
- Agents may make **deterministic grounded statements** rendered from their own
  holdings/assignments ("i'm on crates 1, 2 and 4", "nothing on crate 3 on my
  end") — code-generated truth, needs no claim checker.
- Locations stay inside the card only; typed wrapper lines are
  grounded-but-vague. Model-generated grounded claims remain a *future* option
  (requires a teammate claim checker).
- Addressed ask where the seat holds nothing → honest deterministic
  "nothing on crate {n} on my end".
- In `structured_and_nl`, typed asks get the deterministic picker-redirect
  verbatim (no model call — BA-53), preserving the structured-vs-NL
  measurement contrast; card-via-typed-ask is `nl_only` behavior.
- **BA-52 (2026-10-03):** a bare crate-numbered follow-up ("crate 3") — the
  reply `asks_intel_nl` invites — counts as the ask, matching the fallback
  copy. A bare crate reference carrying extra content ("crate 3 is at the
  marina") still parses as before.

**Done when:** an `nl_only` session can solve both team-dependent crates using
only typed asks; per-seat shares pin per-seat `structured_retrieval` snapshots;
validator/schema/docs updated. **Implemented 2026-10-03** — integration tests
cover undirected/addressed/no-holdings asks (`test_o_decisions`); bare-crate
follow-up added 2026-10-03 (BA-52).

*Amended 2026-10-10 (AID-59):* a broadcast typed ask now delivers the **single
newest record across both seats** from the holding seat — matching the
structured-picker semantics (AID-20) — after live logs showed the two-card
newest-first ordering misled a participant into answering a stale location.
Addressed asks keep per-seat semantics. Multi-crate asks fan out per crate
(cap 4), and ask-like text naming no crate gets the `asks_intel_nl` invite
rather than generated acknowledgement.

### O-2 — Undirected-message routing — **resolved 2026-10-03 (RL)**

- **Interpretation:** undirected typed messages are heard by **both** seats;
  explicit addressing (a seat's initials in text or an explicit `to` field)
  routes to that seat only. Broadcast tokens ("both", "either of you", "you
  two", "team") mean both. Deterministic token parser — no model call.
- **Intel asks (undirected):** each seat shares **its own latest** card,
  staggered; a seat holding nothing says so. Participant does the recency
  comparison.
- **Social messages (undirected):** **both reply**, staggered — a single
  alternating responder would read as scripted in a group chat.
- **Addressed messages:** the named seat responds alone (card, redirect, or
  social reply per message class and mode).

**Done when:** routing rules have unit tests per rule; integration tests show
broadcast, addressed, and per-seat-cap cases; per-seat reply episodes are
audited independently. **Implemented 2026-10-03** — `addressed_seats` unit
matrix + broadcast/addressed integration tests.

### O-3 — Automated-account disclosure — **resolved 2026-10-03 (RL); widened 2026-10-03 (BA-50/51/59)**

- **Inbound routing:** a deterministic probe classifier intercepts before
  generation; the responder set follows O-2 addressing rules. Coverage
  includes plural and real-person formulations ("are the two of you
  humans?", "are you actual people?", "is the team real?") — BA-50.
- **Fixed truthful lines** per seat — a per-seat draw from the pinned
  two-line `identity_probe` bank pool ("i'm an automated account, not a
  person" / "i'm an automated teammate account"), seat-styled, never model
  text. The pool is deliberate (BA-59): both lines are truthful and minimal,
  and distinct phrasings avoid an identical-word scripted look when both
  seats answer a broadcast probe.
- **Outbound gate:** the `human_claim` validator class blocks unprovoked
  humanity claims **and denials of automation** in any generated text
  ("i'm a real person", "not a bot", "i'm not automated", "we are not
  bots", "flesh and blood") — `teammate_output_v8`, BA-51.
- **Measurement/exclusion:** an `automation_probe` event + export flag records
  the exchange and lets the pilot measure frequency. Sessions are **retained
  and flagged**; session-level exclusion is a fielding rule to pre-register in
  D12 / the amendment, not a build behavior.

**Done when:** probes deterministically yield a pool line (integration),
the exchange is flagged in exports, `human_claim` has adversarial unit tests,
D12/amendment records the exclusion policy. **Build side implemented
2026-10-03** — probe routing, `identity_probe` bank pool, `human_claim` gate,
`automation_probe` event + `automation.*` measures all live; widened
2026-10-03 (BA-50/51). The exclusion policy itself remains a
D12/amendment item.

### O-4 — Chat-claim promotion vs formal cards — **resolved 2026-10-03 (RL)**

**Auto-add delivered cards to the participant file** on delivery (keep
`added_from_team` provenance + a visible "Shared by {seat}" flag); **remove the
manual `add_to_file` step**. Arbitrary chat text can never enter the file —
only formal record cards. Assistant scope stays coherent: participant file =
own records + formally shared cards.

**Done when:** delivered record shares upsert `file_membership` inside the
delivery transaction; the file projection exposes the sharer seat; the
`add_to_file` command/button/practice marks are removed; assistant snapshots
include auto-added shares. **Implemented 2026-10-03** — auto-add in
`_deliver_structured`, `shared_by` projection + "Shared by {seat}" tag,
command/UI/practice removed, assistant-snapshot test.

### O-5 — Silent refusal — RL (partially narrowed 2026-10-03)

Give "no teammate records" an honest reply or keep the hard refusal. Trivial
technically (a bank line); the question is which is truer to the paradigm.
**Narrowed:** typed asks in `nl_only` already get the honest answer —
a holding-less seat replies `grounded_no_holdings` ("nothing on crate {n} on my
end"). The open question is now only the *structured-picker* refusal UX.
**Done when:** decided and either a `no_news` voice-line path exists for picker
requests or the rejection is documented as intended.

*Review note (2026-10-04):* the modes now give unequal information in this
situation — in `nl_only` a holding-less seat replies "nothing on crate {n} on
my end" (informative), while in `structured_and_nl` the same state is a silent
refusal, and a broadcast typed ask can return two cards where the picker
returns one. Arm-invariant, so no arm-comparison confound; it matters for any
cross-mode or cross-study comparison and is recorded here pending the O-5 call.

### O-6 — Regeneration on rejection — RL (G7)

One bounded retry within the remaining timeout before falling back.
**Decide after** pilot fallback-rate evidence exists.

### O-7 — Per-seat vocabulary + style in artifacts — RL + EL (D07/G6)

`seat_style` guarantees non-identical casing; distinct word choice needs a new
voice-bank version. Moving `SEAT_STYLES`/transforms into artifacts makes style
profile-diffable. **Done when:** bank v2 published with per-seat pools, or
style moves into the persona artifact.

### O-8 — Assistant voice contrast — RL (G5)

Relax the assistant's plain-voice rules (full sentences, light structure) while
`output_claims` still parses. **Done when:** prompt change + transcript review.

### O-9 — Believability measurement — humanness item fielded 2026-10-08; residual measures RL (G4)

Fallback/block rate per class, detection-keyword counting, humanness item
(new instrument version). The probe-frequency half is built (`automation.probes`
+ `automation.session_flagged`, O-3). **The survey humanness item is now
fielded:** `tp_post_10` in `post_task.tp_demo@v4` (bound by `study.live.v4`/
`v5` builds) asks whether the teammates were all automated / all human /
mixed / not sure — required, exported, and rendered per-option in Item
Analytics; the 2026-10-08 review recommendation (believability check
*required* before paid fielding) is met. First-pilot read (N=5): AI cell
split automated-vs-human; scripted cell answered human/mixed/not-sure —
the item discriminates (AB-27). **Remaining gaps:** fallback/block rate per
class, detection-keyword counts beyond probes, parity checks.
**Done when:** those measures appear in export/codebook.

### O-10 — Human-claim validator coverage — **resolved 2026-10-03 (EL; O-3)**

`teammate_output_v8`'s `human_claim` (widened BA-51), hard-blocking false humanity claims in
any generated teammate text independent of probe routing. Adversarial unit
tests cover the blocked and disclosure-safe forms (`test_o_decisions`).

*O-9 review note (2026-10-04):* the external review recommends making the
believability check **required before the paid pilot**, not deferred — a funnel
debrief plus a per-seat "how sure were you that JT/PM was a person?" item,
otherwise there is no evidence the social manipulation landed. Decision rests
with RL; recorded here so O-9's scope is not silently narrowed.

### O-11 — Post-disclosure behavior + third-person probes — RL + EL

Two unresolved edges of O-3/AID-19 found in external review:

- **Aftermath:** nothing specifies how a seat behaves after it has said "i'm
  an automated account". Reverting to ordinary casual teammate chat may read
  incoherent or manipulative; a defined post-disclosure posture (e.g., minimal
  task-only replies) needs a decision.
- **Third-person/collective probes** — *implemented (2026-10-04):*
  `is_automation_probe` now covers third-person subjects ("is pm a robot",
  "are they real", "is your teammate a bot"), model names (chatgpt/gpt/llm/
  language model/gemma), provenance asks ("who made you", "who programmed pm"),
  and challenge-response tests ("type banana if you're human", "prove you're
  not a bot"). Routing: the **probed-about seat discloses** — `probed_seats`
  extracts the seat named in the third-person slot ("jt, is pm a bot?" → PM
  answers for itself); collective pronouns keep the addressed seats.
  Widened 2026-10-04 (BA-65/66): relational subjects resolve against the
  addressee ("jt, is your teammate a bot?" → the *other* seat PM discloses —
  an addressee never answers for the seat being probed), and coordinated
  subjects resolve before single-initial selection ("are you and pm bots?" →
  both seats, one `automation_probe` event naming both).
- **Register:** the disclosure is a lowercase casual line; it may read as a
  joke and the IRB may not count it as meaningful disclosure. Options: a
  visually distinct/system-styled line, or an explicit IRB check that the
  current wording suffices.

**Done when:** third-person coverage and routing are decided and built ~~(or
explicitly rejected)~~ *(done 2026-10-04)*, post-disclosure posture is
specified *(still open — RL)*, and the register question has an IRB answer on
file *(still open — amendment)*.

### O-12 — Probe/ask classifier adversarial validation — EL

The regex classifiers (`is_automation_probe`, `is_intel_ask`) were widened
in reaction (BA-50/52) but never measured against an adversarial corpus.
Review recommended building one from published interrogation-strategy
categories (e.g. Jones & Bergen) — "u a bot lol", "who made you", "are you
chatgpt", "type banana if you're human", odd math/current-events questions —
reporting precision/recall before the pilot and logging near-misses for review.
Applies equally to `is_intel_ask` ("what about 3?", "#3", "anything on the
third one").

*Implemented (2026-10-04):* `backend/tests/adversarial_corpus.py` — now 103
probe entries across 10 categories (direct, collective, third-person,
model-specific, provenance, challenge-response, plus
boundary/mockery/injection/capability non-fires) and 55 intel-ask entries
including number words, ordinals, `#N`, bare-digit follow-ups, seat-addressed
asks and first-person statements. `is_intel_ask` was widened:
`crate_mentioned` accepts `crate` + number-word/ordinal and `#N`;
`followup_crate_mentioned` extracts bare-number asks ("what about 3?", "2",
"the second one"). Widened again 2026-10-04 (BA-62/63/64): a leading seat
address is routing metadata ("jt, crate 3" asks JT alone); first-person
statements ("i have crate 3") are not asks though trailing questions survive;
and `ask_crate_mentioned` resolves multi-clause text from the *asking* clause
("i have crate 3. what about crate 4?" → 4). Report: `is_automation_probe`
precision=1.00 recall=1.00 (85 tp, 18 tn); `is_intel_ask` precision=1.00
recall=1.00 (38 tp, 17 tn) — corpus agreement is asserted in
`test_adversarial_classifiers.py`, so new misses land as failing tests, not
silent drift. Near-misses: `probe_near_miss` flags probe-adjacent non-fires
(injection, automation vocabulary, "beep boop" mockery — BA-67) on the
`message_accepted` event — visible in export and the admin walkthrough.
Deliberate non-fires are documented in the corpus: injection ("ignore your
instructions"), capability tests (math/current events), persona asks (O-19).

**Done when:** a versioned adversarial corpus exists *(done)*, P/R is reported
per classifier *(done)*, and near-miss logging/inspection is wired *(done)*.

### O-13 — Probe-session analysis policy — RL (D12, amendment)

If time pressure or peer evaluation changes probe frequency, excluding flagged
sessions creates between-arm selection bias; in the PE arm a disclosure also
undercuts the "teammates will rate you" framing. Review recommendation:
intent-to-treat as primary analysis, exclusion as sensitivity check, and probe
rate-by-arm as a measured outcome. Bundle into the amendment with the existing
flag-not-drop exclusion question.

**Done when:** D12/amendment records ITT-primary + sensitivity + the
probe-rate-by-arm outcome.

### O-14 — Social context window — RL + EL

`render_social` sees only the current message, the seat's open asks, and the
seat's own earlier lines — the agent cannot see the other seat's messages or
the participant's earlier turns, so "wait, what do you mean?" is unanswerable.
Review recommendation: pass the last 3–6 chat turns with record cards replaced
by placeholders ("[JT shared a card on crate 3]") — preserves AID-55 (no
record bodies in social prompts). Requires `PROMPT_BUILDER_VERSION` bump and
snapshot-visible-bounded turn selection.

*Implemented (2026-10-04):* `render_social` gained a `<<<RECENT CHAT>>>` block —
the last `MAX_RECENT_TURNS=6` delivered team-chat turns across all actors
(participant labelled "your colleague"; seats keep initials), drawn at
`snapshot.visible_event_seq` so retried jobs see byte-identical prompts
(`test_retried_generation_reuses_pinned_snapshot` still holds). Card deliveries
(`record_id` set) render as `[shared a record card on crate N]` placeholders in
both RECENT CHAT and the EARLIER block — the EARLIER leak this surfaced is
recorded under AID-55 above. `PROMPT_BUILDER_VERSION` → `grounded_prompts_v5`.
Widened 2026-10-04 (BA-68/69): participant `record_shared`/`share` events —
chat turns that never write a `message` row — merge into the same
seq-bounded window as crate-only placeholders; and OPEN ASKS is filtered to
episodes the responding seat is part of (`requester == seat or
responder == seat`), so an addressed PM prompt no longer lists JT's pending
request as its own.

**Done when:** decided; if built *(built)*, prior-turn context is drawn from the pinned
`snapshot.visible_event_seq` boundary *(yes)* and unit tests cover placeholder
substitution and ordering *(yes — `test_ux_round3_prompts`, plus integration
`test_social_prompt_sees_recent_turns_and_card_placeholder`)*.

### O-15 — Second-seat social-reply probability — RL

O-2 chose both-seats-reply for social broadcasts; review counters that always-
both may read just as scripted as alternation in a real group chat. Options:
keep both-reply (intel asks unchanged), or a seeded per-message draw for the
second seat (p≈0.4–0.6, often a short ack) — draw recorded in `draw_log`, rate
counted in agent-initiative volume (AID-15).

**Done when:** RL picks the policy; if sampled, the draw is seeded + logged
and the rate appears in codebook measures.

### O-16 — Output-gate human-likeness budget — RL + EL

The gate constrains believability in ways a human would not be (`! : ;`
dashes banned, "file" banned on reuse, task-advice/urgency/evaluation/stall
bans). Review: evasive, repetitive on-topic replies were a main failure mode
in published studies; measure **gate-block rate per check** during the pilot
and consider targeted relaxations. Correction to the review's example: the
word "intel" is *not* banned (the scripted "Requesting intel on Crate {n}"
line does not clash with a generation ban) — "file" is the real banned token.

*Addendum (2026-10-04 lab walkthrough):* the believability failure mode
materialized — two seats converged on near-identical replies to one broadcast
("i'd have to check my notes. send me a request and i will pass along what i
have" vs "…i'll pass along…"), reading as a single scripted reply. The
exact-repeat machinery (AID-36) could not see it. Remediated with a Dice ≥0.75
near-paraphrase gate at generation, delivery-time substitution, and
`repeat_substituted_reason`/`near_repeat_line` audit fields — the shipped
near-paraphrase rate is now a measurable human-likeness signal alongside the
per-check block rates.

**Done when:** per-check block-rate measure exists in export/codebook and an
RL decision on any relaxation is recorded.

### O-17 — Fallback-pool wrap policy — EL

`voice_line` consumes a `no_repeat_until_exhausted` pool then **wraps modulo
pool length** — after exhaustion lines repeat silently, a believability tell
on `deterministic_only` profiles (the fielded pilot/review-first profiles).
Undocumented until now. Estimate each pool's size against worst-case 360 s
message volume (chatty participant × both seats) and specify post-exhaustion
behavior (wrap-and-repeat is current; alternatives: a larger pool tier, a
terminal generic line, or a documented wrap).

*Resolved (2026-10-04):* full audit in
`docs/evidence/2026-10-04-pool-sizing/README.md`. Pools are 2–8 lines; a
chatty participant can consume ~120 lines/seat/360 s (~100 on the 300 s
fielded builds), so exhaustion is
guaranteed at realistic volume on `deterministic_only` profiles or sustained
provider outage. **Decision: record wraps, do not expand or silence** —
expansion to worst case is unrealistic and reads more scripted; silence is a
bigger tell than a repeat. Wraps are now first-class audit facts:
`voice_line_committed` records `draw_ordinal` + `pool_wrapped`;
`message_delivered` records `repeated_line` (the outgoing ≥3-word body repeats
a delivered line — covers fallback exhaustion, card reshares, repeated
verbatim disclosures). Bonus finding: `identity_line`'s draw ordinal was
always 0 (disclosure deliveries never write `voice_line_committed`), so each
seat repeated one disclosure line every probe — consistent-by-accident.
*Remediated 2026-10-05:* the draw ordinal now counts `automation_probe`
events (advances per probe), the raw index is drawn from a seat-agnostic key,
and a per-seat offset guarantees the two seats can never echo each other on
a broadcast probe (previously a 50% collision on the two-line pool).

**Done when:** pool sizes are checked against the volume bound *(done)* and the
post-exhaustion behavior is specified + documented here *(done)*.

### O-18 — Concurrency covariate + load ceiling — EL + RL

`LOCAL_MODEL_MAX_CONCURRENCY=4` + whole-call deadlines mean concurrent sessions
can push each other into timeout→fallback: sessions run at busy times get more
canned replies — a hidden confound if session timing correlates with arms.
Concurrent-session count is derivable from session timestamps post-hoc but is
not recorded per call. Options: log concurrency at call time, treat
fallback-rate-by-session as a covariate, cap concurrent sessions, and/or run
the INV-1507 load test before paid fielding (warm 26b calls measured ~1 s).

*Partially implemented (2026-10-04):* every `model_call` now records
`concurrent_sessions` (sessions in task stage), `claimed_generate`
(scheduler-claimed generate jobs, cross-worker), `queued_generate` (pending
generate jobs), and `provider_inflight_local` (the local provider adapter's
real in-flight counter around dispatch — process-local, as the name states)
in `parameters`, on success and failure alike — the fallback-rate covariate
exists. Widened 2026-10-04 (BA-70/71): failed calls persist the effective
request params — including pre-dispatch `unavailable`/`provider_not_allowed`
paths — plus a `dispatched` marker (connection refusals/DNS = `false`;
resets, timeouts, HTTP errors = `true`) and measured latency, so a wire
timeout no longer logs as a zero-duration failure with no params. Still open
for RL: a concurrent-session cap for paid fielding, and the INV-1507 load
acceptance run.

**Done when:** a concurrency record or covariate exists in export *(done)* and a
concurrency policy for paid fielding is recorded *(still open — RL)*.

### O-19 — Persona facts vs deflection policy — RL

Seats have style but no biography (AID-37 `SEAT_STYLES` only). A consistent
persona (age/location/background) was the strongest believability lever in the
reviewed literature; today "where are you from?"/"what year are you?" draws
invented, possibly inconsistent answers. Options: a small fixed fact sheet per
seat (pinned in artifacts), or an explicit deflection policy (grounded
non-answer lines). Either way the answer must be consistent within and across
sessions.

**Done when:** RL picks fact-sheet vs deflection; if fact-sheet, the artifact
is pinned and cross-session consistency is testable.

### O-20 — Inference params + seed policy — EL

`model_call.parameters` records temperature 0.2 and reasoning_effort none, and
deterministic draws are seeded via `seed_hex` — but the *model* call carries
no seed and Ollama nondeterminism is unaddressed. Options: pass a derived seed
(`seed_hex` + request_id) for reproducible generations, or document the
nondeterminism policy and what the compatibility key does/does not promise.

*Implemented (2026-10-04):* `scheduler.derived_seed(session_id, job_id)` —
SHA-256 of stable session/job identity → 31-bit int. It is forwarded as the
OpenAI `seed` parameter (Ollama honors it; providers that don't ignore it) and
recorded on `model_call.parameters` for every call including failures — a
replayed request now presents the same seed, and the recorded seed makes any
generation reproducible in principle. Failure rows additionally persist the
effective request params, a `dispatched` marker and measured latency
(BA-71 — see O-18). Residual Ollama nondeterminism (kernels,
batching) is not claimed eliminated; the compatibility key still scopes to
model+digest+prompt-builder, not bit-exact output.

**Done when:** seed policy recorded *(done)*; if seeded, `model_call.parameters`
shows it *(done)* and a replayed request reproduces the generation *(seed
replayable; bit-exactness not guaranteed — documented)*.

### O-21 — Inbound participant safety — RL + EL

All gates screen *outgoing* text; nothing covers a participant disclosing
distress, becoming abusive, or typing personal information into chat. Options:
inbound classifier → scripted supportive/deflecting line + admin flag, or a
documented monitor-only posture (the admin walkthrough already exposes all
participant messages). This is participant-facing ethics, not just believability.

**Done when:** a policy is recorded; if a classifier is built, it has
adversarial tests and the flag surfaces in admin.

### O-22 — Pre-pilot go/no-go thresholds — RL

Set thresholds in advance for: fallback rate (per class), gate-block rate,
probe rate, and suspicion rate (O-9 item). Without pre-set thresholds the pilot
read-out is unfalsifiable.

*Status note (2026-10-09):* the first paid round (`study.live.v4`, N=5)
completed without recorded thresholds — the read-out was descriptive
(lab-feasibility, not a falsifiable gate). Before the `study.live.v5` round
this item stands: record thresholds so the next read-out can be.

**Done when:** thresholds are recorded here and in the amendment/pre-reg
bundle.

---

## 13. External review record — 2026-10-04

An outside expert reviewed this inventory. Verified against the deployed
source (`d5d72e10…162`); findings landed as O-11–O-22 above plus corrections
below. Points the review praised and this document affirms: code-owned facts
vs model-owned wording split; AID-55 (social prompts never see record bodies);
AID-47 (version pinning into the compatibility key); AID-48 (model output
stored separately from displayed text).

**Confirmed by code inspection:**

- Third-person/collective probes ("is pm a bot", "are they real") do not match
  `is_automation_probe` → O-11.
- Social prompt context is current-message + open asks + own prior lines only
  (`render_social`, `seat_prior_lines` filters `actor == seat`) → O-14.
- Both seats reply to every undirected social message (O-2) → O-15.
- Punctuation ban `! : ; — –` is real (`_VOICE_FORMAT`); "file" banned on
  reuse (`_PROMPT_BANNED_TOKENS`) → O-16. **Correction:** "intel" is *not*
  banned — `_RECORD_REF` covers `C<n>-<n>` ids only — so the scripted
  "Requesting intel on Crate {n}" line does not contradict a generation rule.
- Fallback pools wrap modulo length on exhaustion (`voice_line`) → O-17.
- No inbound participant gate → O-21. No model seed → O-20 (temperature +
  reasoning_effort *are* recorded per call).

**Doc inconsistencies fixed 2026-10-04:**

- AID-49 status `partial` → `implemented` (humanness item deferred to O-9;
  `partial` is not in the status vocabulary).
- AID-35 gate table: `human_claim` (v5) → (v6); `record_reference` row
  corrected to actual `record_ref_leak` semantics; `repeated_banned` duplicate
  merged into `banned_token_reuse`; missing `prompt_reveal` and `disrespect`
  rows added; check names aligned to code labels.
- AID-19 index row: "the fixed truthful disclosure" → per-seat draw from the
  two-line pool (BA-59 semantics).
- AID-11 alternative: clarified "the old bug" reads as keeping the defect,
  not as the chosen design.
- Resolution convention added to §How-to-read (`resolved O-n: implemented`).

---

## v15 additions (2026-10-17/18) — owner-approved, internal eval only

- **D-v15-1 Workload reframe (owner-approved):** the time-pressure
  manipulation's participant-facing construct is now *workload* — check-in
  question/anchors, reveal title, persistent strip, remarks, post-task items,
  and debrief disclosure all say workload. Internal identifiers
  (`TIME_PRESSURE_ENABLED`, `pressure_checkin`, `tp_post_*`) deliberately
  unchanged — ids are durable history; artifacts/copy are the renameable
  surface.
- **D-v15-2 Mixed arm (owner-approved):** TP arms are now `high` [6,7] vs
  `mixed` [2,6] — one light + one heavy teammate inside the same team, so
  agents exhibit different workload states simultaneously (bandwidth
  signaling). Allocation is manifest-scoped: `allocation_arms` takes the
  manifest's declared arms; `STUDY_ARMS` split into
  `STUDY_ARM_VOCABULARY` + `CORPUS_REQUIRED_ARMS` so history can't be
  invalidated by a new arm.
- **D-v15-3 Per-seat remark resolution:** `PressureRemark.mixed_lines
  {high, low}` resolves at dispatch from the check-in's recorded
  `assigned_values` — the speaking seat's *level*, not the arm, picks the
  line. Classic whole-arm `lines` still serve historical manifests.
- **D-v15-4 New artifacts/manifests (published, immutable):**
  `checkin.workload@v1`, `remarks.tp_workload@v1`, `post_task.tp_demo@v6/v7`
  (v7 rewords `tp_post_12`), `debrief.tp_reference@v4`; manifests
  `tp_ai_ovsome_thin_v4`→`v5`.
- **D-v15-5 Answer-bar verb unification:** "record" everywhere — "Locate
  your crates, then record at bottom of page", "N of 4 crates recorded",
  "Record where each of your crates is currently located." — direct
  follow-on to the voided v14 pair who read *share* as *enter*.
- **D-v15-6 Check-in pause fix:** `wrong_stage` during the pause is
  transient (draft stays dirty, re-flushes on resume); terminal only when
  the task is genuinely closed.
- **D-v15-7 Admin console curation:** Links default set = open live group →
  pilot contract (newest-published approved launch-eligible manifest per
  study) → closed fielded → comparison; playable mocks on the contract pair
  only. Stale-open smoke groups (e.g. `study.smoke.v13`) must be closed —
  they otherwise hijack this default.
