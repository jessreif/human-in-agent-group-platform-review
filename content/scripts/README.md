# Scripts

This directory holds the versioned texts that make up what the agent teammates and the AI assistant say, and the study texts that differ between conditions. Profiles in `content/profiles/` refer to these files by artifact id and version. Read this to find the file behind a line a participant saw. Files are named `<artifact_id>.v<N>.json`, and a published version is never edited; a change means a new version.

A "bank" here is a file of prepared lines that the platform draws from.

| Artifact | Versions | What it is |
|---|---|---|
| `warning.one_minute_left` | v1–v3 | The warning shown when one minute of task time is left |
| `requests.demo` | v1–v4 | Wording of the record requests the agent teammates send |
| `voices.tp_demo`, `voices.pe_demo` | v1–v2, v1 | Banks of each teammate's fixed lines (their "voice") for the time-pressure and peer-evaluation studies |
| `fallbacks.demo` | v1–v5 | Fallback bank first written for the reference (demo) profiles: lines used in place of model output that is missing or rejected |
| `fallbacks.neutral` | v1–v6 | Fallback bank first written for the proposed pilot profiles, with lines that do not hint at the condition. The `tp_ai_*` pilot profiles bind this bank |
| `prompt.assistant_demo` | v1–v4 | The model prompt for the AI assistant |
| `prompt.social_demo` | v1–v5 | The model prompt for the agent teammates' social replies |
| `checkin.pressure_demo`, `checkin.workload` | v1–v2 each | Text of the check-in questionnaire shown during a time-pressure task |
| `remarks.tp_pressure`, `remarks.tp_workload` | v1, v1–v3 | Fixed team-chat remarks by the agent teammates about time or workload, which differ by arm |
| `exposure.pe_eval_demo`, `exposure.pe_control_demo` | v1 each | Text shown in the `eval` and `control` arms of the peer-evaluation study |

The `notes` field in each file explains what changed in that version. Which profile uses which version is set in the profile itself (for example, `teammates.fallback_bank` names the fallback bank); [`../profiles/README.md`](../profiles/README.md) lists the profiles.
