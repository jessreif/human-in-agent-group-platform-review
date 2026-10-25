# Investigators — AI-Agent Behavior Register (lab observations)

**Status:** living register of observed agent behavior, grounded in deployed source and
session evidence. Started 2026-10-04 during lab testing; updated through the
first paid pilot (`study.live.v4`, N=5 completers, 2026-10-08), the paused
second-pilot session on `study.live.v5` (`tp_ai_v4`/`tp_scripted_v6`: 300 s
task, remarks — AB-26), the 2026-10-10 audit-remediation fielding
(`tp_ai_v6`/`tp_scripted_v8` on `study.live.v7`, opened 2026-10-10:
`prompt.assistant_demo@v4`, `assistant_output_claims_v5`, `grounded_prompts_v7`),
and the 2026-10-10 incentive-compatibility fielding
(`tp_ai_v7`/`tp_scripted_v9` on `study.live.v8`: consent v7 $3.00 + $0.50 bonus
offer paid to every completer, `payment.s5_base_only@v5`), the 2026-10-11
student-testing round (`tp_ai_v11`/`tp_scripted_v13` on `study.live.v9`,
N=5 Connect — AID-61/AID-62: `open_ended` stage, `tp_post_11`), and the
**2026-10-12 AI-only fielding transition (AID-63)**: `study.live.v10` is open
for the N=4 Connect pilot on `tp_ai_v11` × high/low only — the scripted arm
sits behind `SCRIPTED_TEAMMATES_ENABLED=0`, and the file-added cue is removed.
Scripted sessions in this register are historical evidence (v4–v9); new
fielded sessions are all AI-teammate cells.
**Scope:** what the teammate agents (JT, PM) and the AI assistant actually *do* in a
session, as seen by a human tester — and whether each behavior is what the code intends
and what a participant would expect.

This file is **not** the decision inventory (`INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_DECISIONS.md`
— what was decided and by whom), the plan (`INVESTIGATORS_TEAM_EXPERIMENT_PLAN.md` — what
is built), or the audit (`INVESTIGATORS_TEAM_EXPERIMENT_PLAN_AUDIT.md` — independent
verification). It is the running tally of *"wait, why did the agent do that?"* moments
from demo and lab sessions, so that recurring questions have a documented answer and
genuine surprises get recorded before they are forgotten.

## How to read and extend this file

- Behavior IDs are `AB-nn`. Add a new entry whenever a tester (or participant) notices
  agent behavior worth explaining — whether it turns out to be intended or not.
- Each entry states:
  - **Observed** — what the tester saw, with session evidence where available.
  - **Mechanism** — what the code actually does (file + symbol, artifact, or policy).
  - **By design?** — the *engineering* verdict: does the build intend this?
  - **Participant expectation** — the *human* verdict: would a participant read this as
    natural/competent, or as broken/suspicious? This is a judgment call, not a fact.
  - **Status** — `expected` (both columns align), `comprehension gap` (the code is right
    but the presentation reads wrong), `defect — remediated` (was a bug, fixed), or
    `open` (undecided whether to change anything). Documented compounds in use:
    `expected — watch`, `comprehension gap — remediated`,
    `comprehension gap — payment impact`, `by design — narrow boundary kept`,
    `friction eased`. A parenthetical `(SQ-n, date)` is an annotation, not a new
    status — the contract lint bounds the vocabulary.
- Cross-references: `AID-nn` → the decision inventory; `BA-nn`/`AI-n`/`O-n`/`INV-nnnn`
  (numeric) → remediations and tasks in the plan; `INV-WORD-WORD` → harness
  oracle invariants in the harness doc; session IDs cite the live evidence.
- If an entry and the code disagree, the code wins — update the entry.
- Behaviors that turn out to need a *decision* (change the design? pre-register?
  amend?) get promoted into the decisions register as `AID-nn`/`O-n` items; this file
  keeps the observation and links forward. An SQ ruling is not complete until
  the decision inventory reflects it — either as a dated amendment on the AID
  entry it changes (agent behavior: SQ-8/9/10) or as a resolved SQ row alone
  when the ruling is platform-UX rather than agent behavior (SQ-6/7/11 — these
  belong to the plan/UX surface, not the AID inventory).

## Observation index

| ID | Behavior (short) | By design? | Participant-expected? | Status |
|---|---|---|---|---|
| AB-01 | Each teammate replies once to a greeting, staggered | yes (AID-14) | yes | expected |
| AB-02 | A structured "Ask the team" request is answered by exactly one teammate — the newest-record holder | yes (AID-20) | no — picker reads "the team" | comprehension gap |
| AB-03 | A teammate record share is always an answer to a specific ask — never truly unrequested | yes (AID-15) | no — no visible threading | comprehension gap |
| AB-04 | Teammates post scripted asks aimed at the *participant*; unanswered asks expire silently | yes (AID-04/05) | unclear — participant may not know they are the responder | comprehension gap |
| AB-05 | Teammates never answer each other's asks | yes | probably yes | expected |
| AB-06 | Both teammates produced near-identical replies | no — defect | no | defect — remediated |
| AB-07 | JT writes lower-case, PM writes sentence case | yes (AID-37) | yes (subtle) | expected |
| AB-08 | Shared record cards join the case file automatically ("— in your case file") | yes (AID-22, O-4) | yes | expected |
| AB-09 | Check-in ratings persist as a strip above team chat | yes (INV-0902) | yes | expected |
| AB-10 | The assistant answers from the case file (plus a fixed crate→item map for contents questions) — never chat, never teammate holdings | yes (AID-24/26 + SQ-8 route) | probably not | comprehension gap |
| AB-11 | "Are you a bot?" gets a truthful disclosure line | yes (AID-19) | n/a unless probed | expected |
| AB-12 | Scheduled replies land on their own clock — e.g. a greeting reply arriving after the participant's first ask | yes | borderline — can read as non-sequitur | comprehension gap |
| AB-13 | Scripted asks can name crates the participant holds nothing on | yes (target mix per profile) | unclear | expected — watch |
| AB-14 | Stalling at the check-in for 120 s terminates the session — lab copy showed "a technical problem"; since SQ-6/7 the overlay carries a 60 s norm countdown and the stop names the cause | yes (profile `inactivity_cap`) | warning now visible; leave-and-return asymmetry and D04 payment treatment remain open | comprehension gap — payment impact |
| AB-15 | Cross-crate assistant questions ("which crates are the laptops in?") always bounce to the same redirect | partly — gates intentional, dead-end isn't | no — and the redirect copy promises the very feature that failed | defect — remediated (SQ-8, 2026-10-04) |
| AB-16 | Grounded assignment statements share one template — "I'm on crates …" vs "i'm on crates …" — and collide as near-paraphrases whenever the two lists overlap | yes (grounded lines deliberately bypass the gate) | no — both teammates answer "which crates?" in identical frame | comprehension gap — remediated (SQ-9, 2026-10-04) |
| AB-17 | Under deterministic fallback (provider outage / mechanism mode), teammate acks draw from a tiny canonical bank — "noted" ×12, "got it"/"makes sense" ×5–6 in one session | yes (fallbacks are verbatim by design) | no — outage-mode teammates visibly loop the same three lines | expected — watch |
| AB-18 | Roughly half of natural "are you real?" phrasings classify as probe *near-misses* — logged but answered with NO disclosure | yes (classifier boundary deliberate) | probably not — "are you guys real or computers" gets silence | by design — narrow boundary kept (SQ-10, RL 2026-10-04) |
| AB-19 | A `technical_stop` terminal transition leaves request episodes pending (no terminal close_reason) | no — unexamined edge | n/a (invisible to participant; affects exports/audit) | defect — remediated (2026-10-04) |
| AB-20 | Team chat + AI assistant panes are height-starved — on a large monitor each shows only a few lines | yes (single-column right stack + fixed footer) | no — the resizers exist but can't manufacture vertical space | defect — remediated (2026-10-04) |
| AB-21 | Answers are keyed by crate number only, but evidence is item-named — participant must hold the crate→item→location chain across two panels | yes (crate badge deliberately hidden, UX round 3 item 7) | no — matching across panels reads as the task's core friction | friction eased (SQ-11, 2026-10-04) |
| AB-22 | Check-in popup opening mid-typing converted a spacebar press into a submitted rating | no — unintended focus grab | no — a stray keystroke must never submit | defect — remediated (2026-10-05) |
| AB-23 | Every screen carried a "Leave the study" link | path yes (INV-0908); constant visibility not a studied choice | plants the idea of quitting; IRB doesn't require it | friction eased (2026-10-05) |
| AB-24 | Check-in popup re-asked the rating right after a valid click, then jumped to scores | no — stale-revision resync replayed `checkin_open` while the response was still in flight | a committed answer must never be re-asked | defect — remediated (2026-10-05) |
| AB-25 | "where is crate 9" produced the whole crate→item map | no — out-of-range number fell through to the broad-contents route | an invalid crate should get the boundary, not the map | defect — remediated (2026-10-05) |
| AB-26 | Teammates spontaneously remark on the time pressure — two unprompted arm-specific chat lines at 75/83 s | yes (AID-56, bound verbatim copy) | first teammate-initiated small talk; human-seeming but the easiest place to read scripted | expected — watch (fielded 2026-10-09; no live participant yet) |
| AB-27 | First live believability read: `tp_post_10` splits the AI cell automated-vs-human while scripted completers answer human/mixed/unsure | yes — the suspicion check working (O-9) | n/a — a measurement outcome | expected — watch (descriptive only at N=5) |

---

## AB-01 — One greeting reply per teammate, staggered

- **Observed:** participant sends "Hey!"/"Hi!" → JT replies "hello!", PM replies "Hi!",
  a few seconds apart (sessions `14a25c68`, `c53e237e`).
- **Mechanism:** the greeting command schedules one `social_greeting` delivery per seat
  (`lifecycle._greeting`), each on its own sampled typing delay.
- **By design?** Yes — both teammates acknowledge the participant (AID-14).
- **Participant expectation:** natural — two people saying hi back.
- **Status:** expected.

## AB-02 — A structured ask is answered by exactly one teammate

- **Observed:** in session `14a25c68` the tester used "Ask the team about…" four times.
  PM answered Crate 1; JT answered Crates 4, 7, and 6. Reported as "only JT responded."
- **Mechanism:** `retrieval.select_for_crate` returns the seat holding the *newest*
  record for that crate across both teammates; only that seat is scheduled a
  `structured_record` delivery (`lifecycle._teammate_request`). Verified against
  holdings: PM held ordinal 14 on Crate 1; JT held the top ordinal on 4, 7, 6.
- **By design?** Yes — the demo rule "newest record for that crate across both seats"
  (AID-20). The seat choice is a world-draw outcome, not a preference.
- **Participant expectation:** the picker says "the team" — a participant can reasonably
  expect both teammates to weigh in, or at least wonder why one is always silent. There
  is no signal explaining why the other seat didn't answer.
- **Status:** comprehension gap. **Open options:** leave as-is (demo fidelity); the
  non-holder seat posts a deflection ("nothing newer on 7 — check with jt"); or both
  seats share their own latest like `nl_only` typed asks do (per-seat snapshot, O-1).

## AB-03 — Teammate shares are always prompted, but don't look it

- **Observed:** the tester perceived PM's Crate-1 share as "something unrequested right
  away." In the data, the share's `prompted_by_request_id` links it to the participant's
  own Crate-1 ask — it *was* the answer, landing ~10 s later with PM's greeting reply
  interleaved between ask and answer.
- **Mechanism:** every teammate share row requires `prompted_by_request_id`
  (`scheduler._deliver_structured`); unrequested shares have no code path
  (AID-15, `proactive_sharing: none`). The chat renders messages flat — no threading
  marker ties a share back to its request message.
- **By design?** Yes mechanically; the presentation has no request→reply linkage.
- **Participant expectation:** a share with no visible antecedent can read as the agent
  volunteering intel — or as noise.
- **Status:** comprehension gap. **Options:** render "in reply to your request on
  Crate n" on share messages; group/share threading; leave as-is.

## AB-04 — Scripted teammate asks target the participant, and expire silently

- **Observed:** in `14a25c68`, PM asked "Requesting intel on Crate 5." (t=40 s) and JT
  asked Crates 1 and 8 (t=150 s, 240 s). Nobody answered; all three episodes expired at
  deadline.
- **Mechanism:** `scheduler._scripted_request` writes `responder="participant"` — the
  asks are prompts for the *participant* to share a record from their own file
  (spec §7.2: a share on the matching crate resolves the episode). The participant
  never shared, so they expired. `tp_demo_v5` has `crosstalk: null`, so the other seat
  does not even comment.
- **By design?** Yes — three scripted asks per task is content policy (AID-04/05), and
  the participant is the intended responder.
- **Participant expectation:** unclear — a participant who doesn't connect "share a
  card" with "answer a teammate" sees three dangling requests. It can also read as
  *the participant* ignoring the team.
- **Status:** comprehension gap. **Options:** training/instruction emphasis on
  share-to-answer; enable `crosstalk` for demo profiles (the other seat remarks ~28 s
  later — "no_news" vs "after_share"); a gentle reminder nudge; leave as-is.
- **Positive evidence:** session `b20cc945` showed the resolution path working — the
  participant shared a record ~14 s after JT's scripted Crate-1 ask and the episode
  closed with `close_reason='participant_share'` (spec §7.2 mechanism confirmed live).

## AB-05 — Teammates never answer each other

- **Observed:** PM's and JT's scripted asks drew no reaction from the other seat.
- **Mechanism:** scripted requests are written with `responder="participant"` — seats
  are never each other's responder (`scheduler._scripted_request`).
- **By design?** Yes — teammate-to-teammate conversation is not modeled; the
  participant is the only human addressee.
- **Participant expectation:** probably fine — real teammates might answer each other,
  but its absence is not conspicuous in a 360 s task.
- **Status:** expected.

## AB-06 — Near-identical replies from both seats (remediated)

- **Observed:** session `c53e237e` (2026-10-04, pre-fix): PM "i'd have to check my
  notes. send me a request and i will pass along what i have." / JT "i'd have to check.
  send me a request and i'll pass along what i have." — distinct-word Dice ≈ 0.87.
- **Mechanism (pre-fix):** both seats generated from near-identical snapshots at T=0.2
  and converged; the ≥3-word *exact*-repeat gate could not see paraphrases.
- **By design?** No — real defect.
- **Participant expectation:** reads as one scripted reply — a believability break.
- **Status:** defect — remediated. `teammate_output_v7` adds `is_near_repeat`
  (≥6 distinct words, ≥75% Dice vs. all delivered teammate lines), enforced at
  generation, at commit under the lock, and inside fallback selection; audit records
  `repeat_substituted_reason` = `"exact"`/`"near"`.

## AB-07 — JT is lower-case; PM is sentence case

- **Observed:** JT "hello!" / "i'd have to check…" vs. PM "Hello!" / "Sharing intel…".
- **Mechanism:** deterministic per-seat styling (AID-37, AI-3).
- **By design?** Yes — the asymmetry helps the seats read as two different people.
- **Participant expectation:** subtle; most testers won't consciously notice, which is
  the point.
- **Status:** expected.

## AB-08 — Shared cards auto-join the case file

- **Observed:** teammate shares render with "— in your case file" immediately on
  delivery.
- **Mechanism:** `worlds.upsert_added_to_file` on delivery (O-4/AID-22); the tag is the
  UI confirming the card is already usable by the assistant.
- **By design?** Yes — the manual Add-to-file step was removed deliberately.
- **Participant expectation:** matches the demo; fine.
- **Status:** expected.

## AB-09 — Check-in ratings persist above the chat

- **Observed:** "Check-in ratings — JT: 2 · PM: 3 · JC: 2" pinned over team chat after
  the pressure check-in reveal.
- **Mechanism:** `ChatPanel` pressure strip, rendered for the rest of the task after
  `checkin_reveal` (INV-0902).
- **By design?** Yes — demo fidelity; the ratings stay salient.
- **Participant expectation:** consistent with the demo flow.
- **Status:** expected.

## AB-10 — The assistant only knows what's in the case file

- **Observed:** the assistant answers from file contents; it cannot see team chat,
  teammate holdings, or say what the file lacks. One deliberate exception since
  SQ-8 (2026-10-04): recognized *contents* questions ("which crates have the
  laptops?") answer from a separate fixed `CRATE_CONTENTS` map — world
  structure, not file records — so the map is served even where the file does
  not contain it.
- **Mechanism:** ordinary answers use the snapshot pinned at ask-accept containing
  only participant-file records (AID-24, INV-0507); denial gate blocks absence
  statements (AID-26). Contents questions route deterministic-only to the
  crate→item resolver (`is_contents_question`, `deterministic_contents_summary`
  provenance) — a second, separate deterministic source. Chat and teammate
  holdings remain unread on either path.
- **By design?** Yes — hard information boundary; it's a measurement instrument.
- **Participant expectation:** a participant may expect "the AI" to know the chat or
  nudge toward unheld intel; it will not.
- **Status:** comprehension gap — worth one line in training if testers keep assuming
  chat context. Otherwise expected.

## AB-11 — Automation probes get a truthful disclosure

- **Observed:** not exercised in either 10-04 session; behavior verified by tests.
- **Mechanism:** a recognized probe bypasses rate limit + seat cap, gets a fixed
  truthful line per responder seat, logs `automation_probe` (AID-19, BA-75).
- **By design?** Yes — mandatory honest disclosure.
- **Participant expectation:** n/a unless the participant probes; the correct answer is
  a truthful one.
- **Status:** expected.

## AB-12 — Scheduled replies land on their own clock

- **Observed:** PM's "Hello!" (greeting reply, delivered ~13.6 s after the
  participant's greeting) arrived *after* the participant's Crate-1 ask —
  visually a non-sequitur between the ask and PM's answer. (Not a discrepancy
  vs AID-14's ~4.25+1.5 s medians: PM's two summed lognormals clamp at
  11.0 s + 4.4 s plus typing, so ~13.6 s is a high-but-legal draw of the same
  distribution.)
- **Mechanism:** deliveries follow per-job sampled active-time delays
  (`schedules.plan_job_draws`); a scheduled line does not re-check whether the
  conversation moved on before it lands.
- **By design?** Yes — human-like latency is deliberate; interleaving is the cost.
- **Participant expectation:** borderline — one late "Hello!" is harmless, but a stale
  reply to a superseded question would read as the agent not listening.
- **Status:** comprehension gap — monitor for staleness in generated replies (a
  generated answer referencing an already-answered ask would be worse than a late
  greeting). No mechanism currently re-scopes a pending reply against newer messages.

## AB-13 — Scripted asks can name crates the participant can't help with

- **Observed:** PM asked for Crate 5 — an unassigned crate; the participant may hold
  nothing on it.
- **Mechanism:** the scripted plan mixes `assigned`/`unassigned` targets per profile
  policy (`schedules.plan_scripted_requests`, `target="any_useful"`); seats come from
  the profile's multiset shuffle (this session: PM, JT, JT).
- **By design?** Yes — per-spec the asks deliberately span crates inside and outside the
  participant's assignment.
- **Participant expectation:** a participant asked about a crate they have nothing on
  may be unsure whether "I don't have anything" is a valid answer — or whether they can
  answer at all.
- **Status:** expected — watch whether testers understand shares are the only
  resolution path, and whether typed "sorry, nothing on that" *should* close the
  episode (currently only a record share resolves it — open question).

## AB-14 — Stall at the check-in for 120 s → session terminates

- **Observed:** session `b20cc945` (tp_demo_nl_v2, 2026-10-04): the participant blurred
  the tab during the pressure check-in; 121 s after `checkin_open` the session
  transitioned to `technical_stop` with reason `checkin_incomplete`, and the UI showed
  "This session stopped because of a technical problem… checkin_incomplete."
- **Mechanism:** `checkin_open` schedules a **wall-clock** `checkin_inactivity` job at
  `policy.checkin.inactivity_cap_wall_ms` (120 s in the demo profiles). If the session
  is still in any check-in stage when it fires, `_checkin_inactivity` transitions to
  `technical_stop/checkin_incomplete`, writes the unscored result row, closes the
  active clock, sweeps pending jobs, and commits the completion receipt + base payment
  entitlement in the same transaction (spec §8.1 disposition).
- **By design?** Yes — the outcome literal is schema-locked (`inactivity_outcome:
  "technical_stop/checkin_incomplete"`, the only allowed value) and the spec calls for
  a ~120 s cap that "caps stalled sessions without inventing an own-pressure response
  or claiming manipulation exposure." Wall-clock is deliberate: it cannot be evaded by
  closing the browser, and the worker enforces it with no open tab.
- **Participant expectation:** three compounding gaps, as reported by the lab
  tester on the 2026-10-04 build — items 1 and (part of) 2 are since
  remediated; the disposition below separates the dated observation from
  current behavior:
  1. **No warning before the kill** *(observed 2026-10-04; remediated same
     day, SQ-6/SQ-7).* The overlay then showed only "The task clock is paused"
     with no countdown or signal that the *session* was on a 120 s wall-clock
     fuse — the paused-clock copy could even read as "take your time." **Now:**
     the overlay carries a group-norm line plus a live **60-second** countdown
     in collect/wait/reveal — a *stated* norm deliberately under-promising the
     real **120-second** wall cap — and a `checkin_incomplete` stop names the
     cause instead of the generic "technical problem" screen.
  2. **Payment consequence** *(open — D04).* A `checkin_incomplete` stop ends
     the session without a completion. The build records a base
     `payment_entitlement` for `technical_stop`/`abandoned` mirroring the
     `unaffected` withdrawal rule while D04 remains unresolved — i.e., the
     entitlement row exists and actual payment treatment is a decision, not a
     settled fact. What stays true regardless: the participant's time is spent
     either way, and without the (now-added) warning they could not have
     known a deadline existed.
  3. **Leave-and-return is the norm** *(still applicable).* Prolific and
     Research Connect participants routinely step away mid-study and come
     back. The asymmetry still bites: mid-task abandonment is swept on a
     ~30-minute window (BA-08), but the check-in kills at 120 *seconds*. The
     countdown now warns a present participant; a returner still finds
     "Session ended" with no in-product recourse.
- **Status:** comprehension gap — payment impact. Warning and cause-copy
  remediated (SQ-6/SQ-7, 2026-10-04). What remains for a design answer before
  paid fielding: the 120 s vs ~30 min asymmetry, post-reveal cap scoping
  (deferred in SQ-6), and the actual payment treatment — D04's decision, not
  this register's to settle.

## AB-15 — Cross-crate assistant questions always dead-end

- **Observed:** session `1fb2ac95` (tp_demo_nl_v2, 2026-10-04): five typed assistant
  questions — "what stolen objects are in which crates?", "which crates are the
  laptops in?", "can you match the crates to the stolen items?", "which crates have
  the camera?" — every one returned the identical line *"Ask me about a specific crate
  by number or by contents."* Only the single-crate "what are the contents of crate 1?"
  got a real answer (the deterministic file summary).
- **Mechanism (the dead-end is constructed, not accidental):** the model *is* called
  for every typed prompt — all five calls show in `model_call` at 2.5–5.7 s each —
  but every response is undeliverable by construction:
  - a correct multi-crate answer → `answer_key_like` (`_ANSWER_KEY_CRATE_LIMIT = 1`,
    output_claims.py — mentioning >1 crate is a solution dump);
  - the model's hedge ("the provided entries do not contain…") → `false_denial`
    (the file *does* hold relevant records, so denial is forbidden);
  - fallback `assistant_file_summary` needs a crate number — `crate=None` → the
    redirect line. And the redirect's own copy ("…or by contents") advertises a
    contents→crate lookup that **does not exist anywhere** — no code path maps
    "laptops"/"cameras" back to a crate.
  - Compounding: the assistant system prompt (`GROUNDED_ASSISTANT_SYSTEM`) never tells
    the model that >1 crate mention is fatal, so gemma4:26b predictably produces
    multi-crate dumps or denials — both already rejected.
- **By design?** The gates are deliberate (anti-answer-key, no false denials). The
  outcome is not: a legitimate single-crate answer ("Crate 1 has the laptops, per your
  file") would pass — the model just doesn't produce it, the fallback can't cover it,
  and the redirect teaches the user nothing (it doesn't say "one crate at a time").
  Meanwhile five wasted model calls added latency for zero information.
- **Participant expectation:** the assistant looks broken — it promises "by contents,"
  then refuses every contents question verbatim, with no hint why.
- **Status:** defect — remediated 2026-10-04 (option (a) shipped, SQ-8).
  Item-named questions ("which crates have the laptops?") resolve against the
  fixed `CRATE_CONTENTS` map → scoped file summary per matching crate; broad
  "what's in each crate" gets the whole map. Neither names locations — the
  strict answer-key gate is untouched, and the harness's assistant-bound
  invariant was rescoped from ">1 crate" to "crate+location dump" (a contents
  list is not answer-key). New provenance tag `deterministic_contents_summary`
  (schema 0007). The redirect now describes the real contract ("ask which crate
  holds an item").

## AB-16 — Grounded assignment statements collide across seats

- **Observed (sim):** agent-sim run `20261004T193346Z` (tp_demo_v5, `disconnecter`
  session): PM delivered *"I'm on crates 2, 3, 4 and 5"* and JT delivered *"i'm on
  crates 4, 5, 6 and 8"* — a cross-seat near-paraphrase flagged by the harness's
  INV-NEAR-REPEAT check (the same Dice gate remediated for generated lines in
  AB-06). Whether it fires depends on the world draw: when the two seats' crate
  sets overlap ≥3 of 4, the template produces a near-duplicate pair. An earlier
  sim seed produced "i'm on crates 1, 2, 6 and 7" vs "I'm on crates 1, 2, 6 and 8".
- **Mechanism:** `engines.grounded_assignment` (engines.py:619) renders the seat's
  own assignments into one fixed frame — delivered when a typed ask matches
  `is_assignment_ask` (an asker/persona mutation like "which crates" triggers it).
  The code comments say these lines are *"truthful by construction, so they
  bypass the text gate the same way record shares do"* — the collision is a
  deliberate gate exemption, not an oversight.
- **By design?** Yes — the bypass is documented engineering (grounded facts may
  repeat; the gate exists for generated prose). But the *believability* cost is
  real: the exemption trades a truthful answer for a visibly templated one.
- **Participant expectation:** ask "who's on which crates" and both teammates
  answer in the identical frame, one number different — the same copy-paste
  impression AB-06's gate was built to kill, arriving through the sanctioned
  grounded-line path.
- **Status:** comprehension gap — remediated 2026-10-04 (SQ-9). Per-seat frames
  (JT "i'm on crates…" / PM "my crates are…") plus a re-ask variant ("still on
  crates…" / "same as before — crates…") when the seat already answered;
  harness `INV-NEAR-REPEAT` now treats same-seat repeats as warn and cross-seat
  as fail — the post-fix batch runs clean.

## AB-17 — Outage-mode teammates loop a three-line ack bank

- **Observed (sim):** provider=None sim runs (mechanism mode — equivalent to a
  live Ollama outage): "noted" delivered ×12, "got it"/"makes sense" ×5–6 each
  in a single session (INV-SCRIPTED-REPEAT findings across `asker`, `chaotic`,
  `sharer`, `disconnecter` personas). Under the stub provider the generated
  lines vary, so this is specifically the *deterministic-fallback* surface.
- **Mechanism:** generation unavailable → verbatim fallback lines from the pinned
  `fallback_bank` rules — a small canonical pool, drawn per event, with no
  delivered-line dedupe across the session.
- **By design?** Verbatim fallbacks are intentional (never model text, always
  grounded). The *visible repetition* is an accepted degradation, not a choice
  anyone made.
- **Participant expectation:** in an outage the teammates would cycle the same
  three acknowledgments — conspicuous, but bounded and honest. Note *(observed
  pre-2026-10-05):* under repeated probing the fixed disclosure line ("i'm an
  automated account…") repeated 3× in one prober session — also conspicuous,
  and mandated verbatim. The 2026-10-05 identity-draw fix (shared draw +
  per-seat offset, AID-19) prevents *same-probe cross-seat echoes* — it does
  not dedupe across successive probes, so a later probe pair can still reuse
  a line.
- **Status:** expected — watch. If fielding ever runs degraded, this is the
  believability cost. An optional per-session verbatim-draw dedupe is a possible
  hardening task, not a requirement.

## AB-18 — Natural probe phrasings mostly land as near-misses, not probes

- **Observed (sim):** the prober persona's mutated probes were all accepted as
  chat but produced zero `automation_probe` events. Direct classifier check:
  `"are you guys real or computers"`, `"is anyone here actually real"`,
  `"did a person write that"`, `"this feels automated honestly"` all classify
  `probe_near_miss=True`, `is_automation_probe=False` — logged, never disclosed.
  Only canonical phrasings ("are you guys bots?", "you're an AI right",
  "are both of you AI agents") hit the mandatory-disclosure class.
- **Mechanism:** `engines.is_automation_probe` vs `probe_near_miss` — the
  disclosure obligation (O-3, BA-75) covers full probes only; near-misses are
  tagged in the message payload for export measurement.
- **By design?** The boundary is deliberate (the adversarial corpus pins
  P=1.00/R=1.00 on its own 103-item probe set — but that corpus's idea of
  "probe" is narrower than a real participant's phrasing distribution).
- **Participant expectation:** a participant who asks "is anyone here actually
  real" plausibly expects an answer — gets none. The near-miss→silence path is
  correct per policy but is a disclosure-coverage question for RL: how wide
  should the mandatory truthful disclosure reach?
- **Status:** by design — RL ruled 2026-10-04 to keep the narrow disclosure
  boundary (SQ-10 closed); near-misses remain logged-but-silent and measured
  via the tagged message payload. Revisit only if live participants complain.

## AB-19 — technical_stop leaves request episodes pending

- **Observed (sim):** the `idler` persona correctly died `technical_stop` /
  `checkin_incomplete`, but the terminal sweep left 1 request episode with
  `closed_at IS NULL` (INV-EPISODE-BOOKKEEPING warn). The earlier live kill
  (`b20cc945`) shows the same shape — the scripted ask that the participant
  had already answered via share closed; other pending episodes did not.
- **Mechanism:** terminal transitions sweep *jobs* (`sweeps active jobs`) but
  the episode-ledger close path (`session_abandoned` close_reason exists) is
  not applied on every terminal path.
- **By design?** Unclear — probably an unexamined edge, not a decision.
- **Participant expectation:** invisible to the participant; matters for
  exports, episode-level measurement, and audit completeness.
- **Status:** defect — remediated 2026-10-04. `engines.expire_open_episodes`
  closes still-open episodes `session_abandoned` on every terminal path that
  lacks a more specific sweep (`technical_stop`, `withdrawn`; `deadline` and
  `session_abandoned` already closed theirs). Post-fix sim batch: 0 orphaned
  episodes across all terminal outcomes.

## AB-20 — Chat and assistant panes are height-starved on a large monitor

- **Observed (lab, 2026-10-04):** on a full-height desktop monitor the tester
  could "barely see more than one line of text" in each of the Team chat and AI
  assistant panes — the message history is effectively unreadable in flight.
- **Mechanism:** `.task-screen` is `height:100vh; overflow:hidden` — a zero-sum
  budget shared by the header, notice strips, `.task-grid` (`flex:1`), and the
  `.submit-bar` footer (title + prompt + 4 pickers + per-picker status line ≈
  130 px). The right column then splits the remainder 55/45 between chat and
  assistant, and each pane additionally loses its head, hint line and composer
  (~110 px) before `.msgs` gets what's left. The BA-76/77 resizers only
  re-divide the fixed budget — they cannot make the right column taller.
- **By design?** Partly — the simultaneous-workbench contract (INV-0802)
  requires all surfaces visible at once, and the footer was deliberately made
  a distinct answer area (UX round 3, item 10). But the *height allocation* was
  tuned for smaller screens; nobody set a minimum readable-lines floor.
- **Participant expectation:** chat and assistant are the two channels the
  task pushes the participant toward — squinting at ~2 lines of scrollback
  works against both the TP framing and comprehension.
- **Status:** defect — remediated 2026-10-04 (first option shipped): the submit
  bar is now a single compact row (inline pickers, labels inside the status
  line) under 1400 px it wraps rather than squeezes. Chat/assistant panes gain
  ~80 px of `.msgs` height at typical desktop heights; resizer math unchanged.
  The wider redesign options (side-by-side right column) remain available if
  lab testing still finds panes cramped.

## AB-21 — The crate→item→location chain spans two panels and working memory

- **Observed (lab, 2026-10-04):** the tester found it "more complicated knowing
  the Crate number, but not knowing the stolen item name" — answering requires
  matching crate number → item (from record bodies) and item → location (from
  other record bodies), with the evidence in the left pane and the pickers in
  the bottom bar.
- **Mechanism:** record cards carry **no visible crate badge** — crate identity
  lives only in the aria-label and `data-crate` (a deliberate UX round 3,
  item 7 decision). Record bodies mix frames: some name "Crate 7" or "Crate 7
  (tablets)", many name only the item ("Anonymous tip: laptops have not moved
  from The Sawmill"). The fixed `CRATE_CONTENTS` map (crate 1 = laptops …
  crate 8 = power tools) is never shown to the participant as a table; the
  answers bar labels pickers "Crate n" with no item. So the participant must
  *derive and hold* crate→item while scanning for item→location.
- **By design?** The badge removal was deliberate; whether the resulting
  two-hop lookup is *intended task difficulty* or *accidental friction* was
  never decided explicitly.
- **Participant expectation:** reads as busywork — the task's stated goal is
  "locate your crates," but part of the difficulty is remembering which crate
  holds which item, which is a memory test, not a judgment-under-pressure test.
- **Status:** friction eased 2026-10-04 (SQ-11 ruling): pickers and both ask
  dropdowns now label "Crate n — item" from a new `crate_contents` workbench
  field — the crate→item hop no longer requires memory. Record cards stay
  unbadged (UX round 3 item 7 stands), so item→location matching still requires
  reading evidence — partial difficulty preserved deliberately.

## AB-22 — Check-in popup hijacks in-flight typing into a submitted rating

- **Observed (lab, 2026-10-05):** the tester was typing "Crate 1" in the team
  chat when the check-in overlay opened; the next keystrokes landed in the
  modal — the spacebar submitted rating "1" with no intent. (The modal
  auto-focused the first rating button, so Space activated it.)
- **Mechanism:** on open the overlay moved DOM focus to the first `button`
  inside the dialog — rating "1". Any pending keystroke (Space, Enter) then
  activated that focused control; the rating submitted immediately.
- **By design?** No — an unintended-focus bug. Focus management exists to
  keep the modal accessible, but focusing a *submittable control* converts
  ambient typing into submissions.
- **Participant expectation:** a participant mid-sentence should never
  discover a keystroke answered a survey item.
- **Status:** defect — remediated 2026-10-05: focus now lands on the dialog
  container itself (`tabIndex={-1}`), and the overlay cancels Enter/Space
  keydowns — submissions require a real mouse click. The typed chat draft is
  preserved (the modal never consumed text input; focus returns to the
  composer on close).

## AB-23 — A persistent "Leave the study" link advertises quitting

- **Observed (lab, 2026-10-05):** every screen carried a small "Leave the
  study" link (bottom corner / task header). The tester questioned why it
  exists at all — IRB does not require an on-screen quit control (closing
  the browser ends the session), and a constant affordance invites the
  thought "should I leave?"
- **Mechanism:** INV-0908 implemented the withdrawal *path* (command +
  preserved records + entitlement row); the always-visible link was the
  surface chosen to satisfy it.
- **By design?** The path is deliberate; the visibility was not a studied
  choice. Removing the link still leaves a quit path — browser close →
  `heartbeat_timeout`/`abandoned` — which preserves records and entitlement
  identically under the pinned policy. Trade-off recorded: outcome reason
  becomes `abandoned` rather than `participant_withdrawal` (the command
  remains dispatchable via admin/harness, so the path is exercisable).
- **Participant expectation:** n/a — the concern is suggestion, not access.
- **Status:** remediated 2026-10-05 (RL): the on-screen control is removed
  from all stages; `withdraw` stays a real command (admin/monitor surface)
  and the `withdrawn` terminal screen still renders if the path runs.

## AB-24 — Check-in popup re-asks the rating right after a valid click

- **Observed (lab, 2026-10-05):** the tester clicked a rating; the popup
  then "popped up again to ask for response" before immediately showing
  the three members' scores.
- **Mechanism:** a concurrent server event (`generation_resolved`, one seq
  after `checkin_open`) bumped the session revision between the client's
  last poll and the click. The `pressure_response` command rejected with
  `stale_revision` → the store ran a full `resync()` → the check-in reset
  to closed, then the replay re-applied `checkin_open` → phase "collect" —
  and because `checkin_response` was not yet committed (the retry hadn't
  run), the replay *ended* at collect: the popup visibly re-asked the
  rating until the retry committed and the reveal job fired. Command log:
  `pressure_response rejected stale_revision` → `accepted` on retry.
- **By design?** No — optimistic-concurrency resync is deliberate, but the
  replay must not visibly regress a committed interaction.
- **Participant expectation:** a submitted answer is done — nothing should
  ask for it again.
- **Status:** defect — remediated 2026-10-05: `submitCheckin` moves to the
  wait phase optimistically on click; `resync()` preserves the check-in
  object instead of resetting to closed; and `checkin_open`/`checkin_response`
  replays are phase-monotonic (a replayed open never regresses wait/reveal
  back to collect). A `_checkin` job arriving after the cycle completed —
  stage back at `task`, e.g. a lease-loss redelivery — is now cancelled
  server-side when a `pressure_checkin` exposure already exists, so a
  second overlay can never open.

## AB-25 — "where is crate 9" produced the whole crate→item map

- **Observed (lab, 2026-10-05):** the tester asked the assistant about
  crates one at a time ("where is crate 7", "where is crate 8" — each got
  its own file summary), then "where is crate 9" — and received all eight
  crates' contents in one reply (`deterministic_contents_summary`).
- **Mechanism:** the crate-number pattern only accepts 1-8, so "crate 9"
  matched nothing; the bare word "crate" then tripped the broad-contents
  term set and the deterministic route answered with the full map — the
  maximum-disclosure answer for an out-of-range question.
- **By design?** No — the broad-map answer exists for questions that
  genuinely ask across crates; a single invalid number is not that ask.
- **Participant expectation:** a question about one (nonexistent) crate
  should get the honest boundary, not more than was asked.
- **Status:** defect — remediated 2026-10-05: numbered crate references
  outside 1-8 (`crate 9`, `#0`, "crate nine", …) now resolve
  deterministically to "There's no Crate 9 — the team tracks Crates 1
  through 8" plus the standard redirect (`deterministic_file_summary`
  provenance). Valid single-crate asks, item-named asks, and genuinely
  broad contents questions are unchanged.

## AB-26 — Teammates spontaneously remark on the time pressure

- **Observed (preview + sim, 2026-10-09):** on the fielded v5 builds the
  teammates post two unprompted chat lines about the clock. High arm: PM
  "Yikes this is a lot of info, I don't think I am going to finish on time"
  (delivered active 75.2 s in a live preview session) answered by JT "i know
  right!" (83.4 s). Low arm: "Ok this seems manageable, I think I'll finish
  with time to spare" / "me too! i think" — same times, verified in harness
  replays for both arms and both builds (runs 20261009T213114Z/213130Z).
- **Mechanism:** `manipulation.remarks` → `remarks.tp_pressure@v1` (per-arm
  lines + active-second schedule), delivered by `pressure_remark` scheduled
  jobs through `engines.deliver_message` as ordinary teammate chat. The
  first speaker is a per-session fair coin on the `jobs` stream; the other
  seat answers. Bound verbatim copy — never a model call — identical in
  `tp_ai_v6` and `tp_scripted_v8` (AID-56).
- **By design?** Yes — owner-directed NL reinforcement of the pressure
  manipulation, timed 15 s after the 60 s check-in so the own rating lands
  first. The pre-2026-10-09 builds had no remarks; they arrived with the v5
  profile line and remain bound in the current v6/v8 builds.
- **Participant expectation:** the first teammate-initiated banter the
  participant ever sees — two teammates griping (or relaxing) *to each
  other* is arguably the most human-seeming beat in the protocol, and also
  the easiest place for it to read as scripted stage business. The
  coin-flipped speaker keeps the order from looking fixed; verbatim arm
  copy keeps the manipulation uniform across sessions.
- **Status:** expected — watch. The first `study.live.v5` sessions delivered
  them before the pause; the believability read comes from `tp_post_10` on the
  `study.live.v7` round (open since 2026-10-10), and AB-17's small-pool
  repetition concern does not apply (each line delivers once per session).

## AB-27 — First live believability read: the suspicion item discriminates

- **Observed (paid pilot `study.live.v4`, N=5 completers, 2026-10-08/09):**
  `tp_post_10` ("Thinking about the other two members of your team, which
  best describes them?") — AI·high: one "All were automated" and one "All
  were human"; scripted·high: "All were human"; scripted·low: "Some human,
  some automated" and "I am not sure".
- **Mechanism:** required post-task item in `post_task.tp_demo@v4`; Item
  Analytics shows the raw category distribution plus per-option binary
  shares, which flow into Power Inputs/Arm Contrasts. Session `23eb5d14`
  (the "All were automated" answer) is inspectable end-to-end via admin →
  Sessions → Session walkthrough / Participant view (HTML).
- **By design?** Yes — this is the O-9 believability instrument doing its
  job: catching suspicion *and* credulity per cell, without interrupting
  play.
- **Participant expectation:** n/a — a measurement outcome, not a behavior.
- **Status:** expected — watch. Descriptive at N=5; no inference. Two notes
  for the next round: (a) the one "All were automated" in the AI cell is exactly the
  sensitivity signal the item exists to catch; (b) v5 adds the scripted
  pressure remarks to *both* builds, which may shift believability
  independently of the engine — worth watching in the next `tp_post_10`
  read.

---

## Testing layers — what the agent-participant harness now covers

An agent-participant simulation harness (`backend/harness/`, driven by
`scripts/run_agent_sim.sh`) runs whole sessions against a disposable PostgreSQL:
enrollment → consent → training → task → check-in → instruments → completion or
terminal stop, with the bot dispatching real commands through the same handlers
the participant API calls. Evidence lands under
`docs/evidence/<date>-agent-sim/run-*/` (transcripts + findings JSON per session).
The harness's living reference — usage, persona/invariant registries, phase-2
scope — is `INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_HARNESS.md`.

- **Simulation layer (automated):** wording variation (seeded pools + human-style
  mutations), routing/classification, structured-ask seat attribution, episode
  bookkeeping, wall-clock inactivity kills, stale-revision/double-submit/out-of-
  stage command handling, scripted-line repetition, assistant bounds. Verified
  batches: R-01 (8 personas × tp_demo_v5 + tp_demo_nl_v2 + stub — early runs
  carried real fails: `RUNNER` max_steps, near-repeats, an `each_ask` scenario
  failure on `nl_only`; later runs in the batch held — and the whole batch
  **predates the BA-78–BA-89 oracle hardening**), R-02 (8 personas under
  tightened oracles — intermediate run 21/22 on `each_ask`, corrected run
  held), R-03/R-04/R-05 (5-persona verification, 0 fails; stub-provider
  contents-route acceptance), R-06 (P-09–P-14 channel-strategy sweep, 0
  fails), R-07 (first fielded-manifest batch — `tp_ai_v4`/`tp_scripted_v6`
  × normal+asker, 0 fails; the 300 s budget, 45/130/210 requests, 60 s
  check-in, **75/83 s arm-correct pressure remarks** and 240 s warning all
  committed in the job ledger — same schedule bound by the current
  `tp_ai_v6`/`tp_scripted_v8`). Run details live in the harness doc's §8
  log — this register cites run stamps, the harness doc owns the coverage
  story.
- **Human layer (still manual):** felt gameplay, believability, comprehension
  gaps, pacing, consent/payment framing, rendered layout. Every AB entry above
  that needed a human to *notice* still does — the harness verifies mechanisms,
  not impressions.
- **What sim findings mean:** `fail` severities are candidate defects or
  documented-behavior confirmations (AB-16 is the template example); `warn`
  severities are degradation/design-tension observations (AB-17 ack bank, AB-15
  redirect spam reproduced at scale: 15 identical redirects in one
  assistant_poker session); scenario-expectation failures mean the scenario
  didn't exercise what it claims (fixed once already: nl_only has no structured
  asks). Sim evidence is cited per entry as `agent-sim run-<stamp>` +
  session/persona — a lab finding can promote a sim finding to an AB entry, and
  vice versa.
- **Known limitation:** `--provider none` exercises the deterministic-fallback
  surface only; live-model believability needs `--provider ollama` inside the
  worker netns (phase 2), and the HTTP fidelity path (real API endpoints) is
  also phase 2. Harness operational caveats (`JobLeaseLost` noise, run-dir
  conventions, safety contract) are owned by the harness doc §9–§10 — don't
  re-litigate them here.

## Standing questions this register surfaces

| # | Question | Raised by | Where it would be decided |
|---|---|---|---|
| SQ-1 | Should a structured ask signal *why* only one teammate answered (or answer with both seats)? | AB-02 | O-item / RL (demo fidelity vs. transparency) |
| SQ-2 | Should shares/answers carry a visible "reply to your request" link? | AB-03 | UX decision → plan task |
| SQ-3 | Should demo profiles enable crosstalk or a nudge so unanswered scripted asks don't dangle? | AB-04 | profile content policy (D07) |
| SQ-4 | Should a pending reply be re-scoped (or dropped) when the conversation moved on before it delivered? | AB-12 | engineering + RL; new mechanism |
| SQ-5 | Should a typed "nothing on that crate" resolve a scripted ask, or is share-only correct? | AB-04/AB-13 | O-item / RL |
| SQ-6 | Should the inactivity-termination screen name the cause ("took too long") or keep generic "technical problem" copy? And should the cap scope exclude the post-reveal screen? **Resolved 2026-10-04 (RL):** name the cause — the technical_stop screen now states the session ended because the check-in was not completed in time (`checkin_incomplete` reason copy in `StageScreens.tsx`). Post-reveal scoping deferred — the countdown covers collect/wait/reveal uniformly for now. | AB-14 | resolved |
| SQ-7 | Add a visible countdown inside the check-in overlay — the tester's proposal, motivated by IRB pay-on-completion. **Preferred framing (tester, 2026-10-04): group-norm, not technical** — e.g. "All members should complete their response within N seconds so that the group task can continue." It stays diegetic (teammates waiting = consistent with the live-teammate cover), explains the deadline's purpose, and doubles as a mild social-pressure cue compatible with the TP manipulation. Constraints if added: stated N must not exceed the real cap (profiles pin 120 s; a shorter stated deadline under-promises safely, a longer one lies); wall-clock-driven; present in all check-in phases; wording is consent copy. Also reconsider whether 120 s vs. the ~30 min abandonment window is the right asymmetry for leave-and-return participants. **Re-raised 2026-10-04 lab session:** the tester expected the check-in to communicate response importance and found none — the deployed copy (`checkin.pressure_demo.v1`) says only "Your teammates are answering the same question" + "The task clock is paused"; the 120 s wall cap that kills the session is never stated anywhere. A participant who walks away cannot know a deadline exists. **Resolved 2026-10-04 (RL):** implemented — norm text ("Please respond promptly so the team task can continue — everyone needs to answer this check-in") plus a live 60-second countdown shown in collect/wait/reveal. The stated 60 s under-promises the real 120 s wall cap (safe direction per the stated-N constraint); countdown is client-anchored at `checkin_open`. | AB-14 | resolved |
| SQ-8 | Should the assistant answer contents→crate questions ("which crates have laptops?")? The redirect copy already promises it but nothing implements it. Decide: build deterministic contents→crate resolution, or fix the copy. Related: should the assistant prompt disclose the single-crate constraint to the model, and should a "one crate at a time" hint replace the bare redirect? Interacts with `answer_key_like`'s intent (a contents list is not a location dump — is mentioning >1 crate *contents* really answer-key?). **Resolved 2026-10-04 (RL):** deterministic contents→crate built — item-named questions resolve against the fixed crate→item map; broad "what's in each crate" gets the full map; neither names locations. Strict answer-key gate untouched; new provenance tag `deterministic_contents_summary` (schema 0007). Harness invariant rescoped to crate+location dumps (contents map ≠ answer key). | AB-15 | resolved |
| SQ-9 | Should the grounded assignment frame vary per seat (e.g. "i'm covering 2, 3, 4 and 5" for JT, "My crates are 4, 5, 6 and 8" for PM) so the two seats' answers to the same question can't read copy-pasted — or is the deliberate gate exemption for grounded lines acceptable? **Resolved 2026-10-04 (RL):** per-seat frames (JT "i'm on crates…" / PM "my crates are…") plus a re-ask variant ("still on crates…" / "same as before — crates…") picked when the seat already delivered an assignment answer. Grounded exemption kept — lines remain truthful by construction. | AB-16 | resolved |
| SQ-10 | How wide should the mandatory probe-disclosure class reach? Roughly half of natural "are you real" phrasings land as near-misses (logged, silent). Widening = more disclosures on borderline text; not widening = plausible participant questions get silence. **Closed 2026-10-04 (RL):** keep the existing narrow boundary — near-misses stay logged-but-silent (AB-18 by design). Revisit only if live participants complain. | AB-18 | closed — by design |
| SQ-11 | Is the crate→item→location chain intended task difficulty or accidental friction? Record cards carry no visible crate badge (UX round 3 item 7), record bodies mix item names and crate numbers, the `CRATE_CONTENTS` map is never shown, and pickers label "Crate n" only. If easy: label pickers/cards with the item, or item names in ask dropdowns. If hard: it loads working memory in a way that could confound time-pressure effects. **Resolved 2026-10-04 (RL):** friction eased, difficulty partially kept — answer pickers and both ask dropdowns now label "Crate n — item" (from a new `crate_contents` workbench field); record cards stay unbadged per UX round 3 item 7, so item→location matching still requires reading evidence. | AB-21 | resolved |
| SQ-12 | Should the debrief name the pressure remarks too? The bound debrief (`debrief.tp_reference@v2`) explains that teammates were automated and that check-in ratings were assigned — it says nothing about the teammates' scripted "running out of time" / "plenty of time" chat comments (AB-26). Adding a sentence would extend the honest-debrief principle to the newest fixed copy; the wording is consent-family content. | AB-26 | RL/IRB — debrief copy decision before the next paid round opens (v5 opened before this was settled; now applies to `study.live.v7`) |

---

## v15 — workload reframe + the mixed arm (2026-10-17/18, internal eval only)

Fielded on `tp_ai_ovsome_thin_v4`/`v5` (`study.smoke.v15`, no live group opened).
What the agents now do differently:

- **Check-in asks workload, not pressure.** The modal question is "How heavy
  is your workload on this task right now?" (1 = Light workload / 7 = Heavy
  workload); reveal title "Workload reported at the check-in"; the
  `pressure_checkin`/`tp_post_*` ids stay as durable internals.
- **Teammates can now differ inside one team.** The `mixed` arm assigns one
  seat 2/7 and the other 6/7 (fair coin on the `ratings` stream). Remark
  lines resolve per *speaker's recorded seat level*, so under `mixed` the
  light teammate says the light line ("Ok this workload seems light…") and
  the heavy teammate says the heavy line ("Yikes this workload is heavy…")
  — the design intent is one teammate visibly having bandwidth for
  requests/sharing while the other doesn't. Watch for: believability of
  asymmetric teammates, and whether `tp_post_12` ("different workloads")
  separates mixed from high as intended.
- **Manipulation-check item added:** `tp_post_12` on `post_task.tp_demo@v7`
  (order: …09, 11, 12, then 10 last).
- **Check-in pause interaction:** a participant pick in flight when the
  check-in opens is refused `wrong_stage`, stays dirty, and auto-saves on
  resume (fixed 2026-10-18 — before this it froze the crate "Not saved —
  task closed" and lost the pick). Behavior register note: agents pause
  cleanly; participant in-flight actions now survive the pause.
