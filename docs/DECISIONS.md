# Decision register (INV-0004)

Terminology key: `docs/GLOSSARY.md` (D-numbers, profile/arm names, roles).

Opened 2026-09-28 against spec revision `3dce3bb3…` (§13) and plan §3. Candidate values come from the specification; they are proposals, not operational defaults. **Update 2026-10-01: D05 is resolved** (IRB amendment approved — see the amendment note below). **Update 2026-10-07: D01, D02, D04, D06, D07, D10 and D13 are resolved** for the N=5 fielding of `tp_review_first_v10` under owner direction 2026-10-06/07 (IRB office-hours consultation confirmed the blanket protocol covers the fielded changes — no further amendment was required; recorded per the BA-98/BA-102 audit remediation in `docs/evidence/2026-10-07-audit-remediation/`). Remaining unresolved: D03, D08, D09 (residuals), D11, D12, D14. Preview settings that use a candidate stay visibly preview-only. Each record will gain: selected value, rationale, applicable approved material/version, responsible reviewer, decision date, affected profiles and tasks requiring revalidation.

Roles (to assign; one person may hold several): **RL** research lead, **EL** engineering lead, **OP** operator (sudo), **IRB** IRB liaison.

**Amendment update 2026-10-01.** Protocol XXXX-XXXX amendment **approved** (researchers: [Principal Investigator], [Co-Investigator]; next check-in 09/09/2027) covering two changes: (1) the LLM disclosure — Gemma 4 (or the latest released Gemma) self-hosted on backup-host via Ollama, SHA-256-verified frozen weights, per-message model logging, no third-party transmission **except a university AI service fallback when the GPU is overwhelmed**; (2) the risk/interaction description — generated (not scripted) teammate and assistant contributions, frozen system prompts, automated pre-display classifier, full audit logging, natural-language chat/requests/shares. Consequences recorded on the affected rows below: D05 is resolved; D03 is partially addressed; the openai/minimax lab-comparison adapters are **not** the approved fallback endpoint and — since the BA-35 remediation (2026-10-02) — are refused for **all** session traffic (worker refuses to start under a remote `MODEL_PROVIDER`; `run_generation` refuses per job), remaining constructible for offline fixture comparisons only. Preview/QA sessions can carry human tester text, so `APP_MODE` is not a sufficient participant-data boundary.

**Sequencing policy (owner direction 2026-09-29; superseded 2026-10-07).** The original policy bundled all IRB-relevant open decisions into one amendment round before paid piloting. **Superseded:** an IRB office-hours consultation (main-campus IRB, protocol XXXX-XXXX) confirmed the blanket protocol — together with the 2026-10-01 amendment — covers the fielded changes, so no further amendment was required. The owner directed fielding the N=5 pilot directly on `live`; the decisions the pilot exercises are recorded resolved below (D01, D02, D04, D06, D07, D10, D13). The residuals (D03, D08, D09, D11, D12, D14) remain unresolved and non-blocking for a TP-only N=5 pilot; they must not be treated as settled by this fielding, and each retains its "needed by" gate before the work it blocks (notably D12 before interpreting or expanding the pilot, and PE-related decisions before any PE fielding).

**Four-condition launch (owner direction 2026-10-08; corrected same day).** Fielding
moves from the single `tp_review_first_v11` pilot to a 2 × 2 between-subjects design —
teammates (AI vs scripted) × pressure (high vs low) — through one platform link, batch
group `study.live.v2` (members `study.live.v2.ai` → `tp_ai_v1`,
`study.live.v2.scripted` → `tp_scripted_v3`; permuted blocks of four over all cells,
assigned at consent). Both builds bind every approved v11 artifact verbatim (consent
v4, instruments, check-in copy, payment, neutral fallback bank, assistant).
**Corrected design:** the scripted build `tp_scripted_v2` originally kept the v11
ask picker (`structured_and_nl`), which would have made interaction surface a third,
confounded difference between conditions. `tp_scripted_v3` removes it — every launch
cell is `nl_only` typed chat — so the ONLY differences across the four cells are (1)
the pressure arm and (2) teammate generation: `model_with_validated_fallback` (AI,
prompt.social_demo@v3) vs `deterministic_only` (scripted, approved
`fallbacks.neutral@v5` pools including the `asks_intel_nl` typed-ask lines the AI
condition falls back to). This **extends D01** (fielded profile → the two launch
builds) and **D07** (the generated-contribution resolution now applies to the AI
condition; the scripted condition is the deliberate deterministic contrast). The
owner records that the main-campus IRB office-hours consultation covers Group Chat /
AI Assistant behavior adjustments within the existing task, so no amendment is sought.
`tp_review_first_v11`, `tp_scripted_v2`, and the `study.live.v1`/`study.smoke.v1`
batches and groups are closed; all prior test/preview sessions were removed before
launch after a verified backup (`backups/pre-launch-20261008/`, evidence in
`docs/evidence/2026-10-08-four-condition-launch/`).
**Required-items follow-on (owner direction, same day):** the fielded pair is
`tp_ai_v2` / `tp_scripted_v4` behind group `study.live.v3` (smoke `study.smoke.v3`),
binding `consent.reference@v5` — which removes the sentence "You may skip questions
during the study and doing so will not affect your payment." — plus
`confidence.demo@v3` and `post_task.tp_demo@v4` with `skippable: false` on every
item and `own_rating_skippable: false`. The server rejects `item_skip` and
`pressure_skip` on these builds (`skip_not_permitted`), `proceed` stays
`instrument_incomplete` until every declared item is answered, and the UI disables
Continue until the page's required items have values. All prior consent versions
(v4 and earlier, which carried the skip promise) remain pinned to their historical
manifests. This **extends D04/D06**: the v4 skip-promise clause is no longer the
fielded rule — under v5 every measurement item is required to advance, so the
"does not affect payment" statement is moot rather than contradicted.
**IRB scope confirmed (office hours, 2026-10-08):** the IRB prefers demographic
questions remain optional/skippable absent exceptional circumstances — this
protocol asks no demographic questions (demographics come from the platform per
the consent's data section), so nothing needs to be optional on that basis — and
is comfortable with required **study-content** questions. The office also flagged
the failure mode they care most about: participants reaching the end and being
told "you didn't answer an optional question, so no pay." Under the required-items
builds that failure mode is structurally impossible — completion implies every
measure answered, so every participant who finishes through Prolific/Connect is
paid, unconditionally. `study.live.v2` / `study.smoke.v2` are closed.
**Slider UX (2026-10-08, later):** confidence/rating sliders adopt the prior-study
empty-state pattern — an untouched slider reads `—` with the hint "Move the
slider to answer. It has no value until you do." and never carries a semantic
value until moved (the thumb parks at the midpoint purely visually). Live app
and all demo mocks enforce this: a required slider blocks Continue until moved,
so no build can record a fabricated default such as 50.

**Risk remediation (owner direction, 2026-10-08):** accept the content fix, not the
52% repeated-template risk. Approved `requests.demo@v4` preserves all five approved
wordings but draws without replacement in a session-wide keyed permutation.
Immutable successors `tp_ai_v3` / `tp_scripted_v5` differ from v2/v4 only in that
binding; prepare `study.live.v4` and `study.smoke.v4` with new member batches.
This extends D06's script freeze; old published artifacts/manifests and batch pins
remain unchanged. The owner authorizes stamped deployment and live-model/browser
smoke, **not** test-data reset or opening paid enrollment.

**Pilot revision (owner + PhD student, 2026-10-09, after the first N=5 pilot):**
immutable successors `tp_ai_v4` / `tp_scripted_v6`, fielded through new group
`study.live.v5` (smoke: `study.smoke.v5`). (1) `consent.reference@v6`: "approximately
12 minutes ... $3.00" (was 15 minutes / $4.00), paired with `payment.s5_base_only@v4`
(300 cents); the owner confirms the rate exceeds the university IRB $10/hour minimum and
approves the wording for live. (2) Active budget 360 -> 300 s (schema now admits 300
or 360 per profile); check-in unchanged at active 60; scripted requests 45 / 130 / 210
(first one before the check-in); one-minute warning `@v3` at active 240.
(3) `remarks.tp_pressure@v1`: two fixed arm-specific teammate chat lines at active 75
and 83 (15 s / 23 s after the check-in resumes). Speaker order is a per-session fair
coin on the logged `jobs` stream (owner choice, balanced seats). The lines are bound
copy delivered identically in both teammate builds and bypass generation, so the
generated-output gate (incl. `condition_leak`, which they do not trip; `voice_format`,
which the `!` would) is not in their path and is unchanged. The AI social prompt is
deliberately unchanged (owner choice) so the reinforcement is the same in both
engines. Old pins (`study.live.v4`, 360 s, $4.00) remain immutable history.

AI-fidelity operational rule selected by the owner: after the first two live AI
sessions with eligible delivered replies in the same cohort/build/compatibility
key, pause the whole group and platform intake if the reply-weighted generated
share is strictly below **80%**, or audit linkage is missing. Exactly 80% passes.
Review after every subsequent AI session. This is a recruitment stop rule, not a
post-treatment exclusion or a resolution of D12. Count delivered model-backed
JT/PM replies, not assistant calls, attempts/retries, requests, fixed disclosures
or acknowledgements. `Monitor → teammate_fidelity` exposes the recommendation;
operators perform the pause. Record fallback-heavy sessions and preserve payment
and analytic decisions independently.

During this pilot freeze `lifecycle.py` / `scheduler.py` except blocking fixes
authorized by the owner; run the full gate and a new completion/live-model smoke
after every exception. The G6 request-selection edit is the explicitly authorized
pre-launch content remediation, not a general refactor.

**Assistant-generation remediation (audit + release gate, 2026-10-10):** the
second pilot's first live AI session on `study.live.v5` rejected 100% of its
assistant calls to deterministic fallback — the group was paused on discovery
(RCA-2026-10-09, `docs/ROOT-CAUSE_2026-10-09_assistant-generation-rejections.md`),
then the follow-up audit (`docs/assistant-generation-rejections-audit.md`) found
14 findings + 8 hardening items (`docs/assistant-generation-rejections-remediation.md`
is the disposition record). Immutable successors `tp_ai_v5` / `tp_scripted_v7`
behind `study.live.v6` were minted for the first
remediation round and the live group **never opened** (its companion
`study.smoke.v6` carried the v3-checker smoke sessions): the release panel's first cell caught
`prompt.assistant_demo@v3` producing location-label prose that violates the
`grounded_prompts_v7` verbatim-excerpt grammar (12/12 replayed trials rejected).
Approved successor `prompt.assistant_demo@v4` (excerpt-only replies; 12/12
replayed trials accepted `record_excerpts`, zero violations) is bound by
`tp_ai_v6` / `tp_scripted_v8`, fielded through new group **`study.live.v7`**
(smoke: `study.smoke.v7`). Code pins: `assistant_output_claims_v5`,
`grounded_prompts_v7`, `teammate_output_v8`, `assistant_fallback_v2`,
`semantic_record_v1`. The complete 20-cell signed-preview release panel passed on
deployed build `1039f9cb…` (`release/evidence/assistant-panel-v1-2026-10-10.json`,
`scripts/check_assistant_release.py` → passed; monitor preview partitions show
4/4 generated successes per AI arm, zero violations, no alerts). Enrollment on
`study.live.v7` opened under owner authorization for a second pilot round (N=4);
the paused `study.live.v5` and never-opened `study.live.v6` and their pins remain immutable history. The scripted-cell
deterministic assistant is designed parity, not a defect — the panel counts
intended-vs-actual provider dispatch separately.

**2026-10-10 incentive-compatibility succession (owner direction):** the second
pilot block (N=4, one per cell) completed with team-dependent accuracy 0/8, so
the fielded line adds an incentive-compatible offer: `consent.reference@v7`
("paid $3.00 **plus a $0.50 bonus** for correctly locating your assigned
crates"), paired with `payment.s5_base_only@v5` (350¢ flat to every completer —
per owner/university IRB an offered incentive paid out to everyone is low-risk
non-deception; `bonus.type` stays `none` because the contingent frame is copy,
never a payout rule). Instructions ("Case background"), the task answer bar,
and `debrief.tp_reference@v3` ("the accuracy bonus … is paid to every
completer") carry the matching sentences; `training.reference@v3` versions the
instructions pointer so the offer is provable from the bound manifest. This is
a pilot incentive frame, **not** a pre-registered condition — identical across
all four cells, so the 2×2 is unchanged. Successor profiles `tp_ai_v7` /
`tp_scripted_v9` field through group **`study.live.v8`**; `study.live.v7` closes
(its four completers consented v6 / 300¢ — analytically separate by
compatibility key). Admin-dashboard hygiene from the same round: assistant-monitor
partitions now carry a `current` flag against the deployed source checksum, so
superseded-build incident history renders labelled rather than as live failures.
**Deployed and opened 2026-10-10** on source bundle
`cf583ca756ddc390aeaa1efa7fcabe2ae63e20f2e67b1ea052bc47ca176781da`
(`pinned_build: true`, all containers healthy): post-deploy mutating smoke —
preview entry → consent `consented` against `consent.reference@v7` on
`tp_ai_v7` — accepted (rev 4, arm drawn, stage advanced); live-group TEST entry
through `study.live.v8` binds consent v7 / payment v5 / training v3 / debrief
v3 (abandons unconsented, no slot consumed). `study.live.v8` enrollment open;
`study.live.v7` closed. **2026-10-11 update:** `study.live.v8` enrollment
closed by owner direction — the v9 round (`study.live.v9` +
`study.smoke.v9`) opened the same day for the N=5 Connect pilot,
binding `tp_ai_v11`/`tp_scripted_v13` (AID-59–AID-62). The v8 completers stay
pinned to `tp_ai_v7`/`tp_scripted_v9` by compatibility key.
**2026-10-12 update:** the fielded design moved from 2 × 2 to **1 × 2** —
`study.live.v9`/`study.smoke.v9` closed by owner direction and **`study.live.v10`
opened** for the N=4 Connect pilot with AI teammates only (`tp_ai_v11` ×
high/low; members `study.live.v10.ai`/`.ai2`, both same-manifest, cells
balanced by arm in blocks of four). The scripted contrast is retired from
live fielding behind `SCRIPTED_TEAMMATES_ENABLED=0` (AID-63 — `tp_scripted_*`
manifests refuse live mint/enrollment/consent while preview/QA remain
available); the N=30 round is AI × pressure only, for effect-size estimation
and power analysis toward the as-predicted pre-registration — no
AI-vs-scripted contrast is collected. Deployed on source bundle
`b165395b…` after a green `scripts/check.sh`; smoke verified on
`study.smoke.v10` (`TEST_V10SMOKE_E`: entry → consent `consented` on
`consent.reference@v8`, arm=low → `set_initials`); 28 preview sessions
tombstoned via `/admin/cleanup` after a fresh `pg_dump` backup. Dashboard
gained a `version` (batch-group) cohort filter defaulting to the latest
`study.live.*` group. The v9 completers stay pinned by compatibility key.

## A. Research decisions D01–D14

| ID | Status | Question to decide | Candidate values (source) | Owner | Needed by / blocks | Affected tasks |
|---|---|---|---|---|---|---|
| D01 TP exposure/profile | **resolved 2026-10-07** — review-first family fielded as published manifest `tp_review_first_v10` (fielded through live batch `prolific.live.n5.v10`, successor to the v9-pinned `prolific.live.n5`): 60 s review with teammate and AI channels closed, requests at interaction 20/120/210 = active 80/180/270, no early-submission endpoint. Rationale: review-first preserves the manipulation's separation of evidence review from interaction and matches the spec §4.1 recommendation; v10 supersedes v9 (first live-eligible promotion) for the BA-96–BA-102 remediation. Applicable material: `tp_review_first_v10` + its approved bound artifacts. Reviewer: RL (owner direction 2026-10-06/07). Revalidation: pilot data review before expansion (D12). **Update 2026-10-07 (later same day):** `tp_review_first_v11` supersedes v10 — the `solo_review` phase was dropped under owner direction after protocol review found no mention of a solo condition and lab sessions showed zero solo-window activity; all other bindings identical, batches `connect.live.v11` / `prolific.live.n5.v11` / `connect.smoke.v11` succeed the v10 batches (enrollment closed on those) | Which TP profile is fielded; channel lock during review (incl. whether AI stays available); scripted-request schedule; confirm no early-submission endpoint in v2 | `tp_review_first_v2` recommended (spec §4.1, §13): 60 s review with teammate and AI channels closed, requests at interaction 20/120/210 = active 80/180/270; alternative `tp_demo_v1` (channels open from start, requests 40/150/240) | RL | ~~Freeze before paid TP batch~~ fielded | INV-0202, 0203, 0902, 0903 |
| D02 Pressure ratings | **resolved 2026-10-07** — high arm 6/7, low arm 2/3 (demo S2 values; version-indicated via `checkin.pressure_demo@v2`); mapping policy `independent_fair_coin` (higher value to JT or PM by independent fair coin per session, `rng_stream: ratings`) satisfying §7.1 invariant 7. Rationale: demo values are the protocol-consistent S2 baseline; independent coin mapping is already implemented and balance-verified. Applicable material: `tp_review_first_v10` `manipulation` + `condition_set` blocks, `checkin.pressure_demo@v2`. Reviewer: RL. Revalidation: manipulation-check item on pilot completers | Exact high/low displayed values and JT/PM mapping policy (must satisfy §7.1 invariant 7) | Demo high 6/7 vs low 2/3 (S2); historical pilot high 5/6 (S4) — keep a version indicator either way; mapping: higher value to JT or PM by fair coin (S2) or a balanced/independent policy | RL | ~~Before final materials and allocation~~ fielded | INV-0203, 0510, 0902 |
| D03 PE materials | **partially addressed by the 2026-10-01 amendment** — INV-0910 contamination audit complete (`docs/PE_CONTROL_CONTAMINATION_AUDIT.md`) | The approved amendment (2) reaffirms "participants are not evaluated, receive no feedback about themselves" and approves generated teammate/assistant contributions with classifier screening — consistent with ED-19 (nothing rates the participant). **Residual to confirm before paid PE:** whether the approved disclosure wording covers the eval-arm expectation premise (S3) and the post-task premise reveal to the control arm (`pe_post_05`–`pe_post_09` suspicion check) | no evaluation-contingent bonus | RL + IRB | Before paid PE recruitment | INV-0904, 0910, 1601 |
| D04 Compensation | **resolved 2026-10-07** — $4.00 base, no correctness bonus, `ratings_contingent_pay: false`. Payable outcomes: `completed` and `technical_stop` (a platform/technical stop is not the participant "leaving early"); `abandoned`, `withdrawn`, `declined` forfeit the base — the consent-verbatim rule in `consent.reference@v4` ("compensation requires reaching the end and clicking Submit; leaving early is unpaid"). Rationale: prior artifacts declared technical stops/withdrawals payable, contradicting the bound consent text; the BA-97 remediation aligned `_entitlement` to the consent rule rather than re-litigating consent wording, and this also makes the consent's "skipping questions does not affect payment" clause literally true for check-in timeouts. Applicable material: `payment.s5_base_only@v3` (activatable), `consent.reference@v4`, `withdrawal_payment: forfeits_base` in `tp_review_first_v10`; `_entitlement` branches on outcome × policy. Live-DB consequence: the three pre-remediation smoke entitlements (abandoned, pending $4.00) were disposed `ineligible`/0¢ with recorded reasons. Reviewer: RL. Revalidation: first real completer exercises the pending→payable path; payment export reconciliation per WP13. **Update 2026-10-10:** `payment.s5_base_only@v5` (350¢ flat) supersedes v4 for the `study.live.v8` fielding line — an offered $0.50 accuracy bonus is paid to every completer (see the incentive-compatibility succession paragraph above) | Base pay, any correctness bonus (integer cents), technical-stop and withdrawal handling | S5: $4 online base, no bonus selected; demos advertise $0.25/correct crate (max $1.00) — do not activate without approval; never ratings-contingent | RL + IRB | ~~Before payable batch~~ fielded | INV-0907, 1304, 1306 |
| D05 Local inference | **resolved 2026-10-01 — IRB amendment approved** (protocol XXXX-XXXX, amendment item 1). Prior partial: authorization settled 2026-09-28 (owner: S5's "Gemma 3" typo; OIT review covered Gemma 4, server model-agnostic) | Approved language: Gemma 4 (or latest released Gemma), self-hosted on backup-host via Ollama, weights SHA-256-verified once then frozen, per-message model/version logging, no third-party transmission **except the university AI service fallback when the GPU is overwhelmed** — same frozen prompts/classifier/audit on either path. Deployed: `gemma4:26b` (switched from `gemma4:12b` 2026-10-03 — GPU dedicated to this study while other Ollama consumers are paused), digest pinned per call. **Scope note:** the approved fallback is university AI service specifically; the deployed `openai`/`minimax` adapters are *not* university AI service endpoints and — per BA-35 — are refused for all session traffic (constructible for offline fixture comparison only; preview/QA sessions can carry human tester text). A live congestion fallback needs a university AI service adapter (not yet built) | RL + EL + OP | — (unblocks WP10b real-provider work and model-assisted coding) | INV-1001, 1009, 1205, 1403 |
| D06 Instrument/skip policy | **resolved 2026-10-07** — literal participant copy frozen as the approved artifact set bound by `tp_review_first_v10`: `consent.reference@v4`, `instruments/post_task.tp_demo@v3`, `instruments/confidence.demo@v2`, `instruments/debrief.tp_reference@v2`, `instruments/training.reference@v2`, `checkin.pressure_demo@v2`, `voices.tp_demo@v2`, `requests.demo@v3`, `prompt.social_demo@v3`, `prompt.assistant_demo@v2`. Skip policy: the check-in own-pressure rating is **skippable** (`own_rating_skippable: true`) — required so the consent's "skipping questions does not affect payment" clause is literally true (BA-97). PE peer-rating details remain governed by D03 before any PE fielding. Reviewer: RL. Revalidation: none for the TP pilot; PE instrument freeze is a D03 follow-on | Items/order/anchors, optionality, confidence handling, peer ratings in both arms, comprehension checks, score feedback order, distinct recall/perception items; **literal frozen participant copy** — the WP09 screens currently render interim demo-equivalent copy plus the bound artifact key as provenance (`material` projection serves the `material_reference` bodies, which point at where the reference copy lives); D06 freezes the literal wording and triggers new artifact versions + republication | Reference items in `tests/fixtures/reference/demo_constants.json` (ITEMS); peer ratings in both arms (S3); skips allowed without payment loss (S5) — demo forces peer ratings (DEF-11) | RL | ~~Before browser release acceptance~~ fielded | INV-0905, 0906, 0909 |
| D07 Teammate policy | **resolved 2026-10-07** — generated (not scripted) teammate and assistant contributions, per the 2026-10-01 amendment which approved generation with classifier screening: `social_generation: model_with_validated_fallback`, pinned `prompt.social_demo@v3` + `prompt.assistant_demo@v2`, provider `local_openai_chat` on digest-pinned `gemma4:26b`, `grounded_prompts_v5`, validator `teammate_output_v7`, per-call `model_call` audit, deterministic fallback as degraded path only; contents questions stay deterministic (BA-92). No assistant referral; TP request-sender independence retained (coin-drawn majority seat); PE sender policy is out of scope (no PE fielding). Rationale: the amendment approves the generated condition — deterministic scripting contradicts it; WP10a already built the full generation path. Prior interim default (v9's `deterministic_only`) superseded by BA-96. Reviewer: RL. Revalidation: `test_audit_ba96_102.py` records digest-pinned model calls on the fielded manifest; pilot debrief reviews contribution believability | Neutral pilot language, social-generation mode, acknowledgments, initiative, between-study differences, assistant referral (proposed: none), per-source presence cues; **open sub-question (review 2026-09-29):** request-sender independence is proposed for `tp_review_first_v2` (coin-drawn majority seat) but `pe_pilot_v2` keeps the fixed JT/PM/JT multiset — extend to PE (sender identity could interact with post-task peer ratings) or record it as TP-only with rationale | Reference voices/ack/crosstalk in PE fixture; TP busy/distracted prompt language (S2) | RL | ~~Before paired-arm fidelity acceptance~~ fielded (TP) | INV-0204, 0701–0708, 1008 |
| D08 Recruitment/eligibility | unresolved | Platform adapters, cross-study repeat rule, identifiers, batch linkage; **platform composition caveat (2026-10-16):** v7–v11 fielded on Connect, v12+ on Prolific — the pools may differ latently (v13 open_ended free-text volume ~2× Connect-era rounds); cross-platform contrasts are descriptive only, not manipulation effects; **BA-01 adjudication (2026-09-29):** the WP04 build let a matching (platform, panel ID, batch) alone supersede a live session credential — spec §5.1 forbids PID-only recovery ("knowing a panel ID must not recover a session credential; credential loss requires a controlled recovery process"). Engineering default corrected to: valid live cookie resumes; PID-only re-entry is refused and audit-logged. RL review: whether a second-factor/return-link recovery should be added (INV-1305 researcher-mediated path is the current recovery surface) or whether a spec amendment is wanted | Prolific / Connect / lab entry (S5); no S1 targets or code strings | RL | Before live enrollment (external setup starts M4) | INV-0401, 0404, 0407, 1303, 1305 |
| D09 Withdrawal/retention | unresolved | Data-use choice and **final data-use wording** — **surface changed 2026-10-01** (UX feedback item 11): the participant-facing data-use screen no longer offers deletion options; it renders contact information instead and records the `use_all` default to preserve the `debrief → data_use_choice → completion` invariant. The `data_use_choice` table and export path are unchanged, but whether a post-deletion withdrawal route still needs to exist (and where — contact email vs screen) is part of this decision. Plus identity deletion (S5: platform IDs within 48 h of close after payment), research retention, backup tombstones, whether initial-pilot text may form the WP12b held-out corpus | Per S5; do not copy S1's 30-day backup retention | RL + IRB | Before fielding | INV-1305–1309, 1405, 1208 |
| D10 Timing policy | **resolved 2026-10-07** — reference mechanisms (`REFERENCE_CROSSWALK.md` §4) frozen as bound in `tp_review_first_v10`; warning `warning.one_minute_left@v2` at active second 300 — the approved republish of the frozen v1 body (identical wording/trigger/style; v1's bytes are immutable inside published bundles and the live gate requires `approved` — spec §4.1 annotation). Check-in inactivity outcome and fallback budgets unchanged from the bound timing block. Rationale: the N=5 pilot fielded under D13's pilot-as-usability election satisfies the "needs D13 first" ordering. Reviewer: RL + EL. Revalidation: pilot timing distribution review before expansion | Distributions, source/class policies and `timing.mechanism` per profile, warnings, caps, timeout/fallback budget, check-in inactivity outcome | Reference mechanisms in `REFERENCE_CROSSWALK.md` §4; warning `warning.one_minute_left` v1 at active 300 | RL + EL | ~~Freeze before real timing/load acceptance; needs D13 first~~ frozen for the N=5 fielding | INV-0205, 0209–0213, 0609 |
| D11 Semantic validation | unresolved | Rubric, development and held-out corpora, minimum held-out size per construct and arm, coverage and thresholds | none selected | RL | Rubric before tuning; thresholds before semantic analytic use | INV-1201–1210 |
| D12 Analysis/pilot size | unresolved | Initial pilot size, primary outcomes/estimands, exclusions, missingness, expansion criteria; power separately for confirmatory use. **2026-10-15 applied precedent (owner/PI-agreed, per-case — D12 itself still open):** completers who never entered any crate answer (`answer_final`=0, empty `answers_frozen` at deadline, engaged telemetry showing a comprehension/UX failure not low effort) were voided via `deletion_tombstone` — v13 ×4 (02a105c2, 500ec09f, 75ab18de, 8a79ef33; slots refilled to N=16) and v12 ×1 (fa783b04; cohort N=15). `accuracy.entered_count` (cb-1.10) is now the detection measure + post-launch checklist item. Codify as a registered exclusion rule when D12 resolves | S4 aggregates are descriptive only (N=5 per arm) | RL | Before interpreting/expanding the pilot | INV-1101, 1604–1608 |
| D13 Pre-launch usability | **resolved 2026-10-07 by election of the pilot-as-usability option** — the initial pilot (`prolific.live.n5`, N=5) explicitly serves the pre-launch usability role on the fielded manifest (timing plausibility, world difficulty, discoverability), preceded by the E14 lab walkthrough and three live smoke sessions; it is non-confirmatory (D12 governs interpretation/expansion). IRB office-hours consultation confirmed no amendment was required for this fielding. Reviewer: RL. Revalidation: INV-1609 re-verifies this record pre-expansion | Non-analytic staff/lab usability round, or record that the initial pilot serves that role | spec §13 | RL + IRB | ~~Before the D10 freeze~~ satisfied by the fielded pilot; INV-1609 re-verifies pre-expansion | INV-0911, INV-1609 |
| D14 Technical-incident policy | unresolved | Clock treatment during outage, flagged-interval schema, participant handling, payment and analytic flagging. **2026-10-15 applied precedent (owner/PI-agreed — D14 itself still open):** an operator-caused api/ui/worker outage (22:31:23–22:41 EDT) disconnected 4 in-flight v13 sessions mid-protocol; participants were compensated $3.00 each via Prolific manual completion, parked sessions swept `abandoned` with 0¢ `ineligible` entitlements, freed slots refilled. Clocks were never extended or altered; incident documented in `docs/evidence/2026-10-15-postlaunch-v13-tranche1/` | Never extend selected participants' clocks silently (spec §13) | RL + EL | Before real timing acceptance | INV-0612, 1602 |

## B. External lead-time track (plan §2.3)

| Item | Status 2026-09-28 | Artifact | Next action |
|---|---|---|---|
| D03 PE evaluation-expectation approval | **partially covered by the 2026-10-01 amendment** (interaction-model + "not evaluated" language approved); eval-premise reveal residual remains | `requests/D03_pe_evaluation_expectation.md` | RL confirms whether the residual premise question needs a further clarification request |
| D04 Compensation/bonus approval | **resolved 2026-10-07 without sending** — office-hours consultation confirmed the blanket protocol covers the compensation rule; the selected rule is recorded in §A D04 (`payment.s5_base_only@v3`) | `requests/D04_compensation.md` | none — draft retained for the record |
| D05 Local model and GPU capacity | **resolved by IRB amendment approval 2026-10-01** (protocol XXXX-XXXX) — request draft superseded | `requests/D05_local_model_capacity.md` | File the amendment approval letter with the protocol records; pin its hash in the plan header when received as a document |
| Ingress apply and verify | apply done 2026-09-28 (owner); live block == snippet re-verified by agent 2026-09-28 | `BOUNDARY_INVENTORY.md` | Service-dependent checks remain (INV-1401, 1509) |
| Admin password in `.env` | done (owner); `ADMIN_PASSWORD` non-empty, posture 640 `docker-users` + `u:admin1:r` (names-only check) | `BOUNDARY_INVENTORY.md` | none |
| Host PostgreSQL database and roles | **done 2026-09-28 (operator)** — `scripts/db/inv0301_bootstrap.sql`: `investigators_db` (owner `investigators_migrator`), roles `investigators_user`/`_research`/`_compensation`, schemas `app`/`identity`, default privileges; runtime login from `.env` verified by agent (names-only). pg_hba rule 39 admits Docker bridges. Host test DB not created by choice | `scripts/db/inv0301_bootstrap.sql` | WP03: migrator DSN setting, Alembic as migrator, per-table compensation grants, INV-0308 role tests |
| Admin allowlist | confirmed 2026-09-28 (`admin1@university.edu`, `admin2@university.edu`) | `.env` (`ADMIN_EMAILS`) | none |
| Backup registration / WAL (RPO ≤15 min) | not due (start M3) | — | INV-1405 |
| Platform setup and exclusion lists (D08) | not due (start M4) | — | INV-0404 |
| Usability round approval (D13) | **resolved 2026-10-07** — the initial N=5 pilot serves the usability role (§A D13); office-hours consultation confirmed no separate approval needed | — | INV-1609 re-verifies pre-expansion |

The drafts cannot be filed by engineering: they need the research lead's review and institutional channels. D05's was overtaken by the approved amendment (2026-10-01); D04's was overtaken by the office-hours consultation and the recorded §A resolution (2026-10-07); INV-0004 stays `in_progress` until D03's residual is adjudicated and the remaining unresolved rows (D08, D09, D11, D12, D14) reach their gates.

## C. Engineering decisions (WP00–WP01)

| ID | Decision | Rationale | Revisit |
|---|---|---|---|
| ED-01 | Backend on Python 3.12 with `uv` (lock: `backend/uv.lock`); host Python 3.9 is used only by stdlib-only scripts. | Host Python 3.9 is too old for current FastAPI/SQLAlchemy typing; uv gives a hash-locked, reproducible environment on host and in the image. | Toolchain refresh |
| ED-02 | PostgreSQL driver psycopg 3; plain `postgresql://` DSNs are mapped to `postgresql+psycopg://`. | Keeps `.env` DSNs in the estate format. | — |
| ED-03 | `study_admin_common` is **not** mounted in WP01. | Nothing in WP01 needs it; spec §3.2 permits reuse only read-only with a recorded version and contract tests. Aggregate SHA-256 of its `*.py` on 2026-09-28: `b9c859ce…aae8`. | INV-1301 (admin auth) decides reuse |
| ED-04 | Build contexts are `./backend` and `./frontend` (declared source roots), not `..` as in `dct`. | Keeps `.env`, protocol documents and other apps out of every build context (INV-0101, spec §3.3 one source tree per service). | — |
| ED-05 | Compose hardening: read-only root FS, private `/tmp` tmpfs, `cap_drop: ALL`, `no-new-privileges`, non-root users (backend uid 10519, nginx-unprivileged), UI receives no environment. | Least privilege; the UI serves static files only. | — |
| ED-06 | `/health` = liveness (container healthcheck), `/ready` = DB + schema head, `/version` = build stamps; `/metrics` answers only non-proxied callers (404 when `X-Forwarded-*` present); OpenAPI/docs disabled. | Avoid restart loops during DB outages; keep operational data off the public path. | INV-1403/1404 |
| ED-07 | Disposable-database harness = an ephemeral `postgres:15-alpine` container per test run (tmpfs, random password and port) behind the destructive-test guard, which still compares against the real `.env` DSN. | Satisfies INV-0104 without host-cluster admin rights; the host-cluster test database and roles are created with INV-0301. | INV-0301 |
| ED-08 | Build/release identity = SHA-256 over `backend/`, `frontend/`, `content/`, `docker-compose.yml` (`scripts/source_bundle.py`), injected into images and reported by `/version` and `/investigators/version.json`. | No Git tree (plan §2.2). | — |
| ED-09 | The Compose stack is **not** started on 8518/8519 in WP01. Image boot is proven on an isolated network with random loopback ports (`scripts/smoke_stack.sh`). | Starting it would make a placeholder public through the live proxy and point at a database that INV-0301 has not created. First `docker compose up` waits for the owner after INV-0301. | Owner go-ahead |
| ED-10 | Requirement trace supplements plan §9.1 for spec §14 (→ INV-1501–1511), shown in section F of the trace. | §9.1 maps §14 only to plan sections, leaving two normative clauses without an owning task. Proposed plan amendment. | Next plan revision |
| ED-11 | Dependency versions resolved 2026-09-28 and locked; base images pinned by digest (`TOOLCHAIN.md`). | INV-0102 determinism. | Toolchain refresh |

## D. Engineering decisions (WP02)

| ID | Decision | Rationale | Revisit |
|---|---|---|---|
| ED-12 | Manifests and artifacts are strict JSON (`investigators.manifest/1`, `investigators.artifact/1`) validated by Pydantic models with `extra=forbid`, strict types, and single-value `Literal`s for dormant treatments; JSON Schemas in `content/schemas/` are generated from the models and checked for drift. | JSON needs no runtime YAML dependency; one source of truth for the contract; unsupported switches fail at bind (INV-0206). | Schema v2 |
| ED-13 | Hashes are SHA-256 over canonical JSON (sorted keys, no whitespace) of the *materialized* model; enrollments pin `manifest_sha256` + `bundle_sha256` (manifest plus every referenced artifact hash). `content/published/manifests.json` is add-only; any edit to a published manifest or artifact fails bind until new versions are published. | Spec §4/§4.2: hash actual executable content; editing creates a new immutable version. | — |
| ED-14 | Content is baked into the backend image through a named Compose build context (`additional_contexts: content: ./content`, `COPY --from=content`), keeping the build context at `./backend` (ED-04). Runtime lookup: `$INVESTIGATORS_CONTENT_DIR`, else `/srv/investigators/content`, else the source tree. | Content is part of the source-bundle checksum and must match the image that executes it; no volume mounts (compose contract). | — |
| ED-15 | Randomness is keyed, not sequential: each draw = HMAC-SHA256(stream key derived from the session seed, canonical draw key). Streams: assignment, world, order, ratings, schedules, jobs. Draws are logged (`DrawLog`) for persistence with the session (WP03). | Participant behavior and arm cannot shift other draws (INV-0207); portable across Python versions. | — |
| ED-16 | Reference profiles keep the demo distributions and per-class mechanisms but run on the `active_task` clock; demo wall-clock defects (DEF-04, DEF-09) are not reproduced. Crosstalk (+28 s) and acknowledgments use the `concurrent` mechanism with no generation step in both reference and v2 profiles. The demo assistant floor is expressed in v2 as a concurrent delay component. | Spec §6.1/§6.4 forbid delivery behind the pause or after closure; §6.5 names exactly three demo mechanisms. Differences are declared in `content/profiles/diffs/` and checked. | D10 |
| ED-17 | Proposed v2 content choices (all `proposed_unapproved`, preview/QA only): review-first request seats use a coin-drawn majority seat; v2 social replies are deterministic neutral fallbacks (`fallbacks.neutral` v1, no model prompt); payment `payment.s5_base_only` ($4 base, no bonus, not activatable); reference exposure/assistant copy reused until D03/WP10a. **Superseded 2026-10-07:** the fielded `tp_review_first_v10` promotes these choices — social generation is `model_with_validated_fallback` (D07), `payment.s5_base_only@v3` is approved + activatable (D04), and all live-bound artifacts are `approved` successors; `fallbacks.neutral` remains only as the degraded-path voice bank. | Invariant 7 balance; spec §7.3 neutral scripts; D04 unresolved. Research review under D01/D04/D07/D10. | resolved — see §A |
| ED-19 | The platform never generates, simulates or stores ratings of the participant by teammates (no job, table, content artifact or export field for them). Participants rate JT/PM; nothing rates the participant. | Keeps the PE debrief statement "No ratings of you were collected by anyone" true (D03 request relies on it); a review check for WP03 schema and WP07 teammate jobs. | Only via D03 + new spec version |
| ED-18 | Preview descriptors are HMAC-SHA256 tokens pinning manifest SHA, arm, seed, mode and timing mode; the signing key is a caller parameter until INV-1302 adds the setting (no `.env.example` change in WP02, which is a pinned input). | Keeps pinned inputs unchanged; contract testable now. | INV-1302 |
