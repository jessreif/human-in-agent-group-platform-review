"""INV-1101..1105 measurement layer against committed records on disposable PostgreSQL.

Sessions are driven through the real command/job machinery (``audit_helpers``), so every
row the measures read is a genuinely committed record — events, requests, episodes,
shares, answers, exposures, clock intervals. Measures then run through the read-only
``research`` engine, which doubles as the proof that measurement (and especially
scoring) holds no write authority over answers.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy import select, text

from app.db import schema as s
from app.db.tx import Command, execute_command
from app.domain.manifests.store import ContentStore
from app.measurement import episodes, evidence, metrics, periods, scoring
from app.measurement.results import MeasureState
from app.measurement.rows import SessionRows, load_session_rows
from app.services import lifecycle, scheduler, worlds
from app.timing.policy import resolve_policy

from . import audit_helpers as ah
from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _advance(store: ContentStore):
    def advance(ctx: Any) -> None:
        bound = lifecycle.bound_for_session(ctx.conn, store, ctx.session)
        scheduler.advance_due(ctx, bound)

    return advance


def _command(sid: uuid.UUID, kind: str, payload: dict[str, Any], revision: int) -> Command:
    return Command(
        command_id=uuid.uuid4(),
        session_id=sid,
        kind=kind,
        expected_revision=revision,
        payload=payload,
    )


def _resubmit(
    db: MigratedDatabase, store: ContentStore, sid: uuid.UUID, writer: uuid.UUID, cmd: Command
) -> Any:
    """Re-execute the *same* command object (same command_id) — the HTTP-retry path."""
    return execute_command(
        db.runtime,
        cmd,
        lifecycle.handler_for(cmd.kind, store=store, behavior_sha=ah.BUILD_SHA, writer=writer),
        advance_due=_advance(store),
    )


def _rows(db: MigratedDatabase, sid: uuid.UUID) -> SessionRows:
    """Load the session through the *research* role: SELECT-only authority."""
    with db.research.connect() as conn:
        return load_session_rows(conn, sid)


def _world(db: MigratedDatabase, sid: uuid.UUID) -> Mapping[str, Any]:
    with db.runtime.connect() as conn:
        return worlds.snapshot_content(conn, sid)


def _unassigned_file_record(db: MigratedDatabase, sid: uuid.UUID) -> tuple[str, int]:
    """A participant-held record on an unassigned crate (the useful late entries)."""
    assigned = set(_world(db, sid)["assigned"])
    with db.runtime.connect() as conn:
        row = (
            conn.execute(
                select(s.file_membership.c.record_id, s.record.c.crate)
                .select_from(
                    s.file_membership.join(
                        s.record,
                        (s.record.c.session_id == s.file_membership.c.session_id)
                        & (s.record.c.record_id == s.file_membership.c.record_id),
                    )
                )
                .where(s.file_membership.c.session_id == sid)
            )
            .mappings()
            .all()
        )
    unassigned = [r for r in row if r["crate"] not in assigned]
    assert unassigned
    return str(unassigned[0]["record_id"]), int(unassigned[0]["crate"])


def _complete_checkin(
    db: MigratedDatabase, store: ContentStore, sid: uuid.UUID, writer: uuid.UUID
) -> None:
    ah.set_active(db, sid, 60_000)
    ah.tick(db, store)
    assert ah.stage(db, sid)[0] == "checkin_collect"
    ah.run(db, store, sid, writer, "pressure_response", {"value": 6}, ah.stage(db, sid)[1])
    ah.force_wall_due(db, sid, "checkin_reveal")
    ah.tick(db, store)
    ah.run(db, store, sid, writer, "proceed", {}, ah.stage(db, sid)[1])
    assert ah.stage(db, sid)[0] == "task"


def _drive_to_lock(
    db: MigratedDatabase, store: ContentStore, sid: uuid.UUID, writer: uuid.UUID
) -> None:
    """Jump the active clock to the deadline, completing the TP check-in if it fires."""
    ah.set_active(db, sid, 360_000)
    ah.tick(db, store, n=5)
    if ah.stage(db, sid)[0] == "checkin_collect":
        ah.run(db, store, sid, writer, "pressure_response", {"value": 5}, ah.stage(db, sid)[1])
        ah.force_wall_due(db, sid, "checkin_reveal")
        ah.tick(db, store)
        ah.run(db, store, sid, writer, "proceed", {}, ah.stage(db, sid)[1])
        # the open running interval resumes at its frozen elapsed; jump it again
        ah.set_active(db, sid, 360_000)
        ah.tick(db, store, n=5)
    assert ah.stage(db, sid)[0] == "answers_locked"


# ----- INV-1102: exact-event metrics -------------------------------------------------------------


def test_retry_never_inflates_counts(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "M1", "b-m1")
    sid, writer = result.session_id, result.writer_id
    crate = _unassigned_file_record(migrated, sid)[1]
    # a crate no teammate holds would be rejected; find one that accepts
    cmd = None
    for c in range(1, 9):
        trial = _command(sid, "teammate_request", {"crate": c}, ah.stage(migrated, sid)[1])
        out = execute_command(
            migrated.runtime,
            trial,
            lifecycle.handler_for(
                "teammate_request", store=store, behavior_sha=ah.BUILD_SHA, writer=writer
            ),
            advance_due=_advance(store),
        )
        if out.accepted:
            cmd = trial
            break
    assert cmd is not None
    resub = _resubmit(migrated, store, sid, writer, cmd)
    assert resub.accepted  # idempotent replay returns the stored result
    # a genuinely new attempt with a stale revision is rejected — and receipted
    stale = _resubmit(
        migrated,
        store,
        sid,
        writer,
        Command(
            command_id=uuid.uuid4(),
            session_id=sid,
            kind="teammate_request",
            expected_revision=1,  # stale
            payload={"crate": crate},
        ),
    )
    assert not stale.accepted
    counts = metrics.command_counts(_rows(migrated, sid))
    assert counts["teammate_request"]["accepted"] == 1
    assert counts["teammate_request"]["rejected"] == 1


def test_seeking_ai_sharing_end_to_end(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "M2", "b-m2")
    sid, writer = result.session_id, result.writer_id
    # put a real active position on the clock before the share so its committed
    # delivered_at maps inside the (hook-adjusted) interval's wall span
    ah.set_active(migrated, sid, 1_500)
    record_id, _crate = _unassigned_file_record(migrated, sid)
    # proactive share: no solicitation for this crate has been delivered yet
    ah.run(
        db=migrated,
        store=store,
        sid=sid,
        writer=writer,
        kind="record_share",
        payload={"record_id": record_id},
        revision=ah.stage(migrated, sid)[1],
    )
    # one typed and one picker-route assistant prompt
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "assistant_request",
        {"body": "what do we know about crate 3?", "crate": 3},
        ah.stage(migrated, sid)[1],
    )
    ah.run(
        migrated, store, sid, writer, "assistant_request", {"crate": 4}, ah.stage(migrated, sid)[1]
    )
    # one structured teammate request
    asked = None
    for c in range(1, 9):
        out = ah.run(
            migrated,
            store,
            sid,
            writer,
            "teammate_request",
            {"crate": c},
            ah.stage(migrated, sid)[1],
            accept=False,
        )
        if out.accepted:
            asked = c
            break
    assert asked is not None
    # first-action latencies while the share's wall stamp still maps into the
    # committed interval (later clock jumps rewrite the open interval's span)
    times = metrics.first_action_times(_rows(migrated, sid))
    assert times["first_share_ms"].measured
    assert times["first_share_ms"].value == pytest.approx(1_500, abs=1_000)
    assert times["first_request_or_share_ms"].measured
    deliver = ah.job(migrated, sid, "deliver")
    assert deliver is not None
    ah.set_active(migrated, sid, int(deliver["due_active_ms"]) + 50)
    ah.tick(migrated, store, n=4)

    rows = _rows(migrated, sid)
    m = metrics.session_metrics(rows)
    assert m["seeking"]["structured_teammate_requests"]["value"] == 1
    assert m["seeking"]["structured_requests_delivered_replies"]["value"] == 1
    assert m["ai_use"]["prompts"].value == 2
    assert m["ai_use"]["by_route"] == {"typed": 1, "picker": 1}
    assert m["sharing"]["participant_share_events"]["value"] == 1
    assert m["sharing"]["proactive_shares_strict"]["value"] == 1
    assert m["sharing"]["proactive_shares_legacy"]["value"] == 1
    alloc = metrics.seeking_allocation(rows)
    assert alloc.state is MeasureState.MEASURED
    assert alloc.value == pytest.approx(2 / 3)


def test_proactive_share_after_solicitation_is_not_proactive(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "M3", "b-m3")
    sid, writer = result.session_id, result.writer_id
    # Let the 40 s scripted ask commit — it is a delivered agent solicitation.
    ah.set_active(migrated, sid, 40_500)
    ah.tick(migrated, store)
    scripted = [r for r in _rows(migrated, sid).requests if r["responder"] == "participant"]
    assert scripted, "scripted ask should have committed"
    scripted_crates = {int(r["crate"]) for r in scripted if r["crate"] is not None}
    # a participant-held record on a scripted crate: sharing it now answers the ask
    # (prompted) — and any *further* unprompted record on that crate is not proactive
    with migrated.runtime.connect() as conn:
        rows = (
            conn.execute(
                select(s.file_membership.c.record_id, s.record.c.crate)
                .select_from(
                    s.file_membership.join(
                        s.record,
                        (s.record.c.session_id == s.file_membership.c.session_id)
                        & (s.record.c.record_id == s.file_membership.c.record_id),
                    )
                )
                .where(s.file_membership.c.session_id == sid)
            )
            .mappings()
            .all()
        )
    target = next(r for r in rows if r["crate"] in scripted_crates)
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "record_share",
        {"record_id": str(target["record_id"])},
        ah.stage(migrated, sid)[1],
    )
    m = metrics.sharing_metrics(_rows(migrated, sid))
    assert m.participant_share_events.value == 1
    assert m.proactive_shares_strict.value == 0
    assert m.proactive_shares_legacy.value == 0


# ----- scoring: read-only authority over frozen answers -------------------------------------------


def test_scoring_reads_frozen_answers_and_writes_nothing(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "M4", "b-m4")
    sid, writer = result.session_id, result.writer_id
    with migrated.runtime.connect() as conn:
        truth = worlds.world_truth(conn, sid)
    assigned = [int(c) for c in _world(migrated, sid)["assigned"]]
    # answer: crate[0] correct, crate[1] wrong then corrected, crate[2] wrong, crate[3] missing
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "answer_update",
        {"crate": assigned[0], "location_id": truth[assigned[0]]},
        ah.stage(migrated, sid)[1],
    )
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "answer_update",
        {"crate": assigned[1], "location_id": "wrong"},
        ah.stage(migrated, sid)[1],
    )
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "answer_update",
        {"crate": assigned[1], "location_id": truth[assigned[1]]},
        ah.stage(migrated, sid)[1],
    )
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "answer_update",
        {"crate": assigned[2], "location_id": "wrong"},
        ah.stage(migrated, sid)[1],
    )
    _drive_to_lock(migrated, store, sid, writer)

    def _answer_tables() -> tuple[list[Any], list[Any]]:
        with migrated.runtime.connect() as conn:
            rev = conn.execute(
                select(s.answer_revision)
                .where(s.answer_revision.c.session_id == sid)
                .order_by(s.answer_revision.c.crate, s.answer_revision.c.revision)
            ).all()
            fin = conn.execute(
                select(s.answer_final).where(s.answer_final.c.session_id == sid)
            ).all()
        return [tuple(r) for r in rev], [tuple(r) for r in fin]

    before = _answer_tables()
    with migrated.research.connect() as conn:
        score = scoring.score_session(conn, sid)  # read-only role: any write would fail
        bundle = metrics.session_metrics(load_session_rows(conn, sid))
    after = _answer_tables()
    assert before == after, "scoring must never write to the answer tables"

    assert score.correct_total.value == pytest.approx(2 / 4)
    assert score.accuracy_answered_only.value == pytest.approx(2 / 3)
    assert score.missing_count == 1
    states = bundle["answers"]["per_crate"]
    assert states[assigned[3]] == "missing"
    cd = evidence.correction_deterioration(_rows(migrated, sid))
    assert cd["counts"]["wrong_to_correct"] == 1
    assert cd["counts"]["correct_to_wrong"] == 0
    assert cd["correction_rate"]["value"] == pytest.approx(1 / 1)


def test_deadline_persists_scored_result(migrated: MigratedDatabase, store: ContentStore) -> None:
    """The freeze commits the durable scored ``result`` row — dashboard analytic
    eligibility keys on it, so a completed task must never leave it missing."""
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "M4b", "b-m4b")
    sid, writer = result.session_id, result.writer_id
    with migrated.runtime.connect() as conn:
        truth = worlds.world_truth(conn, sid)
    assigned = [int(c) for c in _world(migrated, sid)["assigned"]]
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "answer_update",
        {"crate": assigned[0], "location_id": truth[assigned[0]]},
        ah.stage(migrated, sid)[1],
    )
    _drive_to_lock(migrated, store, sid, writer)
    with migrated.research.connect() as conn:
        row = conn.execute(select(s.result).where(s.result.c.session_id == sid)).mappings().one()
        freeze_seq = conn.execute(
            select(s.answer_final.c.finalized_seq)
            .where(s.answer_final.c.session_id == sid)
            .limit(1)
        ).scalar_one()
    assert row["status"] == "scored"
    assert row["scoring_version"] == scoring.SCORING_VERSION
    assert row["correct_total"] == 1
    assert row["correct_total"] == row["correct_teamdep"] + row["correct_ownfile"]
    assert row["finalized_seq"] == freeze_seq


def test_confidence_is_answered_only_and_never_defaulted(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "M5", "b-m5")
    sid, writer = result.session_id, result.writer_id
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "answer_update",
        {"crate": 1, "location_id": "H1"},
        ah.stage(migrated, sid)[1],
    )
    _drive_to_lock(migrated, store, sid, writer)
    ah.run(migrated, store, sid, writer, "proceed", {}, ah.stage(migrated, sid)[1])
    assert ah.stage(migrated, sid)[0] == "confidence"
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "item_response",
        {"item_id": "confidence", "crate": 1, "value": 70},
        ah.stage(migrated, sid)[1],
    )
    conf = metrics.confidence(_rows(migrated, sid))
    assert conf == {
        1: {
            "status": "answered",
            "value": 70,
            "revision": 1,
            "instrument_version": conf[1]["instrument_version"],
        }
    }
    # never-answered crates carry no row at all — missing, never the slider's 50
    assert set(conf) == {1}


# ----- INV-1103/1104: evidence + episodes -------------------------------------------


def test_fulfillment_and_pending_union(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "M6", "b-m6")
    sid, writer = result.session_id, result.writer_id
    # two concurrent pending asks, one per seat (per-seat pending caps stay satisfied)
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "social_reply",
        {"body": "hi JT", "to": "JT"},
        ah.stage(migrated, sid)[1],
    )
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "social_reply",
        {"body": "hi PM", "to": "PM"},
        ah.stage(migrated, sid)[1],
    )
    ah.set_active(migrated, sid, 2_000)  # let the pending windows accumulate a span
    rows = _rows(migrated, sid)
    pend = episodes.pending_overlap(rows, requester="participant")
    assert pend["episodes"] == 2
    assert pend["censored"] == 2  # both open at the horizon
    # overlap: the union is strictly less than the naive double-counting sum
    assert pend["pending_active_union_ms"] < pend["summed_pending_active_ms"]
    # resolve one of them: run generation, then the scheduled deliver job
    ah.force_wall_due(migrated, sid, "generate")
    ah.tick(migrated, store, n=4)
    deliver = ah.job(migrated, sid, "deliver")
    assert deliver is not None
    ah.set_active(migrated, sid, int(deliver["due_active_ms"]) + 50)
    ah.tick(migrated, store, n=4)
    rows2 = _rows(migrated, sid)
    ful = evidence.request_fulfillment(rows2)
    resolved = [p for p in ful["per_request"] if p["fulfilled"]]
    assert resolved, "delivered request must count as fulfilled"
    with migrated.runtime.connect() as conn:
        table = episodes.episode_table(conn, sid)
    assert any(e["status"] == "resolved" for e in table["episodes"])


def test_evidence_bundle_on_driven_session(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "M7", "b-m7")
    sid, writer = result.session_id, result.writer_id
    asked = None
    for c in range(1, 9):
        out = ah.run(
            migrated,
            store,
            sid,
            writer,
            "teammate_request",
            {"crate": c},
            ah.stage(migrated, sid)[1],
            accept=False,
        )
        if out.accepted:
            asked = c
            break
    assert asked is not None
    deliver = ah.job(migrated, sid, "deliver")
    ah.set_active(migrated, sid, int(deliver["due_active_ms"]) + 50)
    ah.tick(migrated, store, n=4)
    # step through the scripted asks (40/150/240 s) — a single jump to the deadline
    # would cancel them before they could commit, exactly as a real session would
    _complete_checkin(migrated, store, sid, writer)
    for ms in (160_000, 250_000, 360_000):
        ah.set_active(migrated, sid, ms)
        ah.tick(migrated, store, n=5)
    assert ah.stage(migrated, sid)[0] == "answers_locked"
    with migrated.research.connect() as conn:
        bundle = evidence.evidence_measures(conn, sid)
    transfer = bundle["transfer"]
    assert transfer["stage_counts"].get("delivered", 0) >= 1
    assert transfer["stage_counts"].get("in_file", 0) >= 1
    assert "never" in transfer["stage_counts"]  # teammate-held records never sent
    assert bundle["decisive_evidence"]["rate"]["denominator"] == 2
    assert bundle["request_fulfillment"]["rate"]["denominator"] >= 4  # scripted + participant
    assert bundle["redundancy"]["delivery_events"]["value"] >= 1


# ----- INV-1105: periods and exposure ------------------------------------------------


def test_tp_periods_and_checkin_exposure(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "M8", "b-m8")
    sid, writer = result.session_id, result.writer_id
    _complete_checkin(migrated, store, sid, writer)
    rows = _rows(migrated, sid)
    with migrated.runtime.connect() as conn:
        now = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
    summary = periods.period_summary(rows, now=now)
    assert summary["paused_count"] == 1
    assert summary["paused_wall_ms"] is not None and summary["paused_wall_ms"] > 0
    assert summary["pre_checkin_active_ms"] == pytest.approx(60_000, abs=2_000)
    # resumed and running again: a small honest post-resume span, not a jump
    assert summary["post_resume_active_ms"] is not None
    assert 0 <= summary["post_resume_active_ms"] < 60_000
    windows = periods.manipulation_windows(rows)
    checkin = next(e for e in windows["exposures"] if e["kind"] == "pressure_checkin")
    assert checkin["exposed"]
    assert checkin["committed_active_ms"] == pytest.approx(60_000, abs=2_000)
    assert checkin["own_rating"] == 6
    assert checkin["own_rating_role"] == "pre_reveal_manipulation_input"
    # the warning exposure exists only as scheduled until it actually commits
    warning = [e for e in windows["exposures"] if e["kind"] == "one_minute_warning"]
    assert not warning or not warning[0]["exposed"]


def test_warning_exposure_anchors_actual_commit(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "M9", "b-m9")
    sid, writer = result.session_id, result.writer_id
    _complete_checkin(migrated, store, sid, writer)
    ah.set_active(migrated, sid, 300_000)
    ah.tick(migrated, store, n=3)
    windows = periods.manipulation_windows(_rows(migrated, sid))
    warning = next(e for e in windows["exposures"] if e["kind"] == "one_minute_warning")
    assert warning["exposed"]
    assert warning["committed_active_ms"] >= 300_000
    assert warning["due_active_ms"] == 300_000  # intended vs actual kept separate


def test_pe_session_has_no_pause_and_commits_pe_exposures(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = ah.drive_to_task(migrated, store, "pe_demo_v3", "P1", "b-p1")
    sid, writer = result.session_id, result.writer_id
    _drive_to_lock(migrated, store, sid, writer)
    rows = _rows(migrated, sid)
    summary = periods.period_summary(rows)
    assert summary["paused_count"] == 0
    assert summary["total_active_ms"] is not None and summary["total_active_ms"] >= 360_000
    windows = periods.manipulation_windows(rows)
    kinds = {e["kind"] for e in windows["exposures"]}
    assert "pressure_checkin" not in kinds
    # PE arm surfaces committed at their stages (intro on task open / arm surfaces)
    assert kinds & {"pe_intro", "pe_callout", "pe_strip", "pe_system_message", "peer_intro"}


def test_channel_open_excludes_solo_review_phase(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """tp_demo_v1: the task phase's declared windows bound channel-open time; the measure
    reads the resolved policy's phases, so closed phases contribute exactly 0."""
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "P2", "b-p2")
    sid = result.session_id
    ah.set_active(migrated, sid, 2_000)  # put real active time on the clock
    with migrated.runtime.connect() as conn:
        bound = lifecycle.bound_for_session(conn, store, _session_row(conn, sid))
    arm = str(_rows(migrated, sid).session["arm"] or "")
    policy = resolve_policy(bound, arm or bound.manifest.arms[0])
    phases = periods.phase_windows_from_policy(policy)
    rows = _rows(migrated, sid)
    out = periods.channel_open_ms(phases, rows.active_end_ms)
    assert out["active_end_ms"] is not None and out["active_end_ms"] < 60_000
    assert out["per_channel_ms"].get("structured_seeking", 0) > 0
    # tp_demo_v1 declares no closed (solo_review) phase in the task segment
    assert not any(p.name == "solo_review" and not p.enabled for p in policy.phases)


def _session_row(conn: Any, sid: uuid.UUID) -> Mapping[str, Any]:
    return dict(
        conn.execute(select(s.session).where(s.session.c.session_id == sid)).mappings().one()
    )
