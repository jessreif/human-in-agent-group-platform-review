# Source baseline (INV-0002)

**Captured:** 2026-09-28T20:56Z on backup-host (`sha256sum`, read-only).
**Design version targeted by the first implementation release:** `INVESTIGATORS_TEAM_EXPERIMENT_DESIGN_SPEC.md` revision 2026-09-28 (incl. owner decisions; 2026-10-01 amendment + revised-demo reconciliation edits; 2026-10-02 BA-35/BA-41 boundary + prompt-version + BA-38/BA-40 correction edits), SHA-256 `ca309f0168d7d2f24ad80d191b3211ca9690b6c5c7b6c961c740b080a60b63f2`.

`SOURCE_CHECKSUMS.sha256` is the machine-checkable pin list (`sha256sum -c docs/baseline/SOURCE_CHECKSUMS.sha256`, run by `scripts/check.sh`). Any mismatch means an input changed and triggers plan §8 change control (see `../RELEASE_EVIDENCE.md`, "Reopening").

| Artifact | Role | SHA-256 | Matches pin in |
|---|---|---|---|
| Design specification | authoritative design | `b158a258…69e3` (re-pinned 2026-10-15 — demo-file-stem rename annotation only: S2 source row now names the live template `investigators_demo.html` and notes the archived copy keeps its frozen name; previously 50cc173a…2868, fc41291c…a2b0, 69a7de4e…c1a1, ffc31940…9530, 70c33571…05bc, 2c91c81d…201b, 561c8741…06d5, bca9a2ebcba2…8160, 978a7244…f94e, 3be837c4…d849, f8b4ef0f…f2bf, 2ad3edc43b16c05308a44b25425191e575ed12b851c24ca021e6d429a2c856b8, 2285b431…118f, ae01b026…b4b4, 3ac73f1d…4bd6, f780ea49…52bf, ca309f01…63f2, 861a8db4…c8bd, 72e66537…6b8c, 18cfb9b8…177e, f7e2c2e8…20df, 3dce3bb3…6eb8) | plan header |
| Hidden-bench insights | supporting evidence | `4bb1e514…6240` | plan header |
| Timing insights | supporting evidence | `0aaefc64…f43b` (re-pinned 2026-10-06 — I2 row repointed to the archived S2 demo; previously 9233e236472aaa4b5b6f132a681320259225962d31cd705fb703a7b17d77cbde) | plan header |
| Implementation plan | living task register | `e01a8160…df88` at WP00 start | **not pinned**: its checkboxes change as tasks complete; the requirement-trace generator tracks the plan sections it reads (§6, §9) |
| Plan audit | audit record | `6157039d…e3` (re-pinned 2026-10-07 — BA-96–BA-102 remediation record appended (§8); previously a0f430cd…8dd5, 488fdf7f…4752, a5cd800d…50e5, 84b02e0c…5e10, 7faf03a7…aad6, c5681678…a1c4, e1aa446c…70fa, d3ff007d…835e, 21082209…8466, 3b372993…1af5, 9164bcdc…43ca, 65a14b9c…aeb4, ecf38e81…01a6, f510e3f6…cb35, c62e59a3…67e2, 52357eea…a311, dd75dba9…797f, 8e444b96…db34, 23965733…0fb6, 6a5c85ae…3ef9, 8b719735…92c) | captured here |
| `docs/baseline/reference/investigators_time_pressure_demo.html` (S2) | behavioral reference | `7ced90c5…9825` (moved 2026-10-06 from repo root; keeps its frozen historical name — the live playable-mock template was renamed `investigators_demo.html` on 2026-10-15; all line anchors, extracted constants, and artifact `reference_copy` citations resolve to this archived copy) | spec Appendix A |
| `docs/baseline/reference/investigators_peer_eval_demo.html` (S3) | behavioral reference | `90b9587a…b48e` (re-pinned 2026-10-01 — revised reference delivered with UX_Feedback.pdf; previously 7b0069cb…99f8) | spec Appendix A |
| `260920 Investigators Pilot Results.docx` (S4) | pilot report | `cedc3e31…fad4` | spec Appendix A |
| `XXXX-XXXX IRB Approved Protocol.docx` (S5) | approved protocol | `680f2491…a4e6` | spec Appendix A |
| `XXXX-XXXX IRB Approved Protocol.pdf` (S5) | approved protocol (PDF) | `a9f89f03…8955` | plan header, spec Appendix A |
| `apache/investigators.vhost.snippet` | ingress source of truth | `acfe4183…3107` (re-pinned 2026-10-11 — admin allowlist enforcement note + applied layout; live Apache block verified byte-identical 2026-10-11; previously 174bba84…1177) | plan header |
| `.env.example` | settings template | `0fa69601…9faa` (re-pinned 2026-10-16 — `BONUS_PAYMENT_ENABLED` added, default 1; previously `21baf561…e67e4` — `AI_QA_ENABLED` added, default 0; previously f9b51663…94c7 — `TIME_PRESSURE_ENABLED` added, default 1; previously 16c0f7ab…0d0a — `CRATE_OVERLAP_ENABLED`/`CRATE_NUMBER_ENABLED` added, defaults 0; previously c0509f68…780f — `AI_ASSISTANT_ENABLED` added 2026-10-14; previously 6bb37666…1d5d, 8b704184…19c6, 9fb3ac42…cfd8, a912095d…, 4d0735d1…c533, 29bf1d32…c668, cb0c69c2…93c8, c2f63db5…b252, 07413c47…ae33, 18a08b35…e59f, 552336c3…9e17, 35cbc4ac…d2fe) | plan header |
| `/opt/research-study_ssl/TEAM_AI-R_AI-M_STUDY_PLAN.md` (S1) | related build plan | `757d7c70…620b` (re-pinned 2026-10-14 — drift detected during the AI-assistant-flag gate run; file actively edited by its owner, undiffable, informational citation only; previously f129e348…c604, 4cae8577…1b39; file is 44981 bytes) | `SOURCE_CHECKSUMS.sha256` |

All 13 values matched their existing pins on 2026-09-28; the 12 inputs other than the plan are pinned in `SOURCE_CHECKSUMS.sha256`. DOCX/PDF equivalence of the protocol remains unverified (spec Appendix A); nothing here establishes which files were deployed in the historical Qualtrics pilots.

The live Apache block between `BEGIN/END INVESTIGATORS MANAGED ROUTES` in `/etc/httpd/conf.d/investigators.conf` equals the snippet (whitespace-trimmed diff, same capture); see `../BOUNDARY_INVENTORY.md`.

## Re-pin 2026-09-28 (RELEASE_EVIDENCE §4)

- **Design specification:** Gemma wording corrected per the owner. S5's “Gemma 3” is a typo; the [University] OIT security review covered Gemma 4 and approved backup-host for local LLM use as a model-agnostic, single-operator server. D05 now covers only the exact model/digest and GPU capacity. No behavioral requirement changed.
- **Plan audit:** a D05 note was corrected, and a recorded plan-hash field was edited by the agent. The field's exact pre-edit value could not be recovered; the audit is a record, not a requirement source.
- **Reopened tasks:** none. No completed WP00/WP01 artifact depends on these wording changes; INV-0002 is satisfied by this re-pin, and INV-0005's inventory text was corrected in place.
- **Requirement trace:** regenerated.

## Re-pin 2026-09-29 (WP03)

- **`.env.example`:** added a comment pointing to the owner-created `.env.migrate` migration DSN (INV-0301). No setting names or values changed; no task reopened.

## Re-pin 2026-09-29 (WP10a)

- **`.env.example`:** added `LOCAL_MODEL_BASE_URL`/`LOCAL_MODEL_ID`/`LOCAL_MODEL_DIGEST`/`LOCAL_MODEL_MAX_CONCURRENCY` for the loopback-only local model adapter (INV-1001). Empty values = deterministic-fallback worker, same as a model outage. No secrets; no task reopened.

## Re-pin 2026-09-30 (audit §0.5)

- **Plan audit:** appended the §0.5 remediation record for BA-22–BA-31 (writer-lease authorization, entry/concurrency, `in_answer` correctness, multi-process verification, durable-completion browser evidence), plus a terminology-key pointer to `docs/GLOSSARY.md` in the header and the BA-32 lab-found remediation row (dead-cookie entry trap) and BA-33 row (cross-session revision wedge + telemetry stamp). The audit is a record, not a requirement source; no completed artifact depends on the change. Reopened tasks: none.

## Re-pin 2026-10-01 (UX-feedback revision + provider flag)

- **PE demo (`investigators_peer_eval_demo.html`):** the student delivered a revised
  behavioral reference alongside `UX_Feedback.pdf` — two-row workbench layout, live-AI
  hooks (`TEAMMATE_LIVE`/`AI_LIVE`), generative thread replies with a canned fallback
  (scripted `VOICE`/`ACK_P`/crosstalk layers removed), 480 s demo task budget (the
  production task stays at 360 s), payment-code consent, and no mid-task check-in or
  pause guard. Re-pinned; `demo_constants.json` regenerated; `behavior_reference.json`
  PE anchors re-mapped (removed mechanisms recorded as `absent` anchors).
- **Design specification:** only the Appendix A checksum row for the PE demo changed
  (annotated re-pin). No requirement text changed; no task reopened.
- **`.env.example`:** `MODEL_PROVIDER` flag plus `OPENAI_*`/`MINIMAX_*` remote-provider
  settings and their preview/QA-only comments. No secrets; no task reopened.
- **`tests/contracts/test_compose_contract.py`:** extended to encode the
  `investigators-ollama` sidecar boundary (INV-1001): service name set, the worker's
  sole permitted `network_mode`, the sanctioned model-store mount, digest-pinned image.

## Re-pin 2026-10-01 (plan audit §0.6)

- **Plan audit:** appended the dated follow-up on the live-model, cleanup and
  E16 UX rounds (BA-35–BA-42), recorded BA-34's narrower savepoint fix, and
  corrected the new plan citations against the current plan. The audit is a
  record, not a requirement source. The source bundle, plan and spec were not
  changed; no implementation status or checkbox was changed by this
  documentation re-pin. Recommendations to reopen affected tasks remain open,
  as do the release gates.

## Re-pin 2026-10-02 (audit §0.7 + BA-35–BA-42 remediation)

- **Plan audit (23965733…0fb6):** appended the §0.7 BA-35–BA-42 remediation
  record. A record, not a requirement source; no task reopened.
- **Design specification (861a8db4…c8bd):** BA-35 remote-adapters-refused
  boundary wording in §7.4/§13 and BA-41 prompt-version notes.
- **`.env.example` (cb0c69c2…93c8):** BA-35 comment — remote adapters are
  offline fixture-comparison only, refused for all session traffic.
- **`/opt/research-study_ssl/TEAM_AI-R_AI-M_STUDY_PLAN.md` (77ee2a6a…81e0 — superseded, not pinned):**
  owner-edited external input detected by pin mismatch at E17 gate start. No
  Git history exists in that repo, so the change cannot be diffed — flagged for
  owner review; flagged as unverifiable in E17/E18.

## Re-pin 2026-10-02 (BA-37/38/40/42 corrections)

- **Design specification (ca309f01…63f2):** §2.1/§10 reorder wording now records
  the click-to-grab/drop and keyboard paths (BA-38 correction); §7.4 records the
  mandatory `LOCAL_MODEL_DIGEST` fail-closed requirement (BA-40 correction). No
  new requirements — the spec catches up to implemented behavior.
- **Plan audit (dd75dba9…797f, via 8e444b96…db34):** §0.7 rows corrected to
  record the four first-pass overstatements (v2 consent transcription, missing
  click reorder, conditional digest verification, unstamped deployment) and the
  E18 evidence reference.
- **`.env.example` (29bf1d32…c668, via c2f63db5…b252):** `LOCAL_MODEL_DIGEST`
  changed from optional to required when `LOCAL_MODEL_BASE_URL` is set; the
  template ships an all-zeros placeholder that fails closed.
- **Reopened tasks:** none. These are corrections to already-open findings'
  remediation, not requirement changes.
- **Requirement trace:** regenerated (0 unmapped).

## Re-pin 2026-10-02 (UX feedback round 2 + admin legacy filter)

- **Design specification (f780ea49…52bf):** 2026-10-02 UX-round-2 annotations —
  no sort selector or crate filter; "Shared in team chat" state with server
  share dedupe; greeting options windowed to the first 30 active seconds;
  paged walkthrough (background/initials + orientation + try-it pages);
  auto-recorded `item_skip` on page submit replaces per-item skip controls;
  initials are `participant_display`-stored, export-excluded and
  tombstone-purged. Behavior-catching-up edits; no new requirements.
- **`/opt/research-study_ssl/TEAM_AI-R_AI-M_STUDY_PLAN.md` (d8c80d36…e721):**
  owner-edited external input detected again by pin mismatch (post-E19 gate).
  Still no Git history in that repo — undiffable, flagged for owner review.
- **Reopened tasks:** none.
- **Requirement trace:** regenerated (0 unmapped).
## Re-pin 2026-10-02 (BA-43–BA-49 remediation record)

- **Plan audit (52357eea…a311, via dd75dba9…797f):** §0.8 remediation record for
  the 2026-10-02 code-quality addendum (BA-43–BA-49) — each row records the
  implemented mechanism and its regression evidence. Prior history preserved.
- **Reopened tasks:** none. Defect remediations to meet existing spec
  invariants; no requirement changes.
- **Requirement trace:** regenerated (0 unmapped).

## Re-pin 2026-10-03 (audit §0.8 verification follow-up)

- **Plan audit (c62e59a3…67e2, via 52357eea…a311):** §0.8 gained the
  verification-follow-up paragraph and second-pass rows — independent
  verification of E21 found first-pass gaps in BA-44–BA-49 (unmounted-screen
  handoff error, due-during-generation claim delay, event-loop authentication,
  cross-kind projection rollback, unbounded trickle read, remaining malformed-
  input 500s). The audit is a record, not a requirement source. Prior history
  preserved.
- **Plan audit (f510e3f6…cb35, via c62e59a3…67e2):** §0.8 scope-note evidence
  pointer corrected to the second-pass gate log (`check-20261003T025633Z.log`)
  and source stamp (`baa0f408…57c03`) after the generation-thread lifecycle
  fix landed and the gate re-passed. Record-only change.
- **Reopened tasks:** none. Defect remediations to meet existing spec
  invariants; no requirement changes.
- **Requirement trace:** regenerated (0 unmapped).

## Re-pin 2026-10-03 (O-1..O-4 agent decisions, plan §17.7)

- **Design specification (3ac73f1d…4bd6, via f780ea49…52bf):** §2.1 now records
  that team-provided records enter the AI's accessible file automatically on
  delivery (`added_from_team`, "Shared by [seat]" tag) — the manual add step
  was removed (O-4); the practice bullet updated likewise. Behavior-catching-up
  edits accompanying the resolved agent decisions O-1..O-4 (typed asks → formal
  cards in `nl_only`; both-seats routing; automation-probe disclosure;
  auto-file). No new requirements.
- **Reopened tasks:** none. The spec catches up to implemented behavior.
- **Requirement trace:** regenerated (0 unmapped).

## Re-pin 2026-10-03 (§17 / E23 independent audit)

- **Plan audit (ecf38e81…01a6, via f510e3f6…cb35):** appended the
  current-state audit of §17.1–17.7 and the E23 source bundle, preserving
  the §0.8 second-pass BA-43–BA-49 dispositions and older dated snapshots.
  BA-50–BA-61 record reproducible automation-probe/human-claim gaps,
  typed-ask and redirect edges, participant-copy/mock/appendix mismatches,
  repeat and fallback-classifier limits, and narrower evidence claims with
  remediation and acceptance recommendations. The audit is a record, not a
  requirement source; no plan task or release gate was closed by this re-pin.
- **Source impact:** plan/spec, manifests, application bundle and published
  profiles unchanged (`5105640c…0368a`); no requirement-trace regeneration
  needed for an audit-only edit. Release and IRB decisions remain open.

## Re-pin 2026-10-03 (BA-50–BA-61 remediation record)

- **Plan audit (65a14b9c…aeb4, via ecf38e81…01a6):** appended the
  BA-50–BA-61 remediation record to the §17.1–17.7 / E23 addendum — each row
  records the implemented mechanism and its regression evidence on source
  `541acc8972e5a5d8793abfc4c16a97ac858a91e7881c0c5616c1d3aab6c5c523`
  (gate 13/13 PASS, deployed). The audit is a record, not a
  requirement source; no plan task or release gate was closed by this re-pin.
- **Reopened tasks:** none. Defect remediations to meet existing
  behavior/faithfulness invariants; no requirement changes.
- **Requirement trace:** no plan requirement text changed this round —
  regeneration not needed for an audit-only edit.

## Re-pin 2026-10-03 (gemma4:26b deployment switch)

- **Design specification (ae01b026…b4b4, via 3ac73f1d…4bd6):** deployed-model
  mentions updated to `gemma4:26b` with the 2026-10-03 switch date — the owner
  reclaimed the GPU for this study (other Ollama consumers paused; host
  gpu-default sweep stopped), so the larger Gemma 4 variant is resident in the
  sidecar instead of `gemma4:12b`. The amended authorization names Gemma 4
  (or the latest released Gemma) model-agnostically; no requirement text,
  prompt, or validator changed.
- **`.env.example` (4d0735d1…c533, via 29bf1d32…c668):** example
  `LOCAL_MODEL_ID` updated to `gemma4:26b`; the all-zeros digest placeholder
  and the fail-closed comment are unchanged.
- **Living registers updated (not pinned):** plan §2.2/§6/§9/§17 status text,
  `README.md`, `docs/GLOSSARY.md`, `docs/BOUNDARY_INVENTORY.md`,
  `docs/DECISIONS.md` (D05), `docs/IMPLEMENTATION_STATUS.md` (INV-1001) and
  `docs/LAB_SESSION_RUNBOOK.md` §2 (warm-up now automatic via the
  `investigators-ollama-warmup` compose service).
- **Reopened tasks:** none. A deployed-model change inside the already-approved
  Gemma 4 authorization; the digest pin mechanism is unchanged.
- **Requirement trace:** regenerated.

## Re-pin 2026-10-04 UTC (§17.8/§18 independent audit)

- **Plan audit (3b372993…1af5, via 65a14b9c…aeb4):** added a dated
  current-state audit of E24 BA-50–BA-61 remedies and E25's O-11/O-12/O-14/
  O-17/O-18/O-20 implementation. BA-62–BA-75 record reproducible
  routing/disclosure, prompt-context, load/provenance and practice/doc
  mismatches with remediation and acceptance tests. Older audit snapshots
  and second-pass dispositions are preserved; E24/E25 development gates
  remain distinct from G1–G10 release evidence.
- **Source impact:** audit-only change. Plan/spec, manifests, app source and
  published profiles unchanged (`f33c26cb59a0f9aa887ef3af0000e34824a72d234ba5243c3b9936d63bf47332`). No plan task status
  or release gate changed; no requirement-trace regeneration needed.

## Re-pin 2026-10-04 UTC (BA-62–BA-75 remediation record + codebook cb-1.3)

- **Plan audit (21082209…8466, via 3b372993…1af5):** appended the
  BA-62–BA-75 remediation record to the §17.8/§18 / E24–E25 addendum —
  each row records the implemented mechanism and its regression evidence
  across the first pass on
  `099758f9bbeb0ed3753b5d3befd4ffd2d9b2d79f570915b9517fa922fed759e2` (E26)
  and the residual-review pass (E27), deployed on
  `c3cb11baecf077ed9dbbaa185f90b924b514bff04dbc5d2c2acaaf86457c9e47`
  with the cb-1.3 codebook definition correction. The audit is a record,
  not a requirement source; no plan task or release gate was closed by
  this re-pin.
- **App source:** `backend/app/measurement/codebook.py` `automation.probes`
  definition corrected (every recognized probe is logged; `probed` vs
  `seats` marks budget-deferred disclosures; `probe_near_miss` documented)
  and versioned `cb-1.3` per the codebook changelog convention —
  `docs/CODEBOOK.md` regenerated. New source bundle
  `c3cb11baecf077ed9dbbaa185f90b924b514bff04dbc5d2c2acaaf86457c9e47`.
- **Reopened tasks:** none. Defect remediations and measurement-doc
  corrections to meet existing invariants; no requirement changes.
- **Requirement trace:** no plan requirement text changed this round —
  regeneration not needed.

## Re-pin 2026-10-04 (lab-walkthrough usability)

- **Design specification (2285b431…118f, via ae01b026…b4b4):** the
  simultaneous-workbench clause now records that both pane split
  positions are participant-adjustable (accessible drag/keyboard
  separators, 25–75% bounds, session-only state, no handles under the
  900 px breakpoint) — adopted after the 2026-10-04 walkthrough showed
  the fixed 45/55 split cramped the chat and assistant panes. No
  requirement was relaxed; the simultaneous three-pane constraint and
  the no-exclusive-tabs rule stand.
- **App source:** `teammate_output_v7` (`is_near_repeat` + audit fields),
  resizable `.task-grid`; new source bundle `7fef9e06fa56e82c3a89d7babd13f900139661002f46e7c2470b79dcd769f453`.
- **Reopened tasks:** none. Usability/believability fixes inside existing
  requirements; no requirement text deleted.
- **Requirement trace:** regenerated (no unmapped).

## Re-pin 2026-10-04 (independent §20 audit)

- **Plan audit (d3ff007d…835e, via
  `21082209d2c71d73f9c35ad3603c558ef81596ad10b6507bc53023b252168466`):** added a dated
  review of the `7fef9e06fa56e82c3a89d7babd13f900139661002f46e7c2470b79dcd769f453`
  lab-walkthrough source bundle. The near-repeat gate has source and
  focused-test support; workbench geometry and multi-pointer drag remain
  open as BA-76/BA-77. The earlier BA-62–BA-75 E26/E27 closeout remains
  historical and is not reopened.
- **Source impact:** audit and baseline documentation only; plan, spec,
  app source, published content, deployment and participant data unchanged.
  The source-bundle stamp remains
  `7fef9e06fa56e82c3a89d7babd13f900139661002f46e7c2470b79dcd769f453`.
  No plan task or G1–G10 release gate changed.
- **Requirement trace:** no source requirement text changed; regeneration
  not needed. The archived 13/13 gate remains development evidence only.

## Re-pin 2026-10-04 (BA-76/77 remediation record)

- **Plan audit (e1aa446c…70fa, via
  `d3ff007db4e6332f127938fe8f6f37391369872cbae5a3e78ece05fd0de0835e`):** appended the
  BA-76–BA-77 remediation record — gap-corrected track/handle geometry and
  pointer-id-bound drags. The §20 audit's findings text is unchanged.
- **App source:** `.task-grid` track math and `startDrag` pointer binding;
  new source bundle `664e64ac47ecc541d2737cfb5bf5bdd4565bc25009adf125f517bec743d6c3f0`.
- **Reopened tasks:** none.

## Re-pin 2026-10-04 (independent §21 harness audit)

- **Plan audit (c5681678…a1c4 — superseded, via
  `e1aa446ceda7b747c479cd9e06f65a64301cdc7efde626351126a427e0a870fa`):**
  added a dated review of the agent-participant harness on source bundle
  `3bef23aeb4a15ac13e810344e12fd3a1b059e30061d1e4f7d4b9ba69c9b22cf1`.
  BA-78–BA-89 record safety, session-scope, oracle, evidence-provenance and
  generated-document remedies. The earlier BA-76/77 closeout remains a
  historical record.
- **Source impact:** audit and baseline documentation only. Plan, spec, app
  source, harness, profiles, deployment and participant data unchanged;
  API/UI remain pinned to `664e64ac47ecc541d2737cfb5bf5bdd4565bc25009adf125f517bec743d6c3f0`.
  No plan task or G1–G10 release gate changed.

## Re-pin 2026-10-04 (BA-78–BA-89 remediation record)

- **Plan audit (`7faf03a7f600d77f87951b2620bd51132fc656a473f587b27b8ff3d71254aad6`, via
  `c5681678dc2b391827bace105d46deddceee228657f6198b9afa10491e16a1c4`):**
  appended the dated remediation record closing all twelve §21 findings on
  source `7c059d17babd20769a1fa96ff6269ac57c8b73890b2a3e369beaaac19fb47103`,
  with the post-remediation batch and a stamp note for the cosmetic-only
  `oracles.py` reflow that moved the tree to `a10fbe2e…1a1`.
- **Source impact:** audit documentation only — records remediation performed
  on harness/application source described in `INVESTIGATORS_TEAM_EXPERIMENT_PLAN.md`
  §21 and `docs/IMPLEMENTATION_STATUS.md`.
- **Requirement trace:** no requirement text changed, so regeneration was
  not needed. Unit/static checks are not harness safety or release evidence;
  generated click-through and appendix checks still fail on the newer bundle
  (BA-89) and were not silently regenerated by this audit.

## Re-pin 2026-10-04 (independent SQ-6–SQ-11 rollout audit)

- **Plan audit (84b02e0c…5e10 superseded, via
  `7faf03a7f600d77f87951b2620bd51132fc656a473f587b27b8ff3d71254aad6`):**
  appended the dated independent review of the SQ rulings and AB-19–AB-21
  rollout on source bundle
  `d59c8b026242dfb368c69b4d2c1f836cd1a3e723b0a3a5f581de94890e096b07`.
  It narrows and reopens BA-78 for direct-CLI live-env discovery and records
  BA-90–BA-94 (countdown, terminal copy, contents routing/snapshot and harness
  oracle priority). Older remediation records and their source pins stand.
- **Source impact:** audit and baseline documentation only; plan, design,
  code, content, generated participant documents, deployment and participant
  records unchanged. API/UI source stamps already match this bundle at schema
  `0007_contents_fallback`; no plan task or G1–G10 gate was closed.
- **Requirement trace:** no requirement text changed; regeneration not needed.
  Click-through and appendix freshness checks now pass on this bundle. The
  final five-persona archived run is stamped to an earlier source bundle, not
  a current-source acceptance run.

## Re-pin 2026-10-05 (BA-78 residual + BA-90–BA-94 remediation record)

- **Plan audit (historical checksum `a5cd800db1f7b007ed64d182b2ee777e76c40a68b82c1873e1bc276945c950e5`, via
  `84b02e0cc1ca602adf60f7bbcc49eee195a4add2a6afb5c002a65e9462af5e10`):**
  appended the dated remediation record closing the narrowed BA-78 residual
  and all five BA-90–BA-94 findings on source bundle
  `d7a10cf9c9da934b1e9d8cf991b22a57fb6d3dcb2a12d580dabfcd3588b6e770`
  (check-in countdown anchored to `server_time`, reveal-stage terminal copy,
  deterministic-first contents routing, snapshot-scoped summaries,
  severity-aware near-repeat dedupe, always-on project `.env` live reference).
- **Source impact:** audit and baseline documentation only. Application,
  harness, and frontend source changed under this bundle and is described by
  the remediation record; deployment stamps are reported in
  `docs/IMPLEMENTATION_STATUS.md`, not assumed here.
- **Requirement trace:** no requirement text changed; regeneration not
  needed. Generated click-through and appendix checks pass on `d7a10cf9…`.
  Post-fix evidence (`run-20261005T003940Z`, `run-20261005T004059Z`,
  `run-20261005T004116Z`) is archived under
  `docs/evidence/2026-10-04-agent-sim/`.

## Re-pin 2026-10-05 (UX round 4 — training-flow clause)

- **Design specification
  (`2ad3edc43b16c05308a44b25425191e575ed12b851c24ca021e6d429a2c856b8`, via
  `2285b4310d91873587cd7ba2f2ee63839d568fe072ed885c7f84dec7b1fd118f`):**
  the §10 piloted-UI clause now describes the UX_Feedback_v4 training shape —
  the walkthrough ends in a workbench-mirror practice page gating Continue on
  one required share, with team chat and the assistant shown as labelled
  previews instead of two try-it pages gating per-action. No requirement
  sentence changed; the "must demonstrate every analytic interaction and
  equally discoverable routes" clause stands and is now satisfied by
  demonstration (previews show the pane layout and routes) rather than by
  required per-control interaction — a recorded UX trade-off, item 3/4/7 of
  `UX_Feedback_v4.pdf`.
- **Source impact:** spec clause, requirement-trace row, and baseline
  documentation. Application/content changes for UX round 4 (practice world
  single-share payload, verbatim+nonrepeating greeting replies, varied
  `requests.demo@v2` templates, diversified `fallbacks.*@v4` social pools,
  intel-on-its-own-card rendering, `/7` check-in copy, the pre-debrief
  teammate-nature choice item in `post_task.*@v2`, six successor profiles)
  ship in the same source bundle; deployment stamps are reported in
  `docs/IMPLEMENTATION_STATUS.md`.
- **Requirement trace:** regenerated (`build_requirement_trace.py`,
  0 unmapped); the CL-S10-01 row carries the updated clause.

## Re-pin 2026-10-05 (UX round 4 — independent plan audit)

- **Plan audit (then-current checksum
  `488fdf7f14204f8a38dbf6f84d0daa2510c8c0231b650c3b06a0a988be424752`, via full
  historical checksum
  `a5cd800db1f7b007ed64d182b2ee777e76c40a68b82c1873e1bc276945c950e5`):**
  prepended a source-pinned review of the fourteen UX feedback requests on
  bundle `e762130929db6bc0923db84c56cb44aac4474cd1121222e5a6f47a5f32627869`,
  with BA-95 (a practice share can be counted before acknowledgement), the
  recorded §10 training-coverage trade-off, and evidence/release limits.
  Earlier audit findings and their subsequent dated remediations remain intact.
- **Source impact:** audit and baseline documentation only. The plan, design
  spec, application, content, deployment and participant data were not changed
  by this review; no G1–G10 or human-review gate was closed.
- **Requirement trace:** no requirement text changed; it remains current.

## Re-pin 2026-10-05 (UX round 4 — BA-95 remediation record)

- **Plan audit (a0f430cd…8dd5, superseded by 6157039d…e3 on 2026-10-07; via
  `488fdf7f14204f8a38dbf6f84d0daa2510c8c0231b650c3b06a0a988be424752`):**
  appended the BA-95 remediation record to the UX-round-4 addendum — server
  enforcement of the required practice share (`practice_share_required`),
  acknowledgment-gated client share state, retryable failure surfacing,
  in-flight dedupe, and the harness runner's matching practice share, with
  test and live-smoke evidence on source bundle
  `f9fb50dfb264813990aeb9c981cf28715ff63c1a94acce45675667e4c5e4b47b`
  (deployed; see `docs/IMPLEMENTATION_STATUS.md`).
- **Source impact:** audit and baseline documentation only; the application
  and test changes are the bundle above.
- **Requirement trace:** no requirement text changed; it remains current.

## Re-pin 2026-10-06 (S2 demo archival)

- **`investigators_time_pressure_demo.html` (S2):** moved byte-identically from
  the repo root to `docs/baseline/reference/` (SHA unchanged,
  `7ced90c5…9825`). Owner decision: the root file's role was always a shareable
  playable mock of the latest build — not a pinned source — so the frozen S2
  reference is archived and the root file is freed to track the current build.
  `extract_demo_reference.py` and `behavior_reference.json` now read the
  archived path; `demo_constants.json` regenerates byte-identically (same
  bytes, basename recorded); all 76 TP behavior anchors resolve to the archive.
  Artifact `reference_copy`/`sources` strings that name
  `investigators_time_pressure_demo.html` by basename resolve to the archived
  copy.
- **Design specification:** S2 source row and Appendix A checksum row repointed
  to the archived path (`2ad3edc43b16c05308a44b25425191e575ed12b851c24ca021e6d429a2c856b8` → f8b4ef0f…f2bf, superseded by 3be837c4…d849 on 2026-10-07).
- **Timing insights:** I2 row repointed (`9233e236472aaa4b5b6f132a681320259225962d31cd705fb703a7b17d77cbde` → `0aaefc64…f43b`).
- **Requirement trace:** no requirement text changed; it remains current.

## Re-pin 2026-10-07 (BA-96–BA-102 remediation record + fielded-warning annotation)

- **Plan audit (`6157039d…e3`, via
  `a0f430cdf8f1df88601c3c7b389643e24bb8e3d1b9e7ce935aacf011ff7b8dd5`):**
  appended §8 recording the remediation status of all seven second-round
  findings (BA-96 model-treatment manifest, BA-97 consent/payment alignment,
  BA-98 decision register, BA-99 approved-only live bindings, BA-100 re-share
  enforcement, BA-101 export modes, BA-102 plan fielding section); the
  "What moved" preamble now marks its audit-time pins as superseded.
- **Design specification (3be837c4…d849, via
  `f8b4ef0fb49fa84193ec9ad474c1c75eae6f8a4d5da6d992cd884e65dd75f2bf`):**
  §4.1 and §7.3 annotate that `tp_review_first_v10` fields
  `warning.one_minute_left@v2` — the approved republish of the frozen v1 body
  (identical wording, active-300 trigger, low-time styling), selected under
  D10 and required by the BA-99 approved-only live gate.
- **Requirement trace:** regenerated (`build_requirement_trace.py`,
  0 unmapped).

## Re-pin 2026-10-07 (current-build reconciliation)

- **Design specification (`bca9a2ebcba2…8160`, via 3be837c4…d849):** records the deployed services and the fielded `tp_review_first_v10` contract (source bundle `fac6f809…`, `/version` `build_time` `2026-10-07T13:16:55Z`). Resolved selections D01, D02, D04, D06, D07, D10, and D13 are copied into the design. D03, D08, D09, D11, D12, and D14 stay open. G1–G10 stay undemonstrated. The per-table download is described as the remediated `POST /admin/exports/table` path, not as a relaxation of the redaction rules.
- **Reopened tasks:** none. This revision describes an already-fielded build. It does not change participant-facing behavior, and it does not reopen completed work.
- **Requirement trace:** regenerated (`build_requirement_trace.py`). `scripts/check.sh` was not re-run for this re-pin.

## Re-pin 2026-10-10 (fielded-line annotation)

- **Design specification (561c8741…06d5 — superseded, via `bca9a2ebcba2…8160`):** the
  §"Fielded profile" subsection gains a dated blockquote marking the
  2026-10-07 `tp_review_first_v10` description as the historical record and
  pointing at the current fielded line — `study.live.v7`
  (`tp_ai_v6`/`tp_scripted_v8` × high/low, consent v6, 300 s,
  `prompt.assistant_demo@v4`, opened 2026-10-10). No requirement text,
  timing, payment, or bound-material statement changed; the v10 bullets are
  retained verbatim.
- **Reopened tasks:** none. Annotation only; no participant-facing behavior
  change and no completed work reopened.
- **Requirement trace:** regenerated (`build_requirement_trace.py`).
  `scripts/check.sh` was not re-run for this re-pin.


## Re-pin 2026-10-10 (incentive-compatibility annotation)

- **Design specification (2c91c81d…201b — superseded by ffc31940…9530, via 561c8741…06d5):** the
  §"Fielded profile" blockquote pointer is updated to the incentive-compatible
  fielded line — `study.live.v8` (`tp_ai_v7`/`tp_scripted_v9` × high/low,
  `consent.reference@v7` $3.00 + $0.50 bonus offer paid to every completer,
  `payment.s5_base_only@v5` 350¢); `study.live.v7` recorded as the completed
  four-completer pilot block. No requirement text, timing, or bound-material
  statement changed; the v10 bullets remain the retained 2026-10-07 record.
- **Reopened tasks:** none. Annotation only; no participant-facing behavior
  change and no completed work reopened.
- **Requirement trace:** regenerated (`build_requirement_trace.py`).
  `scripts/check.sh` was not re-run for this re-pin.


## Re-pin 2026-10-15 (demo-file-stem rename)

- **Design specification (`b158a258…69e3`, via `50cc173a683814155490e725efbfeab8fde41ed26c44c13313145c0503bb2868`):** annotation-only
  re-pin — the S2 source row notes the live playable-mock template at the repo
  root is now `investigators_demo.html` (the generated mocks likewise dropped
  the `time_pressure_` infix: `demos/investigators_*_demo.{html,js}`); the
  archived S2 reference keeps its frozen name so bound-artifact citations and
  this pin still resolve. No requirement text, timing, payment, or
  bound-material statement changed. `SOURCE_CHECKSUMS.sha256` updated for the
  spec only — the archived demo path, and therefore its pin, is unchanged.

## Re-pin 2026-10-12 (BA-110 fielded-profile annotation)

- **Design specification (`50cc173a683814155490e725efbfeab8fde41ed26c44c13313145c0503bb2868`, via fc41291c8c54ff185f6ac35f6c604ad7fa8bcc7bf1832ede1a3eda6cf7e8a2b0,
  69a7de4e…c1a1, ffc31940…9530, 70c33571…05bc, 2c91c81d…201b):** the 2026-10-12 annotation
  on the fielded-profile subsection records the current state — `study.live.v10`
  closed after the N=4 pilot, AI-only 1×2, `SCRIPTED_TEAMMATES_ENABLED=0`, and
  `tp_ai_v13` as the preview/testing manifest. No requirement text, timing,
  payment, or bound-material statement changed.

## Re-pin 2026-10-11 (documentation-pass annotations + vhost security note)

- **Design specification (`fc41291c8c54ff185f6ac35f6c604ad7fa8bcc7bf1832ede1a3eda6cf7e8a2b0`, via 69a7de4e…c1a1, ffc31940…9530,
  70c33571…05bc, 2c91c81d…201b):** the documentation pass adds the
  `open_ended` stage to the two stage-graph descriptions (§"Lifecycle,
  workbench, and commands" prose and the §5.2 machine diagram) and a
  fielded-profile annotation updated when `study.live.v9` opened 2026-10-11
  as the N=5 Connect pilot (`study.live.v8` closed same day). No requirement
  text, timing, payment, or bound-material statement changed.
- **`apache/investigators.vhost.snippet` (`acfe4183…3107`, via
  174bba84…1177):** the 2026-10-11 admin-allowlist security update (applied
  to `/etc/httpd/conf.d/investigators.conf` the same day; managed-route block
  verified byte-identical to the snippet 2026-10-11). The pin file had not
  been updated with the snippet; caught by `test_pinned_inputs_match_disk`
  during the documentation pass.
- **Reopened tasks:** none. Annotations and ingress hardening record only.
- **Requirement trace:** regenerated (`build_requirement_trace.py` — clean,
  0 unmapped).

## Re-pin 2026-10-12 (external study-plan drift, third occurrence)

- **`/opt/research-study_ssl/TEAM_AI-R_AI-M_STUDY_PLAN.md`
  (`0bff6c70efee959d1fcd4173d282970d8b21379183a252ebf671518ecf2f88a1`,
  via 337b18b6c2d23c574951199a051cf680c7db91098262627b30a86e9e8e927c37):**
  owner-edited external input detected by
  `test_pinned_inputs_match_disk` during the practice-page UX gate run —
  third drift this session (file actively edited by its owner; no git history
  in that repo, undiffable). Re-pinned to current disk state; the file is an
  informational citation only — no Investigators requirement, manifest, or
  bound material depends on its contents. Flagged for owner review.
- **Reopened tasks:** none. External-input volatility record only.

## Re-pin 2026-10-13 (external study-plan, owner-directed)

- **`/opt/research-study_ssl/TEAM_AI-R_AI-M_STUDY_PLAN.md` (`4cae85770b3a302ea74b30a0bdb68cfc8da7830ad5d163863e57ba7935bd1b39`):**
  the owner directed this re-pin on 2026-10-13. The on-disk file
  (44529 bytes) no longer matched
  `0bff6c70efee959d1fcd4173d282970d8b21379183a252ebf671518ecf2f88a1`.
  There is no git history for that path, so the edit is undiffable.
  The citation is informational only: no Investigators requirement,
  manifest, or bound material depends on it. The design spec's Appendix A
  row was left unchanged, matching earlier study-plan re-pins.
  This digest was itself superseded later the same day (see the next section).
- **Reopened tasks:** none.

## Re-pin 2026-10-13 (external study-plan, second owner-directed re-pin)

- **`/opt/research-study_ssl/TEAM_AI-R_AI-M_STUDY_PLAN.md`:**
  the owner directed another re-pin, not a restore, on 2026-10-13.
  The on-disk file (45013 bytes) hashed to
  `f129e348508c018a7a16d848d8d3c98fc4b286f2070f71c93a9f435e5facc604` and no longer matched
  `4cae85770b3a302ea74b30a0bdb68cfc8da7830ad5d163863e57ba7935bd1b39`.
  There is no git history for that path, so the edit is undiffable.
  The citation is informational only: no Investigators requirement,
  manifest, or bound material depends on it. The design spec's Appendix A
  row was left unchanged. The experiment-plan header was not edited.
- **Reopened tasks:** none.

## Re-pin 2026-10-14 (external study-plan drift, detected during gate run)

- **`/opt/research-study_ssl/TEAM_AI-R_AI-M_STUDY_PLAN.md`:** the on-disk file
  (44981 bytes) hashed to
  `757d7c70607eb860f7905961409753d0baf24761f9458557edef1aec1891620b` and no longer matched
  `f129e348508c018a7a16d848d8d3c98fc4b286f2070f71c93a9f435e5facc604` when
  `scripts/check.sh` ran the `AI_ASSISTANT_ENABLED` change. The file was
  modified by its owner on 2026-10-13 19:27 -0400 — hours after the same-day
  re-pin — with no git history in that repo, so the edit is undiffable.
  Re-pinned to current disk state; the file is an informational citation
  only — no Investigators requirement, manifest, or bound material depends on
  its contents. Flagged for owner review.
- **`.env.example` (`c0509f6810ee232e1cc4d5a1d53813a896075e3f32dd761bda065f416675780f`):**
  `AI_ASSISTANT_ENABLED` added (default 1). Re-pins the settings template for
  the feature-flag work; the experiment-plan header was updated to match.
- **`.env.example` (`16c0f7ab90507dced16f01a0914ec4f6e1bdb1a065858cd9dfa4d92e1f790d0a`):**
  `CRATE_OVERLAP_ENABLED` + `CRATE_NUMBER_ENABLED` added (both default 0 —
  new-condition profiles blocked from live fielding until enabled). Re-pins
  the settings template for the crate-condition feature-flag work; the
  experiment-plan header was updated to match.
- **`.env.example` (`f9b516634b46944bdea2621888e8028744cf08abf2eff4b933985ad7759694c7`):**
  `TIME_PRESSURE_ENABLED` added (default 1 — the low/high pressure
  manipulation is the fielded default; `0` removes the check-in, pressure
  remarks, warning/low-time styling, and the tp_post_09/11 follow-up items
  in every mode while keeping the neutral deadline). Re-pins the settings
  template for the time-pressure feature-flag work; the experiment-plan
  header was updated to match.
- **Demo-file stem rename (2026-10-15, no re-pin needed):** the live
  playable-mock template `investigators_time_pressure_demo.html` →
  `investigators_demo.html`, and the generated mocks
  `demos/investigators_time_pressure_*_demo.{html,js}` →
  `demos/investigators_*_demo.{html,js}` (admin demo URLs follow).
  The archived S2 reference `docs/baseline/reference/investigators_time_pressure_demo.html`
  keeps its frozen name — bound artifact citations and this pin resolve
  by that basename.
- **`.env.example` (`21baf5619dac49efb28d681d55f5b57a236d534eb9aad3184b36913d40ce67e4`):**
  `AI_QA_ENABLED` added (default 0 — an addition flag: `1` inserts the
  optional "AI Q&A (Optional)" rules-question page between the
  comprehension check and connecting; `0` keeps the legacy direct
  training → connecting transition). Re-pins the settings template for
  the AI-Q&A feature-flag work; the experiment-plan header was updated
  to match.
- **`.env.example` (`0fa6960191de225e5f2571971a16e93f0a515216daf48bd227a40018d1099faa`):**
  `BONUS_PAYMENT_ENABLED` added (default 1 — a removal flag: `0`
  substitutes the served consent with `consent.reference.nobonus@v1`,
  removes the bonus sentences from the case background / answer bar /
  debrief, and computes entitlements under `payment.s5_nobonus@v1`
  ($3.00 flat) while manifests stay pinned to the bonus-on contract).
  Re-pins the settings template for the bonus-payment feature-flag work;
  the experiment-plan header was updated to match.
- **Reopened tasks:** none. Feature-flag addition + external-input volatility record.
