# Investigators team experiment — infrastructure and study design specification

**Date:** 2026-09-27  
**Home:** `./`  
**Status:** Consolidated authoritative design specification; incorporates the hidden-bench and Triad timing evaluations. The platform is deployed and a non-confirmatory N=5 time-pressure pilot is fielded on `tp_review_first_v10` (see **Current build** below). G1–G10 are not closed by that fielding. “Required” remains the target design, not a claim that every gate has been demonstrated.  
**Scope:** One shared experimental platform supporting the **teammate time-pressure** and **peer-evaluation** crate studies, with reproducible legacy-demo profiles and separately versioned next-pilot profiles.

**Authority:** This document is the single source of truth for Investigators implementation requirements, proposed study profiles, measurement contracts, and acceptance gates. The two insights reports are historical supporting evaluations; their recommendation lists are superseded by this consolidation. Approved research materials govern participant-facing procedures, and unresolved decisions remain explicit in §13. “Required” below describes the target design, not a claim that the infrastructure already implements it.

**Revision 2026-09-28 (plan-audit remediation):** Applies the spec-level findings of the [implementation plan audit](./INVESTIGATORS_TEAM_EXPERIMENT_PLAN_AUDIT.md): participant/admin routes renamed from `/team/` to **`/investigators/`** to avoid confusion with `/team/` on neighbor-study.local; loopback ports 8518 (UI) / 8519 (API) allocated after inventory; preview and acceptance coverage stated as **eight** profile/arm combinations; the demos' “One minute left.” warning at active second 300 made an explicit versioned contract; rating-seat independence (§7.1 invariant 7); reference-profile timing mechanisms (§6.5); timing-dimension conformance matrix, AI referral and presence-cue policy (§6.6); technical-incident policy (§6.1, §13); pre-launch usability evidence (§13); post-pilot semantic held-out corpus (§12.2); canonical `SYSTEM_MAP.md` target (§11). No manipulation, model, deployment or recruitment is authorized by this revision.

**Owner decisions, 2026-09-28 (later the same day):**
- Workspace directory moved from `./estate/team/` to `./`.
- Admin allowlist confirmed: `admin1@university.edu` and `admin2@university.edu`.
- Reference-profile timing mechanisms (§6.5) confirmed.
- Model: S5's “Gemma 3” is a typo. The [University] OIT security review covered Gemma 4 and approved backup-host for local LLM use regardless of model; a single operator runs the host (§7.4, §13). D05 now covers only the exact model/digest and GPU capacity.
- No one-off secret `.incl` file. The admin password is `ADMIN_PASSWORD` in `./.env`, as for `/pcs/` and `/dct/`.
- Ingress applied to `/etc/httpd` and reloaded; `/investigators` returns 301 and the other paths 503 until the service exists.
- Ingress uses the `/pcs/`–`/dct/` layout: Shibboleth admin blocks, then a public block for the whole `/investigators/` path. As a result, admin authentication is enforced by the application, not Apache (§3.1, §11).

**IRB amendment, approved 2026-10-01 (protocol XXXX-XXXX; check-in 09/09/2027):** Two amendment items were approved. (1) The large language model powering both the agent teammates and the AI assistant is **Gemma 4 (or the latest released Gemma as versions become available), self-hosted on the school research server backup-host via Ollama**, with SHA-256-verified frozen weights and per-message model/version logging; participant text never leaves the school research server in normal operation, and no participant text trains any model. The sole authorized off-host path is a **university AI service congestion fallback** when the GPU is overwhelmed; the same frozen prompts, classifier and audit logging then apply. (2) Generated — not scripted — teammate and assistant contributions are approved under the stated controls: frozen task-constraining system prompts including off-task redirection, an automated classifier screening every generated message before display, and full audit logging; the task is fictional with no personal/sensitive content, and the one foreseeable discomfort is the debrief disclosure that teammates were automated. Consequences recorded in §§3.2, 7.4, 13: D05 is resolved; the deployed `gemma4:26b` conforms (switched from `gemma4:12b` 2026-10-03); third-party lab adapters are not the approved fallback; and the social prompt must include the approved off-task redirect rule at its next content-review point.

**Revision 2026-10-07 (current build):** Records the deployed services, the fielded `tp_review_first_v10` contract, and the decisions selected for that pilot (D01, D02, D04, D06, D07, D10, D13). It does not authorize expansion, peer-evaluation recruitment, Phase B inference, or a claim that G1–G10 have passed. The register for those decisions is `docs/DECISIONS.md`; this revision copies the selections into the design so the spec no longer calls them open proposals.

**Consolidation scope:** Adds evidence and claim lineage, per-call knowledge snapshots, agent-policy conformance, validated offline coding, executable timing policies, recipient exposure records, and stronger release evidence. It preserves the two studies, their selected-profile decisions, the six-minute active budget, and all existing protocol reconciliation items. No new timing manipulation, model/provider, deployment, or recruitment is authorized by consolidation.

## Current build (2026-10-07)

This section is the as-built description of source bundle `fac6f809083186f62eb40508234238507921b9dc605d26059605b0e6d6bf3897`. On 2026-10-07, loopback `GET /version` returned that checksum with `pinned_build: true`, `build_time: 2026-10-07T13:16:55Z`, `app_mode: live`, image `investigators-backend:local`, and schema head `0007_contents_fallback`. `GET /ready` was 200, with schema current equal to that head. This file is outside that bundle (`scripts/source_bundle.py` hashes `backend/`, `frontend/`, `content/`, and `docker-compose.yml` only), so editing the spec does not by itself retag the running image.

### Services and ingress

Compose runs five services, all bound so the host proxy is the only public path:

| Service | Role |
|---|---|
| `investigators-ui` | React/TypeScript/Vite static UI on `127.0.0.1:8518` (container port 8080). Base path `/investigators/`. |
| `investigators-api` | FastAPI on `127.0.0.1:8519` (container port 8000). External prefix `/investigators/api`; Apache strips it once. |
| `investigators-ollama` | Digest-pinned Ollama image. `OLLAMA_HOST=127.0.0.1:11434` inside its network namespace. The model port is not published on the host. NVIDIA runtime. |
| `investigators-ollama-warmup` | One-shot sidecar, `network_mode: service:investigators-ollama`, runs `gemma4:26b` at stack start so the first participant call is not a cold load. |
| `investigators-worker` | `python -m app.workers.main`, same network namespace as Ollama, so the approved model URL is literal loopback. The API does not join that namespace. |

PostgreSQL is authoritative, with schemas `app` (research runtime) and `identity` (platform linkage; not part of the research export). The API container root filesystem is read-only. The only sanctioned export write is the host directory `./local-data/exports` mounted at `/srv/exports`. Researcher docs are a read-only mount of the repo at `/srv/docs`, with `.env` masked.

The participant URL is `https://investigators.local/investigators/`. Admin UI and API are application-authenticated (`ADMIN_PASSWORD`, signed `inv_admin` cookie, `SameSite=Strict`, CSRF on mutations). Apache still declares Shibboleth blocks and then overrides them with the public `/investigators/` location, so forwarded identity headers are stripped and ignored. An empty admin password fails closed.

The participant client uses SSE (`GET /events/stream`) when `EventSource` exists and polls `GET /events` otherwise. Commands are `POST /commands` under a per-session writer lease (`x-writer-id`). WebSockets are not the transport.

### Fielded profile

> **2026-10-10 annotation:** this subsection records the 2026-10-07 review-first
> fielding (`tp_review_first_v10`, N=5 pilot, complete). The current fielded
> line is the 2 × 2 nl_only launch: batch **group** `study.live.v8` —
> `tp_ai_v7` / `tp_scripted_v9` × `high`/`low`, permuted blocks of four at
> consent, member batches closed — with `consent.reference@v7` (~12 min,
> $3.00 + a $0.50 bonus offer paid to every completer), 300 s active budget,
> check-in at active 60, requests at 45/130/210,
> pressure remarks at 75/83, warning at 240, all measurement items enforced,
> and `prompt.assistant_demo@v4` on the verbatim-excerpt contract
> (`assistant_output_claims_v5`, `grounded_prompts_v7`). `study.live.v7` ran the
> second pilot block of four on 2026-10-10 under consent v6 / $3.00;
> `study.live.v5` opened the second
> pilot 2026-10-09 and was paused when the assistant-rejection incident
> surfaced; `study.live.v6` was minted without opening.
> **2026-10-11 annotation:** the current pair `tp_ai_v11`/`tp_scripted_v13`
> (consent v8, `post_task.tp_demo@v5`, seat_plan request schedule, `open_ended`
> stage — AID-59–AID-62) is bound by `study.live.v9`/`study.smoke.v9`, opened
> 2026-10-11 for the N=5 Connect pilot (`study.live.v8` closed the
> same day — it had been left open on the superseded pair). Operational
> detail lives in
> `INVESTIGATORS_TEAM_EXPERIMENT_PROLIFIC_CONNECT.md` and
> `PRE-LAUNCH_CHECKLIST.md`; the bullets below describe the v10 fielding as
>
> **2026-10-12 annotation (current):** `study.live.v9` closed with its N=5
> post-launch audit recorded; `study.live.v10` opened and closed the same
> day — **AI-only 1×2** (`tp_ai_v11` × high/low, N=4 completers,
> `SCRIPTED_TEAMMATES_ENABLED=0`). There is no open fielding round: posture is
> preview links only. Current preview/testing manifest is `tp_ai_v13`
> (artifact-bound `open_ended.tp_followup@v1`, BA-105; `tp_ai_v12` bound
> `training.reference@v6` share-only practice). No live batch binds v12/v13.
> The scripted family remains in tree behind the flag for QA/comparison.
> fielded on 2026-10-07 and are retained verbatim.

Live enrollment is one batch. `prolific.live.n5.v10` binds published manifest `tp_review_first_v10` (`material_status: approved`, modes `preview` / `qa` / `live`). The earlier batch `prolific.live.n5` is closed; it is pinned to `tp_review_first_v9` and is not rewritten. The pilot is N=5 and is the pre-launch usability round (D13). It is not a confirmatory analysis (D12 is open). Entry is the platform link in `INVESTIGATORS_TEAM_EXPERIMENT_PROLIFIC_CONNECT.md`.

`tp_review_first_v10` is the live successor of the §4.1 `tp_review_first_v2` proposal. The bound contract is:

- Study `investigators_time_pressure`, arms `high` / `low`, permuted blocks of 2 within study × batch.
- Task: 8 crates, 6 hideouts, 4 assigned, 2 own-file, 2 team-dependent, generator `investigators.world/demo_v1`.
- Active budget 360 s. Solo review occupies active 0–60 s with only file work and answer drafting. Check-in at active 60 s (3.5 s collection, explicit resume, 120 s wall inactivity cap, outcome `technical_stop/checkin_incomplete`). Own pressure rating is skippable. Channels from active 60–360 s add structured seeking, sharing, social reply, and assistant prompting. No early submission. One global answer freeze.
- Displayed teammate pressure: high 6 and 7, low 2 and 3. The higher value is assigned to JT or PM by an independent fair coin (`rng_stream: ratings`).
- Scripted requests at active 80 / 180 / 270 (`requests.demo` v3), targets unassigned / assigned / unassigned, seat policy `coin_majority_shuffle`.
- Every response class uses mechanism `concurrent` with clamped lognormal delays (structured-record median 8.5 s, social-reply median 7 s, assistant median 3.0 s). Social replies time out at 25 s and fall back to a deterministic script. Assistant calls time out at 20 s and fall back to a deterministic file summary. Over-cap concurrency is a visible rejection (4 pending requests per seat, 2 pending assistant prompts).
- Warning `warning.one_minute_left` v2 at active second 300. Wording and low-time styling match v1; v2 exists because v1's bytes are frozen in earlier published bundles and the live gate admits only `approved` artifacts.
- Teammates: `social_generation: model_with_validated_fallback`, `prompt.social_demo` v3, `voices.tp_demo` v2, `fallbacks.neutral` v5. Acknowledgments, crosstalk, assistant referral, unsolicited requests, and proactive sharing are off (`none` / null).
- Assistant: provider `local_openai_chat/gemma4:26b@sha256:5571076f3d70050487b26b341705799e0ab29b808164f90d20d4cf84f699d251`, prompt `prompt.assistant_demo` v2, validator `teammate_output_v7`, source access `participant_file_at_accepted_request`. Prompt assembly version in code is `grounded_prompts_v5`.
- Instruments bound for this profile: `consent.reference` v4, `training.reference` v2, `confidence.demo` v2, `post_task.tp_demo` v3, `debrief.tp_reference` v2, `checkin.pressure_demo` v2. No peer-rating instrument.
- Outcomes: `payment.s5_base_only` v3 ($4.00 base, no bonus, activatable, `technical_stop` base-paid). `withdrawal_payment: forfeits_base`. Completion statuses are `completed`, `declined`, `withdrawn`, `technical_stop`, `abandoned`. Ratings-contingent pay is off.
- Interaction mode `structured_and_nl`.

Contents questions stay on the deterministic retrieval path even when social generation is the model path. A generated line that claims the teammate is human is a validator failure (`human_claim`) and is not displayed. The assistant persona still tells the model not to mention being an AI; the participant-facing disclosure of automated teammates is a debrief matter, and `debrief.tp_reference` v2 is a pointer at the demo debrief rather than a full restatement of that sentence.

### Lifecycle, workbench, and commands

The stage graph in `backend/app/domain/stage_graph.py` is the §5.2 machine: `entry → consent → instructions → training → scripted_connecting → ready`, then `solo_review` for review-first (or `task` for baseline TP and for PE), TP check-in stages, `answers_locked → confidence → peer_ratings` (PE only) `→ post_questionnaire → open_ended` (TP only, 2026-10-11: two sequential free-text follow-ups, code-defined per AID-61) `→ debrief → data_use_choice → completion`, plus `declined`, `withdrawn`, `technical_stop`, and `abandoned`.

Training uses a practice world. Continue is refused with `practice_share_required` until one practice share is stored. The practice page shows team chat and the assistant as labelled previews; it does not make the participant exercise every task control.

Accepted command kinds are `consent`, `set_initials`, `training_step`, `ready`, `withdraw`, `answer_update`, `pressure_response`, `pressure_skip`, `item_response`, `item_skip`, `debrief_ack`, `proceed`, `data_use_choice`, `resume`, `takeover`, `teammate_request`, `record_share`, `social_reply`, `assistant_request`, and `greeting`. `add_to_file` is still named and is rejected (`add_to_file_removed`). Teammate-delivered records are filed automatically (`added_from_team`). The participant reorders the case file; there is no sort control. Greeting choices exist only for the first 30 active seconds of task chat.

The workbench is the two-row grid in §10 (case file, team chat, assistant, answer bar), with session-only separators in the 25–75% range and a stacked layout below 900 px. Display initials are collected on the training background page and stored in `app.participant_display`, which is excluded from research exports.

The post-debrief data-use screen shows contact information and records `use_all`. It does not offer an in-app deletion choice. Whether a later withdrawal route is still owed is the open part of D09. Payment does not depend on that choice. Under the fielded rule, `completed` and `technical_stop` are owed the $4.00 base; `abandoned`, `withdrawn`, and `declined` are not.

Post-task pages have no per-item Skip button. Submitting a page records `item_skip` for each untouched item. The check-in own-pressure rating is separate and is skippable on `tp_review_first_v10`, so a check-in timeout is not an unpaid “failure to answer.”

### Profiles that exist but are not the live batch

`content/profiles/` also holds `tp_demo_v1`–`v6`, `tp_review_first_v2`–`v9`, `pe_demo_v1`–`v6`, `pe_pilot_v2`–`v7`, and `*_nl_*` variants. `tp_demo_v1` still executes the demo timing mechanisms (`demo_structured_delay_length`, `demo_social_additive`, `demo_assistant_floor`) and allows only `preview` and `qa`. Its social-generation field is `model_with_validated_fallback`; acknowledgments and crosstalk are null; the assistant provider id is still the placeholder `univ_local_unresolved_d05`. `pe_demo_v1` uses the same timing mechanisms and the same preview/qa limit, and it still carries the original acknowledgement and crosstalk probabilities even though the revised S3 HTML dropped them. Its social-generation field is also `model_with_validated_fallback`, and its assistant provider id is the same placeholder. Those files are references or superseded drafts. They are not what `prolific.live.n5.v10` runs. Paid peer-evaluation recruitment stays behind the D03 residual.

`nl_only` is a legal interaction mode and forbids structured seeking. The fielded profile is `structured_and_nl`, not `nl_only`.

### Admin, measurement, and exports

Researcher, operator, and compensation are separate admin roles. The researcher nav includes a `dashboard` tab to the right of `documentation`, with nine descriptive sections (overview, exports, readiness, timing, instruments, item analytics, power inputs, arm contrasts, findings). Those GETs are researcher-only, `Cache-Control: no-store`, and one `REPEATABLE READ READ ONLY` snapshot. Inference returns `analysis_not_configured` and minimum detectable effects return `unsupported_model`. Phase B stays behind D12, D14, and O-13.

Study export writes formula-safe CSV plus an `investigators.export_manifest/1` manifest when `ADMIN_EXPORT_DIR` is set. The sharing bundle withholds `message.body`, `model_call.input`, `model_call.raw_response`, and `displayed_output.shown_text` until a `redaction_review` covers the cell. Payment exports stay on the compensation role. The 2026-10-07 dashboard Exports panel downloads one study table at a time through `POST /admin/exports/table` (CSRF and origin gated). `GET /admin/exports/tables` only lists names. The download reads one `REPEATABLE READ READ ONLY` snapshot, drops `withdraw_all`, and leaves sessions whose latest data-use choice is missing or `use_no_free_text` out of text-bearing tables. Declared free-text cells are nulled until a `redaction_review` covers them. `session.seed_hex` is removed from the session download. The response sets `Cache-Control: no-store`. An unknown table name, a malformed body, an unknown scope field, or an empty or oversized scope returns 422. A failed audit insert returns 503 and does not release the CSV. Payment and identity tables are outside the served vocabulary. Dashboard audit DC-01–DC-03 described an earlier passthrough that skipped these checks; the route in this tree performs them. That implements the §1 and §9.2 boundaries for this path. It is not a relaxation of those sections, and it does not replace the archival export manifest.

The relational model in §8.1 is present, plus `participant_display`, `session_credential`, `draw_log`, and `incident_interval`. `identity.platform_link` is the platform-id table. `coding_run`, `coding_result`, and `validation_gate` exist; the model coding path is dormant and is not a substitute for the §9.4 human gate.

### Explicitly not settled by this build

- D03 (peer-evaluation premise and control-arm disclosure), D08 (recruitment / return-link recovery), D09 (withdrawal wording and retention beyond the `use_all` screen), D11 (semantic rubric and thresholds), D12 (estimands, exclusions, expansion), and D14 (technical-incident clock policy) remain open.
- university AI service is the only amendment-authorized off-host congestion fallback, and no such adapter is implemented. `openai` and `minimax` adapters are refused for every session; they exist for offline fixture comparison only.
- G1–G10 in §12 are still the acceptance standard. A development `scripts/check.sh` pass is not those gates. The download-hardening log `docs/evidence/2026-10-07-download-hardening/check-20261007T131714Z.log` records 13/13 on this same source bundle; that log was read for this revision and was not re-run here. Browser-matrix, load, backup/restore, and pilot-output audit evidence are not re-certified here.
- No charting library, no live-updating dashboard, and no confirmatory test or automatic exclusion is part of this build.

## 1. Design objective and evidence

Build a durable, server-authoritative experiment around the existing simultaneous case-file / team-chat / AI-assistant workbench. Each experimental session contains **one human participant and two agent teammates**, JT and PM. The unit of randomization and analysis is the participant session; the apparent team is not three independently recruited participants. Both studies must use the same task engine, persistence, information-access rules, scoring, and operational controls. Differences belong in frozen study manifests, not copied HTML applications.

This specification borrows infrastructure patterns from the related AI-R / AI-M build without inheriting its scientific design, four-person matching, three-arm assignment, compensation rules, or model authorization. Writing it did not itself deploy services or authorize recruitment. The deployment and the N=5 fielding described under **Current build** are later, separately recorded acts; this document still does not authorize expansion past that pilot or a peer-evaluation launch.

### 1.1 Sources and precedence

| ID | Source | Use and limits |
|---|---|---|
| S1 | [Related build plan](/opt/research-study_ssl/TEAM_AI-R_AI-M_STUDY_PLAN.md), updated 2026-09-26 | Architecture, persisted deadlines, isolated previews, identity separation, provenance, admin, exports, and acceptance patterns. Its reported local tests are not evidence that Investigators works. Its live/browser/model acceptance remains outstanding. |
| S2 | [Time-pressure demo](./baseline/reference/investigators_time_pressure_demo.html) | Actual task behavior and condition-specific copy, including `buildWorld`, `TEAMMATE_QUESTIONS`, `openCheckin`, `submitCheckin`, `revealCheckin`, and `INV_FINISH`. **Archived 2026-10-06** to `docs/baseline/reference/` — it keeps its frozen historical name; the live playable-mock template `investigators_demo.html` at the repo root (renamed from the same basename on 2026-10-15) tracks the current build, not the pinned reference (all anchors and citations resolve to the archived copy). |
| S3 | [Peer-evaluation demo](./baseline/reference/investigators_peer_eval_demo.html) | Actual evaluation/control behavior, `EVAL_CONFIG`, `DEMO_POST_ITEMS`, `renderPeer`, and `finishPeer`. **Revised 2026-10-01** (re-pinned in Appendix A): const/let rewrite, payment-code consent, 480-second demo timer, live-AI hooks with generative thread replies plus canned fallback, and no check-in. The earlier revision's `VOICE` pools, probabilistic acknowledgements and crosstalk mechanisms were removed — behavior anchors record them as absent; the built `pe_demo_v1` profile still executes them (§4.1). |
| S4 | [Qualtrics usability pilot report](<./260920 Investigators Pilot Results.docx>) | Narrative and aggregate results from three mini-pilots, N=10 each. No participant-level dataset or original QSF was supplied; the reported estimates cannot be independently recomputed here. |
| S5 | [Supplied approved protocol](<./XXXX-XXXX IRB Approved Protocol.docx>), approval dated 2026-09-10; [PDF companion](<./XXXX-XXXX IRB Approved Protocol.pdf>) | Protocol XXXX-XXXX, *Technology-enabled Team-based Tasks*. DOCX text was reviewed, including consent, deception, payment, storage, AI, and debrief sections. PDF equivalence was not independently verified. |
| S6 | [Project supplement](../AGENTS.supplement.md), [system map](../SYSTEM_MAP.md), [Apache configuration snapshot](../vhost_investigators.conf) | Existing estate boundaries, public participant / protected admin patterns, item timing, export and destructive-test safeguards. Configuration snapshots do not establish current runtime state. |

Additional reviewed evidence:

| ID | Source | Use and limits |
|---|---|---|
| S7 | [Hidden-bench evaluation](./INVESTIGATORS_TEAM_EXPERIMENT_HIDDEN-BENCH_INSIGHTS.md), grounded in the [configuration runbook](/opt/hidden-bench_ssl/HIDDEN-BENCH_CONFIGURATION_RUNBOOK.md) and the source tree selected by its reviewed build configuration, `backend/app/` | Per-agent knowledge, conversation visibility, fact/claim coding, evidence transfer, and runtime/provenance cautions. The evaluation reports 33 selected passing tests and deterministic probes; these do not validate Investigators or a live deployment. |
| S8 | [Timing evaluation](./INVESTIGATORS_TEAM_EXPERIMENT_TIMING_INSIGHTS.md), grounded in the [Triad timing considerations](/opt/team-testbed_ssl/GROUP_TASKS_ROLE_INTERDEPENDENT_KNOWLEDGE_WORK_SIMULATIONS_TIMING.md), [implementation plan](/opt/team-testbed_ssl/GROUP_TASKS_ROLE_INTERDEPENDENT_KNOWLEDGE_WORK_SIMULATIONS_TIMING_PLAN.md), and reviewed code | Timing taxonomy, concurrent generation, delay distributions, clock and exposure boundaries. Reports 37 passing isolated tests plus probes exposing runtime gaps; not evidence of browser, provider, or participant validity. |

Detailed source findings and reproduction notes remain in S7/S8 for audit. All adopted design obligations are stated here; implementers need not assemble requirements from the reports. No test result from another project counts as an Investigators acceptance PASS.

**Precedence:** the research team resolves protocol/material discrepancies; this spec must not silently resolve them by selecting whichever artifact is easiest to implement. The HTML files are behavioral references, not durable data stores or proof of the exact Qualtrics deployment. S4's suggested changes are proposals, not already fielded settings. All numerical operational targets below are proposed engineering criteria unless explicitly attributed to a source.

### 1.2 What the pilots imply for infrastructure

| Evidence from S4 | Required capability / interpretation |
|---|---|
| Mini-pilot 1 reduced eight task minutes to six because attentive participants finished in roughly five to six minutes. | Freeze a 360-second task budget; prohibit early final submission in baseline profiles while allowing answer revisions. Do not copy S1's early-end voting. |
| Moving the check-in from 90 to 60 seconds still allowed requests before manipulation. | Persist actual manipulation exposure and separate pre/post opportunities. Support an independent-review-first profile as a new version. |
| High/low time pressure in mini-pilot 2 used 5/6 versus 2/3; S2 now uses 6/7 versus 2/3. | Record exact displayed values and their seat assignment. Never merge those high-pressure versions without a version indicator. |
| Mini-pilot 2: mean teammate requests 0.6 versus 1.8; AI prompts 5.2 versus 4.8; total correct 1.8 versus 2.2. Team-dependent correct totals were 0 versus 3 out of ten opportunities per arm. | Treat these as descriptive usability signals with five people per arm, not established effects or power estimates. Preserve crate-level denominators and request timing. |
| Mini-pilot 3 showed little separation in seeking; two of five controls said they had been told about evaluation in advance. | Audit every exposure surface and collect recall, perceived evaluation, concern, and suspicion separately. Both demo arms collect peer ratings after the task. |
| Suggested future evaluation-linked bonus may require further review. | Make payment policy separately versioned; do not implement ratings-contingent pay as an implicit next-pilot feature. |

## 2. Verified demo behavior and migration gaps

### 2.1 Shared task contract

- Eight crates, six hideouts; four crates assigned to the participant, displayed in ascending crate order. Exactly two assigned crates are solvable from the initial own file and two depend on newer teammate information.
- Hideouts: The Sawmill, The Marina, Blackwell Storage, Old Cannery, Fairground Sheds, Ridgeline Bunker. Contents: laptops, smart watches, gaming consoles, TVs, headphones, cameras, tablets, power tools.
- Timelines are generated across relative days. Own-file crates have their final move yesterday; team-dependent crates move after the participant's information cutoff. Own-file cutoffs vary between yesterday evening and this afternoon; team-dependent cutoffs between yesterday evening and this morning.
- For unassigned crates, the participant receives two late entries useful to teammates. The remaining entries are divided between JT and PM.
- Initial record order is shuffled. Records can be reordered by drag (a grip affordance marks movable cards), by a click-to-grab/click-to-drop gesture on the same grip, or by keyboard (focus the grip, arrow keys move the card); all three paths share the same reorder semantics with a live-region announcement (BA-38). The fielded UI provides no sort selector and no crate filter (2026-10-01/02 UX rounds). A record the participant shared shows a "Shared in team chat" state in place of its Share control, and the server dedupes repeat shares of the same record (2026-10-02 UX round). Team-provided records enter the AI's accessible file automatically on delivery (`added_from_team`, tagged "Shared by [seat]"; the manual add step was removed in the O-4 change).
- Structured requests return the latest available entry across the teammates' holdings. Free-text teammate replies are a separate social channel and are instructed not to reveal hideouts.
- Scripted teammate requests occur at task seconds 40, 150, and 240: two concern distinct unassigned crates and one an assigned crate. The code comment says “one unowned crate” remains unrequested, but with four unassigned crates and two distinct unassigned requests, **two** remain unrequested. Use generated allocations, not that comment, to define opportunities.
- Both demos post the system chat message **“One minute left.”** when the countdown reaches 60 seconds remaining (active second 300) and switch the timer to its low-time style for the final 60 seconds (TP demo `timeLeft === 60` / `timeLeft <= 60`; PE demo likewise). This is the only in-task time warning. The server application encodes it as a versioned, active-time-based warning job (`warning.one_minute_left`, §6.6) rather than a client tick.
- Participants choose locations during the task; the six-minute endpoint freezes answers. Confidence is collected afterward on a 0–100 scale for answered crates; no answer is a distinct missing state. The slider's visual starting point must not count as a response.
- The time-pressure demo advertises $0.25 per correct assigned crate, maximum $1.00, in addition to base pay; the revised peer-evaluation demo (2026-10-01) contains no bonus copy and uses payment-code completion instead. This is observed demo copy, subject to the protocol reconciliation in §13.
- Training exercises sharing, receiving an entry (auto-filed in the participant's case file), and asking the assistant. Practice activity must remain outside analytic task counts.

### 2.2 Study-specific behavior

| Concern | Time pressure, S2 | Peer evaluation, S3 |
|---|---|---|
| Arms | `high`, `low` | `eval`, `control` |
| Manipulation | At elapsed task second 60, pause countdown; collect participant pressure 1–7; simulated collection delay 3.5 seconds; reveal JT/PM values; explicit Resume. | Evaluation instructions at introduction and reminder, persistent task strip and system message in evaluation arm; generic questionnaire wording in control. |
| Displayed values | High 6/7, low 2/3; higher rating randomly assigned to JT or PM. | No time-pressure check-in. |
| Exposure | Ratings remain visible above chat after resume. Requests are possible before check-in in this demo. | Evaluation expectation is introduced before work. |
| Teammate behavior | Structured requests, shares, and optional generative social replies. | Original revision added seat-specific voice pools, probabilistic thanks, and crosstalk 28 seconds after scripted requests; the **2026-10-01 revision removed those mechanisms** in favor of generated thread replies with a canned fallback. |
| Post-task | Confidence, post-task questions, debrief. | Confidence, JT/PM helpfulness 1–7 **in both arms**, post-task questions, debrief. |

The studies' different acknowledgement/crosstalk behavior is an explicit **between-study implementation difference**. Within each study, manipulation arms must share the same behavior policy. A harmonized behavior policy can be tested as a new profile, but must not replace historical behavior without a version change. Note the 2026-10-01 divergence: `pe_demo_v1` as pinned still executes the original demo's voice/acknowledgement/crosstalk policy, while the revised S3 reference removed it — reconciling the reference profile to the revised demo is a new manifest version under D07/D10, not a silent update.

### 2.3 Gaps that the server application must remove

| Observed source pattern | Required replacement |
|---|---|
| Browser `Math.random()` generates world, condition-dependent presentation, record order, and delays. | Server allocation, stored randomization inputs and complete immutable world/schedule snapshots. |
| Full `WORLD`, truth, and all seats' records exist in participant JavaScript. | Participant-scoped projections; truth and inaccessible records stay server-side. |
| In-memory arrays, `setInterval`, and `setTimeout` own task state and timing. | Durable state machine, active-time clock, transactional commands and recoverable jobs. |
| `INV_FINISH`, embedded data, hidden textarea and 1.2-second “flush” delay represent completion. | Commit data before acknowledging completion; persist throughout the session. |
| Demo post-question buttons style selections but do not durably save their responses. | Versioned item IDs, per-item persistence, explicit missing/skip codes and item events. |
| Browser calls `https://qualtrics-relay.example.com/chatgpt`. | Same-origin experiment API and approved university-local inference only. The endpoint's actual downstream provider was not inspected. |
| Hideout-name filtering is the main model-output boundary. | Source-backed crate/time/assertion validation; an allowed place name alone does not justify a claim. |
| Some asynchronous callbacks can outlive countdown pause/end; timers are not all in the tracked timeout list. | Stage-aware scheduling; delivery authorization rechecked at commit; suppress late responses. |
| AI reply logging can record `fallback:false` before returning a validation fallback. | Separate attempted generation, validation decision, fallback selection and displayed output records. |
| Condition chooser, truth payload and Restart are exposed in demo UI. | Preview-only controls, isolated from live participant bundles and endpoints. |

Preserve S2/S3 as reference artifacts. Build normalized golden fixtures from their behavior; do not keep evolving two independent task engines.

## 3. Shared architecture and borrowing boundary

### 3.1 Proposed deployment

```text
Participant browser — HTTPS investigators.local/investigators/
        │
        ├── investigators-ui: React + TypeScript + Vite, static serving (127.0.0.1:8518)
        └── /investigators/api/: investigators-api, FastAPI + SQLAlchemy + Alembic (127.0.0.1:8519)
                  │
                  ├── dedicated PostgreSQL database / isolated roles
                  ├── durable scheduler + transactional event outbox
                  └── investigators-worker (same network namespace as Ollama)
        investigators-ollama on loopback inside that namespace (port not published);
        adapter id local_openai_chat. university AI service fallback is not built.
Researcher browser — application-enforced admin login (Apache Shibboleth block declared but overridden; §3.1)
        └── /investigators/admin/ and /investigators/api/admin/
```

Use **`/investigators/`** on **investigators.local** as the participant base. The route is deliberately not `/team/`: S1 uses `/team/` on **neighbor-study.local** with ports 8111/8112, and a same-named path invites copy-paste of that block, its ports, allowlist or secret include. S1 is a separate service and must not be repointed, joined to this database, or treated as available ports. After a 2026-09-28 inventory (listening sockets, vhost snapshot, `SYSTEM_MAP.md`, Compose files), loopback ports **8518 (investigators-ui)** and **8519 (investigators-api)** were unused and are allocated to this service; 8514 and 8517 were listening without vhost entries and are not available. Re-verify both ports are free immediately before first container start. Give containers, networks, database, cookies, metrics and backup entries distinct `investigators` names; session cookies are scoped to path `/investigators/`.

The ingress block is declared in the `investigators.local` virtual host of the local `vhost_investigators.conf` snapshot between `# BEGIN/END INVESTIGATORS MANAGED ROUTES`, with source of truth [`investigators/apache/investigators.vhost.snippet`](./apache/investigators.vhost.snippet). The operator applies it to `/etc/httpd/conf.d/investigators.conf` and runs `apachectl configtest` and a graceful reload. Before the containers existed, those paths returned 503; the running services are the five listed under **Current build**. The backend declares `/investigators/api` as its external root path and internal routes without that prefix; Apache strips the external prefix once. The frontend base is `/investigators/`. Verify slash redirects, asset URLs, API routing and SPA refresh beneath the final prefix. Verify host scoping and actual routing through the live proxy before relying on it.

**Ingress layout and admin authentication (owner decision 2026-09-28).** The block follows the estate's `/pcs/` and `/dct/` layout: Shibboleth `<Location>` blocks for `/investigators/admin` and `/investigators/api/admin` (allowlist `admin1@university.edu`, `admin2@university.edu`), followed by one public `<Location "/investigators/">` for the whole path. No one-off secret `.incl` file is used. Consequences that the implementation must honor:

- Because the later public block overrides the earlier admin blocks, **Apache does not enforce Shibboleth on the admin paths**. Verified 2026-09-28: `/pcs/admin` and `/dct/admin`, which use this layout, return 200 without SSO, whereas `/hp/api/admin/` (public block first) redirects to [University] SSO.
- The public block removes `X-Remote-User`, `X-EPPN`, `Eppn` and `X-Shib-Authenticated` on every `/investigators/` request, including admin requests. The API and UI must never infer identity or authorization from these headers.
- **The application enforces admin authentication itself**, using the same pattern as `./estate/pcs/.env` and `./estate/dct/.env`:
  - `ADMIN_PASSWORD` and `ADMIN_EMAILS` live in `./.env`. The template is `.env.example`, which contains no secret. The real `.env` is never committed, printed or logged, and keeps the estate `.env` posture (640, group `docker-users`, `u:admin1:r` ACL).
  - An empty `ADMIN_PASSWORD` disables admin (fail closed).
  - The password check uses a constant-time comparison (`hmac.compare_digest`) rather than `!=`.
  - A successful login issues a signed, expiring admin session cookie scoped to `/investigators/`, with CSRF/origin checks, login rate limiting, and the researcher/operator/compensation role split.
  - `ADMIN_EMAILS` records the confirmed allowlist for audit and for any future move to Apache-enforced SSO.
- The API and UI bind only to 127.0.0.1, so the proxy is the only network path to them.

PostgreSQL is authoritative. SSE with an event cursor plus ordinary HTTP commands is sufficient for one participant per session; WebSockets are optional, and the fielded client does not open one. It uses `GET /events/stream` when `EventSource` exists and `GET /events` otherwise (**Current build**). Use persisted events and reconnect replay regardless of transport. A polling fallback must work through Apache. An in-memory fan-out channel may accelerate notifications, but cannot own clocks, schedules or recovery. Multiple workers require shared DB-backed delivery; do not inherit S1's single-worker assumption accidentally.

### 3.2 Reuse matrix

| S1 pattern | Investigators adaptation |
|---|---|
| Frozen content, hashes, session pinning | Adopt for manifest, world generator, scripts, instruments, prompts, provider policy and payment policy. |
| Server timers and event cadence | Adopt clock/recovery concepts; implement paused check-in and six-minute active budget. |
| Source-validated model outputs | Adopt principle; validate crate chronology and file membership, not candidate/trait extraction. |
| Separate identity permissions | Adopt platform-neutral identity linking for Prolific, Connect and approved lab entry. |
| Signed isolated previews | Adopt one-participant preview cohorts, seed/profile selection, no payable outcomes. |
| Public participant / SSO admin | Adopt with Investigators personnel; do not copy S1's co-investigator allowlist. |
| Lobby, four seats, team barriers/dropout | Do not import into these pilots. The connecting screen is scripted presentation, not human matchmaking. |
| Three-arm team balancing | Replace with 1:1 participant assignment within study and pilot batch. |
| AI-R / AI-M panels, 30-minute chat, all-four bonus | Not part of Investigators. |
| Local-only `gemma4:26b` | Reuse provider abstraction only. Model family is settled (§7.4: Gemma 4; S5's “Gemma 3” is a typo); the exact model/digest and GPU capacity question is resolved by the 2026-10-01 amendment — `gemma4:26b` deployed via Ollama (switched from `gemma4:12b` 2026-10-03), with university AI service as the only authorized congestion fallback. |

Initial project layout: `investigators/backend/`, `investigators/frontend/`, `investigators/content/`, `investigators/tests/`, `investigators/scripts/`, `investigators/docs/`, and `investigators/docker-compose.yml`. Keep world generation, scheduling, study policy, provider access, scoring and export logic in separate modules with stable contracts. Do not make the new service depend on importing another live application's private modules or research tables. Record any deliberately extracted shared library's version and contract tests.

### 3.3 Boundaries learned from hidden-bench and Triad

Borrow explicit knowledge views, inspectable evidence lineage, independent timing components, human-coded validation, and versioned release records. Do not import hidden-bench's unrestricted agent deliberation, majority-vote outcomes, disposable queues, whole-session retries, external judge defaults, or preserved migration history. Do not import Triad's 15-minute no-pause profile, in-memory rooms, poll-triggered model calls, fixed nine-second helper, or nominal timestamps as actual delivery times.

One source tree must own each built service, and one migration history must own the Investigators database. Record the build-context checksum and image digest. A single in-memory notification queue must not be consumed competitively by multiple browsers: each authorized subscriber replays its own cursor over the same committed ledger. Production read endpoints must not start model work or determine whether due events are committed.

## 4. Immutable experiment manifests

Every enrollment pins `study_key`, `phase`, `batch_id`, `manifest_id`, content SHA-256, schema version and application build checksum. With no Git tree assumed here, a source-bundle checksum and image digest are mandatory; pin model name/digest, prompts, validator, script policy, metric definitions, payment policy and debrief version separately.

| Manifest field | Required meaning |
|---|---|
| `study_key` | `investigators_time_pressure` or `investigators_peer_eval`; never infer study from `high`/`control` alone. |
| `condition_set`, `allocation_policy` | Allowed arms, weights, block policy and enrollment strata. |
| `task` | Eight crates/six hideouts, assigned=4, own=2, team-dependent=2; generator and difficulty version. |
| `timing` | Active budget, review interval, check-in behavior, pause caps, scripted request schedule, delivery-delay policy. |
| `channels` | When file work, answer drafting, structured seeking, sharing, social reply and AI prompting are enabled. |
| `manipulation` | Approved copy IDs, rating values/seat mapping policy or evaluation exposure surfaces. |
| `teammates` | Voice, request, acknowledgement and crosstalk versions; social-generation mode. |
| `assistant` | Provider, source-access policy, prompt/validator versions, timeout/fallback policy. |
| `instruments` | Item IDs, order, anchors, optionality, skip handling, scoring/reverse-scoring rules if applicable. |
| `outcomes` | Completion statuses, base/bonus policy, platform code map, withdrawal handling. |
| `mode` | `preview`, `qa`, or `live`; separate storage/identity/payment eligibility. |

Reject unknown condition/profile combinations; do not silently default to high pressure or evaluation as the demos do. Editing a manifest creates a new immutable version. Active sessions retain their original version, even if enrollment is paused or a new batch opens. Emergency termination is a separate audited action.

### 4.1 Required profiles

1. **`tp_demo_v1`**: behavioral reference for S2; 360 active seconds, channels available from start, check-in at 60, 6/7 versus 2/3, requests at 40/150/240. Mark as demo-derived, not proof of a historical Qualtrics version.
2. **`pe_demo_v1`**: reference for the original S3 revision; 360 active seconds, evaluation/control exposures, richer voice/crosstalk policy, peer ratings in both arms. The revised S3 (2026-10-01) keeps the evaluation/control exposures and both-arm peer ratings but drops the voice/acknowledgement/crosstalk machinery and uses a 480-second demo timer — the production budget stays 360 s (§6.6) and a reference-profile realignment is a separate D07/D10 manifest version.
3. **`tp_review_first_v2` — original next-pilot proposal**: first 60 active seconds for own-file review and answer drafting; teammate request/share/reply and AI channels disabled. Check-in follows, outside active task time; remaining 300 active seconds allow all channels. This implements S4's no-pre-manipulation-seeking idea conservatively. The proposal closes both help sources during review. Live enrollment does not bind v2; it binds the v10 successor below.
4. **`pe_pilot_v2` — proposed next-pilot profile, not fielded**: retain six minutes and existing evaluation/control contrast; use a frozen, reviewed message policy and complete post-task instrument. No pressure check-in and no evaluation-contingent bonus. Paid PE recruitment remains behind D03.
5. **`tp_review_first_v10` — fielded profile (2026-10-07)**: the live-eligible successor of item 3, bound by batch `prolific.live.n5.v10`. Same review-first channel split and 80/180/270 request times. Own-pressure rating is skippable. Social generation is `model_with_validated_fallback` on digest-pinned `gemma4:26b`, not deterministic scripting. Acknowledgments, crosstalk, assistant referral, unsolicited requests, and proactive sharing are off. Payment is `payment.s5_base_only` v3 with `withdrawal_payment: forfeits_base`. The full bound list is under **Current build**. v3–v9 remain in the tree as superseded drafts; v9 stays pinned to the closed batch `prolific.live.n5`.

For profile 3, propose requests at **20/120/210 interaction seconds**, equivalent to active task seconds 80/180/270. This avoids dropping the old 40-second request during the locked review period and retains space for replies. These times are proposals requiring content approval, not values from S4. Freeze a selected schedule across both arms. Do not add a review interval to peer evaluation just to make the implementations look alike.

**Preview and acceptance coverage.** Each of the four original profiles contains both of its study's arms, so the engine supports **eight profile/arm combinations** (`tp_demo_v1` × high/low, `pe_demo_v1` × eval/control, `tp_review_first_v2` × high/low, `pe_pilot_v2` × eval/control). All eight must preview and pass the browser path. Successor and `nl` manifests in `content/profiles/` are additional versions, not substitutes for that matrix. Only an approved profile selected under D01 or D03 can be marked launch-eligible. The profile that is launch-eligible today is `tp_review_first_v10`, not v2.

**One-minute warning.** All four profiles carry the reference warning `warning.one_minute_left` v1: the system chat message “One minute left.” is committed at **active second 300** (60 active seconds remaining), and the countdown adopts its low-time style from active 300 through 360. It is scheduled on the `active_task` clock, so the TP check-in pause cannot shift it; in `tp_review_first_v2` it therefore appears at interaction second 240 (active 300). It is a system message, not a teammate utterance, is identical across arms within a study, and is suppressed if the task has already closed. Any change to wording, trigger time or styling is a new warning version selected under D10. **2026-10-07 fielding annotation (D10, BA-99):** the fielded profile `tp_review_first_v10` binds `warning.one_minute_left` **v2** — identical wording, active-300 trigger and low-time styling; the version bump republishes the reference body with `status: approved` because v1's bytes are frozen inside earlier published bundles and the live gate requires approved artifacts.

### 4.2 Executable configuration and compatibility

Materialize defaults before canonical hashing. Hash the actual content, prompts, policy artifacts, and executable build, not a descriptive label such as “unchanged agent bundle.” A bound manifest must resolve to the policy the scheduler and generators actually execute. Reject unsupported switches and combinations before enrollment; recording a field is not implementing it.

The timing block must specify clock basis, response classes, per-source/per-seat distribution parameters, mean versus median convention, truncation method, think modifiers, length/typing rules, cues/rendering, warning schedule, concurrency/rate limits, and timeout/ordering/visibility policies. Define separate classes for structured records, social replies, assistant output, acknowledgments, and scripted requests. Keep factual service, interaction schedule, conversational wording, and manipulation surfaces separate.

Use a full compatibility key for analysis and synthetic runs: study, batch, manifest, generator/content, instrument, timing/behavior policy, prompt/validator, provider/model digest, inference parameters, and metric/coding versions. Seed and replicate identify a synthetic case; retry-attempt lineage is separate. Resume exact unfinished cases rather than counting completed rows, and retry a failed job rather than replaying a human session. Aggregates must explicitly select compatible batches; equal model names or arm labels are insufficient.

A seed is not a guarantee of identical model text. Store actual per-call parameters and provider/model identity, accepted random draws, and immutable generated artifacts. Key timing draws by job and seat in independent streams so an extra chat turn cannot redraw future stimuli or the other seat's behavior.

## 5. Enrollment, assignment and participant lifecycle

### 5.1 Enrollment and randomization

- Use platform adapters to validate configured study/session identifiers. Query-string platform IDs provide linkage, not authorization to resume or access records.
- Issue an opaque random session credential; persist only its hash server-side where feasible. Prefer a Secure, HttpOnly, path-scoped cookie plus CSRF protection; protect event-stream origins as well. Knowing a panel ID must not recover a session credential.
- Consent precedes assignment; assign before the first condition-specific instruction. Store consent text version, timestamp and affirmative action.
- Allocate 1:1 within `study_key × batch` using server-side randomized blocks under a DB lock. Persist assignment and enforce uniqueness transactionally. Do not reassign on reload, dropout or failed comprehension. Report assigned, exposed and completed counts separately; never rebalance retrospectively on outcomes.
- Persist independent random streams for condition, world, file order, rating-seat mapping and teammate schedules. A change to social wording must not change the crate assignment. Store full generated artifacts in addition to seeds so generator updates cannot break replay.
- Duplicate-entry and cross-study eligibility rules must be explicit. Proposed next-pilot default: prevent repeat participation across both Investigators versions, using authorized platform prescreening/exclusion lists; keep any retained identity hash subject to the approved retention policy. Do not promise cross-platform deduplication without a legitimate linking identifier.
- Preview credentials are admin-issued, signed, expiring and scoped to profile/condition/seed. They never consume live allocations or create panel-payment rows. QA worlds and accelerated clocks cannot be enabled by a live URL parameter.

### 5.2 State machine

```text
entry → consent → instructions → training → scripted_connecting → ready
  time-pressure baseline: task → checkin_collect → checkin_wait → checkin_reveal → task
  time-pressure review-first: solo_review → checkin_collect → checkin_wait → checkin_reveal → task
  peer-evaluation: task
→ answers_locked → confidence → [peer_ratings for both PE arms]
→ post_questionnaire → [open_ended for TP] → debrief → data_use_choice → completion
```

`declined`, `withdrawn`, `technical_stop`, and `abandoned` are explicit exceptional states. Record current state, revision, entered time, reason and authorized next transitions. All terminal paths after exposure provide a way to reach debrief; a disconnected participant sees it on authenticated return. Never promise debrief delivery to a browser that never reconnects.

Training uses a separate fixed practice world and practice event namespace. If comprehension checks are added, their items, attempts, removal and payment implications must be approved for this study; do not copy S1's three-strike rule. Provide explicit skip handling for response items in keeping with S5, which says skipped questions do not affect payment. A task clock can still govern task duration; required questionnaire answers cannot become a payment gate by accident. In the piloted UI (2026-10-02 UX round) there is no per-item skip control on instrument pages: submitting a page auto-records `item_skip` for every untouched item, so skipped state is always explicit in the record. The fielded check-in is different: `tp_review_first_v10` sets `own_rating_skippable: true` and accepts `pressure_skip`, so a missing own-pressure rating is stored as skipped rather than imputed. Training cannot finish until one practice share is stored (`practice_share_required`).

The connecting screen schedules JT/PM presentation locally to this session. There is no real teammate dropout or other participant's payment to resolve. A browser disconnect is session recovery; agent/model failure is technical degradation.

## 6. Time, scheduling and failure semantics

### 6.1 Clock contract

Persist UTC wall-clock timestamps and an **active-time ledger**: running intervals and permitted pause intervals with reasons. Compute active elapsed from the database clock, not tick counts or client clocks. Send `server_now`, state revision, active elapsed and current deadline to the browser; display an interpolated countdown, resynchronized on reconnect. Persist all information needed to reconstruct the timer after API or scheduler restart.

For time pressure, the check-in starts once at the 60-second boundary. The active budget pauses through own rating, the 3.5-second collection presentation and reveal/resume. Gate all task commands during the pause. Pause pending agent delivery, scheduled requests and crosstalk on the same active-time axis; a queued reply must not appear behind the check-in overlay. Log scheduled opening, server transition, browser render acknowledgement, own-rating submission, reveal availability, reveal render acknowledgement and resume separately.

Check-in inactivity cap, selected on `tp_review_first_v10`: 120 wall seconds, then save a `technical_stop/checkin_incomplete` outcome and offer debrief/return. This caps stalled sessions without inventing an own-pressure response or claiming manipulation exposure. Skipping the own-rating item must be supported without imputing a rating; the fielded profile does so (`pressure_skip`).

A network disconnect or hidden tab does **not** pause the active clock. Record visibility and connectivity separately; active elapsed is not evidence of attention. Reconnect returns the current stage and any unseen events. If the task expired, display the locked answers and continue to post-task screens. Unexpected server outages also do not silently extend time: reconstruct elapsed time, flag the affected interval, and apply a versioned technical-incident policy rather than manufacturing normal exposure. The policy (clock treatment, flagged-interval schema, participant-facing handling, payment treatment, analytic flag) is a §13 decision that must be selected before real timing acceptance; persisted incident intervals reference the active-time ledger and affected surfaces.

### 6.2 Durable jobs and transaction boundaries

Jobs include greeting, request, structured response, acknowledgement, crosstalk, check-in reveal and deadline transitions. In the piloted UI the greeting options render only during the first 30 active seconds of the task chat and disappear once a greeting is sent or the window expires — a replayed prompt must not resurrect them (2026-10-02 UX round). Store session, job kind, deduplication key, due active/wall time, parent event, state precondition, payload snapshot, lease and status. Workers claim with row locking and recover expired leases. Unique `(session_id, logical_job_key)` plus an outbox gives one logical committed event under at-least-once execution.

At each command, lock the session, advance any due deadline transitions, validate stage and permissions, apply mutation, append event/outbox rows and commit. HTTP retries reuse a command UUID; return the original committed result. A scheduler and a request arriving concurrently cannot both finalize answers or double-post a teammate message.

At active second 360, atomically freeze the latest **server-accepted** answer for each assigned crate. Accept an answer only while active elapsed is strictly less than 360 at locked server validation; client timestamps do not override this boundary. Store late/rejected attempts separately. No early submission endpoint exists in the baseline profiles, and none exists in the v2 profiles unless a versioned D01 decision adds one. In-flight model results are audited but never delivered to the task after closure.

Response delays are sampled once and persisted, not resampled on retries. Record request receipt, queue start, generation end, scheduled delivery, committed delivery and client-render acknowledgement. Use the same delay distributions and fallback deadlines across arms within each study/profile. The demo's 20-second assistant and 25-second social timeouts are reference settings, not a license for condition-dependent waiting.

### 6.3 Intended timing, computation, and actual exposure

Every job declares `clock_basis` (`active_task` or an explicitly authorized wall-time basis). UTC is durable chronology; a monotonic process clock measures computation durations only. Opportunity/exposure intervals are derived analytics, not another task countdown. Do not subtract active-time values from UTC timestamps.

Generation and the intended delay run concurrently. On a common clock, absent other gates:

```text
scheduled = grant + platform_delay + think + typing
eligible = max(scheduled, validated_ready)
```

A three-second generation with a nine-second intended wait is eligible at second nine, not twelve. A twelve-second generation is eligible at twelve. Record readiness overrun separately from ordering/visibility holds, scheduler lateness, network transit, and rendering. Never backdate actual commit to its nominal due time.

Persist the lifecycle `accepted → granted → generating/validating → ready → eligible → committed → dispatched → received → rendered`, allowing generation and waiting to overlap. Terminal states include failed, cancelled, expired, and suppressed. Keep intentional/scheduled silence, empty generation, validation rejection, timeout, fallback, delivery failure, and participant non-response distinct. Neither an empty turn nor a fallback acknowledgment is a successfully delivered substantive answer.

Record request acceptance, grant, broker/queue reason, generation start/end, validation completion, scheduled target, readiness, eligibility, hold reasons, actual commit/dispatch, and browser receive/first/final render receipts. Include event sequence, replay status, tab visibility, and client/server clock uncertainty. A render receipt is availability evidence, not proof of reading. Missing receipts remain missing.

If typing depends on generated length, anchor the schedule to the original grant and finalize the length component after validation; store schedule revisions and indicator slip. Do not restart the full wait after generation. Structured bodies can be selected before timing. The typing cue begins after platform delay and thinking, subject to the frozen cue policy and body readiness. Streaming, if explicitly configured, needs first/final-content times; it must not silently reveal facts before the release policy permits.

### 6.4 Pause, ordering, and deadline authorization

A TP request granted at active second 57 with a nine-second wait remains due at active second 66. A check-in starting at 60 pauses that schedule; the reply is due six active seconds after resume regardless of the wall duration of check-in. Generation may finish during the pause, but readiness does not authorize display. The 3.5-second collection presentation uses wall duration inside the permitted pause. PE has no corresponding pause. Triad's prohibition on `clock_paused` events does not apply here.

At delivery commit, lock and revalidate stage, active deadline, recipient permissions, job revision, and prior delivery. This applies to every task surface, including assistant output, acknowledgments, crosstalk, and scripted requests. A result arriving after closure remains auditable but cannot create a late or second bubble. Retry preserves original context and due time; recovery cannot redraw a wait.

Eligibility and ordering are separate policies. Chronological ordering and first-ready ordering must produce different results when readiness and scheduled order cross. System transitions and injections have independent jobs; they cannot wait for chat generation. Independence does not authorize a future event to appear early. Persist per-recipient availability and ordering holds. Same-time context inclusion uses a documented committed-event sequence boundary, not ambiguous timestamp equality.

Keep factual record time, communication time, recipient availability, and file-membership time separate. On the fielded path that membership time is the automatic `added_from_team` write at delivery, not a participant Add click. Latest crate evidence is determined from validated record chronology, never chat arrival order.

### 6.5 Distribution and source-policy validation

The demos use clamped lognormal delays with length terms; social replies add delay after generation, while assistant replies overlap generation with a minimum elapsed floor. Preserve these behaviors in reference fixtures, but implement the target concurrent lifecycle for robust pilot profiles and document the migration difference. Do not call three mechanisms one generic “response delay.”

Each response class declares `timing.mechanism`. The reference profiles `tp_demo_v1` and `pe_demo_v1` execute the demo mechanisms (`demo_structured_delay_length`, `demo_social_additive`, `demo_assistant_floor`, with clamped lognormal draws) so their golden traces reconcile with S2/S3. The v2 profiles execute `concurrent` for every class. The fielded `tp_review_first_v10` does the same for every response class, reusing the reference clamped-lognormal parameters (medians, caps, and timeouts are under **Current build**). That is the D10 selection for the N=5 pilot. Demo mechanisms are rejected in any non-reference profile. The reference-to-v2 timing difference (additive versus concurrent social delay, the assistant floor, clamping versus any rejection sampling) is an explicit, reviewed profile diff, not a refactor.

Clamping and rejection sampling have different distributions. Holding a lognormal median while changing variance does not hold the mean; truncation can shift even a configured mean. Validate accepted draws and realized waits across seats, response classes, phases, and arms: mean, median, tails, rejection rates, readiness overruns, commit lag, and deadline suppression. Test all supported modifier combinations, not only the base distribution. Separate configured, analytically predicted, simulation-measured, and runtime-observed quantities. Never present a published constant as a measured session statistic.

Measure incomplete/expired replies as well as completed ones: the mean among delivered replies can look faster because slow replies were censored. Long delays proposed for other tasks, including a possible 20–60-second human-like range, are not default settings for a six-minute crate task. Select and freeze realistic policies through separate usability work without changing treatment fidelity.

### 6.6 Baseline controls and dormant extensions

The following covers the timing taxonomy's 25 considerations plus the separate think-time registry dimension. “Hold” means the same frozen policy across arms within a study, not forced identical outcomes under different participant behavior.

| Dimension | Baseline contract / extension boundary |
|---|---|
| Latency by source | Separate structured record, social reply, and assistant policies; audit each. |
| Distribution/variance | Hold within study; retain parameters, accepted draws, and realized distributions. |
| Per-member latency | Explicit seat policy; no accidental alignment of speed with pressure-rating assignment or evaluation arm. |
| Trajectory | No unplanned speed change after check-in or near deadline; future steps require versioned triggers. |
| Delivery failure | No synthetic failure treatment by default; classify real incidents and deadline suppression separately. |
| Partial visibility | Enforce role/recipient timing; staggered delivery is a separate treatment. |
| Ordering | Explicit eligibility/ordering; preserve record chronology independently. |
| Rendering | Freeze complete/streamed delivery and typing rules by source. |
| Presence | Seen/busy/typing are social cues; server delivery is not a read receipt. Which cues are shown per source is frozen under D07. `tp_review_first_v10` sets every presence cue to none. |
| Delay attribution | No improvised person/system excuses; future cue contrasts hold substantive timing fixed. |
| AI referral | Freeze whether referral is allowed (D07). `tp_review_first_v10` sets assistant referral to none. If a later profile allows it, identify a relevant seat without leaking its records. |
| Blocking | Preserve simultaneous workbench; specify which actions remain available while waiting. |
| Concurrency | Explicit cap/scope and resolution; reject visibly rather than silently queue unless queuing is the selected policy. |
| Time budget/visibility | 360 active seconds with the profile's countdown and pause rules. |
| Deadline structure | One global answer freeze; per-crate/round deadlines require a new design. |
| Warnings | Version copy and trigger; do not inherit Triad's warning-free baseline. Reference warning: `warning.one_minute_left` (v1 reference; fielded as approved v2 in `tp_review_first_v10`), “One minute left.” system message at active 300 plus low-time countdown style (§4.1). |
| Rate/cooldown | Separate experimental budgets from operational protections; log rejection reason. |
| Scheduled injections | Type scripted requests versus new information; preserve the profile's schedule. |
| Think time | Separate intended thinking, model computation, and typing; validate modifiers. |
| Two-stage replies | Acknowledgment and substantive evidence remain separate; acknowledgment does not reset the answer schedule. |
| Proactive sharing | No off-policy volunteered facts that remove the need to seek evidence. |
| Contingent responsiveness | No implicit faster service as a reward for sharing; reciprocity is a separate treatment. |
| Availability windows | No synthetic absence by default; classify real outages as incidents. |
| Chatter | Retain explicit between-study differences; hold policy within each study and log parentage. |
| Length/typing | Jointly freeze length and typing policy; longer generated text must not hide a treatment-dependent delay. |
| Human plausibility | Separate usability evidence from experimental fast/slow contrasts; no imported universal delay target. |

Future mean/variance, cue, acknowledgment, capacity, or visibility contrasts must vary the intended component while holding other policies fixed. Supporting a field in the schema does not activate a new study arm.

Maintain this table as an executable **conformance matrix**: for each of the 26 rows record the baseline value per profile, the manifest field (or “unsupported → rejected at bind”), the runtime enforcement point, and the test that demonstrates it. Paired-arm scripts must additionally show that sharing does not buy faster service (contingent responsiveness) and that an acknowledgment neither resets the substantive schedule nor counts as request fulfillment (two-stage replies).

## 7. World, knowledge and agent contracts

### 7.1 World schema and invariants

Store a world snapshot containing crate IDs/contents, hideout IDs, structured timeline events, rendered text, chronological ordinal, participant assignment, roles/cutoffs, seat holdings and initial file order. Model arrival, still-at, departure and transit as distinct event types; do not recover truth by taking the last place name in prose.

The generator validator must prove, across sampled seeds:

1. Eight crates, four distinct participant assignments, exactly two own-file and two team-dependent roles.
2. Own-file evidence supports the final location; initial team-dependent evidence lacks the final-location evidence, which exists in a teammate's holdings.
3. Hideout paths, timestamps and event text are internally consistent; departures/transit are not mistaken for confirmed arrivals.
4. Every assigned team-dependent crate can be solved through the implemented structured-request route, including the actual latest-entry selection logic.
5. Each unassigned crate has the intended useful participant records; scripted-request allocation and the never-requested subset are explicit.
6. No role/answer is exposed through record color, DOM attributes, API fields, seed, source maps or debug output. Staleness is task evidence; it is not an excuse to reveal the experimental role label.
7. In time-pressure profiles, the seat shown with the higher pressure rating is assigned independently of (a) which seat holds the participant's decisive team-dependent records, (b) which seat sends each scripted request, (c) acknowledgment/crosstalk seat selection, and (d) per-seat latency policy—or these are explicitly balanced by design. Report the joint distribution across seeded worlds. Seat-asymmetric reference behavior (for example, S3's 0.65/0.35 PM/JT acknowledgment-seat choice when no sender is specified) is documented as a historical difference and must not be correlated with rating assignment in any pilot profile.

S2/S3 describe teammates as privately assigned four crates but the shown generator primarily persists participant assignments and JT/PM record holdings. The production world must also define internally consistent four-crate JT/PM assignments, with permitted overlap, so requests and references to “my crate” match their roles. This is a specified completion of the model, not a claim that the demos already encode those assignments. Keep those assignments hidden from the participant as the instructions require.

### 7.2 Access boundaries

| Actor / component | May read | Must not read or reveal |
|---|---|---|
| Participant projection | Own initial records; delivered chat; records filed from teammate delivery (`added_from_team`); own answer drafts | Global truth, role labels, undisclosed seat holdings, future jobs or condition keys |
| Structured teammate request service | Requested crate and authorized JT/PM holdings | Other participants' sessions or fabricated records |
| Teammate social generator | Bounded delivered conversation, seat persona, minimal holdings metadata | Answer key; unshared location content; manipulation label unless required by a frozen script |
| Assistant | Snapshot of the participant file at accepted request time | Undelivered teammate entries, global truth, records that arrive after that snapshot |
| Scoring service | Frozen answers and world truth | Authority to revise submitted answers |
| Researcher audit | Authorized full session replay | Automatic unrestricted access to compensation identifiers |

Knowledge transitions are explicit and branching: **held → eligible → selected → committed delivery → rendered**, with a separate **added to file → eligible for AI context** branch. Store each separately. Since the 2026-10-03 O-4 change, a legitimately delivered teammate record joins the participant file on delivery (`file_membership.source = added_from_team`, plus a `record_added_to_file` event). There is no participant Add control. The command name `add_to_file` is still dispatched and is rejected (`add_to_file_removed`). File membership stays a separate stored fact from chat delivery: the assistant's snapshot is the participant file at accepted-request time, so a record delivered after that acceptance is not in that call. Availability, communication, and correct use remain different events, and none proves comprehension. Participant-provided guesses or pasted location text remain unverified input; they cannot create authoritative records or expand the assistant's source privileges.

### 7.3 Teammate behavior

Structured requests perform deterministic retrieval and return original catalog records. Keep request, record selection, scheduled response and actual delivery as separate events; S2's `team_share_received` can precede delayed rendering and is not sufficient evidence of receipt. No model is needed to decide which evidence is shared.

Social replies may be generated locally but cannot convey new crate locations or covertly change responsiveness. Prefer deterministic neutral fallbacks when generation is not the selected policy, and use them as the degraded path when it is. The demos' social prompt describes being distracted, and fallbacks include “bit swamped”; those can interfere with a pressure manipulation even if used in both arms. Audit and freeze a neutral next-pilot script; preserve original wording only in reference fixtures. Likewise, unprompted “I am stuck on…” lines count as possible sharing prompts. Either prohibit off-schedule requests or log their actual crate references before deriving proactive sharing.

**Fielded on `tp_review_first_v10` (D07).** Social generation is `model_with_validated_fallback` (`prompt.social_demo` v3, `voices.tp_demo` v2, `fallbacks.neutral` v5 as the degraded-path bank). Acknowledgments, crosstalk, assistant referral, unsolicited requests, and proactive sharing are off. Contents questions stay on deterministic retrieval. Displayed text is screened by `teammate_output_v7`, including `human_claim`: a generated line that says the teammate is human is not shown. The v9 `deterministic_only` setting is superseded for the live batch and remains the binding of the closed batch `prolific.live.n5`. Reference profiles keep their demo scripts. `pe_demo_v1` still runs the original voice, acknowledgement, and crosstalk policy; paid peer evaluation is not fielded.

Peer-evaluation voice differences must be held constant across evaluation/control. Acknowledgements and crosstalk require stored probability draws and parent request/share IDs. Do not generate actual ratings of the participant or negative personal feedback. The evaluation expectation and its debrief explanation are controlled material, not a real teammate-payment mechanism.

### 7.4 Assistant grounding and provider boundary

The participant explicitly prompts the assistant; this is not S1's passive AI panel. The backend chooses the authorized file snapshot and submits bounded context to the local model. It records record IDs/hashes, prompt/version, provider/model digest, timeout, raw response, validator decision and final display.

Require structured claims tied to source record IDs, crate and timeline event, rendered into safe text. Check that a claimed location belongs to the same crate and supported time/status. Reject unsupported certainty, wrong-crate borrowing, unsupported newer information, fabricated citations and answer-key-like output. Distinguish “latest location in your file” from “true current location.” A truth-coincident guess is still unsupported. Treat prompt and record text as data; adversarial instructions must not modify access rules.

When validation fails or a model times out, display a deterministic summary derived from the same authorized snapshot, with conservative handling of transit/departure. Record fallback reason and what actually appeared. Apply the same policy in all arms; a fallback session remains identifiable for sensitivity analyses. No silent switch to external providers or a different model. No live model change mid-session.

The browser must never call the demo relay. Network policy and application configuration should restrict inference to the approved [University] endpoint. S5's text says “Gemma 3”; the owner confirmed on 2026-09-28 that this is a typo in the application. The [University] OIT security review discussed Gemma 4 and approved the backup-host server itself for local LLM use, as a blanket review not tied to a particular model, noting that it is a single-operator host. Any locally hosted model on backup-host (for example Gemma 4 or Qwen) is therefore within the reviewed scope, and D05 was resolved by the 2026-10-01 IRB amendment: **Gemma 4 (or the latest released Gemma) via Ollama on backup-host**, frozen SHA-256-verified weights, per-message model/version logging, no participant text off-host except through the **university AI service** congestion fallback. Deployed model: `gemma4:26b` (switched from `gemma4:12b` 2026-10-03 while the GPU is dedicated to this study), digest-pinned per call — the pin is mandatory: a configured local endpoint without `LOCAL_MODEL_DIGEST` fails closed at settings validation, adapter construction, worker startup, and every generation call rather than serving unverified output (BA-40). S1 uses `gemma4:26b`; the estate GPU snapshot is historical. Record the exact model and digest per call (§7.4), and verify current capacity before deployment, without cold-loading or displacing another study's model as part of routine setup. Use provider doubles for most tests; real-provider acceptance is a separate requirement. Note that the deployed `openai`/`minimax` lab adapters are not university AI service endpoints — they are refused for all session traffic (preview/QA sessions can carry human tester text, so `APP_MODE` is not a sufficient boundary; the worker refuses a remote `MODEL_PROVIDER` at startup and `run_generation` refuses per job); an approved live fallback requires a dedicated university AI service adapter. The fielded adapter id is `local_openai_chat`, and `tp_review_first_v10` pins `local_openai_chat/gemma4:26b@sha256:5571076f3d70050487b26b341705799e0ab29b808164f90d20d4cf84f699d251`. That id is not the placeholder `univ_local_unresolved_d05`, which reference profiles such as `tp_demo_v1` and `pe_demo_v1` still name; those profiles allow only preview and qa. No university AI service adapter is implemented. The worker shares the Ollama network namespace and calls `http://127.0.0.1:11434`; the adapter refuses any non-loopback model URL. Prompt assembly in the running code is `grounded_prompts_v5`.

### 7.5 Per-call knowledge snapshots and bounded memory

Save an immutable `context_snapshot_id` for every generation attempt, with authorized record IDs/content hashes, acquisition and recipient-availability events, visible event cursor, prior delivered commitments, outstanding requests, prompt hash, and explicit causal parent/reply-to IDs. Model calls do not inherit reliable memory merely because they share a provider or persona.

The assistant's factual snapshot is fixed at **accepted request time**, as in §7.2. Grant or worker execution must not expand it with subsequently added records. Structured teammate retrieval also selects from its declared accepted-request snapshot. Social replies and crosstalk use a recorded grant-time visibility snapshot with their trigger event. Event order is not generation knowledge: a later-delivered reply may have been generated before an intervening share. Default to preserving that bounded reply; any regeneration policy requires a new linked attempt, renewed validation, and the original deadline.

Persist only auditable teammate state: permitted holdings metadata, delivered history, actual commitments, and unresolved requests. Do not claim a teammate remembers an unperformed action. Do not add private pre/post votes or unrestricted autonomous deliberation to mimic hidden-bench. JT, PM, and the assistant may share a model; agreement across personas is not independent corroboration. Preserve common record lineage across channels.

### 7.6 Typed evidence and claim ledger

Keep catalog records separate from claims about records. A factual assertion carries `claim_id`, `crate_id`, `location_id`, `event_type`, `evidence_time`, `source_record_ids`, `source_event_ids`, assertion/uncertainty status, and delivery/context linkage. Reject wrong-entity, negated-as-affirmative, departed-as-arrived, stale-as-current, and unsupported certainty transformations. Permitted nouns alone do not establish support. Apply the factual policy to social replies, acknowledgments, and crosstalk as well as assistant answers.

Do not salvage malformed choices by string containment: “Not The Marina” is not a valid Marina selection. Participant choices use exact enums; model parsing, repair, rejection, and fallback are distinct audit outcomes. A bounded fallback must use the original authorized snapshot and cannot invent a successful evidence transfer.

Derive an evidence-transfer ledger per record/claim: world existence, seat holding, eligibility, selection, durable delivery, render, file addition, assistant-context inclusion, explicit reference, and subsequent saved answer revision. Preserve bypassed stages and never-arrived states. Repetition by three channels can still represent one underlying record.

### 7.7 Agent fidelity across arms

Run identical synthetic participant scripts and world seeds in both arms of each study. Compare factual holdings/retrieval, scheduled responsiveness, failure/fallback policy, initiative/solicitation, verbosity/politeness/acknowledgment, cognitive scope, memory, and opportunity windows. Only approved pressure ratings or evaluation-expectation surfaces may differ. Social generators cannot independently choose new requests, urgency, evaluations, praise/criticism, or task-solving advice.

Use multiple realizations for stochastic wording and inspect distributions; paired tests establish policy equivalence, not byte-identical prose. Live humans may behave differently because of treatment, producing legitimate downstream differences. Do not interpret those differences alone as policy violations. Review off-schedule solicitations before labeling shares proactive; inspect all displayed generated outputs and fallbacks in the initial pilot before expansion.

## 8. Persistence and API contracts

### 8.1 Minimum relational model

| Table / aggregate | Key content and constraints |
|---|---|
| `experiment_manifest`, `batch` | Immutable content and policy hashes; mode, active/recruitment flags; approved launch configuration. |
| `participant`, `session` | Random research PID; pinned manifest; unique enrollment; state/revision, condition, world ID, terminal reason. |
| `identity.platform_link` | Platform PID/study/session identifiers, separate role and retention lifecycle; never part of general research exports. |
| `allocation` | Study/batch/block/slot/arm and unique reservation; audit randomization without exposing seeds to clients. |
| `consent_event`, `stage_transition`, `clock_interval` | Immutable consent and state history, UTC timestamps, pause reasons. |
| `world_snapshot`, `record`, `seat_holding` | Frozen truth/timeline/assignments; referential integrity within session/world. |
| `file_membership` | Record provenance, acquisition source and time; unique session/record. |
| `command_receipt`, `event`, `outbox`, `scheduled_job` | Idempotency, ordered durable events, recoverable delivery, leases. |
| `message`, `request`, `share` | Actor, channel, parent/thread, crate and record IDs; accepted/scheduled/delivered distinctions. |
| `knowledge_snapshot`, `agent_state` | Immutable per-call visibility/record manifests; actual commitments and outstanding requests, scoped to seat/session. |
| `claim`, `claim_source` | Typed assertions, evidence spans/status, catalog record and event lineage; separate from truth records. |
| `model_call`, `displayed_output` | Input snapshot, attempt/parent IDs, per-call provider/model/parameters, raw restricted response, validation/fallback and actual delivery linkage. |
| `request_episode`, `delivery_receipt` | Accepted/granted/resolved/cancelled/expired request lifecycle; timing components, recipient receive/render status and replay evidence. |
| `coding_run`, `coding_result`, `validation_gate` | Immutable corpus/rubric/prompt/model/version keys, explicit coverage/status, metric denominators, adjudication and supersession. |
| `answer_revision`, `answer_final`, `confidence_response` | Server-accepted revision history; one final per session/crate; confidence distinct from answer. |
| `manipulation_exposure`, `item_response` | Scheduled/rendered/acknowledged exposure, own rating, assigned seat values, instrument/item version and missing reasons. |
| `result`, `payment_entitlement`, `completion_receipt` | Immutable scoring, integer-cent bonus, technical/withdrawal status and exactly one logical completion. |
| `admin_audit`, `data_use_choice`, `redaction_review` | Operator interventions, withdrawal choice and export eligibility, scrubbed copies with provenance. |

The current schema also has `app.participant_display` (display initials matching `^[A-Z]{1,3}$`, excluded from research exports), `app.session_credential`, `app.draw_log`, and `app.incident_interval`. `platform_link` is created in schema `identity`, so the qualified name is `identity.platform_link`. `coding_run`, `coding_result`, and `validation_gate` exist; the model coding path is dormant and is not a substitute for the §9.4 human gate.

Use UTC `timestamptz`, explicit foreign keys and check constraints; JSONB for versioned payloads, not as a substitute for relational ownership constraints. Every session-owned lookup checks session membership. Research-runtime credentials cannot read the unrestricted platform-link table. Migrations run as a separate privileged role and are tested from an empty database and the prior release.

### 8.2 Command and event interface

Proposed public endpoints below are relative to `/investigators/api`:

- `POST /entry` — validate enrollment/preview, issue session credential, return authorized state.
- `GET /session` — current projection, state revision, server clock and latest event cursor.
- `GET /events?after=<seq>` — replay only this session's events; polling equivalent required.
- `GET /events/stream` — SSE for the same per-session cursor when the browser has `EventSource`. The fielded client prefers this and falls back to the poll. WebSockets are not used.
- `POST /commands` — typed commands accepted by the dispatcher: `consent`, `set_initials`, `training_step`, `ready`, `withdraw`, `answer_update`, `pressure_response`, `pressure_skip`, `item_response`, `item_skip`, `debrief_ack`, `proceed`, `data_use_choice`, `resume`, `takeover`, `teammate_request`, `record_share`, `social_reply`, `assistant_request`, and `greeting`. `add_to_file` is recognized and rejected (`add_to_file_removed`); teammate records are filed on delivery. `resume` and `takeover` are the writer-lease exceptions; other mutations check `x-writer-id`.
- `POST /telemetry` — bounded batches of client render/focus/item timing; not authoritative answers, condition or score.
- `POST /complete` — idempotent completion after required procedural stages and durable writes; return outcome receipt/authorized platform handoff.

A command includes `command_id`, `expected_revision`, `kind`, bounded payload and optional client monotonic timestamp. The server supplies actor/session, acceptance time, condition and state. Return committed revision, accepted/rejected outcome and canonical projection or event cursor. Distinguish malformed input (400/422), unauthorized access (401/403), stale state (409), quota (429) and unavailable persistence (503). Never show Saved when the commit failed.

A durable event includes `event_id`, monotonic per-session `seq`, schema version, session ID, stage, actor/role, type, command/parent IDs, server time, active milliseconds and payload. Client observations additionally contain render/monotonic times and receipt time. Idempotent replay preserves sequence; a cursor gap triggers resync. A server event is not proof that pixels were seen; expose missing render acknowledgements in audit.

Use one active writer lease per session to handle duplicate tabs. A second tab is read-only or performs an explicit authenticated takeover; it cannot run a second timer or duplicate jobs. Bounded local buffering can retry telemetry/draft commands, but expired task answers are rejected under §6. Credential loss requires a controlled recovery process, not PID-only resume.

### 8.3 Durable completion and participant projections

Commit each event and its delivery intent atomically before dispatch. Persistence failure cannot produce Saved or Completed. Completion requires a server-checked reconciliation of required stage transitions, final answers/missing states, exposure statuses, data-use choice, and logical result/receipt uniqueness. Missing telemetry is recorded as missing, not fabricated to satisfy completeness; missing required durable task data is a technical failure.

Participant payloads use nested allowlists scoped to session and recipient. Exclude private holdings, future events, condition/configuration keys, sampled delay parameters, agent implementation labels, and provider diagnostics. A hidden frontend label is not access control. Research-only replay can join the protected context and timing records.

Receipts and event streams do not consume the authoritative ledger. A disconnected browser cannot cancel a job, and reconnect preserves original event identity and commit time. Separately record replay receipt/render time. Monitor real heartbeat arrival through the proxy during long holds, including silence after the last frame; a generated list of heartbeat timestamps is not a transport test.

## 9. Measures, exports and Qualtrics continuity

### 9.1 Canonical measures

| Measure | Definition |
|---|---|
| Structured teammate seeking | Count accepted participant `teammate_request` commands; also export unique crates and requests with delivered replies. Keep separate from agent-originated requests. |
| Free-text seeking | Preserve social replies and code task-relevant questions separately under a versioned coding rubric; do not pretend the structured button captures all seeking. |
| AI use | Count accepted participant prompts, with route (`typed`/picker), target crate, response/fallback/delivery status. Retries do not add prompts. |
| Sharing | Unique participant record-share actions and total valid share events; distinguish prompted and unprompted, own/unassigned crate, record utility and recipient. |
| Proactive sharing | Primary strict definition: useful unassigned-crate evidence shared before any delivered agent request for that crate, including crosstalk/social solicitation. Also retain a legacy-compatible indicator corresponding to the demo rule. |
| Information acquisition | Records delivered by teammates, records rendered, records added, and relevant evidence included in subsequent AI calls, each separately. |
| Accuracy | Correct assigned crates / 4, own-file correct / 2, team-dependent correct / 2. Missing answers are retained as missing and count as incorrect for the displayed total; report answered-only accuracy separately. |
| Confidence | Explicit 0–100 response per answered crate, with missing/skipped/not-applicable reasons. Never use slider default 50 as data. |
| Seeking allocation | AI share of seeking = AI prompts / (AI prompts + structured teammate requests), undefined when denominator is zero; label as structured-channel measure. |
| Timing | First accepted seeking and first sharing, active-time latency, opportunity duration, censoring at task end, and request-to-delivery latency. |
| Manipulation checks | Exact recall plus perceived pressure/evaluation, expected responsiveness, felt rushed, concern, usefulness, and suspicion; distinct constructs, not one success score. |

Time-pressure windows use **actual reveal/resume events**, not merely nominal second 60 or `checkin_open`. Own pressure is collected before others' values are revealed and cannot serve as a post-manipulation effect measure. Export pre-check-in, paused interval and post-resume activity and opportunity durations. Missing exposure stays missing; do not label a non-rendered manipulation as successful exposure. In review-first profiles there are structurally zero pre-exposure help opportunities, not evidence of behavioral reluctance.

Peer-evaluation exposure records intro, reminder, strip and system-message rendering. Peer ratings are obtained in both arms after task completion. Preserve the distinction between anticipating evaluation and giving ratings afterward. Suspicion questions occur before debrief; do not expose agent labels in their lead-in. Freeze final item wording/order against the approved selected instrument; S4's labels and the demo question arrays are not a complete verified survey export.

### 9.2 Export products

Provide UTF-8 CSV plus JSON schemas/codebook and a reproducible export manifest: source snapshot time, filters, row counts, schema/metric versions and checksums. Exports are generated from committed data at a consistent snapshot.

- Participant-wide table keyed by research PID/session with study, phase, arm, all version stamps, exposure/completion/technical status, behavioral summaries and item responses.
- Crate-long table with assignment/role, initial cutoff, truth, final answer, correctness, confidence and acquisition history. Truth appears only in authorized research products.
- Event/message/request/share/response long tables with ordered IDs and dual clocks.
- Model audit table joining authorized sources, attempts, validation, fallback and display/render records; restricted raw text separately.
- Identity-free sharing bundle with reviewed redactions; unreviewed free text withheld.
- Restricted payment export with platform IDs, base/outcome handling and bonus cents converted to currency. Preview/QA and unapproved payment policies are ineligible.

The researcher dashboard Exports panel can download one study table at a time via `POST /admin/exports/table`. That route is researcher-only, CSRF-gated, sends `Cache-Control: no-store`, and reads one repeatable-read snapshot. It applies the same free-text withholding as the sharing bundle, omits sessions whose latest data-use choice is missing or `use_no_free_text` from text-bearing tables, strips `session.seed_hex`, and returns 422 for an unknown table name, a malformed body, or an unknown scope field. It does not publish payment or identity tables and does not replace the archival export manifest. Dashboard audit DC-01–DC-03 recorded an earlier passthrough that skipped these checks; the route in the current tree performs them. The redaction and data-use boundaries in this section remain in force either way.

Reproduce legacy fields where meanings match: `inv_condition`, `inv_team_requests`, `inv_shares`, `inv_proactive_shares`, `inv_replies`, `inv_prompts_used`, `inv_correct_total`, `inv_correct_teamdep`, `inv_correct_ownfile`, `inv_assignment`, `inv_roles`, `inv_answers`, `inv_confidence`, pressure and peer-rating fields. Audit the exact original `INV_FINISH` keys when implementing the crosswalk; compatibility must not invent a legacy field definition. Add study and version identifiers because condition names alone are ambiguous. In both demos, `counters.first` is set by either a request or a share, yet exported as `inv_first_request_sec`; do not reuse that value as a true request-only latency. Export separately defined first-request and first-action times, and retain a clearly named legacy-compatible value if needed. Compare derived aggregates against captured fixture payloads and document intended differences, especially actual delivery, fallback flags and timing windows.

Historical Qualtrics imports, if later supplied, enter a separate source batch with original variable names, availability flags and import checksums. Never fabricate world seeds, render events or item timestamps absent from the old data. The N=30 narrative report is not a substitute for raw participant records. Do not pool historical and new pilot outcomes without identifying generator, manipulation, scripting, timing and measurement changes.

### 9.3 Evidence transfer, request episodes, and opportunity denominators

Export eligible record/crate opportunities, requests, first actual delivery/render/addition/context inclusion, and never-arrived or censored states. Never replace missing arrival time with zero. Keep denominators explicit: participants, crates, records, requests, or coded assertions.

| Additional process measure | Definition and limit |
|---|---|
| Decisive-evidence delivery | Assigned team-dependent crates with a qualifying final-location record delivered before closure / two assigned team-dependent crates; separately report requested/rendered subsets. |
| Request fulfillment | Eligible accepted requests receiving their policy-defined substantive response before closure / eligible accepted requests; retain time remaining and expired requests. |
| Correction/deterioration | Wrong-to-correct and correct-to-wrong saved revisions conditional on a prior nonmissing answer; retain unchanged and missing categories. Not proof of conformity or consensus. |
| Acquisition-to-use | Revisions following relevant delivery, file addition, or assistant citation; descriptive process associations, not identified mediation. |
| Redundancy | Delivery count versus distinct relevant records/claims; repeated obsolete evidence is not new current evidence. |
| Unsupported assertion | Unsupported assertions / coded factual assertions, with delivery counts, coding coverage, and undefined denominators explicit. |
| Fidelity | Empty, fallback, late/suppressed, failed, and off-schedule solicitation rates by arm, source, model, and policy version. |

Requests are episodes with target source, crate/topic, accepted/granted times, pending interval, acknowledgment, substantive response, resolution/cancellation, and subsequent actions. Asking another source while one request is pending may be hedging; asking afterward may be verification. Use explicit IDs where available and uncertainty-tagged offline linkage for free text. Prompt counts alone do not establish substitution.

Report wall time, active time, channel-open opportunity time, and post-manipulation opportunity time separately. Compute unions of overlapping pending intervals and blocked-action intervals; do not subtract summed waits from task time. Productive work may continue while requests are pending. For TP, exposure requires actual reveal evidence and action opportunity resumes when the channel opens; for review-first there is no pre-exposure help opportunity.

Do not transplant all-agent accuracy, majority vote, or consensus outcomes into a one-human experiment. Keep technical validity and behavioral outcomes separate. A control that could not be found or a draft lost by the UI is an apparatus problem; failure to share available evidence may be an outcome. Preserve assigned-condition accounting and prespecified incident/exclusion rules. Post-treatment asking, latency, or exposure can be mediators; do not automatically adjust them out of the primary treatment estimate.

### 9.4 Offline semantic coding and validation

Exact authenticated event/record IDs govern structured behaviors. Semantic coding is a separate offline pipeline for free text, not a substitute for online authorization or output validation. Generic keyword overlap that drops crate numbers, negation, or chronology cannot define useful evidence acquisition.

Use two stages: (1) code explicit statements with spans, questions, hypotheses, negation, inference, and ambiguity; (2) derive first introduction, self-repetition, other-source repetition, and recipient-specific novelty from visibility history. A single message cannot establish historical novelty. Do not credit a catalog fact merely because a privileged coder can infer it from incomplete prose.

Keep stance (endorse/question/contradict/uncertain/no stance) separate from repetition. “Thanks” is acknowledgment, not evidence; “Crate 8 arrived at Marina” cannot match crate 3; departure is not arrival; “JT says Marina, but that record is old” is attribution plus temporal challenge; multiple channels repeating one record retain one lineage. Unsolicited requests such as “Anything useful for my crate 3?” change proactive-sharing eligibility.

Human coders label before seeing model predictions; at least two independently code a meaningful subset with adjudication and category-specific agreement. Freeze rubric, development versus held-out corpus, and gate definition before tuning. Cover both studies/arms, generated/fallback text, quotes, wrong entities, negation, transit/arrival, uncertainty, late responses, and solicitation. Blind arms where practical and record unavoidable unmasking. Report precision/recall, errors, denominators, and coverage by construct and condition on the same real held-out corpus; report synthetic stress cases separately. Coder disagreement is not an error bar, and a model judge is not ground truth.

Each immutable `coding_run_id` binds input snapshot, fixture set, rubric, prompt, model/digest/parameters, and code/validator versions. Results carry `coded`, `not_coded`, `failed`, or `abstained`; an empty coded set differs from missing coverage. No mixed-run export by default. Corrections create superseding records rather than overwrite historical results.

A server-computed immutable gate binds exact metrics, denominators, coverage, thresholds, and reviewer identities. Missing evidence cannot PASS; any override is explicitly an override, not scientific certification. Set construct-specific thresholds before analytic use rather than copying hidden-bench numbers. A working coding UI or a stored judge result does not establish validity.

### 9.5 Additional export and replay products

Add evidence-transfer/claim lineage, request episodes and pending overlaps, answer-revision transitions, recipient exposure and timing-component tables, context-snapshot manifests, and immutable coding/gate products. Include measurement status, censoring/eligibility, fallback/suppression reasons, and compatibility keys. Protected raw model/coding input is separate from blinded annotation exports; preserve necessary meaning while removing arm/timing/identity details where possible. Validate projections so missing coverage or stripped metadata cannot silently become a zero-valued measure.

Every aggregate must recompute from the selected event and coding snapshots with declared versions. Full source truth in researcher exports does not confer permission to send it to an external judge. Existing approved local-provider and retention boundaries apply to offline coding too.

## 10. Participant workbench and completion

Keep case file, team chat, AI assistant and four answer controls simultaneously usable on supported desktop/laptop screens. Do not replace the workbench with mutually exclusive tabs that change access costs. The fielded layout (adopted 2026-10-01) is a two-row grid — case file left spanning both rows, team chat upper-right, assistant lower-right, answers along the bottom — with per-pane tinted styling that distinguishes the three channels without implying record roles. After the 2026-10-04 lab walkthrough (fixed pane sizes cramped the chat/assistant column), both split positions are participant-adjustable: accessible drag/keyboard separators resize the case-file↔right-column and chat↔assistant boundaries within a 25–75% range, session-only (no persistence, no participant-identifiable state); below 900 px the panes still stack without handles. Training uses the same spatial model and must demonstrate every analytic interaction and equally discoverable routes to AI and teammates; in the fielded UI (2026-10-05 UX round 4) orientation is a paged walkthrough — a background page collecting display initials, paged instruction copy, then a workbench-mirror practice page that gates Continue on one required share while team chat and the assistant are shown as labelled previews (the same pane layout and routes the task uses) rather than presenting every control at once. Preserve neutral record styling, usable font sizes and a stable participant-controlled record order (reorder via drag, click-to-grab/drop on the grip, or keyboard arrows — all equivalents with the same move semantics; the piloted UI has no sort selector); no role-based color hints.

Drag/drop must have keyboard and click alternatives. Use visible focus, accessible labels, adequate contrast, manageable independent scroll areas and dialogs with focus containment/return. Do not force-scroll the file when an unrelated chat arrives. On stage transitions, scroll the new page to its beginning once; do not use persistent scroll-reset loops. A reply arriving during reading should not steal focus. Reject obsolete state revisions, preserve dirty drafts across focus changes and polling, and reconcile them only with an explicit server acknowledgment or rejection. Repainting chat must not reset reading position or erase another field when focus moves. Test at 1366×768 and 1920×1080 and at 200% zoom; document any genuinely unsupported viewport before enrollment.

During saving/disconnection, clearly distinguish draft, saving, saved and rejected states. Reconnected users see the same case order/filter where feasible, delivered transcript, saved answers and remaining clock. Condition names, investigator controls, raw model diagnostics and implementation vocabulary are absent before debrief.

At timeout, freeze answers, collect confidence and study-specific post-task items, then debrief. Score/answer-key feedback comes after post-task measures by proposed default to avoid changing those responses; freeze this order in the selected materials. The debrief explains automated teammates and the actual manipulation, and for evaluation clarifies that no other people rated the participant. The post-debrief data-use surface shows study contact information and records the approved `use_all` default rather than an in-app deletion choice (participant-facing deletion options removed in the 2026-10-01 UX round; withdrawal deletion remains an audited operator disposition under §11). Payment eligibility is unchanged by data-use state as described in S5. The fielded rule is `payment.s5_base_only` v3: a $4.00 base and no bonus. `completed` and `technical_stop` are owed that base; `abandoned`, `withdrawn`, and `declined` forfeit it. Completion codes require committed outcome/data-use state and remain retrievable on reload; debrief must not auto-dismiss on a short timer.

## 11. Researcher controls, privacy and operations

The admin allowlist is `admin1@university.edu` ([Principal Investigator]) and `admin2@university.edu` ([Co-Investigator]), confirmed by the owner on 2026-09-28 against the S5 personnel; S1's Rylee Linhardt entry is not copied. Under the §3.1 ingress layout, Apache strips identity headers but does not enforce SSO on admin paths, so the application must authenticate every admin UI and API request itself, using `ADMIN_PASSWORD` from `./.env` as `/pcs/` and `/dct/` do, and never trust forwarded identity headers. Separate researcher, operator and compensation permissions.

Admin supports:

- Signed previews for all eight profile/arm combinations (§4.1), fixed seed, faithful timing and separately marked accelerated QA timing.
- Enrollment pause/resume per batch; immutable manifest comparison and launch validation.
- Session monitor for stage, remaining active time, exposure, saved-answer count, reconnects, errors and terminal status.
- Replay showing **what the participant could see at each point**, alongside restricted truth/holdings views, source-bound model outputs and job delivery lag.
- Balance reports for assigned/exposed/completed sessions and world difficulty by arm; latency/fallback differences by arm and batch.
- Audited technical-stop and support actions. Operators cannot silently edit condition, truth or frozen answers.
- Export/redaction/withdrawal queues, payment reconciliation and identity purge with dry-run and scope validation.
- Descriptive researcher dashboard, in a tab to the right of documentation: overview, exports, readiness, timing, instruments, item analytics, power inputs, arm contrasts, and findings. Those reads are researcher-only, send `Cache-Control: no-store`, and use one repeatable-read snapshot. The exports grid is the §9.2 per-table download. Inference and minimum-detectable-effect cells stay unconfigured until D12, D14, and O-13. The panel has no charting library and does not refresh live.

Store research data under random IDs. Treat initials as potentially identifying; preserve the approved participant display convention but omit initials from shared exports (2026-10-02 UX round: initials live in the export-excluded `participant_display` table, never in command results, event payloads or admin views, and are deleted by the tombstone's mutable purge). Keep platform IDs, demographics linkage, tokens and raw request URLs out of routine logs. Do not store full keystroke streams: record item focus/blur/change/submission, lengths/counts and required task text, not unrelated typing. Sanitize participant/model text; do not inject raw model HTML. Bound text sizes, stream rates and model concurrency; enforce CSRF/origin/session checks and cross-session authorization.

S5 specifies university-managed storage/backups and describes platform-ID deletion after review/payment within 48 hours of study closing. Operationalize that approved schedule with a separate identity retention policy, payment reconciliation, audited purge and treatment of identity-bearing backups/snapshots. Do not copy S1's 30-day backup retention as if it were approved here. Research retention, withdrawal deletion and backup expiration must be explicitly documented; restore procedures must reapply deletion/withdrawal tombstones before making restored data usable.

Back up committed research data and manifests, restrict access, verify checksums, monitor backup age and rehearse restoration into a scratch database. Proposed durability target: no loss of acknowledged commands on application restart; database-host failure recovery target RPO ≤15 minutes and RTO ≤60 minutes, requiring WAL archiving or equivalent beyond nightly dumps. Demonstrate the chosen backup arrangement before promising those targets.

Destructive tests require a dedicated `INVESTIGATORS_TEST_DATABASE_URL`, explicit destructive-test opt-in and a guard proving it differs from every live DSN. Never reset a live/shared schema. Cleanup defaults to preview/QA scope and dry-run; live deletion requires approved scope, export backups and audit. Honor the project's prior simulation data-loss lesson.

Before deployment, verify routes/ports and current GPU capacity, configure least-privilege services, apply migrations, health/readiness checks, backup registration and trusted ingress. Update the canonical [`./estate-docs/SYSTEM_MAP.md`](./estate-docs/SYSTEM_MAP.md) (the `fow_ssl/SYSTEM_MAP.md` copy is regenerated) and `AGENTS.supplement.md` only when the implementation creates real boundaries. Ingress edits go to the live `/etc/httpd/conf.d/investigators.conf` through the operator's sudo workflow; the local `vhost_investigators.conf` is a refreshed snapshot, so the Investigators snippet file remains the source of truth. Use build/version health stamps. Drain new enrollment before updates; running sessions remain on a compatible pinned version or the deployment waits. A rollback must preserve schema/data compatibility and active-session replay, not merely restart an older image.

### 11.1 Release evidence and current operational truth

Bind each launch checklist to the actual build, schema, selected protocol/profile, provider, and material versions. Preserve test output, fixture hashes, human adjudication, real-provider timing, browser traces, gate decisions, and reviewer identities as immutable release artifacts. Separate technical conformance, measurement validity, and manipulation success; passing one does not imply the others.

Researcher replay must show the participant projection, evidence branches, per-call context, scheduled versus actual timing, failed/suppressed outputs, and coding coverage. Launch/admin summaries must expose unrun checks rather than an unconditional “complete.” Never use a historical runbook's completion language, generated manifest check, synthetic heartbeat list, or another project's passing tests as deployment certification.

## 12. Verification and acceptance gates

| Gate | Required evidence |
|---|---|
| G1 — Content and configuration | Both study manifests validate; all eight profile/arm combinations preview; stable hashes; executable policy matches materialized configuration and artifact hashes; unsupported switches rejected; only intended arm differences; no live condition override; unresolved recruitment decisions identified. |
| G2 — World validity | Property tests over at least 1,000 seeded worlds and hand-audited fixtures prove §7 invariants, reachability of team-dependent answers, and no participant/API truth leakage. |
| G3 — Behavior fidelity | Golden replays for both demo-derived profiles, plus documented diffs for v2; seeded request/voice/ack/crosstalk timing; correct file-membership boundary (fielded path: automatic `added_from_team` on delivery, `add_to_file` rejected); paired-arm fidelity matrix (§7.7), bounded memory and unsolicited-request audit. |
| G4 — Timing and concurrency | Tests at 59.999/60 seconds and 359.999/360 seconds; pause/reveal/resume, sleep/background tabs, disconnect and restart; duplicate/reordered commands, two tabs, job lease recovery and simultaneous finalize. Exactly one result and completion receipt; all §12.1 timing traces pass with actual commit/exposure timestamps. |
| G5 — AI/source integrity | Wrong-crate/location, chronology, synonym, prompt-injection, fabricated citation, empty/error/timeout, newly-added-record races and late output fixtures. Typed entity/time/negation and context-race checks; no inaccessible source or unsupported task fact in displayed fixture outputs. Human-reviewed held-out prompts and real-model checks remain separate from fixture tests. |
| G6 — Full browser path | Chromium and Firefox on supported desktops; every arm through consent/training/task/confidence/post/debrief/withdrawal/completion; item skip, refresh at every stage, keyboard alternatives and reconnect. Saved data and code routing verified against DB. |
| G7 — Analysis/export integrity | Wide summaries recompute exactly from long events; denominators/missingness correct; legacy crosswalk fixture reconciliation; privacy and payment export boundaries tested; CSV formula injection neutralized; evidence branches, never-arrived/censored states, request episodes, and correction/deterioration recompute. Semantic outcomes additionally require §12.2. |
| G8 — Load and failure | At least 40 concurrent simulated sessions through real six-minute scheduling with a model double, then a separately bounded run using the approved local provider. No lost/duplicate accepted actions. Proposed p95 ordinary command latency <500 ms and server scheduled-delivery lag <1 s outside deliberate pauses. |
| G9 — Operations/security | Cross-session denial, admin spoof denial (forged identity headers ignored; admin UI/API reject unauthenticated requests through the live proxy), preview/live isolation, DB role tests, worker restart, provider outage, backup/restore/deletion rehearsal and health checks. Test unavailable persistence: no false Saved/Completed claims. |
| G10 — Pilot readiness | Research-team material walkthrough, resolved §13 recruitment decisions, exact instrument/payment/debrief freeze, real browser/provider results, and complete audit of initial pilot displayed AI outputs before expansion. |

Model latency must be reported separately from imposed social delay. Before recruitment, set and test the provider capacity limit and response-time budget against the six-minute task; p95 beyond the configured fallback deadline is failure, not a reason to extend selected participants' clocks. Pause **new enrollment** on any confirmed cross-session/hidden-record leak or data-loss incident. Proposed warning thresholds: >5% generation fallback over the last 100 calls or >5% technical stops over the last 20 sessions; review causes before expansion. These are operating thresholds, not automatic scientific exclusions.

Use PostgreSQL for locking/migration/role tests; SQLite-only success does not establish production correctness. A load run against a fake model does not verify local GPU capacity, and API tests do not replace browser manipulation-render evidence. Document unrun/blocked checks explicitly.

### 12.1 Required timing and recovery traces

| Case | Required evidence |
|---|---|
| Generation 3 s / scheduled wait 9 s; generation 12 s / wait 9 s | Eligibility at 9 and 12 seconds respectively; readiness overrun and later commit lag distinct. |
| Browser absent until 11 s after due | Scheduler commits independently; replay records late receipt/render without backdating exposure. |
| TP grant 57, due 66, pause at 60 active seconds | No overlay delivery; due six active seconds after resume. PE equivalent stays uninterrupted. |
| Deadline crossing during generation or commit | One transactional answer freeze; no new task message may commit at/after active 360, and the closed browser task suppresses late arriving content. |
| Duplicate command, concurrent tabs, retries | One accepted request and at most one substantive event per logical job. |
| Restart mid-wait or check-in | Original state, draws, pause ledger, context, and deadlines survive. |
| Crossed ready/scheduled order; independent injection | Declared ordering demonstrated; future injections do not move early and due transitions do not wait for model work. |
| Length and modifier combinations | Realized distributions, typing/indicator timing, rejection and suppression rates match the supported policy. |
| Hidden tab, reconnect, slow browser, stale snapshot | Authoritative server clock; separate receive/render/visibility records; no draft loss or state rewind. |
| Provider timeout, empty or malformed result | Prespecified terminal/fallback state, no late second bubble, responsive deadline processing. |
| Share/addition during generation | Snapshot remains bounded; later facts cannot be retroactively attributed to earlier generation. |
| Long transport hold under full-duration load | Actual proxy heartbeat and client arrival traces; no silent tail stall, lost accepted actions, or concealed scheduling lag. |

Measure command latency, readiness, scheduled-delivery lag, and browser exposure separately. One-second polling does not prove subsecond exposure. A fake-clock or fake-provider pass does not establish production-clock, GPU, proxy, browser, or human plausibility performance.

### 12.2 Measurement and pilot-audit gates

Before using semantic outcomes, require the §9.4 frozen rubric, held-out human gold data, explicit coverage, category/condition metrics, immutable gate evidence, and research-selected thresholds. Missing metrics cannot PASS. Because the held-out corpus must be **real** participant text, it is drawn from the initial pilot (excluding withdrawn participants and subject to the approved data-use terms); pre-pilot work may build the pipeline and a development set from synthetic or staff-authored cases, reported separately. Semantic outcomes for the initial pilot therefore remain unavailable until this gate passes. This measurement gate is additional to G7's exact-event arithmetic; it must not block ordinary exact-record analysis solely because an optional semantic measure is unavailable.

Use a deterministic chronology oracle to compare initial participant evidence, evidence after adding a decisive teammate record, and fully authorized evidence. Own-file items must be solvable initially and team-dependent items via the specified route. Model screening alone cannot establish human task validity.

Before expansion, inspect every displayed generated response and fallback in the initial pilot with its input snapshot and actual delivery time. Review all apparent proactive shares for earlier solicitation and all incident cases for lost evidence/opportunity. Store findings, fixes, and the next pinned release without retroactively changing assigned conditions or inventing exclusions after seeing arm differences.

Zero known hidden-record leaks and unsupported/wrong-crate factual displays in the acceptance fixture suite is a release criterion, not a claim of zero population error. Technical conformance, human manipulation checks, and semantic measurement validity remain separately reported.

## 13. Research decisions and protocol reconciliation

These decisions do not prevent building the shared engine or researcher previews. They prevent treating a preview as a finalized paid-pilot configuration. The supplied protocol is one artifact; a later amendment or institutional clarification may resolve a mismatch. This table records discrepancies for the research team, not a legal determination.

| Decision | Evidence / unresolved point | Proposed treatment |
|---|---|---|
| Pressure timing | S4 proposes 60-second independent review; S2 currently allows interaction before second 60. | **Selected 2026-10-07 (D01):** review-first, fielded as `tp_review_first_v10` on batch `prolific.live.n5.v10`. `tp_demo_v1` remains the baseline reference. v2 is the original proposal, not the live binding. |
| Pressure values | Reported pilot used high 5/6; demo now high 6/7. | **Selected 2026-10-07 (D02):** high 6/7 and low 2/3 on `tp_review_first_v10`, with the higher value assigned to JT or PM by an independent fair coin (`rng_stream: ratings`). |
| Evaluation manipulation | S5 repeatedly says participants are not evaluated and receive no personal feedback; S3 tells them teammates will rate them. The 2026-10-01 amendment approved generative agents/assistant while reaffirming "participants are not evaluated"; the residual question — the eval-arm premise and revealing it to control participants post-task (INV-0910 audit) — stays open. | Research team confirms the remaining premise/debrief reconciliation before paid evaluation recruitment; recorded under D03. Do not infer authorization from the filename alone. |
| Bonus | The TP demo promises $0.25/crate; the revised PE demo (2026-10-01) contains no bonus copy; S5 lists $4 online base pay and does not select bonus payments. | **Selected 2026-10-07 (D04):** $4.00 base, no correctness bonus, ratings-contingent pay off (`payment.s5_base_only` v3, activatable). `completed` and `technical_stop` are owed the base. `abandoned`, `withdrawn`, and `declined` forfeit it (`withdrawal_payment: forfeits_base`, consent.reference v4). The data-use choice does not change that entitlement. |
| Model and transmission | S5's text says university-local “Gemma 3” (owner-confirmed typo; the OIT security review covered Gemma 4 and approved the server as model-agnostic); S1 uses `gemma4:26b`; demos call an external Render relay. | **Resolved 2026-10-01 by IRB amendment:** Gemma 4 (or latest released Gemma) self-hosted on backup-host via Ollama, frozen SHA-256-verified weights, per-message logging; university AI service is the sole authorized congestion fallback. Deployed `gemma4:26b` (switched from `gemma4:12b` 2026-10-03). No relay reuse; the `openai`/`minimax` lab adapters are not the approved fallback and are refused for all session traffic — offline fixture-comparison adapters only. |
| Optional answers | Demos gate some ratings; S5 permits skipping questions without payment loss. | Include explicit skip/missing states and avoid response-dependent completion payment gates. On `tp_review_first_v10` the check-in own rating is skippable (`pressure_skip`); instrument pages record `item_skip` for untouched items when the page is submitted. |
| Script harmonization | The original S3 adds voice/thanks/crosstalk (removed in the 2026-10-01 revision, which uses generated replies plus canned fallback); S2 includes busy/distracted social language. | **Selected 2026-10-07 (D07) for the time-pressure pilot:** `model_with_validated_fallback`, acknowledgments and crosstalk off, assistant referral off, presence cues off, proactive sharing off. Contents questions stay deterministic. Validator `teammate_output_v7`. The off-task redirect line remains in `grounded_prompts_v5` (recorded on `model_call` and in the compatibility key). Peer-evaluation voice policy stays behind D03; `pe_demo_v1` still runs the original acknowledgement and crosstalk policy. |
| Peer ratings in controls | Both S3 arms rate JT/PM after the task; S4 narrative emphasizes evaluation arm but reports ratings in both. | Preserve both-arm collection unless selected instrument changes; distinguish anticipation from post-task rating. |
| Platform / repeat eligibility | S5 allows Prolific, Connect and possible lab recruitment; S1 is Prolific-only. | Configure platform adapters and exclusion policy per batch; no borrowed S1 sample targets or code strings. |
| Withdrawal and retention | S5 describes withdrawal after disclosure with pay unaffected, but debrief appendix does not spell out the UI mechanism. | Participant-facing surface narrowed to contact information recording the `use_all` default (2026-10-01 UX round); withdrawal deletion remains an audited operator disposition. Reconcile the approved withdrawal/retention wording and process under D09 — whether participants must be offered an in-app deletion route is part of that residual. The payment consequence of leaving early is the D04 rule in the bonus row; that selection does not close D09. |
| Timing policy and extensions | Triad offers additional factors; demo timing mechanisms differ by response class. | **Selected 2026-10-07 (D10):** `tp_review_first_v10` uses `concurrent` for every response class, the reference clamped-lognormal parameters, a 120-wall-second check-in inactivity cap with outcome `technical_stop/checkin_incomplete`, and `warning.one_minute_left` v2. Reference profiles keep the three demo mechanisms. Additional delay, failure, attribution, reciprocity, or visibility treatments remain dormant. |
| Semantic measurement gates | Hidden-bench's coding thresholds and novelty labels do not directly fit crate evidence. | Freeze crate rubric, held-out corpus, coverage requirements, and construct-specific thresholds before semantic analytic use; retain exact-event measures independently. |
| Sample, estimands and exclusions | Only small usability aggregates supplied. | Set target N/power, primary outcome, hypothesis, exclusions and missingness plan separately before confirmatory use; technical flags do not silently remove rows. The open batch is N=5 and is the D13 usability election, not this confirmatory plan. D12 remains open. |
| Technical-incident policy | §6.1 requires a versioned policy for server/scheduler outages during active time; no source selects one. | Select clock treatment, flagged-interval schema, participant handling, payment and analytic flagging before real timing acceptance; never extend selected participants' clocks silently. |
| Pre-launch usability evidence | §6.5–6.6 require separate usability evidence for timing plausibility; §12.2 says model screening cannot establish human task validity. | **Selected 2026-10-07 (D13):** the N=5 pilot on `prolific.live.n5.v10` is that usability round. It is not a confirmatory analysis. Interpretation and expansion stay behind D12. |
| PE control-arm contamination | Two of five mini-pilot-3 controls reported being told about evaluation. | Inventory every participant-visible surface per arm, including platform listing/title, consent and completion pages, before paid PE recruitment. |

## 14. Implementation sequence and definition of done

1. **Inventory and freeze.** Archive checksums of S2/S3, extract content/behavior fixtures, create manifest schemas, resolve route/model inventory, and record §13 decisions. Done: every proposed arm can be described by one validated manifest and a clear reference-to-new diff.
2. **Persistent foundation.** Implement isolated database/roles, migrations, enrollment, consent, assignment, command/event contracts and preview isolation. Done: reload/retry never reallocates and cross-session access tests pass.
3. **Task engine.** Implement seeded world snapshots, information ACLs, clock/state machine, scheduler/outbox, answer locking and deterministic teammates. Done: evidence ledger, typed clock jobs, replay, concurrent-generation model doubles, and crash/concurrency tests pass without a real model dependency.
4. **Shared workbench.** Port simultaneous layout, training and keyboard equivalents; add complete durable item responses and stage navigation. Done: all eight profile/arm combinations complete with deterministic providers, including recovery and skipped questions.
5. **Study modules.** Implement pressure baseline/review-first and evaluation/control exposure hooks; provenance for every manipulation, voice and timing setting. Done: intended treatment differences are demonstrable and unintended differences absent in paired seed tests.
6. **Grounded local inference.** Add provider adapter, snapshot permissions, validation, fallbacks and restricted audit. Done: per-call snapshots, typed claims, bounded memory, paired-arm conformance, adversarial fixtures and held-out human review pass; actual local-provider performance reported separately.
7. **Research operations.** Add admin replay, exports/codebook/crosswalk, data-use/identity lifecycle and payment reconciliation. Done: event-derived measures and evidence/episode exports match fixture truth, offline coding gates are separately recorded, and a full synthetic session can be exported, withdrawn and restored correctly.
8. **Deployment rehearsal and small pilot.** Verify ingress, backup/restore, browser/load acceptance and research-material freeze; then activate a separately authorized pilot batch. Done: evidence for G1–G10 recorded, with no unresolved blocker hidden by a working demo.

**Status against the 2026-10-07 build.** Steps 1–7 have an implementation in the deployed tree and were used for the lab walkthrough and the N=5 `tp_review_first_v10` batch. That records what is running. It is not a finding that each step's Done line has been accepted. Step 8's ingress is in place and the N=5 batch is the open enrollment; backup/restore rehearsal, the browser matrix, load acceptance, and recorded G1–G10 evidence are not complete. G1–G10 remain the acceptance standard in §12.

The finished infrastructure must reproduce both reference variants, run the selected next-pilot profiles without code forks, recover every acknowledged action through process restarts, enforce who can know each record, and export enough provenance to reconstruct what each participant was assigned, could see, actually received, answered and was paid for. Completion of this specification alone is not evidence that those implementation conditions have been met.

## Appendix A. Source snapshot checksums

SHA-256 of the directly reviewed primary implementation/planning inputs at specification creation. These identify the planning inputs; they do not establish the exact files deployed in the historical Qualtrics pilots.

| File | SHA-256 |
|---|---|
| `TEAM_AI-R_AI-M_STUDY_PLAN.md` | `af628034d6b4384d9844807c524a36fed6143f617d9bf8355ed2b3f0082c0b52` |
| `docs/baseline/reference/investigators_time_pressure_demo.html` (archived 2026-10-06; formerly at repo root) | `7ced90c5e0c6f45882e9c1c54650fd0c23b137d744b3c9a138ea49d53d6b9825` |
| `investigators_peer_eval_demo.html` (revised reference 2026-10-01, previously `7b0069cb…99f8`) | `90b9587aedaa1b032c6316d9bb92937736598a007927a3f093139a9b8c27b48e` |
| `260920 Investigators Pilot Results.docx` | `cedc3e31d833bf936d4e6ad3ac36757a67c6a4ae4ea0833a793018ea0fa1fad4` |
| `XXXX-XXXX IRB Approved Protocol.docx` | `680f2491fe37b7f097dbe87e251d7ad33d404da68bf11b502edcd5437801a4e6` |
| `XXXX-XXXX IRB Approved Protocol.pdf` (added 2026-09-28; DOCX/PDF equivalence still unverified) | `a9f89f03c35562e6e5e10ba25560a3f91e1114bbad8e7ec36106be7a81298955` |


## Appendix B. Consolidation traceability and evidence status

This appendix records where the two evaluations were adopted; it is not a second requirements list. Their detailed repository-specific findings remain historical audit evidence, not defects attributed to Investigators.

| Evaluation recommendations | Authoritative implementation location |
|---|---|
| HB-01 per-call snapshots; visibility and memory | §§7.5, 8.1, 12.1 |
| HB-02 typed assertions and semantic parsing | §§7.4, 7.6, G5 |
| HB-03 paired-arm agent fidelity | §§7.3, 7.7, G3 |
| HB-04 empty/fallback/late states and durable completion | §§6.3–6.4, 8.3, G9 |
| HB-05 two-stage coding and explicit coverage | §9.4 |
| HB-06 immutable coding/gate evidence | §§9.4, 11.1, 12.2 |
| HB-07 evidence transfer and revision measures | §§7.6, 9.3, 9.5, G7 |
| HB-08 compatibility and retries | §§4.2, 6.2, 9.5 |
| HB-09 source/migration ownership | §§3.3, 11.1 |
| HB-10 later realism/ambiguity work | §§6.5–6.6, 12.2; new study policy remains versioned |
| Timing P0 clock/lifecycle and exposure | §§6.1–6.4, 8.1–8.3 |
| Timing P0 scheduler, commit authorization, provenance | §§3.3, 4.2, 6.2–6.4, 7.5 |
| Timing P0 browser revisions/drafts | §10, G6, §12.1 |
| Timing P1 distributions, request episodes, opportunity time | §§6.5, 9.3, 12.1 |
| Timing P2 dormant taxonomy extensions | §§6.6, 13 |
| Plan audit 2026-09-28 (AUD-02, 05–08, 13, 14, 16, 17; route rename; warning) | §§3.1, 4.1, 6.1, 6.2, 6.5, 6.6, 7.1, 11, 12, 12.2, 13, 14 |

The hidden-bench evaluation's 33 passing selected tests accompanied probes showing incorrect keyword matches, weak choice salvage, and context/early-stop differences. The Triad evaluation's 37 passing tests accompanied probes showing additive generation, backdated delivery, post-closure replies, and helper ordering/indicator gaps. These demonstrate why acceptance must exercise runtime behavior and scientific constructs, not merely stored flags or helper arithmetic. Neither evaluation ran an Investigators pilot or certified its implementation. The 2026-10-07 **Current build** section describes the deployed services and the fielded `tp_review_first_v10` contract after the N=5 batch opened. It does not move any row in this table, and it is not acceptance evidence. Consolidation of the two evaluations was a documentation change. G1–G10 and the additional measurement gate remain undemonstrated.
