# Profiles

This directory holds the study profiles. A profile (its JSON file is called a manifest) is the full configuration of one study design: the study it belongs to, the arms, the task layout, the timing, how participants can talk to their agent teammates, whether teammate replies may come from the language model, and which versions of the texts and questionnaires in `content/scripts/` and `content/instruments/` it uses. Each session is tied to exactly one published profile. Read this to find out which profile is which and which ones the pilots used.

Profiles are never edited after publication. A change means a new file with the next version number (`_v<N>`). `content/published/manifests.json` lists every published profile with its SHA-256 hashes and publication date. At present it lists 85 profiles, the same 85 files that are in this directory. The `notes` field of each profile explains what changed from the previous version.

## Fields worth knowing

- `profile_kind` is `reference` (a profile kept to reproduce the original demo, for previews and QA only) or `proposed_pilot` (a profile intended for fielding).
- `material_status` is `reference`, `proposed_unapproved`, or `approved`. Only an `approved` `proposed_pilot` profile whose linked decisions in `docs/DECISIONS.md` are all resolved may list `live` in `modes_allowed`; the schema rejects anything else.
- `teammates.social_generation` is `model_with_validated_fallback` (teammate replies may come from the model, checked by the validator and replaced by fixed fallback text if rejected) or `deterministic_only` (all teammate text is fixed in advance).
- `interaction.mode` is `structured_and_nl` (request buttons per crate plus free-text chat) or `nl_only` (free-text chat only). The oldest profiles omit it.
- `task` gives the task layout. Most profiles have 8 crates, 6 hideouts, and 4 crates assigned to the participant. The `tp_ai_ov*` profiles add `crate_overlap` (how many crates the participant shares with the agent teammates) and, for the `_thin` variants, `participant_intel_budget: 20` (a smaller case file of about 20 records).
- `condition_set` lists the arms. For the time-pressure study, each arm sets the workload values the agent teammates report at the check-in.

## Profile families

| Family | Versions | Study | Kind and status | Teammate replies | Interaction | Allowed modes | Published |
|---|---|---|---|---|---|---|---|
| `tp_demo_v*` | v1–v6 | Time pressure | reference | model with fallback | `structured_and_nl` from v4 | preview, qa | 2026-09-28 to 2026-10-05 |
| `tp_demo_nl_v*` | v1–v3 | Time pressure | reference | model with fallback | `nl_only` | preview, qa | 2026-10-03 to 2026-10-05 |
| `pe_demo_v*` | v1–v6 | Peer evaluation | reference | model with fallback | `structured_and_nl` from v4 | preview, qa | 2026-09-28 to 2026-10-05 |
| `pe_demo_nl_v*` | v1–v3 | Peer evaluation | reference | model with fallback | `nl_only` | preview, qa | 2026-10-03 to 2026-10-05 |
| `pe_pilot_v*` | v2–v7 | Peer evaluation | proposed pilot, not approved | fixed text only | `structured_and_nl` from v5 | preview, qa | 2026-09-28 to 2026-10-05 |
| `tp_review_first_v*` | v2–v7 | Time pressure | proposed pilot, not approved | fixed text only | `structured_and_nl` from v5 | preview, qa | 2026-09-28 to 2026-10-05 |
| `tp_review_first_v*` | v8–v9 | Time pressure | proposed pilot, approved | fixed text only | `structured_and_nl` | preview, qa, live | 2026-10-06 |
| `tp_review_first_v*` | v10–v11 | Time pressure | proposed pilot, approved | model with fallback | `structured_and_nl` | preview, qa, live | 2026-10-07 |
| `tp_scripted_v1` | v1 | Time pressure | reference | fixed text only | `structured_and_nl` | preview, qa | 2026-10-07 |
| `tp_scripted_v*` | v2–v13 | Time pressure | proposed pilot, approved | fixed text only | `structured_and_nl` in v2, `nl_only` from v3 | preview, qa, live | 2026-10-08 to 2026-10-15 |
| `tp_ai_v*` | v1–v14 | Time pressure | proposed pilot, approved | model with fallback | `nl_only` | preview, qa, live | 2026-10-08 to 2026-10-15 |
| `tp_ai_ovnone_v*`, `tp_ai_ovnone_thin_v*` | v1–v3 | Time pressure | proposed pilot, approved | model with fallback | `nl_only` | preview, qa, live | 2026-10-14 to 2026-10-15 |
| `tp_ai_ovsome_v*` | v1–v3 | Time pressure | proposed pilot, approved | model with fallback | `nl_only` | preview, qa, live | 2026-10-14 to 2026-10-15 |
| `tp_ai_ovsome_thin_v*` | v1–v9 | Time pressure | proposed pilot, approved | model with fallback | `nl_only` | preview, qa, live | 2026-10-14 to 2026-10-18 |
| `tp_ai_ovfull_v*`, `tp_ai_ovfull_thin_v*` | v1–v3 | Time pressure | proposed pilot, approved | model with fallback | `nl_only` | preview, qa, live | 2026-10-14 to 2026-10-15 |

In the publication index, v3 of each `tp_ai_ov*` family carries the date 2026-10-14 and v1 and v2 carry 2026-10-15.

The time-pressure arms are `high` and `low` in every profile except `tp_ai_ovsome_thin_v4` to `v9`, which use `high` and `mixed`. The peer-evaluation arms are `eval` and `control`.

### The crate-overlap profiles

The `tp_ai_ov*` profiles vary how many crates the participant has in common with the agent teammates. The setting is `task.crate_overlap`:

| Setting | `task.n_crates` |
|---|---|
| `ovnone` (no overlap) | 12 |
| `ovsome` (partial overlap) | 8 |
| `ovfull` (full overlap) | 4 |

The `_thin` variants also give the participant a smaller case file (`participant_intel_budget: 20`). The crate-overlap feature is switched on by the `CRATE_OVERLAP_ENABLED` setting in `backend/app/settings.py`, which is off by default.

The `title` field of the `tp_ai_ovnone_thin_v*` and `tp_ai_ovfull_thin_v*` profiles says "some crate overlap". That wording was copied from the `ovsome_thin` title and is wrong; the `task.crate_overlap` field (`none` or `full`) is correct.

## Profiles used in the pilots

Two pilots with participants recruited on Prolific used these profiles. [`../../pilots/README.md`](../../pilots/README.md) describes them in full.

| Pilot | Batch group | Profiles | Design |
|---|---|---|---|
| Pilot 1 | `study.live.v14` | `tp_ai_ovnone_v3`, `tp_ai_ovnone_thin_v3`, `tp_ai_ovsome_v3`, `tp_ai_ovsome_thin_v3` | Crate overlap (none, partial) × case file (full, thin). No workload check-in. |
| Pilot 2 | `study.live.v15` | `tp_ai_ovsome_thin_v9` | Partial overlap, thin case file. Workload check-in with a `high` and a `mixed` arm. |

## Difference records

`diffs/` holds reviewed records of the differences between a reference profile and a profile derived from it, named `<reference>__<derived>.json`. They cover each `tp_demo` and `pe_demo` version (v1 to v6) paired with the matching `tp_review_first` or `pe_pilot` version (v2 to v7), and `tp_demo`/`pe_demo` v4 to v6 paired with the `_nl` variants v1 to v3. There are no difference records for the `tp_scripted` or `tp_ai` families.
