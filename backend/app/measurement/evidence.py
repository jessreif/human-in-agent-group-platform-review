"""Evidence-delivery, fulfillment, redundancy and correction measures (INV-1103;
spec §9.3 process measures with declared denominators).

All measures declare their denominator explicitly — crates, requests, records or coded
assertions — and carry the censoring rule in ``missing_rule``. "Before closure" means
before the committed ``task_deadline`` event; when the task never closed (abandoned,
in-progress), the observation horizon is the last committed event and the measure is
reported as *censored*, not zero.

Evidence-transfer stages reuse ``app.services.claims.transfer_ledger`` (the committed
ledger chain world→held→eligible→selected→delivered→rendered plus the file/context/
reference/answer branch); the ``in_answer`` stage is derived here by joining
``claim_source`` lineages to *same-crate* answer revisions committed after the citing
claim's delivered/rendered reference event (BA-25).
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from sqlalchemy import Connection

from app.measurement.results import count_measure, ratio_measure
from app.measurement.rows import SessionRows, load_session_rows
from app.services.claims import TransferStage, transfer_ledger


def _closure_seq(rows: SessionRows) -> int | None:
    """Committed seq of the task-closing event (``task_deadline``), else None — the task
    never closed, so all "before closure" judgements are censored at the horizon."""
    for e in rows.events:
        if e["type"] == "task_deadline":
            return int(e["seq"])
    return None


def _horizon_seq(rows: SessionRows) -> int:
    return rows.last_event_seq


# ----- evidence transfer -------------------------------------------------------------------

_RENDER_TELEMETRY_TYPES = ("telemetry_render", "telemetry_render_first_final")


def _render_event_seqs(rows: SessionRows) -> dict[int, int]:
    """delivered-surface seq -> earliest committed render-telemetry event seq.

    BA-05 writes a ``delivery_receipt`` row and the ``telemetry_render`` /
    ``telemetry_render_first_final`` event that evidences it in the same commit;
    the telemetry event's ``data.seq`` (or a resolvable ``data.message_id``)
    names the delivered surface, so the render anchor is a committed event seq,
    never a client-reported timestamp.
    """
    committed_of_message = {
        str(m["message_id"]): int(m["committed_seq"])
        for m in rows.messages
        if m.get("committed_seq") is not None
    }
    out: dict[int, int] = {}
    for e in rows.events:
        if str(e["type"]) not in _RENDER_TELEMETRY_TYPES:
            continue
        data = (e.get("payload") or {}).get("data") or {}
        target: int | None = None
        raw_seq = data.get("seq")
        if isinstance(raw_seq, int | float):
            target = int(raw_seq)
        elif data.get("message_id") is not None:
            target = committed_of_message.get(str(data["message_id"]))
        if target is not None:
            out[target] = min(out.get(target, 1 << 62), int(e["seq"]))
    return out


def _claim_anchor(
    claim: Mapping[str, Any],
    rows: SessionRows,
    rendered: frozenset[int],
    render_seqs: Mapping[int, int],
) -> int | None:
    """Earliest event seq at which ``claim``'s reference reached the participant —
    ``None`` when the claim has no committed participant-visible reference event
    at all (rejected/quarantined output, coder lineage with no declared context).

    Delivered reference events: ``displayed_output.delivered_seq`` rows bound to
    the claim's ``model_call`` whose shown text is the model output itself —
    ``fallback='none'`` only, because a substituted deterministic fallback never
    carried the claim's text — plus a declared ``context_event_seq`` linkage
    (spec §7.6 delivery/context linkage) when the registering caller recorded
    one. When a ``delivery_receipt`` shows the surface was rendered
    (``rendered_at``), the anchor moves to the earliest committed render
    telemetry event for it; **when no render receipt exists the anchor falls
    back to the delivered commit seq itself** — the declared behavior: the
    association then reads "revised after the reference was *delivered*", never
    "after it was seen". Multiple deliveries anchor at the earliest one."""
    delivered: set[int] = set()
    call_id = claim.get("model_call_id")
    if call_id is not None:
        for o in rows.displayed_outputs:
            if (
                o.get("call_id") == call_id
                and o.get("delivered_seq") is not None
                and str(o.get("fallback") or "none") == "none"
            ):
                delivered.add(int(o["delivered_seq"]))
    ctx_seq = claim.get("context_event_seq")
    if ctx_seq is not None:
        delivered.add(int(ctx_seq))
    if not delivered:
        return None
    return min(render_seqs.get(d, d) if d in rendered else d for d in delivered)


def in_answer_records(rows: SessionRows) -> set[str]:
    """BA-25: ids of cited records whose *same-crate* answer was revised after the
    citing claim's reference reached the participant.

    Same-crate join: ``claim_source.record_id -> record.crate -> claim.crate ->
    answer_revision.crate`` — one crate end to end. A wrong-crate citation (the
    kind ``evaluate_support`` flags unsupported) cannot mark a record
    ``in_answer``, and neither can a revision of some other crate's answer.
    Ordering anchors at the claim's delivered (rendered, when a render receipt
    exists) reference event — see ``_claim_anchor`` — and *every* subsequent
    committed revision counts, not only the crate's first. The stage is a
    descriptive association (spec §9.3 acquisition-to-use), never proof the
    participant relied on the record."""
    claim_by_id = {c["claim_id"]: c for c in rows.claims}
    crate_of_record = {str(r["record_id"]): int(r["crate"]) for r in rows.records}
    revisions_by_crate: dict[int, list[int]] = {}
    for r in rows.answer_revisions:
        revisions_by_crate.setdefault(int(r["crate"]), []).append(int(r["accepted_seq"]))
    rendered = frozenset(
        int(d["seq"]) for d in rows.delivery_receipts if d.get("rendered_at") is not None
    )
    render_seqs = _render_event_seqs(rows)
    out: set[str] = set()
    for cs in rows.claim_sources:
        rid = cs.get("record_id")
        claim = claim_by_id.get(cs["claim_id"])
        if rid is None or claim is None or claim.get("crate") is None:
            continue
        crate = int(claim["crate"])
        if crate_of_record.get(str(rid)) != crate:
            continue  # same-crate join: a wrong-crate citation is not use of the record
        anchor = _claim_anchor(claim, rows, rendered, render_seqs)
        if anchor is None:
            continue  # the reference never reached a participant-visible surface
        if any(seq > anchor for seq in revisions_by_crate.get(crate, ())):
            out.add(str(rid))
    return out


def transfer_summary(conn: Connection, session_id: uuid.UUID) -> dict[str, Any]:
    """Per-record transfer-ledger stages (claims.transfer_ledger) plus the ``in_answer``
    derivation — a descriptive same-crate post-reference revision association
    (see ``in_answer_records``), never proof of reliance (spec §9.3).

    ``never`` = teammate-held records that reached neither delivery nor the file —
    reported as a set of record ids, never as a bare count that could hide *which*
    evidence never arrived."""
    ledger = transfer_ledger(conn, session_id)
    rows = load_session_rows(conn, session_id)
    for rid in in_answer_records(rows):
        stages = ledger.setdefault(rid, [])
        if TransferStage.IN_ANSWER.value not in stages:
            stages.append(TransferStage.IN_ANSWER.value)
    by_stage: dict[str, list[str]] = {}
    for rid, stages in ledger.items():
        for stage in stages:
            by_stage.setdefault(stage, []).append(rid)
    return {
        "ledger": {rid: sorted(stages) for rid, stages in sorted(ledger.items())},
        "stage_counts": {stage: len(ids) for stage, ids in sorted(by_stage.items())},
        "never_arrived": sorted(by_stage.get(TransferStage.NEVER.value, ())),
    }


# ----- decisive-evidence delivery ------------------------------------------------------------

DeliveryChannel = Literal["initial", "share", "message", "rendered"]


@dataclass(frozen=True)
class DecisiveDelivery:
    crate: int
    decisive_record_id: str | None
    delivered: bool
    delivery_channel: DeliveryChannel | None
    delivered_seq: int | None
    rendered: bool
    requested: bool  # participant issued a structured request for the crate
    censored: bool  # task never closed: judgement is at the observation horizon


def _record_delivery(rows: SessionRows) -> dict[str, tuple[int, str]]:
    """record_id -> (first delivery seq, channel). Delivery = initial holding (seq 0 —
    available from task open), a committed share, or a delivered message carrying the
    record."""
    first: dict[str, tuple[int, str]] = {}
    for m in rows.file_memberships:
        if m["source"] == "initial":
            first[str(m["record_id"])] = (0, "initial")
    for share in rows.shares:
        rid = str(share["record_id"])
        seq = int(share["delivered_seq"])
        if rid not in first or seq < first[rid][0]:
            first[rid] = (seq, "share")
    for m in rows.messages:
        if m.get("record_id") is not None and m["status"] == "delivered":
            rid = str(m["record_id"])
            seq = int(m["committed_seq"])
            if rid not in first or seq < first[rid][0]:
                first[rid] = (seq, "message")
    return first


def decisive_evidence(rows: SessionRows) -> dict[str, Any]:
    """Spec §9.3 decisive-evidence delivery: assigned team-dependent crates with a
    qualifying final-location record delivered before closure / 2.

    "Qualifying final-location record" is any committed record of that crate whose
    ``location_id`` equals the pinned truth and whose event_type confirms presence
    (``arrival``/``still_at``) — the world's ``participant_decisive`` id identifies the
    earliest such record. Requested/rendered subsets are reported per crate; a session
    without a closed task reports every undelivered crate as ``censored``.
    """
    truth = rows.truth
    teamdep = sorted(c for c in rows.assigned_crates if rows.role_of.get(c) == "team")
    closure = _closure_seq(rows)
    horizon = _horizon_seq(rows)
    bound = closure if closure is not None else horizon
    delivered = _record_delivery(rows)
    requested_crates = {
        int(r["crate"])
        for r in rows.requests
        if r["requester"] == "participant" and r["crate"] is not None
    }
    rendered_rids = {
        str(m["record_id"])
        for m in rows.messages
        if m.get("record_id") is not None
        and any(
            d["seq"] == m["committed_seq"] and d.get("rendered_at") is not None
            for d in rows.delivery_receipts
        )
    }

    per_crate: list[DecisiveDelivery] = []
    n_delivered = 0
    for crate in teamdep:
        t = truth.get(crate)
        qualifying = {
            str(r["record_id"])
            for r in rows.records
            if int(r["crate"]) == crate
            and r["location_id"] == t
            and r["event_type"] in ("arrival", "still_at")
        }
        candidates = [
            (seq, rid, ch)
            for rid in qualifying
            if (seq_ch := delivered.get(rid)) is not None
            for seq, ch in [seq_ch]
            if seq <= bound
        ]
        candidates.sort()
        got = candidates[0] if candidates else None
        n_delivered += got is not None
        decisive_id = rows.decisive_records.get(crate)
        per_crate.append(
            DecisiveDelivery(
                crate=crate,
                decisive_record_id=decisive_id,
                delivered=got is not None,
                delivery_channel=None if got is None else cast(DeliveryChannel, got[2]),
                delivered_seq=None if got is None else got[0],
                rendered=bool(qualifying & rendered_rids),
                requested=crate in requested_crates,
                censored=closure is None and got is None,
            )
        )
    return {
        "rate": ratio_measure(
            "evidence.decisive_delivery",
            n_delivered,
            len(teamdep),
            denominator_kind="teamdep_crates",
            missing_rule="undefined_when_no_teamdep_crates",
        ).as_dict(),
        "requested_subset": ratio_measure(
            "evidence.decisive_delivery_requested",
            sum(1 for c in per_crate if c.delivered and c.requested),
            sum(1 for c in per_crate if c.requested),
            denominator_kind="teamdep_crates",
            missing_rule="undefined_when_none_requested",
        ).as_dict(),
        "rendered_subset": ratio_measure(
            "evidence.decisive_delivery_rendered",
            sum(1 for c in per_crate if c.delivered and c.rendered),
            len(teamdep),
            denominator_kind="teamdep_crates",
            missing_rule="undefined_when_no_teamdep_crates",
        ).as_dict(),
        "per_crate": [c.__dict__ for c in per_crate],
    }


# ----- request fulfillment -------------------------------------------------------------------


def request_fulfillment(rows: SessionRows) -> dict[str, Any]:
    """Spec §9.3 request fulfillment: eligible accepted requests that received their
    policy-defined substantive response before closure / eligible accepted requests.

    "Eligible accepted request" = a committed ``request`` row (requests only exist via
    accepted commands/jobs). "Substantive response" = the episode's
    ``substantive_event_seq`` (a committed delivery or a participant share answering the
    ask), never a bare acknowledgment — two-stage replies keep them separate (§6.6).
    Expired/cancelled episodes are retained in ``by_status``; when the task never
    closed, still-open episodes are censored at the horizon, not counted as failed.
    """
    closure = _closure_seq(rows)
    horizon = _horizon_seq(rows)
    bound = closure if closure is not None else horizon
    ep_by_req = {e["request_id"]: e for e in rows.request_episodes}
    n_eligible = n_fulfilled = 0
    by_status: dict[str, int] = {}
    n_censored = 0
    per_request: list[dict[str, Any]] = []
    for req in sorted(rows.requests, key=lambda r: int(r["accepted_seq"])):
        ep = ep_by_req.get(req["request_id"])
        if ep is None:
            continue  # no committed episode: not an eligible request
        n_eligible += 1
        sub_seq = ep["substantive_event_seq"]
        fulfilled = sub_seq is not None and int(sub_seq) <= bound
        censored = ep["closed_at"] is None
        n_fulfilled += fulfilled
        n_censored += censored
        by_status[str(ep["status"])] = by_status.get(str(ep["status"]), 0) + 1
        per_request.append(
            {
                "request_id": str(req["request_id"]),
                "requester": str(req["requester"]),
                "responder": str(req["responder"]),
                "crate": req["crate"],
                "status": str(ep["status"]),
                "fulfilled": fulfilled,
                "censored": censored,
                "close_reason": ep["close_reason"],
            }
        )
    return {
        "rate": ratio_measure(
            "evidence.request_fulfillment",
            n_fulfilled,
            n_eligible,
            denominator_kind="eligible_requests",
            missing_rule="undefined_when_no_eligible_requests",
        ).as_dict(),
        "by_status": dict(sorted(by_status.items())),
        "censored_open": n_censored,
        "per_request": per_request,
    }


# ----- correction / deterioration -------------------------------------------------------------

Transition = Literal[
    "wrong_to_correct",
    "correct_to_wrong",
    "unchanged_correct",
    "unchanged_wrong",
]


def correction_deterioration(rows: SessionRows) -> dict[str, Any]:
    """Spec §9.3: wrong→correct and correct→wrong saved revisions, conditional on a prior
    nonmissing answer. Denominator = revision *transitions* (revisions ≥ 2 within a
    crate); the first revision of a crate has no prior answer and is not a transition.
    Unchanged revisions (same location re-saved, or wrong→wrong / correct→correct
    location changes) are retained; crates never answered stay in ``missing``, they are
    not transitions. Not a conformity measure (spec §9.3)."""
    truth = rows.truth
    by_crate: dict[int, list[Mapping[str, Any]]] = {}
    for r in rows.answer_revisions:
        by_crate.setdefault(int(r["crate"]), []).append(r)

    counts: dict[str, int] = {
        "wrong_to_correct": 0,
        "correct_to_wrong": 0,
        "unchanged_correct": 0,
        "unchanged_wrong": 0,
    }
    transitions: list[dict[str, Any]] = []
    for crate, revs in sorted(by_crate.items()):
        revs = sorted(revs, key=lambda r: int(r["revision"]))
        t = truth.get(crate)
        for prev, cur in itertools.pairwise(revs):
            prev_ok = str(prev["location_id"]) == t
            cur_ok = str(cur["location_id"]) == t
            if cur_ok and not prev_ok:
                kind: Transition = "wrong_to_correct"
            elif prev_ok and not cur_ok:
                kind = "correct_to_wrong"
            elif cur_ok:
                kind = "unchanged_correct"
            else:
                kind = "unchanged_wrong"
            counts[kind] += 1
            transitions.append(
                {
                    "crate": crate,
                    "from_revision": int(prev["revision"]),
                    "to_revision": int(cur["revision"]),
                    "from_location": str(prev["location_id"]),
                    "to_location": str(cur["location_id"]),
                    "transition": kind,
                }
            )
    total = sum(counts.values())
    never = sorted(set(rows.truth) - set(by_crate))
    return {
        "correction_rate": ratio_measure(
            "evidence.correction_rate",
            counts["wrong_to_correct"],
            total,
            denominator_kind="revision_transitions",
            missing_rule="undefined_when_no_transitions",
        ).as_dict(),
        "deterioration_rate": ratio_measure(
            "evidence.deterioration_rate",
            counts["correct_to_wrong"],
            total,
            denominator_kind="revision_transitions",
            missing_rule="undefined_when_no_transitions",
        ).as_dict(),
        "counts": counts,
        "transitions": transitions,
        "crates_never_answered": never,
        "crates_single_revision": sorted(c for c, r in by_crate.items() if len(r) == 1),
    }


# ----- redundancy ----------------------------------------------------------------------------


def redundancy(rows: SessionRows) -> dict[str, Any]:
    """Spec §9.3: delivery events versus distinct records delivered to the participant.
    A repeat delivery of the same record (replay/resync or a second share) is redundant —
    not new evidence. Initial file membership is *acquisition*, not a delivery event, and
    is excluded."""
    deliveries: list[tuple[str, int]] = []
    for m in rows.messages:
        if m.get("record_id") is not None and m["status"] == "delivered":
            deliveries.append((str(m["record_id"]), int(m["committed_seq"])))
    for share in rows.shares:
        deliveries.append((str(share["record_id"]), int(share["delivered_seq"])))
    deliveries.sort(key=lambda d: d[1])
    distinct = {rid for rid, _ in deliveries}
    n_total = len(deliveries)
    return {
        "delivery_events": count_measure(
            "evidence.delivery_events",
            n_total,
            unit="delivery_events",
            missing_rule="missing_if_no_deliveries",
        ).as_dict(),
        "distinct_records": count_measure(
            "evidence.distinct_records_delivered",
            len(distinct),
            unit="records",
            missing_rule="missing_if_no_deliveries",
        ).as_dict(),
        "redundant_deliveries": count_measure(
            "evidence.redundant_deliveries",
            n_total - len(distinct),
            unit="delivery_events",
            missing_rule="missing_if_no_deliveries",
        ).as_dict(),
        "redundancy_ratio": ratio_measure(
            "evidence.redundancy_ratio",
            n_total - len(distinct),
            n_total,
            denominator_kind="delivery_events",
            missing_rule="undefined_when_no_deliveries",
        ).as_dict(),
    }


# ----- unsupported assertions -----------------------------------------------------------------


def assertion_support(rows: SessionRows) -> dict[str, Any]:
    """Spec §9.3: unsupported assertions / coded (evaluated) factual assertions.

    Denominator = claims whose support was evaluated (``supported``/``unsupported``/
    ``partial``); ``unevaluated`` claims and ``negated`` claims are reported in coverage
    but excluded from the rate — a never-evaluated assertion is missing data, not a
    supported one. Explicitly undefined when no assertion was evaluated."""
    evaluated = [
        c
        for c in rows.claims
        if str(c["support_status"]) in ("supported", "unsupported", "partial")
    ]
    n_unsupported = sum(1 for c in evaluated if c["support_status"] == "unsupported")
    by_status: dict[str, int] = {}
    by_issuer: dict[str, int] = {}
    for c in rows.claims:
        by_status[str(c["support_status"])] = by_status.get(str(c["support_status"]), 0) + 1
        by_issuer[str(c["issuer"])] = by_issuer.get(str(c["issuer"]), 0) + 1
    return {
        "unsupported_rate": ratio_measure(
            "evidence.unsupported_assertion_rate",
            n_unsupported,
            len(evaluated),
            denominator_kind="evaluated_assertions",
            missing_rule="undefined_when_no_evaluated_assertions",
        ).as_dict(),
        "by_support_status": dict(sorted(by_status.items())),
        "by_issuer": dict(sorted(by_issuer.items())),
        "claims_total": len(rows.claims),
        "coverage_note": (
            "assertions are exact rows; free-text factual statements need offline coding (WP12a)"
        ),
    }


def evidence_measures(conn: Connection, session_id: uuid.UUID) -> dict[str, Any]:
    """The full INV-1103 bundle for one session (deterministic)."""
    rows = load_session_rows(conn, session_id)
    return {
        "transfer": transfer_summary(conn, session_id),
        "decisive_evidence": decisive_evidence(rows),
        "request_fulfillment": request_fulfillment(rows),
        "correction_deterioration": correction_deterioration(rows),
        "redundancy": redundancy(rows),
        "assertion_support": assertion_support(rows),
    }
