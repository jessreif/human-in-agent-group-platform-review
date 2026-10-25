"""Exact-event metrics (INV-1102; spec §9.1 canonical measures).

Every count here is derived from committed records only:

- command counts come from ``command_receipt`` — one receipt per ``command_id`` by
  constraint, so HTTP retries/idempotent re-submissions can never inflate counts;
- event counts come from ``event`` — one row per ``(session_id, seq)``;
- domain counts come from the committed domain rows (``request``, ``share``,
  ``message``, ``answer_revision``/``answer_final``, ``confidence_response``,
  ``item_response``, ``manipulation_exposure``);
- missing answers are a distinct state, never coerced to 0;
- a zero denominator produces ``MeasureState.UNDEFINED_DENOMINATOR`` — an explicit
  undefined result, not 0 and not an exception (seeking allocation, spec §9.1).

"Accepted" means an accepted ``command_receipt`` row; the event log and the receipts
agree because events are written inside the same command transaction. Counting receipts
is therefore identical to counting accepted-command events deduplicated by
``command_id``.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import Connection

from app.measurement.results import (
    Measure,
    count_measure,
    optional_measure,
    ratio_measure,
)
from app.measurement.rows import SessionRows, load_session_rows
from app.measurement.telemetry import (
    attention_measures,
    file_telemetry_measures,
    item_dwell_ms,
    item_timing,
)

# Event types written by accepted participant commands (app/services/lifecycle.py).
EVENT_TEAMMATE_REQUEST = "teammate_requested"
EVENT_ASSISTANT_REQUEST = "assistant_requested"
EVENT_SOCIAL_REPLY = "message_accepted"  # participant free-text to team channel
EVENT_SHARE = "record_shared"
EVENT_ANSWER = "answer_updated"

SEEKING_COMMANDS = ("teammate_request", "social_reply", "assistant_request")
SHARING_COMMANDS = ("record_share",)
ACTION_COMMANDS = (*SEEKING_COMMANDS, *SHARING_COMMANDS, "answer_update")

# Route taxonomy for AI use (spec §9.1): the picker commits an ``assistant_request``
# whose payload carries a crate but no free-text body; a typed prompt carries ``body``.
Route = Literal["typed", "picker"]


def _accepted_kinds(rows: SessionRows) -> dict[str, int]:
    """accepted receipts per command kind — deduped by command_id (unique constraint)."""
    counts: dict[str, int] = {}
    seen: set[tuple[str, Any]] = set()
    for r in rows.command_receipts:
        key = (str(r["kind"]), r["command_id"])
        if key in seen or r["outcome"] != "accepted":
            continue
        seen.add(key)
        counts[str(r["kind"])] = counts.get(str(r["kind"]), 0) + 1
    return counts


def command_counts(rows: SessionRows) -> dict[str, dict[str, int]]:
    """Per-kind ``accepted``/``rejected`` command counts from ``command_receipt``.

    Retries of the same ``command_id`` share one receipt, so they can never inflate
    counts; rejected commands are still durably receipted and counted separately.
    """
    counts: dict[str, dict[str, int]] = {}
    seen: set[Any] = set()
    for r in rows.command_receipts:
        if r["command_id"] in seen:
            continue  # command identity dedup: a retried command is one command
        seen.add(r["command_id"])
        kind = str(r["kind"])
        outcome = str(r["outcome"])
        bucket = counts.setdefault(kind, {"accepted": 0, "rejected": 0})
        bucket[outcome] = bucket.get(outcome, 0) + 1
    return counts


def _events_of(rows: SessionRows, type_: str) -> list[Mapping[str, Any]]:
    return [e for e in rows.events if e["type"] == type_]


# ----- seeking ----------------------------------------------------------------------------


@dataclass(frozen=True)
class SeekingMetrics:
    structured_teammate_requests: Measure
    structured_requests_unique_crates: Measure
    structured_requests_delivered_replies: Measure
    free_text_teammate_messages: Measure
    total_structured_seeking: Measure

    def as_dict(self) -> dict[str, Any]:
        return {k: v.as_dict() for k, v in self.__dict__.items()}


def seeking_metrics(rows: SessionRows) -> SeekingMetrics:
    """Structured seeking = accepted participant ``teammate_request`` commands; free-text
    messages to teammates are counted separately (task-relevant questions among them are
    an offline coding product, not an exact measure — spec §9.1). Agent-originated
    requests are excluded by ``requester = 'participant'``."""
    kinds = _accepted_kinds(rows)
    structured = kinds.get("teammate_request", 0)
    free_text = kinds.get("social_reply", 0)
    participant_structured = [
        r for r in rows.requests if r["requester"] == "participant" and r["kind"] == "structured"
    ]
    episode_by_request = {e["request_id"]: e for e in rows.request_episodes}
    delivered = sum(
        1
        for r in participant_structured
        if (ep := episode_by_request.get(r["request_id"])) is not None
        and ep["substantive_event_seq"] is not None
    )
    return SeekingMetrics(
        structured_teammate_requests=count_measure(
            "seeking.structured_teammate_requests",
            structured,
            unit="accepted_commands",
            missing_rule="missing_if_no_receipts",
        ),
        structured_requests_unique_crates=count_measure(
            "seeking.structured_requests_unique_crates",
            len({int(r["crate"]) for r in participant_structured if r["crate"] is not None}),
            unit="crates",
            missing_rule="missing_if_no_receipts",
        ),
        structured_requests_delivered_replies=count_measure(
            "seeking.structured_requests_delivered_replies",
            delivered,
            unit="requests",
            missing_rule="missing_if_no_receipts",
        ),
        free_text_teammate_messages=count_measure(
            "seeking.free_text_teammate_messages",
            free_text,
            unit="accepted_commands",
            missing_rule="missing_if_no_receipts",
        ),
        total_structured_seeking=count_measure(
            "seeking.total_structured_seeking",
            structured + free_text,
            unit="accepted_commands",
            missing_rule="missing_if_no_receipts",
        ),
    )


# ----- AI use ------------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptOutcome:
    """Per-prompt disposition for one accepted ``assistant_request``."""

    request_id: str
    crate: int | None
    route: Route
    episode_status: str
    delivered: bool
    fallback: str | None  # displayed_output.fallback for the delivered reply, if any
    model_call_status: str | None  # latest model_call status bound to the reply


def _prompt_route(request_event: Mapping[str, Any] | None) -> Route:
    """``typed`` when the participant supplied free text; ``picker`` when the prompt came
    from the crate picker (crate set, body absent)."""
    body = None
    if request_event is not None:
        body = (request_event.get("payload") or {}).get("body")
    return "typed" if isinstance(body, str) and body.strip() else "picker"


def assistant_use(rows: SessionRows) -> dict[str, Any]:
    """AI use (spec §9.1): accepted participant ``assistant_request`` commands only —
    retries cannot add prompts (one receipt per ``command_id``). Each prompt is reported
    with its route (``typed``/``picker``), target crate and response/fallback/delivery
    disposition."""
    kinds = _accepted_kinds(rows)
    episode_by_request = {e["request_id"]: e for e in rows.request_episodes}
    # The command's canonical event is ``assistant_requested`` — a command can stamp
    # several events (the accepted request also emits the visible participant message),
    # so the route derivation must not read a sibling's payload.
    event_by_command = {
        e["command_id"]: e
        for e in rows.events
        if e.get("command_id") and e["type"] == "assistant_requested"
    }
    prompts = [
        r
        for r in rows.requests
        if r["requester"] == "participant" and r["responder"] == "assistant"
    ]
    message_by_request: dict[Any, list[Mapping[str, Any]]] = {}
    for m in rows.messages:
        if m.get("request_id") is not None and m["channel"] == "assistant":
            message_by_request.setdefault(m["request_id"], []).append(m)
    output_by_message = {o["message_id"]: o for o in rows.displayed_outputs}
    call_status = {c["call_id"]: str(c["status"]) for c in rows.model_calls}

    outcomes: list[PromptOutcome] = []
    by_route: dict[str, int] = {"typed": 0, "picker": 0}
    by_crate: dict[int, int] = {}
    for req in sorted(prompts, key=lambda r: int(r["accepted_seq"])):
        ev = event_by_command.get(req["command_id"])
        route = _prompt_route(ev)
        by_route[route] += 1
        if req["crate"] is not None:
            crate = int(req["crate"])
            by_crate[crate] = by_crate.get(crate, 0) + 1
        ep = episode_by_request.get(req["request_id"])
        # Only the responder's messages count toward delivery: the participant's own
        # ask is now also a request-linked delivered message in the channel.
        delivered_msgs = [
            m
            for m in message_by_request.get(req["request_id"], [])
            if m["status"] == "delivered" and m["actor"] == "assistant"
        ]
        out = next(
            (
                output_by_message[m["message_id"]]
                for m in delivered_msgs
                if m["message_id"] in output_by_message
            ),
            None,
        )
        outcomes.append(
            PromptOutcome(
                request_id=str(req["request_id"]),
                crate=None if req["crate"] is None else int(req["crate"]),
                route=route,
                episode_status="none" if ep is None else str(ep["status"]),
                delivered=bool(delivered_msgs),
                fallback=None if out is None else str(out["fallback"]),
                model_call_status=None
                if out is None or out["call_id"] is None
                else call_status.get(out["call_id"]),
            )
        )
    return {
        "prompts": count_measure(
            "ai_use.prompts",
            kinds.get("assistant_request", 0),
            unit="accepted_commands",
            missing_rule="missing_if_no_receipts",
        ),
        "by_route": by_route,
        "by_crate": dict(sorted(by_crate.items())),
        "delivered": count_measure(
            "ai_use.delivered",
            sum(1 for o in outcomes if o.delivered),
            unit="requests",
            missing_rule="missing_if_no_receipts",
        ),
        "per_prompt": outcomes,
    }


# ----- sharing (incl. proactive) ------------------------------------------------------------


def _assigned_set(rows: SessionRows) -> set[int]:
    return set(rows.assigned_crates)


def _agent_request_announce_seqs(rows: SessionRows) -> dict[int, int]:
    """crate -> earliest committed seq at which an agent→participant *request* for that
    crate was actually delivered to the participant (a delivered message bound to the
    request row). Requests never delivered do not remove proactive eligibility."""
    first: dict[int, int] = {}
    delivered_req_msgs = {
        m["request_id"]: int(m["committed_seq"])
        for m in rows.messages
        if m.get("request_id") is not None
        and m["status"] == "delivered"
        and m["committed_seq"] is not None
    }
    for r in rows.requests:
        if r["responder"] != "participant" or r["crate"] is None:
            continue
        seq = delivered_req_msgs.get(r["request_id"])
        if seq is None:
            continue
        crate = int(r["crate"])
        first[crate] = min(first.get(crate, seq), seq)
    return first


def _solicitation_seqs(rows: SessionRows) -> dict[int, int]:
    """crate -> earliest committed seq of *any* delivered agent solicitation for that
    crate: request announcements plus crosstalk/social replies chained to them
    (spec §9.1: crosstalk/social solicitation removes proactive eligibility)."""
    first = _agent_request_announce_seqs(rows)
    request_msg_ids = {
        m["message_id"]
        for m in rows.messages
        if m.get("request_id") is not None and m["status"] == "delivered"
    }
    # Crosstalk/social solicitation: delivered seat messages replying to a request
    # announcement for the same crate (the PE no_news nudge, spec §2.2).
    for m in rows.messages:
        if (
            m["status"] == "delivered"
            and m["actor"] in ("JT", "PM")
            and m.get("parent_message_id") in request_msg_ids
            and m.get("crate") is not None
            and m["committed_seq"] is not None
        ):
            crate = int(m["crate"])
            first[crate] = min(first.get(crate, int(m["committed_seq"])), int(m["committed_seq"]))
    return first


@dataclass(frozen=True)
class SharingMetrics:
    participant_share_events: Measure
    unique_records_shared: Measure
    prompted_shares: Measure
    unprompted_shares: Measure
    own_crate_shares: Measure
    unassigned_crate_shares: Measure
    proactive_shares_strict: Measure
    proactive_shares_legacy: Measure
    per_share: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            **{k: v.as_dict() for k, v in self.__dict__.items() if isinstance(v, Measure)},
            "per_share": [dict(s) for s in self.per_share],
        }


def sharing_metrics(rows: SessionRows) -> SharingMetrics:
    """Participant-initiated shares (``share`` rows, ``sharer='participant'`` — one row per
    committed share; retries cannot duplicate). Prompted = linked to a delivered agent
    request; own/unassigned = record crate in/out of the participant's assigned set.

    Proactive sharing, two declared definitions (spec §9.1):
    - ``strict``: share of an unassigned-crate record committed *before* the earliest
      delivered agent solicitation for that crate (request announcements plus chained
      crosstalk/social solicitation).
    - ``legacy`` (S2/S3 demo rule): unprompted (``prompted_by_request_id IS NULL``) share
      of a record whose crate is unassigned and has no delivered scripted teammate
      request yet — the demo's ``!ctx && !isMine && !requestedCrates`` rule.
    """
    assigned = _assigned_set(rows)
    record_crate = {str(r["record_id"]): int(r["crate"]) for r in rows.records}
    announced = _agent_request_announce_seqs(rows)
    solicited = _solicitation_seqs(rows)
    mine = [s for s in rows.shares if s["sharer"] == "participant"]

    per_share: list[dict[str, Any]] = []
    n_prompted = n_own = n_strict = n_legacy = 0
    for share in sorted(mine, key=lambda x: int(x["delivered_seq"])):
        crate = record_crate.get(str(share["record_id"]))
        prompted = share["prompted_by_request_id"] is not None
        seq = int(share["delivered_seq"])
        unassigned = crate is not None and crate not in assigned
        first_solicited = solicited.get(crate) if crate is not None else None
        first_announced = announced.get(crate) if crate is not None else None
        strict = unassigned and (first_solicited is None or seq < first_solicited)
        legacy = unassigned and not prompted and (first_announced is None or seq < first_announced)
        n_prompted += prompted
        n_own += 0 if unassigned or crate is None else 1
        n_strict += strict
        n_legacy += legacy
        per_share.append(
            {
                "share_id": str(share["share_id"]),
                "record_id": str(share["record_id"]),
                "crate": crate,
                "delivered_seq": seq,
                "prompted": prompted,
                "assigned_crate": None if crate is None else not unassigned,
                "proactive_strict": strict,
                "proactive_legacy": legacy,
            }
        )
    n_mine = len(mine)
    return SharingMetrics(
        participant_share_events=count_measure(
            "sharing.participant_share_events",
            n_mine,
            unit="share_events",
            missing_rule="missing_if_no_shares",
        ),
        unique_records_shared=count_measure(
            "sharing.unique_records_shared",
            len({str(s["record_id"]) for s in mine}),
            unit="records",
            missing_rule="missing_if_no_shares",
        ),
        prompted_shares=count_measure(
            "sharing.prompted_shares",
            n_prompted,
            unit="share_events",
            missing_rule="missing_if_no_shares",
        ),
        unprompted_shares=count_measure(
            "sharing.unprompted_shares",
            n_mine - n_prompted,
            unit="share_events",
            missing_rule="missing_if_no_shares",
        ),
        own_crate_shares=count_measure(
            "sharing.own_crate_shares",
            n_own,
            unit="share_events",
            missing_rule="missing_if_no_shares",
        ),
        unassigned_crate_shares=count_measure(
            "sharing.unassigned_crate_shares",
            sum(1 for s in per_share if s["assigned_crate"] is False),
            unit="share_events",
            missing_rule="missing_if_no_shares",
        ),
        proactive_shares_strict=count_measure(
            "sharing.proactive_shares_strict",
            n_strict,
            unit="share_events",
            missing_rule="missing_if_no_shares",
        ),
        proactive_shares_legacy=count_measure(
            "sharing.proactive_shares_legacy",
            n_legacy,
            unit="share_events",
            missing_rule="missing_if_no_shares",
        ),
        per_share=tuple(per_share),
    )


# ----- allocation, timing, answers, confidence, checks ---------------------------------------


def seeking_allocation(rows: SessionRows) -> Measure:
    """AI share of structured-channel seeking (spec §9.1):
    ``ai_prompts / (ai_prompts + structured teammate requests)``.
    Explicitly ``undefined_denominator`` when the participant never used either channel —
    a label, not 0."""
    kinds = _accepted_kinds(rows)
    ai = kinds.get("assistant_request", 0)
    structured = kinds.get("teammate_request", 0)
    return ratio_measure(
        "seeking.allocation_ai_share",
        ai,
        ai + structured,
        denominator_kind="accepted_commands",
        missing_rule="undefined_when_no_seeking",
    )


def _first_active_ms(rows: SessionRows, types: tuple[str, ...]) -> int | None:
    """Earliest ``active_ms`` among committed events of the given types (None = missing,
    never 0)."""
    stamps = [
        int(e["active_ms"])
        for e in rows.events
        if e["type"] in types and e.get("active_ms") is not None
    ]
    return min(stamps) if stamps else None


def _first_share_active_ms(rows: SessionRows) -> int | None:
    """Earliest participant share on the active axis.

    ``record_shared`` events predate the ``active_ms`` event stamp, so the share's
    committed ``delivered_at`` wall time is converted through the clock ledger
    (``periods.active_ms_at_wall``). A share whose wall time falls outside every
    committed interval contributes nothing — never a fabricated 0."""
    from app.measurement.periods import active_ms_at_wall

    stamps = [
        int(e["active_ms"])
        for e in rows.events
        if e["type"] == EVENT_SHARE and e.get("active_ms") is not None
    ]
    stamps.extend(
        ms
        for sh in rows.shares
        if sh["sharer"] == "participant"
        and (ms := active_ms_at_wall(rows, sh["delivered_at"])) is not None
    )
    return min(stamps) if stamps else None


def first_action_times(rows: SessionRows) -> dict[str, Measure]:
    """Active-time latencies (ms) of first accepted seeking/sharing actions.

    - ``first_teammate_request_ms`` / ``first_assistant_prompt_ms`` / ``first_share_ms``:
      channel-specific firsts.
    - ``first_request_ms``: true request-only latency (structured + assistant).
    - ``first_request_or_share_ms``: the legacy ``inv_first_request_sec`` semantics —
      either a request or a share sets it (spec §9.2 keeps the renamed value explicit).
    Missing actions stay ``missing_data``; censoring at task end is expressed by the
    period measures (INV-1105), not by clamping these to the deadline.
    """
    first_tm = _first_active_ms(rows, (EVENT_TEAMMATE_REQUEST,))
    first_ai = _first_active_ms(rows, (EVENT_ASSISTANT_REQUEST,))
    first_share = _first_share_active_ms(rows)
    request_firsts = [v for v in (first_tm, first_ai) if v is not None]
    action_firsts = [v for v in (*request_firsts, first_share) if v is not None]
    rule = "missing_when_never_performed"
    return {
        "first_teammate_request_ms": optional_measure(
            "timing.first_teammate_request_ms", first_tm, missing_rule=rule, unit="active_ms"
        ),
        "first_assistant_prompt_ms": optional_measure(
            "timing.first_assistant_prompt_ms", first_ai, missing_rule=rule, unit="active_ms"
        ),
        "first_request_ms": optional_measure(
            "timing.first_request_ms",
            min(request_firsts) if request_firsts else None,
            missing_rule=rule,
            unit="active_ms",
        ),
        "first_share_ms": optional_measure(
            "timing.first_share_ms", first_share, missing_rule=rule, unit="active_ms"
        ),
        "first_request_or_share_ms": optional_measure(
            "timing.first_request_or_share_ms",
            min(action_firsts) if action_firsts else None,
            missing_rule=rule,
            unit="active_ms",
        ),
    }


def answer_states(rows: SessionRows) -> dict[str, Any]:
    """Answer coverage per assigned crate: ``answered`` vs ``missing`` are distinct
    states (spec §9.1); a crate with revisions but no frozen final is ``unfrozen``
    (abandoned/withdrawn before the deadline)."""
    finals = {int(f["crate"]): f for f in rows.answer_finals}
    revised = {int(r["crate"]) for r in rows.answer_revisions}
    per_crate: dict[int, str] = {}
    for crate in sorted(rows.truth):
        if crate in finals:
            per_crate[crate] = "answered"
        elif crate in revised:
            per_crate[crate] = "unfrozen"
        else:
            per_crate[crate] = "missing"
    assigned = rows.assigned_crates
    return {
        "per_crate": per_crate,
        "answered": sum(1 for c in assigned if per_crate[c] == "answered"),
        "missing": sum(1 for c in assigned if per_crate[c] == "missing"),
        "unfrozen": sum(1 for c in assigned if per_crate[c] == "unfrozen"),
    }


def confidence(rows: SessionRows) -> dict[int, dict[str, Any]]:
    """Latest committed ``confidence_response`` per crate (spec §9.1): 0-100 only when
    ``status='answered'``; skipped/missing stay distinct — the slider's visual default is
    never data."""
    latest: dict[int, Mapping[str, Any]] = {}
    for r in rows.confidence_responses:
        latest[int(r["crate"])] = r  # rows are ordered by (crate, revision)
    return {
        crate: {
            "status": str(r["status"]),
            "value": None if r["value"] is None else int(r["value"]),
            "revision": int(r["revision"]),
            "instrument_version": int(r["instrument_version"]),
        }
        for crate, r in sorted(latest.items())
    }


def manipulation_checks(rows: SessionRows) -> dict[str, Any]:
    """Manipulation-check surfaces (spec §9.1): exposure receipts and post-task item
    responses as committed — distinct constructs, no composite score.

    ``pressure_checkin.own_rating`` is the participant's *own* rating collected before
    teammates' values are revealed: it is a pre-reveal manipulation input, never a
    post-exposure effect measure (spec §9.1, INV-1105)."""
    exposures = [
        {
            "exposure_id": str(x["exposure_id"]),
            "kind": str(x["kind"]),
            "status": str(x["status"]),
            "artifact_id": x["artifact_id"],
            "artifact_version": x["artifact_version"],
            "committed_seq": x["committed_seq"],
            "rendered_seq": x["rendered_seq"],
            "acknowledged_seq": x["acknowledged_seq"],
            "own_rating": x["own_rating"],
            "assigned_values": x["assigned_values"],
            "exposed": x["committed_seq"] is not None,
            "own_rating_role": "pre_reveal_manipulation_input"
            if x["kind"] == "pressure_checkin"
            else None,
        }
        for x in rows.manipulation_exposures
    ]
    items = [
        {
            "instrument_id": str(r["instrument_id"]),
            "item_id": str(r["item_id"]),
            "status": str(r["status"]),
            "response": r["response"],
            "missing_reason": r["missing_reason"],
            "instrument_version": int(r["instrument_version"]),
            "first_seq": int(r["first_seq"]),
            "last_seq": int(r["last_seq"]),
        }
        for r in rows.item_responses
    ]
    return {"exposures": exposures, "item_responses": items}


def automation_metrics(rows: SessionRows) -> dict[str, Any]:
    """O-3 (2026-10-03): how often the participant asked whether teammates are
    human. ``probes`` counts ``automation_probe`` events; ``flagged`` marks the
    session so analysis can drop or sensitivity-check it — the exchange is
    retained, never hidden."""
    probes = _events_of(rows, "automation_probe")
    return {
        "probes": count_measure(
            "automation.probes",
            len(probes),
            unit="events",
            missing_rule="missing_if_no_events",
        ),
        "flagged": count_measure(
            "automation.session_flagged",
            1 if probes else 0,
            unit="sessions",
            missing_rule="missing_if_no_events",
        ),
    }


def typed_seeking_metrics(rows: SessionRows) -> dict[str, Any]:
    """O-1/O-2: typed intel asks in ``nl_only`` produce ``teammate_requested``
    events tagged ``via: typed`` — the natural-language counterpart of the
    picker's structured requests."""
    typed = [
        e
        for e in _events_of(rows, EVENT_TEAMMATE_REQUEST)
        if isinstance(e.get("payload"), Mapping) and e["payload"].get("via") == "typed"
    ]
    return {
        "typed_teammate_requests": count_measure(
            "seeking.typed_teammate_requests",
            len(typed),
            unit="events",
            missing_rule="missing_if_no_events",
        ),
    }


def telemetry_metrics(rows: SessionRows) -> dict[str, Any]:
    """Case-file + attention telemetry measures (2026-10-12 build).

    ``file_telemetry_state = 'missing_data'`` marks sessions whose events carry
    no case-file telemetry at all — pre-build cohorts and dead queues are
    indistinguishable and both stay missing (never zeroed)."""
    events = list(rows.events)
    timing = item_timing(events)
    return {
        **attention_measures(events),
        "item_dwell_ms": item_dwell_ms(events),
        "item_timing": timing,
        "item_timing_partial": sorted(k for k, v in timing.items() if v["partial"]),
        **file_telemetry_measures(
            events,
            file_size=len(rows.file_memberships) or None,
            added_from_team=[f for f in rows.file_memberships if f["source"] == "added_from_team"],
            participant_shares=[r for r in rows.shares if r["sharer"] == "participant"],
            teammate_shares=[r for r in rows.shares if r["sharer"] != "participant"],
            event_active_ms=rows.event_active_ms,
        ),
    }


def session_metrics(rows: SessionRows) -> dict[str, Any]:
    """The full INV-1102 metric bundle for one session (deterministic ordering)."""
    return {
        "command_counts": command_counts(rows),
        "seeking": {**seeking_metrics(rows).as_dict(), **typed_seeking_metrics(rows)},
        "automation": automation_metrics(rows),
        "ai_use": assistant_use(rows),
        "sharing": sharing_metrics(rows).as_dict(),
        "seeking_allocation": seeking_allocation(rows).as_dict(),
        "first_action_times": {k: v.as_dict() for k, v in first_action_times(rows).items()},
        "answers": answer_states(rows),
        "confidence": confidence(rows),
        "manipulation_checks": manipulation_checks(rows),
        "telemetry": telemetry_metrics(rows),
    }


def session_metrics_for(conn: Connection, session_id: uuid.UUID) -> dict[str, Any]:
    """Connection-level convenience wrapper: load committed rows, then pure compute."""
    return session_metrics(load_session_rows(conn, session_id))
