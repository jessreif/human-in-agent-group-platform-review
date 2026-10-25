# Development history

This page summarizes how the platform was built and revised before the pilots in `pilots/`. The detailed working records (dated build logs, audit rounds, design-feedback rounds, and earlier pilot exports) are not included because they describe superseded versions. `docs/IMPLEMENTATION_STATUS.md` is the task ledger from that period. The summaries of simulated-participant runs are kept under `docs/evidence/*/run-*/summary.md` because `docs/agent/` cites them.

## Build (2026-09-28 to 2026-09-30)

The platform was built in numbered work packages: the session clock and job scheduler, the agent teammate engines and checks, the participant workspace and local model adapter, study modules and instruments, measures and exports, and the researcher controls. Each package closed with its own tests. The first full run of the services together, driven through a real browser, was on 2026-09-30.

## Design feedback and code audits (2026-10-01 to 2026-10-07)

Researchers worked through the participant experience in nine rounds of written design feedback. Each round was triaged and either implemented or declined with a reason. Independent code audits ran alongside them, and their findings were fixed in numbered remediation rounds. In this period the agent teammate decisions in `docs/agent/` were implemented, the model moved to the 26-billion-parameter Gemma 4 model, and the pools of pre-written fallback lines were sized against the length of the task.

## Simulated participants (from 2026-10-04)

Simulated participants (`backend/harness/`) completed whole sessions against the running platform. Their runs were used to find failures before any participant saw them, and they were repeated after major changes. A risk assessment on 2026-10-08 ran both the model-generated and the bank-only configurations through the harness and fixed what it found.

## Earlier pilots (from 2026-10-06)

Before the current version, 123 participants completed sessions on earlier versions of the platform, across several waves. After each wave the team reviewed the chat transcripts and participants’ feedback and revised the agent teammates’ instructions and the task instructions. These waves also added case-file telemetry, the optional rules Q&A, and the crate overlap and case-file size settings used in Pilot 1. Their data are not included here; the pilot data in `pilots/` cover only the 90 participants who completed the current version.

## Current version (2026-10-16 to 2026-10-18)

Pilot 1 (`study.live.v14`) and Pilot 2 (`study.live.v15`) ran on the current version. Their data and analysis are in `pilots/`.
