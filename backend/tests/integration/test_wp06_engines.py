"""INV-0601-0612: scheduler and world-engine behavior against disposable PostgreSQL.

Active time is driven by setting the open running interval's ``active_ms_at_start`` (and
refreshing ``started_at``), which is exactly what the ledger contributes for a real running
interval — no virtual clocks are involved. ``claimable_at`` is reset along with it because
that column is only a wall-time *estimate*; due-ness is what the tests then exercise.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy import func, insert, select, text, update

from app.db import schema as s
from app.db.tx import Command, CommandResult, TxContext, execute_command
from app.domain.manifests.store import ContentStore
from app.services import activetime, engines, lifecycle, scheduler
from app.services.entry import EntryResult, enter
from app.workers.main import record_outage_incidents

from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration

BUILD_SHA = "ab" * 32


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _seed_batch(db: MigratedDatabase, store: ContentStore, manifest_id: str, batch_id: str) -> None:
    from app.domain.manifests.binding import bind
    from app.services.sessions import register_manifest

    bound = bind(store, manifest_id, mode="qa")
    with db.superuser.begin() as conn:
        register_manifest(conn, bound)
        conn.execute(
            insert(s.batch).values(
                batch_id=batch_id,
                study_key=bound.manifest.study_key,
                manifest_id=bound.manifest_id,
                mode="qa",
                enrollment_open=True,
            )
        )


def _query(pid: str, batch: str) -> dict[str, str]:
    return {"PROLIFIC_PID": pid, "STUDY_ID": "s", "SESSION_ID": "x", "batch": batch}


def _cmd(session_id: uuid.UUID, kind: str, payload: dict[str, object], revision: int) -> Command:
    return Command(
        command_id=uuid.uuid4(),
        session_id=session_id,
        kind=kind,
        expected_revision=revision,
        payload=payload,
    )


def _advance(store: ContentStore):
    def advance(ctx: TxContext) -> None:
        bound = lifecycle.bound_for_session(ctx.conn, store, ctx.session)
        scheduler.advance_due(ctx, bound)

    return advance


def _run(
    db: MigratedDatabase,
    store: ContentStore,
    sid: uuid.UUID,
    writer: uuid.UUID,
    kind: str,
    payload: dict[str, object],
    revision: int,
    *,
    accept: bool = True,
) -> CommandResult:
    out = execute_command(
        db.runtime,
        _cmd(sid, kind, payload, revision),
        lifecycle.handler_for(kind, store=store, behavior_sha=BUILD_SHA, writer=writer),
        advance_due=_advance(store),
    )
    if accept:
        assert out.accepted, (kind, out.reason)
    return out


def _stage(db: MigratedDatabase, sid: uuid.UUID) -> tuple[str, int]:
    with db.runtime.connect() as conn:
        row = conn.execute(
            select(s.session.c.stage, s.session.c.revision).where(s.session.c.session_id == sid)
        ).one()
        return row.stage, row.revision


def _drive_to_task(
    db: MigratedDatabase,
    store: ContentStore,
    manifest_id: str,
    pid: str,
    batch: str,
    seed_hex: str | None = None,
) -> EntryResult:
    _seed_batch(db, store, manifest_id, batch)
    result = enter(
        db.runtime,
        store,
        query=_query(pid, batch),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
        seed_hex=seed_hex,
    )
    sid, writer = result.session_id, result.writer_id
    _run(
        db,
        store,
        sid,
        writer,
        "consent",
        {"action": "consented", "text_version": "consent.reference@v3"},
        1,
    )
    _run(db, store, sid, writer, "training_step", {"step": 1}, _stage(db, sid)[1])
    # BA-95: completion requires a committed practice share first.
    _run(
        db,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "share", "record_id": "PRACTICE-K3-04"},
        _stage(db, sid)[1],
    )
    _run(
        db,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "pick_location", "crate": 3, "location": "The Sawmill"},
        _stage(db, sid)[1],
    )

    _run(
        db,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "ask", "text": "anything on crate 3?"},
        _stage(db, sid)[1],
    )
    _run(
        db,
        store,
        sid,
        writer,
        "training_step",
        {
            "step": "comprehension",
            "complete": True,
            "answers": dict(lifecycle.COMPREHENSION_KEY),
        },
        _stage(db, sid)[1],
    )
    _drive_connecting(db, store, sid, writer)
    return result


def _drive_connecting(
    db: MigratedDatabase, store: ContentStore, sid: uuid.UUID, writer: uuid.UUID
) -> None:
    """INV-0701: PM joins at the wall-clock connecting job, Start gates on it, and the task
    opens (clock starts) when the post-start hold job commits."""
    _force_wall_due(db, sid, "connecting")
    _tick(db, store)
    _run(db, store, sid, writer, "ready", {}, _stage(db, sid)[1])
    assert _stage(db, sid)[0] == "ready"
    _force_wall_due(db, sid, "task_open")
    _tick(db, store)
    assert _stage(db, sid)[0] in ("task", "solo_review")


def _set_active(db: MigratedDatabase, sid: uuid.UUID, active_ms: int) -> None:
    """Jump the session's open running interval to ``active_ms`` elapsed (see module doc)."""
    with db.runtime.begin() as conn:
        conn.execute(
            text(
                "UPDATE app.clock_interval "
                "SET active_ms_at_start = :ms, started_at = clock_timestamp()"
                " WHERE session_id = :sid AND ended_at IS NULL AND kind = 'running'"
            ),
            {"ms": active_ms, "sid": sid},
        )
        conn.execute(
            text(
                "UPDATE app.scheduled_job SET claimable_at = clock_timestamp()"
                " WHERE session_id = :sid AND status IN ('pending') AND clock_basis = 'active_task'"
            ),
            {"sid": sid},
        )


def _active(db: MigratedDatabase, sid: uuid.UUID) -> int:
    with db.runtime.connect() as conn:
        return activetime.active_ms(
            conn, sid, conn.execute(text("SELECT clock_timestamp()")).scalar_one()
        )


def _open_interval(db: MigratedDatabase, sid: uuid.UUID) -> Mapping[str, Any] | None:
    with db.runtime.connect() as conn:
        return activetime.open_interval(conn, sid)


def _tick(db: MigratedDatabase, store: ContentStore, *, worker: str = "w1", n: int = 1) -> int:
    total = 0
    for _ in range(n):
        committed = scheduler.tick(db.runtime, store, worker_id=worker, provider=None, limit=25)
        if committed == 0:
            break
        total += committed
    return total


def _force_wall_due(db: MigratedDatabase, sid: uuid.UUID, kind: str) -> None:
    with db.runtime.begin() as conn:
        conn.execute(
            update(s.scheduled_job)
            .where(
                s.scheduled_job.c.session_id == sid,
                s.scheduled_job.c.kind == kind,
                s.scheduled_job.c.status == "pending",
            )
            .values(due_wall_at=func.now(), claimable_at=func.now())
        )


def _jobs(db: MigratedDatabase, sid: uuid.UUID) -> list[Mapping[str, Any]]:
    with db.runtime.connect() as conn:
        return (
            conn.execute(select(s.scheduled_job).where(s.scheduled_job.c.session_id == sid))
            .mappings()
            .all()
        )


def _job(db: MigratedDatabase, sid: uuid.UUID, kind: str) -> Mapping[str, Any] | None:
    return next((j for j in _jobs(db, sid) if j["kind"] == kind), None)


def _messages(db: MigratedDatabase, sid: uuid.UUID) -> list[Mapping[str, Any]]:
    with db.runtime.connect() as conn:
        return conn.execute(select(s.message).where(s.message.c.session_id == sid)).mappings().all()


def _events(db: MigratedDatabase, sid: uuid.UUID, type_: str) -> list[Mapping[str, Any]]:
    with db.runtime.connect() as conn:
        return (
            conn.execute(
                select(s.event).where(s.event.c.session_id == sid, s.event.c.type == type_)
            )
            .mappings()
            .all()
        )


def _complete_checkin(
    db: MigratedDatabase, store: ContentStore, sid: uuid.UUID, writer: uuid.UUID
) -> None:
    """Drive a TP session through the 60 s check-in and back to ``task`` at ~60 s."""
    _set_active(db, sid, 60_000)
    _tick(db, store)
    assert _stage(db, sid)[0] == "checkin_collect"
    _run(db, store, sid, writer, "pressure_skip", {}, _stage(db, sid)[1])
    assert _stage(db, sid)[0] == "checkin_wait"
    _force_wall_due(db, sid, "checkin_reveal")
    _tick(db, store)
    assert _stage(db, sid)[0] == "checkin_reveal"
    _run(db, store, sid, writer, "proceed", {}, _stage(db, sid)[1])
    assert _stage(db, sid)[0] == "task"


# ----- INV-0603/0604: the durable schedule ------------------------------------------------------


def test_task_schedule_committed_at_ready(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_v3", "S1", "b-s1")
    jobs = _jobs(migrated, result.session_id)
    # INV-0701: the connecting/task_open wall jobs committed before the clock existed.
    wall = {j["kind"] for j in jobs if j["clock_basis"] == "wall"}
    assert wall == {"connecting", "task_open"}
    active = [j for j in jobs if j["clock_basis"] == "active_task"]
    kinds = sorted(j["kind"] for j in active)
    assert kinds == [
        "checkin",
        "deadline",
        "greeting_prompt",
        "scripted_request",
        "scripted_request",
        "scripted_request",
        "warning",
    ]
    assert all(j["status"] == "pending" for j in active)
    due = {j["kind"]: j["due_active_ms"] for j in jobs}
    assert due["warning"] == 300_000 and due["deadline"] == 360_000 and due["checkin"] == 60_000
    assert _events(migrated, result.session_id, "task_schedule_committed")


def test_pe_schedule_has_no_checkin(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = _drive_to_task(migrated, store, "pe_demo_v3", "S2", "b-s2")
    assert _stage(migrated, result.session_id)[0] == "task"
    kinds = [j["kind"] for j in _jobs(migrated, result.session_id)]
    assert "checkin" not in kinds and "warning" in kinds and "deadline" in kinds


def test_scripted_request_delivers_on_active_axis(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_v3", "S3", "b-s3")
    sid = result.session_id
    _set_active(migrated, sid, 40_500)  # first scripted request is at active 40 s
    _tick(migrated, store)
    with migrated.runtime.connect() as conn:
        req = (
            conn.execute(
                select(s.request).where(
                    s.request.c.session_id == sid,
                    s.request.c.requester.in_(("JT", "PM")),
                )
            )
            .mappings()
            .one()
        )
        episode = (
            conn.execute(
                select(s.request_episode).where(s.request_episode.c.request_id == req["request_id"])
            )
            .mappings()
            .one()
        )
    assert req["responder"] == "participant" and episode["status"] == "pending"
    msgs = _messages(migrated, sid)
    assert any(m["actor"] in ("JT", "PM") and m["request_id"] == req["request_id"] for m in msgs)
    _tick(migrated, store)  # a second pass must not redeliver
    assert len(_messages(migrated, sid)) == len(msgs)


def test_teammate_request_delivers_pinned_lead(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_v3", "S4", "b-s4")
    sid, writer = result.session_id, result.writer_id
    _complete_checkin(migrated, store, sid, writer)  # stay on the active axis past 60 s
    crate = None
    for candidate in range(1, 9):
        out = _run(
            migrated,
            store,
            sid,
            writer,
            "teammate_request",
            {"crate": candidate},
            _stage(migrated, sid)[1],
            accept=False,
        )
        if out.accepted:
            crate = candidate
            break
        assert out.reason in ("no_teammate_records", "stale_revision")
    assert crate is not None, "no crate had teammate records"
    deliver = _job(migrated, sid, "deliver")
    assert deliver is not None
    _set_active(migrated, sid, int(deliver["due_active_ms"]) + 50)
    _tick(migrated, store, n=3)
    with migrated.runtime.connect() as conn:
        share = (
            conn.execute(
                select(s.share).where(
                    s.share.c.session_id == sid,
                    s.share.c.prompted_by_request_id.is_not(None),
                )
            )
            .mappings()
            .first()
        )
        episode = (
            conn.execute(
                select(s.request_episode).where(
                    s.request_episode.c.session_id == sid,
                    s.request_episode.c.closed_at.is_not(None),
                )
            )
            .mappings()
            .first()
        )
    assert share is not None and share["sharer"] in ("JT", "PM")
    assert episode is not None and episode["status"] == "resolved"
    msgs = [m for m in _messages(migrated, sid) if m["record_id"] is not None]
    assert msgs and msgs[0]["record_id"] == share["record_id"]


def test_structured_requests_leave_visible_participant_messages(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """UX feedback: a picker ask must appear in the channel as the participant's own
    message — the request is durable and linked, not only visible to the responder."""
    result = _drive_to_task(migrated, store, "tp_demo_v3", "S5", "b-s5")
    sid, writer = result.session_id, result.writer_id
    _complete_checkin(migrated, store, sid, writer)

    crate = None
    for candidate in range(1, 9):
        out = _run(
            migrated,
            store,
            sid,
            writer,
            "teammate_request",
            {"crate": candidate},
            _stage(migrated, sid)[1],
            accept=False,
        )
        if out.accepted:
            crate = candidate
            break
        assert out.reason in ("no_teammate_records", "stale_revision")
    assert crate is not None, "no crate had teammate records"
    team = [
        m
        for m in _messages(migrated, sid)
        if m["channel"] == "team" and m["actor"] == "participant"
    ]
    assert any(
        m["body"] == f"Requesting intel on Crate {crate}."
        and m["crate"] == crate
        and m["request_id"] is not None
        for m in team
    )

    _run(
        migrated,
        store,
        sid,
        writer,
        "assistant_request",
        {"crate": 2},
        _stage(migrated, sid)[1],
    )
    _run(
        migrated,
        store,
        sid,
        writer,
        "assistant_request",
        {"body": "what does my file say?"},
        _stage(migrated, sid)[1],
    )
    assistant = [
        m
        for m in _messages(migrated, sid)
        if m["channel"] == "assistant" and m["actor"] == "participant"
    ]
    bodies = [m["body"] for m in assistant]
    assert "What does my file say about Crate 2?" in bodies
    assert "what does my file say?" in bodies


# ----- INV-0602: TP check-in pause / resume ------------------------------------------------------


def test_checkin_pauses_and_resume_restarts(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_v3", "C1", "b-c1")
    sid, writer = result.session_id, result.writer_id
    _set_active(migrated, sid, 60_000)
    _tick(migrated, store)
    assert _stage(migrated, sid)[0] == "checkin_collect"
    interval = _open_interval(migrated, sid)
    assert interval is not None and interval["kind"] == "paused"
    assert interval["reason"] == "pressure_checkin"

    # Active-due work cannot fire while paused; the answer freeze cannot sneak past either.
    _tick(migrated, store)
    assert _stage(migrated, sid)[0] == "checkin_collect"
    remaining_msgs = len(_messages(migrated, sid))

    _run(
        migrated,
        store,
        sid,
        writer,
        "pressure_response",
        {"value": 6},
        _stage(migrated, sid)[1],
    )
    assert _stage(migrated, sid)[0] == "checkin_wait"
    _force_wall_due(migrated, sid, "checkin_reveal")
    _tick(migrated, store)
    assert _stage(migrated, sid)[0] == "checkin_reveal"
    _run(migrated, store, sid, writer, "proceed", {}, _stage(migrated, sid)[1])
    assert _stage(migrated, sid)[0] == "task"
    interval = _open_interval(migrated, sid)
    assert interval is not None and interval["kind"] == "running"
    assert _active(migrated, sid) < 61_000  # elapsed never advanced past the pause point
    assert len(_messages(migrated, sid)) == remaining_msgs


def test_checkin_inactivity_cap_is_wall_clocked(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_v3", "C2", "b-c2")
    sid = result.session_id
    _set_active(migrated, sid, 60_000)
    _tick(migrated, store)
    assert _stage(migrated, sid)[0] == "checkin_collect"
    inactivity = _job(migrated, sid, "checkin_inactivity")
    assert inactivity is not None and inactivity["clock_basis"] == "wall"
    _force_wall_due(migrated, sid, "checkin_inactivity")
    _tick(migrated, store)
    assert _stage(migrated, sid)[0] == "technical_stop"
    with migrated.runtime.connect() as conn:
        outcome = conn.execute(
            select(s.result.c.status).where(s.result.c.session_id == sid)
        ).scalar_one()
    assert outcome == "technical_stop"
    # The task clock is closed and every pending active job is suppressed, not pending.
    statuses = {j["kind"]: j["status"] for j in _jobs(migrated, sid)}
    assert statuses["deadline"] == "suppressed" and statuses["warning"] == "suppressed"


def test_checkin_job_cannot_reopen_after_cycle_completed(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """One pressure_checkin per session: a retried or redelivered ``checkin`` job
    arriving after the cycle finished — stage is back at ``task`` — must not open
    a second overlay or re-pause the clock."""
    result = _drive_to_task(migrated, store, "tp_demo_v3", "C3", "b-c3")
    sid, writer = result.session_id, result.writer_id
    _complete_checkin(migrated, store, sid, writer)
    assert _stage(migrated, sid)[0] == "task"
    opens_before = len(_events(migrated, sid, "checkin_open"))
    with migrated.runtime.begin() as conn:
        conn.execute(
            insert(s.scheduled_job).values(
                job_id=uuid.uuid4(),
                session_id=sid,
                logical_job_key="checkin-redelivered",
                kind="checkin",
                clock_basis="active_task",
                due_active_ms=60_000,
                claimable_at=func.now(),
                payload={},
            )
        )
    _tick(migrated, store)
    assert _stage(migrated, sid)[0] == "task"
    assert len(_events(migrated, sid, "checkin_open")) == opens_before
    interval = _open_interval(migrated, sid)
    assert interval is not None and interval["kind"] == "running"


# ----- INV-0607/0605: warning and the 360 s freeze -----------------------------------------------


def test_warning_delivers_once_at_300(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_v3", "W1", "b-w1")
    sid = result.session_id
    _complete_checkin(migrated, store, sid, result.writer_id)
    _set_active(migrated, sid, 299_500)
    _tick(migrated, store)
    assert "One minute left." not in [
        m["body"] for m in _messages(migrated, sid)
    ]  # strictly before the boundary: nothing fires early
    _set_active(migrated, sid, 300_000)
    _tick(migrated, store)
    bodies = [m["body"] for m in _messages(migrated, sid)]
    assert bodies.count("One minute left.") == 1
    with migrated.runtime.connect() as conn:
        n = conn.execute(
            select(func.count())
            .select_from(s.manipulation_exposure)
            .where(
                s.manipulation_exposure.c.session_id == sid,
                s.manipulation_exposure.c.kind == "one_minute_warning",
            )
        ).scalar_one()
    assert n == 1
    _tick(migrated, store)  # still one delivery under a second pass
    assert [m["body"] for m in _messages(migrated, sid)].count("One minute left.") == 1


def test_deadline_freezes_answers_and_suppresses_late_output(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_v3", "D1", "b-d1")
    sid, writer = result.session_id, result.writer_id
    _complete_checkin(migrated, store, sid, writer)
    _run(
        migrated,
        store,
        sid,
        writer,
        "answer_update",
        {"crate": 1, "location_id": "H2"},
        _stage(migrated, sid)[1],
    )
    _run(
        migrated,
        store,
        sid,
        writer,
        "answer_update",
        {"crate": 1, "location_id": "H3"},
        _stage(migrated, sid)[1],
    )
    _run(
        migrated,
        store,
        sid,
        writer,
        "answer_update",
        {"crate": 2, "location_id": "H1"},
        _stage(migrated, sid)[1],
    )
    _set_active(migrated, sid, 359_500)
    _tick(migrated, store)
    assert _stage(migrated, sid)[0] == "task"  # strictly under the budget: still open
    _set_active(migrated, sid, 360_000)
    _tick(migrated, store)
    assert _stage(migrated, sid)[0] == "answers_locked"
    with migrated.runtime.connect() as conn:
        finals = (
            conn.execute(select(s.answer_final).where(s.answer_final.c.session_id == sid))
            .mappings()
            .all()
        )
    by_crate = {f["crate"]: f for f in finals}
    assert set(by_crate) == {1, 2}
    assert by_crate[1]["location_id"] == "H3" and by_crate[1]["revision"] == 2
    # The open interval is closed; active elapsed is final.
    assert _open_interval(migrated, sid) is None
    assert _active(migrated, sid) >= 360_000
    # Late answers are refused.
    rejected = _run(
        migrated,
        store,
        sid,
        writer,
        "answer_update",
        {"crate": 3, "location_id": "H4"},
        _stage(migrated, sid)[1],
        accept=False,
    )
    assert rejected.reason in ("task_closed", "wrong_stage", "stale_revision")
    # Remaining active work (warning, deliveries, scripted asks) is suppressed, not pending.
    pending = [j for j in _jobs(migrated, sid) if j["status"] in ("pending", "claimed")]
    assert not [j for j in pending if j["clock_basis"] == "active_task"]


def test_advance_due_freezes_inside_a_command(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """INV-0607: a lagging scheduler cannot let a command slip past the freeze — the due
    deadline transition runs inside the command's own session lock."""
    result = _drive_to_task(migrated, store, "tp_demo_v3", "D2", "b-d2")
    sid, writer = result.session_id, result.writer_id
    _complete_checkin(migrated, store, sid, writer)
    _set_active(migrated, sid, 360_000)
    out = _run(
        migrated,
        store,
        sid,
        writer,
        "answer_update",
        {"crate": 1, "location_id": "H1"},
        _stage(migrated, sid)[1],
        accept=False,
    )
    # The deadline fired inside the command: stage moved first, then the answer was judged
    # on the post-transition state (task closed) — never accepted at 360+.
    assert _stage(migrated, sid)[0] == "answers_locked"
    assert not out.accepted


# ----- INV-0604/0605: generation lifecycle -------------------------------------------------------


def test_greeting_schedules_seat_replies(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_v3", "G1", "b-g1")
    sid, writer = result.session_id, result.writer_id
    _complete_checkin(migrated, store, sid, writer)
    _run(
        migrated,
        store,
        sid,
        writer,
        "greeting",
        {"text": "Hi!"},
        _stage(migrated, sid)[1],
    )
    _set_active(migrated, sid, 120_000)
    _tick(migrated, store, n=4)
    actors = {m["actor"] for m in _messages(migrated, sid) if m["channel"] == "team"}
    assert {"JT", "PM"} <= actors


def test_social_reply_falls_back_when_provider_unavailable(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """provider=None records a failed/unavailable ``model_call`` and still delivers the
    deterministic script — the participant never waits on a dead model (INV-0608)."""
    result = _drive_to_task(migrated, store, "tp_demo_v3", "R1", "b-r1")
    sid, writer = result.session_id, result.writer_id
    _complete_checkin(migrated, store, sid, writer)
    _run(
        migrated,
        store,
        sid,
        writer,
        "social_reply",
        {"body": "how are you doing?", "to": "JT"},
        _stage(migrated, sid)[1],
    )
    gen = _job(migrated, sid, "generate")
    assert gen is not None and gen["clock_basis"] == "wall"
    _tick(migrated, store, n=2)  # generate -> deliver
    deliver = _job(migrated, sid, "deliver")
    assert deliver is not None
    _set_active(migrated, sid, int(deliver["due_active_ms"]) + 50)
    _tick(migrated, store, n=3)
    with migrated.runtime.connect() as conn:
        call = (
            conn.execute(select(s.model_call).where(s.model_call.c.session_id == sid))
            .mappings()
            .one()
        )
        out = (
            conn.execute(select(s.displayed_output).where(s.displayed_output.c.session_id == sid))
            .mappings()
            .one()
        )
    assert call["status"] in ("failed", "timeout", "skipped")
    assert out["fallback"] == "deterministic_script"
    with migrated.runtime.connect() as conn:
        req = (
            conn.execute(
                select(s.request).where(
                    s.request.c.session_id == sid, s.request.c.kind == "free_text"
                )
            )
            .mappings()
            .one()
        )
    replies = [
        m
        for m in _messages(migrated, sid)
        if m["channel"] == "team" and m["request_id"] == req["request_id"]
    ]
    assert replies and replies[0]["actor"] == "JT" and replies[0]["body"]


def test_assistant_request_uses_file_only_context(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_v3", "A1", "b-a1")
    sid, writer = result.session_id, result.writer_id
    _complete_checkin(migrated, store, sid, writer)
    _run(
        migrated,
        store,
        sid,
        writer,
        "assistant_request",
        {"body": "summarise crate 1", "crate": 1},
        _stage(migrated, sid)[1],
    )
    _tick(migrated, store, n=2)
    deliver = _job(migrated, sid, "deliver")
    assert deliver is not None
    _set_active(migrated, sid, int(deliver["due_active_ms"]) + 50)
    _tick(migrated, store, n=3)
    with migrated.runtime.connect() as conn:
        snap = (
            conn.execute(
                select(s.knowledge_snapshot).where(
                    s.knowledge_snapshot.c.session_id == sid,
                    s.knowledge_snapshot.c.purpose == "assistant_context",
                )
            )
            .mappings()
            .one()
        )
        own = conn.execute(
            select(func.count())
            .select_from(s.file_membership)
            .where(s.file_membership.c.session_id == sid)
        ).scalar_one()
        out = conn.execute(
            select(s.displayed_output.c.fallback).where(s.displayed_output.c.session_id == sid)
        ).scalar_one()
    # The assistant's context is exactly the participant file — no teammate holdings.
    assert len(snap["record_manifest"]) == own
    assert out == "deterministic_file_summary"
    msgs = [m for m in _messages(migrated, sid) if m["channel"] == "assistant"]
    assert msgs and msgs[-1]["actor"] == "assistant"


def test_assistant_free_text_crate_mention_scopes_summary(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """A typed "where is crate N?" scopes the deterministic file summary by the
    mention — the dropdown is not the only path to a scoped answer (lab feedback)."""
    result = _drive_to_task(migrated, store, "tp_demo_v3", "A1", "b-a1")
    sid, writer = result.session_id, result.writer_id
    _complete_checkin(migrated, store, sid, writer)
    with migrated.runtime.connect() as conn:
        crate = int(
            conn.execute(
                select(func.min(s.record.c.crate))
                .select_from(
                    s.file_membership.join(
                        s.record,
                        (s.record.c.session_id == s.file_membership.c.session_id)
                        & (s.record.c.record_id == s.file_membership.c.record_id),
                    )
                )
                .where(s.file_membership.c.session_id == sid)
            ).scalar_one()
        )
    _run(
        migrated,
        store,
        sid,
        writer,
        "assistant_request",
        {"body": f"where is crate {crate}?"},
        _stage(migrated, sid)[1],
    )
    _tick(migrated, store, n=2)
    deliver = _job(migrated, sid, "deliver")
    assert deliver is not None
    _set_active(migrated, sid, int(deliver["due_active_ms"]) + 50)
    _tick(migrated, store, n=3)
    msgs = [m for m in _messages(migrated, sid) if m["channel"] == "assistant"]
    assert msgs and f"Crate {crate}," in msgs[-1]["body"]


def test_generation_finish_during_pause_does_not_display(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """Spec §6.4: generation may finish during the check-in pause, but readiness does not
    authorize display — delivery waits for the resumed active axis."""
    result = _drive_to_task(migrated, store, "tp_demo_v3", "P1", "b-p1")
    sid, writer = result.session_id, result.writer_id
    _set_active(migrated, sid, 57_000)
    _run(
        migrated,
        store,
        sid,
        writer,
        "social_reply",
        # BA-53: a social body — a typed intel ask ("any update on crate 3?")
        # now takes the deterministic picker redirect in structured mode and
        # would never schedule a generate job.
        {"body": "how are you doing over there?", "to": "PM"},
        _stage(migrated, sid)[1],
    )
    _set_active(migrated, sid, 60_000)
    _tick(migrated, store, n=2)  # checkin opens (pause) + generate commits during pause
    assert _stage(migrated, sid)[0] == "checkin_collect"
    assert _job(migrated, sid, "generate")["status"] == "committed"
    assert _job(migrated, sid, "deliver") is not None
    _tick(migrated, store)  # active frozen at 60 s: the ready reply cannot display
    team_msgs = [m for m in _messages(migrated, sid) if m["actor"] == "PM"]
    assert not team_msgs
    # Drive through the check-in; resume; the reply then delivers on the active axis.
    _run(migrated, store, sid, writer, "pressure_skip", {}, _stage(migrated, sid)[1])
    _force_wall_due(migrated, sid, "checkin_reveal")
    _tick(migrated, store)
    _run(migrated, store, sid, writer, "proceed", {}, _stage(migrated, sid)[1])
    deliver = _job(migrated, sid, "deliver")
    assert deliver is not None and deliver["status"] == "pending"
    _set_active(migrated, sid, int(deliver["due_active_ms"]) + 50)
    _tick(migrated, store, n=3)
    team_msgs = [m for m in _messages(migrated, sid) if m["actor"] == "PM"]
    assert team_msgs


# ----- INV-0610 boundary + INV-0603 leases --------------------------------------------------------


def test_checkin_boundary_below_60s(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_v3", "B1", "b-b1")
    sid = result.session_id
    _set_active(migrated, sid, 59_500)
    _tick(migrated, store)
    assert _stage(migrated, sid)[0] == "task"
    _set_active(migrated, sid, 60_000)
    _tick(migrated, store)
    assert _stage(migrated, sid)[0] == "checkin_collect"


def test_pe_clock_never_pauses_at_60(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = _drive_to_task(migrated, store, "pe_demo_v3", "B2", "b-b2")
    sid = result.session_id
    _set_active(migrated, sid, 60_000)
    _tick(migrated, store)
    assert _stage(migrated, sid)[0] == "task"
    interval = _open_interval(migrated, sid)
    assert interval is not None and interval["kind"] == "running"


def test_pe_acknowledgment_and_crosstalk_scheduled(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "pe_demo_v3", "E1", "b-e1")
    sid = result.session_id
    _set_active(migrated, sid, 45_000)  # pe_demo's first scripted ask is at 40 s
    _tick(migrated, store)
    kinds = [j["kind"] for j in _jobs(migrated, sid)]
    assert kinds.count("deliver") >= 1  # the crosstalk job the scripted ask scheduled


# ----- INV-0612: incident intervals ---------------------------------------------------------------


def test_outage_incidents_persist_against_ledger(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_v3", "I1", "b-i1")
    sid = result.session_id
    _set_active(migrated, sid, 100_000)
    with migrated.runtime.connect() as conn:
        t0 = conn.execute(text("SELECT clock_timestamp() - interval '5 seconds'")).scalar_one()
        t1 = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
    n = record_outage_incidents(
        migrated.runtime, started_at=t0, ended_at=t1, surfaces="persistence"
    )
    assert n >= 1
    with migrated.runtime.connect() as conn:
        row = (
            conn.execute(select(s.incident_interval).where(s.incident_interval.c.session_id == sid))
            .mappings()
            .one()
        )
    assert row["kind"] == "server_outage" and row["clock_effect"] == "counted"
    assert row["active_ms_at_start"] < row["active_ms_at_end"]
    # A second recording of the same window does not duplicate the session's row.
    record_outage_incidents(migrated.runtime, started_at=t0, ended_at=t1, surfaces="persistence")
    with migrated.runtime.connect() as conn:
        n2 = conn.execute(
            select(func.count())
            .select_from(s.incident_interval)
            .where(s.incident_interval.c.session_id == sid)
        ).scalar_one()
    assert n2 == 1
    # The clock was never extended: elapsed is still ~100 s + real elapsed.
    assert _active(migrated, sid) < 105_000


# ----- UX round 4 (UX_Feedback_v4, 2026-10-05) ---------------------------------------------------

_GREETING_BUTTONS = {"Hello!", "Hi!", "Hey!"}


def _complete_checkin_v6(
    db: MigratedDatabase, store: ContentStore, sid: uuid.UUID, writer: uuid.UUID
) -> None:
    """v6 profiles require the own rating (UX round 3, item 5) — respond, not skip."""
    _set_active(db, sid, 60_000)
    _tick(db, store)
    assert _stage(db, sid)[0] == "checkin_collect"
    _run(db, store, sid, writer, "pressure_response", {"value": 5}, _stage(db, sid)[1])
    assert _stage(db, sid)[0] == "checkin_wait"
    _force_wall_due(db, sid, "checkin_reveal")
    _tick(db, store)
    assert _stage(db, sid)[0] == "checkin_reveal"
    _run(db, store, sid, writer, "proceed", {}, _stage(db, sid)[1])
    assert _stage(db, sid)[0] == "task"


def test_greeting_replies_render_verbatim_and_distinct(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """Item 8: greeting replies read exactly like the participant's buttons —
    ``Hi!``/``Hello!``/``Hey!`` with no seat lower-casing — and the two seats
    never answer with the identical greeting."""
    result = _drive_to_task(migrated, store, "tp_demo_v6", "G4", "b-g4")
    sid, writer = result.session_id, result.writer_id
    _complete_checkin_v6(migrated, store, sid, writer)
    _run(
        migrated,
        store,
        sid,
        writer,
        "greeting",
        {"text": "Hi!"},
        _stage(migrated, sid)[1],
    )
    _set_active(migrated, sid, 120_000)
    _tick(migrated, store, n=6)
    greetings = [
        m
        for m in _messages(migrated, sid)
        if m["channel"] == "team" and m["actor"] in ("JT", "PM") and m["body"] in _GREETING_BUTTONS
    ]
    assert {m["actor"] for m in greetings} == {"JT", "PM"}
    assert len({m["body"] for m in greetings}) == len(greetings)


def test_scripted_request_wording_varies_per_request(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """Item 10: requests.demo@v2 draws a template per (seat, index) — the scripted
    requests are no longer the one canned "Requesting intel on Crate N." line."""
    import re

    # Pin the world seed: the template draw is per-session, so an unpinned seed makes
    # "more than one phrasing" a ~4%-of-runs flake. Seed 03 draws three distinct
    # templates for this plan (verified against the seeded draw keys).
    result = _drive_to_task(migrated, store, "tp_demo_v6", "V4", "b-v4", seed_hex="03" * 32)
    sid = result.session_id
    # Scripted requests deliver only on the open task surface — drive through the
    # 60 s check-in first, then jump to 300 s: all three requests due, deadline
    # (360 s) not yet reached.
    _complete_checkin_v6(migrated, store, sid, result.writer_id)
    _set_active(migrated, sid, 300_000)
    _tick(migrated, store, n=10)
    reqs = [
        m
        for m in _messages(migrated, sid)
        if m["request_id"] is not None and m["actor"] in ("JT", "PM")
    ]
    assert len(reqs) == 3
    allowed = {
        t.replace("{n}", str(n))
        for t in (
            "Requesting intel on Crate {n}.",
            "Do you have anything on Crate {n}?",
            "Any updates on Crate {n}?",
            "What does your file have on Crate {n}?",
            "Could use whatever you have on Crate {n}.",
        )
        for n in range(1, 9)
    }
    assert all(m["body"] in allowed for m in reqs)
    # Crate digits aside, the seeded draw picks more than one phrasing.
    forms = {re.sub(r"\d+", "{n}", m["body"]) for m in reqs}
    assert len(forms) >= 2


def test_social_replies_diverge_across_seats(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """Item 13: the same social question to both seats no longer delivers
    paraphrase replies — the diversified v4 pool plus the near-repeat guard."""
    result = _drive_to_task(migrated, store, "tp_demo_v6", "D4", "b-d4")
    sid, writer = result.session_id, result.writer_id
    _complete_checkin_v6(migrated, store, sid, writer)
    for seat in ("JT", "PM"):
        _run(
            migrated,
            store,
            sid,
            writer,
            "social_reply",
            {"body": "how are you doing?", "to": seat},
            _stage(migrated, sid)[1],
        )
    _set_active(migrated, sid, 200_000)
    _tick(migrated, store, n=8)
    with migrated.runtime.connect() as conn:
        reqs = (
            conn.execute(
                select(s.request.c.request_id).where(
                    s.request.c.session_id == sid, s.request.c.kind == "free_text"
                )
            )
            .scalars()
            .all()
        )
    replies = {
        m["actor"]: m["body"]
        for m in _messages(migrated, sid)
        if m["channel"] == "team" and m["request_id"] in reqs
    }
    assert set(replies) == {"JT", "PM"}
    assert not engines.is_near_repeat(replies["PM"], [replies["JT"]])
