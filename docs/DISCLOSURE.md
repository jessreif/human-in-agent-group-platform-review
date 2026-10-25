# Disclosure ledger

This ledger records what the participant is told, what the design withholds, and where the correction happens. It does not rewrite the sentences. Quoted lines are the text in this tree. Status labels are defined in `docs/README.md`.

## What “no deception” covers here

**Implemented.** Teammates do not claim to be human. An automation question is classified by `engines.is_automation_probe`. The reply is `engines.identity_line`: a line from the profile’s `identity_probe` bank when that rule exists, otherwise the code default “i'm an automated account, not a person”. The banks in `content/scripts/fallbacks.neutral.v6.json` and `fallbacks.demo.v5.json` contain that sentence and “i'm an automated teammate account”. The line is not model text. The detector’s agreement with the 103-item corpus is **Verified** in `docs/EVALUATIONS.md`.

The principle does **not** mean that every screen describes the design before the task. Consent says some features are withheld until the end. The connecting screen, the check-in, and the bonus sentence are part of that withholding. The debrief is where those points are corrected. The code matches that design. It is not a bug relative to the consent form. A statement that agent teammates never deceive participants is accurate only in the narrower sense above: they never claim to be human and always answer truthfully when asked.

## Consent (before the task)

Artifact: `content/instruments/consent.reference.v9.json`. The same task sentence is in consent v2 through v8 and in `consent.reference.nobonus.v1.json` (the no-bonus artifact drops the bonus sentence; it does not describe the crate task either).

| Statement in the form | Why it is there | Where it is corrected | How consent covers it |
|---|---|---|---|
| “Your team will be given information about a business decision and asked to discuss it by text chat and make a recommendation.” | It is the consent text in the approved IRB protocol, confirmed on 2026-10-21 as approved cover language. | The instructions and the task screen describe the crate task. The debrief names an investigative task when time pressure is off, and workloads when time pressure is on. | The next consent sentence says some features are not described in advance because telling people would change behavior, and that those features are explained at the end. |
| “There are some features of this study's design that we are not describing to you now… We will explain these fully at the end of the session, before you finish.” | The withheld pieces are that the teammates are automated, that check-in ratings are assigned, and, when the bonus flag is on, that the bonus is paid to every completer. | `DebriefScreen` in `frontend/src/components/StageScreens.tsx`. | This is the consent sentence that authorizes the withholding. |
| “This study may use AI tools to generate content you see during the session. The AI tools used in this study run on a secure university-managed server.” | The assistant and, on AI profiles, teammate replies can be model text. | Not retracted. The debrief adds that the teammates were automated systems. | Consent discloses AI generation and the local server. It does not say the teammates may be automated. |
| “you will be paid $3.00 and will be eligible to receive a bonus of up to $0.50 based on your performance” | Shown when the bonus flag is on. Policy `payment.s5_base_only.v5.json` is a flat base. The debrief says the accuracy bonus is paid to every completer. | `DebriefScreen`, only when `state.bonusPayment` is true. | The performance wording is the withheld feature. The debrief corrects it. **Implemented** in the component. Not re-tested in the recorded run. |

## During the task

| What the participant sees | Design reason | Correction |
|---|---|---|
| Connecting screen implies other people are pressing Start (`CONNECT_PM_JOIN_WALL_MS` is 10600 in the client timing). | The session should feel like a team is forming. | Debrief: teammates were automated systems operated by the research team. |
| Check-in copy says teammates are answering. Artifacts: `checkin.pressure_demo.v1` / `v2` and `checkin.workload.v1` / `v2`. | The manipulation is the rating the participant sees. | Debrief, when time pressure is on: the workload ratings were assigned by the research team, not reported by anyone. The participant’s own rating was recorded as entered. |
| Remark lines differ by arm (`content/scripts/remarks.tp_pressure.v1.json` on the time-pressure reference profiles). | The words are part of the manipulation. | Not separately corrected. They are teammate messages, covered by the debrief’s statement that teammates were automated. |

## Debrief and data use

`DebriefScreen` is hardcoded in `frontend/src/components/StageScreens.tsx`. It is not the body of `debrief.tp_reference@v4`. The current non-peer-eval paragraph is about workloads when time pressure is on, and about sharing information when it is off. Peer evaluation (`studyKey === "investigators_peer_eval"`) says no other participants were present and that no ratings of the participant were collected from teammates.

`DataUseScreen` says responses will be used as described in the consent form and that the decision does not change payment. Continue on the debrief records a `use_all` disposition and goes to the payment code (comment: UX round 2, item 13).

## What the playable demo does

**Not in demo.** The mock’s chat returns a canned social line. It does not run the probe detector. See the first table in `demos/README.md`.
