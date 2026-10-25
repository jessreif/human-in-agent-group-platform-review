# Data dictionary

Table names below are the ``Table(...)`` calls in ``backend/app/db/schema.py``, extracted by a one-off read on 2026-10-21. Count: 49. Column definitions stay in that file and are not retyped here.

Event payloads are the ``type`` strings stored on the event table. Export headers are the CSV writers in ``backend/app/export/`` and ``backend/app/measurement/``.

Active time is the task clock (``backend/app/services/activetime.py``). Wall-clock time is used for abandonment and for the check-in technical stop. The Phase 1 reading of those limits was about 30 minutes idle and about 120 seconds for an incomplete check-in. Those two figures were not re-measured in this pass. **Described only.**

## Tables

- `experiment_manifest`
- `batch`
- `batch_group`
- `batch_group_member`
- `participant`
- `session`
- `participant_display`
- `platform_link`
- `session_credential`
- `allocation`
- `consent_event`
- `stage_transition`
- `clock_interval`
- `draw_log`
- `world_snapshot`
- `record`
- `seat_holding`
- `file_membership`
- `command_receipt`
- `event`
- `outbox`
- `scheduled_job`
- `message`
- `request`
- `request_episode`
- `share`
- `delivery_receipt`
- `knowledge_snapshot`
- `agent_state`
- `claim`
- `claim_source`
- `model_call`
- `displayed_output`
- `answer_revision`
- `answer_final`
- `confidence_response`
- `manipulation_exposure`
- `item_response`
- `result`
- `payment_entitlement`
- `completion_receipt`
- `data_use_choice`
- `incident_interval`
- `qa_exchange`
- `admin_audit`
- `redaction_review`
- `coding_run`
- `coding_result`
- `validation_gate`
