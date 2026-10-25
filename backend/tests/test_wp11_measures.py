"""Unit tests for the WP11 measurement layer (INV-1101..1105).

These exercise the pure compute path: every measure consumes a ``SessionRows``
projection built from synthetic row dicts — no database required. The same functions
run against committed rows in the integration suite (``tests/integration/``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.measurement import codebook, episodes, evidence, metrics, periods, scoring
from app.measurement.results import MeasureState
from app.measurement.rows import SessionRows

T0 = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)


def _u(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


def _receipt(command_id: uuid.UUID, kind: str, outcome: str = "accepted") -> dict[str, Any]:
    return {
        "command_id": command_id,
        "kind": kind,
        "outcome": outcome,
        "received_at": T0,
    }


def _event(
    seq: int,
    type_: str,
    *,
    actor: str = "participant",
    active_ms: int | None = None,
    command_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "seq": seq,
        "event_id": _u(10_000 + seq),
        "type": type_,
        "actor": actor,
        "stage": "task",
        "active_ms": active_ms,
        "command_id": command_id,
        "server_time": T0 + timedelta(seconds=seq),
        "payload": payload or {},
    }


def _request(
    n: int,
    *,
    requester: str = "participant",
    responder: str = "JT",
    crate: int | None = 3,
    kind: str = "structured",
    seq: int,
    active_ms: int | None = None,
    command_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "request_id": _u(20_000 + n),
        "requester": requester,
        "responder": responder,
        "crate": crate,
        "target": "latest" if kind == "structured" else "social",
        "kind": kind,
        "command_id": command_id,
        "accepted_seq": seq,
        "accepted_active_ms": active_ms,
        "accepted_at": T0 + timedelta(seconds=seq),
    }


def _episode(
    n: int,
    request_id: uuid.UUID,
    *,
    status: str = "pending",
    opened: float = 0,
    closed: float | None = None,
    substantive_seq: int | None = None,
    closed_seq: int | None = None,
) -> dict[str, Any]:
    return {
        "episode_id": _u(30_000 + n),
        "request_id": request_id,
        "status": status,
        "acknowledged_event_seq": None,
        "substantive_event_seq": substantive_seq,
        "closed_event_seq": closed_seq,
        "close_reason": "delivered" if status == "resolved" else None,
        "pending_ms": None,
        "subsequent_action": None,
        "opened_at": T0 + timedelta(seconds=opened),
        "closed_at": None if closed is None else T0 + timedelta(seconds=closed),
    }


def _world(assigned=(1, 2, 3, 4), roles=None, truth=None, decisive=None) -> dict[str, Any]:
    roles = roles or {1: "own", 2: "own", 3: "team", 4: "team"}
    truth = truth or {n: f"H{n}" for n in range(1, 9)}
    return {
        "assigned": list(assigned),
        "role_of": {str(k): v for k, v in roles.items()},
        "crates": [{"num": n, "truth": truth[n]} for n in sorted(truth)],
        "participant_decisive": {str(k): v for k, v in (decisive or {}).items()},
    }


def _rows(**kw: Any) -> SessionRows:
    events = kw.get("events", ())
    kw.setdefault("_event_by_seq", {int(e["seq"]): e for e in events})
    return SessionRows(session_id=_u(1), **kw)


# ----- receipts dedup / counts ------------------------------------------------------------------


def test_command_counts_dedup_command_id() -> None:
    cid = _u(5)
    rows = _rows(
        command_receipts=(
            _receipt(cid, "teammate_request"),
            _receipt(cid, "teammate_request"),  # a retried command shares one identity
            _receipt(_u(6), "teammate_request"),
            _receipt(_u(7), "assistant_request"),
            _receipt(_u(8), "answer_update", outcome="rejected"),
        )
    )
    counts = metrics.command_counts(rows)
    assert counts["teammate_request"]["accepted"] == 2
    assert counts["assistant_request"] == {"accepted": 1, "rejected": 0}
    assert counts["answer_update"] == {"accepted": 0, "rejected": 1}


def test_seeking_metrics_counts_and_unique() -> None:
    r1 = _request(1, seq=3, active_ms=10_000, command_id=_u(5))
    r2 = _request(2, seq=5, crate=4, active_ms=20_000, command_id=_u(6))
    ep1 = _episode(1, r1["request_id"], status="resolved", substantive_seq=9, closed_seq=9)
    rows = _rows(
        command_receipts=(
            _receipt(_u(5), "teammate_request"),
            _receipt(_u(6), "teammate_request"),
            _receipt(_u(7), "social_reply"),
        ),
        requests=(r1, r2),
        request_episodes=(ep1, _episode(2, r2["request_id"])),
        events=(_event(9, "message_delivered", actor="JT", active_ms=30_000),),
    )
    sm = metrics.seeking_metrics(rows)
    assert sm.structured_teammate_requests.value == 2
    assert sm.structured_requests_unique_crates.value == 2
    assert sm.structured_requests_delivered_replies.value == 1
    assert sm.free_text_teammate_messages.value == 1
    assert sm.total_structured_seeking.value == 3


def test_agent_originated_requests_not_participant_seeking() -> None:
    scripted = _request(9, requester="JT", responder="participant", crate=5, seq=2)
    rows = _rows(
        requests=(scripted,),
        request_episodes=(_episode(9, scripted["request_id"]),),
    )
    sm = metrics.seeking_metrics(rows)
    assert sm.structured_teammate_requests.value == 0
    assert sm.structured_requests_unique_crates.value == 0


# ----- AI use ------------------------------------------------------------------------------------


def test_assistant_use_routes() -> None:
    cid_typed, cid_picker = _u(11), _u(12)
    r_typed = _request(
        1, responder="assistant", crate=3, kind="free_text", seq=3, command_id=cid_typed
    )
    r_picker = _request(
        2, responder="assistant", crate=4, kind="free_text", seq=6, command_id=cid_picker
    )
    rows = _rows(
        command_receipts=(
            _receipt(cid_typed, "assistant_request"),
            _receipt(cid_picker, "assistant_request"),
        ),
        requests=(r_typed, r_picker),
        request_episodes=(
            _episode(1, r_typed["request_id"], status="resolved", substantive_seq=8),
            _episode(2, r_picker["request_id"]),
        ),
        events=(
            _event(
                3, "assistant_requested", command_id=cid_typed, payload={"body": "hi", "crate": 3}
            ),
            _event(
                6, "assistant_requested", command_id=cid_picker, payload={"body": None, "crate": 4}
            ),
        ),
    )
    au = metrics.assistant_use(rows)
    assert au["prompts"].value == 2
    assert au["by_route"] == {"typed": 1, "picker": 1}
    assert au["by_crate"] == {3: 1, 4: 1}
    assert au["per_prompt"][0].route == "typed"
    assert au["per_prompt"][1].route == "picker"
    assert au["delivered"].value == 0  # no committed assistant messages yet


# ----- sharing incl. proactive -------------------------------------------------------------------


def _share(
    n: int, record_id: str, *, seq: int, prompted: uuid.UUID | None = None
) -> dict[str, Any]:
    return {
        "share_id": _u(40_000 + n),
        "sharer": "participant",
        "record_id": record_id,
        "prompted_by_request_id": prompted,
        "delivered_seq": seq,
        "delivered_at": T0 + timedelta(seconds=seq),
        "source": "participant_action",
        "recipients": ["JT", "PM"],
    }


def _record(rid: str, crate: int, location: str = "H1") -> dict[str, Any]:
    return {
        "record_id": rid,
        "crate": crate,
        "location_id": location,
        "event_type": "arrival",
        "timeline_ordinal": 1,
    }


def _announce_msg(request_id: uuid.UUID, seq: int, crate: int) -> dict[str, Any]:
    return {
        "message_id": _u(50_000 + seq),
        "actor": "JT",
        "channel": "team",
        "status": "delivered",
        "request_id": request_id,
        "committed_seq": seq,
        "accepted_seq": seq,
        "record_id": None,
        "parent_message_id": None,
        "crate": crate,
    }


def test_proactive_strict_vs_legacy() -> None:
    """Strict removes eligibility on *any* delivered solicitation for the crate
    (including crosstalk chained to the ask); legacy follows the demo rule
    (unprompted + unassigned + no delivered scripted request)."""
    world = _world()
    ask = _request(1, requester="JT", responder="participant", crate=6, seq=10)
    announce = _announce_msg(ask["request_id"], seq=11, crate=6)
    # share on crate 6 after the delivered ask: neither strict nor legacy
    share_after = _share(1, "r6", seq=20)
    # share on crate 7 (never solicited): both strict and legacy
    share_clean = _share(2, "r7", seq=21)
    # prompted share on crate 8 (resolved the ask): strict is still False since the
    # solicitation preceded; legacy excludes via !ctx anyway
    ask8 = _request(2, requester="PM", responder="participant", crate=8, seq=22)
    announce8 = _announce_msg(ask8["request_id"], seq=23, crate=8)
    share_prompted = _share(3, "r8", seq=30, prompted=ask8["request_id"])
    # own-crate share: neither
    share_own = _share(4, "r1", seq=35)
    rows = _rows(
        world_content=world,
        requests=(ask, ask8),
        messages=(announce, announce8),
        shares=(share_after, share_clean, share_prompted, share_own),
        records=(
            _record("r6", 6),
            _record("r7", 7),
            _record("r8", 8),
            _record("r1", 1),
        ),
    )
    sm = metrics.sharing_metrics(rows)
    assert sm.participant_share_events.value == 4
    assert sm.unassigned_crate_shares.value == 3
    assert sm.own_crate_shares.value == 1
    assert sm.prompted_shares.value == 1
    assert sm.proactive_shares_strict.value == 1
    assert sm.proactive_shares_legacy.value == 1
    flags = {s["record_id"]: (s["proactive_strict"], s["proactive_legacy"]) for s in sm.per_share}
    assert flags["r6"] == (False, False)
    assert flags["r7"] == (True, True)
    assert flags["r8"] == (False, False)
    assert flags["r1"] == (False, False)


def test_proactive_before_ask_is_strict_and_legacy() -> None:
    world = _world()
    ask = _request(1, requester="JT", responder="participant", crate=6, seq=100)
    announce = _announce_msg(ask["request_id"], seq=101, crate=6)
    share_first = _share(1, "r6", seq=5)  # shared before the ask was ever delivered
    rows = _rows(
        world_content=world,
        requests=(ask,),
        messages=(announce,),
        shares=(share_first,),
        records=(_record("r6", 6),),
    )
    sm = metrics.sharing_metrics(rows)
    assert sm.proactive_shares_strict.value == 1
    assert sm.proactive_shares_legacy.value == 1


def test_proactive_strict_catches_crosstalk_solicitation() -> None:
    """A crosstalk nudge chained to a delivered ask counts as solicitation in the strict
    rule. (In this implementation crosstalk only ever follows a delivered scripted ask,
    so strict and legacy coincide whenever solicitation is announcement+crosstalk only;
    they diverge only if a solicitation channel without a ``request`` row is added —
    the definitions stay separate so that divergence is visible.)"""
    world = _world()
    ask = _request(1, requester="JT", responder="participant", crate=6, seq=10)
    announce = _announce_msg(ask["request_id"], seq=11, crate=6)
    crosstalk = {
        "message_id": _u(50_099),
        "actor": "PM",
        "channel": "team",
        "status": "delivered",
        "request_id": None,
        "committed_seq": 12,
        "accepted_seq": 12,
        "record_id": None,
        "parent_message_id": announce["message_id"],
        "crate": 6,
    }
    share = _share(1, "r6", seq=15)
    rows = _rows(
        world_content=world,
        requests=(ask,),
        messages=(announce, crosstalk),
        shares=(share,),
        records=(_record("r6", 6),),
    )
    sm = metrics.sharing_metrics(rows)
    # solicitation for crate 6 exists before the share in both rules here
    assert sm.proactive_shares_strict.value == 0
    assert sm.proactive_shares_legacy.value == 0


# ----- allocation / timing ---------------------------------------------------------------------


def test_seeking_allocation_zero_denominator_is_undefined() -> None:
    alloc = metrics.seeking_allocation(_rows())
    assert alloc.state is MeasureState.UNDEFINED_DENOMINATOR
    assert alloc.value is None
    assert alloc.missing_rule == "undefined_when_no_seeking"


def test_seeking_allocation_value() -> None:
    rows = _rows(
        command_receipts=(
            _receipt(_u(1), "assistant_request"),
            _receipt(_u(2), "assistant_request"),
            _receipt(_u(3), "teammate_request"),
        )
    )
    alloc = metrics.seeking_allocation(rows)
    assert alloc.state is MeasureState.MEASURED
    assert alloc.value == pytest.approx(2 / 3)


def test_first_action_times_missing_vs_measured() -> None:
    rows = _rows(
        events=(
            _event(4, "teammate_requested", active_ms=10_000),
            _event(7, "record_shared", active_ms=25_000),
        )
    )
    times = metrics.first_action_times(rows)
    assert times["first_teammate_request_ms"].value == 10_000
    assert times["first_share_ms"].value == 25_000
    assert times["first_request_ms"].value == 10_000
    assert times["first_request_or_share_ms"].value == 10_000
    assert times["first_assistant_prompt_ms"].state is MeasureState.MISSING_DATA
    assert times["first_assistant_prompt_ms"].value is None


# ----- scoring ------------------------------------------------------------------------------------


def test_scoring_missing_counts_incorrect_answered_only_undefined() -> None:
    truth = {n: f"H{n}" for n in range(1, 9)}
    roles = {1: "own", 2: "own", 3: "team", 4: "team"}
    score = scoring.score_snapshot(
        finals={1: "H1", 2: "WRONG", 3: "H3"},  # crate 4 (teamdep) unanswered
        truth=truth,
        role_of=roles,
        assigned=(1, 2, 3, 4),
        revised=frozenset({1, 2, 3, 4}),
    )
    assert score.correct_total.value == pytest.approx(2 / 4)
    assert score.correct_ownfile.value == pytest.approx(1 / 2)
    assert score.correct_teamdep.value == pytest.approx(1 / 2)
    assert score.accuracy_answered_only.value == pytest.approx(2 / 3)
    per = {c.crate: c for c in score.per_crate}
    assert per[4].state == "unfrozen"  # revised but never frozen
    assert per[4].correct is None
    assert per[5].state == "missing"  # unassigned and never touched
    assert score.scoring_version == scoring.SCORING_VERSION


def test_scoring_none_answered_undefined() -> None:
    score = scoring.score_snapshot(
        finals={},
        truth={n: f"H{n}" for n in range(1, 9)},
        role_of={1: "own", 2: "own", 3: "team", 4: "team"},
        assigned=(1, 2, 3, 4),
    )
    assert score.correct_total.value == 0.0  # displayed total: missing counts incorrect
    assert score.accuracy_answered_only.state is MeasureState.UNDEFINED_DENOMINATOR
    assert score.missing_count == 4


def test_answer_states_distinguish_missing_unfrozen() -> None:
    rows = _rows(
        world_content=_world(),
        answer_finals=({"crate": 1, "revision": 1, "location_id": "H1", "finalized_seq": 9},),
        answer_revisions=(
            {"crate": 1, "revision": 1, "location_id": "H1", "accepted_seq": 3},
            {"crate": 2, "revision": 1, "location_id": "H9", "accepted_seq": 4},
        ),
    )
    states = metrics.answer_states(rows)
    assert states["per_crate"][1] == "answered"
    assert states["per_crate"][2] == "unfrozen"
    assert states["per_crate"][3] == "missing"
    assert states["answered"] == 1 and states["unfrozen"] == 1 and states["missing"] == 2


# ----- evidence rates -----------------------------------------------------------------------------


def test_correction_deterioration() -> None:
    rows = _rows(
        world_content=_world(truth={**{n: "H0" for n in range(3, 9)}, 1: "H1", 2: "H2"}),
        answer_revisions=(
            {"crate": 1, "revision": 1, "location_id": "HX", "accepted_seq": 1},
            {"crate": 1, "revision": 2, "location_id": "H1", "accepted_seq": 2},  # w2c
            {"crate": 1, "revision": 3, "location_id": "H1", "accepted_seq": 3},  # same ok
            {"crate": 2, "revision": 1, "location_id": "H2", "accepted_seq": 4},
            {"crate": 2, "revision": 2, "location_id": "H9", "accepted_seq": 5},  # c2w
        ),
    )
    out = evidence.correction_deterioration(rows)
    assert out["counts"] == {
        "wrong_to_correct": 1,
        "correct_to_wrong": 1,
        "unchanged_correct": 1,
        "unchanged_wrong": 0,
    }
    assert out["correction_rate"]["value"] == pytest.approx(1 / 3)
    assert out["deterioration_rate"]["value"] == pytest.approx(1 / 3)
    assert out["crates_never_answered"] == [3, 4, 5, 6, 7, 8]
    assert out["crates_single_revision"] == []


def test_correction_no_transitions_undefined() -> None:
    rows = _rows(
        world_content=_world(),
        answer_revisions=({"crate": 1, "revision": 1, "location_id": "HX", "accepted_seq": 1},),
    )
    out = evidence.correction_deterioration(rows)
    assert out["correction_rate"]["state"] == "undefined_denominator"
    assert out["crates_single_revision"] == [1]


def test_request_fulfillment_and_censoring() -> None:
    r_ok = _request(1, seq=3, active_ms=1_000)
    r_open = _request(2, seq=5, crate=4, active_ms=2_000)
    rows = _rows(
        requests=(r_ok, r_open),
        request_episodes=(
            _episode(
                1, r_ok["request_id"], status="resolved", substantive_seq=8, closed=8, closed_seq=8
            ),
            _episode(  # expired at the deadline, as the real sweep does
                2, r_open["request_id"], status="expired", closed=10, closed_seq=10
            ),
        ),
        events=(
            _event(8, "message_delivered", actor="JT", active_ms=3_000),
            _event(10, "task_deadline", actor="system", active_ms=360_000),
        ),
    )
    out = evidence.request_fulfillment(rows)
    assert out["rate"]["value"] == pytest.approx(1 / 2)
    assert out["rate"]["denominator"] == 2
    assert out["by_status"] == {"resolved": 1, "expired": 1}
    assert out["censored_open"] == 0  # closed task: episodes are expired, not censored
    # without the deadline the still-open episode is censored, not failed
    rows_open = _rows(
        requests=(r_open,),
        request_episodes=(_episode(2, r_open["request_id"]),),
        events=(_event(8, "message_delivered", actor="JT", active_ms=3_000),),
    )
    out_open = evidence.request_fulfillment(rows_open)
    assert out_open["censored_open"] == 1
    assert out_open["rate"]["value"] == 0.0


def test_redundancy_counts_repeat_deliveries() -> None:
    rows = _rows(
        messages=(
            {
                "message_id": _u(60_001),
                "status": "delivered",
                "record_id": "r1",
                "committed_seq": 5,
                "request_id": None,
                "channel": "team",
            },
            {
                "message_id": _u(60_002),
                "status": "delivered",
                "record_id": "r1",
                "committed_seq": 9,
                "request_id": None,
                "channel": "team",
            },
        ),
        shares=(_share(1, "r2", seq=12),),
    )
    out = evidence.redundancy(rows)
    assert out["delivery_events"]["value"] == 3
    assert out["distinct_records"]["value"] == 2
    assert out["redundant_deliveries"]["value"] == 1
    assert out["redundancy_ratio"]["value"] == pytest.approx(1 / 3)


def test_assertion_support_undefined_when_unevaluated() -> None:
    rows = _rows(claims=({"claim_id": _u(1), "support_status": "unevaluated", "issuer": "coder"},))
    out = evidence.assertion_support(rows)
    assert out["unsupported_rate"]["state"] == "undefined_denominator"
    rows2 = _rows(
        claims=(
            {"claim_id": _u(1), "support_status": "supported", "issuer": "assistant"},
            {"claim_id": _u(2), "support_status": "unsupported", "issuer": "assistant"},
            {"claim_id": _u(3), "support_status": "unsupported", "issuer": "coder"},
        )
    )
    out2 = evidence.assertion_support(rows2)
    assert out2["unsupported_rate"]["value"] == pytest.approx(2 / 3)
    assert out2["by_issuer"] == {"assistant": 2, "coder": 1}


# ----- in_answer (BA-25) -------------------------------------------------------------------------


def _claim(
    n: int,
    crate: int,
    *,
    context_seq: int | None = None,
    call_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "claim_id": _u(90_000 + n),
        "crate": crate,
        "location_id": "H1",
        "event_type": "arrival",
        "assertion": "asserted",
        "support_status": "supported",
        "issuer": "assistant",
        "snapshot_id": None,
        "model_call_id": call_id,
        "context_event_seq": context_seq,
    }


def _claim_source(n: int, claim_id: uuid.UUID, record_id: str) -> dict[str, Any]:
    return {
        "claim_source_id": _u(95_000 + n),
        "claim_id": claim_id,
        "source_kind": "record",
        "record_id": record_id,
        "event_seq": None,
    }


def _shown(call_id: uuid.UUID, delivered_seq: int, *, fallback: str = "none") -> dict[str, Any]:
    return {
        "output_id": _u(96_000 + delivered_seq),
        "call_id": call_id,
        "fallback": fallback,
        "delivered_seq": delivered_seq,
        "message_id": None,
        "shown_sha256": "x" * 64,
    }


def _rev(crate: int, revision: int, seq: int) -> dict[str, Any]:
    return {
        "crate": crate,
        "revision": revision,
        "location_id": f"H{crate}",
        "accepted_seq": seq,
    }


def test_in_answer_same_crate_post_reference() -> None:
    """BA-25 (a): a crate-3 claim citing a crate-3 record, delivered to the
    participant, followed by a crate-3 revision -> in_answer."""
    call = _u(7)
    rows = _rows(
        records=(_record("r3", 3),),
        claims=(_claim(1, 3, call_id=call),),
        claim_sources=(_claim_source(1, _u(90_001), "r3"),),
        displayed_outputs=(_shown(call, 10),),
        answer_revisions=(_rev(3, 1, 20),),
    )
    assert evidence.in_answer_records(rows) == {"r3"}


def test_in_answer_cross_crate_revision_never_counts() -> None:
    """BA-25 (b): a revision of crate 4's answer never marks a crate-3 claim's
    record in_answer."""
    call = _u(7)
    rows = _rows(
        records=(_record("r3", 3),),
        claims=(_claim(1, 3, call_id=call),),
        claim_sources=(_claim_source(1, _u(90_001), "r3"),),
        displayed_outputs=(_shown(call, 10),),
        answer_revisions=(_rev(4, 1, 20),),
    )
    assert evidence.in_answer_records(rows) == set()


def test_in_answer_wrong_crate_citation_never_joins() -> None:
    """BA-25: claim.crate must equal the cited record's crate — a claim about
    crate 3 citing a crate-4 record cannot mark it even when crate 3 is revised."""
    call = _u(7)
    rows = _rows(
        records=(_record("r4", 4),),
        claims=(_claim(1, 3, call_id=call),),
        claim_sources=(_claim_source(1, _u(90_001), "r4"),),
        displayed_outputs=(_shown(call, 10),),
        answer_revisions=(_rev(3, 1, 20),),
    )
    assert evidence.in_answer_records(rows) == set()


def test_in_answer_prereference_revisions_do_not_count() -> None:
    """BA-25 (c): only revisions after the reference event count — and every
    subsequent revision is inspected, not just the crate's first."""
    call = _u(7)
    base = dict(
        records=(_record("r3", 3),),
        claims=(_claim(1, 3, call_id=call),),
        claim_sources=(_claim_source(1, _u(90_001), "r3"),),
        displayed_outputs=(_shown(call, 10),),
    )
    pre_only = _rows(answer_revisions=(_rev(3, 1, 4),), **base)
    assert evidence.in_answer_records(pre_only) == set()
    # the first revision predates the reference; a later one qualifies
    with_later = _rows(answer_revisions=(_rev(3, 1, 4), _rev(3, 2, 30)), **base)
    assert evidence.in_answer_records(with_later) == {"r3"}


def test_in_answer_anchors_at_render_when_receipt_exists() -> None:
    """A render receipt moves the anchor to the committed render-telemetry event:
    a revision between delivery and render does not count; one after does."""
    call = _u(7)
    base = dict(
        records=(_record("r3", 3),),
        claims=(_claim(1, 3, call_id=call),),
        claim_sources=(_claim_source(1, _u(90_001), "r3"),),
        displayed_outputs=(_shown(call, 10),),
        delivery_receipts=(
            {
                "seq": 10,
                "recipient": "participant",
                "kind": "initial",
                "received_at": T0,
                "rendered_at": T0,
            },
        ),
        events=(_event(15, "telemetry_render", payload={"data": {"seq": 10}}),),
    )
    between = _rows(answer_revisions=(_rev(3, 1, 12),), **base)
    assert evidence.in_answer_records(between) == set()  # 12 < render anchor 15
    after = _rows(answer_revisions=(_rev(3, 1, 12), _rev(3, 2, 20)), **base)
    assert evidence.in_answer_records(after) == {"r3"}


def test_in_answer_missing_render_receipt_falls_back_to_delivered() -> None:
    """BA-25 (d): no render receipt -> the declared fallback anchor is the
    delivered commit seq ("revised after delivery", never "after it was seen")."""
    call = _u(7)
    rows = _rows(
        records=(_record("r3", 3),),
        claims=(_claim(1, 3, call_id=call),),
        claim_sources=(_claim_source(1, _u(90_001), "r3"),),
        displayed_outputs=(_shown(call, 10),),
        delivery_receipts=(
            {  # received, never rendered — not a render receipt
                "seq": 10,
                "recipient": "participant",
                "kind": "initial",
                "received_at": T0,
                "rendered_at": None,
            },
        ),
        answer_revisions=(_rev(3, 1, 12),),
    )
    assert evidence.in_answer_records(rows) == {"r3"}
    # no receipt at all: same declared fallback
    rows_none = _rows(
        records=(_record("r3", 3),),
        claims=(_claim(1, 3, call_id=call),),
        claim_sources=(_claim_source(1, _u(90_001), "r3"),),
        displayed_outputs=(_shown(call, 10),),
        answer_revisions=(_rev(3, 1, 12),),
    )
    assert evidence.in_answer_records(rows_none) == {"r3"}


def test_in_answer_never_delivered_claim_has_no_anchor() -> None:
    """A claim with no displayed output and no declared context_event_seq never
    reached the participant — the reference event is missing, not seq 0."""
    rows = _rows(
        records=(_record("r3", 3),),
        claims=(_claim(1, 3),),  # no model_call, no context linkage
        claim_sources=(_claim_source(1, _u(90_001), "r3"),),
        answer_revisions=(_rev(3, 1, 30),),
    )
    assert evidence.in_answer_records(rows) == set()
    # a declared context_event_seq is itself a committed reference event
    rows_ctx = _rows(
        records=(_record("r3", 3),),
        claims=(_claim(1, 3, context_seq=9),),
        claim_sources=(_claim_source(1, _u(90_001), "r3"),),
        answer_revisions=(_rev(3, 1, 30),),
    )
    assert evidence.in_answer_records(rows_ctx) == {"r3"}


def test_in_answer_fallback_output_carries_no_claim() -> None:
    """A deterministic-fallback display never showed the claim's text — it is not
    a reference event."""
    call = _u(7)
    rows = _rows(
        records=(_record("r3", 3),),
        claims=(_claim(1, 3, call_id=call),),
        claim_sources=(_claim_source(1, _u(90_001), "r3"),),
        displayed_outputs=(_shown(call, 10, fallback="deterministic_file_summary"),),
        answer_revisions=(_rev(3, 1, 20),),
    )
    assert evidence.in_answer_records(rows) == set()


def test_decisive_evidence_delivery() -> None:
    truth = {n: f"H{n}" for n in range(1, 9)}
    world = _world(truth=truth, decisive={3: "r3x", 4: "r4x"})
    rows = _rows(
        world_content=world,
        records=(
            _record("r3x", 3, "H3"),
            _record("r3old", 3, "HX"),
            _record("r4x", 4, "H4"),
        ),
        shares=(_share(1, "r3x", seq=20),),  # decisive record for crate 3 delivered
        messages=(
            {
                "message_id": _u(70_001),
                "status": "delivered",
                "record_id": "r4x",
                "committed_seq": 999,  # delivered only after the deadline seq
                "request_id": None,
                "channel": "team",
            },
        ),
        events=(_event(500, "task_deadline", actor="system", active_ms=360_000),),
    )
    out = evidence.decisive_evidence(rows)
    assert out["rate"]["value"] == pytest.approx(1 / 2)
    per = {c["crate"]: c for c in out["per_crate"]}
    assert per[3]["delivered"] and per[3]["delivery_channel"] == "share"
    assert not per[4]["delivered"] and not per[4]["censored"]  # after closure: simply late


def test_decisive_evidence_censored_without_closure() -> None:
    rows = _rows(
        world_content=_world(decisive={3: "r3x", 4: "r4x"}),
        records=(_record("r3x", 3, "H3"), _record("r4x", 4, "H4")),
        events=(_event(5, "message_delivered", active_ms=10_000),),
    )
    out = evidence.decisive_evidence(rows)
    assert all(c["censored"] for c in out["per_crate"])
    assert out["rate"]["value"] == 0.0


# ----- episodes (INV-1104) ---------------------------------------------------------------------


def test_pending_union_does_not_double_count() -> None:
    """Two overlapping pending windows (structured ask to JT + free-text to PM) union to
    their span, not their sum."""
    r1 = _request(1, responder="JT", seq=1, active_ms=10_000)
    r2 = _request(2, responder="PM", kind="free_text", crate=None, seq=2, active_ms=20_000)
    rows = _rows(
        requests=(r1, r2),
        request_episodes=(
            _episode(1, r1["request_id"], status="resolved", substantive_seq=8, closed_seq=8),
            _episode(2, r2["request_id"], status="resolved", substantive_seq=9, closed_seq=9),
        ),
        events=(
            _event(8, "message_delivered", actor="JT", active_ms=50_000),
            _event(9, "message_delivered", actor="PM", active_ms=70_000),
        ),
    )
    out = episodes.pending_overlap(rows, requester="participant")
    # pending spans: [10,50] and [20,70] active ms → union 60_000; sum would be 90_000
    assert out["pending_active_union_ms"] == 60_000
    assert out["summed_pending_active_ms"] == 90_000
    assert out["pending_active_spans"] == [(10_000.0, 70_000.0)]


def test_episode_censored_at_horizon() -> None:
    r = _request(1, seq=1, active_ms=10_000)
    rows = _rows(
        requests=(r,),
        request_episodes=(_episode(1, r["request_id"]),),
        events=(_event(4, "answer_updated", active_ms=80_000),),
    )
    (ep,) = episodes.episode_rows(rows)
    assert ep.censored
    assert ep.pending_active_ms == 70_000  # truncated at the active horizon, not dropped
    out = episodes.pending_overlap(rows, requester="participant")
    assert out["censored"] == 1


def test_opportunity_windows_for_participant_asks() -> None:
    ask = _request(1, requester="JT", responder="participant", crate=6, seq=3, active_ms=40_000)
    rows = _rows(
        requests=(ask,),
        request_episodes=(
            _episode(1, ask["request_id"], status="resolved", substantive_seq=7, closed_seq=7),
        ),
        events=(_event(7, "record_shared", active_ms=55_000),),
    )
    out = episodes.opportunity_windows(rows)
    assert out["episodes"] == 1
    assert out["opportunity_active_ms"] == 15_000


# ----- periods (INV-1105) -------------------------------------------------------------------------


def _interval(
    n: int, kind: str, start_s: float, end_s: float | None, active_start: int, reason: str = ""
) -> dict[str, Any]:
    return {
        "interval_id": _u(80_000 + n),
        "kind": kind,
        "reason": reason,
        "started_at": T0 + timedelta(seconds=start_s),
        "ended_at": None if end_s is None else T0 + timedelta(seconds=end_s),
        "active_ms_at_start": active_start,
    }


def test_period_segments_tp_pause() -> None:
    rows = _rows(
        session={"binding": {}},
        clock_intervals=(
            _interval(1, "running", 0, 60, 0, "task_open"),
            _interval(2, "paused", 60, 90, 60_000, "pressure_checkin"),
            _interval(3, "running", 90, 390, 60_000, "checkin_resume"),
        ),
    )
    summary = periods.period_summary(rows)
    assert summary["pre_checkin_active_ms"] == 60_000
    assert summary["paused_wall_ms"] == 30_000
    assert summary["post_resume_active_ms"] == 300_000
    assert summary["total_active_ms"] == 360_000
    labels = [s["label"] for s in summary["segments"]]
    assert labels == ["pre_checkin", "paused", "post_resume"]
    paused = summary["segments"][1]
    assert paused["active_ms_contributed"] == 0  # frozen, not accruing


def test_period_segments_no_checkin() -> None:
    """PE: no pause — a single pre_checkin running period; paused is honestly empty."""
    rows = _rows(
        session={"binding": {}},
        clock_intervals=(_interval(1, "running", 0, 360, 0, "task_open"),),
    )
    summary = periods.period_summary(rows)
    assert summary["paused_count"] == 0
    assert summary["paused_wall_ms"] == 0
    assert summary["post_resume_active_ms"] == 0
    assert summary["total_active_ms"] == 360_000


def test_open_segment_censored() -> None:
    rows = _rows(
        session={"binding": {}},
        clock_intervals=(_interval(1, "running", 0, None, 0, "task_open"),),
    )
    summary = periods.period_summary(rows)
    assert summary["censored"]
    assert summary["total_active_ms"] is None  # open clock: total is unknown, not 0
    now = T0 + timedelta(seconds=100)
    summary2 = periods.period_summary(rows, now=now)
    assert summary2["total_active_ms"] == 100_000


def test_channel_open_excludes_solo_review() -> None:
    phases = [
        periods.PhaseWindow("solo_review", 0, 30_000, frozenset()),
        periods.PhaseWindow(
            "task",
            30_000,
            360_000,
            frozenset({"structured_seeking", "sharing", "assistant_prompting"}),
        ),
    ]
    out = periods.channel_open_ms(phases, 360_000)
    assert out["per_channel_ms"]["structured_seeking"] == 330_000
    assert out["any_channel_open_ms"] == 330_000  # solo_review is closed time, not reluctance
    # before the task phase opens there is no help opportunity at all
    out_early = periods.channel_open_ms(phases, 15_000)
    assert out_early["any_channel_open_ms"] == 0


def test_manipulation_windows_anchor_actual_events() -> None:
    rows = _rows(
        events=(
            _event(10, "checkin_open", actor="system", active_ms=60_500),
            _event(20, "task_deadline", actor="system", active_ms=360_000),
        ),
        manipulation_exposures=(
            {
                "exposure_id": _u(90_001),
                "kind": "pressure_checkin",
                "status": "acknowledged",
                "artifact_id": "copy",
                "artifact_version": 1,
                "due_active_ms": 60_000,
                "scheduled_at": T0,
                "committed_seq": 10,
                "received_seq": None,
                "rendered_seq": None,
                "acknowledged_seq": None,
                "own_rating": 6,
                "assigned_values": {"JT": 6, "PM": 7},
            },
            {
                "exposure_id": _u(90_002),
                "kind": "one_minute_warning",
                "status": "scheduled",
                "artifact_id": "warn",
                "artifact_version": 1,
                "due_active_ms": 300_000,
                "scheduled_at": T0,
                "committed_seq": None,  # never committed: scheduled is not exposure
                "received_seq": None,
                "rendered_seq": None,
                "acknowledged_seq": None,
                "own_rating": None,
                "assigned_values": None,
            },
        ),
    )
    out = periods.manipulation_windows(rows)
    checkin, warning = out["exposures"]
    assert checkin["exposed"] and checkin["committed_active_ms"] == 60_500  # actual, not 60_000
    assert checkin["post_exposure_active_ms"] == 360_000 - 60_500
    assert checkin["own_rating_role"] == "pre_reveal_manipulation_input"
    assert not warning["exposed"]
    assert warning["committed_active_ms"] is None
    assert warning["post_exposure_active_ms"] is None  # missing stays missing


# ----- codebook -----------------------------------------------------------------------------------


def test_codebook_is_in_sync_with_generated_doc() -> None:
    doc = Path(__file__).resolve().parents[2] / "docs" / "CODEBOOK.md"
    assert doc.is_file(), (
        "docs/CODEBOOK.md missing — run `uv run python -m app.measurement.codebook`"
    )
    assert doc.read_text(encoding="utf-8") == codebook.render_codebook()


def test_codebook_covers_every_measure_state_and_rule() -> None:
    doc = codebook.render_codebook()
    assert codebook.CODEBOOK_VERSION in doc
    for state in MeasureState:
        assert state.value in doc
    for key, _ in codebook.MISSING_RULES:
        assert f"`{key}`" in doc
    for m in codebook.MEASURES:
        assert m.key.split(" /")[0].split(" (")[0] in doc
        assert m.missing_rule in {k for k, _ in codebook.MISSING_RULES}
