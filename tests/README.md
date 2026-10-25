# tests/

This page explains how the automated checks in this repository are organized, which ones to run when, and the rules for changing them. Read it before you run tests, add a test, or claim that the repository passes.

There is no continuous-integration server. The script `scripts/check.sh`, called "the gate", is the single command that decides whether the repository passes.

Tests live in three places:

- backend tests in `backend/tests/`;
- frontend unit tests in `frontend/tests/`;
- repository-level contract tests in `tests/contracts/`. These check that configuration, documents, and code agree with each other.

## Tiers

Run these from the repository root unless the command changes directory itself.

| Tier | Command | When to run it |
|---|---|---|
| Fast | see below | While you are editing. |
| Full | `scripts/check.sh` | Before every deployment, and before saying that the repository passes. It is required, not something left to a nightly run. |
| Browser | `cd frontend && INVESTIGATORS_E2E=1 npm run test:e2e` | Before a release that changes how participants enter the study, how telemetry is sent when a participant leaves the page, or the layout of the case file. It does not replace the full tier. |

The gate also runs `npm run test:e2e`, but without `INVESTIGATORS_E2E` set. In that case the command only runs a smoke test that opens `about:blank` in Chromium; it does not run the participant scenarios. See [`e2e/README.md`](e2e/README.md) for what the browser tier covers and the 2026-10-13 run that passed. The write-up of that run and the failures before it was kept at `docs/evidence/test-suite-plan/JOURNEY.md`, which is not included in this repository.

### Fast

```bash
cd frontend && npm run -s typecheck && npm run -s lint && npm test
cd backend && uv run pytest -q -m "not integration"
uv run --project backend --frozen pytest -q -p no:cacheprovider tests/contracts
```

`pytest -m "not integration"` also runs the tests marked `medium`. These open a loopback socket or wait on a real semaphore, but they do not use a database. They are slower than ordinary unit tests. To skip them while editing:

```bash
cd backend && uv run pytest -q -m "not integration and not medium"
```

One contract test compares an external study-plan file against its pinned checksum in `docs/baseline/SOURCE_CHECKSUMS.sha256`. That test can fail while the plan's owner is still editing the file. A failure there is not a reason to skip the rest of the fast tier, but the full tier does not pass until the checksum matches the file on disk.

### Full

Run `scripts/check.sh`. Do not add `--with-images` unless you want it to build the container images and run the smoke test on a throwaway stack.

The backend part of the gate is `scripts/test_backend.sh`. It runs pytest in parallel with pytest-xdist, using `WORKERS=${INVESTIGATORS_TEST_WORKERS:-4}` workers and `--dist loadfile`, and gives each worker its own throwaway PostgreSQL container. Do not use `-n auto`. A worker that has no `INVESTIGATORS_TEST_DATABASE_URL_GW<n>` of its own fails instead of sharing another worker's database. There is no option to run the whole suite against one shared database.

### What is not a tier

There is no way to run only the tests affected by a change. The repository has no Git history and no tool that maps changes to tests. Running only the test files for the code you touched does not replace the full tier before a deployment or before saying the repository passes.

A tool that selected affected tests would still need to include areas that many changes touch, not only the edited file: migrations and database roles, how manifests and instruments are bound, the definitions of exported metrics, the content store, the event names the frontend uses, and the entry and single-writer behavior.

Do not re-run a failed case-file test or a failed contract test in the hope that it passes. There is no list of known flaky tests to skip. If a test is ever found to be flaky, it needs an owner, a fixed seed, an expiry date, and a deterministic test of the same behavior.

## Timing profiles of the test suites

`scripts/profile_tests.sh` records one run of the unit suites under `docs/evidence/test-suite-plan/<utc>/`: a checksum, tool versions, the vitest JSON report, pytest durations, JUnit XML, and a short note. It does not start PostgreSQL and does not replace `scripts/check.sh`, and `check.sh` does not call it. It exits with an error when either unit suite fails.

## Deleting a test

The test review of 2026-10-13 did not delete any tests.

A test may be deleted only when all three of the following exist for that specific test:

1. Coverage evidence that another test runs the same code branch.
2. A mutation or injected fault that the remaining tests still catch.
3. A reason other than "it is slow" or "it has not failed lately".

On the evidence available in the 2026-10-13 review, these tests are not candidates for deletion: the sweep over a thousand generated task worlds, the dashboard benchmark with N=1000, the tests of database roles and append-only tables, the payment and commit guards, and the tests comparing historical and current manifests.

Trying a mutation-testing tool (`mutmut` or similar) is a separate decision for the repository owner. No such tool is installed. If one is approved later, the first run should cover only `app/measurement/telemetry.py` and `app/domain/world.py`, not the whole backend, and its results should go under `docs/evidence/test-suite-plan/`. Those results alone do not justify deleting a test.

## Directory map

| Directory | Contents | Status |
|---|---|---|
| `contracts/` | Repository-level checks: container and compose boundaries, the environment template against the settings code, pinned inputs, the requirement trace, the status ledger, reference fixtures, and the rules of the leakage scanner | In use |
| `fixtures/reference/` | Constants and expected behaviors and defects taken from the original demo (`behavior_reference.json`, `demo_constants.json`; implementation task INV-0003) | In use |
| `e2e/` | Notes for the browser tier. The Chromium smoke test and the four participant scenarios live in `frontend/e2e/` | The smoke test runs in the gate; the four scenarios are the optional browser tier |
| `load/` | Reserved for full-length concurrent sessions and fault cases | Empty; not part of this suite |

`fixtures/reference/` is the only fixture directory under `tests/`. The backend keeps its own fixtures, for example recorded task-world replays in `backend/tests/fixtures/world_replays/` and adversarial inputs in `backend/tests/adversarial_corpus.py`.
