# Browser tests

This page explains the browser tests that drive the participant interface in a real browser. The test code itself lives in `frontend/e2e/`; this directory holds only these notes.

## Chromium smoke test

`npm run test:e2e`, run from `frontend/`, starts a smoke test. The gate (`scripts/check.sh`) also runs it as one of its steps. The smoke test only opens `about:blank` in Chromium to confirm the browser starts; it does not touch the study. It uses the Chromium build already installed on the lab host for other applications, found through `PLAYWRIGHT_BROWSERS_PATH`. Setting it up did not download a browser or install system packages.

## Participant scenarios

Running the same command with `INVESTIGATORS_E2E=1` set also runs four participant scenarios. The command starts its own throwaway API and database. It does not replace `scripts/check.sh`.

The scenarios did not pass on the first attempts. Notes on two of the cases, the killed event-stream connection and the page departure after a drag-to-share, were written up in `docs/evidence/test-suite-plan/JOURNEY.md`, which is not included in this repository.

The run that passed was on 2026-10-13, with Chromium only. It checked that:

- a participant can enter through a preview link, go through the task, and close the page;
- the event stream delivers a message, survives a killed connection, and then either reconnects or falls back to polling;
- a case-file card that stays on screen for the real 300 ms counts as viewed and a card off screen does not, both at desktop width and at 900 px or narrower;
- reordering with the keyboard works, a drag-to-share is recorded, and telemetry is delivered when the page closes.

Firefox was not part of this run.
