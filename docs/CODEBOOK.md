# Investigators measure codebook

**Codebook version:** `cb-1.11`  
**Generated:** from `backend/app/measurement/codebook.py` (do not edit by hand — regenerate with `cd backend && uv run python -m app.measurement.codebook`)  
**Scope:** WP11 measurement half (INV-1101-1105). All measures are computed from committed database records only; client-reported timing is never a source. Spec: `docs/DESIGN_SPEC.md` §§9.1-9.3.

## Measure states

| State | Meaning |
|---|---|
| `measured` | A value exists. |
| `undefined_denominator` | The declared denominator is 0/absent — the measure does not exist for this session. Not 0, never a crash. |
| `missing_data` | Required committed records are absent — kept missing, never imputed or backfilled to 0. |
| `not_applicable` | The construct is structurally unavailable for this study/session (e.g. `pressure_checkin` on a PE profile). |

## Missing-data rules

- **`missing_if_no_receipts`** — Counts committed command receipts; zero accepted receipts is a true 0. The command ids are unique per session, so client retries can never inflate counts — a retried command shares one receipt.
- **`missing_if_no_shares`** — Zero committed share rows is a true 0 (the participant never shared); shares are durable records, not UI hints.
- **`missing_if_no_deliveries`** — Zero delivered record-bearing messages/shares is a true 0.
- **`missing_if_no_events`** — Zero committed events of the counted type is a true 0 (the behavior never occurred); events are durable records, not UI hints.
- **`missing_when_never_performed`** — No committed event of the action type exists → state ``missing_data``, value NULL. Never 0 and never the task deadline.
- **`undefined_when_no_seeking`** — Denominator (AI prompts + structured teammate requests) is 0 → state ``undefined_denominator``, value NULL. The structured-channel share of seeking does not exist when no seeking happened.
- **`undefined_when_no_eligible_requests`** — Denominator (eligible accepted requests) is 0 → ``undefined_denominator``.
- **`undefined_when_no_transitions`** — Denominator (answer-revision transitions, i.e. revisions with a prior nonmissing answer) is 0 → ``undefined_denominator``.
- **`undefined_when_no_evaluated_assertions`** — Denominator (support-evaluated claims) is 0 → ``undefined_denominator``. Unevaluated claims are missing coverage, never supported.
- **`undefined_when_no_deliveries`** — Denominator (delivery events) is 0 → ``undefined_denominator``.
- **`undefined_when_no_teamdep_crates`** — Denominator (assigned team-dependent crates) is 0 — e.g. malformed world — → ``undefined_denominator``.
- **`undefined_when_none_requested`** — Denominator (team-dependent crates the participant requested) is 0 → ``undefined_denominator``.
- **`missing_counts_incorrect`** — Displayed accuracy treats a missing or unfrozen assigned-crate answer as incorrect; the answered-only companion measure is reported separately.
- **`undefined_when_none_answered`** — Denominator (answered assigned crates) is 0 → ``undefined_denominator``.
- **`censored_at_horizon`** — Intervals open at the last committed event are truncated at the horizon and flagged censored; never closed early and never extended by guesswork.
- **`missing_never_zero`** — A required committed record (event stamp, timestamp, exposure anchor) that is absent yields NULL for that field — arrival/exposure times are never backfilled to 0 (spec §9.3).
- **`legacy_default_independent_8_null`** — Sessions whose snapshot predates `world_params` and whose manifest carries no `task` pins resolve to the legacy administered shape — `crate_overlap='independent'`, `n_crates=8`, `intel_budget=NULL`; the NULL marks 'full legacy file', not missing data.
- **`missing_when_no_file_telemetry`** — No `telemetry_file_*` / `telemetry_share_attempt` events in the session → ``missing_data``, value NULL — the session either predates the 2026-10-12 telemetry build or its client telemetry queue failed; the two are indistinguishable and both mean 'no data', never zero. Since cb-1.8 the rule applies per family: a once-per-mount `file_meta` marker proves the collector ran, so a family with zero events under a present marker is a true zero, while a family with no marker and no events (e.g. only a share attempt survived) stays ``missing_data``. An `io_supported:false` marker reports the view family as ``unobservable``.

## Measures

### `seeking.structured_teammate_requests` — Structured teammate seeking

- **Definition:** Count of accepted participant `teammate_request` commands (structured intel asks to JT/PM). Counted from command receipts — one per command_id, so retries never inflate. Agent-originated requests are separate records (requester is a seat), never counted here.
- **Source records:** command_receipt (outcome='accepted'), request
- **Denominator:** accepted_commands
- **Missing rule:** `missing_if_no_receipts`

### `seeking.structured_requests_unique_crates` — Unique crates sought

- **Definition:** Distinct crates among participant-issued structured requests.
- **Source records:** request (requester='participant', kind='structured')
- **Denominator:** crates
- **Missing rule:** `missing_if_no_receipts`

### `seeking.structured_requests_delivered_replies` — Structured requests with delivered replies

- **Definition:** Participant structured requests whose episode recorded a substantive response (committed delivery) — acknowledgment alone does not count (two-stage replies, spec §6.6).
- **Source records:** request_episode.substantive_event_seq
- **Denominator:** requests
- **Missing rule:** `missing_if_no_receipts`

### `seeking.free_text_teammate_messages` — Free-text teammate messages

- **Definition:** Accepted participant `social_reply` commands. Which of these are task-relevant questions is an offline coding product under the versioned rubric (WP12a) — the structured button never captures all seeking.
- **Source records:** command_receipt, request (kind='free_text', target='social')
- **Denominator:** accepted_commands
- **Missing rule:** `missing_if_no_receipts`

### `seeking.typed_teammate_requests` — Typed intel asks (nl_only)

- **Definition:** Count of `teammate_requested` events tagged `via: typed` — natural-language intel asks in `nl_only` that routed into per-seat structured retrieval (O-1/O-2, 2026-10-03). The typed counterpart of `seeking.structured_teammate_requests`; keep the channels separate when comparing arms.
- **Source records:** event (type='teammate_requested', payload.via='typed')
- **Denominator:** events
- **Missing rule:** `missing_if_no_events`

### `automation.probes` — Automation identity probes

- **Definition:** Count of `automation_probe` events — participant messages classified as asking whether teammates are real people / bots (O-3, 2026-10-03). Every recognized probe is logged: probes bypass ordinary pending caps and the operational rate limit (BA-75), so the event count is complete. The event's `probed` vs scheduled `seats` distinguishes probes whose disclosure line was deferred by the bounded per-seat probe budget. Probe-adjacent non-fires carry `probe_near_miss` on `message_accepted` (O-12/BA-67).
- **Source records:** event (type='automation_probe')
- **Denominator:** events
- **Missing rule:** `missing_if_no_events`

### `automation.session_flagged` — Probe session flag

- **Definition:** 1 when the session contains ≥1 `automation_probe` event, else 0. Sessions are flagged, not dropped: the fielding rule (exclude vs sensitivity-check) is a pre-registration decision outside the prototype.
- **Source records:** event (type='automation_probe')
- **Denominator:** sessions
- **Missing rule:** `missing_if_no_events`

### `ai_use.prompts` — AI prompts used

- **Definition:** Accepted participant `assistant_request` commands with route: `typed` when the command carried free-text body, `picker` when it came from the crate picker (crate set, no body). Per-prompt rows carry target crate, delivery, fallback and model-call status. Retries do not add prompts.
- **Source records:** command_receipt, request (responder='assistant'), event payload, message, displayed_output, model_call
- **Denominator:** accepted_commands
- **Missing rule:** `missing_if_no_receipts`

### `sharing.participant_share_events` — Participant share events

- **Definition:** Committed `share` rows with sharer='participant'. Prompted shares carry `prompted_by_request_id` linking the delivered agent ask they answered.
- **Source records:** share
- **Denominator:** share_events
- **Missing rule:** `missing_if_no_shares`

### `sharing.prompted_shares / sharing.unprompted_shares` — Prompted vs unprompted shares

- **Definition:** Prompted = share linked (prompted_by_request_id) to an agent→participant request it resolved; unprompted = no such link.
- **Source records:** share.prompted_by_request_id
- **Denominator:** share_events
- **Missing rule:** `missing_if_no_shares`

### `sharing.own_crate_shares / sharing.unassigned_crate_shares` — Own vs unassigned crate shares

- **Definition:** Own = shared record's crate is in the participant's assigned four; unassigned = not assigned. Record crate comes from the committed `record` row, not the client.
- **Source records:** share ⨝ record
- **Denominator:** share_events
- **Missing rule:** `missing_if_no_shares`

### `sharing.proactive_shares_strict` — Proactive sharing (strict)

- **Definition:** Share of an unassigned-crate record committed *before* the earliest delivered agent solicitation for that crate: request announcements (delivered message bound to an agent→participant request row) plus chained crosstalk/social solicitation replies (spec §9.1).
- **Source records:** share ⨝ record, request ⨝ message, message.parent_message_id
- **Denominator:** share_events
- **Missing rule:** `missing_if_no_shares`
- **Notes:** Primary definition for analysis.

### `sharing.proactive_shares_legacy` — Proactive sharing (legacy-compatible)

- **Definition:** The S2/S3 demo rule: unprompted share (`!ctx` ↔ prompted_by_request_id NULL) of a record whose crate is unassigned (`!isMine`) and has no delivered scripted teammate request so far (`!requestedCrates`).
- **Source records:** share ⨝ record, request ⨝ message
- **Denominator:** share_events
- **Missing rule:** `missing_if_no_shares`
- **Notes:** Retained for INV_FINISH crosswalk only; strict is primary.

### `seeking.allocation_ai_share` — Seeking allocation (AI share)

- **Definition:** ai_prompts / (ai_prompts + structured teammate requests). A structured-channel measure only — free-text seeking is excluded by definition until offline coding classifies it.
- **Source records:** command_receipt
- **Denominator:** accepted_commands
- **Missing rule:** `undefined_when_no_seeking`

### `accuracy.correct_total` — Accuracy (displayed total)

- **Definition:** Correct frozen answers on the participant's 4 assigned crates / 4. Missing and unfrozen assigned crates count as incorrect in this displayed total.
- **Source records:** answer_final ⨝ world_snapshot.content.crates[].truth
- **Denominator:** assigned_crates
- **Missing rule:** `missing_counts_incorrect`

### `accuracy.entered_count` — Crate locations entered

- **Definition:** Assigned crates carrying a frozen final answer (a selected location) — the crate-completion count, 0 to n_assigned. Counts locations selected regardless of correctness; unfrozen and never-touched assigned crates do not contribute.
- **Source records:** answer_final
- **Denominator:** assigned_crates
- **Missing rule:** `missing_if_no_events`

### `accuracy.correct_ownfile / accuracy.correct_teamdep` — Accuracy by role

- **Definition:** Correct frozen answers on own-file assigned crates / 2 and on team-dependent assigned crates / 2, split by world_snapshot role_of.
- **Source records:** answer_final, world_snapshot.content (truth, role_of)
- **Denominator:** ownfile_crates / teamdep_crates
- **Missing rule:** `missing_counts_incorrect`

### `accuracy.answered_only` — Accuracy among answered

- **Definition:** Correct / answered assigned crates; undefined when none answered.
- **Source records:** answer_final, world_snapshot.content
- **Denominator:** answered_assigned_crates
- **Missing rule:** `undefined_when_none_answered`

### `answers.per_crate_state` — Per-crate answer state

- **Definition:** answered (frozen final exists) | unfrozen (revisions exist but the task never froze them) | missing (no revision). Missing is a distinct state, never conflated with wrong.
- **Source records:** answer_revision, answer_final
- **Denominator:** crates
- **Missing rule:** `missing_never_zero`

### `confidence.per_crate` — Confidence per answered crate

- **Definition:** Latest committed confidence_response per crate: 0-100 only when status='answered'; 'skipped' and 'missing' stay distinct. The slider's visual default is never data (spec §9.1).
- **Source records:** confidence_response
- **Denominator:** answered_crates
- **Missing rule:** `missing_never_zero`

### `request.episodes` — Teammate asks of participant

- **Definition:** Request episodes whose responder is the participant — the teammate asks the participant is expected to answer with a share. Episode lifecycle: a share on the asked crate closes 'participant_share'; scripted/structured delivery closes 'delivered'; deadline and abandonment expire the rest.
- **Source records:** request_episode ⨝ request (responder='participant')
- **Denominator:** episodes
- **Missing rule:** `missing_if_no_events`

### `request.addressed` — Teammate asks answered by share

- **Definition:** Episodes closed with close_reason='participant_share' — the participant shared a record on the asked crate while the episode was open. Responsive sharing, distinct from sharing.participant_share_events which counts every share including unrequested ones.
- **Source records:** request_episode.close_reason
- **Denominator:** episodes
- **Missing rule:** `missing_if_no_events`

### `request.addressed_frac` — Share of teammate asks answered

- **Definition:** request.addressed / request.episodes.
- **Source records:** request_episode
- **Denominator:** episodes
- **Missing rule:** `undefined_when_no_seeking`
- **Notes:** Undefined (missing) when the session opened no participant-responder episodes.

### `request.answer_s_median` — Median share-answer latency (s)

- **Definition:** Median wall-clock seconds from episode opened_at to closed_at across participant_share-resolved episodes; missing when the participant answered none.
- **Source records:** request_episode (opened_at, closed_at, close_reason)
- **Denominator:** addressed_episodes
- **Missing rule:** `missing_when_never_performed`

### `confidence.rated_count` — Crates with a rated confidence

- **Definition:** Assigned crates whose latest confidence_response revision is answered — the denominator confidence.mean silently conditions on. Compare against accuracy.entered_count to see rating coverage.
- **Source records:** confidence_response
- **Denominator:** answered_crates
- **Missing rule:** `missing_if_no_events`

### `answers.update_events` — Answer update events

- **Definition:** Committed answer_updated events — every pick and revision, not just frozen finals. Zero is a true zero (board never touched); compare with accuracy.entered_count.
- **Source records:** event (answer_updated)
- **Denominator:** events
- **Missing rule:** `missing_if_no_events`

### `answers.first_answer_s / answers.last_answer_s` — First/last answer-entry latency (s)

- **Definition:** Wall seconds from task_clock_started's server_time to the first and last answer_updated. Wall (not active) time — domain events carry no active_ms; idle suspension can inflate the value. last_answer_s near the task budget = deadline-driven entry.
- **Source records:** event (answer_updated, task_clock_started).server_time
- **Denominator:** task_wall_s
- **Missing rule:** `missing_when_never_performed`

### `items.median_dwell_ms / items.dwell_lt1s_count` — Per-item dwell (median, sub-1s count)

- **Definition:** telemetry_item_start→item_end pairs per item_id: client_ms delta when both endpoints share a client_epoch, else server_time. dwell_lt1s_count = pairs under 1000 ms — the flat-liner signal. Missing when no complete pairs exist.
- **Source records:** event (telemetry_item_start, telemetry_item_end)
- **Denominator:** paired_items
- **Missing rule:** `missing_when_never_performed`

### `telemetry.reconnects` — Client reconnections

- **Definition:** Distinct telemetry client epochs minus one — each is a client reload/restart mid-session. A silent-disruption flag for interpreting a session's task window.
- **Source records:** telemetry events (client_epoch)
- **Denominator:** epochs
- **Missing rule:** `missing_if_no_events`

### `chat.rendered_frac` — Team messages rendered to client

- **Definition:** Team-channel delivered messages carrying a client telemetry_render receipt / delivered team messages — the delivery→DOM pipeline, not attention.
- **Source records:** message (channel='team', status='delivered'), event (telemetry_render)
- **Denominator:** delivered_team_messages
- **Missing rule:** `missing_if_no_deliveries`

### `chat.scroll_events` — Chat-pane scroll telemetry

- **Definition:** Client chat_scroll telemetry (throttled to ≥1.5 s apart, geometry only) — the only participant-side signal the team channel was engaged. Requires the cb-1.11 emit; sessions bound to earlier builds are unobservable, never zero.
- **Source records:** event (telemetry_chat_scroll)
- **Denominator:** telemetry_events
- **Missing rule:** `missing_if_no_events`
- **Notes:** Observability gated on session behavior_source_sha256 == the deploying build.

### `open_ended.total_chars / open_ended.min_item_chars` — Free-text volume (total / minimum)

- **Definition:** Character lengths of answered open_ended item responses — total and per-item minimum. A near-zero minimum flags 'did not engage' answers (e.g. 'mine').
- **Source records:** item_response (instrument_id LIKE 'open_ended%', status='answered')
- **Denominator:** answered_open_ended_items
- **Missing rule:** `missing_if_no_events`

### `timing.first_teammate_request_ms / first_assistant_prompt_ms / first_request_ms / first_share_ms / first_request_or_share_ms` — First-action latencies (active ms)

- **Definition:** Active-time stamp of the first committed event of each kind. first_request_ms is request-only (structured + assistant); first_request_or_share_ms is the legacy `counters.first` semantic (either sets it) and is the honest replacement for INV_FINISH's mislabelled `inv_first_request_sec` — keep it clearly named.
- **Source records:** event (teammate_requested, assistant_requested, record_shared).active_ms
- **Denominator:** active_ms
- **Missing rule:** `missing_when_never_performed`

### `timing.request_to_delivery_ms` — Request→response latency

- **Definition:** Per episode: substantive_event_seq's active stamp minus the request's accepted_active_ms. Never computed from client clocks; expired episodes are excluded from the mean and retained by status (§6.5 censoring).
- **Source records:** request, request_episode, event
- **Denominator:** requests
- **Missing rule:** `censored_at_horizon`

### `exposure.one_minute_warning` — Warning exposure

- **Definition:** manipulation_exposure(kind='one_minute_warning'): committed_seq anchors actual delivery (the committed message_delivered event), received/rendered_seqs anchor client receipts. Scheduled but uncommitted is 'scheduled', not exposure; a missing render receipt stays missing.
- **Source records:** manipulation_exposure, event
- **Denominator:** exposures
- **Missing rule:** `missing_never_zero`

### `exposure.pressure_checkin / pe_*` — Manipulation exposure windows

- **Definition:** Per exposure: actual commit event (checkin_open / exposure_committed), render and acknowledgment anchors, assigned values, post-exposure opportunity span to the active horizon. Windows use actual reveal/resume events, never nominal seconds 60/300.
- **Source records:** manipulation_exposure, event
- **Denominator:** exposures
- **Missing rule:** `missing_never_zero`

### `manipulation_checks.items + pressure_checkin.own_rating` — Manipulation checks

- **Definition:** Post-task item_responses (perceived pressure/evaluation, expected responsiveness, felt rushed, concern, usefulness, suspicion) and the check-in own_rating are DISTINCT constructs — no composite success score. own_rating is a pre-reveal manipulation input (collected before teammate values are revealed) and can never be a post-exposure effect.
- **Source records:** item_response, manipulation_exposure.own_rating
- **Denominator:** items
- **Missing rule:** `missing_never_zero`
- **Notes:** Suspicion items precede debrief; agent labels stay out of lead-ins.

### `evidence.decisive_delivery` — Decisive-evidence delivery

- **Definition:** Assigned team-dependent crates with a qualifying final-location record (crate match + location == truth + arrival/still_at) delivered before task closure / 2. Delivery = initial file, committed share, or delivered record-bearing message. Requested and rendered subsets reported separately; unclosed tasks report censored crates.
- **Source records:** world_snapshot, record, share, message, delivery_receipt, event
- **Denominator:** teamdep_crates
- **Missing rule:** `undefined_when_no_teamdep_crates`

### `evidence.request_fulfillment` — Request fulfillment

- **Definition:** Eligible accepted requests whose episode recorded a substantive response before closure / eligible accepted requests. Acknowledgment is not fulfillment; expired/cancelled are retained by status; still-open episodes at an unclosed horizon are censored.
- **Source records:** request, request_episode, event
- **Denominator:** eligible_requests
- **Missing rule:** `undefined_when_no_eligible_requests`

### `evidence.correction_rate / evidence.deterioration_rate` — Correction and deterioration

- **Definition:** wrong→correct and correct→wrong saved-revision transitions / all transitions (revisions with a prior nonmissing answer). First revisions and never-answered crates are not transitions. Descriptive process measures — not proof of conformity or consensus (spec §9.3).
- **Source records:** answer_revision, world_snapshot truth
- **Denominator:** revision_transitions
- **Missing rule:** `undefined_when_no_transitions`

### `evidence.delivery_events / distinct_records_delivered / redundant_deliveries / redundancy_ratio` — Delivery redundancy

- **Definition:** Deliveries of record-bearing messages+shares versus distinct records. Repeat deliveries (replay/resync or re-share) are redundant — repeated obsolete evidence is not new current evidence.
- **Source records:** message, share
- **Denominator:** delivery_events
- **Missing rule:** `undefined_when_no_deliveries`

### `evidence.unsupported_assertion_rate` — Unsupported assertion rate

- **Definition:** Claims evaluated 'unsupported' / support-evaluated claims. 'unevaluated' and 'negated' claims sit in coverage, not the rate. Free-text factual statements require offline coding (WP12a) — this covers exact claims.
- **Source records:** claim
- **Denominator:** evaluated_assertions
- **Missing rule:** `undefined_when_no_evaluated_assertions`

### `transfer.* (world/held/eligible/selected/delivered/rendered/in_file/in_context/referenced/in_answer/never)` — Evidence-transfer ledger

- **Definition:** Per-record stage reachability (services.claims.transfer_ledger) plus the in_answer derivation (BA-25): a cited record reaches in_answer when the claim citing it is about the record's own crate (claim_source.record_id → record.crate = claim.crate = answer_revision.crate) and a same-crate answer revision commits after the claim's reference event — its displayed_output delivery (the render telemetry event when a delivery_receipt shows a render; the delivered commit seq when it does not). Claims that never reached a participant-visible surface have no anchor and cannot mark records. Descriptive association only — never proof of reliance. 'never' lists the teammate-held record ids that never reached delivery or the file — a set, not a count that could hide which.
- **Source records:** seat_holding, file_membership, share, message, delivery_receipt, knowledge_snapshot, claim, claim_source, displayed_output, event, answer_revision
- **Denominator:** records
- **Missing rule:** `missing_never_zero`

### `episodes.pending_* / opportunity_*` — Request-episode pending and opportunity

- **Definition:** Per-episode pending on both clocks; the UNION of overlapping pending windows (never the sum — concurrent pending must not double-count); opportunity windows for participant-responder asks from accept to resolution or the horizon.
- **Source records:** request, request_episode, event
- **Denominator:** requests
- **Missing rule:** `censored_at_horizon`

### `periods.pre_checkin / paused / post_resume / channel_open` — Period segmentation and channel-open time

- **Definition:** Running/paused clock_interval segments (paused freezes active elapsed); pre-check-in vs post-resume split at the first pause; per-channel opportunity ms = active ms inside phases enabling the channel. solo_review contributes zero channel-open time — structurally closed time is not reluctance (spec §9.1).
- **Source records:** clock_interval, manifest channel phases, event horizon
- **Denominator:** active_ms
- **Missing rule:** `censored_at_horizon`

### `attention.hidden_count / hidden_ms_total / *_during_task / carried_open / censored` — Window attention (tab visibility and focus)

- **Definition:** Tab-level transitions only. Two horizons: session-wide totals and `*_during_task` values restricted to the solo_review → answers_locked window on server seq (cb-1.8). A hidden interval open at the window close counts and sets `hidden_carried_open` (it ran unobserved past task close); one open at the last telemetry event sets `hidden_censored` and does not duration-estimate. `attention_first_epoch` is the earliest client_epoch seen — 0 means fully instrumented, >0 warns of dropped epochs. Zero events = true zeros: the client emits on every windowing change while its queue is alive.
- **Source records:** event (telemetry_visibility, telemetry_blur, telemetry_focus) payload.client_ms
- **Denominator:** telemetry_events
- **Missing rule:** `missing_if_no_events`

### `item.dwell_ms / item_timing / item_timing_partial` — Per-item engagement time

- **Definition:** `telemetry_item_start`→`telemetry_item_end` pairs per item_id, FIFO on client_ms within each epoch (cross-epoch pairs never match), summed across repeated opens. Exported as a JSON map in participant_wide.item_dwell_ms; item_timing gives per-item starts/ends/dwell and a `partial` flag (unpaired start or end); item_timing_partial lists the partial ids. Unpaired ends are dropped — missing stays missing. Client-reported timing (best-effort), not the command timestamp.
- **Source records:** event (telemetry_item_start, telemetry_item_end)
- **Denominator:** items
- **Missing rule:** `missing_if_no_events`

### `file.coverage_frac / n_records_viewed / views_state` — Case-file coverage

- **Definition:** Distinct file records with ≥1 qualified viewport impression (`file_record_view` — ≥300 ms at intersectionRatio ≥0.6 inside the case-file scrollport, suspended under hidden tabs and occluding check-in overlays) ÷ file_membership rows. An impression proves the card was on screen, not that it was read. `file_views_state` reports measured / missing_data / unobservable (io_supported:false `file_meta`).
- **Source records:** event (telemetry_file_record_view, telemetry_file_meta), file_membership
- **Denominator:** file_records
- **Missing rule:** `missing_when_no_file_telemetry`

### `file.dwell_ms_total / dwell_segments / dwell_state (+ per-crate, per-record)` — Case-file dwell time

- **Definition:** Summed qualified-view segments per record (`file_record_dwell`; open segments close with final:true at task close, unmount or pagehide). Per-crate and per-record aggregates land in crate_long and evidence_transfer. `file_dwell_state` reports measured / no_events / missing_data / unobservable.
- **Source records:** event (telemetry_file_record_dwell, telemetry_file_meta), record, file_membership
- **Denominator:** dwell_segments
- **Missing rule:** `missing_when_no_file_telemetry`

### `file.max_scroll_depth / reached_bottom / scroll_reversals / scroll_distance_px` — Case-file scroll behavior

- **Definition:** Client-aggregated scroll state (`file_scroll_summary`, emitted ≤1 per 5 s while changed + at task close, unmount and pagehide): farthest item index and (scrollTop+clientHeight)/scrollHeight depth fraction, max over the session; `file_reached_bottom` = max_depth ≥ 0.95; direction_reversals counts ≥8 px sign changes of scroll delta; distance is summed absolute scroll px including the initial baseline. Session aggregates take the max per client_epoch, then max across epochs.
- **Source records:** event (telemetry_file_scroll_summary)
- **Denominator:** summaries
- **Missing rule:** `missing_when_no_file_telemetry`

### `file.reorder_count / reorder_distance / reorder_state` — Case-file rearrangement

- **Definition:** Committed reorders (`file_reorder`) — count and summed |to-from| index distance across all methods (drag/grip/arrow). The final order itself is `file_order_final` in the raw events; `file_final_order`/`file_order_moved` verify committed order (not just attempts). `file_reorder_state` distinguishes true zero (a `file_meta` marker proves the collector ran) from missing.
- **Source records:** event (telemetry_file_reorder, telemetry_file_order_final, telemetry_file_meta)
- **Denominator:** reorders
- **Missing rule:** `missing_when_no_file_telemetry`

### `share.attempts / via_drag_frac / share_state / channel_known / channel_unknown` — Share affordance channel

- **Definition:** `share_attempt` outcomes by via (button|drag): sent / rejected:<kind> / ignored_not_own. Since cb-1.8 the durable share rows are the denominator: each share joins its record's first `sent` attempt; `share_channel_known` counts joined shares, `share_channel_unknown` counts durable shares with no matching attempt (telemetry loss — channel unrecoverable). `share_via_drag_frac` = drag-joined durable shares ÷ durable shares; `share_via_drag_frac_observed` = drag ÷ sent attempts (attempt-space descriptive, biased upward when attempts were lost). `file_share_state` stays measured when only durable shares exist so the channel loss is visible rather than NULL.
- **Source records:** event (telemetry_share_attempt), share
- **Denominator:** shares
- **Missing rule:** `missing_when_no_file_telemetry`

### `file.post_share_return_ms / _n / _persist_ms (+ per-record fields)` — Return latency to teammate-added intel

- **Definition:** Per record: first `file_record_view` minus the acquisition anchor (durable share delivered_seq or file_membership.acquired_event_seq). Occurrence semantics (client_ms receipt → client_ms view, same epoch only — client_ms restarts per page load) is the headline `file_post_share_return_ms` (min over added records, `_n` = contributing count). `_persist_ms` is the cb-1.7 persist-time proxy (view active_ms - anchor active_ms), kept as fallback. Per-record occ/server/anchor detail lands in evidence_transfer. NULL when the record was never viewed or the anchor is missing.
- **Source records:** event (telemetry_file_record_view, telemetry_receive/render), share.delivered_seq, file_membership.acquired_event_seq
- **Denominator:** added_records
- **Missing rule:** `missing_when_no_file_telemetry`

### `file.latest_record_viewed / stale_viewed_without_latest / n_records_viewed (+ file_world_latest_available/viewed/stale_viewed_without_world_latest)` — Per-crate latest-vs-stale exposure (crate_long)

- **Definition:** Per crate: the file-member record with max timeline_ordinal is the latest intel; `file_latest_record_viewed` = it was viewed; `file_stale_viewed_without_latest` = any older file record for the crate was viewed while the latest never was. cb-1.8 adds the world-latest comparison: `file_world_latest_available` = the crate has a newer record NOT in the participant's file (new intel exists beyond what they hold); `file_world_latest_viewed` / `file_world_stale_viewed_without_latest` compare their exposure to that broader record. File-absent crates read 0/NULL (denominator policy), telemetry-absent sessions read NULL. Exposure evidence only — never proof of reliance.
- **Source records:** event (telemetry_file_record_view), record.timeline_ordinal, file_membership
- **Denominator:** file_records_per_crate
- **Missing rule:** `missing_when_no_file_telemetry`

### `condition.crate_overlap / n_crates / intel_budget` — Crate-distribution condition factors (participant_wide)

- **Definition:** The administered task shape, exported per session as `crate_overlap` (independent|none|some|full), `n_crates` (4|8|12), and `intel_budget` (20 or NULL). Resolution order: the persisted `world_snapshot.content.world_params` first — the immutable administered world; then the registered manifest's `task` block (pre-world sessions); then the legacy defaults `independent`/`8`/NULL for sessions without either. Never the deployment flags — a flag flip must not rewrite a historical session's condition. `covered_unassigned` in `world_params` names the unassigned crates the participant's file covers under `none` (empty for worlds generated before that field existed; legacy behavior = all unassigned covered). Denominators are unchanged: accuracy is still over the four assigned crates.
- **Source records:** world_snapshot.content.world_params, experiment_manifest.content.task
- **Denominator:** sessions
- **Missing rule:** `legacy_default_independent_8_null`

## Telemetry semantics (bounded, best-effort)

Client telemetry posts batched items to `/telemetry`; the server whitelists `TELEMETRY_KINDS` and persists one `telemetry_<kind>` event per item. Two timings exist and must not be conflated: `payload.client_ms` is the honest per-item occurrence time on the client monotonic clock; `event.active_ms` (stamped since 2026-10-12, previously NULL) is the **batch persist-time** task stamp shared by every item in the flush. `event.stage` likewise reflects the flush, not the interaction. Telemetry is not authoritative: batches are dropped on offline/queue-cap/cooldown, so absence means missing — never fabricated. Every item carries `client_epoch` — a per-page-load integer; measures aggregate per epoch and never subtract across epochs. A `file_meta` item fires once per mount with `io_supported` and task_open file size: it is the zero-vs-missing marker (families with zero events under a present marker are true zeros). On `pagehide` the queue drains with fetch keepalive, so closed tabs still land final dwell/order — still best-effort. Render receipts (`telemetry_render`) prove DOM presence of a chat/assistant message, NOT viewport visibility; per-record case-file viewport evidence is the separate `file_record_view`/`file_record_dwell` pair (≥300 ms at intersectionRatio ≥0.6 inside the case-file scrollport, suspended while the tab is hidden or an overlay check-in covers the card).

### Event vocabulary

| Kind | Emitted by | payload.data | Notes |
|---|---|---|---|
| `telemetry_receive` | state controller | `{seq, type, replayed}` | the committed event row arrived at the client store |
| `telemetry_render` | chat + assistant panes | `{message_id, channel}` | message committed into the DOM — mount receipt, NOT viewport visibility (no IntersectionObserver on chat) |
| `telemetry_render_first_final` | chat + assistant panes | `{message_id, channel}` | fires alongside render — the first/final render receipt pair |
| `telemetry_visibility` | workbench shell | `{state: hidden|visible}` | document.visibilitychange; pairs close hidden intervals |
| `telemetry_focus` | workbench shell | `{}` | window focus |
| `telemetry_blur` | workbench shell | `{}` | window blur (includes alt-tab without tab switch) |
| `telemetry_item_start` | item form components | `{item_id}` | instrument item engagement opened |
| `telemetry_item_end` | item form components | `{item_id}` | item engagement closed; pairs FIFO within one epoch |
| `telemetry_file_meta` | case-file pane | `{io_supported, n_items}` | once per workbench mount — the zero-vs-missing marker: families with zero events under a present marker are true zeros |
| `telemetry_file_record_view` | case-file pane | `{record_id, crate, source}` | qualifying impression: ≥300 ms at intersectionRatio ≥0.6 inside the file scrollport; deduped per record per mount |
| `telemetry_file_record_dwell` | case-file pane | `{record_id, dwell_ms, final?}` | cumulative exposure segment at card exit; final:true flush at task close/pagehide |
| `telemetry_file_scroll_summary` | case-file pane | `{depth_frac, max_depth_frac, farthest_index, n_items, direction_reversals, distance_px}` | aggregated scroll stats on ~5 s cadence — raw scroll events are never emitted |
| `telemetry_file_reorder` | case-file pane | `{record_id, from_index, to_index, method: drag|grip|arrow}` | file card reordered |
| `telemetry_file_order_final` | case-file pane | `{order: [record_id, ...], n_items}` | final order snapshot at task close (idempotent finalizer) |
| `telemetry_share_attempt` | case-file pane + chat drop zone | `{record_id, via: button|drag, result: sent|ignored_not_own|rejected:<kind>|error}` | client-side attempt; the durable share row is the committed fact — join by record_id for channel attribution |

## Teammate condition and manipulation fidelity (four-cell TP pilot)

`inv_condition` is the pressure arm (high/low through v14; high/mixed on v15), NOT the teammate factor. `participant_wide.teammate_condition` is `ai` or `scripted`, derived from the pinned `experiment_manifest.content.teammates.social_generation`: `model_with_validated_fallback` → `ai`; `deterministic_only` → `scripted`. Unknown manifests stay NULL. Never infer condition from realized model success.

Manifest mapping is by `social_generation`, not by profile name: every `tp_ai_*` profile (including the crate-condition `tp_ai_ov*` family) → AI; every `tp_scripted_*` profile → scripted (scripted has never been fielded since study.live.v9). Historical pins: v9 `tp_ai_v11`/`tp_scripted_v13`; v8 `tp_ai_v7`/`tp_scripted_v9`; v7 `tp_ai_v6`/`tp_scripted_v8`; v5 `tp_ai_v4`/`tp_scripted_v6`; v4 `tp_ai_v3`/`tp_scripted_v5`. v12-v14 are AI-only on the `tp_ai_ov*_v3` manifests. Cross-check the manifest body rather than hard-coding version names or pooling incompatible builds.

`teammate_generated_reply_share` = `teammate_generated_replies` / `teammate_reply_denominator`, a per-session manipulation-fidelity covariate (not an exclusion rule). The denominator is unique output-audited JT/PM team messages with `status=delivered`. Generated means `fallback=none` and the linked model call `status=succeeded`. A succeeded call substituted at delivery counts as fallback. Failed attempts, retries, assistant output, scripted request announcements, shares, greetings, short acknowledgements and fixed disclosure lines are not opportunities in this denominator.

Companion columns: `teammate_fallback_replies`, `teammate_unknown_replies`, `teammate_reply_denominator`, `teammate_fidelity_state`. With no eligible deliveries: NULL share / `undefined_denominator`; missing condition or broken call linkage: NULL / `missing_data`; scripted condition: NULL / `not_applicable` (never interpret as failed AI generation). Count and rate provenance is observed; condition provenance is configured.

Operational rule selected by the owner: after two live AI sessions with eligible delivered replies within the SAME cohort/build/compatibility key, pause the whole batch group AND platform intake if the reply-weighted share is strictly below 80%, or audit linkage is missing. Exactly 80% passes. Pressure arms may be combined for this operational check only; analytic aggregates remain arm-specific. `Monitor → teammate_fidelity` reports `pause_recommended`/`missing_data`; an operator performs the pause, never silently changing clocks, excluding participants or rewriting their cell.

Required-items launch profiles (AI v2+, scripted v4+) prohibit skips; legacy profiles retain their pinned skip semantics. Blank unsubmitted observations are still missing, not zeros. Before interpreting the N=12 pilot, independently recompute each primary measure from the long tables for ALL eligible sessions, and report denominators and this fidelity covariate by cell.

## Changelog

| Version | Change |
|---|---|
| `cb-1.0` | Initial WP11 codebook: seeking, AI use, sharing (strict/legacy proactive), accuracy, confidence, timing, exposure, episodes, periods, evidence rates. |
| `cb-1.1` | BA-25: `in_answer` rederived as a same-crate, post-reference revision association anchored at the claim's delivered (rendered, when a render receipt exists) reference event; cross-crate revisions, wrong-crate citations and never-delivered claims can no longer mark a record in_answer. |
| `cb-1.2` | O-1..O-3: added `seeking.typed_teammate_requests` (nl_only typed intel asks routed to per-seat retrieval) and `automation.probes` / `automation.session_flagged` (identity-probe frequency and the flag-not-drop session marker). New missing rule `missing_if_no_events`. |
| `cb-1.3` | BA-75/BA-67: `automation.probes` definition corrected — every recognized probe is logged (probes bypass ordinary caps and the operational rate limit), and the event's `probed` vs scheduled `seats` marks budget-deferred disclosures; `probe_near_miss` on `message_accepted` documented for analysts. |
| `cb-1.4` | 2026-10-08 risk remediation: explicit teammate condition and realized delivered generated-reply share in participant_wide; denominator, missing audit linkage, scripted non-applicability and the operational 80% pause rule documented. |
| `cb-1.5` | 2026-10-11 instrument update: `post_task.tp_demo@v5` adds `tp_post_11` ("After the check-in, my teammates seemed more rushed.") after `tp_post_09`; the code-defined `open_ended` stage (`open_ended_followup` v1: `crate_strategy`, `source_reliance`) sits between `post_questionnaire` and `debrief` on TP variants. Manifest mapping refreshed for the current pair `tp_ai_v12` / `tp_scripted_v13`. |
| `cb-1.6` | 2026-10-12 BA-105: the `open_ended` follow-up pair is artifact-bound — `instruments.open_ended` → `open_ended.tp_followup@v1` (`crate_strategy`, `source_reliance`; required, 0-4000 chars, stage between `post_questionnaire` and `debrief`). Manifests without the pin have no `open_ended` stage; v10-era rows keep the legacy instrument id `open_ended_followup`. Current preview pair `tp_ai_v13` / `tp_scripted_v13` (scripted not fielded). |
| `cb-1.13` | 2026-10-17 v15 workload manipulation (internal eval, not yet fielded): `tp_ai_ovsome_thin_v4` relabels the check-in/remarks/post-task copy from time pressure to workload and pairs `high` ([6,7] both seats) with `mixed` ([2,6] — the seat carrying 6 is drawn per session on the `ratings` stream). `session.arm`/`allocation.arm` may therefore read `mixed`; measure names and `tp_post_*` ids are unchanged, and `post_task.tp_demo@v6` adds `tp_post_12` (differential-workload manipulation check) after `tp_post_11`. |
| `cb-1.7` | 2026-10-12 case-file telemetry: six new client kinds (`file_record_view`, `file_record_dwell`, `file_scroll_summary`, `file_reorder`, `file_order_final`, `share_attempt`) persist as `telemetry_<kind>` events; telemetry rows now carry a persist-time `active_ms` stamp. New measures: `attention.*`, `item.dwell_ms`, `file.*` session measures, per-crate `file_*` columns in crate_long, per-record `viewed_in_file`/`file_dwell_ms` in evidence_transfer. New missing rule `missing_when_no_file_telemetry` — pre-build sessions read NULL, never 0. |
| `cb-1.8` | 2026-10-13 audit remediation (INVESTIGATORS_TEAM_EXPERIMENT_CASE_FILE_TELEMETRY_PLAN_AUDIT.md, F01-F15): `file_meta` kind + `client_epoch` per page load join the vocabulary; views now require intersectionRatio >= 0.6 and suspend under hidden tabs/occluding check-in; scroll distance counts all movement and farthest_index is computed at emit; pagehide finalizes dwell/order via keepalive. Measures: attention is windowed to solo_review..answers_locked with carried/censored flags; item timing reports per-item pair completeness; scroll counters aggregate per epoch; share channel joins durable share rows (unknown channel kept); return latency splits occurrence/server/persist semantics; crate_long adds world-latest availability/view/stale columns; per-family *_state fields replace the single missingness test. Export wp11-export/3, metrics.wp11/2. |
| `cb-1.9` | 2026-10-15 crate-condition experiment (INVESTIGATORS_TEAM_EXPERIMENT_CRATE_OVERLAP_NUMBER_PLAN.md, locked decisions D1-D10): new manifests may pin `crate_overlap` (none|some|full vs legacy independent) and `participant_intel_budget` (thin case file). Every session world now persists `world_snapshot.content.world_params` {n_crates, n_assigned, crate_overlap, participant_intel_budget} — the immutable per-session condition record; analysts derive the crate-overlap and case-file cells from it, never from deployment flags. Crate counts 4/8/12 all export; denominators are unchanged (n_assigned stays 4, own-file/team-dependent stay 2/2). `world_params` on legacy-era sessions reads overlap=independent, n_crates=8. |
| `cb-1.10` | 2026-10-15 `accuracy.entered_count` — assigned crates carrying a frozen final answer (0 to n_assigned), the crate-completion count surfaced on Admin → Findings next to `accuracy.correct_total`. Motivated by the v13 zero-answer completer pattern: engaged sessions that froze an empty board are now visible at a glance rather than indistinguishable from all-wrong 0s. |
| `cb-1.11` | 2026-10-16 missingness-vs-value surfaces — the generalization of the entered_count fix. New scalars: `request.episodes` / `request.addressed` / `request.addressed_frac` / `request.answer_s_median` (the request_episode ledger — shares responsive to teammate asks vs unrequested shares), `confidence.rated_count` (ratings present, vs the silently conditioned `confidence.mean`), `answers.update_events` / `answers.first_answer_s` / `answers.last_answer_s` (answer-entry timing; last-second panic entry vs considered picks), `items.median_dwell_ms` / `items.dwell_lt1s_count` (per-item instrument dwell — flat-liner detection), `telemetry.reconnects` (client-epoch restarts — silent mid-session disruption), `chat.rendered_frac` (delivered→rendered coverage), `chat.scroll_events` (chat-pane engagement; new `chat_scroll` client emit — sessions bound before this build report ``unobservable``, never zero), `open_ended.total_chars` / `open_ended.min_item_chars` (free-text volume floor). |
