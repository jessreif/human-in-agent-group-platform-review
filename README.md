# Human-in-Agent-Group Platform

> **Anonymized copy for peer review.** Author names, email addresses, the university and school, and the IRB protocol number have been replaced with placeholders such as `[Principal Investigator]`, `[University]`, `@university.edu`, and `XXXX-XXXX`. Nothing else was changed. Because the replacements edit published profiles, consent instruments, and documents whose SHA-256 hashes are pinned, the hash checks in this copy (`content/published/manifests.json`, `docs/FINGERPRINTS.json`, `docs/baseline/SOURCE_CHECKSUMS.sha256`, and the contract tests that read them) will report mismatches. The pinned hashes are left as they were, because they are the hashes of the files the pilots ran under and match the `manifest_sha256` values in the pilot data.

This repository is a prototype of a platform for experiments in which one human participant works on a team task with AI agent teammates. The researcher specifies the task, what each agent teammate knows, when it acts, and how it interacts with the participant. The platform carries out every scheduled action exactly as specified, lets agent teammates converse in natural language through a locally hosted open-weight model, checks every generated message before the participant sees it, and records everything that happened in each session.

The prototype implements one task, the **Investigation Task**, an adaptation of the hidden-profile paradigm. The participant and two agent teammates (shown as `JT` and `PM`) track stolen crates across hideouts using time-stamped intelligence that is split among them, so doing well requires asking teammates for information they hold. The platform has run two pilot studies with participants recruited on Prolific.

In the code, the platform goes by the name `investigators` (service names, the database, the `/investigators/` URL path, and export file names).

## What is here

- **Playable demos.** `demos/index.html` opens a page with one demo per pilot condition. The demos run in a browser with no server and use canned replies, so they show the participant's screens rather than the platform's behavior.
- **Pilot sessions.** The pilot exports, hand coding, analysis tables, and transcript viewer contain participant-level data, so they are shared on ResearchBox (https://researchbox.org/10535) rather than in this repository. The transcript viewer shows the team chat from every pilot session, with each agent teammate message labeled by how it was produced and, where a model reply was blocked, the blocked text and the check that blocked it. `pilots/` here holds the analysis code, results, and figures.
- **Source code.** The full platform: server, background worker, participant and researcher interfaces, study materials, simulation harness, and tests. `docs/CODE_TOUR.md` walks through the parts that carry the main design guarantees.

## How the platform works

A **profile** is a JSON file in `content/profiles/` that specifies a study: the task, its timing, each agent teammate's information and schedule, the pre-written message banks, the instruments, and the conditions. Publishing a profile records a SHA-256 hash of it and of every file it uses (`content/published/manifests.json`). Published profiles are never edited, and every session records the hash of the profile it ran under. `check_paired_arms` (`backend/app/domain/manifests/diff.py`) rejects a profile whose conditions differ anywhere outside the declared manipulation.

The server, not the participant's browser, controls each session. Every participant action is applied as a single database transaction, and anything due later, such as an agent teammate's reply, is saved as a job for the background worker, so a restart neither loses nor repeats it. The task clock counts active time from saved records.

Agent teammates are constrained in four ways:

- The profile's schedule decides when agent teammates act (`backend/app/services/scheduler.py`). The model does not.
- Task information is posted exactly as generated for the session and never passes through the model.
- Conversational replies come from a local model (`gemma4:26b` through Ollama), using a prompt that excludes task materials (`backend/app/services/prompts.py`). Each reply must pass the checks in `validate_teammate_output` (`backend/app/services/engines.py`); a reply that fails is replaced by a pre-written line.
- A question about whether a teammate is automated gets a fixed, truthful answer (`engines.is_automation_probe`, `engines.identity_line`), never model text.

The database keeps what the model returned (`model_call`) separately from what the participant was shown (`message`), along with every event, timing draw, and seed. `docs/PLATFORM_OVERVIEW.md` describes the architecture and these guarantees in detail.

## Pilot studies

| Pilot | Batch group | Profiles | Design | Started | Completed |
|---|---|---|---|---|---|
| Pilot 1 | `study.live.v14` | `tp_ai_ovnone_v3`, `tp_ai_ovnone_thin_v3`, `tp_ai_ovsome_v3`, `tp_ai_ovsome_thin_v3` | Overlap between the participant's and agent teammates' crates (none, partial) × case-file size (full, thin) | 74 | 60 |
| Pilot 2 | `study.live.v15` | `tp_ai_ovsome_thin_v9` | Agent teammates' reported workload at a check-in (high, mixed) | 36 | 30 |

Both pilots were designed to test the platform end to end, not to test hypotheses. `pilots/README.md` describes the data and how to reproduce the analysis.

## Current scope and limitations

- The prototype implements one task. Each session has one participant and two agent teammates, and agent teammates do not volunteer information unless asked.
- The stack is set up for a single server, with the model served from that server's GPU.
- The model's wording cannot be reproduced from the stored seed. Everything else that varies between sessions (task contents, assignment, bank choices, timing) is computed from seeds and can be regenerated.
- Participants are not told before the task that their teammates are automated; the debrief tells them. `docs/DISCLOSURE.md` lists what is withheld and where it is corrected.
- Two repository contract tests fail because a design document was edited for publication after its hash was pinned. `docs/EVALUATIONS.md` explains this and records the test runs.

## Repository layout

| Path | Contents |
|---|---|
| `demos/` | Demo landing page, notes on what the demos simulate, and the template they are generated from |
| `pilots/` | Pilot analysis code, results, and figures; the data are on ResearchBox |
| `docs/` | Documentation; `docs/README.md` is the index |
| `backend/` | Server and background worker (Python 3.12, FastAPI, SQLAlchemy, PostgreSQL), simulation harness, migrations, and tests |
| `frontend/` | Participant and researcher interfaces (React, TypeScript, Vite); `frontend/public/demos/` holds the generated demo files |
| `content/` | Profiles, published manifests, message banks, and instruments |
| `scripts/` | Build, test gate, fingerprinting, export, and simulation scripts |
| `tests/` | Repository contract tests and reference fixtures |
| `release/` | Configuration and results for evaluating the optional AI assistant panel |

## Running the code

Running the full platform needs Docker with Compose, a PostgreSQL 15 server, and, for model-generated replies, a GPU that can serve Gemma 4 26B under Ollama. With `MODEL_PROVIDER=none`, all conversational replies come from the pre-written banks instead.

```bash
cp .env.example .env              # then fill in database URLs and admin passwords
scripts/check.sh                  # lint, type checks, tests, contracts, generated docs
scripts/build_images.sh
docker compose up -d
```

Simulated participants can be run against a disposable database without the model:

```bash
AGENT_SIM_PROVIDER=stub scripts/run_agent_sim.sh --personas normal,asker,prober --seed 7
```

The backend unit tests (`cd backend && uv sync --frozen --group dev && uv run pytest -m "not integration" -q`) gave 2098 passed, 482 deselected on 2026-10-21.

## Source and data

The deployed code has no git history, so each build is identified by a SHA-256 fingerprint of `backend/`, `frontend/`, `content/`, and `docker-compose.yml` (`scripts/source_bundle.py`), which a running deployment reports at `/version`. The `--behavior` variant covers only the code that defines what participants experience.

| Fingerprint | Value |
|---|---|
| This repository, full source | `21b8104b899134c536cab0e098d6e4013aadb443351a8666474bd76a4da6294b` |
| This repository, participant-facing source | `5f7f67c5a29954ec5e8e276f554637626a611672d18e075a2cd0e8fb2de5cb91` |
| Deployed code on 2026-10-21, full source | `96c87fb557e3e30d0ad45d5636aed61d9c29bdd9795af714fcfb82a5a6680968` |
| Deployed code on 2026-10-21, participant-facing source | `60e2c664d5a97c8656df5ce3e773c05fece5cac8f7a54c1128b87e797f6188e7` |

The values differ from the deployed code because, for publication, server secrets, host names, and deployment configuration were removed; the recruitment-platform client was replaced by a stub (`scripts/_prolific.py`); and the fielded completion code was replaced by `PLACEHOLDER-CODE`. Study logic, prompts, message banks, checks, timing, consent and debrief text, and every profile under `content/` are unchanged, so the profile hashes recorded in the pilot data still match. In the pilot exports, Prolific participant IDs were replaced with `REDACTED`. Internal planning documents, build logs, and data from earlier pilot rounds are not included; `docs/development-history.md` summarizes them.

## License

See `LICENSE`.
