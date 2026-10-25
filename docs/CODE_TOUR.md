# Code tour

A guided path through the parts of the code that carry the platform’s main guarantees. It takes about half an hour. Status labels are defined in `docs/README.md`.

## 1. Code, not the model, decides when agent teammates act

Open `backend/app/services/scheduler.py`, functions `schedule_task_jobs` and `schedule_response`. The schedule of requests, remarks, check-ins, and greetings is written as persisted jobs from the profile when the task starts. The model never chooses when to speak.

Then open `backend/app/services/engines.py`, functions `identity_line` and `validate_teammate_output`. An automation question never gets model text. Any other generated line must pass the checks, or a bank line is delivered instead.

**Implemented.** The automation-question detector is **Verified** on its labeled corpus (`docs/EVALUATIONS.md`).

## 2. Profiles are fingerprinted

`content/published/manifests.json` stores each published manifest’s id and hash. A profile file under `content/profiles/` is the readable form. Editing a published profile without republishing changes the hash, and the binding check refuses it. `scripts/source_bundle.py` fingerprints the source itself.

**Implemented.**

## 3. Conditions that differ in more than the manipulation are rejected

`backend/app/domain/manifests/diff.py`, function `check_paired_arms`. Any path under `manipulation.arm` may differ between conditions. Any other difference raises `ArmDiffError`. The checker allows every key under `manipulation.arm`, so the manipulation’s scope is whatever the researcher declares there.

**Implemented.**

## 4. What the model is allowed to see

`backend/app/services/prompts.py`, functions `render_social` and `build_request`. `PROMPT_BUILDER_VERSION` is `grounded_prompts_v7`. The prompt contains the participant’s message, open requests, a short window of recent chat with shared cards shown only as placeholders, and the agent teammate’s own earlier messages. Task materials and solutions are never in it. Participant text is quoted inside delimited blocks that the system prompt declares as data, so typed instructions cannot change an agent teammate’s instructions.

**Implemented.**

## 5. Checks and fallback

`validate_teammate_output` in `engines.py` records named violations such as `hideout_leak` and caps replies at 400 characters. A reply that fails is not retried; the fallback bank line is served instead. Repetition (`near_repeat`) is checked again at delivery time in the scheduler.

**Implemented.**

## 6. Automation disclosure

`is_automation_probe` classifies the message and `identity_line` picks the bank line. The labeled corpus, 103 questions including 18 expected non-fires, is in `backend/tests/adversarial_corpus.py`. It is researcher-written from observed forms and a published list of strategies. `backend/harness/lab_corpus.json` is separate: phrasing the simulated participants draw on, collected from test sessions.

The playable mocks do not do this. **Not in demo.**

## 7. The log keeps model output apart from what was shown

`backend/app/db/schema.py` defines both the model-call record and the message the participant was shown. A blocked generation is in the first and not the second. The pilot exports in `pilots/data/` contain both tables (`investigators_model_call_*.csv` and `investigators_message_*.csv`) for every session.

**Implemented.**

## 8. Pilot sessions

`pilots/README.md` lists the profiles, sessions, and files for both pilots. Open `pilots/analysis/transcript_viewer.html` to read what agent teammates said. Each message is labeled by source, and blocked model replies are shown with the check that blocked them. The same labels are in `pilots/analysis/message_sources.csv`. Descriptive statistics come from `pilots/analysis/analysis.R`.

## 9. Simulated participants

`backend/harness/personas.py`, `oracles.py`, and `runner.py`. Personas are seeded strategies, for example `normal`, `asker`, `prober`, `idler`, `chaotic`, and `disconnecter`. Oracles (`oracles_v3`) check each session’s log against the platform’s rules. A bank-only run needs a disposable database but not the model.

**Implemented.**

## 10. Participant protections

Read `docs/DISCLOSURE.md`, then the consent artifact `content/instruments/consent.reference.v9.json`, then `DebriefScreen` in `frontend/src/components/StageScreens.tsx`. The instruments are the JSON files in `content/instruments/`. Comprehension is recorded as a training step, not as a separate instrument.

## 11. Researcher tools

The admin UI is `frontend/src/admin/` (`AdminApp.tsx`, `dashboard.tsx`, `walkthrough.tsx`, `simulation.ts`), and its API is `backend/app/api/admin.py`. Replay and walkthrough views are not on the participant path. An empty `ADMIN_PASSWORD` disables the admin surface entirely.
