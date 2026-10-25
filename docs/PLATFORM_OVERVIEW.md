# Human-in-Agent-Group Platform — overview

This document describes the platform as a software system: what it does,
how it is built, which guarantees it makes, where those guarantees stop,
and what evidence supports each one. It is written for researchers and
research engineers who have not seen the project before and are not
specialists in experiment software. In the code the application is called
`investigators`, after its first task.

Section 0 is a reading guide and Section 1 says what evidence the document
rests on. If you have ten minutes, read Sections 0 to 2 and Section 11.

Most of this document describes the build that was audited on 2026-10-05.
Where something has changed since then and the change can be checked in
this repository, the text says so and gives the date.

---

## 0. Reading guide

### 0.1 The research setting in one paragraph

A participant joins what appears to be a three-person investigation team
tracking crates of stolen goods across hideouts. In the reference design
there are eight crates and six hideouts, and the participant has six
minutes (five in the later `tp_ai_*` profiles) to locate the four crates
assigned to them. By design, two of those crates can be solved from the
participant's own *case file*, and two need newer information that only
teammates hold. The two teammates, **JT** and **PM**, are automated agents.
Team members can ask each other for records, share records, and chat. An
*AI assistant* pane answers questions about the participant's case file but
never names hideouts. Experimental conditions vary time pressure (the
**TP** study, with a `high` and a `low` arm; the latest profiles use `high`
and `mixed`) or peer evaluation (the **PE** study, with an `eval` and a
`control` arm). Later TP profiles also vary how many crates the participant
shares with the agent teammates and how large the case file is (see
[`../content/profiles/README.md`](../content/profiles/README.md)). The
platform records every action, message, model call, and disclosure, so
that researchers can measure how people seek, share, and use information
from human-seeming and AI sources.

### 0.2 Terms used below

| Term | Meaning |
|---|---|
| **Session** | One participant's run, from entry to a final stage. |
| **Stage** | A step of the study (consent, task, debrief, and so on). Only the server decides which stage a session is in. |
| **Profile / manifest** | A published, versioned configuration of one study design (timing, content, how participants can interact). The JSON file is called a manifest; "profile" is the everyday name. Each session is tied to exactly one. |
| **Arm** | The experimental condition within a profile (`high`/`low` or `high`/`mixed` for TP, `eval`/`control` for PE). |
| **Command** | One participant action sent to the server, such as an answer, a request, a share, or a message. Each command is applied in a single database transaction. |
| **Event** | A permanent record of something that happened. The events of a session are its replayable history. |
| **Scheduled job** | A database row for something due to happen later, such as a warning, a deadline, or a teammate reply. A worker process carries it out. |
| **Check-in** | A short questionnaire during a TP task. The task clock stops while it is open. |
| **Active time / wall time** | The two clocks. Active task time pauses during check-ins; wall time does not. |
| **Projection** | The filtered view of session state that a participant is allowed to see. |
| **Fallback** | Pre-written, version-pinned text used when model output is unavailable or rejected. |
| **Validator** | Fixed, rule-based checks that every model output must pass before a participant can see it. |
| **Harness / persona** | The harness runs software bots as participants. Each persona is a scripted strategy. The harness exercises the platform without human participants. |
| **Oracle** | An automated check that judges whether a harness session behaved correctly. |
| **Source bundle** | A SHA-256 fingerprint of the application source code. It identifies which code a deployment runs. |
| **D-items (D01 to D14)** | Research decisions tracked in [`DECISIONS.md`](./DECISIONS.md), such as payment policy or final consent wording. Some of them had to be settled before paid participants could take part. |
| **BA, AB, AID, O, SQ codes** | Identifiers from the project's registers. BA codes are audit findings, AB codes are behavior findings from lab play, and AID, O, and SQ codes are rulings on how the agents should behave. They are kept here only where a reader would search for them in the linked documents. |

[`GLOSSARY.md`](./GLOSSARY.md) has the full key to identifiers used in the
code and documents.

### 0.3 Companion documents

| Document | What it is | How it is maintained |
|---|---|---|
| [`../README.md`](../README.md) | Orientation, directory map, operating commands | Hand-written |
| [`TECHNICAL_APPENDIX.md`](./TECHNICAL_APPENDIX.md) | Inventory of stage transitions, which commands are allowed in which stage, the event types participants may receive, the table list, and the profile table | **Generated from the code for every image build**, and the gate fails if it is out of date. It is the authority for those facts. |
| [`DESIGN_SPEC.md`](./DESIGN_SPEC.md) | The authoritative study design | Hand-written, versioned |
| [`agent/INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_HARNESS.md`](./agent/INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_HARNESS.md) | Manual for the simulation harness: personas, oracles, command line, run log, limits | Hand-written; tests check selected claims |
| [`agent/INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_DECISIONS.md`](./agent/INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_DECISIONS.md) | Rulings on agent behavior (AID, O, and SQ codes) | Hand-written; tests check version strings and label coverage |
| [`agent/INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_BEHAVIORS.md`](./agent/INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_BEHAVIORS.md) | Behavior findings from lab play (AB codes) | Hand-written |
| [`DECISIONS.md`](./DECISIONS.md) | Register of research decisions D01 to D14 | Hand-written |
| [`IMPLEMENTATION_STATUS.md`](./IMPLEMENTATION_STATUS.md) | Task ledger and dated deployment entries | Started from the implementation plan, then maintained by hand; `build_status_ledger.py --check` validates task states and evidence citations |
| [`evidence/`](./evidence/) | Summaries of runs with simulated participants | Archived per run; historical and not regenerated |
| This document | An interpretation of the platform's guarantees and their limits | Hand-written. A few tests check selected status claims (§9.4); everything else needs manual review |

---

## 1. Evidence and scope

This table says which build was audited, which evidence the rest of the
document relies on, and what it does not cover.

| Item | Value |
|---|---|
| **Audited build** (2026-10-05) | Source bundle `d1a6a44f4b4bb001c0b4f6db52b724a5f0863f181b0198e0df8c11f5d03a7921`. A running deployment reports its own fingerprint at `GET /version`. The header of the generated `TECHNICAL_APPENDIX.md` and the newest ledger entry give the fingerprint of the latest build. This document lives under `docs/`, outside the directories the fingerprint covers, so editing it does not change the fingerprint. |
| **Last complete browser matrix** | Evidence package E15, dated 2026-09-30 and recorded in `IMPLEMENTATION_STATUS.md` (the package itself is not included). In this run real browsers took every profile and arm combination then in scope through to a stored completion, and also tested the withdrawal path. There were eight combinations: `tp_demo_v1`, `tp_review_first_v2`, `pe_demo_v1`, and `pe_pilot_v2`, each with its two arms. Earlier runs also exercised the technical-stop and abandonment paths. Profile versions published later have **not** had a new full browser matrix on the audited build. (At the audit date the generated `TECHNICAL_APPENDIX.md` listed 24 profiles; it lists more now.) |
| **Latest harness sweep** | `run-20261005T103408Z` ([summary](./evidence/2026-10-05-agent-sim/run-20261005T103408Z/summary.md)): six channel-strategy personas on `tp_demo_v5`, seed 42, with no findings of fail severity. It ran on source bundle `84ee7bce…0597` with oracle version `oracles_v3`. |
| **Model use in that sweep** | The sweep requested the provider `none`, and the provider in effect was `deterministic-fallback`. **No live model** took part. |
| **What this document does not establish** | It does not establish a browser or reconnect matrix on the audited build. It also does not cover harness runs with a live model, simulation over HTTP, load or soak testing with 40 or more simultaneous sessions, review by humans of whether the agents seem human, or the release gates G1 to G10 defined in `docs/IMPLEMENTATION_PLAN.md`. A passing health check (`/ready`, `/version`, container health) shows that the system is up. It is not an acceptance run with participant sessions. |
| **Research approvals** | **D05** (local model inference) was **resolved on 2026-10-01** by an approved IRB amendment (protocol XXXX-XXXX). At the audit date, D01, D02, D04, D06 to D14 and part of D03 were still unresolved. [`DECISIONS.md`](./DECISIONS.md) records that D01, D02, D04, D06, D07, D10 and D13 were resolved on 2026-10-07, leaving D03, D08, D09 (in part), D11, D12 and D14. |

---

## 2. What the platform is

The platform is **server-authoritative**. The participant's browser shows
the state of the session but never decides it. The server owns the stage,
the clock, scheduled events, and completion, and stores them in
PostgreSQL.

Each session follows a fixed sequence: entry → consent → instructions →
training → connecting → task (with check-ins during the task in TP) →
answers → confidence → instruments → open-ended follow-up questions (TP) →
debrief → *data-use* → completion. The *data-use* stage keeps an internal
name from an earlier design. The screen now shows contact information for
the research team and records a default `use_all` disposition. It does not
let the participant choose to have their data deleted, and its final
wording depends on decision D09.

Profiles differ in whether teammate replies can come from a language model,
and this matters for interpreting any claim about AI behavior. The setting
is `social_generation` in the profile:

- **`model_with_validated_fallback`.** Teammate social replies and
  assistant answers may be **model-generated**. Each generated output must
  pass the validator, or it is replaced by fallback text. All reference
  (demo) profiles use this setting, and so do the later `tp_ai_*` pilot
  profiles, including the ones used in the two pilots listed in
  [`../README.md`](../README.md).
- **`deterministic_only`.** No model text reaches participants. All
  teammate and assistant language is pinned and fixed in advance. The early
  proposed pilot profiles (`tp_review_first_v2` to `v9`, `pe_pilot_*`) and
  the `tp_scripted_*` profiles use this setting.

The generated `TECHNICAL_APPENDIX.md` (§2 there) lists the profiles with
their kind and setting.

**Status at the audit date: lab prototype.** The audited build was ready
for playthroughs by researchers and lab staff. It was not approved for paid
participants. "Lab prototype" does not mean the system had passed
acceptance testing with a live model or under concurrent load, that humans
had judged the agents believable, or that the IRB had approved fielding.
The two pilots that later ran with paid participants are described in
[`../pilots/README.md`](../pilots/README.md).

---

## 3. Architecture

```
                 Apache reverse proxy (TLS)
            investigators.local/investigators/*
             /                                   \
   investigators-ui                        investigators-api
   static React SPA                        FastAPI + SQLAlchemy Core
   (participant + admin;                   participant commands,
    no secrets)                            event stream, admin API
                                                   |
                                     PostgreSQL 15 (external;
                                     `app` + `identity` schemas,
                                     role-separated connections)
                                                   |
                                         investigators-worker
                                   (scheduler thread + generation thread)
                                                   |
                                   shared network namespace with the model
                                                   |
                                         investigators-ollama
                                   (gemma4:26b, digest-pinned; listens on
                                    loopback only, no published port)
```

| Service | Responsibility | Exposure |
|---|---|---|
| `investigators-api` | Participant and admin HTTP interfaces. Every change a participant makes is a command applied in a single transaction. | Host loopback `127.0.0.1:8519`, behind Apache |
| `investigators-worker` | Claims and runs scheduled jobs. Timing jobs run on one thread and model generation on another, so a slow model call cannot delay a warning or deadline that is due. | No listener |
| `investigators-ollama` | Local model server | Listens on `127.0.0.1:11434` inside the network namespace it shares with the worker. No port is published to the host. |
| `investigators-ui` | Static single-page app. It shows whatever stage the server reports. | Host loopback `127.0.0.1:8518`, behind Apache |

### 3.1 Trust boundaries

Each boundary below is described by its mechanism, its limits, and the
evidence for it.

- **Participant and server.** *Mechanism:* the server sends participants
  only event types and payload fields on an allowlist. Arm labels, correct
  answers, what each teammate holds, and internal vocabulary stay on the
  server. Every gate run scans the built participant bundle for
  restricted words. *Limits:* the allowlist controls what the API sends.
  Anything a participant types in free text is their own. *Evidence:* the
  allowlist in §7 of the generated `TECHNICAL_APPENDIX.md`,
  `scripts/check_bundle_leakage.py`, and the test
  `tests/contracts/test_bundle_leakage.py`.
- **Admin interface.** *Mechanism:* there are three roles (researcher,
  operator, compensation), each with its own password. Admin sessions use
  an HMAC-signed cookie that expires, double-submit CSRF protection, login
  throttling, and rejection of forged identity headers. An empty password
  disables a role, and admin actions write audit rows. *Limits:* **the
  application enforces admin authentication, not the ingress.** The Apache
  configuration has Shibboleth single-sign-on blocks for the admin paths,
  but a later public block for `/investigators/` overrides them. The owner
  checked and accepted this on 2026-09-28, and it is recorded in the
  ingress virtual-host snippet, which is not included in this repository.
  Apache strips identity headers on the whole path, so the API never trusts
  them. *Evidence:* `backend/app/services/adminauth.py`,
  [`ADMIN_SURFACE.md`](./ADMIN_SURFACE.md), and the admin integration tests
  (`backend/tests/integration/test_wp13_admin.py`).
- **Worker and model.** *Mechanism:* Ollama listens only on loopback inside
  the namespace shared by the worker and the model, and publishes no port.
  The worker's provider adapter refuses any model endpoint address that is
  not loopback. Adapters for remote providers are refused for all session
  traffic. *Limits:* these controls restrict **who can reach the model
  server and where the worker sends model requests**. They do not show
  that the namespace has no outbound network route; the worker uses the
  same namespace to reach PostgreSQL. Control of outbound traffic has not
  been set up or tested. *Evidence:* `docker-compose.yml` (the model and
  worker services) and `backend/app/providers/local.py`.
- **Containers.** *Mechanism:* read-only root filesystems, all Linux
  capabilities dropped, `no-new-privileges`, a tmpfs `/tmp`, base images
  pinned by digest, and secrets passed only as environment variables from
  `.env`. The API has one bind mount: the repository, read-only at
  `/srv/docs`, with `.env` hidden by a `/dev/null` mount on top of it. The
  API uses this mount to serve a fixed list of documents to the researcher
  role. *Evidence:* `docker-compose.yml` and
  `tests/contracts/test_compose_contract.py`.

---

## 4. Execution model

These design choices are what make sessions recoverable after a failure
and replayable afterwards. §3 to §5 of the generated
`TECHNICAL_APPENDIX.md` give the exact stage transitions, command tables,
and job kinds.

- **The server owns the stage graph.** A session can move only along
  declared transitions (`backend/app/domain/stage_graph.py`). The client
  cannot move a session to a stage.
- **One transaction per command.** Each command carries an id (a UUID)
  generated by the client and the session revision the client last saw.
  The handler locks the session row (`SELECT … FOR UPDATE`). Inside that
  one transaction it appends events, updates projections, schedules
  follow-up jobs, and stores a *receipt*. Sending the same UUID again
  returns the stored receipt, so retries are safe. A command based on an
  outdated revision is refused with `stale_revision`, and the client
  reloads the session state. Request bodies have size limits and are
  validated before anything changes.
- **Scheduling survives restarts.** Anything that happens later is a
  `scheduled_job` row with a logical key, a clock basis, and a lease,
  rather than a timer in memory. Workers claim due jobs with
  `FOR UPDATE SKIP LOCKED` and run them inside the session's transaction.
  Expired leases are reclaimed. A retried job reuses its pinned inputs, so
  a retry cannot produce a different random outcome. Evidence package E15
  includes a run with two worker processes and two API processes that
  showed each job ran exactly once and duplicate commands were recognized
  across processes.
- **Two clocks.** Active task time pauses during check-ins, while wall
  time governs limits that must not pause. Both can be rebuilt from the
  committed `clock_interval` rows.
- **One writer per session.** A lease marks one browser tab as the writer.
  A second tab becomes read-only and offers an explicit way to take over.

---

## 5. Where AI is used, and how it is bounded

Model text reaches a participant only through the pipeline below, and only
on profiles whose `social_generation` is `model_with_validated_fallback`.
On `deterministic_only` profiles the pipeline stops at step 1, so all
language is fixed in advance.

1. **Rule-based routing first.** Free-text team messages are classified
   by rules before any model is involved. The rules identify:
   - which teammate is being addressed;
   - automation probes, that is, questions about whether a teammate is a
     bot (§5.1);
   - record requests typed as free text, which go to the record lookup for
     that teammate;
   - questions that have fixed answers grounded in the records.

   None of these schedule a model call.
2. **Knowledge snapshot.** When a command is accepted, it pins a
   `knowledge_snapshot`. For the assistant this is the record ids and their
   SHA-256 digests; for teammates it is their open requests. The model can
   only be told what the snapshot contains.
3. **Grounded prompt.** The prompt is built from the snapshot alone.
   Participant text is quoted inside escaped delimiters. A teammate prompt
   gets a short window of recent chat in which shared records appear only
   as placeholders, so **the contents of records never enter a prompt for
   a social reply**.
4. **Seeded, logged model call.** Each call has an overall deadline and a
   seed derived from the session and the job. Its parameters, seed,
   latency, dispatch status, and the number of other model calls in
   progress at that moment are all stored, including for failed calls.
5. **Validate or fall back.** While the session is locked, the output must
   pass validator `teammate_output_v8`. §6 of the generated
   `TECHNICAL_APPENDIX.md` lists every kind of violation it checks for,
   including leaking the condition, leaking answers, claiming to be human,
   revealing the prompt, and repeating or closely paraphrasing an earlier
   line. Location claims in assistant output are also checked again
   against the pinned records. Output that is rejected, empty, failed, or
   late is replaced by fallback text, which is itself checked so that it
   does not repeat a line already delivered.

**Every delivered line has a recorded origin.** The origin is one of: model
output with its `model_call` row, a rule-based route, or a fallback draw.
It is stored in `displayed_output`, `voice_line_committed`, and
`message_delivered`. Researchers can therefore separate model behavior from
scripted behavior in every session.

### 5.1 Disclosure when a participant asks whether a teammate is automated

The platform has a rule for answering a participant who asks whether a
teammate is a bot. The rule and its limits:

- **Mechanism.** A message classified as an automation probe
  (`engines.is_automation_probe`) **skips** the usual rate limit and the
  cap on pending requests. It is **always logged**: the message, an
  `automation_probe` event, and a flag on the session. A fixed, truthful
  disclosure line is scheduled from each teammate the probe is about. For
  example, "is pm a bot?" is answered by PM.
- **Budget.** Each teammate has its own budget of
  `MAX_PENDING_PROBE_DISCLOSURES = 3` queued disclosures
  (`backend/app/services/team_chat.py`). If a teammate's budget is full
  during a burst of probes, the probe is still recorded but **no further
  disclosure line is queued for that teammate**.
- **Near-misses.** Messages that resemble a probe but are not classified as
  one are flagged (`probe_near_miss`) for researcher review. They do
  **not** trigger a disclosure. This narrow boundary was chosen on purpose:
  see agent-behavior ruling SQ-10 and behavior finding AB-18 in
  [`agent/INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_BEHAVIORS.md`](./agent/INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_BEHAVIORS.md),
  which found that roughly half of natural "are you real?" phrasings are
  near-misses.
- **Evidence.** The harness invariant `INV-PROBE-DISCLOSURE` checks that
  every participant message classified as a probe has an
  `automation_probe` event. The `probes:all_disclosed` scenario check
  matches each probe message to its event.

---

## 6. Data and provenance

- **Schemas.** About 44 operational and research tables live in the `app`
  schema. Links to the recruitment platform (`platform_link`) and session
  credentials (`session_credential`) live in a separate `identity` schema.
  §8 of the generated `TECHNICAL_APPENDIX.md` lists the tables. The schema
  changes only through Alembic migrations. The API reports itself not
  ready if the database's migration head differs from the one the build
  expects.
- **Events first.** `event` rows are the replayable history. The case file,
  chat, answers, and request episodes are projections derived from events
  inside the same transactions.
- **Where evidence came from.** Each evidence record stores its origin (an
  `initial` case-file record or one `added_from_team`) and its place in the
  timeline. `manipulation_exposure` rows record what a participant was
  shown, when, and by which mechanism.
- **What exports contain.** A research export contains committed rows from
  `app` tables that belong to sessions, and nothing else. It leaves out
  `participant_display` (display initials), every `identity` table, and
  payment tables. Recruitment-platform links and payment state appear only
  in a separate compensation export, which must run under its own database
  role. Guard checks make the export fail if a restricted table ever
  becomes exportable.
- **Retention and redaction.** Deletion runs as a dry-run plan by default,
  purges are scheduled for after the study closes, and free text goes
  through a review queue before export. The final retention policy is
  decision D09.

---

## 7. Security and participant protection

| Area | Mechanism | Limit or note |
|---|---|---|
| Participant entry | Signed preview tokens that expire (`?pt=`). A session bearer cookie, double-submit CSRF protection, and the single-writer lease. | Integration with the recruitment platform is decision D08 (unresolved). |
| Admin access | A password per role, an HMAC-signed cookie that expires, CSRF protection, throttling, audit rows | Enforced by the application; Apache does not enforce single sign-on on these paths (§3.1). |
| Injection | Parameterized SQL (SQLAlchemy Core); request bodies that are size-limited and validated; participant text quoted with escaping in prompts | — |
| Information leakage | Allowlist on the server of what participants receive; scan of the built bundle for restricted words | The scan covers the built participant bundle. |
| Secrets | Kept only in `.env`. Never built into images or mounted; validation errors do not print values. | — |
| Automation disclosure | Recognized probes skip the usual caps and are always logged. A disclosure is queued from each teammate while that teammate's budget allows. | Budget and near-miss limits are in §5.1. |
| Exit paths | Withdrawal, technical stop, and abandonment are final stages that record whether the participant is owed payment | At the audit date payment policy (decision D04) was unresolved; `DECISIONS.md` records it as resolved on 2026-10-07. |

---

## 8. Simulation and observability

Software participants can run the platform from start to finish. This is
how the lab checks the mechanics before human sessions, and it lets
researchers examine behavior without running a study.

### 8.1 Simulation harness (`backend/harness/`)

- **How it drives the system.** The bot calls the **same command and
  lifecycle handlers** that the participant API uses (`execute_command`,
  `lifecycle.handler_for`). It runs inside the same process, against a
  throwaway database. It does **not** go through HTTP, the authentication
  middleware, the event stream, or a browser.
- **Personas.** Fourteen registered strategies (P-01 to P-14):
  - an ordinary participant;
  - a participant who asks a lot;
  - a participant who asks whether teammates are bots;
  - a participant who tries to push the assistant past its limits;
  - an idle participant;
  - a participant who shares a lot;
  - a participant who sends hostile input;
  - a participant who disconnects and reconnects;
  - six channel-strategy personas that each use only some of the
    information channels (case file, chat, assistant).
- **Oracles.** The checks run on every session include:
  - shares that no request prompted;
  - near-repeats of the same line by both teammates;
  - logging of probes;
  - which teammate responds to a request;
  - limits on what the assistant reveals about answers;
  - bookkeeping of request episodes;
  - the wording used to reject hostile input.

  Each persona also declares what its sessions should look like, such as
  the final stage and that every request was closed. The harness manual
  documents each check and what it deliberately does not cover.
- **Reproducing a run.** If the manifest, source bundle, oracle version,
  set of personas, seed, and provider mode are all the same, and
  paraphrasing is off, a second run reproduces the persona decisions, the
  bots' messages, and the task content. Ids generated by the database are
  excluded from this comparison. Runs that use a live model or
  `--paraphrase` are **not** guaranteed to reproduce the same text byte for
  byte, because that depends on how faithfully the model honors its seed.
- **Provider modes.** Every archived run states the provider it requested
  and the provider actually used. All archived sweeps so far used `none`
  (in effect `deterministic-fallback`).

### 8.2 Tools for researchers

- **Session walkthrough** (admin). A single timeline that merges the events
  the participant saw, command receipts (including rejected commands), and
  model calls.
- **HTML session simulation** (admin, on demand). A self-contained file
  that replays a committed session in a reconstruction of the participant's
  screen, using the recorded time gaps. A restricted "internals" side panel
  shows what the participant did not see.
- **Harness replays.** Every harness session writes the same replay data
  as JSON (`replays/<sid>.json`). With `--simulate`, and if Node.js is
  installed, the harness also renders it with the same player into
  `simulations/<sid>.html`. If a render fails, a warning is logged and that
  HTML file is skipped; the rest of the batch continues.
- **Documentation tab** (admin, researcher role only). Shows the main
  design documents and a code index of this repository, read-only.

---

## 9. Verification

### 9.1 Test suites

These test counts are from the audited build. They change as tests are
added.

| Suite | What it covers | Count |
|---|---|---|
| Backend unit | Commands, scheduler, routing and validation, exports, measures. Uses a simulated clock and stand-ins for the model provider. | ~1,690 |
| Backend integration | A real throwaway PostgreSQL database: migrations, leases, scheduler claims, admin interfaces, lifecycle | ~290 |
| Harness | Personas, channel isolation, pools of bot messages, oracles | ~50 |
| Frontend | Rebuilding client state, stage router, admin interfaces, simulation player | ~150 |
| Repository contracts | Container hardening, consistency between documents and code, bundle leakage, whether generated documents are up to date | ~110 |

### 9.2 Gate variants

The gate is the script that must pass before a deployment.

- `scripts/check.sh` is the **source gate**. It runs:
  - verification of pinned inputs;
  - backend lint, type checks, and unit and integration tests;
  - frontend checks;
  - repository contract tests;
  - the leakage scan of the participant bundle;
  - a check that generated documents are up to date;
  - validation of the compose file;
  - a check that the needed ports are free.
- `scripts/check.sh --with-images` also **builds the images and runs a
  smoke test on a throwaway stack** (`scripts/smoke_stack.sh`). The default
  command does not.

A report that the gate passed should name the command, the log, the source
fingerprint, and whether `--with-images` was used.

### 9.3 Protecting live databases

Integration tests refuse to start unless a guard shows that the database
is a throwaway one. The database name must contain `test`, and the
connection string must differ from every live connection string the guard
can find.

### 9.4 What the checks do and do not cover

| Category | Examples | How it is checked |
|---|---|---|
| Generated for each build | The generated technical appendix, the click-through mock | Regenerated by `scripts/build_images.sh`; the gate fails if they are out of date |
| Generated section checked against sources | Requirement trace | A test checks it against the plan and the specification |
| Maintained by hand, with specific checks | Status ledger, decision registers, harness manual | Tests check specific items: ledger states and evidence citations, version strings, label coverage, and the fingerprints cited for runs |
| Written by hand | This document | Tests check the wording about probe disclosure, the status of D05, the wording about gate variants, the naming of schema tables, and that no source fingerprint is called "current". Everything else needs manual review. |

A passing gate does not prove every narrative, security, or scientific
claim in the documentation.

---

## 10. Reproducibility

- **Identifying the source.** The source bundle is a SHA-256 over the
  directories `backend/`, `frontend/`, `content/`, and the file
  `docker-compose.yml`, computed by `scripts/source_bundle.py`. Generated
  directories, dependency directories, and `.env` are excluded. Images are
  stamped with it, and the API (`/version`) and the UI (`version.json`)
  report it. The fingerprint lets you **match** a deployment to source you
  have kept. It cannot recover files that were not kept, because the
  repository has no Git history; the dated evidence packages hold the
  records that were kept.
- **Inputs outside the bundle.** Reproducing how a deployment behaved also
  needs these inputs, which are pinned separately or controlled by the
  operator:
  - the model digest (`gemma4:26b`, recorded for each call);
  - environment settings (`.env`, never committed);
  - the database at the pinned migration head;
  - the Apache virtual-host configuration (not included in this
    repository; applied by an operator);
  - the build and deployment logs.
- **Pinned dependencies.** Base images are pinned by digest. Backend
  dependencies come from `uv.lock` (`uv sync --frozen`) and frontend
  dependencies from `package-lock.json` (`npm ci`). Published manifests can
  only be added, never changed, and a session pins its manifest by SHA-256
  when it starts, so a session's configuration cannot change partway
  through. [`TOOLCHAIN.md`](./TOOLCHAIN.md) lists the locked versions.
- **Determinism in tests and simulation.** Tests use a simulated clock and
  stand-ins for the model provider. Harness runs are seeded (§8.1 says
  exactly what a second run reproduces). Retried scheduled jobs reuse their
  pinned inputs.

### 10.1 Running it

```bash
cd human-in-agent-group-platform     # the repository root
scripts/check.sh                  # source gate
scripts/check.sh --with-images    # optional: + image build and disposable smoke stack
scripts/build_images.sh           # stamped images; regenerates per-build docs
scripts/preflight_ports.sh        # first start only
docker compose up -d
curl http://127.0.0.1:8519/ready     # database reachable, schema at pinned head
curl http://127.0.0.1:8519/version   # running source stamp

# simulated participants (disposable database, stub in place of the model):
AGENT_SIM_PROVIDER=stub scripts/run_agent_sim.sh --personas normal,asker,prober --seed 7
```

You also need, outside this repository:

- a PostgreSQL 15 server with separate connection strings for each role;
- an Apache reverse proxy (its virtual-host snippet is not included);
- a GPU large enough to hold the model, for profiles that generate text
  with a live model.

---

## 11. Known limitations

- **Not fielded at the audit date.** At the audit date, use with paid
  participants was blocked on the unresolved D-items listed in §1, and the
  interim participant text and candidate timing values were for lab use
  only. Later fielding is recorded in [`DECISIONS.md`](./DECISIONS.md) and
  [`../pilots/README.md`](../pilots/README.md).
- **Browser evidence is dated.** The last full browser matrix (evidence
  package E15, 2026-09-30) covered the eight combinations then in scope.
  Newer profile versions, and a reconnect matrix on the audited build,
  still need their own acceptance evidence.
- **Simulation tests mechanisms, not experience.** The harness checks
  routing, logging of disclosures, timing, storage, and lifecycle rules.
  Whether teammates *feel* human, and how participants experience the
  pacing and understand the task, can only be judged in playthroughs with
  people.
- **The second phase of the harness is not built or verified.** It would
  add harness runs with a live model inside the worker's namespace (the
  worker image does not yet include `harness/`), load or soak testing with
  at least 40 simultaneous sessions, and driving the platform over HTTP.
  The `--provider ollama` command-line option does not provide any of
  these by itself.
- **Single host.** The stack is set up for one lab server. The design (an
  API that keeps no state between requests, jobs stored in the database,
  PostgreSQL) does not rule out running several servers, but this has not
  been tested.
- **Admin authentication is enforced only by the application** (§3.1).
  Single sign-on at the ingress does not apply to the admin paths.
- **No claim of network isolation.** The model server listens only on
  loopback, but outbound network routes from the worker and model namespace
  have not been audited (§3.1).
- **The fallback for an overloaded GPU is not built.** The IRB-approved
  fallback when the GPU is overwhelmed is university AI service, and no adapter for
  it exists yet (see the D05 entry in `DECISIONS.md`). The existing remote
  adapters are refused for session traffic.

---

## 12. Repository layout

```
backend/            Server application
  app/api/            HTTP routes (participant, admin)
  app/services/       Lifecycle, scheduler, generation routing and validation
  app/domain/         Stage graph, manifests, worlds
  app/db/             Schema, transactions, export, test-database guard
  app/providers/      Model adapters (local loopback; remote, refused for sessions)
  harness/            Simulation harness (personas, oracles, runner, CLI)
  migrations/         Alembic migrations
  tests/              Unit and integration tests
frontend/           Participant and admin single-page apps (React + Vite)
  src/admin/          Researcher tools: walkthrough, simulation export, docs tab
  scripts/            render_simulation.mjs (harness HTML rendering)
content/            Versioned study content: worlds, manifests, scripts,
                    instruments, coding rubric. Loaded and pinned by SHA-256.
scripts/            Gate, image build, source bundle, export, smoke stack, …
tests/contracts/    Repository contracts (hardening, document consistency, leakage)
pilots/             Pilot study exports, hand coding, and analysis code
docs/               Generated inventory, registers, ledger, dated evidence
                    packages and gate records, this overview
```
