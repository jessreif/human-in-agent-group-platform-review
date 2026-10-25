"""INV-1111 (spec §9.3/§9.5): recompute every wide measure from long records on
hand-audited synthetic sessions — including never-arrived evidence, missing
render receipts, repeated records and unfulfilled late requests.

Each test drives a real session to a terminal state (so the committed record set
is exactly what production produces), then recomputes the measures *independently*
— straight SQL over the long-record tables, expectations written literally — and
asserts the measurement layer agrees. Where a pathology cannot be produced by a
drive (e.g. a repeat delivery), the expected value is recomputed from the raw
rows the same way.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, insert, select, update

from app.db import schema as s
from app.db.tx import db_now, execute_command
from app.domain.manifests.store import ContentStore
from app.measurement import episodes, evidence, metrics, periods, scoring
from app.measurement.rows import load_session_rows
from app.services import lifecycle

from . import audit_helpers as ah
from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _world(db: MigratedDatabase, sid: uuid.UUID) -> dict:
    with db.runtime.connect() as conn:
        return dict(
            conn.execute(
                select(s.world_snapshot.c.content).where(s.world_snapshot.c.session_id == sid)
            )
            .mappings()
            .one()["content"]
        )


def _wrong_location(truth: dict[int, str], crate: int) -> str:
    for loc in ("The Marina", "The Sawmill", "Blackwell Storage", "Riverside Depot"):
        if loc != truth[crate]:
            return loc
    raise AssertionError("no non-truth location available")


def _complete_checkin(
    db: MigratedDatabase, store: ContentStore, sid: uuid.UUID, writer: uuid.UUID
) -> None:
    ah.set_active(db, sid, 60_000)
    ah.tick(db, store)
    assert ah.stage(db, sid)[0] == "checkin_collect"
    ah.run(db, store, sid, writer, "pressure_skip", {}, ah.stage(db, sid)[1])
    ah.force_wall_due(db, sid, "checkin_reveal")
    ah.tick(db, store)
    ah.run(db, store, sid, writer, "proceed", {}, ah.stage(db, sid)[1])
    assert ah.stage(db, sid)[0] == "task"


def _request_any_crate(
    db: MigratedDatabase, store: ContentStore, sid: uuid.UUID, writer: uuid.UUID
) -> int:
    """Issue one accepted structured teammate request; returns the crate."""
    for candidate in range(1, 9):
        out = ah.run(
            db,
            store,
            sid,
            writer,
            "teammate_request",
            {"crate": candidate},
            ah.stage(db, sid)[1],
            accept=False,
        )
        if out.accepted:
            return candidate
        assert out.reason in ("no_teammate_records", "stale_revision")
    raise AssertionError("no crate had teammate records")


def _finish_to_terminal(
    db: MigratedDatabase, store: ContentStore, sid: uuid.UUID, writer: uuid.UUID
) -> None:
    """Confidence (answered crates answered, missing not ratable) -> post_task skips ->
    debrief -> data_use -> complete."""
    ah.run(db, store, sid, writer, "proceed", {}, ah.stage(db, sid)[1])
    assert ah.stage(db, sid)[0] == "confidence"
    with db.runtime.connect() as conn:
        answered = [
            r["crate"]
            for r in conn.execute(
                select(s.answer_final.c.crate).where(s.answer_final.c.session_id == sid)
            ).mappings()
        ]
    for crate in answered:
        ah.run(
            db,
            store,
            sid,
            writer,
            "item_response",
            {"item_id": "confidence", "crate": crate, "value": 65},
            ah.stage(db, sid)[1],
        )
    while ah.stage(db, sid)[0] == "confidence":
        ah.run(db, store, sid, writer, "proceed", {}, ah.stage(db, sid)[1])
    assert ah.stage(db, sid)[0] == "post_questionnaire"
    for item_id in [f"tp_post_{i:02d}" for i in range(1, 10)]:
        ah.run(
            db,
            store,
            sid,
            writer,
            "item_skip",
            {"item_id": item_id},
            ah.stage(db, sid)[1],
        )
    # BA-105: tp_demo_v3 binds no instruments.open_ended — post goes straight
    # to debrief (artifact-bound coverage lives on tp_ai_v13).
    assert ah.stage(db, sid)[0] == "debrief"
    ah.run(db, store, sid, writer, "debrief_ack", {}, ah.stage(db, sid)[1])
    ah.run(
        db,
        store,
        sid,
        writer,
        "data_use_choice",
        {"choice": "use_no_free_text"},
        ah.stage(db, sid)[1],
    )
    out = execute_command(
        db.runtime,
        ah.cmd(sid, "complete", {}, ah.stage(db, sid)[1]),
        lifecycle.completion_handler(store=store, writer=writer),
        advance_due=ah._advance(store),
    )
    assert out.accepted


def test_wide_measures_recompute_from_long_records(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """Full TP drive with a scripted participant path; every headline measure is
    recomputed here by hand (direct SQL / literal expectations) and compared to the
    measurement layer."""
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "HA1", "b-ha1")
    sid, writer = result.session_id, result.writer_id
    world = _world(migrated, sid)
    truth = {int(c["num"]): str(c["truth"]) for c in world["crates"]}
    assigned = [int(c) for c in world["assigned"]]
    assert len(assigned) == 4

    # Answers: assigned[0] correct; assigned[1] wrong then correct (one transition);
    # assigned[2] stays wrong; assigned[3] never answered.
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
        {"crate": assigned[1], "location_id": _wrong_location(truth, assigned[1])},
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
        {"crate": assigned[2], "location_id": _wrong_location(truth, assigned[2])},
        ah.stage(migrated, sid)[1],
    )

    # One structured request, delivered.
    _request_any_crate(migrated, store, sid, writer)
    deliver = next(j for j in ah.jobs(migrated, sid) if j["kind"] == "deliver")
    ah.set_active(migrated, sid, int(deliver["due_active_ms"]) + 50)
    ah.tick(migrated, store, n=5)

    # One share of an initially-held record, then the same record again (repeat
    # delivery -> redundancy, not new evidence).
    with migrated.runtime.connect() as conn:
        held = (
            conn.execute(
                select(s.file_membership.c.record_id).where(
                    s.file_membership.c.session_id == sid,
                    s.file_membership.c.source == "initial",
                )
            )
            .scalars()
            .all()
        )
    rec = str(held[0])
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "record_share",
        {"record_id": rec},
        ah.stage(migrated, sid)[1],
    )
    # UX round 2: a repeat share of the same record is an accepted idempotent
    # no-op — no second share row, no duplicate record_shared event.
    out = ah.run(
        migrated,
        store,
        sid,
        writer,
        "record_share",
        {"record_id": rec},
        ah.stage(migrated, sid)[1],
    )
    assert out.result["already"] is True

    _complete_checkin(migrated, store, sid, writer)

    # A second, deliberately late structured request — the deadline lands before
    # any substantive response can commit.
    ah.set_active(migrated, sid, 355_000)
    late_accepted = _request_any_crate(migrated, store, sid, writer)

    ah.set_active(migrated, sid, 360_000)
    ah.tick(migrated, store)
    assert ah.stage(migrated, sid)[0] == "answers_locked"
    _finish_to_terminal(migrated, store, sid, writer)

    # ----- independent recomputation -------------------------------------------------------
    with migrated.runtime.connect() as conn:
        rows = load_session_rows(conn, sid)

        # command counts: straight GROUP BY over command_receipt.
        raw_cmds = {
            (r["kind"], str(r["outcome"])): int(r["n"])
            for r in conn.execute(
                select(
                    s.command_receipt.c.kind,
                    s.command_receipt.c.outcome,
                    func.count().label("n"),
                )
                .where(s.command_receipt.c.session_id == sid)
                .group_by(s.command_receipt.c.kind, s.command_receipt.c.outcome)
            ).mappings()
        }
        expected_cmds = metrics.command_counts(rows)
        for (kind, outcome), n in raw_cmds.items():
            assert expected_cmds[kind][outcome] == n, (kind, outcome)

        # seeking: 2 accepted teammate_request commands; retries share command_ids.
        n_req = raw_cmds[("teammate_request", "accepted")]
        assert n_req == 2
        seek = metrics.seeking_metrics(rows)
        assert seek.structured_teammate_requests.value == n_req
        assert seek.structured_requests_delivered_replies.value == 1  # late one unfulfilled

        # sharing: participant-initiated shares only (teammate deliveries of records
        # are share rows too — the measure filters sharer='participant').
        n_shares = conn.execute(
            select(func.count())
            .select_from(s.share)
            .where(s.share.c.session_id == sid, s.share.c.sharer == "participant")
        ).scalar_one()
        assert n_shares == 1  # the repeat share was deduped
        share = metrics.sharing_metrics(rows)
        assert share.participant_share_events.value == n_shares

        # redundancy: delivery events (delivered messages + shares) vs distinct ids.
        raw_deliveries = [
            str(r[0])
            for r in conn.execute(select(s.share.c.record_id).where(s.share.c.session_id == sid))
        ] + [
            str(r[0])
            for r in conn.execute(
                select(s.message.c.record_id).where(
                    s.message.c.session_id == sid,
                    s.message.c.status == "delivered",
                    s.message.c.record_id.is_not(None),
                )
            )
        ]
        red = evidence.redundancy(rows)
        assert red["delivery_events"]["value"] == len(raw_deliveries)
        assert red["distinct_records"]["value"] == len(set(raw_deliveries))
        assert red["redundant_deliveries"]["value"] == len(raw_deliveries) - len(
            set(raw_deliveries)
        )
        assert raw_deliveries.count(rec) == 1

        # scoring: literal truth comparison against answer_final.
        finals = {
            int(r["crate"]): str(r["location_id"])
            for r in conn.execute(
                select(s.answer_final.c.crate, s.answer_final.c.location_id).where(
                    s.answer_final.c.session_id == sid
                )
            ).mappings()
        }
        expected_correct = sum(1 for c in assigned if finals.get(c) == truth[c])
        score = scoring.score_rows(rows)
        assert score.correct_total.numerator == expected_correct == 2
        assert score.correct_total.denominator == 4
        assert score.answered_count == 3 and score.missing_count == 1
        states = metrics.answer_states(rows)
        assert states["answered"] == 3 and states["missing"] == 1

        # correction: exactly one wrong->correct transition on assigned[1].
        corr = evidence.correction_deterioration(rows)
        assert corr["counts"] == {
            "wrong_to_correct": 1,
            "correct_to_wrong": 0,
            "unchanged_correct": 0,
            "unchanged_wrong": 0,
        }
        # never-answered spans all 8 crates (unassigned included), single-revision
        # only the two crates that got exactly one revision.
        assert corr["crates_never_answered"] == sorted(
            set(range(1, 9)) - {assigned[0], assigned[1], assigned[2]}
        )
        assert sorted(corr["crates_single_revision"]) == sorted([assigned[0], assigned[2]])

        # fulfillment: recomputed from request_episode rows — every episode counted;
        # the late request is closed without a substantive response.
        eps = (
            conn.execute(select(s.request_episode).where(s.request_episode.c.session_id == sid))
            .mappings()
            .all()
        )
        n_fulfilled = sum(1 for e in eps if e["substantive_event_seq"] is not None)
        fulfil = evidence.request_fulfillment(rows)
        assert fulfil["rate"]["denominator"] == len(eps)
        assert fulfil["rate"]["numerator"] == n_fulfilled
        # per_request is ordered by accepted_seq — the last participant request is
        # the 355 s one, closed without a substantive response.
        mine = [p for p in fulfil["per_request"] if p["requester"] == "participant"]
        assert len(mine) == 2
        assert mine[-1]["crate"] == late_accepted and not mine[-1]["fulfilled"]
        assert mine[0]["fulfilled"]

        # periods: the check-in boundary recomputed from clock_interval directly.
        intervals = (
            conn.execute(
                select(s.clock_interval)
                .where(s.clock_interval.c.session_id == sid)
                .order_by(s.clock_interval.c.started_at)
            )
            .mappings()
            .all()
        )
        pause = next(i for i in intervals if i["kind"] == "paused")
        per = periods.period_summary(rows)
        assert per["pre_checkin_active_ms"] == int(pause["active_ms_at_start"])
        assert abs(per["pre_checkin_active_ms"] - 60_000) < 1_000
        assert per["paused_count"] == 1 and per["paused_wall_ms"] > 0
        # set_active pins active_ms_at_start; the closing tick lands a few ms late.
        assert abs(per["total_active_ms"] - 360_000) < 500
        assert (
            abs(per["post_resume_active_ms"] - (360_000 - int(pause["active_ms_at_start"]))) < 500
        )

        # never-arrived evidence: teammate-held records that never reached the file
        # or a delivery — recomputed as (non-initial holdings) minus delivered.
        held_ids = {
            str(r)
            for r in conn.execute(
                select(s.seat_holding.c.record_id).where(s.seat_holding.c.session_id == sid)
            ).scalars()
        }
        file_ids = {
            str(r)
            for r in conn.execute(
                select(s.file_membership.c.record_id).where(s.file_membership.c.session_id == sid)
            ).scalars()
        }
        delivered_ids = {
            str(r)
            for r in conn.execute(
                select(s.message.c.record_id).where(
                    s.message.c.session_id == sid,
                    s.message.c.status == "delivered",
                    s.message.c.record_id.is_not(None),
                )
            ).scalars()
        } | {
            str(r)
            for r in conn.execute(
                select(s.share.c.record_id).where(s.share.c.session_id == sid)
            ).scalars()
        }
        expected_never = sorted(held_ids - file_ids - delivered_ids)
        transfer = evidence.transfer_summary(conn, sid)
        assert transfer["never_arrived"] == expected_never
        assert expected_never, "hand-audit needs a session with never-arrived evidence"

        # missing render receipts: no telemetry was ever posted, so nothing is
        # rendered — the ledger must show absence, not a zero-filled rendered stage.
        n_receipts = conn.execute(
            select(func.count())
            .select_from(s.delivery_receipt)
            .where(s.delivery_receipt.c.session_id == sid)
        ).scalar_one()
        assert n_receipts == 0
        assert transfer["stage_counts"].get("rendered", 0) == 0


def test_unfulfilled_late_request_is_expired_not_dropped(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """A participant request opened at ~355 s and overtaken by the 360 s deadline is
    retained as an expired/cancelled episode in the fulfillment denominator — never
    silently excluded, never counted as fulfilled."""
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "HA2", "b-ha2")
    sid, writer = result.session_id, result.writer_id
    _complete_checkin(migrated, store, sid, writer)
    ah.set_active(migrated, sid, 355_000)
    crate = _request_any_crate(migrated, store, sid, writer)
    ah.set_active(migrated, sid, 360_000)
    ah.tick(migrated, store)
    assert ah.stage(migrated, sid)[0] == "answers_locked"

    with migrated.runtime.connect() as conn:
        rows = load_session_rows(conn, sid)
        ep = (
            conn.execute(
                select(s.request_episode).where(
                    s.request_episode.c.session_id == sid,
                    s.request_episode.c.request_id.in_(
                        select(s.request.c.request_id).where(
                            s.request.c.session_id == sid,
                            s.request.c.requester == "participant",
                            s.request.c.crate == crate,
                        )
                    ),
                )
            )
            .mappings()
            .one()
        )
    assert ep["closed_at"] is not None and ep["substantive_event_seq"] is None
    fulfil = evidence.request_fulfillment(rows)
    mine = [p for p in fulfil["per_request"] if p["requester"] == "participant"]
    assert len(mine) == 1 and not mine[0]["fulfilled"] and not mine[0]["censored"]
    assert fulfil["rate"]["numerator"] < fulfil["rate"]["denominator"]


def test_in_answer_same_crate_post_reference_handaudit(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """BA-25 fixture on a real driven session: ``in_answer`` is a same-crate,
    post-reference association anchored at the delivered/rendered reference
    event — cross-crate revisions, pre-reference revisions, wrong-crate
    citations and never-delivered claims must never mark it.

    The deterministic QA drive never produces real assistant claims, so the
    claim rows, the render receipt and its telemetry event are hand-inserted
    (the same rows ``POST /telemetry`` and ``register_claim`` commit); answer
    revisions and the delivered reference surface come from real commands/jobs.
    """
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "HA4", "b-ha4")
    sid, writer = result.session_id, result.writer_id
    world = _world(migrated, sid)
    truth = {int(c["num"]): str(c["truth"]) for c in world["crates"]}

    # Teammate-held records on four distinct crates for the citations.
    with migrated.runtime.connect() as conn:
        held = conn.execute(
            select(s.seat_holding.c.record_id, s.record.c.crate)
            .select_from(
                s.seat_holding.join(
                    s.record,
                    (s.record.c.session_id == s.seat_holding.c.session_id)
                    & (s.record.c.record_id == s.seat_holding.c.record_id),
                )
            )
            .where(
                s.seat_holding.c.session_id == sid,
                s.seat_holding.c.holder != "participant",
            )
        ).all()
    by_crate: dict[int, str] = {}
    for rid, crate in held:
        by_crate.setdefault(int(crate), str(rid))
    crates = sorted(by_crate)
    assert len(crates) >= 4, "fixture needs teammate-held records on four crates"
    cA, cB, cC, cD = crates[:4]
    ridA, ridB, ridC, ridD = (by_crate[c] for c in (cA, cB, cC, cD))

    def in_answer() -> dict[str, list[str]]:
        with migrated.runtime.connect() as conn:
            return evidence.transfer_summary(conn, sid)["ledger"]

    # (c) A crate-A revision committed *before* the reference — must never mark
    # in_answer on its own, and the later-revision rule must inspect every
    # revision, not only this first one.
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "answer_update",
        {"crate": cA, "location_id": _wrong_location(truth, cA)},
        ah.stage(migrated, sid)[1],
    )

    # A real delivered teammate reply — the rendered reference surface (seq d1).
    _request_any_crate(migrated, store, sid, writer)
    deliver = next(
        j for j in ah.jobs(migrated, sid) if j["kind"] == "deliver" and j["status"] == "pending"
    )
    ah.set_active(migrated, sid, int(deliver["due_active_ms"]) + 50)
    ah.tick(migrated, store, n=5)

    # A second crate-A revision: post-delivery but pre-render — under the render
    # anchor it still predates the reference and must not count.
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "answer_update",
        {"crate": cA, "location_id": truth[cA]},
        ah.stage(migrated, sid)[1],
    )

    with migrated.runtime.connect() as conn:
        delivered_seq = int(
            conn.execute(
                select(func.max(s.message.c.committed_seq)).where(
                    s.message.c.session_id == sid,
                    s.message.c.status == "delivered",
                    s.message.c.actor != "participant",
                )
            ).scalar_one()
        )
        mid_seq = int(
            conn.execute(
                select(func.max(s.answer_revision.c.accepted_seq)).where(
                    s.answer_revision.c.session_id == sid,
                    s.answer_revision.c.crate == cA,
                )
            ).scalar_one()
        )
        req_seq = int(
            conn.execute(
                select(func.max(s.request.c.accepted_seq)).where(
                    s.request.c.session_id == sid,
                    s.request.c.requester == "participant",
                )
            ).scalar_one()
        )
        loc_of = {
            str(r["record_id"]): str(r["location_id"])
            for r in conn.execute(
                select(s.record.c.record_id, s.record.c.location_id).where(
                    s.record.c.session_id == sid
                )
            ).mappings()
        }
    assert delivered_seq < mid_seq

    claimA, claimB, claimX, claimC = (uuid.uuid4() for _ in range(4))
    with migrated.runtime.begin() as conn:
        now = db_now(conn)
        stage_name, rev = ah.stage(migrated, sid)
        render_seq = int(
            conn.execute(
                select(s.session.c.next_event_seq).where(s.session.c.session_id == sid)
            ).scalar_one()
        )
        assert render_seq > mid_seq
        # What POST /telemetry commits for a render (BA-05): one telemetry event
        # referencing the delivered surface plus the durable receipt row.
        conn.execute(
            insert(s.event).values(
                session_id=sid,
                seq=render_seq,
                event_id=uuid.uuid4(),
                stage=stage_name,
                actor="participant",
                type="telemetry_render",
                revision=rev,
                server_time=now,
                payload={"client_ms": 10, "data": {"seq": delivered_seq}},
            )
        )
        conn.execute(
            insert(s.delivery_receipt).values(
                session_id=sid,
                seq=delivered_seq,
                recipient="participant",
                kind="initial",
                received_at=now,
                rendered_at=now,
            )
        )
        conn.execute(
            update(s.session)
            .where(s.session.c.session_id == sid)
            .values(next_event_seq=render_seq + 1)
        )
        for claim_id, crate, ctx_seq, rid in (
            (claimA, cA, delivered_seq, ridA),  # rendered surface -> render anchor
            (claimB, cB, req_seq, ridB),  # no receipt -> delivered-commit fallback
            (claimX, cA, req_seq, ridD),  # wrong-crate citation of ridD
            (claimC, cC, None, ridC),  # no reference event at all
        ):
            conn.execute(
                insert(s.claim).values(
                    session_id=sid,
                    claim_id=claim_id,
                    crate=crate,
                    location_id=loc_of[rid],
                    event_type="arrival",
                    assertion="asserted",
                    support_status="supported",
                    issuer="assistant",
                    context_event_seq=ctx_seq,
                    created_at=now,
                )
            )
            conn.execute(
                insert(s.claim_source).values(
                    session_id=sid,
                    claim_source_id=uuid.uuid4(),
                    claim_id=claim_id,
                    source_kind="record",
                    record_id=rid,
                )
            )

    # Render anchor in force: both crate-A revisions predate the render event —
    # the pre-delivery one and the post-delivery/pre-render one alike. Nothing
    # else has a qualifying revision yet.
    stages = in_answer()
    for rid in (ridA, ridB, ridC, ridD):
        assert "in_answer" not in stages.get(rid, ())

    # (b)+(d): a crate-B revision is post-reference only for the crate-B claim
    # (whose anchor fell back to the declared context commit seq — no receipt).
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "answer_update",
        {"crate": cB, "location_id": truth[cB]},
        ah.stage(migrated, sid)[1],
    )
    stages = in_answer()
    assert "in_answer" in stages[ridB]
    assert "in_answer" not in stages.get(ridA, ())  # a crate-B revision is not crate-A use
    assert "in_answer" not in stages.get(ridD, ())  # wrong-crate citation never joins
    assert "in_answer" not in stages.get(ridC, ())

    # A crate-C revision cannot help the never-delivered claim: the reference
    # event is missing, not seq 0.
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "answer_update",
        {"crate": cC, "location_id": truth[cC]},
        ah.stage(migrated, sid)[1],
    )
    stages = in_answer()
    assert "in_answer" not in stages.get(ridC, ())

    # (a) A post-render revision on crate A finally marks the cited record.
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "answer_update",
        {"crate": cA, "location_id": _wrong_location(truth, cA)},
        ah.stage(migrated, sid)[1],
    )
    stages = in_answer()
    assert "in_answer" in stages[ridA]
    assert "in_answer" in stages[ridB]
    assert "in_answer" not in stages.get(ridC, ())
    assert "in_answer" not in stages.get(ridD, ())


def test_pending_overlap_unions_not_sums(migrated: MigratedDatabase, store: ContentStore) -> None:
    """INV-1104 on a real session: two overlapping pending windows contribute their
    union, never a double-counted sum (spec §9.3)."""
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "HA3", "b-ha3")
    sid, writer = result.session_id, result.writer_id
    _complete_checkin(migrated, store, sid, writer)
    # Two open-ended pending windows over the same span must union to one span.
    _request_any_crate(migrated, store, sid, writer)
    _request_any_crate(migrated, store, sid, writer)

    intervals = [(100.0, 300.0), (150.0, 250.0), (400.0, 500.0)]
    assert episodes.union_total(intervals) == 300.0

    with migrated.runtime.connect() as conn:
        rows = load_session_rows(conn, sid)
    overlap = episodes.pending_overlap(rows, requester="participant")
    # Two open episodes: the union never exceeds the naive sum, is nonzero while
    # requests are pending, and the wall union is censored (None), not zero.
    assert overlap["episodes"] == 2 and overlap["censored"] == 2
    assert 0 < overlap["pending_active_union_ms"] <= overlap["summed_pending_active_ms"]
    assert overlap["pending_wall_union_ms"] is None
