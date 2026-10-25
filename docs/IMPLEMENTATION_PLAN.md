# Investigators team experiment — implementation plan and task register

**Current authorized pilot, open 2026-10-15 UTC:** four conditions (no overlap
standard/reduced, 12 crates; some overlap standard/reduced, 8 crates), targeting
four valid completers/cell on Prolific with dropout replacement. Old v11 intake
has been closed by audited action. Completion quotas, active holds, exact-cell
pinning and a non-payable test-only smoke policy are deployed on `83443dad…`,
schema 0015. Release 14/14, private backup/restore, historical comparisons and
four uninterrupted real-model browser completions passed, with 56 paired item
timings. Only `study.live.v12` is open; its four cells had zero allocations and
four available places each at opening. Both test groups are closed.
No old bindings or allocations are rewritten. The owner publishes the external
listing; Droid has not published it.
Current selection/operations: `docs/FOUR_CONDITION_PROLIFIC_PILOT.md`.

### Previous verified release (superseded operational status)

**Current status, verified 2026-10-14T23:23Z:** student-preview fixes are **deployed and verified**, retaining earlier audit fixes. Build: `eb173d9fad41b1612544dbf49772ad261c0cd62d5bec3ba292c555be24349058` (`2026-10-14T23:19:48Z`); schema: **`0014_nopress_arm`**; API/worker/UI healthy. **Release gate: 14/14 PASS** (2,060 backend units, 453 disposable-DB integrations, 273 frontend tests, 124 contracts). Seven faithful Chromium conditions completed (five continuous, two resumed after a driver hydration race); seven final public mocks passed responsive geometry/code checks. Final signed-preview consent → real-model Q&A → Connecting → task PASS, without an intake toggle. **No new fielding, live rebinding or historical rewrite.** All intake settings, flags, allocation counts and real funnel remain unchanged. Live-intake exposure remains **owner-accepted, not fixed**. Evidence: `docs/evidence/2026-10-14-student-ux-remediation/`.

**Previous release summary (superseded):**
- `study.live.v11` is **OPEN**, not preview-only. Both member batches (`.ai`, `.ai2`) bind `tp_ai_v13` and remain individually closed. The read-only 2026-10-14 snapshot contains **34 completions, 4 abandoned, 2 declined, and zero active live sessions**. The N=30 cohort has reached 34 completers; enrollment has not automatically closed.
- The current study uses AI teammates and a 300-second task. **Time pressure is an optional deployment feature, not a fundamental study element** (owner clarification 2026-10-14). It remains disabled, and new consents allocate `nopress`, one cell per member. Historical high/low identifiers are preserved, displayed as distinct variants in operational admin labels, and do not alone establish delivered manipulation. Neutral allocations remain neutral after a later feature re-enable. Published materials and historical design records below are unchanged.
- Verified flags: `SCRIPTED_TEAMMATES_ENABLED=0`, `AI_ASSISTANT_ENABLED=0`, `CRATE_OVERLAP_ENABLED=1`, `CRATE_NUMBER_ENABLED=1`, `TIME_PRESSURE_ENABLED=0`, `AI_QA_ENABLED=1`. These deployment flags also affect live intake; they are not restricted to internal previews.
- **Owner-accepted residual risk:** after the audit identified that exposure, the owner explicitly directed **“Leave it open; fix code only.”** Live enrollment and flags are unchanged. The exposure is **accepted, not remediated**; this plan does not claim a fresh scientific freeze or authorize additional recruitment.
- Code fixes cover visible pressure-off/rescue transitions, pre-boundary pressure flips, consent-pinned Q&A identity and prompt/parameter provenance, the 20-second Q&A deadline, projection retry without new events, smoke failure evidence/cleanup, rapid mock exchange ownership, null-safe analysis cells with unsupported pooling refused, and snapshot-only replay.
- Neutral-arm fixes also prevent allocation slot reuse across repeated arm-count changes, retain historical balance cells with current-allocatability markers, and preserve neutral treatment across runtime/commands/instruments/Q&A/reporting.
- Current internal previews use `tp_ai_v14` plus the six `tp_ai_ov{none,some,full}[_thin]_v3` successors, consent v9/training v8. Old signed links and live batches retain their old immutable pins. Seven fresh unopened links expire 2026-10-15 23:22:47 UTC. New completions display `PLACEHOLDER-CODE`; historical handoffs stay unchanged.
- The three confirmed student previews were audited, not four: all final responses and 14 paired item timings/session are present. Fixed duplicate questionnaire proceeds, competing crate-answer saves and own-share render linkage. Completion now uses session mode for payment eligibility; historical incorrect preview `pending` 350-cent rows remain preserved but are blocked from approval/payment and excluded from the payment queue.
- Engineering tests do not close research-owned decisions D03/D08/D09/D11/D12/D14, WP10b formal acceptance, remaining ops/release gates, or expansion approval. Historical fielding and implementation records below remain historical.

### Historical implementation overview (2026-10-12, superseded by the current summary)

**Original plan date:** 2026-09-28 (revised the same day to remediate [INVESTIGATORS_TEAM_EXPERIMENT_PLAN_AUDIT.md](./INVESTIGATORS_TEAM_EXPERIMENT_PLAN_AUDIT.md) findings AUD-01–AUD-21)  
**Status:** N=4 pilot complete; preview-only posture — WP00–WP09, WP10a, WP11, WP12a, WP13, WP17 (Phase A) complete; WP16 initial-pilot execution verified on `study.live.v10`. Deployed build `9ae1c609`, `APP_MODE=live`. **Current fielded design is 1×2** (AI teammates × high/low pressure; AID-63): `study.live.v10` fielded `tp_ai_v11` only and closed 2026-10-12 after N=4 completers (§22). The N=30 effect-size/power cohort and the N=200+ preregistered study carry **no AI-vs-scripted contrast**. Scripted-teammate code/profiles remain behind `SCRIPTED_TEAMMATES_ENABLED=false` for comparison/QA and historical reconciliation only. **Current preview/testing manifest is `tp_ai_v13`** (BA-105: `tp_ai_v12` + the artifact-bound `open_ended.tp_followup@v1` pin; v12 = `training.reference@v6` share-only practice gate) — used by admin-issued signed preview links only; no live batch binds it and nothing is launched or enrolled on it. Testing posture is preview links only (`POST /admin/previews`; the admin Links panel auto-selects the newest `tp_ai_*` profile). Recent fielding/tooling changes — dashboard version filter + session tooltips, verbatim `item_response`/`chat_text` exports, redaction-tab removal — are logged in §22 (see §3 for the decision register and remaining research-owned items D03/D08/D09/D11–D13). Historical fielding references (`prolific.live.n5`, `tp_review_first_v10`, `study.live.v5`–`v9`) remain below for auditability. The BA-01–BA-31 audit remediations are complete (`docs/evidence/2026-09-29-audit-remediation/`, `docs/evidence/2026-09-30-audit-remediation-2/`); the second-round BA-96–BA-102 remediations are recorded in `docs/evidence/2026-10-07-audit-remediation/`. Live model generation is deployed (Ollama `gemma4:26b` sidecar, `local_openai_chat` adapter, per-call digest-pinned audit; remote `openai`/`minimax` adapters confined to offline fixture comparison per BA-35). The 2026-09-29 sequencing direction (bundle all IRB-relevant decisions into one amendment round) was superseded 2026-10-06/07: an IRB office-hours consultation confirmed the blanket protocol covers the fielded changes, the owner directed fielding the N=5 pilot directly, and D01/D02/D04/D06/D07/D10/D13 are recorded resolved in `docs/DECISIONS.md` and §3. Still open and non-blocking for this pilot: D03, D08 residual, D09 residual, D11, D12, D14; WP10b formal acceptance (INV-1009), WP14 ops items, WP15 remaining gates, WP16 expansion. Writing or revising this document completes no application, validation, deployment, or recruitment task. Pre-build infrastructure done by the owner on 2026-09-28:
- The `/investigators/` vhost block is applied to `/etc/httpd` and reloaded; the service-dependent checks in INV-1401 are now verified against the running deployment (marked done).
- `./.env` was created from `.env.example`.  
**Workspace:** `./` (filesystem directory; the participant URL route is `/investigators/`)  
**Authoritative design:** [INVESTIGATORS_TEAM_EXPERIMENT_DESIGN_SPEC.md](./INVESTIGATORS_TEAM_EXPERIMENT_DESIGN_SPEC.md)  
**Pinned input snapshot (SHA-256):**

| Artifact | SHA-256 |
|---|---|
| Design specification (revision 2026-09-28, incl. owner decisions; model-authorization correction; Appendix A re-pinned for the 2026-10-01 PE demo; 2026-10-01 amendment + revised-demo reconciliation edits; 2026-10-02 BA-35/BA-41 edits + BA-38/BA-40 correction edits + 2026-10-02 UX-round-2 annotations + 2026-10-03 O-4 auto-file wording + 2026-10-03 gemma4:26b switch annotations + 2026-10-04 lab-walkthrough resizable-workbench clause + 2026-10-05 UX-round-4 training-flow clause + 2026-10-06 S2-demo appendix repoint to the archived copy + 2026-10-07 D10/BA-99 fielded-warning-version annotations + 2026-10-07 current-build reconciliation + 2026-10-07 batch-vocabulary alignment + 2026-10-10 fielded-line annotation (study.live.v7 supersession note) + 2026-10-10 incentive-compatibility annotation (study.live.v8 / consent v7 / payment v5) + 2026-10-11 open_ended-stage descriptions + fielding-pause/testing-pair annotation + 2026-10-12 BA-110 fielded-profile annotation (study.live.v10 closed / AI-only / tp_ai_v13 preview) + 2026-10-15 demo-file-stem rename annotation (live template now `investigators_demo.html`; archived S2 reference keeps its frozen name)) | `b158a25801393395e2cfe825a68bd8d84a6a67294435d4144f5514ea470069e3` |
| Hidden-bench insights (supporting; link paths updated for the directory move) | `4bb1e5145c494176e27ecc8a8bd94986612bb0f20d5949282c8566271e356240` |
| Timing insights (supporting; link paths updated for the directory move; I2 row repointed to the archived S2 demo) | `0aaefc64a05bfd5cf174613654db2bbd1b1116776a275c806cc088291566f43b` |
| `XXXX-XXXX IRB Approved Protocol.pdf` (DOCX hash is in spec Appendix A) | `a9f89f03c35562e6e5e10ba25560a3f91e1114bbad8e7ec36106be7a81298955` |
| `apache/investigators.vhost.snippet` (applied ingress, Shibboleth admin-allowlist layout per 2026-10-11 security note, no `.incl`) | `acfe4183a0ba485a62debece8f7845c17da8fa4353909765a28e8b8741e43107` |
| `.env.example` (settings template; no secrets; `AI_ASSISTANT_ENABLED` flag added 2026-10-14; `CRATE_OVERLAP_ENABLED`/`CRATE_NUMBER_ENABLED` added 2026-10-15; `TIME_PRESSURE_ENABLED` added 2026-10-15; `AI_QA_ENABLED` added 2026-10-16; `BONUS_PAYMENT_ENABLED` added 2026-10-16) | `0fa6960191de225e5f2571971a16e93f0a515216daf48bd227a40018d1099faa` |

## 1. Purpose and operating rules

Implement one durable experimental platform for the teammate time-pressure and peer-evaluation Investigators studies: one participant, two agent teammates, a simultaneous workbench, controlled evidence access, a 360-second active task, and reproducible measurement. The design specification defines required behavior; this plan defines the work, dependencies, evidence, and release sequence needed to deliver it. If they diverge, resolve the design in the specification and update the affected tasks rather than treating this plan as a competing source of scientific requirements.

The hidden-bench and timing evaluations remain supporting evidence, not additional implementation backlogs. Their adopted requirements are in the specification (spec Appendix B). §9 traces every spec section and every adopted HB/Timing recommendation to owning tasks; INV-0007 keeps that trace current.

### 1.1 Starting point

The inspected `investigators/` directory contains the two HTML demos, the consolidated design specification, both evaluation reports, the plan audit, the Qualtrics pilot DOCX, and approved-protocol DOCX/PDF, plus `apache/investigators.vhost.snippet`, `.env.example` (settings template) and the owner-created `.env` (contents not inspected). No `backend/`, `frontend/`, migrations, production tests, or Compose service exists at planning time. This is a source inventory, not a claim about every live service elsewhere on the host.

Host facts relevant to the build, checked read-only on 2026-09-28:

| Fact | Consequence |
|---|---|
| Ports 8518/8519 are free; `/investigators` → 301 and all other `/investigators/*` paths → 503 through the live proxy. | Ready for the first containers. |
| No `investigators_db` exists yet. Host PostgreSQL 15 listens on 5432; PCS/DCT reach it via `host.docker.internal` with `extra_hosts: host-gateway`. | Database and role creation is an operator step (§2.3). |
| `pcs/` and `dct/` each own a Compose file with build context `..`, `env_file: .env`, loopback ports, a healthcheck, and a read-only mount of the shared `study_admin_common` library. | This is the local template for INV-0106. |
| `./estate/update_permissions.sh` (and `./estate-scripts/fix-dotenv-perms.sh`) apply the estate `.env` posture. | Use them to secure `.env`. |

Preserve both demos as behavioral references. Build the shared engine separately. Do not transplant hidden-bench's database, Triad's room loop, or another project's service names, routes, model choice, or acceptance results.

### 1.2 Task and evidence conventions

- Every checkbox below is an implementation TODO. Stable IDs remain unchanged when work is split or reordered; newly discovered work receives a new ID.
- An unchecked task may be not started, in progress, or blocked. Record its state, owner, blocker, and next step in the working status ledger; do not mark a blocked task complete.
- Mark a task checked only when its artifact exists and the relevant verification evidence is recorded. A code path, configuration field, or passing helper test alone does not establish the promised runtime behavior.
- Each work package names an accountable role. These are responsibilities to assign, not assumed staffing or instructions to spawn agents. One person may hold several roles.
- Store gate evidence by release and exact build/manifest hashes. Statuses are `not_run`, `in_progress`, `pass`, `fail`, or `blocked`; missing evidence cannot become PASS.
- Work using local fixtures, reversible source edits, and isolated test resources can proceed independently of unresolved fielding decisions. Prepare concrete configurations and evidence before the final launch decision.
- Deployment, provider activity, participant recruitment, and payments are future tasks. Revising this plan performs none of them.
- Privileged host actions (Apache vhost, Shibboleth, cron/backup registration, systemd) are prepared as files or `/tmp/*.sh` scripts and **executed by the operator with sudo** (`/opt/AGENTS.md` sudo-script workflow). Agents never run sudo.

Proposed working records under `investigators/docs/`: `IMPLEMENTATION_STATUS.md`, `DECISIONS.md`, `REFERENCE_CROSSWALK.md`, `REQUIREMENT_TRACE.md` (seeded from §9), `TIMING_CONFORMANCE_MATRIX.md`, `DATA_DICTIONARY.md`, `OPERATIONS_RUNBOOK.md`, and `releases/<release_id>/`. These support execution and evidence; scientific requirements remain in the specification.

## 2. Delivery milestones and dependencies

| Milestone | Deliverable | Work packages | Exit evidence |
|---|---|---|---|
| M0 — Frozen contracts | Reference inventory, schemas, decision register, requirement trace, module/API boundaries; external lead-time requests opened | WP00–WP02 | Materialized profiles and representative fixtures are reviewable; unresolved choices are explicit; every spec clause maps to a task (INV-0007); §2.3 requests are filed. |
| M1 — Durable task core | Enrollment, world, permissions, commands, clock, jobs, deterministic teammates | WP03–WP07 | A synthetic session survives restart and reaches one frozen result without a real model. |
| M2 — Complete browser experiment | Shared workbench, both studies, training, post-task, debrief and completion | WP08–WP09 | All eight profile/arm combinations complete with deterministic providers and recoverable data. |
| M3 — Controlled inference core | Snapshot-grounded assistant/social generation, validation, bounded fallbacks on provider doubles | WP10a (parallel with M2) | Adversarial fixtures meet the selected policy on provider doubles. |
| M4 — Research-ready records | Exact-event exports, semantic coding pipeline and development set, researcher controls, identity lifecycle | WP11, WP12a, WP13 | Full synthetic data reconcile; coding pipeline complete; semantic validity explicitly **unavailable** until WP12b. |
| M5 — Operational release candidate | Integrated inference and real-provider evidence, applied ingress, browser/load/failure/security and recovery evidence | WP10b, WP14–WP15 | G1–G9 evidence and pre-launch G10 requirements recorded for the actual release. |
| M6 — Audited initial pilot | Resolved fielding decisions, usability evidence (D13), bounded pilot, complete initial output audit, then real-corpus semantic validation | WP16, WP12b | Initial pilot reviewed; expansion has its own recorded decision and pinned release; semantic gate PASS or explicit unavailability. |

**Critical path:** WP00 → WP01/WP02 → WP03 → WP04/WP05 → WP06 → WP07 → {WP08/WP09 ∥ WP10a} → INV-0911 pre-freeze usability (D13→D10) → WP10b → WP15 → WP16 → WP12b (semantic use only). WP11, WP12a, WP13 and WP14 must converge before release; they are not optional cleanup after recruitment. The §2.3 external track runs in parallel from M0 and can itself become critical (notably D03/D04 amendments and D05 capacity). WP12's semantic certification gates semantic outcomes rather than blocking valid exact-event analysis solely because an optional coded measure is unavailable.

Independent work can overlap after interfaces are agreed: world fixtures with database scaffolding; browser components with typed API mocks; coding rubric preparation with engine construction; operations configuration with model-double testing; WP10a validation with WP08/WP09 browser work. Integration acceptance waits for real dependencies. Avoid a large final integration step by completing the first vertical slice early.

### 2.1 First end-to-end slice

Build a preview-only TP reference session with one fixed world, deterministic replies, one request/record transfer/Add-to-file sequence, one assistant model-double response, a check-in pause, one answer revision, the “One minute left.” warning at active 300, timeout, and completion. Persist it in disposable PostgreSQL; replay it after API/worker restart. Then generalize the same contracts to all profiles, rather than building a polished browser around temporary in-memory state.

### 2.2 Reviewable change boundaries

Use one coherent module or contract change per reviewable change set, with a source-bundle checksum (the monorepo is single-operator and edited in place). Keep schema migrations paired with compatible API changes. Prefer additive migrations and explicit backfills; test previous-release compatibility before relying on rollback. A work package may span several change sets, but its exit evidence must cover their integrated behavior.

### 2.3 External lead-time track

These items depend on people or systems outside engineering control. Open them at the milestone shown; record owner, request date, and status in `DECISIONS.md`/`IMPLEMENTATION_STATUS.md`.

| Item | Why it has lead time | Start by | Needed by | Owning task/decision |
|---|---|---|---|---|
| PE evaluation-expectation approval (D03) | S5 states participants are not evaluated; S3 tells them teammates will rate them. Likely IRB amendment. | M0 | Paid PE batch (WP16) | D03, INV-0910 |
| Compensation/bonus approval (D04) | Demo $0.25/crate bonus not selected in S5. Possible amendment. | M0 | Payable batch | D04 |
| Local model and GPU capacity (D05) | **Resolved — IRB amendment approved 2026-10-01** (protocol XXXX-XXXX, item 1): Gemma 4 (or latest released Gemma) self-hosted on review-host via Ollama, SHA-256-verified frozen weights, per-message model/version logging, no third-party transmission except a **university AI service fallback when the GPU is overwhelmed**. Deployed: `gemma4:26b` (digest-pinned per call; switched from `gemma4:12b` 2026-10-03) on the Ollama sidecar sharing the worker's network namespace — literal loopback per INV-1001. Remote `openai`/`minimax` adapters exist for offline lab A/B comparison but are **not** university AI service endpoints — refused for all session traffic per BA-35 (worker startup refuses a remote `MODEL_PROVIDER`; `run_generation` refuses per job). A live congestion fallback per the amendment needs a university AI service adapter (not yet built). | Done | WP10b formal acceptance | D05, INV-1009 |
| Ingress apply and verify | Shared vhost for every app; operator-applied. **Applied 2026-09-28** (`Syntax OK`, graceful reload). Service-dependent checks remain. | Done (apply) | INV-1509 | INV-1401, INV-1403 |
| Admin password in `./.env` | Apache does not enforce SSO on admin paths under the selected layout (spec §3.1). `ADMIN_PASSWORD` and `ADMIN_EMAILS` live in `.env`, as for `pcs/.env` and `dct/.env`. No one-off `.incl` file. **`.env` created 2026-09-28 and secured** (640, group `docker-users`, `u:admin1:r` ACL, via `update_permissions.sh`); re-run that script after any `.env` recreation. | Done | Before any secret is relied on | INV-0103, INV-1301, INV-1403 |
| Host PostgreSQL database and roles | Creating `investigators_db`, the migration/runtime/research/compensation roles (INV-0301) and a separate disposable test database for `INVESTIGATORS_TEST_DATABASE_URL` needs PostgreSQL admin rights on the host cluster. **Done 2026-09-28 (operator):** `scripts/db/inv0301_bootstrap.sql` created `investigators_db` (owner `investigators_migrator`), login roles `investigators_user` (runtime), `investigators_research`, `investigators_compensation`, schemas `app`/`identity` and default privileges; runtime login from `.env` verified. Host test database intentionally not created (tests use a throwaway container). Migrations/role tests remain under INV-0301/0308. | Done | WP03 integration tests | INV-0104, INV-0301 |
| Admin allowlist | **Confirmed 2026-09-28:** `admin1@university.edu`, `admin2@university.edu`. | — | — | INV-1301 |
| Backup registration and WAL/RPO arrangement | Host cron/backup ownership; RPO ≤15 min needs more than nightly dumps. | M3 | M5 | INV-1405 |
| Platform study setup (Prolific/Connect) and exclusion lists (D08) | External accounts and prescreeners. | M4 | Live enrollment | D08, INV-0404 |
| Usability round approval (D13) | May require approval to run staff/lab sessions. | M2 | Before the D10 freeze | D13, INV-0911 |

## 3. Research decisions and launch dependencies

Research leadership owns the scientific/material decisions; engineering records the selected values and implements versioned alternatives. The following register operationalizes specification §13 (including the rows added in its 2026-09-28 revision). None requires stopping unrelated foundation work.

> **Supersession marker (2026-10-14):** several "Resolved … fielded" selections below record the *2026-10-07 review-first fielding* (`tp_review_first_v10` / `prolific.live.n5`) and are historical, not current bindings. Current intake is **open `study.live.v11`**, AI-only members on **`tp_ai_v13`**; `study.live.v10`/`tp_ai_v11` is closed historical fielding. The v11 manifest selects consent `consent.reference@v8` (~12 min, $3.00 + up-to-$0.50 bonus eligibility, all items required), payment `payment.s5_base_only@v5` (350¢ flat), seat_plan requests 45/100/155/210/245 s, `prompt.social_demo@v4` + `prompt.assistant_demo@v4`, `post_task.tp_demo@v5`, and artifact-bound `open_ended.tp_followup@v1`. The current deployment disables assistant/time-pressure surfaces and enables rules Q&A; see the current summary and §3.1 for the accepted intake exposure. D03/D08/D09/D11/D12/D14 remain unresolved; D12's N=30 direction is historical recruitment intent, not an automatic stop.

| Decision | Required recorded outcome | Work affected / deadline |
|---|---|---|
| D01 — TP exposure/profile | **Resolved 2026-10-07** — review-first family selected and fielded as `tp_review_first_v10` (published; live batch `prolific.live.n5` binds it): 60 s review with teammate and AI channels closed, scripted requests at interaction 20/120/210 = active 80/180/270, no early-submission endpoint. Supersedes v9 (first live-eligible promotion, 2026-10-06). | WP02/WP09; fielded. |
| D02 — Pressure ratings | **Resolved 2026-10-07** — high arm displays 6/7, low arm 2/3 (demo S2 values retained with version indicator via `checkin.pressure_demo@v2`); JT/PM mapping is `independent_fair_coin` per the bound manipulation block, satisfying §7.1 invariant 7 | WP02/WP05/WP09; fielded in `tp_review_first_v10`. |
| D03 — PE materials | Reconcile evaluation expectation with applicable approved protocol/amendment; freeze disclosure/debrief wording; the INV-0910 contamination audit is recorded in `docs/PE_CONTROL_CONTAMINATION_AUDIT.md` — its flagged question (confirm revealing the manipulation premise to control participants post-task via the shared suspicion items is acceptable) is now an explicit ask in `docs/requests/D03_pe_evaluation_expectation.md` | WP09/WP16; before paid PE recruitment. |
| D04 — Compensation | **Resolved 2026-10-07** (revised selection under the BA-97 audit remediation) — $4.00 base, no bonus, `ratings_contingent_pay: false`; payable outcomes are `completed` and `technical_stop` (a technical stop is not "leaving early"); `abandoned`, `withdrawn` and `declined` forfeit the base per `consent.reference@v4` ("leaving early is unpaid") — bound as `payment.s5_base_only@v3` (activatable) with `withdrawal_payment: forfeits_base` in `tp_review_first_v10`; `_entitlement` branches on outcome × policy | WP09/WP13; fielded. |
| D05 — Local inference | **Resolved 2026-10-01 by IRB amendment** (protocol XXXX-XXXX, item 1): Gemma 4 (or latest released Gemma) on review-host via Ollama, frozen SHA-256-verified weights, per-message model/version logging, no off-host participant text except the **university AI service** congestion fallback. Deployed `gemma4:26b` (digest-pinned per call; switched from `gemma4:12b` 2026-10-03); the same provider boundary applies to offline coding. Residual: INV-1009 formal acceptance evidence, and a university AI service adapter if the live fallback is ever wanted | WP10b/WP12/WP14; resolved for acceptance purposes. |
| D06 — Instrument/skip policy | **Resolved 2026-10-07** — literal participant copy frozen in the approved artifact successors bound by `tp_review_first_v10` (`consent.reference@v4`, `instruments/post_task.tp_demo@v3`, `instruments/confidence.demo@v2`, `instruments/debrief.tp_reference@v2`, `instruments/training.reference@v2`, `checkin.pressure_demo@v2`, `voices.tp_demo@v2`, `requests.demo@v3`); check-in own rating is skippable (consent states skipping questions does not affect payment) | WP09; fielded. |
| D07 — Teammate policy | **Resolved 2026-10-07** — generated (not scripted) teammate and assistant contributions per the 2026-10-01 amendment: `social_generation: model_with_validated_fallback` with pinned `prompt.social_demo@v3` and `prompt.assistant_demo@v2`, `local_openai_chat` on digest-pinned `gemma4:26b`, `grounded_prompts_v5`, `teammate_output_v7` validation, deterministic fallback as the degraded path only; contents questions route deterministically (BA-92); no assistant referral; request-sender independence via the coin-drawn majority seat (TP-only — PE retains the fixed multiset, recorded as out of scope for this fielding) | WP07/WP10; fielded in `tp_review_first_v10`. |
| D08 — Recruitment/eligibility | Select platform adapters, cross-study repeat rule, identifiers and batch linkage | WP04/WP13; before live enrollment. |
| D09 — Withdrawal/retention | Confirm participant data-use posture and **final data-use wording**. 2026-10-01 UX round: the participant-facing screen now shows contact information only and records the `use_all` default — whether the protocol requires offering an in-app deletion route is part of this residual. Also identity deletion schedule, research retention, backup tombstone handling, and whether initial-pilot text may form the WP12b held-out corpus | WP12b/WP13/WP14; before fielding. |
| D10 — Timing policy | **Resolved 2026-10-07** — reference mechanisms frozen per `REFERENCE_CROSSWALK.md` §4 as bound in `tp_review_first_v10`; warning `warning.one_minute_left@v2` (approved republish of the frozen v1 body — identical wording, active-300 trigger, low-time style; spec §4.1 annotation) at active second 300 | WP02/WP06; frozen for the N=5 fielding. |
| D11 — Semantic validation | Freeze rubric, development and held-out corpora, minimum held-out size per construct and arm, coverage and construct-specific thresholds | WP12a/WP12b; rubric before tuning, thresholds before semantic analytic use. |
| D12 — Analysis/pilot size | Record initial pilot size, primary outcomes/estimands, exclusions, missingness and expansion criteria; power separately for confirmatory use. *Owner direction 2026-10-12:* the next cohort is **N=30 for effect-size estimation and power analysis toward the as-predicted pre-registration (N=200+)**; it contains no AI-vs-scripted contrast (1×2 AI-only, AID-63) | WP11/WP16; before interpreting/expanding pilot. |
| D13 — Pre-launch usability evidence | **Resolved 2026-10-07 by election of the second option** — the initial pilot (`prolific.live.n5`, N=5) explicitly serves the pre-launch usability role: it exercises timing plausibility, world difficulty and discoverability on the fielded manifest while remaining non-confirmatory (D12 governs interpretation/expansion). The earlier lab walkthrough (E14, INV-0909) plus the three live smoke sessions preceded it | INV-1609 re-verifies this record pre-expansion. |
| D14 — Technical-incident policy | Select clock treatment during server/scheduler outage, flagged-interval schema, participant-facing handling, payment treatment and analytic flagging | WP06 (INV-0612)/WP16; before real timing acceptance. |

Each decision record contains status, selected value, rationale, applicable approved material/version, responsible reviewer, date, affected profiles, and tasks requiring revalidation. Record unresolved values as unresolved, not operational defaults. Proposed preview settings remain visibly preview-only in researcher controls.

### 3.1 Current fielding state (verified 2026-10-14)

| Surface | Actual state |
|---|---|
| Real-participant intake | `study.live.v11` **OPEN**, left unchanged by explicit owner direction |
| Live bindings | `.ai` and `.ai2` both bind `tp_ai_v13`; individual member enrollment closed |
| Current v11 funnel | 34 completed, 4 abandoned, 2 declined; zero active at the read-only pre-deploy check |
| Smoke intake | `study.smoke.v11` closed between checks; `TEST_*` sessions only, excluded from analysis/payment |
| Flags | scripted teammates off; assistant off; overlap/number gates on; time pressure off; rules Q&A on |
| Published design versus runtime | AI-only 1×2 manifest remains bound, but the pressure manipulation is currently disabled by the deployment flag |
| Allocation and historical balance | New consents use `nopress`, one cell per member. All 38 historical v11 allocations remain visible (19 high/19 low); v10 shows all 6. Historical rows are unchanged. |
| Deployed remediation | `c20e12db…`, schema `0014_nopress_arm`, API/worker/UI healthy; §24 evidence |
| Accepted exposure | Internal-testing flag posture reaches open live intake; owner declined closure. This is not a resolved audit finding or a new research freeze |
| Historical rounds | v8/v9/v10 closed; the v10 N=4 pilot and its `tp_ai_v11` binding do not describe current intake |

`tp_ai_v13` is both the current live manifest and available for signed previews,
not preview-only. No live enrollment or feature-flag change is part of this remediation.
Operational launch/closeout records remain in `PRE-LAUNCH_CHECKLIST.md`,
`POST-LAUNCH_CHECKLIST.md`, `INVESTIGATORS_TEAM_EXPERIMENT_PROLIFIC_CONNECT.md`,
`docs/DECISIONS.md`, and `docs/IMPLEMENTATION_STATUS.md`; read their dated snapshots
as historical when superseded by this verified state.

#### Historical 2026-10-07 review-first snapshot

Recorded under the second plan-audit remediation round (BA-96–BA-102; `docs/evidence/2026-10-07-audit-remediation/`). Owner direction 2026-10-06: field the N=5 pilot directly on `live` rather than a `qa` intermediate; IRB office-hours consultation confirmed the blanket protocol covers these changes (main-campus IRB, protocol XXXX-XXXX + the 2026-10-01 amendment) — no further amendment was required for this fielding.

**Fielded configuration.** `APP_MODE=live`. Batch `prolific.live.n5` immutably pinned `tp_review_first_v9` at creation (batch→manifest bindings are write-once), so the remediated manifest is fielded through successor batch **`prolific.live.n5.v10`**, created against published manifest **`tp_review_first_v10`** (`material_status: approved`, `modes_allowed` includes `live`, all bound artifacts `status: approved` — the live gate was narrowed back to approved-only under BA-99). `prolific.live.n5` remains permanently paused with only its three smoke sessions. Entry is the platform-parameterized link documented in `INVESTIGATORS_TEAM_EXPERIMENT_PROLIFIC_CONNECT.md`.

**Actual generation mode.** `social_generation: model_with_validated_fallback` — non-contents social replies and assistant requests run through `local_openai_chat` against digest-pinned `gemma4:26b` (`5571076f…d251`) with `grounded_prompts_v5` construction, `teammate_output_v7` validation, per-call `model_call` audit rows, and deterministic fallback as the degraded path. Contents questions route deterministically by design (BA-92). Integration evidence: `backend/tests/integration/test_audit_ba96_102.py` records digest-pinned `model_call` rows on the fielded manifest.

**Decisions resolved vs intended.** Resolved for this fielding (§3): D01, D02, D04, D05, D06, D07, D10, D13. Still open and **not** blocking the N=5 pilot: D03 (PE materials — the pilot is TP-only), D08 (recruitment/eligibility — Prolific selected operationally; cross-study repeat rule and exclusion lists remain), D09 (withdrawal/retention — interim posture: consent-rule base forfeiture recorded under D04; identity-deletion schedule and data-use wording residuals remain), D11 (semantic validation — no semantic analytic claims will be made from the pilot), D12 (pilot interpretation/expansion), D14 (technical-incident policy — `technical_stop` payment treatment is decided; clock/flagging schema remains).

**Payment posture.** `payment.s5_base_only@v3`, activatable: `completed`/`technical_stop` → $4.00 base; `abandoned`/`withdrawn`/`declined` → ineligible (consent-verbatim rule). The three pre-remediation smoke entitlements were disposed `ineligible`/0¢ with recorded reasons (BA-97).

**Gates.** The 13-step release gate passed on the remediated build `71a98e68…547e` (`docs/evidence/2026-10-07-audit-remediation/`; the prior v9 build's gate is in `docs/evidence/2026-10-06-live-promotion-v9/`). Deployment verified: `/ready` 200, `/version` reports the gate source with `pinned_build: true`, live bind exercised by batch creation, entry→consent→allocation smoke-verified. Not run for this fielding: WP10b real-provider formal acceptance (INV-1009), the WP12b held-out semantic gate, load/backup drills (WP14–WP16 items below).

**Remaining open work.** WP10b (real-provider acceptance evidence), WP14 (backup/WAL registration INV-1405, monitoring), WP15 (remaining release gates), WP16 (recruitment execution beyond the N=5 listing — exclusion lists, expansion criteria under D12), and the WP12b held-out corpus decision (D09 residual). G1–G10 go/no-go checks re-verified per WP15 before any expansion beyond the N=5 pilot.

**Enrollment state.** `prolific.live.n5` (v9-pinned) stays permanently closed. **`prolific.live.n5.v10` enrollment opened 2026-10-07** after the remediated build passed gate 13/13 on `71a98e68…547e` and redeployed; entry→consent→allocation was smoke-verified on the deployed image. Synthetic smoke sessions in both batches (abandoned; entitlements disposed/self-correcting to `ineligible`) are identifiable by `TESTSMOKE` panel IDs — expected, non-analytic.

## 4. Proposed implementation structure

Paths below are proposed artifacts, not claims that files already exist (except `apache/`, `.env.example` and `.env`).

```text
investigators/
  apache/
    investigators.vhost.snippet   source of truth for the /investigators/ ingress block (exists; declared)
  backend/
    app/
      api/                  participant, telemetry, protected admin endpoints
      domain/               world, records, claims, state, scoring
      services/             enrollment, commands, projections, evidence, completion
      timing/               active clock, policy sampling, eligibility, ordering, warnings
      workers/              durable jobs, generation, outbox notifications
      providers/            approved local adapter and deterministic doubles
      measurement/          exact metrics, exports, offline coding/gates
      db/                   models, repositories, transactions
    migrations/             Investigators-owned Alembic history
    tests/                  unit and PostgreSQL integration tests
  frontend/
    src/                    shared workbench, stage screens, typed API/state
    tests/                  browser-facing component/interaction checks
  content/
    schemas/                manifests, world/claim/instrument contracts
    profiles/               reference and proposed pilot profiles
    scripts/                requests, voices, acknowledgments, warnings, fallbacks
    instruments/            consent, training, measures, debrief versions
  tests/
    fixtures/               worlds, reference traces, adversarial/coding cases
    e2e/                    full Chromium/Firefox paths
    load/                   full-duration concurrent sessions and fault cases
  scripts/                  preview, verification, exports, safe operational tools
  docs/                     decisions, trace, codebook, runbook, release evidence
  docker-compose.yml        isolated Investigators services (UI 127.0.0.1:8518, API 127.0.0.1:8519)
  .env.example              settings template (exists)
  .env                      owner-held secrets and settings (exists; never copied into source, printed or logged)
```

Use the specified FastAPI/SQLAlchemy/Alembic/PostgreSQL backend and React/TypeScript/Vite frontend. The API sets `root_path=/investigators/api`; the Vite build uses `base=/investigators/`. Choose and lock compatible dependency versions during scaffolding; this plan does not prescribe unverified current library versions. API, worker, and UI build from declared source roots. Source permissions and transaction boundaries belong in backend services, never browser-only controls.

## 5. Work packages and comprehensive TODO register

### WP00 — Baseline inventory and execution records

**Owner:** Engineering lead with research lead. **Dependencies:** None. **Design:** §§1–3, 13–14, Appendix B. **Artifacts:** status/decision registers, baseline checksums, reference crosswalk, requirement trace.

- [x] **INV-0001** Create the task-status ledger with ID, owner, state, dependency, artifact/evidence link, blocker and next action; copy no task as already passed.
- [x] **INV-0002** Archive checksums of the design, both supporting reports, both demos, the pilot report, the protocol DOCX **and PDF**, and the vhost snippet; record the design version each implementation release targets.
- [x] **INV-0003** Extract demo content, event schedules (including the “One minute left.” message at `timeLeft === 60` and the ≤60 s low-timer style), world/retrieval behavior, per-class timing mechanisms, `INV_FINISH` payloads, item arrays, seat-asymmetric behaviors (e.g., S3's 0.65/0.35 PM/JT acknowledgment-seat choice) and known defects into reference fixtures/crosswalks without altering the demos.
- [ ] **INV-0004** Open D01–D14 with concrete candidate values from the specification, responsible roles, and launch/use dependencies; file the §2.3 external requests. *Progress:* the decision register lives at §3 + `docs/DECISIONS.md` (D01–D14 with resolutions/owner); external asks are filed under `docs/requests/` (e.g., `D03_pe_evaluation_expectation.md`). *Open:* D03's residual and D08, D09, D11, D12, D14 remain unresolved — `docs/DECISIONS.md` explicitly keeps this task in progress until they reach their gates.
- [x] **INV-0005** Inventory existing ingress/service/database/model boundaries using the canonical `./estate-docs/SYSTEM_MAP.md`, the live `/etc/httpd/conf.d/investigators.conf` (the `fow_ssl` copies are regenerated snapshots) and current read-only checks before any boundary-changing work. Record the 2026-09-28 inventory: 8518/8519 unused and allocated; 8514/8517 listening without vhost entries; `/team/` belongs to neighbor-study.local (S1).
- [x] **INV-0006** Define release evidence naming, immutable source/build snapshots, review records and how design changes trigger affected task/gate reopening.
- [x] **INV-0007** Maintain `docs/REQUIREMENT_TRACE.md` (seeded from §9): every spec section and every “must/required/never” clause, plus every adopted HB-xx/Timing Fx/Px recommendation, maps to ≥1 owning task and ≥1 verification. Update on every spec or plan revision; an unmapped clause is a plan defect.

**Exit:** Another implementer can identify the source baseline, selected versus proposed profiles, unresolved decisions, the requirement trace, and the next unblocked tasks.

### WP01 — Application scaffold and isolated development harness

**Owner:** Backend/frontend engineering. **Dependencies:** WP00. **Design:** §§3, 8, 11.

- [x] **INV-0101** Create the declared module layout, one backend application factory, one frontend application, and separate worker entry point. No imports from other live apps' private modules. Reusing the estate's shared `study_admin_common` (as PCS/DCT do) is allowed only as a read-only mount with its version recorded and contract tests (spec §3.2).
- [x] **INV-0102** Select and lock dependencies, build/lint/type-check commands and deterministic test commands; document the exact runtime/tool versions.
- [x] **INV-0103** Add typed settings loaded from `./.env`, following `.env.example` (`ADMIN_PASSWORD`, `ADMIN_EMAILS`, `DATABASE_URL`, `INVESTIGATORS_TEST_DATABASE_URL`, `INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS`, `APP_MODE`), matching the `pcs`/`dct` settings pattern. Requirements: safe startup failure for missing required configuration; an empty `ADMIN_PASSWORD` disables admin rather than blocking startup; no secret logging; explicit preview/QA/live mode; test-only virtual clock access.
- [x] **INV-0104** Create isolated disposable PostgreSQL and provider-double test harnesses. Require `INVESTIGATORS_TEST_DATABASE_URL` plus explicit destructive opt-in and prove it differs from every live DSN before reset.
- [x] **INV-0105** Add health/readiness and build/schema/version stamps, structured redacted logs, request IDs and basic failure metrics.
- [x] **INV-0106** Create UI/API/worker container build definitions and `investigators/docker-compose.yml`, modelled on `dct/docker-compose.yml`: `env_file: .env`, `extra_hosts: host.docker.internal:host-gateway`, a healthcheck, and `restart: unless-stopped`. Use distinct `investigators-*` container names, bind only `127.0.0.1:8518` (UI) and `127.0.0.1:8519` (API), and re-verify both ports are free before first start. Never bind 8111/8112 or change ingress from Compose.
- [x] **INV-0107** Establish automated checks for builds, typing, meaningful unit/integration tests, source/configuration validation and accidental participant-bundle leakage, including absence of the demo relay host (`qualtrics-relay.example.com`), truth fields, condition selectors and agent implementation labels.

**Exit:** A clean checkout/source snapshot builds deterministically, boots against disposable resources, and runs tests without real providers or production databases.

### WP02 — Manifests, profiles, content and executable policies

**Owner:** Backend engineering with research review. **Dependencies:** WP00–WP01. **Design:** §§2, 4, 6.5–6.6, 13.

- [x] **INV-0201** Define strict versioned manifest/schema contracts, canonical default materialization, content hashes and immutable profile publication.
- [x] **INV-0202** Implement `tp_demo_v1`, `pe_demo_v1`, `tp_review_first_v2`, and `pe_pilot_v2` as separate manifests over one engine; include both arms in each profile, yielding **eight** profile/arm preview combinations (spec §4.1).
- [x] **INV-0203** Encode the baseline 40/150/240 active-second requests and proposed review-first 80/180/270 schedule separately; record that the latter remains a proposal until D01. Reference `warning.one_minute_left` v1 from every profile (INV-0213).
- [x] **INV-0204** Version all manipulation surfaces, seat-value mappings, warnings, instruments, script banks, fallback copy, payment and debrief policies, keeping the four teammate-policy layers separate: factual service, interaction schedule, conversational realization, and manipulation surfaces (spec §4.2).
- [x] **INV-0205** Implement one policy resolver for source/class/seat timing, length, cues, availability, caps, failure handling and permissions; ensure runtime components consume the resolved policy.
- [x] **INV-0206** Reject unsupported schema fields, profile combinations, distribution/trajectory modes and dormant timing treatments. Do not expose a switch that merely stores a value without behavior.
- [x] **INV-0207** Separate random streams for assignment, world, order, ratings, schedules and per-job draws; persist realized artifacts/draws in addition to seeds. **Verify:** runs with identical seeds that differ only in participant chat volume or arm produce identical world, schedule, rating-seat and other-seat draws.
- [x] **INV-0208** Implement manifest/build/model/prompt/validator/metric compatibility keys and paired-arm diffs that permit only intended differences.
- [x] **INV-0209** Implement signed preview descriptors and faithful versus explicitly accelerated QA profiles; prevent live URL overrides and accelerated analytic timing.
- [x] **INV-0210** Build `docs/TIMING_CONFORMANCE_MATRIX.md` over the 26 spec §6.6 dimensions: baseline value per profile, manifest field or “unsupported → rejected at bind”, runtime enforcement point, and test ID. Each row with an owner decision (AI referral, presence cues → D07; warnings, distributions → D10) cites it.
- [x] **INV-0211** Implement per-class `timing.mechanism` (spec §6.5): reference profiles run `demo_structured_delay_length`, `demo_social_additive`, `demo_assistant_floor` with clamped draws; v2 profiles run `concurrent`; demo mechanisms are rejected in non-reference profiles. Publish the reference-to-v2 timing diff and check it through INV-0208.
- [x] **INV-0212** Implement a synthetic-run registry keyed by full compatibility key + seed + replicate, with separate retry-attempt lineage and exact unfinished-case resume. Test that a failed low-numbered replicate never causes a completed replicate to rerun (hidden-bench `_count_done` lesson). Used by INV-0509, INV-0706, INV-1008 and INV-1507.
- [x] **INV-0213** **Encode the one-minute warning.** Define `warning.one_minute_left` v1 as content (`"One minute left."`, system sender, no teammate voice) plus a timing policy: an independent `active_task`-clock job at **active second 300** that commits one system message per session, and a `low_time` countdown state from active 300 through 360. Requirements: identical within a study across arms; unaffected by the TP check-in pause (so it fires at active 300 in every profile, i.e., interaction second 240 in `tp_review_first_v2`); never waits on chat/model work; one logical delivery under retries/restarts; suppressed and logged if the task has already closed; recorded as a `manipulation_exposure`-style exposure with commit, receive and render receipts. **Verify:** fake-clock traces at 299.999/300 active; TP session with a long check-in pause still warns at active 300; restart at 299 s yields exactly one warning; replay after reconnect shows it once; reference golden traces match both demos' timing and wording.

**Exit:** Every supported preview binds to a complete executable configuration; unresolved launch choices cannot silently become live defaults; the timing conformance matrix has no unowned row. Contributes G1.

### WP03 — Persistence, transactions and durable event contracts

**Owner:** Backend/data engineering. **Dependencies:** WP01 and WP02 schema interfaces. **Design:** §§6.2, 8, 11.

- [x] **INV-0301** Create `investigators_db` on the host PostgreSQL 15 cluster with isolated roles for migrations, runtime, research/admin and compensation linkage. The operator runs the prepared SQL script (§2.3). Keep one Investigators migration history, and give the separate disposable test database a DSN that differs from `DATABASE_URL`.
- [x] **INV-0302** Implement manifest/batch/session/identity/allocation/consent/world/record/holding/file-membership aggregates with ownership foreign keys and uniqueness constraints.
- [x] **INV-0303** Implement command receipts, monotonic per-session events, stage/clock intervals, jobs, leases and transactional outbox; define lock ordering and bounded transaction retry. At each command, lock the session and advance any due deadline transitions before validating.
- [x] **INV-0304** Implement messages/requests/shares/episodes, knowledge snapshots/agent state, claims/source links, model attempts/outputs and recipient delivery receipts.
- [x] **INV-0305** Implement answer revisions/finals, confidence, manipulation and warning exposures, item responses, results (bonus stored as integer cents), entitlements, completion receipts, data-use choices, incident intervals and admin/redaction audit.
- [x] **INV-0306** Define coding-run/result/gate persistence interfaces now; implement their immutable constraints with WP12a without forcing an unvalidated semantic metric into runtime.
- [x] **INV-0307** Enforce command UUID idempotency, logical job/event uniqueness, state revision and row-lock transitions; commit event and delivery intent atomically.
- [x] **INV-0308** Test migrations from empty and previous schemas, role restrictions, cross-session foreign keys, duplicate commands, concurrent updates and unavailable persistence on PostgreSQL.
- [x] **INV-0309** Define retention-aware indices and consistent-snapshot exports; ensure replay reconstructs required state from committed records rather than transient queues.

**Exit:** Acknowledged writes survive application restart; duplicate logical actions remain singular; failed commits cannot be reported as saved. Contributes G4/G9.

### WP04 — Enrollment, identity, assignment and procedural lifecycle

**Owner:** Backend engineering with research/operations input. **Dependencies:** WP02–WP03. **Design:** §5, §§8.2–8.3, 13.

- [x] **INV-0401** Implement platform-neutral entry adapters and selected batch identifier validation; query-string panel IDs are linkage, never credentials.
- [x] **INV-0402** Issue opaque session credentials using Secure/HttpOnly cookies scoped to path `/investigators/`, hash server-side, and enforce CSRF/origin/session authorization, including on the event stream.
- [x] **INV-0403** Persist consent (text version, timestamp, affirmative action) before assignment; assign before the first condition-specific instruction; allocate 1:1 within study/batch under a DB lock, with immutable reservation and no reload/dropout reassignment.
- [x] **INV-0404** Implement repeat-participation eligibility and authorized identity linkage per D08, without promising unsupported cross-platform deduplication.
- [x] **INV-0405** Implement the full legal stage graph, procedural prerequisites, state revision, terminal reasons and debrief paths for decline/withdrawal/technical stop/abandonment.
- [x] **INV-0406** Isolate practice, signed previews and QA from live allocation, analytic task counts and payment entitlement.
- [x] **INV-0407** Implement authenticated resume, one writer lease with read-only second tab or explicit takeover, and controlled credential-loss recovery.
- [x] **INV-0408** Implement completion reconciliation and one durable outcome receipt; skip/missing answers cannot accidentally block base payment.
- [x] **INV-0409** Verify allocation under concurrent entry, refresh at each stage, invalid credentials, expired previews and all exceptional paths.

**Exit:** A participant has one stable assignment and recoverable lifecycle; platform identifiers alone cannot resume or read a session. Contributes G1/G6/G9.

### WP05 — World generator, chronology, projections and evidence ledger

**Owner:** Domain/backend engineering. **Dependencies:** WP02–WP03. **Design:** §§7.1–7.2, 7.5–7.6, 9.3.

- [x] **INV-0501** Port world generation into deterministic server functions: eight crates/six hideouts, four assignments, two own-file/two team-dependent, typed movement timelines and cutoffs.
- [x] **INV-0502** Define consistent JT/PM assignments/holdings and useful participant records; explicitly derive requested and never-requested subsets.
- [x] **INV-0503** Implement a deterministic chronology oracle distinguishing arrival/still-at/departure/transit and validating decisive-evidence reachability.
- [x] **INV-0504** Persist complete generated worlds, initial file order and record text/IDs/hashes; never require future generator code to recreate historical data.
- [x] **INV-0505** Implement nested recipient allowlists for participant, teammate, assistant, scoring and researcher views; exclude truth, private holdings, role/condition labels, future jobs and timing draws from participant payloads; participant endpoints accept no seat parameter. (The scoring/researcher projections are declared and server-side today; the scoring *service* and its frozen-answer read authority land in WP11 — see INV-1102.)
- [x] **INV-0506** Implement deterministic structured retrieval at accepted-request snapshot, authenticated record attachments, and idempotent Add-to-file with delivered-record authorization.
- [x] **INV-0507** Implement immutable context manifests: assistant/structured evidence fixed at accepted request; social context fixed at grant with visible sequence, parent events and permitted memory.
- [x] **INV-0508** Implement typed claims and source lineage plus branching evidence-transfer records; distinguish delivery/render/file inclusion/reference/revision and never-arrived states.
- [x] **INV-0509** Validate at least 1,000 seeded worlds and hand-audited initial/augmented/full-information cases; test cross-session, DOM/API/source-map leakage and guesses/pasted text that attempt to expand privileges.
- [x] **INV-0510** Verify spec §7.1 invariant 7: across ≥1,000 seeded TP worlds, report the joint distribution of high-rating seat × holder of each decisive team-dependent record × scripted-request sender, and require independence (or a documented balanced design). Feed the result to D02 and the admin balance report (INV-1302).

**Exit:** The intended information dependence is proven independently of model performance, every actor's knowledge can be reconstructed, and rating-seat assignment is not confounded with seat information. Contributes G2/G5.

### WP06 — Authoritative clock, scheduler and failure semantics

**Owner:** Backend/timing engineering. **Dependencies:** WP02–WP05 interfaces. **Design:** §§6, 8.3, 12.1.

- [x] **INV-0601** Implement the persisted active-time ledger and server-derived deadlines; store UTC chronology and process-duration measurements separately.
- [x] **INV-0602** Implement TP pause at active 60, collection/reveal/resume and selected inactivity/skip policy; PE stays continuous. Hidden tabs/disconnects do not pause.
- [x] **INV-0603** Implement declared active/wall clock bases for jobs, independent check-in/warning/deadline jobs, durable claim/lease recovery and one logical delivery under retries, **with ≥2 scheduler workers and ≥2 API processes** sharing one PostgreSQL.
- [x] **INV-0604** Implement accepted/granted/generating/validated-ready/eligible/committed lifecycle with explicit failed/cancelled/expired/suppressed states; generation overlaps the imposed wait (v2 `concurrent` mechanism).
- [x] **INV-0605** Persist all sampled components, body-length schedule finalization, readiness overrun, holds, actual commit/dispatch, indicator slip and browser receipt fields without backdating.
- [x] **INV-0606** Implement declared ordering policies independently of eligibility; future injections cannot move early and due transitions cannot wait on chat/model work.
- [x] **INV-0607** At delivery commit recheck stage, deadline, recipient, revision and duplicate status; atomically freeze answers at active 360 and suppress all late task output. Expose no early-submission endpoint in any profile unless D01 adds a versioned one.
- [x] **INV-0608** Implement episode resolution/cancellation and cap/cooldown enforcement with explicit rejection reasons, separating experimental request budgets from operational abuse limits; prevent silent queue-induced treatments.
- [x] **INV-0609** Implement supported sampling with mean/median and clamping/rejection semantics explicit; test accepted distributions and all supported phase/class modifiers, including censored tails. Label configured, analytically predicted, simulation-measured and runtime-observed values distinctly in exports.
- [x] **INV-0610** Verify 3-second generation/9-second wait, 12/9 overrun, 11-second late browser, TP 57→66 pause example **and its PE uninterrupted counterpart**, crossed ordering, same-time event-sequence cases, and typing-indicator start at grant + platform + think with indicator/first/final content asserted independently. *(Typing-indicator clause dormant: every profile binds `presence_cue: "none"` pending D07 — there is no indicator path to assert at runtime; first/final tape assertions are scoped to INV-1507.)*
- [x] **INV-0611** Verify 59.999/60, 299.999/300 (warning) and 359.999/360 boundaries, restart during pause/wait, lease expiry, simultaneous finalize, duplicate tabs, timeout/late second bubble and persistence outage.
- [ ] **INV-0612** Implement the D14 technical-incident policy: persist outage/incident intervals against the active-time ledger with affected surfaces; apply the selected clock/participant/payment treatment; never silently extend a participant's clock. Test API outage and scheduler outage mid-task and mid-check-in. *(Mechanism implemented and verified mid-task/mid-check-in; box stays open — acceptance requires the D14 clock/payment/analytic treatment selection, which is unresolved.)*

**Exit:** The timer, jobs, warning and accepted answers remain correct without an open browser or live model, across multiple workers; actual versus intended times are independently auditable. Contributes G4/G8.

### WP07 — Deterministic teammates and behavioral fidelity

**Owner:** Backend engineering with research content review. **Dependencies:** WP02, WP05–WP06. **Design:** §§2.2, 6.6, 7.3, 7.7.

- [x] **INV-0701** Implement scripted connecting/greetings, structured teammate requests, record responses and neutral deterministic social fallbacks as typed jobs.
- [x] **INV-0702** Implement profile-specific voice/thanks/crosstalk, stored probability draws and parent IDs; preserve PE's documented 28-second crosstalk relationship in its reference profile.
- [x] **INV-0703** Enforce no off-policy task-solving, new facts, urgency, evaluations, improvised delay excuses or unsolicited requests; log any permitted solicitation with crate/topic parentage.
- [x] **INV-0704** Maintain bounded auditable commitments and unresolved requests; prevent false memory and duplicate promises/replies after recovery.
- [x] **INV-0705** Audit and version neutral next-pilot wording separately from historical distracted/busy language; no hidden behavioral speed-up for high pressure.
- [x] **INV-0706** Run paired-arm participant scripts (registered via INV-0212) for factual access, responsiveness, initiative, style, cognitive scope, memory and opportunity windows; include contingent-responsiveness cases (share-then-request vs request-only latency) and two-stage cases (acknowledgment neither resets the substantive schedule nor counts as fulfillment); separate surface stochasticity from policy differences.
- [x] **INV-0707** Reconcile golden reference traces (including the one-minute warning) and explicitly approved v2 differences; preserve the two studies' distinct scripts without creating separate engines.
- [x] **INV-0708** Verify per-seat acknowledgment, crosstalk, presence-cue and latency policies are identical, or explicitly balanced, with respect to rating-seat assignment in TP pilot profiles (spec §7.1 invariant 7); document reference-profile seat asymmetries as historical differences.

**Exit:** A deterministic teammate-only session is complete and scientifically inspectable before generative wording is added. Contributes G3.

### WP08 — Shared participant workbench and browser synchronization

**Owner:** Frontend engineering. **Dependencies:** WP01–WP02 contracts; integrated acceptance requires WP04–WP07. **Design:** §§8.2–8.3, 10.

- [x] **INV-0801** Build typed API/state clients for entry/session/commands/events/telemetry/completion with command UUIDs, expected revisions, exact enum choices, and explicit handling of 400/422, 401/403, 409, 429 and 503 outcomes.
- [x] **INV-0802** Build simultaneous case file, team chat, assistant and four answer controls (crates in ascending order; neutral record styling, no role colors); keep source routes equally discoverable and avoid exclusive tabs. *2026-10-01 UX round:* layout is now the piloted two-row grid (case file 45% left spanning both rows; chat upper-right; assistant lower-right; answer bar across the bottom) with per-pane tinted channel styling.
- [x] **INV-0803** Implement record sorting/reordering, authenticated attachment display, Share/Request/Add actions and keyboard/click alternatives to drag/drop. *2026-10-01 UX round:* sort selector and up/down reorder buttons removed; drag reorder retained with a four-arrow grip.
- [x] **INV-0804** Display server-interpolated/resynchronized time, stage locks, the server-committed “One minute left.” system message and `low_time` countdown style from INV-0213; client timers never own state transitions, warnings or accepted answers.
- [x] **INV-0805** Implement cursor-based SSE with polling fallback, replay/cursor-gap resync and monotonic revisions; each subscriber replays its own cursor; no model call or release triggered by a state GET.
- [x] **INV-0806** Preserve dirty drafts until acknowledgment/rejection; handle stale replies, saving/disconnected states, second-tab takeover and late rejected answers without false Saved claims.
- [x] **INV-0807** Emit bounded receive/render/first-final-content/visibility/focus/item telemetry; distinguish replay and clock uncertainty without recording full keystroke streams.
- [x] **INV-0808** Preserve reading position, focus and file/filter state; manage dialogs and one-time stage scroll reset without repeated scroll interference.
- [x] **INV-0809** Implement authorized projection only; participant builds contain no live condition selector, restart/debug truth, agent implementation labels or relay access.
- [x] **INV-0810** Verify desktop Chromium/Firefox at 1366×768, 1920×1080 and 200% zoom, keyboard paths, hidden tab, slow network, out-of-order snapshots and lost receipt cases; document any unsupported viewport before enrollment.

**Exit:** Real browsers complete a durable task with deterministic providers and recover cleanly without losing drafts or exposing hidden state. Contributes G6.

### WP09 — Study modules, instruments and completion experience

**Owner:** Frontend/backend engineering with research lead. **Dependencies:** WP04, WP06–WP08; D01–D04/D06–D07 before final materials. **Design:** §§1.2, 4.1, 5.2, 9.1, 10, 13.

- [x] **INV-0901** Implement consent/instructions/training/scripted connecting/ready with versioned material and item events; training uses a separate fixed practice world and practice event namespace, demonstrates every analytic interaction with equally discoverable AI and teammate routes, and never counts as analytic activity. *2026-10-01 UX round:* consent renders the full protocol XXXX-XXXX copy; training rebuilt as a full-width workbench mirror (case file left, chat upper-right, assistant lower-right, answers along the bottom) with a teammate-shared record so add-to-file is exercised.
- [x] **INV-0902** Implement TP own-rating/skip, 3.5-second collection presentation, exact teammate values/mapping, reveal receipts, explicit resume and persistent ratings strip.
- [x] **INV-0903** Implement review-first channel locks and answer drafting, then remaining 300 active interaction seconds; test its distinct scripted schedule in both arms.
- [x] **INV-0904** Implement PE intro/reminder/strip/system-message exposures versus neutral control copy; no TP pause or actual teammate evaluation generation.
- [x] **INV-0905** Persist final-answer freeze, confidence with explicit response/missing states, and JT/PM ratings in both PE arms; default slider positions are not answers. *2026-10-01 UX round:* confidence renders as a card grid (one per answered crate); all instruments submit page-level — no per-item Submit/Skip, Continue gated on all items answered.
- [ ] **INV-0906** Persist all post-task items with selected ordering, anchors, skip states and per-item timing; suspicion precedes debrief and score/key feedback follows post-task measures per D06. *Progress 2026-09-29:* post-task persistence, ordering, anchors, skip states, per-item telemetry and suspicion-before-debrief all verified (E9). **Open:** the score/key feedback surface does not exist yet — it is D06-gated (frozen-copy decision); only the negative ordering constraint (no feedback before post-task measures) is currently enforced and tested.
- [x] **INV-0907** Implement non-auto-dismissing debrief, automated-teammate explanation, evaluation clarification, data-use choice and recoverable completion handoff. *2026-10-01 UX round:* the participant-facing data-use screen now shows contact information only and records the `use_all` default; the `debrief → data_use_choice → completion` invariant is preserved; withdrawal-route question folded into D09.
- [x] **INV-0908** Implement technical-stop, abandonment-return and withdrawal paths with preserved records and selected payment handling; never fabricate manipulation exposure.
- [ ] **INV-0909** Capture material/exposure snapshots for all **eight** profile/arm combinations and perform researcher walkthrough against the selected approved instrument.
- [x] **INV-0910** PE control-arm contamination audit: inventory every participant-visible surface per arm (platform listing, study title/description, consent, instructions, training, messages/emails, completion page) for evaluation-related wording; record findings and fixes in D03 before paid PE recruitment.
- [x] **INV-0911** Execute the **pre-freeze** portion of D13 on the lab prototype: record the D13 selection (non-analytic staff/lab usability round under applicable approval, or the pilot-serves-role option), and if a usability round is selected run it against the first-compose-up stack covering timing plausibility, world difficulty, discoverability and warning noticeability, with results recorded against D10 **before the D10 freeze**. A provisional D10 may be recorded for non-fielded lab previews only; the freeze binds real timing/load acceptance (WP10b). *(Moved out of WP16 by BA-22: INV-1609 retains only the pre-expansion verification that the D13 record stands or that pilot usability outputs are reviewed.)* *Done:* the D13 election was recorded 2026-10-07 — the initial pilot serves the usability role, so no separate lab round was required; pilot usability outputs were reviewed in the v10 post-launch (§22).

**Exit:** All eight profile/arm previews work; only explicitly selected, approved pilot profiles can be marked launch-ready. Contributes G1/G6/G10.

### WP10a — Grounded local model adapter and output validation (provider doubles)

**Owner:** Inference/backend engineering with research review. **Dependencies:** WP05–WP07. Runs in parallel with WP08/WP09. **Design:** §§7.4–7.7, 12.

- [x] **INV-1001** Implement one approved local-provider adapter interface with bounded concurrency, per-call model/digest/parameters, redacted diagnostics, no browser relay or external fallback, and no model change within a session (tested).
- [x] **INV-1002** Submit immutable permitted snapshots and bounded prompts; enforce accepted-request evidence cutoff for assistant/structured service and grant cutoff for social generation.
- [x] **INV-1003** Implement typed claims and safe rendering; reject wrong crate/time, negation inversion, transit-as-arrival, unsupported certainty, hidden records and fabricated citations; render “latest location in your file” rather than asserting true current location.
- [x] **INV-1004** Treat user/record text as data; test instruction injection, paraphrase/synonym leakage and attempts to reveal prompts, private holdings or condition keys.
- [x] **INV-1005** Implement empty/error/malformed/timeout/validation outcomes and deterministic source-bound fallback, with separate attempted and displayed records. Persist parse, repair, rejection and fallback as distinct outcomes; never salvage a choice by string containment (fixture: “Not The Marina” is not a Marina selection).
- [x] **INV-1006** Restrict retries to the original job/snapshot/deadline; quarantine late outputs and never add a second bubble or extend selected participants' task clocks.
- [x] **INV-1007** Exercise greetings, seeking, shares, repetition, contradiction, silence, insults and generation-time context races using doubles and adversarial fixtures.

**Exit:** Source permissions and claims are enforced online against provider doubles. Contributes G5.

### WP10b — Integrated generation review and real-provider acceptance

**Owner:** Inference/backend engineering with research review. **Dependencies:** WP10a, WP08–WP09 (incl. INV-0911 pre-freeze usability); D05/D07/D10. **Design:** §§7.4–7.7, 12.

- [ ] **INV-1008** Run multiple paired-arm generated realizations (registered via INV-0212) and held-out human output review; require zero known hidden-record leaks or unsupported/wrong-crate factual displays in acceptance fixtures. *Progress 2026-10-12:* real-provider evidence now exists from `study.live.v10` — 11/12 teammate replies model-generated and validated `supported`, one sanctioned `deterministic_script` fallback, zero leaks/incidents in the post-launch audit (§22). The registered multi-realization set and held-out human review remain open.
- [ ] **INV-1009** After endpoint/model/capacity resolution, run a bounded real-local-provider acceptance set under the INV-1507 concurrent load, record actual latency and fallbacks, and verify configured budgets without displacing another study's model. **Failure criterion:** provider p95 beyond the configured fallback deadline is a failure, not a reason to extend participants' clocks. *Progress 2026-10-01:* real-provider calls are live in the deployment (`gemma4:26b` via `local_openai_chat` since 2026-10-03 — `gemma4:12b` before that; `reasoning_effort: "none"` injected into every request and recorded in `model_call.parameters`; `temperature: 0.2`). Driven live sessions show model-generated teammate replies (~1 s warm, validated+delivered) and gated assistant answers with deterministic fallback on rejection. Cold load (≈40 s for 26b) exceeds call timeouts → the `investigators-ollama-warmup` compose service warms the model at stack start and pins it ~1 y via `--keepalive` (supersedes the manual `docs/LAB_SESSION_RUNBOOK.md` §2 warm-up and the 24 h keep-alive). Formal INV-1507-load acceptance and p95 evidence remain open.

**Exit:** Actual provider conformance is reported separately from fake-model success. Contributes G5/G8.

### WP11 — Exact measures, exports and historical compatibility

**Owner:** Data/analysis engineering with research methods lead. **Dependencies:** WP03, WP05–WP09; full audits incorporate WP10a/WP10b. **Design:** §§9.1–9.3, 9.5.

- [x] **INV-1101** Publish a versioned codebook for seeking, AI use (with `typed`/picker route), sharing, strict/legacy proactive sharing, accuracy, confidence, timing, warning exposure, manipulation checks and all missing/undefined states.
- [x] **INV-1102** Implement exact-event metrics from committed records; retries do not inflate counts, missing answers remain explicit, and zero-denominator seeking allocation is undefined. The scoring service reads frozen answers and has no authority to revise them.
- [x] **INV-1103** Implement evidence-delivery/fulfillment/redundancy and correction/deterioration measures with crate/request/assertion denominators, prior-answer eligibility and censoring.
- [x] **INV-1104** Implement request episodes, cross-source pending overlap, resolution and opportunity intervals; sum unions rather than double-count overlapping waits.
- [x] **INV-1105** Export pre-check-in/paused/post-resume periods, actual manipulation exposure and channel-open time; do not treat own pressure as post-exposure or closed review time as reluctance.
- [x] **INV-1106** Generate consistent-snapshot participant-wide, crate-long, event/message/model/context/claim/evidence/episode/revision/exposure/incident tables plus a fidelity-rate table (empty/fallback/late/suppressed/failed/off-schedule by arm, source, model, policy) with schema/metric versions, filters, counts, checksums and configured/predicted/simulated/observed labels.
- [x] **INV-1107** Reconcile original `INV_FINISH` fixture fields; separate actual first request from the legacy first-action value mislabeled `inv_first_request_sec`.
- [x] **INV-1108** Implement compatibility-filtered aggregates and descriptive technical-incident summaries; no silent pooling across profiles, versions or post-treatment exclusions.
- [x] **INV-1109** Provide restricted payment exports, redacted sharing bundles and formula-safe CSV; omit initials from shared exports; verify identity, truth and free-text review boundaries.
- [x] **INV-1110** Implement a separate historical-import contract if raw Qualtrics data are supplied; preserve original fields, checksums and unavailable metadata rather than inventing events/seeds. Mark import execution not applicable until inputs exist.
- [x] **INV-1111** Recompute every wide measure from long records on hand-audited synthetic sessions, including never-arrived evidence, missing render receipts, repeated records and unfulfilled late requests.

**Exit:** Results and denominators can be independently reconstructed without a model judge. Contributes G7.

### WP12a — Offline semantic coding pipeline and development set (pre-pilot)

**Owner:** Research methods/annotation lead with data engineering. **Dependencies:** WP03 coding schema and WP11 export contracts; rubric preparation can start after WP00. **Design:** §§9.4–9.5, 12.2.

- [x] **INV-1201** Define crate-specific categories/examples for explicit statements, questions, inference, negation, ambiguity, solicitation, repetition and stance; retain exact entity/time/evidence spans.
- [x] **INV-1202** Implement message-level coding followed by deterministic history/recipient-novelty derivation; authenticated attachments bypass semantic matching.
- [x] **INV-1203** Prepare a blinded **development** corpus from synthetic and staff-authored cases covering both studies/arms, generated/fallback text and adversarial cases; document unavoidable unmasking; report its metrics separately from real-corpus results.
- [x] **INV-1205** Implement immutable coding runs keyed by source/corpus/rubric/prompt/model/parameters/code; results distinguish coded, not-coded, failed and abstained, with supersession rather than overwrite. Any model-assisted coding runs only on the D05-approved local provider; external judges are prohibited unless separately approved.
- [x] **INV-1208** Block mixed-run or incomplete-coverage semantic analysis by default; keep exact-record exports usable and label unavailable semantic outcomes honestly.
- [x] **INV-1209** Validate the pipeline against wrong-crate, negated-arrival, stale record, quoted disagreement, repeated lineage and late-generated/early-context examples.

**Exit:** Pipeline implementation is complete and exercised on the development set; semantic outcomes remain explicitly unavailable. No other project's gate values or judge outputs satisfy the gate.

### WP12b — Real held-out validation and semantic gate (post-pilot)

**Owner:** Research methods/annotation lead. **Dependencies:** WP12a, WP16 initial-pilot data, D09 data-use terms, D11 thresholds. **Design:** §§9.4, 12.2.

- [ ] **INV-1210** Assemble the real held-out gold corpus from initial-pilot text (withdrawn participants excluded; approved data-use terms applied), meeting the D11 minimum per construct and arm; freeze before any tuning.
- [ ] **INV-1204** Have at least two humans independently code a meaningful subset before seeing model predictions; retain agreement and adjudication by category.
- [ ] **INV-1206** Compare exact/heuristic/semantic outputs on the same real held-out cases; report precision/recall, errors, denominators and condition-specific coverage; keep synthetic/development metrics separate.
- [ ] **INV-1207** Implement server-computed immutable gate decisions with frozen D11 thresholds and reviewer identity; missing evidence fails closed and overrides cannot mint scientific PASS. Verify that documented gate/metric definitions match the executing code (no methods-vs-code drift).

**Exit:** Semantic analytic use has actual human gold and passed validation evidence, or is recorded as unavailable.

### WP13 — Research administration, privacy and participant disposition

**Owner:** Admin/backend engineering with research/operations lead. **Dependencies:** WP04, WP09, WP11; WP12a for coding views. **Design:** §11, §§8.3, 9.5.

- [x] **INV-1301** Protect admin UI/API (`/investigators/admin`, `/investigators/api/admin`) **in the application**. The selected ingress layout (Shibboleth admin blocks, then a public block for the whole path) means Apache does not enforce SSO there, and it strips identity headers. Requirements:
  - Password login against `ADMIN_PASSWORD` from `./.env`, the same pattern as `pcs`/`dct`. An empty value disables admin (fail closed).
  - Constant-time comparison (`hmac.compare_digest`), not `!=`.
  - On success, a signed, expiring admin session cookie scoped to `/investigators/`, with CSRF/origin checks and login rate limiting.
  - The password is never sent to logs or source.
  - Never trust `X-EPPN`, `X-Remote-User`, `Eppn` or `X-Shib-Authenticated`.
  - Record the confirmed allowlist (`admin1@university.edu`, `admin2@university.edu`) for audit.
  - Separate researcher, operator and compensation roles.
  - **Verify through the live proxy:** unauthenticated and forged-header admin UI and API requests are rejected.
- [x] **INV-1302** Implement signed previews for all eight profile/arm combinations, batch validation/enrollment controls, assignment/exposure/completion balance, world-difficulty and rating-seat balance by arm (INV-0510), latency/fallback by arm and batch, and session health views. *2026-10-01 additions:* self-service "Test links" admin tab — issuer-scoped preview-link listing (`GET /admin/previews/mine`) joining `admin_audit` `preview_issue` rows to session state, with honest statuses including "Cleaned up — test data removed" for tombstoned sessions (hidden by default behind a "show N cleaned-up links" checkbox, per 2026-10-01 feedback); issuance authorization expanded to researcher/operator roles; preview nonce + entry URL stored in the audited payload; compensation role excluded.
- [x] **INV-1303** Implement participant-view replay beside restricted truth/holdings/context/timing/claim evidence; never use admin truth projection for participant API responses.
- [x] **INV-1304** Display fallback/empty/suppressed/late rates, manipulation and warning receipt status, unresolved episodes, incident intervals and coding coverage by selected compatibility key.
- [x] **INV-1305** Implement audited technical stops/support actions without silent edits to assignment, truth or frozen answers; pin active versions during releases.
- [x] **INV-1306** Implement separate identity retention, entitlement reconciliation and approved deletion schedule, including the specified platform-ID handling after study closing.
- [x] **INV-1307** Implement withdrawal/data-use disposition and deletion tombstones that reapply after restore; keep compensation eligibility independent of research data-use choice.
- [x] **INV-1308** Implement reviewed redaction/export queues and safe cleanup with dry-run, preview/QA scope defaults, audited live scope and export backups before destructive operations. *2026-10-01 remediation (BA-34):* the mutable sweep now runs each per-table delete in its own savepoint — a mutable parent blocked by an append-only child (`model_call`/`message` ← `displayed_output` FK) is reported `blocked` in the tombstone work order instead of aborting cleanup with a 500. Tombstoned sessions are hidden from `list_sessions` and research exports (deleted-by-contract INV-1307); blocked parents and immutable rows await the migrated privileged purge mechanism. Integration regression test: `test_cleanup_survives_displayed_output_fk_block`. *2026-10-12 update:* per owner direction the **Redaction admin tab is removed** — `item_response` open text and the synthesized `chat_text` table now export verbatim for `use_all` sessions with no review step; the backend redaction schema/endpoints remain for historical/backend compatibility but are not a researcher workflow (§22).
- [x] **INV-1309** Test cross-session/admin/compensation denials, token/log redaction, text sanitization, CSRF/origin checks, rate/text limits and false-completion prevention during DB outage.

**Exit:** Researchers can audit and operate the study without exposing identity or private evidence to participants or unauthorized staff. Contributes G9/G10.

### WP14 — Deployment, recovery and operating runbook

**Owner:** Operations engineering (privileged steps executed by the operator). **Dependencies:** WP01, WP03, WP13 interfaces; review D05/D08/D09 before activation. **Design:** §§3.1, 11–11.1.

- [x] **INV-1401** Verify ingress (apply step done). *Done 2026-10-07+:* the running service has been exercised through the proxy for every deployed build (`/ready` 200, `/version` pinned stamps, live entries at `/investigators/` on `investigators.local`); unauthenticated probes return the documented responses. *Progress 2026-09-28:* the owner applied the revised block to `/etc/httpd/conf.d/investigators.conf`; `apachectl configtest` reported `Syntax OK`, followed by a graceful reload. Unauthenticated probes through the proxy returned 301 for `/investigators` (→ `/investigators/`) and 503 with no SSO redirect for `/investigators/`, `/investigators/admin`, `/investigators/api/admin/` and `/investigators/api/health`, as expected while no service runs. The remaining checks below need the running service. The `/investigators/` block was declared on 2026-09-28 inside the `investigators.local` virtual host of `./estate/vhost_review_snapshot.conf`, between `# BEGIN/END INVESTIGATORS MANAGED ROUTES`, source of truth `investigators/apache/investigators.vhost.snippet`: UI → 127.0.0.1:8518, API → 127.0.0.1:8519. Layout per owner decision: Shibboleth `<Location>` blocks for the admin paths, then a public `<Location "/investigators/">` for the whole path, as in `/pcs/` and `/dct/`. SSE is unbuffered and identity headers are stripped across the path. No one-off `.incl` file. For any future change, the operator backs up `/etc/httpd/conf.d/investigators.conf`, copies the reviewed file, runs `apachectl configtest` and a graceful reload. Once the service runs, verify through the proxy:
  - `/investigators` → 301; public SPA/API reachable without SSO.
  - `/investigators/admin*` and `/investigators/api/admin*` reach the application **without** an Apache SSO redirect (expected under this layout), and the application rejects them without its own admin login (INV-1301).
  - neighbor-study `/team/` is unaffected.
  - The live `/etc` block still matches `apache/investigators.vhost.snippet`. The local `vhost_review_snapshot.conf` is regenerated from `/etc`, so edit the snippet first and then re-apply.
- [x] **INV-1402** Prepare production Compose, least-privilege services, source/image stamps, runtime settings and migration ownership; keep credentials out of source and logs. *Done:* `investigators/docker-compose.yml` fields api/ui/worker/ollama/warmup on loopback-only ports with migrator/runtime/read roles; `scripts/build_images.sh` stamps the source checksum and a live app refuses to boot unpinned (deployed builds through `9ae1c609`).
- [x] **INV-1403** Configure the API `root_path=/investigators/api` and frontend `base=/investigators/`; verify prefix stripped once, SPA refresh, slash redirects, cookie path, loopback-only binding, and that no forwarded identity header reaches the app. Mount or load `./.env` (estate `.env` posture: 640, group `docker-users`, `u:admin1:r` ACL), and confirm `ADMIN_PASSWORD` is set before any admin use. Never reuse neighbor-study's ports, allowlist or secret include.
- [ ] **INV-1404** Configure model network restrictions, health/readiness, worker lag, outbox lag, DB/storage health and transport heartbeat observability. *Progress 2026-10-01:* the Ollama sidecar owns the stable network namespace on the compose network and the worker joins it (`network_mode: service:…`), so the literal-loopback INV-1001 guarantee survives worker rebuilds — the earlier direction (sidecar joining the worker) orphaned the sidecar whenever the worker was recreated. The `host.docker.internal` host-gateway mapping moved to the namespace-owning service (`extra_hosts` cannot be set on a netns-joining container). `/ready` and `/health` verified after rebuilds; model warm-up procedure documented in `docs/LAB_SESSION_RUNBOOK.md` §2. Worker-lag/outbox-lag dashboards remain open; the admin **monitor** tab + readiness flags provide interim observability.
- [ ] **INV-1405** Register `investigators_db` in the `fow_ssl` nightly backup, which currently covers `fow_app_db,human_agent_group_db` per `AGENTS.supplement.md`, together with checksums and age monitoring. Configure recovery capable of the proposed RPO ≤15 minutes/RTO ≤60 minutes (WAL archiving or equivalent) rather than assuming nightly dumps meet it. *Progress 2026-10-12:* ad-hoc full `pg_dump` backups are taken at each fielding milestone into `backups/<date>/` (latest: 3.3 MB post-v10-close); automated nightly registration and WAL archiving remain open.
- [ ] **INV-1406** Rehearse restore into scratch infrastructure, verify acknowledged research records, and reapply withdrawal/deletion tombstones before access.
- [ ] **INV-1407** Document enrollment drain, pinned active sessions, compatible migrations, deploy/rollback and outage recovery; test rollback without losing or restarting participant sessions. *Progress:* deploy/rollback is exercised in practice — `build_images.sh` + targeted container recreate keeps running sessions on pinned manifests (api/worker/ui redeployed 2026-10-12 for dashboard/export changes with zero session impact); `PRE-/POST-LAUNCH_CHECKLIST.md` and `PROLIFIC_CONNECT.md` document drain and completion-code routing. A dedicated rollback-rehearsal record and consolidated runbook remain open.
- [ ] **INV-1408** Configure enrollment pause on confirmed hidden-record/cross-session leak or data loss; expose proposed >5% fallback/100-call and >5% technical-stop/20-session warnings with actual denominators and no automatic analytic exclusions. *Progress 2026-10-12:* enrollment pause is implemented via the admin batches API and was exercised to close `study.live.v10`; the automated fallback/technical-stop threshold warnings remain open (readiness flags on the dashboard are the interim surface).
- [ ] **INV-1409** Prepare a build-specific operator runbook for startup/shutdown, readiness, bounded QA cleanup, support, exports, payment reconciliation and incident handling; do not print secrets or embed destructive default commands. *Progress:* `PRE-LAUNCH_CHECKLIST.md`, `POST-LAUNCH_CHECKLIST.md`, `PROLIFIC_CONNECT.md`, `INVESTIGATORS_TEAM_EXPERIMENT_BACKUP_PLAYBOOK.md` and `docs/LAB_SESSION_RUNBOOK.md` cover these functions in pieces; a single consolidated build-specific runbook is not assembled.
- [ ] **INV-1410** Once real boundaries are implemented, update the canonical `./estate-docs/SYSTEM_MAP.md` (not the regenerated `fow_ssl` copy) and `AGENTS.supplement.md` with verified ownership, `/investigators/` routes, ports 8518/8519 and backup facts; do not edit generator-owned `AGENTS.md`.

**Exit:** Deployment and recovery are concrete and rehearsed; actual activation follows the applicable authorized release step. Contributes G8/G9.

### WP15 — Integrated verification and release candidate

**Owner:** QA/release engineering with research reviewer. **Dependencies:** WP01–WP14 as applicable; no live provider test before D05. **Design:** §12, §§12.1–12.2.

- [ ] **INV-1501** Execute and evidence the requirement-to-test matrix maintained by INV-0007, covering every spec section, all eight profile/arm combinations and G1–G10; tag fake-clock, real-clock, provider-double, real-provider and browser evidence separately.
- [ ] **INV-1502** Execute G1–G3 configuration/world/behavior suites, including ≥1,000 worlds, rating-seat independence, paired arms and explicit reference-to-v2 differences (including timing mechanisms).
- [ ] **INV-1503** Execute all G4/§12.1 boundary, generation, ordering, restart, idempotency, multi-tab, **multi-worker/multi-subscriber**, warning and deadline traces on production-relevant PostgreSQL semantics.
- [ ] **INV-1504** Execute G5 access/adversarial fixtures and separate held-out human/local-provider review; reject hidden-record and unsupported/wrong-crate displays in the fixture release set.
- [ ] **INV-1505** Execute G6 full Chromium/Firefox paths for all eight profile/arm combinations, including refresh at each stage, optional responses, recovery, withdrawal and retrievable completion.
- [ ] **INV-1506** Execute G7 exact-event/export/legacy/privacy reconciliation; record WP12 semantic status (development-set only / unavailable until WP12b) with missing evidence visible.
- [ ] **INV-1507** Run ≥40 concurrent synthetic sessions (registered via INV-0212) through real six-minute schedules with a provider double, then a separately bounded approved-provider run; include mixed TP/PE load and check-in/model overlap.
- [ ] **INV-1508** Measure G8 command p95 <500 ms and scheduled-delivery p95 <1 second outside deliberate pauses as proposed targets; report browser exposure separately; provider p95 beyond the configured fallback deadline under this load is a failure.
- [ ] **INV-1509** Execute G9 through the applied `/investigators/` ingress: auth boundaries, real heartbeat arrivals/tail stalls, persistence failures, provider outage, restart, restore/deletion and compatible rollback.
- [ ] **INV-1510** Package immutable source/image/manifest/fixture/snippet hashes, commands/environment, results, failures, reviewer decisions and unresolved blockers; reopen affected gates on changes.
- [ ] **INV-1511** Reconcile D01–D14 deadlines with the selected release and produce a concrete pre-launch checklist; no unrun gate or borrowed project result can be labeled passed.

**Exit:** The release candidate has auditable G1–G9 evidence and is ready for the pre-launch portion of G10. Fix failures and rerun affected checks; avoid repeatedly rerunning unrelated suites without a reason.

### WP16 — Initial pilot, audit, expansion and closeout

**Owner:** Research lead with operations and analysis support. **Dependencies:** WP15, applicable D01–D14, authorized batch activation. **Design:** G10, §§11–13.

- [ ] **INV-1609** Verify the D13 record before the initial pilot and again before expansion: *initial-pilot half done — the D13 record (pilot-serves-usability-role election) was verified before `study.live.v10` and the pilot's usability outputs were reviewed in the post-launch (§22); the expansion-time re-verification before N=30 remains open* — either the INV-0911 pre-freeze usability evidence stands, or an explicit record shows the initial pilot serves the usability role and its usability outputs are reviewed before expansion. *(Pre-freeze execution moved to INV-0911 per BA-22 — the D13→D10 freeze must precede WP10b, not the pilot workstream.)*
- [ ] **INV-1601** Complete the research-team walkthrough and pre-launch G10 material/profile/instrument/payment/debrief freeze; record selected platform, pilot size, exclusions, missingness and incident rules (D12/D14). *Progress:* the v10 pre-launch checklist was executed on the pinned `tp_ai_v11` pair and Connect is the selected platform. *Open:* D12 and D14 remain unresolved in `docs/DECISIONS.md`, and the generic pre-launch checklist is not a populated, sealed v10 freeze record — attach the freeze or a recorded scoped waiver (BA-109).
- [x] **INV-1602** Activate only the selected pinned pilot batch after applicable launch authorization; verify entry, allocation, health, completion-code routing and entitlement handling on clearly marked QA sessions first. *Evidence:* `study.smoke.v10` QA sessions verified entry→allocation and health before `study.live.v10` opened 2026-10-12; the full-completion gap closed 2026-10-12 — session `465b4009` (TEST_* synthetic, arm low, member `.ai`) drove Connect `?batch=` entry through the full spine to `completion` with `handoff.platform: connect`, `completion_code: PLACEHOLDER-CODE` matching `CONNECT_COMPLETION`, and a 350¢ `payment.s5_base_only@v5` entitlement (`docs/evidence/2026-10-12-postlaunch-v10-pilot/qa-smoke/QA-SMOKE.md`, BA-109).
- [x] **INV-1603** Monitor initial enrollment for exposure failures, hidden evidence, data loss, fallback, late jobs and technical stops; use the defined enrollment-pause response without silently changing active treatment. *Done:* all six v10 entries monitored through the post-launch review; the enrollment-pause response was exercised to close intake after N=4 completers; no silent treatment change.
- [x] **INV-1604** Audit every initially displayed generated response and fallback with context, source support, actual timing and recipient receipts; review all seemingly proactive shares for prior solicitation. *Complete for v10 (2026-10-12):* all 19 model calls audited in `docs/evidence/2026-10-12-postlaunch-v10-pilot/ASSISTANT-CHANNEL-AUDIT.md` — 12 social replies (11 generated displays + 1 sanctioned `deterministic_script` fallback) + 7 assistant calls, all verdicts `supported`, every claim source inside the participant's own file (zero hidden-record/wrong-crate disclosures), timings in budget, no unsolicited generation. 18/19 delivered outputs have exact render receipts; the single gap (assistant output seq 105 on `b0466488`) is adjudicated a missed client telemetry emission — channel liveness proven by immediately-following receipts — and retained as explicit exposure uncertainty, not claimed as displayed.
- [x] **INV-1605** Produce pilot accounting for assigned/exposed/completed/technical/withdrawn sessions, world and rating-seat balance, opportunity durations, evidence bottlenecks, incident intervals and manipulation checks; check that outcome differences cannot be explained by missing evidence, unequal failures or different time-at-risk; preserve small-pilot uncertainty. *Complete for v10 (2026-10-12):* funnel/allocation/schedule/measurement accounting in the post-launch README plus `ACCOUNTING-ADDENDUM.md` — 6 distinct worlds, coin-flipped remark-seat order, near-balanced JT/PM holdings, all 5 guaranteed seat_plan opportunities delivered on schedule (bottleneck = participant engagement, not delivery), zero incident rows, technical stop kept in operational accounting, differential-failure table shows no arm/member/build-systematic pattern; small-pilot uncertainty flagged throughout.
- [ ] **INV-1606** Resolve violations with versioned fixes and targeted gate reruns; record the expansion portion of G10 separately from the earlier launch decision. *Progress:* the v10 usability finding (practice share not discoverable) is resolved via `training.reference@v6`/`tp_ai_v12` — **preview-only**, verified share-only advance on signed links; the expansion G10 decision is not yet recorded.
- [ ] **INV-1607** Expand only under the selected next batch/release decision; retain compatibility keys and avoid pooling changed treatments or measures implicitly. *Open:* next step is the N=30 effect-size/power cohort (AI-only 1×2, no scripted contrast — AID-63); `tp_ai_v12` produces a new compat key, so v10 and v12 cohorts will not pool implicitly.
- [ ] **INV-1608** Close batches with consistent exports, entitlement/payment reconciliation, data-use disposition, identity purge and backup/tombstone verification; archive reproducible analysis and release records. Hand eligible initial-pilot text to WP12b. *Progress 2026-10-12:* v10 closeout largely done — dashboard export + per-member offline bundles + fresh `pg_dump`, entitlement review (5 pending 350¢ incl. the payable technical stop), data-use choices recorded (4/4 `use_all`). **Remaining:** Connect-platform payment reconciliation (student's manual step) and identity-purge execution.

**Exit:** The initial pilot is audited, expansion has explicit evidence, and operational/research closeout preserves the approved data lifecycle. A successful page load is not this milestone.

### WP17 — Researcher study dashboard

**Owner:** Engineering. **Dependencies:** WP11 measure registry, WP13 admin surface. **Design:** `INVESTIGATORS_TEAM_EXPERIMENT_DASHBOARD_PLAN.md` (audited). Read-only `/admin/dashboard/*` endpoints over the shared cohort contract (receipt-based outcomes, tombstone/data-use eligibility, compatibility partitioning), one `REPEATABLE READ READ ONLY` snapshot per request, plus a `dashboard` admin tab with nine descriptive sections. Phase A is operational/descriptive only — inferential statistics, MDE scenarios and outcome families are Phase B, gated on D12/D14/O-13.

- [x] **INV-1700** Dashboard filter/cohort/outcome contract, typed envelopes, `REPEATABLE READ READ ONLY` snapshot wrapper, `/admin/dashboard/overview`, `GET /admin/exports` catalog, `GET /admin/exports/tables` vocabulary, `POST /admin/exports/table` per-table CSV download (CSRF-gated, single-snapshot, checksummed, free-text-withholding per dashboard plan §3.2).
- [x] **INV-1710** Researcher-only gates on all dashboard routes (401/403 contracts), `Cache-Control: no-store`, read-only enforcement; data-egress downloads run as gated POST mutations.
- [x] **INV-1709** Frontend `dashboard.tsx`: nav wiring after `documentation`, filter bar (study/batch/compatibility/mode/UTC range), nine sections, AbortController race fencing, session-detail handoff.
- [x] **INV-1702** `/admin/dashboard/readiness`: profile-aware coverage flags (variant stage paths, bound item keys, exposure anchors), paginated completer audit.
- [x] **INV-1703** `/admin/dashboard/timing`: stage-visit reconstruction (re-entry preserved), wall vs active durations, open-visit censoring, task-budget consumption.
- [x] **INV-1704** `/admin/dashboard/instruments`: bound artifact wording via ContentStore (hash-checked, pointer-only labelled), codebook presentation.
- [x] **INV-1705** `/admin/dashboard/analysis` items: per-item descriptives and missingness from `item_response`.
- [x] **INV-1706** Power inputs (descriptive): per-arm N/mean/SD/coverage on the registered scalars; MDE scenarios reported `unsupported_model` (Phase B).
- [x] **INV-1707** Arm contrasts (descriptive): per-partition per-arm descriptives + direction labels; inference fields null with `analysis_not_configured`.
- [x] **INV-1708** Neutral findings cards citing cohort, measure keys, Ns and the analysis digest.
- [x] **INV-1711** Disposable-Postgres integration tests: authz boundary, no-store, filter validation, tombstone/cohort rules, section envelopes, export catalog, read-only snapshot, snapshot-concurrency proof, data-use/withdrawal exclusions, download withholding/checksum contracts, 1,000-session benchmark (under 30 s).
- [x] **INV-1712** Docs, ledger, trace, gates and deployment records.

**Exit (Phase A):** the nine dashboard sections render committed data for filtered cohorts under one snapshot semantics; nothing inferential is implied. Phase B (inference, MDE, declared outcome families, semantic measures) requires D12/D14/O-13 and its own registration.

## 6. Gate ownership and evidence index

| Design gate | Implementing packages | Required release artifact |
|---|---|---|
| G1 — Content/configuration | WP00/WP02/WP09 | Bound manifests for all eight profile/arm combinations, arm diffs, material/decision versions, executable-policy checks, timing conformance matrix. |
| G2 — World validity | WP05 | Seeded-world report, hand-audited oracle cases, permission/projection denial results, rating-seat independence report. |
| G3 — Behavior fidelity | WP05/WP07/WP10a/WP10b | Reference traces (including warning), Add-to-file boundary, v2 diffs, paired-arm fidelity, seat-policy balance and solicitation reports. |
| G4 — Timing/concurrency | WP03/WP06/WP08 | Actual timeline traces, DB races, multi-worker delivery, restart/replay, warning and deadline results. |
| G5 — AI/source integrity | WP05/WP10a/WP10b | Context/claim fixtures, adversarial cases, choice-salvage rejection, held-out human and actual-provider evidence. |
| G6 — Browser journey | WP04/WP08/WP09/WP13 | Chromium/Firefox traces joined to saved DB records for all eight profile/arm combinations, including withdrawal and completion routing. |
| G7 — Analysis/export | WP11/WP12a (pre-launch); WP12b is the post-pilot semantic-use gate only | Exact reconciliation, legacy mapping, denominators, coverage and export authorization tests. Pre-launch semantic status is honestly `unavailable`/development-set-only — never a semantic PASS; WP12b is mandatory before semantic analytic use. |
| G8 — Load/failure | WP06/WP10b/WP14/WP15 | Full-duration load report, model-double versus actual-provider results, actual latency distributions, worker/outbox lag observability. |
| G9 — Operations/security | WP03/WP04/WP10a/WP13/WP14 | Role/auth tests, provider/persistence outage behavior, actual `/investigators/` proxy results, restore/deletion and rollback rehearsal. |
| G10 — Pilot readiness/audit | WP09/WP15/WP16 | Pre-launch material/decision freeze (including D13 evidence and INV-0910 audit) plus later initial-pilot audit and expansion decision. |
| Semantic-use gate (§12.2) | WP12a/WP12b | Frozen rubric/corpora, independent coding/adjudication on real held-out data, coverage, metrics and immutable PASS or explicit unavailability. |

G10 is deliberately staged: the initial-pilot output audit cannot precede that pilot. Pre-launch readiness must pass first; output audit is then mandatory before expansion. The semantic-use gate is likewise separate from basic event instrumentation, is evaluated on post-pilot real data (WP12b), and does not retroactively certify prior unvalidated semantic analyses.

## 7. Risk-driven integration checks

| Risk | Preventive implementation | Decisive evidence |
|---|---|---|
| Configuration says one thing, runtime does another | WP02 policy resolver, timing conformance matrix and rejecting unsupported fields | A changed supported field changes the intended trace; irrelevant fields do not. |
| Same facts become multiple apparent sources | WP05 claim/evidence lineage | JT/PM/assistant repetitions retain one record lineage. |
| Newly added fact leaks into an earlier request | WP05/WP10a immutable snapshot cutoff | Share/add during generation cannot change the original request's authorized context. |
| Polling/backdating makes replies appear timely | WP06 independent scheduler and actual timestamps | Browser absent across due time; commit and late receive/render remain distinct. |
| TP pause changes one channel but not another | WP06 typed clocks and WP08 stage gates | All task delivery waits through check-in; wall-based collection alone proceeds; the one-minute warning still fires at active 300. |
| Rating-seat assignment confounded with seat information or speed | INV-0510, INV-0708 | Joint distribution of high-rating seat × decisive holder × request sender × seat policy shows independence or documented balance. |
| Slow provider changes the treatment | WP10a bounded calls and invariant fallbacks | Recorded real-provider waits and fallback differences, no clock extension; p95 within fallback deadline. |
| UI polling erases answers or steals reading time | WP08 revision/draft/focus rules | Stale snapshots and focus changes preserve unacknowledged drafts and scroll. |
| Missing coded data becomes “no sharing” | WP12a explicit status/coverage | Not-coded/failed/abstained cannot enter zero-mention denominators. |
| A working demo masks data loss | WP03/WP04 persist-before-display/completion | DB failure produces no false acknowledgment; restart reconstructs accepted work. |
| Cleanup or migration harms a live study | WP01/WP14 isolated ownership and guarded tests | Disposable DSN proof, export-first cleanup, compatible migration/restore evidence. |
| Route or port collision with another study | Distinct `/investigators/` route, 8518/8519, dedicated snippet (INV-1401/1403) | Proxy checks show neighbor-study `/team/` unaffected; no shared port, allowlist or secret include. |
| Admin data exposed because Apache does not enforce SSO on admin paths (selected layout) | Application-enforced admin auth; identity headers stripped and never trusted (INV-1301, INV-1403) | Live-proxy tests: unauthenticated and forged-header admin UI/API requests rejected; no admin data in public responses. |
| Pilot changes are hidden in pooled results | WP02/WP11 compatibility keys | Aggregation requires explicit compatible versions and exposes exclusions/incidents. |

## 8. Completion, deferral and change control

The full implementation is complete when the shared engine supports both reference variants and selected pilot profiles, durable and authorized behavior is demonstrated through G1–G9, required research decisions are resolved for the selected batches, the pre-launch and post-pilot portions of G10 are recorded at their proper times, and measures used analytically have the required validation. Runtime, data/analysis, and operational completion are all necessary.

Keep the following outside baseline delivery unless the research design changes: new timing-factor experiments, unrestricted autonomous teammates, real human-team matching, external provider/judge fallback, ratings-contingent bonuses, new round/crate deadlines, compulsory pre-task teammate votes, model-majority outcomes, and the later human-coded social-realism/ambiguity studies (HB-10). Reject their activation rather than leaving misleading partially working controls. Raw Qualtrics import execution depends on receiving raw data; preserving compatibility and a truthful import contract does not require inventing it.

For each approved change, update the specification first when behavior or scientific meaning changes, then the affected manifest/content, this task register, the requirement trace (INV-0007), migration/version keys, reference crosswalk and gate evidence, and re-pin the header hashes. Retest the dependency closure of the changed contract. Running sessions keep their pinned compatible release; a deploy is not permission to redraw their world, reassign condition, replace a context snapshot, or restart their clocks.

The next implementation step is WP00 (including INV-0007 and the §2.3 external requests) followed by the scaffold and schema contracts in WP01–WP03; §10 gives the concrete start order. Carry unresolved research choices as explicit launch dependencies while building the deterministic end-to-end slice.

## 9. Requirement traceability

Seed for `docs/REQUIREMENT_TRACE.md` (INV-0007). Each row lists the owning tasks; verification evidence is attached per release under INV-1501.

### 9.1 Specification sections

| Spec section | Owning tasks / decisions |
|---|---|
| §1.1 sources/precedence | INV-0002, 0003 |
| §1.2 pilot implications | D01, D02, D06, INV-0203, 0305, 0607, 0902, 0910, 1103, 1105 |
| §2.1 shared task contract (incl. one-minute warning) | INV-0003, 0203, 0213, 0501–0506, 0802, 0804, 0901, 0905; D04 |
| §2.2 study-specific behavior | INV-0510, 0602, 0702, 0707, 0708, 0902, 0904, 0905 |
| §2.3 demo gaps | INV-0107, 0207, 0408, 0505, 0607, 0809, 0906, 1001, 1003, 1005 |
| §3.1 deployment (`/investigators/`, 8518/8519) | INV-0106, 0402, 0603, 0805, 1401–1403, 1509 |
| §3.2 reuse matrix | Plan §1.1, §8; INV-0401, 1301; D05, D06 |
| §3.3 hidden-bench/Triad boundaries | Plan §1.1, §8; INV-0301, 0603, 0805, 1402, 1510 |
| §4 manifests | INV-0201, 0206, 0208 |
| §4.1 profiles, eight combinations, warning | INV-0202, 0203, 0213, 0903, 0909, 1302, 1505; D01, D03 |
| §4.2 executable config/compatibility | INV-0201, 0204, 0205, 0206, 0207, 0208, 0212 |
| §5.1 enrollment/randomization | INV-0401–0404, 0406; D08 |
| §5.2 state machine | INV-0405, 0408, 0901; D06 |
| §6.1 clock contract, incident policy | INV-0601, 0602, 0612, 0804, 0902; D10, D14 |
| §6.2 jobs/transactions | INV-0303, 0307, 0603, 0605, 0607 |
| §6.3 intended/computed/actual | INV-0603–0605, 0807 |
| §6.4 pause/ordering/deadline | INV-0602, 0606, 0607, 0610 |
| §6.5 distributions, timing mechanisms | INV-0211, 0609, 0911; D10, D13 |
| §6.6 26 dimensions | INV-0205, 0206, 0210, 0213, 0608, 0703, 0706, 0708; D07, D10 |
| §7.1 world invariants (incl. 7) | INV-0501–0504, 0509, 0510 |
| §7.2 access boundaries | INV-0505, 0506, 0509 |
| §7.3 teammate behavior | INV-0701–0705 |
| §7.4 assistant grounding | INV-1001–1005, 1009; D05 |
| §7.5 per-call snapshots | INV-0507, 0704, 1002, 1006 |
| §7.6 claim ledger | INV-0508, 1003, 1005, 0801 |
| §7.7 fidelity across arms | INV-0706, 0708, 1008 |
| §8.1 relational model | INV-0301–0306 |
| §8.2 command/event API | INV-0303, 0407, 0801, 0805 |
| §8.3 durable completion/projections | INV-0408, 0505, 1509 |
| §9.1 canonical measures | INV-0905, 1101–1105; D06 |
| §9.2 exports/legacy | INV-1106, 1107, 1109, 1110 |
| §9.3 evidence/episodes/opportunity | INV-1103–1106 |
| §9.4 offline coding | INV-1201–1210; D11 |
| §9.5 additional exports, coding boundary | INV-1106, 1205, 1208; D05 |
| §10 workbench/completion | INV-0802–0810, 0901, 0906, 0907 |
| §11 admin/privacy/ops | INV-0104, 1301–1309, 1401–1410; D09 |
| §11.1 release evidence | INV-1510, 1511 |
| §12 gates G1–G10 | §6; INV-1009, 1501–1511 |
| §12.1 timing traces | INV-0213, 0610, 0611, 0612, 0810, 1005–1007, 1503, 1509 |
| §12.2 measurement/pilot audit | INV-0509, 1207, 1210, 1604–1606, 1609 |
| §13 decisions | D01–D14 |
| §14 sequence/definition of done | Plan §2, §2.3, §8 |
| Appendix A checksums | INV-0002 |
| Appendix B traceability | INV-0007; this section |

### 9.2 Hidden-bench recommendations

| ID / lesson | Owning tasks |
|---|---|
| HB-01 per-call snapshots, visibility cursor | INV-0507, 1002, 1007 |
| HB-02 typed assertions; choice salvage | INV-0508, 1003, 1005 |
| HB-03 paired-arm fidelity | INV-0703, 0706, 0708, 1008 |
| HB-04 empty/fallback/late; durable completion | INV-0604, 1005, 0408, 1309 |
| HB-05 two-stage coding, coverage | INV-1201, 1202, 1205, 1208 |
| HB-06 immutable coding/gate records; definition drift | INV-1205, 1207 |
| HB-07 evidence transfer, correction/deterioration | INV-0508, 1103, 1111 |
| HB-08 compatibility keys, retries, exact-replicate resume | INV-0208, 0212, 1006, 1108 |
| HB-09 one source tree / migration owner | INV-0101, 0301, 1402 |
| HB-10 later realism/ambiguity work | Deferred (§8) |
| §3.6 four-layer teammate policy | INV-0204 |
| §5 multiple SSE listeners / single-worker assumption | INV-0603, 0805, 1503 |
| Stage A human usability | INV-0911 (pre-freeze), INV-1609 (pre-expansion check); D13 |
| Stage D / E pilot audit and release evidence | INV-1604, 1605, 1510 |
| External judge not a default | INV-1205; D05; §8 |

### 9.3 Timing recommendations

| ID / lesson | Owning tasks |
|---|---|
| F1 concurrent generation | INV-0211, 0604, 0610 |
| F2 independent commit, no backdating | INV-0603, 0605, 0610 |
| F3 commit-time reauthorization | INV-0607, 0611, 0213 |
| F4 one executable policy resolver | INV-0205 |
| F5 ordering vs eligibility; indicator timing | INV-0606, 0610 |
| F6 schema breadth; four-way quantity labels | INV-0206, 0609, 1106 |
| F7 durability, drafts, revisions | WP03, INV-0805, 0806 |
| F8 real artifact hashes, recipient projection | INV-0208, 0505 |
| P1 episodes and opportunity time | INV-1104, 1105 |
| P1 distribution/browser/load checks | INV-0609, 0810, 1009, 1507, 1508 |
| P2 dormant extensions / 26-row taxonomy | INV-0206, 0210 |
| §7 clamp vs rejection; stream independence | INV-0207, 0211, 0609 |
| §8.5 rating-seat alignment | INV-0510, 0708 |
| Warnings (no Triad warning-free baseline) | INV-0003, 0213, 0804 |
| Human plausibility via separate usability work | INV-0911 (pre-freeze), INV-1609 (pre-expansion check); D13 |

## 10. Build start checklist (as of 2026-09-28)

**Ready:**
- Spec, supporting reports and plan are pinned (header table).
- Ingress is applied: `/investigators` → 301, other paths 503.
- Ports 8518/8519 are free.
- `.env` exists with the estate posture (640, `docker-users`).
- Admin allowlist and reference-profile timing are confirmed.

**Operator actions (sudo; can run in parallel with WP00–WP01):**
1. ~~Apply the `.env` posture~~ — done 2026-09-28 (`.env` is 640, group `docker-users`).
2. ~~Set `ADMIN_PASSWORD` and `DATABASE_URL`~~ — done 2026-09-28.
3. ~~Run the INV-0301 database/role script~~ — done 2026-09-28 (`scripts/db/inv0301_bootstrap.sql`); host test database skipped by choice. WP03 still adds a migrator DSN setting, runs Alembic as `investigators_migrator`, and grants compensation tables per migration.

**First engineering sequence:**
1. **WP00:** INV-0001, 0002, 0003, 0005, 0006 and 0007 (seed `docs/REQUIREMENT_TRACE.md` from §9); INV-0004 (open D01–D14 and file the §2.3 requests, especially D03 and D04; D05 fully resolved by the 2026-10-01 IRB amendment — `gemma4:26b` deployed).
2. **WP01:** INV-0101 through INV-0107. Base Compose on `dct/docker-compose.yml`; boot against disposable PostgreSQL and provider doubles only.
3. **WP02 contracts:** INV-0201 through INV-0213, including the eight profile/arm manifests, `timing.mechanism`, and `warning.one_minute_left` v1.
4. **First vertical slice (§2.1)** on WP03–WP06 foundations, then generalize.

**Do not start until the named dependency is met:**
- ~~Real-provider work (WP10b), until D05~~ — **D05 resolved 2026-10-01** (IRB amendment; deployed per §11). Formal INV-1009 acceptance evidence still pending.
- Paid or live enrollment, until the G10 pre-launch freeze and applicable approvals (D03/D04).
- Semantic analytic use, until WP12b.

## 11. Implementation log — 2026-10-01 (live-AI lab readiness round)

This log records the changes made between the E15 audit remediation and the 2026-10-01 lab-testing reset. Gate status after the round: Ruff format/lint clean, strict mypy clean, 1,423 backend unit tests + 207 integration tests passing (includes the BA-34 regression), 72 frontend tests passing, stack healthy at migration head `0005_model_call_outcomes` (`/ready` verified).

### Live model generation (WP10a operationalized; WP10b in progress)

- **Provider selection:** `MODEL_PROVIDER` in `.env` — `ollama` (deployed default), `openai`, `minimax`, `none`, `auto` (`backend/app/providers/select.py`). Toggling is a one-word `.env` edit plus `docker compose up -d investigators-worker`.
- **Local path:** `local_openai_chat` adapter → Ollama `gemma4:26b` (switched from `gemma4:12b` 2026-10-03). The Ollama sidecar owns a stable network namespace on the compose network and the worker joins it, preserving INV-1001's literal-loopback rule (`http://127.0.0.1:11434`) across worker rebuilds. Every call is audited in `model_call` with `model@sha256:<digest>` pinning, and (BA-40) the *served* digest is verified against `LOCAL_MODEL_DIGEST` via Ollama `/api/tags` at worker startup and before every generation — a confirmed same-tag/different-digest mismatch or malformed listing fails closed; an unreachable listing is treated as an outage (deterministic fallback).
- **Thinking-model latency:** `reasoning_effort: "none"` is injected into every local request (capability-gated in `run_generation`, recorded in `model_call.parameters`). Without it the model's hidden reasoning consumed ~24 s of a 25 s timeout. `temperature: 0.2` for more literal, checkable output. Warm teammate replies ≈2 s.
- **Remote adapters:** `backend/app/providers/remote.py` — `RemoteOpenAIProvider` (chat-completions + Bearer) and `RemoteMiniMaxProvider` (Anthropic `/v1/messages`, `x-api-key`). Both: HTTPS-only, `SecretStr` keys registered with the log redactor. **BA-35 (2026-10-02):** refused for *all* session traffic — the worker fails to start with a remote `MODEL_PROVIDER` and `run_generation` refuses per job (`validation.error_kind="provider_not_allowed"`); preview/QA sessions can carry human tester text, so `APP_MODE` alone is not a participant-data boundary. The adapters remain constructible for offline fixture comparisons. The served-model check accepts dated-snapshot echoes (`gpt-4.1-mini-2025-07-14`); anything else is still model drift. Live-verified 2026-10-01 (pre-BA-35, under preview/QA-only enforcement): `gpt-4.1-mini` 4.9 s, `MiniMax-M2` 5.1 s, plus a full driven session on `openai`. Remote calls send participant text + the pinned file snapshot off-host — lab-comparison adapters only. **The IRB amendment approved 2026-10-01 sanctions a remote path exclusively through the university AI service** (congestion fallback when the GPU is overwhelmed); `api.openai.com`/`api.minimax.io` are not that endpoint, so their session-traffic refusal stays, and a live university AI service fallback needs its own adapter (not yet built).
- **Cold-start hazard:** `gemma4:26b` loads in ≈40 s, exceeding call timeouts — a request hitting a cold model falls back deterministically. Mitigation (2026-10-03): the `investigators-ollama-warmup` compose service runs `ollama run gemma4:26b --keepalive=8760h` at stack start and the worker `depends_on` its success, so the model is resident before any session call; no manual warm-up step is needed (`docs/LAB_SESSION_RUNBOOK.md` §2 updated).

### Output-grounding fixes found through live model output (INV-1003/INV-1005 hardening)

- **Timestamp `voice_format` defect (BA-32 class fix in `engines.py`):** the voice-format check banned `:` outright while the pinned assistant artifact *requires* timestamps like "6:00 PM" — generated answers could essentially never pass. Digit-colon-digit time-of-day patterns are now stripped before the punctuation check; prose colons still reject.
- **`false_denial` gap (`output_claims.py`, checker → v2):** a response claiming "the records contain no information about crate N" carried zero parseable location claims and passed validation *despite being wrong* — the file held 8 entries for that crate. Denial-of-evidence phrasing is now a violation when matching records exist. Immediately caught a live `gpt-4.1-mini` fabrication during remote verification.
- **Known residual:** natural historical narration ("was seen at X, then moved to Y") still produces superseded-location typed claims that the conservative §7.6 checker rejects — participants then receive the deterministic file summary (more complete, always accurate). Richer claim typing (historical assertions, negation-vs-affirmation) is a deliberate follow-up, not a pre-lab rush.

### Participant-facing fixes

- **BA-33 — same-browser stale session wedge:** a browser holding a dead session's cookie (revision 5) minted a fresh revision-1 session, but the store kept the old monotonic revision; every `resume` posted `expected_revision: 5` → `stale_revision` → inert "Preparing your session" forever. `store.ts` now resets all session-scoped state when `session_id` changes; companion fix in `participant.py` stops telemetry events advertising `revision + 1` the server never committed. Regression test asserts a dead-cookie → fresh-link path resumes at `expected_revision: 1` and reaches `writer: held` (72/72 frontend tests).
- **Free-text assistant prompts:** `crate_mentioned()` parses "crate N" from typed questions so the file summary scopes correctly instead of deflecting (integration-tested; out-of-range crate numbers still deflect).

### Administration and cleanup (WP13)

- **Self-service test links** (`/investigators/admin` → Test links): mint links per profile/arm, list only the caller's own issuances (`preview_issue` audit rows store nonce + entry URL; pre-nonce rows are skipped), live session status per link including "Cleaned up — test data removed" for tombstoned sessions. Researcher and operator roles may issue; compensation may not.
- **BA-34 — preview cleanup 500:** `displayed_output` (append-only immutable) FK-references `model_call` and `message`; the unsaved mutable sweep aborted on the FK violation and returned 500. Now each delete runs in its own savepoint; blocked tables are recorded in the tombstone payload's `blocked` list. Tombstoned sessions are excluded from `list_sessions` and research exports; session detail still lists the tombstone work order honestly.
- **2026-10-01 preview cleanup run:** 98 preview sessions tombstoned via the sanctioned `POST /admin/cleanup` (operator role, dry-run then live). Mutable tables cleared (delivery_receipt, outbox, file_membership, seat_holding, scheduled_job, agent_state, clock_interval, request_episode, manipulation_exposure → 0); `model_call` (36 rows) and `message` (315 rows) remain where immutable `displayed_output` children block deletion — audited in the tombstones. Append-only research rows (`event`, `record`, `consent_event`, `draw_log`, `displayed_output`, …), session rows, compensation records (`completion_receipt`, `payment_entitlement`, `result`, `data_use_choice`) and the `admin_audit` trail itself are retained by design — physical removal awaits the separately migrated privileged mechanism (WP13 design), not runtime SQL. The admin board shows zero sessions. 19 `preview_issue` audit rows remain in the issuer's link history with honest statuses; the 6 never-opened links stay redeemable until their signed `expires_at` (no revocation mechanism — stateless tokens).
- **Idempotent reapplication:** `reapply_tombstones` updated for the new `(deleted, blocked)` return shape; blocked-row reporting preserved in `deletion_reapplied` audits.

### Validation evidence this round

- Backend gate (`scripts/test_backend.sh`): format/lint/mypy clean; 1,423 unit + 207 integration tests green on ephemeral PostgreSQL.
- Admin surface: 18/18 integration tests including the new `test_cleanup_survives_displayed_output_fk_block`.
- Providers: unit tests for selection/remote adapters and served-digest verification (BA-40: mismatch/malformed fail closed, unreachable = outage); live smoke calls on all three backends and a driven browser session on `openai` (pre-BA-35; remote adapters now refused for all session traffic) exercising generation → validation → audited fallback.
- Frontend: 72/72 (includes the BA-33 regression).
- Deployed state: `investigators-api`/`-ui`/`-worker`/`-ollama` healthy; `/ready` reports schema head `0005_model_call_outcomes`; worker provider `local_openai_chat`; model warm on GPU with 24 h keep-alive.

### IRB amendment approval (received 2026-10-01, protocol XXXX-XXXX)

Two amendment items approved; next check-in 09/09/2027:

1. **Model/provider disclosure** — Gemma 4 (or latest released Gemma) on review-host via Ollama, frozen SHA-256-verified weights, per-message model logging, no third-party transmission except the **university AI service** congestion fallback. Resolves **D05**. The deployed stack already conforms (Ollama, digest-pinned calls, `model_call` audit); the openai/minimax lab adapters are *not* the approved endpoint and are refused for all session traffic (BA-35).
2. **Generated-content risk/interaction description** — generated (not scripted) teammates and assistant, frozen system prompts, automated pre-display classifier, full audit logging, natural-language chat/requests/shares, "participants are not evaluated." Matches the built guardrails (system prompts, `output_claims`/`teammate_output` validators as the classifier, `model_call` audit). Partially addresses **D03** — the eval-arm premise reveal to the control arm remains the residual confirmation.

The bundled-amendment sequencing policy (one round before paid piloting) still covers the remainder: D03 residual, D04, D06, D09, D13, and anything D08/D14 requires. Recorded in `docs/DECISIONS.md`.

### Open follow-ups carried forward

- university AI service adapter for the approved live congestion fallback (requires the [University] endpoint contract + review).
- INV-1009 formal real-provider acceptance (p95 under INV-1507 load) — currently only driven-session evidence exists.
- Claim-extractor upgrade for historical assertions so generated assistant prose passes §7.6 more often.
- Privileged physical purge mechanism for tombstoned immutable rows (WP13 design) — currently a documented work order, not runtime-executable.
- Preview-token revocation or a shorter lab TTL, if stale never-opened links prove confusing.
- Worker/outbox lag dashboards (INV-1404 remainder).
- **Protocol-conformance follow-up (amendment item 2):** the approved text says agents "respond only with task-relevant information and redirect messages that do not relate to the task." `GROUNDED_SOCIAL_SYSTEM` currently instructs react/acknowledge/point-to-request but has no explicit off-task redirect line; the deterministic `social_fallback` classifier does handle non-task text. Add the redirect rule to the social prompt (bumps `PROMPT_BUILDER_VERSION`) at a deliberate content-review point rather than mid-lab-round — flagged for the research lead.

## 12. Implementation log — 2026-10-01 (UX feedback round)

Student UX feedback (`UX_Feedback.pdf`, 13 items) against the revised piloted reference
`investigators_peer_eval_demo.html` (re-pinned, see `docs/baseline/README.md`).

### Consent (item 1)

`ConsentScreen` now renders the complete protocol XXXX-XXXX consent copy from the revised
demo: [University] heading, purpose, voluntariness/withdrawal, teammate-visibility warning,
initials instruction, partial-disclosure/debrief note, ~15-minute estimate,
payment-code compensation, de-identification/secondary-use and AI-tool disclosure,
contacts, and agree/decline choices. Still bound through the material artifact path
(`text_version` from the bound artifact key; D06 publication pending).

### Workbench and practice (items 2–8)

- **Two-row grid replaces the three-pane row** (item 7): case file left spanning both
  rows (45%), team chat upper-right, AI assistant lower-right, answer bar across the
  bottom — the piloted layout. Responsive collapse to one column on narrow screens.
- **Distinct pane colors** (item 8): per-pane tinted headers/backgrounds (case file,
  team chat, assistant) plus a green answer bar, matching the demo's visual language
  with readable contrast.
- **Case-file simplification** (items 5–6): sort selector and up/down reorder buttons
  removed; cards remain draggable (drag-and-drop reorder preserved) with a small
  four-arrow grip indicator.
- **Practice mirrors the workbench** (items 3–4): `TrainingScreen` rebuilt on the same
  full-width task-screen grid — case file left, team chat upper-right, assistant
  lower-right, answer controls along the bottom; the confusing top-of-page
  action-confirmation strip is gone. The practice world gained a teammate-shared record
  so the add-to-file flow is demonstrated end to end. The share-whitespace regression
  (item 2) is covered by the rebuilt layout (no stray element after actions).

### Structured asks surface as chat messages (item 9)

- `teammate_request` now commits a participant-authored team message
  ("Requesting intel on Crate N.") linked to the request (`request_id`, `crate`);
  `assistant_request` surfaces the typed prompt verbatim or the phrased crate ask
  ("What does my file say about Crate N?") in the assistant channel. Both carry the
  command's active-axis grant so per-command export lookups stay pinned.
- **Measurement kept honest:** `assistant_use` route classification reads only the
  canonical `assistant_requested` event payload (the command now emits a second
  `message_accepted` event; a last-wins event map previously flipped picker→typed),
  and request-linked *delivery* counts only the responder's messages (`actor ==
  "assistant"`), so the participant's own ask can no longer count as a delivered reply.
  Regressions covered by `test_wp06_engines` request-message cases plus the corrected
  WP10a/WP11 integration assertions.

### Instruments become page-level (items 10, 12)

- **Confidence** renders as a grid of cards — one per answered crate, showing the
  recorded answer with a 0–100 slider — matching the crate-answer submission format.
  "Move the slider to answer" hints and per-item Skip buttons are gone; an unanswered
  crate shows confidence as not applicable. One Continue submits the page.
- **Peer ratings and the post-task questionnaire** use the same page-level contract:
  no per-item Submit/Skip; Continue is enabled only when every item on the page is
  answered (untouched sliders do not count); the backend enforces completeness on
  `proceed`, so a page cannot advance with required items missing. Seat-specific
  `applies_to` persistence and server item IDs are unchanged.
- The artifacts still mark items skippable; the UI now chooses not to offer skip —
  the stored responses are complete values, so measurement needs no migration.

### Debrief and data use (item 11)

The participant-facing data-deletion choice screen is replaced by contact-only copy:
the `data_use_choice` stage still exists and is satisfied by recording the approved
default `use_all` (completion invariant `debrief → data_use_choice → completion` is
preserved deliberately), while the rendered screen shows study contact information
instead of deletion options. Debrief text no longer promises a deletion step. The
backend `data_use_choice` table and export path are unchanged; only the participant
surface narrowed. See `docs/DECISIONS.md` (D09 residual note).

### Task budget unchanged (item 13)

The production task remains 360 s / 5 minutes; the revised demo's 8-minute budget is
documented as a deliberate divergence (`behavior_reference.json` REF-TASK-01).

### Reference-fixture re-pin (change control)

The delivered `investigators_peer_eval_demo.html` is a revised reference (const/let
rewrite, live-AI hooks, generative thread replies + canned fallback, no check-in or
pause guard, payment-code consent). `docs/baseline/SOURCE_CHECKSUMS.sha256`, spec
Appendix A, the plan header and `docs/baseline/README.md` were re-pinned;
`tests/fixtures/reference/demo_constants.json` was regenerated and PE anchors in
`behavior_reference.json` re-mapped (removed layers recorded via `absent` anchors);
`tests/contracts/test_compose_contract.py` now encodes the `investigators-ollama`
sidecar boundary (INV-1001). The requirement trace was regenerated.

Consequence recorded in spec §4.1: `pe_demo_v1` as pinned still executes the
original demo's voice/acknowledgement/crosstalk policy, which the revised
reference removed. Realigning the reference profile is a new manifest version
under D07/D10, not a silent update. Also noted in the spec: the revised PE demo
carries no bonus copy (S2 still advertises $0.25/crate), so the §13 bonus
reconciliation row is now TP-demo-only.

### Admin board (post-feedback tweak, same day)

Per request, "My links" on the admin links tab now hides tombstoned sessions
("Cleaned up — test data removed") behind a `show N cleaned-up links` checkbox
instead of listing them inline; the `deleted` flag on the preview-link session
join is now typed in `src/admin/api.ts`. Backend and audit unchanged.

## 13. Implementation log — 2026-10-02 (audit remediation round 3: BA-35–BA-42)

This log records the remediation of the audit's §0.6 second-round findings
(`INVESTIGATORS_TEAM_EXPERIMENT_PLAN_AUDIT.md`, items BA-35–BA-42). Evidence:
`docs/evidence/2026-10-02-ba35-42-remediation/` (E17 — full `scripts/check.sh`
gate log + stamped source hash).

- **BA-35 — remote providers are offline-only.** Preview/QA sessions can carry
  human tester text, so `APP_MODE=live` is not a sufficient participant-data
  boundary. The worker now refuses to start with a remote `MODEL_PROVIDER`, and
  `run_generation` refuses remote providers per job (recorded on `model_call` as
  `validation.error_kind="provider_not_allowed"`). `RemoteOpenAIProvider`/
  `RemoteMiniMaxProvider` remain constructible for offline fixture comparisons.
- **BA-36 — per-item skip restored.** Instruments still submit at page level
  (backend `proceed` completeness enforced), but every item again exposes a Skip
  control: `item_skip` commands persist, reload rehydrates skipped state, and a
  later answer overrides a skip (and vice versa).
- **BA-37 — literal consent copy bound.** `consent.reference@v2` carries the
  verbatim Appendix B panel copy, including the previously omitted "may skip
  questions without losing payment" and early-leave sentences. `ConsentScreen`
  renders the bound artifact body verbatim; the recorded `text_version` must
  equal the session's bound artifact key — mismatches are refused at the
  command boundary.
- **BA-38 — keyboard reorder equivalence.** The case-file drag grip is a
  focusable button with ARIA sortable semantics; arrow keys move the card within
  the filtered order through the same `moveFileRecord`/`reorderFile` semantics
  as drag, and a live region announces the resulting position.
- **BA-39 — honest tombstone semantics.** Tombstoned sessions no longer
  authenticate (`credentials.authenticate`), redeem preview descriptors, resume
  live entry, receive credential recovery, or have their scheduler jobs claimed.
  Monitor/balance/session lists exclude them; `/admin/previews/mine` reports
  `deleted` + `purge_pending`; `session_detail` and `/admin/retention` expose
  pending-privileged-purge row counts. Tombstone = revocation + purge work
  order, not a claim that append-only/identity rows are physically gone.
- **BA-40 — served-digest verification.** The local adapter verifies Ollama
  `/api/tags` identity against `LOCAL_MODEL_ID`/`LOCAL_MODEL_DIGEST` at startup
  and before every generation when a digest is pinned: confirmed
  same-tag/different-digest and malformed listings fail closed; an unreachable
  listing remains an outage (deterministic fallback). Served digest is recorded
  in call params.
- **BA-41 — off-task redirect prompt rule.** `GROUNDED_SOCIAL_SYSTEM` now
  requires redirecting off-topic participant messages back to the investigation
  rather than engaging them (the amendment's generated-agent control).
  `PROMPT_BUILDER_VERSION` bumped to `grounded_prompts_v3`, recorded on
  `model_call.builder_version`, and added as an explicit compatibility-key
  component (`prompt_builder`) so prompt changes never pool sessions.
- **BA-42 — evidence + counts.** The final gate log and source hash are archived
  in E17; this plan's integration-test count corrected from 206 to 207; pinned
  inputs re-pinned for the legitimately changed files (`.env.example`, spec,
  audit addendum, and the owner-edited external `TEAM_AI-R_AI-M_STUDY_PLAN.md`
  reference).
## 14. Implementation log — 2026-10-02 (BA-37/38/40/42 corrections)

Independent re-review of the BA-35–BA-42 closeout found four overstatements; this
round corrects them. Evidence: `docs/evidence/2026-10-02-ba37-42-corrections/`
(E18 — development evidence, not G1–G10 release gates).

- **BA-37 — verbatim consent + server fail-closed.** The v2 artifact's
  `#XXXXXXXX` dropped the hyphen that Appendix B's raw text layer actually
  carries (`#2027-` + line-wrap + `0060`). `consent.reference@v3` publishes the
  corrected literal copy and is bound by the new current profiles
  `tp_demo_v3`, `pe_demo_v3`, `tp_review_first_v4`, `pe_pilot_v4` (v1 pointer
  and v2 superseded lineages stay published for reproduction, labeled
  "(legacy)"). The consent handler now fails closed: `consented` on a
  pointer-only bound artifact is refused as `consent_unavailable` even when the
  client submits the matching `consent.reference@v1` key — so legacy manifests
  cannot silently produce consenting sessions; declining still reaches the
  honest terminal state. `/admin/preview-combos` reports `consent_key` +
  `consent_current`, and one-of-each issuance only mints current-consent links.
- **BA-38 — click reorder added.** The grip gained the spec's click
  alternative: click grabs a card (`aria-pressed`, grabbed styling,
  announcement), a second grip click drops it at that position, Escape or
  re-click releases without moving. Drag and arrow-key reorder unchanged.
- **BA-40 — digest pin made mandatory.** Empty `LOCAL_MODEL_DIGEST` no longer
  bypasses verification: settings validation rejects a configured local
  endpoint without a digest, the adapter refuses construction unpinned,
  `provider_from_settings` verifies `/api/tags` at startup, and `generate()`
  re-verifies unconditionally per call (mismatch/malformed = fail closed;
  unreachable = outage → deterministic fallback).
- **BA-42 — stamped deployment.** The earlier rebuild bypassed
  `scripts/build_images.sh`, so live `/version` reported
  `source_sha256:"unset"`. Images were rebuilt through the stamping path and
  `/version` now reports the bundle hash for the deployed source.

## 15. Implementation log — 2026-10-02 (UX feedback round 2)

Second student UX pass (`UX_Feedback_v2.pdf`, 13 items) against the E18 build.
Evidence: `docs/evidence/2026-10-02-ux-round-2/` (E20 — development evidence,
not G1–G10 release gates). E19 (`2026-10-02-admin-legacy-filter`) covers the
intervening admin-console round: "(legacy)" preview combos hidden behind an
opt-in checkbox, mint feedback copies the entry URL to the clipboard and
announces it, and the practice footer names only remaining actions phrased as
the controls that perform them.

- **Consent/debrief stray text.** The "Study information reference" and
  "Debrief reference" provenance lines are removed from participant-facing
  copy; the bound-artifact key is still recorded server-side.
- **Background/initials page.** The walkthrough opens with a background page
  collecting 1–3-letter display initials via the new `set_initials` command.
  Initials are stored in `app.participant_display` (migration 0006): excluded
  from every research export (`EXPORT_EXCLUDED_TABLES`), absent from command
  results and event payloads, invisible in admin views, and deleted by the
  tombstone's mutable purge. Same-value resubmission is idempotent; changes
  are refused `initials_locked`.
- **Walkthrough format.** Instructions are paged orientation copy and the
  training stage is two try-it pages (share/add/ask-team, then
  ask-assistant/answer) rather than all controls at once; each page's Continue
  gates on that page's required practice actions. The fixed practice world and
  `training_step` provenance are unchanged.
- **Case file.** Sort/filter controls and the reorder hint text are removed;
  drag, click-grab/drop and keyboard reorder are unchanged.
- **Shared state.** A participant-shared record shows "Shared in team chat" in
  place of its Share control; `_record_share` dedupes server-side
  (`record_already_shared` on repeats) and the workbench projection carries
  `shared` record IDs.
- **Greeting window.** Greeting options render only while a `greeting_prompt`
  event is within 30 active seconds and disappear on send or expiry; replayed
  prompts cannot resurrect them.
- **Confidence empty state.** A session that locked zero crate answers renders
  "no crate answers to rate" with a legal Continue instead of an empty page.
- **Auto-skip.** Per-item "Prefer not to answer" controls are gone; submitting
  an instrument page auto-records `item_skip` for every untouched item (the
  backend still enforces all-items-resolved on `proceed`).
- **Ratings piping.** `{seat}` placeholders in instrument text substitute the
  display name; the participant's initials label their own row in the check-in
  reveal and connecting screens with collision-safe display naming
  (`state/names.ts`).
- **Completion path.** The debrief screen's Continue records the `use_all`
  data-use default and completes in one step — the questions/concerns page is
  gone; `data_use_choice` remains the resume fallback for a session that
  re-enters between stages.

## 16. Implementation log — 2026-10-02 (audit remediation round 4: BA-43–BA-49)

Code-quality audit addendum remediation (audit §0.8). Evidence:
`docs/evidence/2026-10-03-ba43-49-remediation/` (E21 — development evidence,
not G1–G10 release gates). New tests: `backend/tests/integration/test_audit_ba43_49.py`
(13) and the "2026-10-02 audit remediations" block in `frontend/tests/store.test.ts` (7).

- **BA-43 answer save ordering.** `SessionStore` serializes saves per crate
  (`answerInFlight`) and an acknowledgement commits only the value that was sent:
  a newer draft stays `dirty` and is requeued (`requeueFlush`); failed saves
  requeue only a different unsent value, never loop a rejected one.
- **BA-44 resumable final handoff.** `complete()` mirrors `dispatch`'s recovery:
  a persisted `stale_revision` resyncs and retries under a NEW command UUID
  (rejection receipts are final for their id); uncertain outcomes still replay
  the same id for receipt recovery. `_data_use_choice` is idempotent
  (`{"already": true}` no-op; conflicting choice refused `choice_locked`).
  `useBusy` now exposes a visible retryable error rendered on the debrief and
  data-use screens instead of silently swallowing the failure.
- **BA-45 dispatch vs generation.** `scheduler.tick` finishes every pure-dispatch
  job before any provider generation runs, and re-leases each generation job via
  `extend_job_lease` (lease = generation timeout + dispatch margin, authorized by
  the same claimed/lease-owner check `finish_job` uses). A job whose lease was
  lost is skipped — never double-run; `finish_job` stays the sole commit point.
- **BA-46 event loop.** `/commands`, `/complete`, `/telemetry` execute their
  synchronous persistence via `run_in_threadpool` (command tx, writer renewal,
  telemetry batch insert) — verified by an in-flight heartbeat test.
- **BA-47 projection fencing.** Session and workbench snapshots reject any
  response older than the last applied revision for the current session
  (per-kind watermarks); `sessionEpoch` fences in-flight reads across session
  re-entry; `resetSessionScoped` clears both so low-revision re-entry still works.
- **BA-48 single inference budget.** `LocalChatProvider.generate` recomputes the
  remaining deadline before identity verification, semaphore acquisition, and
  the HTTP exchange; a spent budget or a late response fails `TIMEOUT`
  immediately; response bodies bounded at 4 MiB.
- **BA-49 envelope validation.** `/commands` and `/complete` share
  `_command_envelope` (64 KiB body cap, JSON-object check, UUID/int4-revision/
  payload-object/non-negative-client-ms validation) → stable 400s; unknown kinds
  surface as `409 unknown_command`; `/telemetry` gets the same object/JSON/size
  checks. Table-driven tests assert no `internal_error` and no committed state.

### 16.1 Verification follow-up — 2026-10-03 (BA-44–BA-49 second pass)

Independent verification of E21 confirmed BA-43 but found the first-pass
remediation incomplete for the other six findings; this entry records the
second-pass corrections (same evidence directory, new gate log).

- **BA-44 — durable handoff error.** The debrief→data-use→complete chain moved
  into `SessionStore.finishHandoff`; failures after `debrief_ack` unmounts the
  debrief screen now land in store-level `handoffError`, rendered by whichever
  stage screen is visible. The chain re-checks the live stage before each step,
  so a resync-advanced session skips already-committed commands.
- **BA-45 — claim-level partition + dedicated generation thread.** `tick` split
  into `tick_dispatch` (excludes provider-needing `generate` jobs at claim time
  via `claim_jobs(defer_provider_generation=True)`) and `tick_generation`
  (`kinds=["generate"]`); the worker runs generation on its own thread. A job
  becoming due during a model call is claimable on the next dispatch poll —
  verified by a real-DB claim test, not just ordering of one batch.
- **BA-46 — auth offloaded too.** `_authenticate`'s synchronous SQL now also
  runs via `run_in_threadpool` in the three async POST handlers; the GET routes
  are sync `def` handlers and already authenticate off the loop. A heartbeat
  test inverts the delayed-auth reproduction.
- **BA-47 — unified snapshot fence.** Per-kind watermarks replaced by a single
  `snapshotRevWatermark` shared by session snapshots, workbench snapshots, and
  committed command receipts — an older workbench can no longer roll back a
  stage a newer session snapshot established. `resync` re-checks
  `sessionEpoch` after the events fetch (re-anchored to the epoch the applied
  session established).
- **BA-48 — deadline-bounded body read.** The urllib transports read the body
  via chunked `read1` with the socket timeout shrunk to the remaining budget
  per chunk and the deadline re-checked between chunks; expiry raises
  `TimeoutError` and closes the response. A real loopback trickle-server test
  asserts the call ends near the budget (~0.2 s), not ~1.9 s.
- **BA-49 — remaining 500 paths closed.** `client_monotonic_ms` is int-range
  checked before float conversion (no more `OverflowError`); telemetry items
  bound `client_ms` to int8, range-check receipt `seq` before the bigint bind,
  and require `delivery_kind` to be a string before frozenset membership (an
  array used to `TypeError`); `/complete` catches `CommandConflict`; the 64 KiB
  cap already applied during the streamed read (`_bounded_body`) — now also
  asserted by test.

The first second-pass gate run also exposed a defect in the new code itself:
`worker.run` could return without `stop` set (the `max_beats` test path), and
the daemon generation thread kept claiming `generate` jobs against the
shared test database — 22 ordering-dependent integration failures, each file
green standalone. `run()` now always signals `gen_stop` and joins the thread
in `finally`; `test_generation_thread_does_not_outlive_run` pins it.

## 17. Plan — UX feedback round 3 (`UX_Feedback_v3.pdf`) — 2026-10-03

Third tester pass against the E21 build (`baa0f408…57c03`): 12 UX items, 3
feature requests, 3 AI prompt/style items. Target evidence:
`docs/evidence/2026-10-03-ux-round-3/` (E22 — development evidence, **not**
G1–G10 release gates). This continues the lab-prototype sequence: finish a
working prototype, test with the professor and PhD students, then bundle every
remaining protocol decision into **one** IRB amendment before paid piloting.
Items marked **[IRB bundle]** change participant-facing protocol behavior and
belong in that amendment.

### 17.1 UX items

| # | Feedback | Plan | Where |
|---|---|---|---|
| UX-1 | Remove the "1 action left to try — …" footer on the try-it pages | Delete the `practice-note` footer and `PRACTICE_ACTION_HINTS`. The Continue button stays disabled until the page's required actions are done, so the gating is unchanged. | `StageScreens.tsx` `TrainingScreen` |
| UX-2 | Overlapping buttons on the instructions page | The fixed `.withdraw-overlay` (bottom-right, z-index 40) sits on top of the panel's primary button on short viewports. Move "Leave the study" into normal document flow beneath the panel on panel screens, and reserve bottom padding so nothing can overlap it. | `App.tsx`, `styles.css` |
| UX-3 | Drop the "Record your answers" practice | Remove the answer-picker section and the `draft_answer` required action from page 2. Page 2 becomes assistant-only and is retitled "Practice — the AI assistant". | `TrainingScreen` |
| UX-4 | Waiting room: one teammate already connected, the third joins after the participant, plus "The clock starts when all of you have pressed Start." | Order rows as already-present teammate (JT, connected from first paint), then the participant, then PM ("joining…" until the scheduled `teammate_presented`). JT's presence is committed in the same transaction as the stage transition, so the screen shows it as connected rather than waiting for the event round-trip. Add the clock-start sentence. The server Start gate is unchanged. | `ConnectingScreen` |
| UX-5 | Remove the option to skip the time-pressure question **[IRB bundle]** | Remove the "Skip this question" button from `CheckinOverlay` (UI). To enforce it on the server, relax schema `Checkin.own_rating_skippable` from `Literal[True]` to `bool`, and have `_pressure` reject `pressure_skip` (`skip_not_permitted`) when the bound policy says it is not skippable. New TP profile versions (§17.2) set it to `false`. Published profiles stay immutable, and the inactivity-cap outcome still records non-response. | `CheckinOverlay.tsx`, `schema.py`, `lifecycle.py`, new profiles |
| UX-6 | Share button flickers ("Sharing…" → "Share" → "Shared in team chat") | The local `busy` clears when the command acks, but the durable `sharedRecordIds` arrives later with the event or workbench refresh. Fix: mark the record shared optimistically in the store from the committed `record_share` receipt (it is idempotent and deduplicated server-side), and roll back only on rejection. The control then goes straight from "Sharing…" to "Shared in team chat". | `store.ts` `shareRecord`, `CaseFile.tsx` |
| UX-7 | Remove "Crate" labels | Remove the visible `Crate N` badge on case-file cards (workbench and practice). Keep crate identity in accessible names (`aria-label`) and in the answer bar, because participants still need to know which crate an answer is for. Selectors keep the numbers, with simplified wording. The tests that currently read `.file-crate` move to `data-crate` attributes. | `CaseFile.tsx`, `TrainingScreen` |
| UX-8 | Teammate-added intel should look different from your own | Use the existing participant-safe `FileRecord.source` (`initial` / `added_from_team`). Add a `file from-team` class with an accent border/background plus a "From team chat" tag. No seat, holding or truth information is exposed. | `CaseFile.tsx`, `styles.css` |
| UX-9 | Cue that the AI assistant is generating a response | Add `assistantPending` to the store: set it when `assistant_request` commits, and clear it when an assistant-channel `message_delivered` arrives (or on stage change or task close). Rebuild it on resync from the event log: an `assistant_requested` with no later assistant delivery means pending. Render an animated "Assistant is thinking…" bubble with `role="status"`. | `store.ts`, `AssistantPanel.tsx`, `styles.css` |
| UX-10 | Visually separate the submission bar from the workspace | Give `.submit-bar` a tinted background, a strong top border and shadow, and a heading ("Your answers"). | `Workbench.tsx`, `styles.css` |
| UX-11 | "Questions or concerns?" flashes between the debrief and "You are finished" | Add a store-level `handoffInFlight`. While the normal debrief chain runs, `App` keeps the debrief screen in a "Finishing…" state instead of mounting `DataUseScreen`. A session that genuinely reloads in `data_use_choice` still gets the resume screen, and errors still show via `handoffError`. | `store.ts`, `App.tsx` |
| UX-12 | Remove two completion-page sentences | Delete "Copy it and keep it — …" and "You can now return to the study platform. This page can safely be reloaded." Keep the completion code. | `CompletionScreen` |

### 17.2 Feature requests

**FR-1 — interaction-mode feature flag (structured + natural language vs
natural-language only) [IRB bundle].**
- **Schema.** Add `interaction.mode: Literal["structured_and_nl", "nl_only"]` to
  the manifest as an arm-invariant study control (it is not under
  `manipulation.arm.*`, so `check_paired_arms` keeps both arms identical).
  Resolve it into `ResolvedPolicy.controls["interaction_mode"]`; it is part of
  `behavior_policy_hash`.
- **Server enforcement.** Under `nl_only`, `channels_open` never yields
  `structured_seeking`, and the projected channel list omits it, so
  `teammate_request` is rejected `channel_closed` by the existing gate.
  `assistant_request` requires a non-empty `body` (a crate-only request is
  rejected `structured_request_disabled`), and the scripted teammate-to-participant
  asks are unaffected.
- **Participant projection.** Add a neutral `interaction: {structured: bool}`
  to the workbench and practice projections. It carries no arm label or
  implementation vocabulary.
- **UI.** When `structured` is false, the "Ask the team about…" and "Ask the
  assistant about…" selectors are not rendered (workbench and practice). The
  practice `ask_team` action is satisfied by sending a team chat message, and
  `ask_assistant` by typing a question.
- **Profiles.** Pinned profiles cannot be edited, so publish new versions:
  `tp_demo_v4`, `pe_demo_v4`, `tp_review_first_v5`, `pe_pilot_v5`
  (`structured_and_nl`, check-in not skippable). Also publish NL-only siblings
  `tp_demo_nl_v1` and `pe_demo_nl_v1` for lab A/B of the flag. Add the diff
  records and PROFILES/PROFILE_PAIRS entries, and regenerate the schema/docs.
- **Tests.** Backend command-gate tests for both modes and frontend rendering
  tests for both modes.

**FR-2 — exportable click-through HTML mock per build.**
- `scripts/build_clickthrough.py` renders one self-contained, editable HTML
  file at `docs/clickthrough/investigators_clickthrough.html` (inline CSS/JS,
  no network). It contains every participant stage in order: consent,
  background/initials, workspace tour, both try-it pages, waiting room,
  workbench (with check-in overlay), locked, confidence, peer ratings,
  questionnaire, debrief and completion.
- Copy is drawn from the bound content artifacts and the same components'
  literal strings where practical. A mode toggle shows both
  `structured_and_nl` and `nl_only`.
- Next/Back always advance: nothing gates progression and nothing connects to
  live agents (canned replies only). Each screen has a stable `id` and an
  "edit me" comment so testers can revise it.
- The page is stamped with the build source hash. `scripts/check.sh`
  regenerates it and fails if it is stale, and a repository contract checks
  that it has no external URLs or `fetch`.

**FR-3 — technical appendix per build.**
- `scripts/build_tech_appendix.py` generates
  `docs/TECHNICAL_APPENDIX.md`. It covers:
  - architecture: API, worker with dispatch and generation threads, UI, local
    model
  - manifest/profile binding and `behavior_policy_hash`
  - the stage graph
  - command/transaction/receipt model
  - scheduler and job kinds
  - timing and active clock
  - generation pipeline (snapshot → prompt → provider → validator → fallback →
    delivery)
  - persistence schema summary
  - event projection and allowlist
  - exports
  - provenance and checksums
  - deployment/version stamping
- Tables (stages, commands, job kinds, channels, profiles, validator classes)
  are generated from code so the appendix stays current. The appendix excludes
  recruitment-platform and participant-identifying details.
- It is stamped with the source hash, and staleness is checked in
  `scripts/check.sh` like FR-2.

### 17.3 AI prompt/style items

- **AI-1 — no repeated multiword messages.**
  - **Context.** `render_social` receives the seat's prior delivered team
    messages (last N, quoted data) with an explicit instruction never to
    repeat any of them.
  - **Enforcement.** Add a `repeat_message` violation in the output gate: the
    normalized text (case, punctuation, whitespace) matches a prior delivered
    message from either teammate in the session and has at least 3 words.
    Short acknowledgements ("thanks", "np") are exempt.
  - **Fallback.** The deterministic fallback draw skips lines already
    delivered in the session (next unused line in the pool; a pool exhausted
    of multiword lines falls back to the short `default` acknowledgements).
  - **Versions.** Bump `VALIDATOR_VERSION` → `teammate_output_v3` and
    `PROMPT_BUILDER_VERSION` → `grounded_prompts_v4`.
- **AI-2 — warm replies to pleasantries.**
  - Replace the hard off-topic redirect rule: pleasantries and small talk
    ("how are you?", "good luck") get a brief friendly human reply (for
    example "doing ok, bit buried in my crates, you?") without forcing a task
    redirect. Genuinely off-topic or extraction attempts still stay
    unengaged.
  - Fix the fallback classifier so "how are you" maps to `social` even when it
    contains ask-words. Add warmer social lines in a new fallback bank version
    bound by the new profiles.
- **AI-3 — distinct JT/PM voices.** Add per-seat style blocks in
  `render_social`, kept within the validator's voice rules:
  - **JT:** brief, lower-case, dry, uses "yeah"/"np".
  - **PM:** slightly fuller sentences, friendlier, normal capitalization, uses
    "sounds good"/"thanks for that".

  Covered by unit tests of the rendered system prompts and integration tests
  of the duplicate gate and fallback no-repeat.

### 17.4 Sequence and verification

1. Frontend UX (UX-1, 3, 4, 7, 12 in `StageScreens.tsx` first; then UX-2, 5,
   6, 8, 9, 10, 11) with frontend tests.
2. AI-1/2/3 in the backend, with unit and integration tests.
3. FR-1: schema, policy, gates, projection, UI, new profiles plus publish/diffs.
4. FR-2/FR-3 generators wired into `scripts/check.sh` and contracts.
5. Full `scripts/check.sh` gate into the E22 evidence directory, then
   audit/status/checksum updates, stamped rebuild and redeploy, and
   `/version`, `/ready`, worker and schema-head checks. Deployment defaults
   switch to the new profile versions only after the gate passes.

**Open release work is unchanged and still not claimed:** the browser
completion/reconnect matrix, the 40-session timing run, queue-delay
distributions, and G1–G10.

### 17.5 Implementation record — 2026-10-03

Implemented as planned, with these refinements found while building:

- **UX-4.** No backend change was needed: JT's `teammate_presented` already
  commits with the stage transition, and PM joins via the scheduled wall job.
  The screen orders JT → participant → PM and never shows JT as "joining".
- **UX-5.** `Checkin.own_rating_skippable` widened to `bool`. `_pressure`
  rejects `pressure_skip` with `skip_not_permitted` when the bound policy
  says it is not skippable. Earlier profiles keep their published `true`, and
  `store.skipCheckin` was removed from the UI.
- **UX-6.** The committed `record_share` receipt (or a `record_already_shared`
  refusal) marks the record shared immediately in the store.
- **UX-9.** The "generating" cue is derived from the event-backed assistant
  stream (an accepted ask with no reply yet), so it survives reloads and
  resyncs without new store state.
- **UX-11.** `handoffInFlight` is set only when the chain starts on the
  debrief. `App` keeps the same mounted `DebriefScreen` (showing "Finishing…")
  through the transient `data_use_choice` stage; Finish on the resume screen
  is unaffected.
- **FR-1.** Manifest `interaction: {mode}` is optional and omitted from the
  canonical dump when unset, so every earlier publication hash is unchanged
  (pinned by `test_pre_round3_profiles_hash_exactly_as_published`).
  - A schema cross-rule forbids `structured_seeking` in any `nl_only` phase,
    so team asks hit the existing `channel_closed` gate.
  - Crate-only assistant asks are rejected `structured_request_disabled`.
  - The workbench projects a neutral `structured_requests` boolean.
  - Published 2026-10-03:
    - `tp_demo_v4`, `pe_demo_v4`, `tp_review_first_v5`, `pe_pilot_v5`
      (`structured_and_nl`)
    - `tp_demo_nl_v1`, `pe_demo_nl_v1` (`nl_only`; declared in the new
      `PROFILE_VARIANTS` diff records)
  - New artifacts: `prompt.social_demo@v2`, `fallbacks.demo@v2`,
    `fallbacks.neutral@v2`. Proposed-pilot profiles still bind the neutral
    bank and run deterministic-only (INV-0705), and the new pleasantry lines
    avoid busy/stuck/time wording.
- **AI-1.**
  - Prompt context: `prompts.seat_prior_lines` (the speaking seat's last 12
    delivered team lines, bounded by the snapshot's `visible_event_seq`).
  - Generated text: `repeat_message` in the scheduler's in-lock validation
    (`teammate_output_v3`), compared against both seats' delivered lines.
  - Fallback text: the draw rotates past delivered lines.
  - Repeat threshold: lines of 3 or more words (`REPEAT_MIN_WORDS`).
- **AI-2.** The BA-41 off-topic redirect is kept; pleasantries are carved out
  explicitly. The fallback classifier routes pleasantries to `social` unless a
  concrete crate/where ask is present.
- **AI-3.** `prompts.SEAT_STYLES`: JT is brief, lower-case and dry; PM is
  friendlier, fuller and capitalized. Prompt builder `grounded_prompts_v4`.
- **FR-2/FR-3.** `scripts/build_clickthrough.py` →
  `docs/clickthrough/investigators_clickthrough.html` (14 screens, TP/PE and
  interaction-mode toggles, canned replies, no network).
  `scripts/build_tech_appendix.py` → `docs/TECHNICAL_APPENDIX.md` (tables
  introspected from code). Both:
  - are stamped with the source-bundle SHA
  - are regenerated by `scripts/build_images.sh`
  - are verified current by a new `scripts/check.sh` step
  - are pinned by `tests/contracts/test_build_docs.py`
  - live under `docs/`, outside the bundle, so writing them never changes
    the stamp.

- **Admin picker.** `/admin/preview-combos` adds `current`, which is true when
  the manifest has the current consent AND is the newest published version of
  its `<family>_v<N>` line. The "Run a test session" buttons default to the
  six round-3 profiles, and superseded builds sit behind the legacy toggle.
  The first E22 gate run caught the stale 24-combo assertion
  (`check-20261003T035322Z.log`, superseded).

New tests:
- Frontend: UX round 3 and FR-1 blocks in `app.test.tsx` and `wp09.test.tsx`.
- Backend unit: `tests/test_ux_round3_prompts.py`,
  `tests/test_ux_round3_content.py`.
- Backend integration: `tests/integration/test_ux_round3.py`.
- Contracts: `tests/contracts/test_build_docs.py`.

**Evidence (E22).** `docs/evidence/2026-10-03-ux-round-3/`.
- Gate `check-20261003T040334Z.log`: 13/13 PASS on source
  `e80bd59a…8663`.
  - Backend: 1,474 unit and 251 integration tests.
  - Frontend: 109 tests.
  - Repository contracts: 98.
- Deployed: API and UI stamped `e80bd59a…8663`, `/ready` 200, schema head
  `0006`.
- This is development evidence only.

### 17.6 Post-audit remediation — 2026-10-03 (AI-1/2/3 verification gaps)

An independent verification of the E22 build confirmed the gate and deployment
claims but found three narrower gaps in the AI items. All three are closed in
code (no content republish — every change applies to already-published profiles):

- **AI-1a — punctuation-equivalent repeats.** `engines.is_repeat` tokenized
  `don't` and `dont` differently, so a model could evade the gate by dropping an
  apostrophe. `_line_words` now normalizes curly/straight apostrophes and strips
  them from tokens (`"dont send that request"` ≡ `"don't send that request"`).
- **AI-1b — in-flight repeat race.** The generation-time `repeat_message` gate
  only saw lines already committed — two replies could validate identical text
  while both were in flight. New `scheduler._team_line_or_substitute` rechecks
  teammate text against the committed delivery log under the session lock at
  delivery time (`_deliver_text`, `_acknowledgment`, `_crosstalk`) and
  substitutes a still-unused `social_fallback` line. The delivered event carries
  `repeat_substituted` and `displayed_output.fallback` reads
  `deterministic_script` (the `CHECK` constraint's existing engine name —
  `social_fallback` *is* that engine). The deliver payload now carries
  `participant_text` so the substitute stays context-appropriate. Structured
  record shares and scripted-request announcements stay verbatim (substitution
  would corrupt their request/record semantics).
- **AI-2 — greeting + availability misroute.** "hi, do you have a sec?" matched
  `_ASKS` ("have") and got the intel-redirect line. `_PLEASANTRY` now covers
  bare greetings and availability wording ("have/got a sec|minute", "you
  there"); `_CRATE_ASK` is replaced by `_CONCRETE_ASK`, a wider intel-ask
  detector (`crate|where|which|send|share|intel|info|news|update|pass|check|
  location|have <determiner>`), so a real ask wrapped in a greeting still routes
  to the request-redirect lines.
- **AI-3 — seat-identical deterministic lines.** Pilot profiles run
  `social_generation: deterministic_only` from one shared bank, so JT and PM
  could render the identical string. `engines.seat_style`
  (`seat_text_style_v1`) styles bank/voice draws deterministically: JT lower
  case, PM sentence case — the rendered string can never be identical across
  seats and cannot introduce banned punctuation. Model text stays prompt-styled.
  Richer per-seat wording still needs a new bank version (spec §12 G6).
- **Version bump.** `VALIDATOR_VERSION` → `teammate_output_v4` (recorded on
  every `model_call.validation` and `displayed_output`).

New tests: punctuation/classifier/`seat_style` unit cases in
`tests/test_ux_round3_prompts.py`; integration
`test_concurrent_inflight_replies_cannot_deliver_the_same_line` (two
same-text replies in flight → second substituted + flagged) and
`test_deterministic_fallback_renders_pm_sentence_case` in
`tests/integration/test_ux_round3.py`.

**Evidence (post-E22 remediation).**
`docs/evidence/2026-10-03-ai-remediation/` — gate 13/13 PASS on source
`b2f2dea6…e093` (1,489 unit / 253 integration / 109 frontend / 98 contracts);
deployed with `/ready` 200, schema head `0006`. Development evidence only.

**[IRB bundle] items for the single amendment:**
- the required own pressure rating (UX-5)
- the natural-language-only interaction mode, if it is to be fielded (FR-1)
- the warmer teammate social copy and the per-seat voices (AI-2/AI-3)

### 17.7 Agent-decision register — O-1..O-4 implementation — 2026-10-03

The systematic AI-agent decisions inventory
(`INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_DECISIONS.md`) collected every explicit
and implicit agent behavior into AID-01..55 plus open items O-1..O-10. The
research team resolved O-1..O-4 as follows; all four are now implemented:

- **O-1 — `nl_only` is solvable (typed asks → formal cards).** Interaction mode
  changes *request modality only*: a typed ask naming a crate routes each
  responder seat into the same snapshot-pinned structured pipeline
  (`retrieval.seat_latest_for_crate` + `snapshot_for_seat_request`), so each
  teammate drops *its own* latest record card and the participant compares
  recency. A seat with no holdings answers with a deterministic grounded line
  ("nothing on crate {n} on my end"); assignment asks ("what are you working
  on?") get grounded per-seat crate lists from `teammate_assignments`.
  Locations stay inside the card — grounded typed lines carry crate numbers
  and availability only. A bare crate-numbered follow-up ("crate 3") after the
  `asks_intel_nl` prompt counts as the ask (BA-52). In `structured_and_nl`,
  typed intel asks get the deterministic picker-redirect line verbatim — no
  model call (BA-53) — preserving the structured-vs-NL measurement contrast.
  Delivered lines are `verbatim` payloads — exempt from repeat substitution.
- **O-2 — undirected text is heard by both seats.** `engines.addressed_seats`
  (pure function): an explicit `to` or seat-initial token addresses that seat;
  broadcast tokens ("both", "you two", "team", "anyone") or no address → both
  seats. Clock text is stripped so "6 pm" never addresses PM. Each responder
  opens its own `free_text` episode (per-seat audit), draws its delay
  independently on the `social_reply` class — natural staggering, never
  scripted alternation — and per-seat pending caps drop only the over-cap
  seat. Agent→agent chaining remains none.
- **O-3 — truthful automation disclosure.** `engines.is_automation_probe`
  intercepts identity probes before any model call — including plural and
  real-person formulations ("are the two of you humans?", "are you actual
  people?", BA-50). Each responder draws verbatim from the pinned two-line
  `identity_probe` pool ("i'm an automated account, not a person" / "i'm an
  automated teammate account") — a *pool*, not a single fixed line, so the
  two seats need not echo identical words (BA-59); both lines are truthful
  and minimal. The event `automation_probe` is logged; `automation.probes`
  counts occurrences and `automation.session_flagged` marks probe sessions —
  flag-not-drop; the exclusion rule is a pre-registration decision, not
  prototype behavior. Generated text is hard-blocked from claiming humanity
  (`human_claim` class — "i'm a real person", "not a bot", "we are humans",
  and denials/idioms like "i'm not automated", "we are not bots",
  "flesh and blood", "we're real" — BA-51, `teammate_output_v6`); the
  disclosure lines themselves pass by construction.
- **O-4 — delivered cards auto-file.** Every structured record delivery
  inserts a `file_membership` row (`added_from_team`) in the same commit —
  the manual `add_to_file` command and its UI button are removed (the command
  is absent from the stage-accepted sets, so a stale client receives
  `wrong_stage`). The file projection exposes `shared_by`,
  the *earliest* teammate sharer; `CaseFile` renders "Shared by JT/PM".
  Assistant snapshots include auto-filed cards automatically (they read
  `file_membership`). Practice mirrors the new behavior (no manual add).

**Content.** New `asks_intel_nl` and `identity_probe` pools in
`fallbacks.demo@v3` / `fallbacks.neutral@v3`; six successor profiles bind them
(`tp_demo_v5`, `pe_demo_v5`, `tp_review_first_v6`, `pe_pilot_v6`,
`tp_demo_nl_v2`, `pe_demo_nl_v2`) with reviewed diffs. `teammate_output_v6`
is the validator version (v6 widens `human_claim` to automation-denial and
idiom forms — BA-51).

**Measurement.** `seeking.typed_teammate_requests` (events `via: typed`),
`automation.probes`, `automation.session_flagged`; codebook `cb-1.3`.

**Tests.** `tests/test_o_decisions.py` (61 unit cases: routing, probes, asks,
grounded lines, human claims); `tests/integration/test_o_decisions.py`
(delivery per seat, broadcast, addressed, caps, probe logging, auto-add,
command removal) plus the `world_persistence` rewrite.

**[IRB bundle] additions:**
- `nl_only` typed-ask semantics (O-1) — if the arm is fielded
- both-seats responder broadcast (O-2)
- the identity-probe disclosure wording and the probe-session flag/exclusion
  rule (O-3 — flag-not-drop is prototype behavior; exclusion is a fielding
  decision to pre-register)
- auto-file on delivery + "Shared by" tag (O-4)

### 17.8 Plan-audit remediation — BA-50..BA-61 — 2026-10-03

The 2026-10-03 audit addendum (`INVESTIGATORS_TEAM_EXPERIMENT_PLAN_AUDIT.md`,
§17.1–17.7 against E23) recorded 12 findings; all are remediated on source
`541acc89…c523`, gate 13/13 PASS, deployed (`/ready` 200, schema `0006`).
Evidence: `docs/evidence/2026-10-03-ba-remediation/`.

- **BA-50/51 (high priority).** `is_automation_probe` now covers plural and
  real-person formulations ("are the two of you humans?", "are you actual
  people?", "is the team real?"); `human_claim` now blocks automation
  *denials* and idioms ("i'm not automated", "we are not bots", "flesh and
  blood", "we're real") — validator `teammate_output_v6`. A bare "real"
  probes only at clause end ("are you real quick" is safe).
- **BA-52/53.** A bare crate-numbered follow-up ("crate 3") counts as the
  `nl_only` ask the `asks_intel_nl` line invites (`_BARE_CRATE_ASK`); in
  structured modes a typed intel ask gets the deterministic picker-redirect
  verbatim — zero `model_call` rows.
- **BA-54/55/56 (surfaces).** Stale manual-add copy rewritten for auto-add;
  practice and the click-through mock show per-seat outcomes for crate asks
  (JT shares a card, PM reports nothing newer); the mock gains the
  `data-use` screen between debrief and completion.
- **BA-57/58.** The repeat gate normalizes hyphens ("re-check" ≡ "recheck");
  greeting-wrapped substantive asks fall to `default` lines, not
  pleasantries (`_SUBSTANTIVE_ASK`, extracted `_fallback_when`).
- **BA-59/60/61.** Disclosure is a per-seat draw from the pinned two-line
  `identity_probe` pool (documented semantics, unchanged behavior); the
  technical appendix now qualifies the repeat rule (verbatim lines exempt)
  and describes export boundaries (session-owned rows only;
  `participant_display`, `identity.*` and payment tables excluded —
  compensation linkage is restricted-role only); plan/evidence wording
  corrected (`wrong_stage`, `structured_record` delay class, v6) and the
  per-seat-cap drop and delay-class tests added.

**Not run:** the full browser matrix, the load test, and participant
sessions — this is a development gate and does not grant release approval.

**[IRB bundle]:** the disclosure-pool wording (two lines, per-seat draw)
joins the O-3 wording item; nothing else in this round changes the
amendment list.
## 18. Implementation log — 2026-10-04 (expert-review O-11/12/14/17/18/20)

The 2026-10-03 external review of the agent-decision register produced
O-11..O-22 (register §13). Four items were selected for implementation
before the lab round; the rest stay recorded for the amendment bundle.
Source `f33c26cb…7332`, gate 13/13 PASS, deployed `2026-10-04T01:18:17Z`
(`/ready` 200, schema `0006`, `gemma4:26b` resident ~12-month keep-alive).
Evidence: `docs/evidence/2026-10-04-agent-improvements/`,
`docs/evidence/2026-10-04-pool-sizing/`.

- **O-11/O-12 (classifier + routing).** `is_automation_probe` widened to
  third-person subjects ("is pm a robot", "are they real"), model names
  ("are you chatgpt", "is this gpt"), provenance ("who made you"),
  challenge-response ("type banana if you're human") and curly-apostrophe
  forms. Routing: the probe is disclosed by the seat it is about
  (`probed_seats`) — "jt, is pm a bot?" answers from PM; undirected probes
  disclose from both. `crate_mentioned` accepts number words, ordinals and
  `#N`; `followup_crate_mentioned` resolves bare-number asks only in pure
  follow-up position. Probe-adjacent non-fires flag `probe_near_miss` on
  `message_accepted` for review. Versioned adversarial corpus
  (`tests/adversarial_corpus.py`, 93 probe + 36 ask entries) asserts
  P=1.00/R=1.00 for both classifiers in `test_adversarial_classifiers.py`.
- **O-14 (social context).** `render_social` gains a bounded RECENT CHAT
  window — last `MAX_RECENT_TURNS=6` delivered team turns across all
  actors at `snapshot.visible_event_seq` (retry-identical prompts kept),
  card deliveries as `[shared a record card on crate N]` placeholders.
  `PROMPT_BUILDER_VERSION` → `grounded_prompts_v5`. The new integration
  test caught a pre-existing AID-55 violation: `seat_prior_lines` returned
  card-delivery bodies verbatim; both context blocks now placeholder
  cards, so record bodies never reach social prompts.
- **O-17 (pool wrap observability).** Pool audit
  (`docs/evidence/2026-10-04-pool-sizing/`): pools of 2–8 lines will
  exhaust at realistic volume; policy is record wraps, don't expand or
  silence. `voice_line` returns `VoiceDraw(draw_ordinal, pool_wrapped)`;
  `voice_line_committed` records both, and `message_delivered` records
  `repeated_line` — the authoritative at-commit repeat flag covering
  exhaustion repeats, card reshares and repeated disclosures.
- **O-18/O-20 (load + seed).** `derived_seed(session_id, job_id)` →
  31-bit, forwarded as the OpenAI `seed` (Ollama honors it) and recorded
  on `model_call.parameters` for every call including failures, alongside
  `concurrent_sessions`, `inflight_generate` and `queued_generate` — the
  load covariate for fallback-rate analysis.

**Not run:** the full browser matrix, the load test, and participant
sessions — this is a development gate and does not grant release approval.

**[IRB bundle]:** unchanged — post-disclosure posture/register, ITT
analysis policy, second-reply probability, gate budget, persona facts,
inbound safety, go/no-go thresholds and the believability check stay
recorded as O-9/O-11(remainder)/O-13/O-15/O-16/O-19/O-21/O-22.

## 19. Plan-audit remediation — BA-62..BA-75 — 2026-10-04

The 2026-10-04 audit update (`INVESTIGATORS_TEAM_EXPERIMENT_PLAN_AUDIT.md`,
§17.8/§18) recorded 14 findings; all are remediated after a residual-review
pass closed six leftovers and a measurement-doc polish corrected the
`automation.probes` codebook definition (cb-1.3). Final source
`c3cb11ba…9e47`, gate 13/13 PASS (1,625 unit / 285 integration /
137 frontend / 98 contracts; first pass `099758f9…59e2`
1,621/281/137/98; residual pass `16b49d76…3ce0` deployed 04:04:47Z),
deployed `2026-10-04T04:53:49Z` (`/ready` 200, schema `0006`,
`pinned_build: true` on both images, all containers healthy, `gemma4:26b`
resident 100% GPU). Evidence:
`docs/evidence/2026-10-04-ba62-75-remediation/`.

- **BA-75 (highest priority).** A recognized automation probe can no longer
  be blocked by either operational bound: probes bypass `_seat_over_cap`
  *and* the 120/min `rate_limited` check (classified in the dispatcher
  before the limiter runs — residual review fix), so a full queue or a
  saturated window can never refuse the truthful disclosure or eat the
  audit trail. The `automation_probe` event is always appended (with
  `probed` targets vs scheduled `seats`), and disclosure *scheduling* gets
  its own bounded budget — at most `MAX_PENDING_PROBE_DISCLOSURES=3`
  *unfinished* probe lines per seat, counting claimed-but-undelivered
  deliveries as well as pending (residual review fix). Beyond it the probe
  is still accepted, logged and flagged while no further line queues.
  Integration tests cover the full-cap disclosure, the saturated
  rate-limit bypass, the claimed-job budget, and the log-only path.
- **BA-62/63/64 (ask routing).** Seat-addressed bare asks classify and
  route correctly — "jt, crate 3" / "jt, #5 please" reach JT only
  (`_LEADING_SEAT_ADDR` normalization). Structured-mode typed asks in the
  newer forms get the pinned `asks_intel` picker-redirect (`force_when`,
  not a regenerated social line — "what about 3?" previously produced
  model text). First-person statements are suppressed
  (`_FIRST_PERSON_POSSESS`): "i have crate 3" / "we got nothing on crate 3"
  are statements, while "i have crate 3, do you?" stays an ask because the
  trailing question survives the clause strip. Residual review fix:
  `ask_crate_mentioned` resolves multi-clause messages from the *asking*
  clause — "i have crate 3. what about crate 4?" requests crate 4, not the
  first-mentioned 3 (applied to team asks and the assistant file-summary
  scoping).
- **BA-65/66 (probe routing).** Relational subjects resolve to the other
  seat — "jt, is your teammate a bot?" discloses from PM — and coordinated
  subjects hit both — "are you and pm bots?". A plural-safe direct branch
  catches "pm and you robots?" forms; corpus at 103 probe + 55 ask entries,
  P=1.00/R=1.00.
- **BA-67.** "beep boop" and similar automation-adjacent mockery flag
  `probe_near_miss` on `message_accepted` — intentionally a non-fire, now
  inspectable in exports and the walkthrough.
- **BA-68/69 (prompt context).** RECENT CHAT merges participant
  `record_shared` events — visible team-chat turns that never write a
  `message` row — rendered as the same crate-only placeholders, still
  bounded by `snapshot.visible_event_seq`. The OPEN ASKS block is filtered
  to requests the responding seat is actually part of
  (`requester == seat or responder == seat`); another seat's pending
  episode no longer leaks into the prompt.
- **BA-70/71 (load + error provenance).** `_model_load_covariates` is now
  honest about what it counts: `claimed_generate` (scheduler-claimed jobs,
  cross-worker via the DB) vs `provider_inflight_local` (the adapter's
  process-local in-flight counter — renamed in the residual review so the
  scope is in the name). Failure paths persist the effective request
  params — now including the fully-constructed request on the
  `unavailable`/`provider_not_allowed` pre-dispatch paths — the derived
  seed, a `dispatched` marker distinguishing pre-wire from on-wire
  failures (connection refusals and DNS failures report
  `dispatched=false`; resets, timeouts and HTTP errors stay `true`), and
  real measured latency.
- **BA-72/73 (practice + mock parity).** `frontend/src/state/practiceAsk.ts`
  mirrors the backend classifier subset (digits, number words, ordinals,
  `#N`, addressed asks, bare follow-ups, first-person suppression, clock
  text); practice shows per-crate outcomes, and the click-through mock
  uses the same matcher — verified under Node against the audit's 13-case
  list. Vitest coverage: `frontend/tests/practiceAsk.test.ts` (26 cases).
  Residual review fixes: the mock's Ask/Send handlers now resolve the
  chat list at screen scope (the practice screen nests it in a sibling
  section, so the old section-scoped lookup made the buttons dead), and a
  teammate-share reply that claims "the card lands in your case file"
  actually appends a mock file card and updates the count badge.
- **BA-74.** The decisions register's stale version references are
  corrected (`grounded_prompts_v5`, `fallbacks.demo@v3` /
  `fallbacks.neutral@v3` as bound).

**Not run:** the browser matrix, the load test, and participant sessions —
this is a development gate and does not grant release approval.

**[IRB bundle]:** unchanged — the probe-cap bypass removes a deployment
defect, not a design question; the amendment list stays as recorded.

## 20. Lab-walkthrough remediation — near-paraphrase replies + resizable workbench — 2026-10-04

Live test-session feedback (the 2026-10-04 walkthrough on `c3cb11ba…9e47`)
surfaced two participant-facing defects; both are remediated on source
`7fef9e06…f453` (gate 13/13 PASS — 1,633 unit / 287 integration / 138 frontend / 98 contracts; deployed `2026-10-04T15:36:19Z` — `/ready` 200, schema `0006`, `pinned_build: true` on both images, all four containers healthy, `gemma4:26b` resident 100% GPU). Evidence:
`docs/evidence/2026-10-04-lab-walkthrough/`.

- **Convergent teammate replies (believability tell).** One broadcast
  social message drew near-identical replies from both seats — "i'd have
  to check my notes. send me a request and i will pass along what i have"
  (JT) vs "i'd have to check. send me a request and i'll pass along what
  i have" (PM). Both lines were model-generated at temperature 0.2; the
  ≥3-word normalized-exact machinery (AID-36) could not see the
  near-paraphrase. `teammate_output_v7` adds `engines.is_near_repeat`: a
  line of ≥6 distinct words sharing ≥75% distinct-word Dice overlap with
  any delivered teammate line (either seat) is rejected at generation
  (`near_repeat` violation → deterministic fallback), rechecked at commit
  under the session lock (`_team_line_or_substitute` — the in-flight race
  where both replies validate before either delivers), and skipped inside
  `social_fallback`'s draw so substitutes are never near-duplicates
  either. Audit fields distinguish the two rules: `repeat_substituted` +
  `repeat_substituted_reason` ("exact"/"near") on `message_delivered`,
  and `near_repeat_line` for a near-paraphrase that still ships
  (verbatim-exempt lines, pool exhaustion). Verbatim payloads, record
  shares and scripted announcements stay exempt — their wording is
  semantic. Tests: the `is_near_repeat` parametrize block in
  `tests/test_ux_round3_prompts.py`; the concurrent near-paraphrase
  substitution and generation-gate tests in
  `tests/integration/test_ux_round3.py`.
- **Fixed workbench split.** The piloted two-row grid (case file 45% left
  spanning both rows; chat and assistant splitting the right column) was
  immovable, cramping both right-hand panes against a wide case file.
  `frontend/src/components/Workbench.tsx` now renders two accessible
  separators (`role="separator"` with aria orientation/valuenow,
  arrow-key + Home/End support, pointer-drag with pointer capture) that
  move the case-file↔right-column and chat↔assistant splits within 25–75%
  bounds, stored as `--sx`/`--sy` custom properties on `.task-grid`.
  Layout state is session-only (no persistence — a shared lab machine
  never leaks a previous participant's arrangement); below the 900 px
  breakpoint the panes stack without handles, unchanged. Vitest coverage
  in `frontend/tests/app.test.tsx` (keyboard, bounds, pointer-drag).

*Residual review (2026-10-04, audit BA-76/77):* the first-pass resize had two
defects the independent review caught. **BA-76** — the percentage tracks
summed to 100% *before* the 10px gap, overrunning the grid and leaving the
handles off the real boundaries; tracks now divide `100% − gap` and handles,
tracks and the pointer conversion share one `(W − 30px)` geometry.
**BA-77** — a second pointer on the same handle could move or terminate an
in-flight drag; `startDrag` now binds to the initiating `pointerId` and the
Vitest case interleaves an intruder pointer through a live drag.
Rendered-geometry verification (handle-on-boundary in a real browser at
25/45/75%) remains a browser-matrix item — jsdom cannot render the CSS.
Residual pass on source `664e64ac…c3f0` (gate 13/13 PASS — 1,633 unit /
287 integration / 138 frontend / 98 contracts; deployed
`2026-10-04T16:48:03Z` — `/ready` 200, schema `0006`, `pinned_build: true`,
all four containers healthy, `gemma4:26b` resident).

**Not run:** the browser matrix, the load test, and participant sessions —
this is a development gate and does not grant release approval.

**[IRB bundle]:** unchanged — both fixes are deployment defects/usability,
not design questions; the amendment list stays as recorded.

## 21. Agent-participant simulation harness — 2026-10-04

The researcher paused manual wording-variation testing to build an automated
participant layer first: human sessions keep finding comprehension/believability
gaps, while routing, timing, disclosure, and episode bookkeeping are better
verified by a bot that can run hundreds of seeded sessions. The split is recorded
in the behaviors register's new "Testing layers" section
(`INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_BEHAVIORS.md`) — simulation verifies
mechanisms; lab sessions verify experience. The harness's living reference —
usage, persona/invariant registries, safety contract, phase-2 scope, run log —
is `INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_HARNESS.md`.

**Implementation** (`backend/harness/`, `scripts/run_agent_sim.sh`):

- `simdb.py` — disposable-PostgreSQL provisioning: ephemeral container (tmpfs,
  random loopback port — same shape as `test_backend.sh`) or an operator DSN;
  either way the destructive guard must pass before bootstrap + migrate.
- `clockwork.py` — time drivers: `set_active` jumps the running interval;
  `tick` drives `scheduler.tick` rounds until quiescent.
- `view.py` — the bot's projection of what a participant can see (stage, chat,
  file, open scripted asks, open channels, greeting options).
- `runner.py` — enrollment + stage-spine driver + the core loop: virtual wall
  credit → per-job wall countdown → active-time advance → worker tick → persona
  decide → dispatch through `execute_command`/`lifecycle.handler_for`. The
  `complete` command uses its own `completion_handler` factory, as the API does.
- `personas.py` — 8 seeded personas: `normal`, `asker`, `prober`,
  `assistant_poker`, `idler` (check-in stall → `checkin_incomplete`), `sharer`
  (resolves scripted asks via shares), `chaotic` (stale revisions, double
  submits, malformed payloads, out-of-stage commands), `disconnecter`
  (mid-task silence → `resume`).
- `utterances.py` — pools seeded from observed participant phrasing + mutations
  (crate→create, `#N`, ordinals, ellipses, casing, filler prefixes, follow-ups)
  + optional Ollama paraphrase (`AGENT_SIM_OLLAMA_URL`, worker-netns only).
- `oracles.py` — always-on invariants (unprompted shares, teammate near-repeat
  Dice gate, probe-disclosure, seat attribution vs. `select_for_crate`,
  answer-key bound, episode bookkeeping, receipt-reason whitelist) plus
  persona-declared scenario expectations; canonical scripted lines are exempt
  from the believability gate but counted as conspicuous-repetition warnings.
- `report.py`/`cli.py` — per-run evidence under
  `docs/evidence/<date>-agent-sim/run-*/`: summary + per-session transcripts,
  command receipts, the final job ledger, and findings — enough to replay any
  scenario by seed.

**Verified** (`docs/evidence/2026-10-04-agent-sim/`): tp_demo_v5 × 8 personas,
tp_demo_nl_v2 × 3, stub-provider generation — all scenario expectations hold;
a `technical_stop/checkin_incomplete` idler, share-closed episodes, probe
disclosures, resume receipts, correct seat attribution, no orphaned episodes.

**Sim findings promoted to the register:** AB-16 (scripted assignment-reveal
template collides cross-seat as a near-paraphrase → SQ-9), AB-17 (outage-mode
ack-bank looping), AB-18 (natural probe phrasings → near-miss silence → SQ-10),
AB-19 (`technical_stop` leaves request episodes pending), and the AB-15 redirect
dead-end reproduced at scale (15× in one assistant_poker session).

**Phase 2 (not built):** HTTP-fidelity dispatch against the real participant API
endpoints, live-model runs inside the worker netns (`--provider ollama` — needs
`harness/` added to the backend Dockerfile COPY set; the worker image currently
ships only `app/`), and a load/soak profile. The observed `JobLeaseLost` log noise during batch ticks is
transient (no lost deliveries — final job states verified); flagged for a future
audit question if it appears in live worker logs.

**BA-78–BA-89 remediation (same day, bundle `7c059d17…`):** the §21 audit found
the harness weaker than its claims and all twelve items are fixed —
BA-78/86/87 harden provisioning (real opt-in/APP_MODE/live-env-file inputs to
`assert_disposable`, fail-closed without a live reference, container cleanup on
any post-spawn failure, no unused container for `--dsn`); BA-79/80 correct the
participant projection (session-scoped record join, participant-visible file
fields only — answers now derive from hideouts named in visible bodies or the
public picker list, not hidden `location_id`); BA-81/82/83 tighten the oracles
(`expect_reject` acceptance → fail finding, per-message probe→event matching,
per-request structured-delivery correlation with an honest nl_only
not-applicable); BA-84 makes worlds replayable via a live-refused
`enter(seed_hex=…)` hook; BA-85 makes `--provider ollama` fail loudly on
non-live resolution and stamps requested+effective provider in `summary.md`;
BA-88 makes run dirs collision-proof; BA-89 regenerated the stale per-build
docs. The audit also caught a latent projection bug (`interaction_mode` read as
an object — `structured_requests` was always True on nl_only) and the
`assistant:no_answer_key` no-op token was dropped (INV-ASSISTANT-BOUNDED covers
it always-on). Post-fix batch (`run-20261004T213722Z`): all scenario
expectations hold under the tightened checks; the only fail is the documented
AB-16 grounded-assignment collision. Harness unit coverage:
`backend/tests/test_agent_sim_harness.py` (17 tests).

**SQ-6–SQ-11 / AB-18–AB-21 rulings + remediation (same day, bundle `d59c8b02…`):** the RL
ruled on the lab-session findings and all are within the existing IRB protocol.
Check-in overlay now carries group-norm copy + a live 60 s countdown (real cap
stays 120 s) and the technical-stop screen names `checkin_incomplete`; the
assistant answers contents→crate questions deterministically off the fixed
`CRATE_CONTENTS` map (no locations — answer-key gate untouched, new
`deterministic_contents_summary` provenance tag → **migration
`0007_contents_fallback`**, deploy must run `scripts/migrate.sh upgrade`);
grounded assignment statements use per-seat frames + re-ask "still" variants;
SQ-10 closed by design (narrow probe boundary kept); pickers/ask dropdowns
label "Crate n — item" via a new `crate_contents` workbench field; terminal
paths close open episodes `session_abandoned` (AB-19); the submit bar is
slimmed to one row (AB-20). Harness verification batch
`run-20261004T223959Z` — all 5 personas, **0 fails** — after the run itself
caught the missing fallback enum (→ 0007) and same-seat verbatim repeats (→
re-ask frames). Oracles updated: `INV-NEAR-REPEAT` cross-seat=fail /
same-seat=warn; `INV-ASSISTANT-BOUNDED` guards crate+location dumps only.

## 22. Implementation log — 2026-10-12 (v10 pilot closeout, AI-only fielding, dashboard/exports, UX_Feedback_v9 → tp_ai_v12)

**Fielding transitions.** `study.live.v9` (student N=5 Connect pilot, `tp_ai_v11`/`tp_scripted_v13`) closed; `study.live.v10` opened and **closed the same day** for the N=4 Connect pilot. The fielded design is now **1×2 AI teammates × pressure** (AID-63): the `tp_scripted_v13` arm was closed without intake, and `SCRIPTED_TEAMMATES_ENABLED=false` disables scripted teammates in the live deployment. The N=30 effect-size/power cohort and the N=200+ preregistered study carry **no AI-vs-scripted contrast**. Scripted code/profiles remain in the repo for comparison, QA and historical reconciliation only.

**v10 pilot results (post-launch evidence: `docs/evidence/2026-10-12-postlaunch-v10-pilot/`).** Six live entries, all real Connect PIDs: 4 completers (`b0466488` low, `ce24dca8` low, `907deaf6` high, `fdc8c38a` high), 1 payable `technical_stop`/`checkin_incomplete`, 1 `abandoned`/`heartbeat_timeout`. Mechanical verification was clean: request schedule ~45/100/155/210/245 s, check-in 60 s, pressure remarks ~75/83 s arm-correct, warning 240 s, deadline ~300.3 s; allocation 3 high/3 low; all required items answered with zero skipped; all 4 chose `use_all`; receipts and 5 pending entitlements clean. Teammate generation: 11/12 replies model-generated, all verdicts `supported`, zero leaks — one sanctioned `deterministic_script` ack fallback. Two observations for N=30 planning: **low accuracy variance** (scores 0/4, 1/4, 2/4, 1/4) and an **engagement floor effect** (one completer never triggered a teammate reply and never answered one assigned crate, which also removed that crate's confidence item under the per-answered-crate contract). Manipulation check direction was correct on n=4 (`tp_post_11` high 5,7 vs low 3,3); believability `tp_post_10` split 2 human / 1 automated / 1 mixed. The abandoned participant's trail showed **no server error** — she completed the typed ask twice but never found the Share action, so practice Continue stayed gated; that directly motivated UX_Feedback_v9 item 3.

**Researcher tooling (deployed `b165395b`, `036fada2`).** Dashboard gained a **version dropdown** (`batch_group_id`) defaulting to the newest `study.live.*` group with an "All" option, URL-persisted, applied across all tabs via the shared cohort contract (`session.batch_group_id`); Sessions-column headers gained plain-English hover explanations (tooltips merged into `FilterTh` filter hints).

**Export changes (deployed `3fc50bf2`, `f993b365`).** Per owner direction, IRB does not require the redaction workflow: `item_response` open-ended answers now export **verbatim** for `use_all` sessions (was `withheld_unreviewed`); the **Redaction admin tab was removed** (backend redaction tables/endpoints remain for historical/backend compatibility but are no longer a researcher workflow); a synthesized **`chat_text`** export card on Dashboard → Export combines participant `message` bodies, `model_call` I/O and `event` payloads into one chronological CSV with no withheld markers — consent gating preserved (non-`use_all` sessions excluded). Live-verified on v10: 1,215 rows / 4 sessions, 83 messages, 19+19 model-call rows, 1,094 events, zero withheld.

**UX_Feedback_v9 → `tp_ai_v12` (deployed `9ae1c609`).** Three participant-facing fixes from `UX_Feedback_v9.pdf`: (1) bolded final bullet on "Your workspace" ("All intelligence is accurate as of the time it was recorded… where the crate is now"); (2) "Remember: Your goal is to identify the current location…" paragraph on the connecting screen; (3) **practice completion now requires Share alone** — the typed ask remains as optional practice (invalid text still rejected `practice_ask_invalid`; valid text returns the canned card), and "will answer" → "may answer" copy. This required a new bound artifact: **`training.reference@v6`** → new manifest **`tp_ai_v12`** (all other pins identical to `tp_ai_v11`). Verified on the deployed build through the preview path: a fresh signed-link session advanced consent → instructions → Share-only → comprehension → `scripted_connecting` with **zero asks** (high arm), and a second session verified the optional ask still returns the canned card (low arm). The 8 playable demo mocks were regenerated with the same copy/gate. `tp_ai_v12` is **preview/QA material only** — no fielded batch binds it; the admin Links panel auto-selects it for self-minted preview links. Testing posture remains admin-issued signed preview links; nothing was launched or enrolled.

**Gates for the v12 line:** ruff/mypy clean; 1,907 backend unit + 121 integration tests; 178 frontend tests; `manifests check` green; demo `--check` green; deployed `9ae1c609` live and healthy.

## 23. Implementation log — 2026-10-14 post-crate-overlap audit remediation

**Scope and authority.** The audit compared the recovered crate-overlap baseline
`ded9a5c2…` with deployed `26f341219e…`, including the already-fixed closed
Q&A projection connection and smoke shared-flag behavior. The owner requested all
identified code remediations and this plan/status update, but explicitly declined
live-intake closure: **“Leave it open; fix code only.”** No live enrollment or
feature-flag change was made. The existing exposure is accepted rather than
reported as resolved.

| Finding | Resolution |
|---|---|
| 1. Open live intake receives internal-testing flags (P1) | **Owner-accepted residual.** `study.live.v11` remains open. Time pressure/assistant remain off and Q&A remains on; nominal high/low arms alone do not prove manipulation delivery. |
| 2. Neutral review/check-in rescue invisible to the browser (P1) | **Fixed.** Both paths append the existing participant-visible `stage_proceeded` event with `to="task"`; browser stage/projection updates no longer rely on dropped internal events. |
| 3. Mixed cells sort `None` against an integer (P1) | **Fixed.** Missing budgets normalize to `None`; the single-cell gate uses a deterministic null-safe sort. |
| 4. `condition_spec.cells` permits unimplemented pooling (P1) | **Fixed by refusal.** Mixed overlap/count/budget cells are rejected even with a declaration. Existing formulas/simulations remain single-cell; no new confirmatory model was improvised. |
| 5. Pressure-on → off before the review boundary strands `solo_review` (P2) | **Fixed.** Suppressed legacy check-in jobs end review at the due active-time boundary; deferred jobs remain deferred. |
| 6. Q&A bypasses the consent-pinned model/build (P2) | **Fixed.** Live Q&A checks the consent generation identity and session build before dispatch. Mismatch yields `generation_policy_mismatch` fallback without a provider call; older jobs recover their pin from the consent event. |
| 7. Q&A loses prompt/effective-parameter provenance (P2) | **Fixed.** `generation_audit` JSONB stores prompt ID/hash/messages, effective parameters, timeout, flags, expected identity and dispatch status. Provider-returned parameters survive unusable completions. Replay/research export expose the audit; the existing non-`use_all` table drop also protects its question text. |
| 8. Failed answer projection leaves “Thinking…” indefinitely (P2) | **Fixed.** One coalesced 2.5-second retry runs even with idle SSE/empty polls. Success, disposal and session replacement cancel it; old-session failures cannot restart it. |
| 9. Early smoke failure reads uninitialized `qa_outcome` (P2) | **Fixed.** Outcome state is initialized before enrollment/entry, so those failures retain their original error and produce failure JSON. |
| 10. Smoke closure failure still reports PASS (P2) | **Fixed.** Closure retries up to three times and verifies the enrollment response. Exhausted or unconfirmed cleanup forces FAIL/nonzero exit; evidence records `enrollment_closed`. |
| 11. Rapid mock questions overwrite the same answer (P2) | **Fixed.** Each delayed callback captures its own exchange node; reversed completion order is regression-tested. All 320 generated mock files are current. |
| 12. Declared Q&A 20-second timeout is unused (P3) | **Fixed.** Accepted jobs carry `qa.TIMEOUT_MS`; the generation lane also uses it for legacy Q&A jobs. Actual provider calls receive 20 seconds, recorded in the audit. |
| Residual S01. Replay task shape null when binding is unavailable | **Fixed.** Persisted world content supplies task shape without a bound manifest. Disposable-DB regression verifies the 12-crate/no-overlap shape with no ContentStore. |

**Validation.** `scripts/check.sh --log docs/evidence/2026-10-14-qa-audit-remediation`
passed **all 14 steps**: Ruff format/lint, strict mypy, **2,048 units**, **425
integrations** on four disposable PostgreSQL containers, 42 profiles × 2 arms,
frontend typecheck/lint/**250 tests**/production build, leakage scan, **122
contracts**, generated clickthrough/technical appendix/**320 mocks**, Compose and
port checks. The Chromium check is the existing blank-page tool smoke, **not**
a complete participant-browser E2E certification. New regressions cover identity
mismatch, legacy job pins, invalid-completion provenance, pressure flips, replay
without binding, idle/empty-poll retry with session fencing, rapid mock callbacks,
nullable analysis cells/spec bypass, and smoke setup/cleanup failures.

**Deployment.** Stamped `scripts/build_images.sh` build time
**2026-10-14T16:45:38Z**; preserved pre-remediation backend/UI rollback tags.
The immediate pre-deploy read found **zero active real live sessions** and open
`study.live.v11`. Applied additive migration `0013_qa_provenance` **before**
recreating API/worker/UI, avoiding the previous missing-column window. The
Ollama namespace/model was not recreated. API/UI `/version`, the worker stamp,
`/ready`, actual Alembic head and all three health checks agree. Migration leaves
old exchanges with `{}` audit data; it does not fabricate historical provenance.

**Deployed mutating smoke.** `TEST_QA_AIQA_51C8A2B6`, session `8dc98e48…`,
on `study.smoke.v11` committed consent, practice/comprehension, `qa_ask`,
Continue and ready, reaching **task** through **ai_qa**. Q&A status **answered**,
`local_openai_chat`, digest-pinned `gemma4:26b`, **1,173 ms**. Persisted audit:
`ai_qa.rules@v1`, 2 prompt messages with SHA-256, model pin/served digest,
temperature 0.2, max_tokens 300, reasoning_effort none, derived seed,
`timeout_ms=20000`, and the deployed consent-build identity. The smoke group
was confirmed **closed**; the live group remained **open**. Post-deploy v11
counts were unchanged: 34 completions, 4 abandoned, 2 declined, no active
live sessions at verification. This smoke stops at task; it does not claim a new
paid completion or a full real-browser study walkthrough.

**Evidence and remaining work.** Exact build-source manifest, supplemental script
checksums, full gate/build/migration/recreate logs, deployed versions, pre/post
state and smoke JSON are indexed in
`docs/evidence/2026-10-14-qa-audit-remediation/README.md`. Unsupported
mixed-condition models, research decisions, formal acceptance/ops gates and
first-post-remediation real-participant checks remain separate work. This code
release neither silently restores the pressure manipulation nor closes the
owner-accepted intake exposure.

## 24. Implementation log — 2026-10-14 neutral-arm audit remediation

**Scope.** The follow-up audit of neutral allocation build `235dc703…`
verified three defects despite its passing release gate: reused allocation
slots after arm-count changes, hidden historical balance cells, and
`nopress` sessions receiving high-pressure treatment after a global re-enable.
The owner requested full remediation. Live intake and flags stay unchanged.

**Implemented fixes.**

| Finding | Resolution |
|---|---|
| Allocation collisions (P1) | Under the existing batch/group lock, continue only a contiguous last block matching the current deterministic cell order. Otherwise start at the maximum historical block index plus one, slot zero. Historical reservations never change; complete current blocks remain balanced. |
| Historical balance omitted (P2) | Return observed historical cells alongside current allocatable cells, with `allocatable` markers. Counts sum to the total. Admin labels historical cells and does not treat them as current fielded cells. |
| Neutral treatment changes after re-enable (P2) | Resolve a neutral policy preserving `arm=nopress` and no pressure payload/check-in/warnings/remarks. Effective pressure requires both the flag and non-neutral identity across scheduling/dispatch, commands/completeness, wire/instruments, Q&A prompt/audit and reporting fallback. Actual historical exposure remains evidence. |

**Validation/deployment status:** full release gate **14/14 PASS** on
`c20e12db75368a90bcaffac40b723aa756f935e6989bb0468a70fbe56127fb53`;
deployed; build time **2026-10-14T18:33:03Z**, verification **18:35Z**.
Strict mypy/Ruff, 2,059 units, 436 disposable-DB
integrations, 251 frontend tests, 122 contracts, 42 profiles × 2 arms and
400 current mocks pass. Regressions include repeated one/two-arm changes in both
directions, partial blocks, immutable reservations, historical cell totals,
neutral completion after re-enable in both task variants, wire/command/item/
readiness projections, pressure-job suppression and Q&A prompt/provenance.
No migration or historical rewrite is required; schema stays
`0014_nopress_arm`. Evidence:
`docs/evidence/2026-10-14-nopress-audit-remediation/README.md`.

**Deployment verification.** Preserved rollback backend/UI tags; the immediate
pre-deploy read found zero active real sessions and unchanged live intake/flags.
Recreated API/worker/UI only, leaving Ollama/model/ingress untouched. Build
stamps and readiness agree with actual schema `0014_nopress_arm`.
`TEST_QA_AIQA_EC3193F7` (`a3b55c21…`) reached task through Q&A, with both
session/allocation `nopress` at block 7/slot 0. Gemma answered in **1,085 ms**;
audit pressure=false, no pressure jobs, replay pressure=false even when
evaluated with an enabled flag. The deployed neutral policy probe also
confirms re-enable cannot add treatment. HTTP admin balance reconciles
v11's **38** and v10's **6** historical allocations. Smoke intake is closed;
live remains open with 34 completed/4 abandoned/2 declined and no active real
sessions at verification. The smoke stops at task; full completions and flag
flips were tested only on disposable databases, not by changing live flags.

The accepted open-intake exposure and outstanding research/ops work in
§3.1/§23 remain unchanged. This remediation neither enables pressure nor
authorizes additional recruitment.
