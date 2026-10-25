# Investigators — Agent-Participant Simulation Harness (living document)

**Status:** living reference. First written 2026-10-04 on source bundle
`3bef23ae…22cf1`; **BA-78–BA-89 remediation** landed the same day on
`7c059d17babd20769a1fa96ff6269ac57c8b73890b2a3e369beaaac19fb47103` — see §10.2.
Run log current through **R-07** (2026-10-09) — the first batch on the
fielded 300 s builds (then `tp_ai_v4`/`tp_scripted_v6`; the fielded line ran
`tp_ai_v7`/`tp_scripted_v9`, same 300 s schedule), not just demo profiles.
The current fielded round is `study.live.v12` (Prolific-only, opened
2026-10-15): the four `tp_ai_ov*_v3` builds at `arm=nopress` —
`TIME_PRESSURE_ENABLED=0` collapses the pressure axis to a neutral allocation
value rather than a randomized arm. Prior rounds: `study.live.v11` (N=30,
`tp_ai_v13` AI-only × high/low, closed) and `study.live.v10` (N=4 Connect
pilot, `tp_ai_v11` × high/low, closed) both fielded AI-only while the
scripted arm sat behind `SCRIPTED_TEAMMATES_ENABLED=0` (AID-63).
**Scope:** the scripted participant-bot layer (`backend/harness/` +
`scripts/run_agent_sim.sh`) — what it is, how to run it, what it verifies, how
its findings feed the behaviors register, and what remains unbuilt (phase 2).

## How to read this file

- This is the harness's **contract + operating manual + run log**, in the spirit
  of `INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_DECISIONS.md` (design decisions) and
  `INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_BEHAVIORS.md` (observed behavior). If
  an entry and the code disagree, the code wins — update this file.
- ID conventions: `P-nn` = persona entries (§4), `INV-*` = oracle invariants
  (§5.1), `EXP-*` = scenario-expectation tokens (§5.2), `R-nn` = recorded run
  batches (§8). Findings promoted to participant-facing significance get an
  `AB-nn`/`SQ-n` in the behaviors register — this file links out, the register
  links back by run stamp.
- Cross-references: `AB-nn`/`SQ-n` →
  `INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_BEHAVIORS.md`; `AID-nn` →
  `INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_DECISIONS.md`; plan record →
  `INVESTIGATORS_TEAM_EXPERIMENT_PLAN.md` §21.

## 1. Why this layer exists

Manual lab testing surfaced two distinct kinds of observation: **mechanism**
questions (did the ask route to the right seat? did the probe disclose? did the
check-in cap fire?) and **experience** questions (does this reply *feel* like a
person? does the pacing read?). The first kind is wasteful to test by hand —
worse, human sessions sample the input space one message at a time, while the
bugs live in the tail (mutated phrasing, stale revisions, idles, reconnects).

The harness replaces manual mechanism-testing with a seeded participant-bot that
drives **whole real sessions** against a disposable database, dispatching
commands through the same handlers the participant API calls. Human testing
then concentrates on the experience layer — see the register's "Testing layers"
section for the split, which is binding:

| Layer | Verifies | Owner |
|---|---|---|
| Simulation (this harness) | routing/classification, disclosure policy, episode bookkeeping, timing/inactivity kills, command-envelope hardening, scripted repetition, assistant bounds | automated, seeded, repeatable |
| Human lab sessions | believability, felt pacing, comprehension gaps, suspicious moments, consent/payment framing, rendered layout | researchers |

## 2. How to run

### 2.1 Quick start

```bash
# One normal session on the demo manifest, ephemeral disposable DB (default):
scripts/run_agent_sim.sh

# A batch: comma list, or name:count mix:
scripts/run_agent_sim.sh --manifest tp_demo_v5 \
    --personas normal,asker,prober,idler

scripts/run_agent_sim.sh --manifest tp_demo_v5 --personas normal:3,chaotic:2 --seed 7

# NL-only manifest (no structured asks — expectations degrade accordingly):
scripts/run_agent_sim.sh --manifest tp_demo_nl_v2 --personas normal,prober

# Fielded build (300 s task + pressure remarks — R-07 verified the schedule):
scripts/run_agent_sim.sh --manifest tp_ai_v7 --personas normal,asker
scripts/run_agent_sim.sh --manifest tp_scripted_v9 --personas normal,asker

# Exercise the generate→validate→deliver path without a model:
scripts/run_agent_sim.sh --provider stub --personas normal,asker

# Channel-strategy sweep (P-09..P-14) + standalone session-simulation HTMLs:
scripts/run_agent_sim.sh --seed 42 --simulate --personas \
    casefile_only,chat_only,assistant_only,casefile_chat,casefile_assistant,all_channels

# Refresh the lab-transcript utterance corpus after lab sessions
# (read-only harvest; run from backend/):
set -a && . ../.env && set +a
uv run --frozen python -m harness.harvest \
    --dsn "${DATABASE_URL/host.docker.internal/127.0.0.1}"
```

Exit code is `1` when any fail-severity finding is produced, `0` otherwise —
safe to chain in scripts/CI.

### 2.2 CLI reference (`python -m harness.cli`, wrapped by the script)

| Flag | Default | Meaning |
|---|---|---|
| `--manifest` | `tp_demo_v5` | manifest/profile id bound `mode="qa"` |
| `--personas` | `normal` | comma list or `name:count` mix |
| `--seed` | `1` | base seed; session *i* gets `seed*1000+i` |
| `--provider` | `none` | `none` = deterministic fallback · `stub` = canned generator · `ollama` = live model (**worker netns only**) |
| `--paraphrase` | off | rewrite bot utterances via `AGENT_SIM_OLLAMA_URL` when reachable |
| `--dsn` | — | operator-supplied DSN — **full destructive guard applies** (opt-in, `APP_MODE`, live-DSN comparison via the configured env file). Precedence: an explicit `--dsn` always wins; `INVESTIGATORS_TEST_DATABASE_URL` is consulted only when `--dsn` is absent; an ephemeral container is spawned only when neither is set. Whichever database is selected is reset after the guard passes |
| `--out` | `docs/evidence/<date>-agent-sim/` | evidence output root |
| `--wall-step-ms` | `4000` | virtual wall credit per loop iteration |
| `--max-steps` | `400` | loop bound → `RUNNER` fail finding if exhausted |
| `--label` | `adhoc` | free-text label stamped into `summary.md` |
| `--simulate` | off | render each `replays/<sid>.json` into `simulations/<sid>.html` via `node frontend/scripts/render_simulation.mjs` — the same standalone player the admin walkthrough's "Generate HTML simulation" button produces. Needs `node` on PATH (host runs; the worker netns has none → warn + replay JSON kept) |

Environment: `AGENT_SIM_OLLAMA_URL` (+ optional `AGENT_SIM_OLLAMA_MODEL`,
default `gemma4:26b`) enables the paraphrase tier — loopback-only inside the
worker netns, so `--paraphrase` is a no-op on the host. The wrapper exports
`INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS=1`, `INVESTIGATORS_LIVE_ENV_FILE`,
and `INVESTIGATORS_EXTRA_LIVE_DSNS` (the `.env.example` DSN) so the disposable
guard can prove the target DB is not live.

### 2.3 Provider modes — what each actually covers

| Mode | Generation path | Use for |
|---|---|---|
| `none` | jobs run with `provider=None` → deterministic fallback lines | fast mechanism regression: routing, episodes, timing, invariants |
| `stub` | `_StubProvider` cycles four canned lines per seat | the generate→`teammate_output_v*` validate→deliver→`model_call` pipeline without a model |
| `ollama` | real adapter from settings | live-model believability/validation — **only inside `docker exec investigators-worker`** (Ollama is loopback-bound in the worker netns; not reachable from the host) |

## 3. Architecture

```
scripts/run_agent_sim.sh ──► harness.cli ──► simdb.provision ──► Runner
                                │                                │
                        report.run_batch ◄── persona.decide ◄── view.participant_view
                                │                                │
                         oracles.check_session ◄── execute_command (real handlers)
```

| Module | Responsibility |
|---|---|
| `simdb.py` | Disposable PG: ephemeral `postgres:15-alpine` container (tmpfs, random loopback port, `--rm`, `investigators.disposable=true` label) or operator DSN. `assert_disposable` must pass before a single statement; then INV-0301 role bootstrap (`tests.integration.pg_bootstrap.apply_bootstrap`) + Alembic to head. Returns role engines (superuser/migrator/runtime/research/compensation). |
| `clockwork.py` | Time drivers. `set_active` rewrites the open running interval's `active_ms_at_start` (same mechanism as the integration suite) and resets `claimable_at` on pending active-basis jobs; `active`/`advance_active` read via `activetime.active_ms`; `force_wall_due` makes wall jobs claimable; `tick` drives `scheduler.tick` until quiescent (≤6 rounds × 25 jobs). |
| `view.py` | The bot's participant-side projection — stage, revision, `active_ms`, arm, open channels (`resolve_policy(...).channels_open`), committed chat (ordering `coalesce(committed_seq, accepted_seq)`), own file (`file_membership` sources `initial`/`added_from_team` ordered by `timeline_ordinal`), assigned crates (`world_snapshot.content->'assigned'`), open scripted asks addressed to the participant (`request_episode.closed_at IS NULL`, `responder='participant'`), greeting options (`teammates.voice_bank`, allowed during task while `active_ms < 30 s`). The bot sees only what the UI could show — no server secrets. |
| `utterances.py` | Three seeded tiers: pools drawn from observed participant phrasing; human-style mutations; optional local-LLM paraphrase. §6. |
| `personas.py` | 14 personas over a shared `Persona` base (seeded `random.Random` + utterance engine + per-session `mem`). Emits `Action`s — real command kinds plus pseudo-actions `_nop` and `_force_wall{job}`; `revision_offset`/`expect_reject` support hostile-input probes. §4. |
| `oracles.py` | Always-on invariants + persona-declared scenario expectations evaluated against the durable record post-run. §5. |
| `runner.py` | Enrollment (`seed_batch` + `services.entry.enter`, `app_mode="qa"`), stage-spine driver (consent → training ×2 → force `connecting` → `ready` → force `task_open`), the core loop, command dispatch, terminal accounting, `_job_dump`. §3.1. |
| `report.py` | Evidence writer + batch orchestration (`run_batch`). §7. |
| `cli.py` | Argument parsing, provider selection, `_StubProvider`, batch exit code. |

### 3.1 The two-clock loop — the load-bearing design choice

Each iteration of `Runner.run_session`:

```
wall_ms += wall_step_ms            # virtual wall credit
_fire_due_wall()                   # per-job countdown → make due (both halves:
                                   #   claimable_at <= now AND due_wall_at <= now)
tick()                             # worker pass (deliveries, transitions, generation)
advance_active(persona.step_ms)    # only during task/solo_review — the check-in
                                   #   pauses active time exactly as live
tick()
view = participant_view()          # fresh projection
for action in persona.decide():    # _nop / _force_wall / real commands
    dispatch via execute_command + lifecycle.handler_for
                                   # ("complete" → completion_handler, as the API)
tick()
```

- **Active time** jumps by `persona.step_ms` per task iteration — a 360 s
  demo task completes in ~24 steps, a 300 s fielded task in ~20. The bound
  `budget_ms` is read from the resolved policy (`oracles._budget_ms`,
  `personas` budget fractions — 360 s is only the fallback default), so
  personas, oracles and the dead-window all track whatever the profile
  pins.
- **Wall time** is virtual: `_fire_due_wall` tracks a per-job budget measured at
  first sight (`due_wall_at − created_at`) and decrements it by `wall_step_ms`
  each iteration. This is what lets the 120 s `checkin_inactivity` cap fire
  *while the bot is inside the check-in* — the budget starts when the job is
  created, not when the run starts, so the idler dies of the cap exactly as a
  live stalled participant would, while a responsive persona returns to task
  first.
- Every command outcome (accepted or rejected, with reason) lands in the
  transcript; the runner also calls `scheduler.advance_due` inside the command
  transaction, mirroring the API's command envelope.

## 4. Persona registry (P-nn)

| # | Name | Behavior | Expectations |
|---|---|---|---|
| P-01 | `normal` | Greets, interleaves scripted-ask answers (share 55% / typed decline), structured + typed asks, social chatter; submits assigned-crate answers at 85% of budget (`step_ms=15 s`) | `terminal:completion`, `episodes:no_orphaned` |
| P-02 | `asker` | Works through all 8 crates with mixed phrasing — structured (40%, when enabled), addressed/broadcast typed asks (30%), follow-ups (30%) — at `step_ms=8 s` | `terminal:completion`, `structured_response:each_ask` |
| P-03 | `prober` | Normal participant that fires **unmutated** probes from the verified `PROBES` pool at 10/45/80% of the task budget | `terminal:completion`, `probes:all_disclosed` |
| P-04 | `assistant_poker` | Hammers the assistant: single-crate questions (50%), contents/cross-crate questions — the AB-15 reproducer (35%), edge/malformed inputs (15%) | `assistant:no_crash` |
| P-05 | `idler` | Greets, then never acts again (`step_ms=20 s`) | `terminal:technical_stop`, `terminal_reason:checkin_incomplete` |
| P-06 | `sharer` | Resolves every open scripted ask with a matching `record_share` | `terminal:completion`, `episodes:participant_share` |
| P-07 | `chaotic` | Normal behavior + hostile plumbing: stale revisions (`revision_offset −1..−3`), double submits, malformed crates (0/9/−1/99), out-of-stage commands (6% each) | `no_crash`, `integrity:only_expected_rejections` |
| P-08 | `disconnecter` | Acts normally until `active_ms > 100 s`, goes silent for 6 iterations, sends `resume`, finishes | `resume:accepted` |
| P-09 | `casefile_only` | **Channel strategy: case file only.** Reads `initial` records for answers; never greets, asks, replies, or shares | `terminal:completion` |
| P-10 | `chat_only` | **Channel strategy: team chat only.** Greets, asks, replies, shares; answers from `added_from_team` cards (delivered via the channel) or guesses | `terminal:completion`, `structured_response:each_ask` |
| P-11 | `assistant_only` | **Channel strategy: AI assistant only.** Crate + contents asks (~55%/step); the assistant never names hideouts → answers are blind guesses | `terminal:completion` |
| P-12 | `casefile_chat` | **Strategy: case file + team chat** — the classic teammate strategy, no assistant | `terminal:completion`, `structured_response:each_ask` |
| P-13 | `casefile_assistant` | **Strategy: case file + AI assistant** — reads initial cards, asks the bot, silent in chat | `terminal:completion` |
| P-14 | `all_channels` | **Strategy: all three channels** — full information seeking | `terminal:completion`, `structured_response:each_ask` |

**Channel-provenance semantics (P-09..P-14, `ChannelPersona`):** a record's
`file_membership.source` decides which channel it counts as — `initial` is
case-file information; `added_from_team` is *chat* information (it arrives as a
teammate share in the channel, so a chat-ignoring participant never sees it).
`_known_records` enforces this for both sharing candidates and answer evidence:
a `chat_only` player answers from delivered cards, `casefile_only` from the
starting set, `assistant_only` gets no record evidence at all and guesses from
the public hideout list. Scripted asks from teammates go unanswered whenever
chat is off — as a real chat-ignoring participant would. Check-in, instruments,
and the post-task spine are unaffected: channel choice is about *information
seeking*, not study completion.

The assistant's answer-key bound is not a scenario token — it is enforced for
*every* session by the always-on `INV-ASSISTANT-BOUNDED` invariant (§5.1), so
P-04 does not re-declare it.

**Adding a persona:** subclass `Persona`, set `name`/`expectations`, override
the `on_task`/`on_checkin`/`on_posttask` hooks, register in `REGISTRY`. Persona
memory (`self.mem`) resets per session. If the persona relies on a new
utterance shape, extend the pools in `utterances.py` — and if the shape claims
to probe, **verify it against `engines.is_automation_probe` first** (the AB-18
near-miss surface exists precisely because natural phrasings classify
differently than intuition suggests).

## 5. The oracle layer

Two kinds of checks run after every session against the durable record (not the
transcript — the *truth*):

- **Invariants** encode properties that must hold *regardless of what the bot
  did* — implementation defects when they fail.
- **Expectations** (persona-declared tokens) encode what a session of that
  persona *should* produce — a violated expectation means the scenario didn't
  do its job, not that the app is broken.

Severities: `fail` (candidate defect — exits nonzero), `warn` (degradation /
design tension — surfaces for the register without failing the run), `info`
(expectation met, recorded for the log).

### 5.1 Always-on invariants

| ID | Severity | Check |
|---|---|---|
| `INV-UNPROMPTED-SHARE` | fail | every JT/PM share traces to a prompting request (AB-03) |
| `INV-NEAR-REPEAT` | fail (cross-seat) / warn (same-seat) | no two delivered *teammate* lines are `engines.is_near_repeat` near-paraphrases — the AID-36 believability gate. Cross-seat near-paraphrase is the AB-16 threat (seats look copy-pasted); same-seat repeats are warnings — repetitive, but a human re-answering may echo themselves (SQ-9 re-ask frames now soften the common case). Scope: JT/PM only, record-share cards excluded (verbatim by design), canonical `fallback_bank` lines excluded (counted by INV-SCRIPTED-REPEAT instead). Dedupe is per repeated body *at the worst severity seen* — a cross-seat fail replaces a same-seat warn for the same key, so a warning can never suppress a failure (BA-94) |
| `INV-SCRIPTED-REPEAT` | warn | a canonical scripted line delivered ≥3× is conspicuous even though verbatim (e.g. disclosure line ×6 under probing) |
| `INV-PROBE-DISCLOSURE` | fail | every participant message classifying `is_automation_probe` has an `automation_probe` event |
| `INV-SEAT-ATTRIBUTION` | fail | a structured ask's responder is the seat holding the *newest* record for that crate (`world_snapshot.holdings` vs `record.timeline_ordinal`) |
| `INV-ASSISTANT-BOUNDED` | fail | two bounds: (a) no delivered assistant line names >1 crate *and* a hideout location (the answer-key dump); (b) **model-originated** text (`displayed_output.fallback='none'`) must obey the app's own `answer_key_like` gate — >1 crate means the validator's contract was bypassed. The sanctioned `deterministic_contents_summary` route is exempt from (b) — it never reaches the validator |
| `INV-ASSISTANT-REPEAT` | warn | one assistant body delivered ≥3× — the AB-15 redirect dead-end and summary resends surface here |
| `INV-EPISODE-BOOKKEEPING` | warn | closed episodes carry known close reasons (`delivered`/`deadline`/`participant_share`/`session_abandoned`/`no_response`); terminal sessions leave no silently-pending episodes (AB-19 surfaced through this) |
| `INV-RECEIPT-REASON` | warn | every rejected command carries a reason in the 34-entry `EXPECTED_REJECTIONS` whitelist — new rejection vocabularies must land here deliberately |
| `INV-EXPECT-REJECT` | fail | an `expect_reject` action (stale revision, malformed payload, out-of-stage, double-submit) was *accepted* — the hostile-input path failed silently (BA-81) |
| `RUNNER` | fail | `max_steps` exhausted without reaching a terminal stage |

Deliberate scoping decisions worth knowing before trusting a green run:

- `INV-NEAR-REPEAT` checks **believability**, not gate-internal consistency — it
  deliberately re-checks lines the app's own gate exempts (grounded assignment
  statements, scripted lines). That divergence is how AB-16 was found: the
  exemption is a documented engineering choice, but the *impression* a
  collision makes is still a real cost.
- Record-share cards are exempt everywhere: the same record text re-shared is
  correct, not repetitive.
- Scripted `fallback_bank` lines are exempt from near-repeat *failure* but
  counted for conspicuous-repetition warnings — scripted ≠ invisible.

### 5.2 Scenario expectations (EXP tokens)

| Token | Semantics |
|---|---|
| `terminal:<stage>` | session ends at the named stage |
| `terminal_reason:<reason>` | terminal reason matches |
| `episodes:participant_share` | ≥1 episode closed via `participant_share` |
| `episodes:no_orphaned` | zero request episodes pending at end |
| `structured_response:each_ask` | **per-request** correlation (BA-83): each structured ask must close `delivered` on its own episode; zero asks on a structured-capable profile is a *failure* (the scenario never exercised the path); `nl_only` records an explicit not-applicable. A `deadline` close is accepted only inside the **dead window** (`accepted_active_ms ≥ budget − 36 s` = 26.4 s max delivery + ≤9 s typing): personas deliberately keep asking in the window — real participants will too, and an ask with no runway honestly expires at the freeze |
| `probes:all_disclosed` | **per-message** correlation (BA-82): every probe message's `message_id` must appear in an `automation_probe` event payload — aggregate counts can mask an unaudited probe |
| `resume:accepted` | ≥1 accepted `resume` receipt (same-writer resume is a silent lease renew — the receipt, not a `resumed` event, is the durable evidence) |
| `assistant:no_crash` | stage ≠ `aborted` |
| `no_crash` | session reached *some* terminal stage |
| `integrity:only_expected_rejections` | pointer to `INV-RECEIPT-REASON` — passes iff no unexpected reasons appear |

## 6. The utterance engine

Three tiers, all driven by one seeded `random.Random` — a batch is fully
replayable by `--seed` when the paraphraser is off. A fourth source feeds the
pools: `lab_corpus.json` (committed, version 1) — real participant lines
harvested read-only from finished sessions by `python -m harness.harvest`.
`draw()` samples `authored_pool + lab_pool`, so bot phrasing tracks observed
language instead of only what the team wrote (the self-reference problem:
team-written pools test the team's imagination, not participants').
Harvesting is incremental (session ids recorded in the corpus) and replays
safely — the corpus is a file, not a live query. `_classify` skips the
server-rendered structured-request line (`"Requesting intel on Crate N."`) —
that phrasing is emitted by the structured command surface as a message row,
not typed by a participant, so it must not enter `TEAM_ASKS` (where it would
be replayed as authored text; 2026-10-05 fidelity pass).

1. **Pools** (`utterances.py` top): `GREETS`, `TEAM_ASKS`,
   `TEAM_ASK_FOLLOWUPS`, `TEAM_SOCIAL`, `TEAM_DECLINE_ASK`, `ASSISTANT_CRATE`,
   `ASSISTANT_CONTENTS` (the AB-15 reproducer), `ASSISTANT_EDGE` (empty/blank/
   injection/out-of-range), `STOLEN_ITEMS`, `PROBES` (**verified** against
   `is_automation_probe` — mandatory disclosure), `NEAR_MISS_PROBES`
   (verified near-miss — the AB-18 surface). Typed-ask pools deliberately
   exclude `"Requesting intel on Crate {c}."` — that phrasing is the server's
   own render of the structured `teammate_request` command; a typed ask
   parroting UI chrome read as scripted in the replay (2026-10-05).
2. **Mutations** (`mutate`, default intensity 0.35): crate-N form variants
   (`crate 3` → `#3`, `create 3`, bare `3`, `three`, `the third crate`), a typo
   map (crate→create, which→wich, laptops→labtops…), informal casing, dropped
   punctuation, trailing ellipsis, filler prefixes, double spaces — the
   double-space mutation only ever **doubles an existing inter-word space**
   (inserting at an arbitrary position split words mid-token, e.g. "are" →
   "a re"; fixed 2026-10-05).
3. **Paraphrase** (`--paraphrase`): rewrites draws via
   `AGENT_SIM_OLLAMA_URL` (temperature 0.8, seeded). Reachable only inside the
   worker netns; on first failure the engine permanently falls back to
   mutations for that session rather than retrying per line.

Replaying: `sessions/<sid>.json` records the exact command sequence + seed;
rerun `scripts/run_agent_sim.sh --manifest <m> --personas <p> --seed <s>`.

## 7. Evidence layout

```
docs/evidence/<date>-agent-sim/run-<stamp>/
├── summary.md            # run metadata + source bundle stamp + outcome table + all findings
├── sessions/<sid>.json   # per session: steps[] (stage, active_ms, wall_ms, actions w/ receipts),
│                         # transcript[] (every dispatch + outcome/reason), jobs[] (final
│                         # scheduled-job ledger incl. virtual wall budget), findings[]
├── replays/<sid>.json    # the admin-replay payload captured at run end (same shape as
│                         # GET /admin/sessions/{id}/replay, restricted tier, + "persona")
└── simulations/<sid>.html# --simulate only: the standalone workbench player — identical
                          # artifact to the walkthrough's "Generate HTML simulation" button
```

Re-render a replay to HTML any time (host has node + the frontend deps):

```bash
node frontend/scripts/render_simulation.mjs <run>/replays/<sid>.json -o sim.html
```

`summary.md` carries the source-bundle hash at run time — cite it when a
finding promotes to an AB entry (e.g. "agent-sim run-20261004T193346Z,
disconnecter, `aa9f56af…`" — the stamp recorded in that run's summary.md).

## 8. Run log (R-nn) — append batches here as they land

| # | Stamp | Manifest(s) | Personas | Provider | Outcome |
|---|---|---|---|---|---|
| R-01 | 2026-10-04 (run-192550Z … run-193425Z) | tp_demo_v5 ×8, tp_demo_nl_v2 ×3 | all 8 | none, stub | **mixed — the early runs failed real checks; later runs in the batch held.** run-192550Z: 25 fail-severity findings (`RUNNER` max_steps exhausted on `normal`, PM-vs-PM `INV-NEAR-REPEAT`s, `terminal:completion` fail — normal stopped at `data_use_choice`, and the disconnecter PM-vs-JT near-repeat that became AB-16); run-193407Z: `structured_response:each_ask` FAIL (9 delivered vs 0 structured asks — the `nl_only` scenario exercised nothing, fixed as a carve-out in BA-83). The batch's *later* runs held expectations — idler → `checkin_incomplete`, sharer → `participant_share` closes, prober → disclosures, disconnecter → resume accepted. Findings promoted to register: AB-16, AB-17, AB-18, AB-19, AB-15-at-scale. **Caveat:** these runs predate the BA-78–BA-89 remediation — they were produced by the pre-fix guard/oracles and remain evidence for the *application's* behavior on those stamps, not for post-remediation harness correctness |
| R-02 | 2026-10-04 (run-213222Z, run-213722Z) | tp_demo_v5 ×8 | all 8 | none | post-remediation batch on `7c059d17…`. run-213222Z (intermediate): **2 fail-severity findings** — the asker's `structured_response:each_ask` at **21/22** (one ask fired inside the response-dead window closed `deadline` — a real over-ask the tightened oracle correctly caught) plus the documented AB-16 collision. run-213722Z (post-correction): all scenario expectations held — per-request structured delivery (BA-83), per-message probe disclosure (BA-82), enforced `expect_reject` (BA-81). Personas then suppressed asks in the last-10% window — *superseded by R-05*: the suppression was reverted (real participants ask late) and the oracle accepts a `deadline` close only inside the dead window instead. BA-84 replay verified: identical `seed_hex` → identical world content (generated ids excluded); different seed → different world |
| R-03 | 2026-10-04 (run-223632Z, run-223749Z, run-223959Z) | tp_demo_v5 ×5 (normal, asker, assistant_poker, idler, prober) | 5/5 | none | verification batch for the SQ-6–SQ-11 / AB-19/20/21 remediation: **all 5 personas pass, 0 fails** on run-223959Z. The run earned its keep twice mid-batch: (1) run-223632Z caught a `ck_displayed_output_fallback` check-constraint violation — the new `deterministic_contents_summary` provenance tag wasn't in the enum → schema 0007 migration added; (2) run-223749Z surfaced same-seat verbatim assignment repeats (JT×4, PM×4) → `INV-NEAR-REPEAT` rescoped (cross-seat=fail, same-seat=warn) and `grounded_assignment` gained re-ask frames ("still on crates…"). Post-fix: SQ-8 contents summaries deliver (warn-level repeats on a declared bot are honest), SQ-9 frames diverge cross-seat, idler still dies `checkin_incomplete`, **22/22** asks delivered, 3/3 probes disclosed, 0 orphaned episodes |
| R-04 | 2026-10-05 (run-003940Z, run-004059Z, run-004116Z) | tp_demo_v5 ×5 (+1 stub) | 5/5 | none, stub | verification batch for the BA-78-residual / BA-90–BA-94 audit remediation on `d7a10cf9…`: **all 5 personas pass, 0 fails** (run-004059Z). Evidence wins: (1) BA-92 acceptance under `--provider stub` — contents asks delivered the deterministic `Crate n — item` map while other asks got canned text, so valid model output can no longer displace the deterministic route; (2) run-004059Z shows "i'm on crates…" → "**still** on crates…" — `assignment_answers_delivered` now counts pending grounded jobs, so rapid-fire re-asks get the repeat frame instead of verbatim; (3) BA-94 live — same-seat near-repeat reports warn, never suppressing a cross-seat fail. Incidental fix verified: `--out` resolves repo-root-relative so evidence lands in the canonical path instead of inside the source bundle |
| R-05 | 2026-10-05 (run-012215Z) | tp_demo_v5 ×5 | 5/5 | none | verification batch on `668a0e91…` (`oracles_v3`) after the review-driven hardening: identity-collision fix (JT/PM can no longer echo a disclosure line on a broadcast probe — shared draw + per-seat offset), late-ask restoration (the asker fired **28 asks through the freeze**, last action at 364.4 s active — 28/28 delivered, no false deadline-fails), `INV-ASSISTANT-BOUNDED` dual-arm (location pairing + `answer_key_like` on model-originated text), `ORACLE_VERSION` stamped into every summary. Permanent fixtures (AB-06/AB-16 lines, pre-SQ-8 model text) now pin the loosened invariants against their original failure modes |
| R-06 | 2026-10-05 (run-103408Z) | tp_demo_v5 ×6 (P-09..P-14 channel personas) | 6/6 | none | channel-strategy sweep at seed 42: all six `ChannelPersona` strategies completed, **0 fails** — activity volume scales with channels exactly as designed (`casefile_only` 41 events → `all_channels` 141). First batch to exercise `--simulate`: `replays/` + `simulations/` artifacts landed per session, persona label stamped in the sim banner |
| R-07 | 2026-10-09 (run-213114Z, run-213130Z) | tp_ai_v4 ×2, tp_scripted_v6 ×2 | normal, asker | none | **verification batch for the 2026-10-09 pilot revision — the first runs on the fielded 300 s builds: 4/4 sessions reach `completion`, 0 fails.** The bound budget resolves end-to-end (oracle dead-window + persona fractions are budget-relative, no 360 s assumption fired). Job ledger confirms the whole new schedule committed on every session: requests 45/130/210 s, check-in 60 s, `pressure_remark` jobs at **75/83 s**, warning 240 s. Replay inspection shows the arm-correct verbatim lines (high "Yikes this is a lot of info…"/"i know right!", low "Ok this seems manageable…"/"me too! i think") — no dedicated remark invariant exists yet; verification is via the committed job ledger + replay text |

**R-06 post-review (2026-10-05):** reading the generated `all_channels` simulation
surfaced three language-fidelity findings, two fixed same-day. (a) **Sim player
duplicated every participant bubble** — `assistant_requested`/`teammate_requested`
bookkeeping events rendered alongside the `message_accepted` rows; the player now
renders only message rows deduped by `message_id`, matching the participant store.
(b) **Mid-word space splits** — `mutate()`'s double-space inserted at any offset,
producing "a re" from "are"; it now only doubles an existing inter-word space.
(c) **Typed asks parroted UI chrome** — `"Requesting intel on Crate {c}."` sat in
`TEAM_ASKS` (authored + corpus); removed from both and `harvest._classify` now
skips the canonical server render so future harvests can't re-add it. One finding
left **open by design**: a broad assistant ask (`"what stolen objects are in which
crates?"`) draws `deterministic_contents_summary` — the full crate→item map. The
route is intended (bounded asks are the AB-25 fix), but whether an assistant
*should* answer that generously is a disclosure/believability call for the
researcher, not a harness defect.

## 9. Safety invariants — the disposable-DB contract

Non-negotiable, enforced in `simdb.provision` (hardened under BA-78/86/87 — the
guard now receives the *real* configuration rather than manufactured inputs):

1. `INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS=1` must actually be set in the
   environment (the wrapper exports it; a bare `python -m harness.cli` without
   it is refused — no flag is manufactured inside `provision`).
2. `APP_MODE=live` refuses before any schema touch.
3. `assert_disposable` compares the target against every live DSN the guard can
   collect — process env (`DATABASE_URL`, `INVESTIGATORS_EXTRA_LIVE_DSNS`) plus
   the configured live env file (`live_env_file` arg or
   `INVESTIGATORS_LIVE_ENV_FILE`) **and always the project `.env`**
   (`_PROJECT_ENV_FILE`, added under BA-78-residual) — so a bare
   `python -m harness.cli` cannot omit the real live reference just because a
   *different* reference arrived via env. With **no** live reference at all the
   guard fails closed rather than comparing blind.
4. **The harness never runs against a live database** — there is no override
   flag, by design.
5. Ephemeral containers: tmpfs data dir, `--rm`, random loopback port, random
   password, `investigators.disposable=true` label — and **removed on any
   provisioning failure** post-spawn (guard refusal, bootstrap, migration), not
   just on success paths (BA-86). An operator-supplied `--dsn` never removes
   anything, and the wrapper skips spawning a container it would not use
   (BA-87).
6. Bootstrap is the INV-0301 role layout + Alembic head — the same surface the
   integration suite tests against, so findings transfer.

## 10. Known limitations & caveats

- **`--provider none` covers the deterministic-fallback surface only.** Live-model
  believability (generated-line quality, validator behavior under real model
  output) needs `--provider ollama` — phase 2, worker netns only. Since BA-85 the
  mode fails loudly if settings resolve to no/double/remote adapter, and
  `summary.md` stamps requested vs effective provider.
- **In-process dispatch ≠ HTTP.** Commands go through `execute_command` +
  `handler_for` — the real handler path, but not the API's HTTP/auth/middleware
  layer. Endpoint routing, token auth, SSE delivery, and browser-visible
  behavior are untested here — phase 2.
- **`JobLeaseLost` log noise** appears during batch ticks: a batch-claimed
  deliver job gets superseded by an earlier commit in the same pass. Verified
  transient (all final job states `committed`/`suppressed`, no lost
  deliveries). If it ever appears in *live* worker logs, it deserves its own
  audit question.
- **Determinism boundary:** batch replay reproduces persona decisions,
  utterances, and **world draws** (BA-84 — enrollment seed is pinned per run,
  verified identical content minus generated ids); DB-assigned ids (`batch_id`
  suffixes, message/event UUIDs) still differ. `--paraphrase` on reduces
  replayability to the Ollama seed's fidelity.
- **`harness/` is not in the backend image** — the Dockerfile ships `app/`,
  `migrations/`, `alembic.ini` only. Phase-2 containerized runs need
  `COPY harness ./harness`.
- **Source-bundle drift:** `summary.md` stamps the bundle hash at run time —
  always cite it alongside a run id. The tree now moves with *application*
  changes too (the BA-90–BA-94 batch touched scheduler/engines/frontend), so
  a run stamped to an older bundle is evidence about that build, not the
  current one — R-05 (`668a0e91…`, `oracles_v3`) is the latest verification.

### 10.1 Remediation record — BA-78–BA-89 (2026-10-04, bundle `7c059d17…`)

The §21 audit round found the harness weaker than its own claims; all twelve
items are remediated:

| BA | Fix |
|---|---|
| BA-78 | `provision()` reads the real opt-in env, `APP_MODE`, and the configured live env file — no manufactured allow-flag/preview mode; fails closed without a live reference. Unit-tested refusal paths |
| BA-79 | file projection joins `record` on `(session_id, record_id)` — cross-session contamination impossible |
| BA-80 | bot file view carries only participant-visible fields (`shared_by`/`event_type`/`source`, no `location_id`); answers pick hideouts named in visible record bodies, else the public six-hideout list |
| BA-81 | `expect_reject` actions that are *accepted* emit `INV-EXPECT-REJECT` fail findings |
| BA-82 | probe disclosure matches each probe `message_id` to its `automation_probe` event — not aggregate counts |
| BA-83 | `structured_response:each_ask` correlates request→episode per ask; zero-ask on a structured profile is a scenario failure; `nl_only` is an explicit n/a |
| BA-84 | `enter(seed_hex=…)` test-only override (live-refused); sessions derive `sha256("agent-sim:<manifest>:<seed>")` — verified replayable worlds |
| BA-85 | `--provider ollama` refuses `None`/double/remote resolutions; summary stamps requested+effective |
| BA-86 | post-spawn failures (guard/bootstrap/migrate) remove the container and dispose the engine |
| BA-87 | wrapper skips container spawn entirely for `--dsn` runs (no docker required) |
| BA-88 | run dirs are exclusively allocated (`-N` suffix on same-second collision); `summary.md` written last and carries the run id |
| BA-89 | clickthrough + technical appendix regenerated on the new bundle; both `--check` pass |

Plus one found-by-remediation fix: `view.structured_requests` read
`getattr(interaction_mode, "structured_requests", True)` on a *string* — always
`True`; now correctly `interaction_mode != "nl_only"`. And `assistant_poker`'s
no-op `assistant:no_answer_key` token was dropped (the bound is the always-on
invariant's job).

Regression coverage: `backend/tests/test_agent_sim_harness.py` (17 unit tests —
guard refusals, container cleanup, provider truthfulness, run-dir collision,
expect-reject, FileEntry field contract, live seed refusal).

## 11. Phase 2 (not built — do not assume coverage)

**Order matters — reordered 2026-10-05 (RL review):** the live-model and load
paths carry the actual fielding risks; HTTP fidelity is correctness polish.
`--provider none` covers the canned-fallback surface thoroughly, but the demo
profiles field *model-generated* replies and the open risks all live there:
generated-reply believability (AB-06), per-check block rates (O-16), fallback
rates against go/no-go thresholds (O-22), and the concurrency confound
(O-18 — do busy periods get more canned replies? Only a load run can tell
before that becomes a confound in paid data).

1. **Live-model runs** — `--provider ollama` inside `docker exec
   investigators-worker` (or equivalent netns); requires the Dockerfile `COPY`.
   First: it exercises believability + gate block rates under real model
   output, the surface `none`/`stub` cannot see.
2. **Load/soak profile** — concurrent sessions against one disposable DB to
   exercise scheduler contention, lease handoff, rate limits, and the
   O-18 concurrency→fallback-rate relationship (INV-1507 adjacency;
   ≥40-session requirement).
3. **HTTP-fidelity dispatch** — drive the real participant API endpoints
   (session-token auth, envelope serialization, SSE/poll view) instead of
   in-process handlers, keeping persona/oracle/report layers identical so the
   same scenarios verify both paths. *Note (2026-10-09):* a manual real-time
   HTTP smoke was driven against the deployed stack for the v5 builds
   (consent → task → check-in → 45 s request → 75/83 s remarks), but that
   was curl choreography, not the persona/oracle stack — in-harness HTTP
   dispatch remains unbuilt.

## 12. How this file is maintained

- New persona → P-nn row + §4 note; new invariant → §5.1 row (and keep
  `EXPECTED_REJECTIONS` honest); new expectation token → §5.2 row.
- **Oracle changes are versioned and fixture-pinned.** Any semantic change to
  an invariant or expectation bumps `oracles.ORACLE_VERSION` (stamped into
  every `summary.md`). Loosening an oracle is legitimate only when the
  *original failure mode* stays caught — the archived failing transcript
  lines live as permanent fixtures in `tests/test_agent_sim_harness.py`
  (AB-06 convergence, AB-16 grounded collision, pre-SQ-8 multi-crate model
  text, location dumps). If a loosened check can't still fail on its original
  evidence, the fixture fails and the change doesn't land.
- Every verified batch gets an `R-nn` row in §8 with its evidence dir.
- Findings that matter to *participants* promote to `AB-nn` in the behaviors
  register and stay linked by run stamp; harness-mechanics discoveries stay
  here. Open questions that need a decision promote to `SQ-n`/`D-nn` — and a
  ruled SQ that changes *agent* behavior must also land as a dated amendment
  on the affected `AID-nn` entry in the decisions inventory (the SQ→AID hop
  was missed once; don't repeat it).
- Phase-2 items get struck through (not deleted) as they land.
