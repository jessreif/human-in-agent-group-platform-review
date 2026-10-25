# content/

This directory holds the study content: every setting, text, and questionnaire that a session uses, stored as versioned JSON files. Read this before changing anything a participant sees or any study setting. The backend loads and validates these files through `backend/app/domain/manifests/`.

Two terms are used throughout:

- A **profile** (its file is called a **manifest**) is the configuration of one study design: the task, the timing, the arms, how participants can interact with teammates, and which versions of the texts and questionnaires to use.
- An **artifact** is one versioned piece of content that a profile refers to, such as a bank of teammate lines, a prompt, a consent text, or a questionnaire.

## What is here

| Path | Contents |
|---|---|
| `profiles/<manifest_id>.json` | The profiles. See [`profiles/README.md`](profiles/README.md) for the full list and which ones the pilots used. |
| `profiles/diffs/` | Reviewed records of the differences between a reference profile and a profile derived from it (an early proposed pilot profile or a natural-language-only variant). They are checked against the resolved policies. |
| `scripts/` | Artifacts for what teammates and the assistant say: warnings, request wording, teammate voices, fallback lines, prompts, check-in text, teammate time remarks, and the text shown in the peer-evaluation conditions. See [`scripts/README.md`](scripts/README.md). |
| `instruments/` | Questionnaires and the texts for consent, training, and debriefing. See [`instruments/README.md`](instruments/README.md). |
| `policies/` | Payment policies and a timing overlay for quality-assurance testing that runs the task clock ten times faster. |
| `coding/` | Content for the offline coding of participant messages. It is not used by sessions. See [`coding/README.md`](coding/README.md). |
| `schemas/` | JSON Schemas generated from the code. Do not edit them by hand. See [`schemas/README.md`](schemas/README.md). |
| `published/manifests.json` | The publication index: for each published profile, its SHA-256 hashes and the date it was published. Entries are only ever added. Never edit a published profile or artifact; publish a new version instead. |

Artifact files are named `<artifact_id>.v<N>.json`, where `N` is the version.

## Adding or changing content

1. Add a new version of the artifact or profile as a new file. Do not edit an existing published version.
2. From `backend/`, run `uv run python -m app.domain.manifests.cli publish --date YYYY-MM-DD` to add it to the publication index.
3. From `backend/`, run `uv run python -m app.domain.manifests.cli docs --write` to regenerate the documents that are built from this content.
4. Run `scripts/check.sh` from the repository root.

## Rules

Everything in this directory is part of the source fingerprint (the SHA-256 over the application source) and is built into the container images. Nothing here may contain secrets or participant data.
