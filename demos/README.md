# Playable demos

The playable mocks let you click through a participant's session in a browser, with no server, database, or model. They are for seeing what participants see: the consent and training screens, the task workspace, the team chat, the check-in, and the post-task questions. They are useful for getting oriented, for showing the task to colleagues, and for checking screen wording.

To play one, open `index.html` in this folder and pick a pilot condition. The `template/` folder holds the source file the demos are generated from. It is not a demo itself; open the demos from the landing page instead.

The mocks are not the platform. Agent teammate replies are canned, and none of the platform's server-side behavior (model generation, the checks on generated replies, scheduling, logging) runs. The table below lists what they do not simulate. To see how the real platform behaved with participants, use `pilots/analysis/transcript_viewer.html` instead.

The mocks are also not the frozen behavioral reference at `docs/baseline/reference/investigators_time_pressure_demo.html` (SHA-256 `7ced90c5e0c6f45882e9c1c54650fd0c23b137d744b3c9a138ea49d53d6b9825`). That file is an earlier mock kept unchanged as a fixed point of comparison. Do not edit it.

## How the mocks are built

`scripts/build_demo_mocks.py` reads the template `demos/template/investigators_demo.html` and writes all 560 variants, each as an HTML file and a JS file, to `frontend/public/demos/`. Running `python3 scripts/build_demo_mocks.py --check` confirms that the generated files match the template. On 2026-10-21 it printed `OK: 1120 demo mocks current`. That result is **Verified** for this up-to-date check only, not for anything the mocks show.

Any completion code on the finish screen is the placeholder `PLACEHOLDER-CODE`. It is not a payment code.

## What the mocks simulate and what they do not

Read this before using a mock to describe the platform.

| What you do in the mock | What happens | Status |
|---|---|---|
| Ask a teammate whether it is a bot | You get a canned social reply. You do **not** get the fixed truthful line the platform uses ("i'm an automated account, not a person" or "i'm an automated teammate account"). | **Not in demo.** On the platform, this is handled by `engines.is_automation_probe` and `engines.identity_line`. The result of testing the detector on 103 questions is in `docs/EVALUATIONS.md`. |
| Wait for a model reply | Nothing is generated. Both the "AI" and the "scripted" reply pools are canned (`SOCIAL` and `SOCIAL_SCRIPTED` in the template). | **Not in demo.** |
| See a check reject a model reply and substitute a pre-written line | The mock never calls a model and never runs `validate_teammate_output`. | **Not in demo.** |
| See the time-pressure remark from `remarks.tp_pressure` | The mock's remark matches the workload bank ("yikes this is a lot for 5 min…"), not the time-pressure bank that the profiles name. | **Not in demo** with the time-pressure bank's wording. |
| Open a profile with solo review (a stage in which the participant reviews their own case file alone before the team chat opens) | No generated file turns solo review on. The template's default is `solo: true`, but every generated file sets `solo: false`. | **Not in demo.** |
| Finish | The finish screen shows `PLACEHOLDER-CODE`. | Placeholder, not the code used in the field. |

## Variant matrix

The build script generates one file for every combination of the settings below. `scripted: false` means the file is labeled as the AI-teammate condition; the replies are canned either way.

| Setting | Values |
|---|---|
| Base task configuration | `nosolo_nl`, `nosolo_nl_scripted`, `nosolo_structured`, `nosolo_non-nl`, `ovnone`, `ovsome`, `ovfull`, `ovnone_thin`, `ovsome_thin`, `ovfull_thin` |
| Assistant turned off | suffix `noassist` |
| Time pressure turned off | suffix `nopress` |
| Optional AI Q&A page about the rules | suffix `aiqa` |
| Bonus wording turned off | suffix `nobonus` |
| Arm (the check-in condition) | `high`, `low`, `mixed` for every configuration. Configurations with `nopress` also get a file with no arm. |

In the base configuration names, `ovnone`, `ovsome`, and `ovfull` mean no, partial, and full overlap between the participant's crates and the agent teammates' crates, and `thin` means a smaller case file. Every generated configuration sets `solo: false`. Files are named `investigators_<variant>_<arm>_demo.html`, with a `.js` file of the same name beside it. Open the HTML file directly from disk in a browser; it loads the JS file next to it, and no server is needed.

## Variants included

The repository includes the six variants that match the pilot conditions. Run `python3 scripts/build_demo_mocks.py` to generate the rest.

| Pilot condition | File in `frontend/public/demos/` |
|---|---|
| Pilot 1, no overlap, full file | `investigators_ovnone_noassist_nopress_aiqa_nobonus_demo.html` |
| Pilot 1, no overlap, thin file | `investigators_ovnone_thin_noassist_nopress_aiqa_nobonus_demo.html` |
| Pilot 1, partial overlap, full file | `investigators_ovsome_noassist_nopress_aiqa_nobonus_demo.html` |
| Pilot 1, partial overlap, thin file | `investigators_ovsome_thin_noassist_nopress_aiqa_nobonus_demo.html` |
| Pilot 2, high workload | `investigators_ovsome_thin_noassist_aiqa_nobonus_high_demo.html` |
| Pilot 2, mixed workload | `investigators_ovsome_thin_noassist_aiqa_nobonus_mixed_demo.html` |

The suffixes match the settings recorded for the pilot sessions: assistant off, AI Q&A about the rules on, and bonus wording off. Pilot 1 had no check-in, so its files use `nopress`. The admin dashboard links to every variant, so generate the full set before using those links.

## Top bar and the skip control

During the task, the top bar shows how many crates have been recorded, a marker for each crate, a five-minute timer, and the participant's crate list. The control labeled `skip →` is a shortcut for testers. It ends the task and moves on to the post-task screens. It exists only in the mock, and its tooltip (the button's title attribute) says so.

## Screen order in the mock

The mock goes through these screens in order: entry, consent, instructions, training, an optional AI Q&A page when that setting is on, a connecting screen, a short "ready" screen, the task (with a check-in in configurations that have time pressure), the post-task questions, the debrief, and the finish code. The comprehension questions are part of training. This is the mock's own sequence. It is not guaranteed to match the platform's stage list, `STAGES` in `backend/app/db/schema.py`.

## Header stamp

The template header says it reflects build `9226e2a4` (2026-10-09). The body was edited after that date, including the workload remarks, the mixed arm, the AI Q&A page, and the no-bonus wording. Treat the header date as out of date. The build id is **Described only**: it names a build, not the date of the file you are reading.
