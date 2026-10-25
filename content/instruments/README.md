# Instruments

This directory holds the versioned questionnaires participants answer and the texts for consent, training, and debriefing. Profiles in `content/profiles/` refer to these files by artifact id and version. Files are named `<artifact_id>.v<N>.json`, and a published version is never edited; a change means a new version.

| Artifact | Versions | What it is |
|---|---|---|
| `post_task.tp_demo`, `post_task.pe_demo` | v1–v9, v1–v2 | Questionnaire after the task, for the time-pressure and peer-evaluation studies |
| `confidence.demo` | v1–v3 | Confidence ratings for the participant's answers |
| `peer_ratings.pe_demo` | v1 | Ratings of teammates in the peer-evaluation study |
| `open_ended.tp_followup` | v1 | Open-ended follow-up questions in the time-pressure study |
| `consent.reference`, `consent.reference.nobonus` | v1–v9, v1 | Consent text (with and without the bonus offer) |
| `training.reference` | v1–v8 | Training and instructions text |
| `debrief.tp_reference`, `debrief.pe_reference` | v1–v4, v1 | Debriefing text for each study |

The consent, training, and debrief files have the artifact kind `material_reference`; the others have the kind `instrument`. The `notes` field in each file explains what changed in that version.

Final participant wording was tied to research decisions D03 and D06 (see `docs/DECISIONS.md`). Storing participants' responses to these instruments is covered by implementation tasks INV-0905 and INV-0906 in `docs/IMPLEMENTATION_STATUS.md`.
