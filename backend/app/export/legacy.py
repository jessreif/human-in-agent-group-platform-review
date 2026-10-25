"""INV_FINISH legacy-field reconciliation (INV-1107, spec §9.2).

The two reference demos write ``inv_*`` embedded-data fields through ``INV_FINISH``
(``tests/fixtures/reference/demo_constants.json`` -> ``<study>.inv_finish``; 39 fields for
the time-pressure study, 32 for peer-evaluation). This module is the executable half of
``docs/INV_FINISH_FIELD_RECONCILIATION.md``: every legacy field maps to an export source or
is explicitly unavailable, and nothing invents a legacy definition.

Critical correction recorded here: in both demos ``counters.first`` is set by *either* a
request *or* a share, yet was exported as ``inv_first_request_sec``. The server export
therefore separates ``first_teammate_request_ms`` (request-only, observed) from
``legacy_first_action_sec`` (the legacy-compatible first-of-either value). A consumer
wanting the old semantics uses the latter; the old field name is never emitted.
"""

from __future__ import annotations

from dataclasses import dataclass

# Reconciliation statuses.
EXPORTED = "exported"  # present in the research export under the same name or a named column
DERIVED = "derived"  # reconstructed in a long table rather than a single wide cell
RENAMED = "renamed"  # legacy semantics exported under a corrected, clearly labeled name
EXCLUDED = "excluded"  # deliberately never exported (identity/redaction boundary)
UNAVAILABLE = "unavailable"  # client-side telemetry the server does not persist


@dataclass(frozen=True)
class FieldMapping:
    status: str
    source: str  # export table.column, or a short reason for non-exported fields
    note: str = ""


_TELEMETRY_NOTE = (
    "demo client-side telemetry; the server stores no equivalent committed record "
    "(bounded telemetry is not authoritative, spec §5 crosswalk)"
)

_MAPPINGS: dict[str, FieldMapping] = {
    "inv_condition": FieldMapping(
        EXPORTED,
        "participant_wide.inv_condition",
        "arm label; study_key/version stamps in the same row remove the ambiguity of "
        "bare condition names (spec §9.2)",
    ),
    "inv_initials": FieldMapping(
        EXCLUDED,
        "never stored server-side",
        "omitted from all shared exports per spec §9.2/INV-1109 (DEF-10 replacement: "
        "explicit display-name policy, not initials)",
    ),
    "inv_team_requests": FieldMapping(
        EXPORTED,
        "participant_wide.inv_team_requests",
        "accepted teammate_request commands; retries do not inflate the count",
    ),
    "inv_first_request_sec": FieldMapping(
        RENAMED,
        "participant_wide.legacy_first_action_sec",
        "LEGACY FIELD IS MISLABELED: counters.first fires on the first request OR share, "
        "so the value is a first-*action* latency. The true request-only latency is "
        "participant_wide.first_teammate_request_ms",
    ),
    "inv_shares": FieldMapping(
        EXPORTED, "participant_wide.inv_shares", "participant share rows delivered"
    ),
    "inv_proactive_shares": FieldMapping(
        EXPORTED,
        "participant_wide.inv_proactive_shares",
        "strict definition: share of a crate's record before any accepted agent request "
        "for that crate (spec §9.1); the demo rule differed and is not reproduced",
    ),
    "inv_replies": FieldMapping(
        EXPORTED,
        "participant_wide.inv_replies",
        "delivered JT/PM messages linked to a participant request",
    ),
    "inv_prompts_used": FieldMapping(
        EXPORTED,
        "participant_wide.inv_prompts_used",
        "accepted assistant_request commands; one per prompt regardless of retries",
    ),
    "inv_checkin_at": FieldMapping(
        DERIVED,
        "exposure(pressure_checkin).committed_seq / participant_wide.checkin_committed_seq",
        "observed commit seq; scheduled value kept separately as "
        "checkin_scheduled_active_ms (configured provenance)",
    ),
    "inv_own_pressure": FieldMapping(
        EXPORTED,
        "participant_wide.inv_own_pressure",
        "manipulation_exposure.own_rating; pre-reveal, never a post-manipulation measure",
    ),
    "inv_pressure_JT": FieldMapping(
        EXPORTED,
        "participant_wide.inv_pressure_JT",
        "manipulation_exposure.assigned_values.JT (revealed value)",
    ),
    "inv_pressure_PM": FieldMapping(
        EXPORTED,
        "participant_wide.inv_pressure_PM",
        "manipulation_exposure.assigned_values.PM (revealed value)",
    ),
    "inv_requests_pre_checkin": FieldMapping(
        EXPORTED,
        "participant_wide.inv_requests_pre_checkin",
        "accepted teammate_request commands committed before the check-in exposure",
    ),
    "inv_prompts_pre_checkin": FieldMapping(
        EXPORTED,
        "participant_wide.inv_prompts_pre_checkin",
        "",
    ),
    "inv_shares_pre_checkin": FieldMapping(
        EXPORTED,
        "participant_wide.shares_pre_checkin",
        "TP had no shares-post counterpart; symmetric pre/post share counts are exported "
        "under the corrected name (shares_pre_checkin / shares_post_checkin)",
    ),
    "inv_requests_post_checkin": FieldMapping(
        EXPORTED,
        "participant_wide.inv_requests_post_checkin",
        "",
    ),
    "inv_prompts_post_checkin": FieldMapping(
        EXPORTED,
        "participant_wide.inv_prompts_post_checkin",
        "",
    ),
    "inv_peer_rating_JT": FieldMapping(
        EXPORTED,
        "participant_wide.inv_peer_rating_JT",
        "peer_ratings instrument item for seat JT (skippable per D06/DEF-11)",
    ),
    "inv_peer_rating_PM": FieldMapping(
        EXPORTED,
        "participant_wide.inv_peer_rating_PM",
        "",
    ),
    "inv_correct_total": FieldMapping(EXPORTED, "participant_wide.inv_correct_total", ""),
    "inv_correct_teamdep": FieldMapping(EXPORTED, "participant_wide.inv_correct_teamdep", ""),
    "inv_correct_ownfile": FieldMapping(EXPORTED, "participant_wide.inv_correct_ownfile", ""),
    "inv_assignment": FieldMapping(
        DERIVED, "crate_long.assigned", "per-crate assigned flag; wide list derivable"
    ),
    "inv_roles": FieldMapping(
        DERIVED, "crate_long.role", "per-crate role (own/team-dependent/unassigned)"
    ),
    "inv_answers": FieldMapping(
        DERIVED,
        "crate_long.final_answer + answer_revision",
        "final answer per crate plus full revision history; missing stays missing",
    ),
    "inv_confidence": FieldMapping(
        DERIVED,
        "crate_long.confidence_value/confidence_status",
        "explicit 0-100 per answered crate with skipped/missing status (spec §9.1)",
    ),
    "inv_first_action_sec": FieldMapping(
        EXPORTED,
        "participant_wide.legacy_first_action_sec",
        "same value the demo wrote for both first-request and first-action fields",
    ),
    "inv_window_blurs": FieldMapping(UNAVAILABLE, _TELEMETRY_NOTE),
    "inv_seconds_away": FieldMapping(UNAVAILABLE, _TELEMETRY_NOTE),
    "inv_idle_gaps": FieldMapping(UNAVAILABLE, _TELEMETRY_NOTE),
    "inv_max_idle_gap": FieldMapping(UNAVAILABLE, _TELEMETRY_NOTE),
    "inv_clicks_case": FieldMapping(UNAVAILABLE, _TELEMETRY_NOTE),
    "inv_clicks_team": FieldMapping(UNAVAILABLE, _TELEMETRY_NOTE),
    "inv_clicks_ai": FieldMapping(UNAVAILABLE, _TELEMETRY_NOTE),
    "inv_case_scroll_pct": FieldMapping(UNAVAILABLE, _TELEMETRY_NOTE),
    "inv_share_menu_opens": FieldMapping(UNAVAILABLE, _TELEMETRY_NOTE),
    "inv_reply_opens": FieldMapping(UNAVAILABLE, _TELEMETRY_NOTE),
    "inv_entries_added": FieldMapping(
        EXPORTED,
        "participant_wide.inv_entries_added",
        "file_membership rows with source=added_from_team",
    ),
    "inv_events": FieldMapping(
        EXPORTED,
        "participant_wide.inv_events",
        "committed event count (server log is authoritative; demo counted client log)",
    ),
    "inv_teammate_replies": FieldMapping(
        DERIVED,
        "message(actor=JT|PM, status=delivered)",
        "per-message rows; wide count derivable; includes non-request replies unlike inv_replies",
    ),
    "inv_teammate_reply_fallbacks": FieldMapping(
        DERIVED,
        "model_call/fidelity_rate",
        "model_call status=fallback (and displayed_output.fallback != 'none'); the demo "
        "flagged a fallback field before validation ran (DEF-05), so the honest source "
        "is the attempt/outcome records",
    ),
}

STUDY_KEYS = {"time_pressure": "tp_demo_v1", "peer_evaluation": "pe_demo_v1"}
SCORESHEET_FIELD = "inv_scoresheet"


def mapping_for(field: str) -> FieldMapping | None:
    return _MAPPINGS.get(field)


def reconcile(fields: list[str]) -> dict[str, FieldMapping]:
    """Every legacy field -> its mapping; unknown fields raise (an unaudited legacy field
    must never silently pass — spec §9.2)."""
    out: dict[str, FieldMapping] = {}
    for field in fields:
        mapping = mapping_for(field)
        if mapping is None:
            raise KeyError(f"unmapped legacy field {field!r} — extend _MAPPINGS first")
        out[field] = mapping
    return out
