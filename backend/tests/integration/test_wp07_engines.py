"""INV-0701-0708: connecting sequence, agent memory, output validation, draw persistence.

Builds on the WP06 drive helpers — every test still runs against the disposable PostgreSQL
schema and the real command/job path (``execute_command`` + ``scheduler.tick``).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping

import pytest
from sqlalchemy import func, select

from app.db import schema as s
from app.domain.manifests.store import ContentStore
from app.measurement.synthetic_registry import (
    CaseKey,
    JsonlEventStore,
    SyntheticRunRegistry,
)
from app.providers.base import ProviderResponse
from app.services import lifecycle, scheduler
from app.services.entry import enter
from tests.integration.test_wp06_engines import (
    BUILD_SHA,
    _complete_checkin,
    _drive_to_task,
    _events,
    _force_wall_due,
    _job,
    _query,
    _run,
    _seed_batch,
    _set_active,
    _stage,
    _tick,
)

from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _agent_state(db: MigratedDatabase, sid: uuid.UUID, seat: str) -> Mapping[str, object] | None:
    with db.runtime.connect() as conn:
        return (
            conn.execute(
                select(s.agent_state).where(
                    s.agent_state.c.session_id == sid, s.agent_state.c.seat == seat
                )
            )
            .mappings()
            .one_or_none()
        )


class _StubProvider:
    """Fixed-text provider double (QA/test only)."""

    provider_id = "stub"
    is_double = True

    def __init__(self, text: str) -> None:
        self._text = text

    def generate(self, request: object, *, timeout_s: float) -> ProviderResponse:
        return ProviderResponse(
            text=self._text,
            provider_id="stub",
            model_id="stub-v0",
            latency_ms=1,
            finish_reason="stop",
            params={},
        )


def _drive_to_connecting(
    db: MigratedDatabase, store: ContentStore, manifest_id: str, pid: str, batch: str
):
    _seed_batch(db, store, manifest_id, batch)
    result = enter(
        db.runtime,
        store,
        query=_query(pid, batch),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
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
    return result


# ----- INV-0701: scripted connecting / task-open ------------------------------------------------


def test_connecting_sequence_and_ready_gate(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_connecting(migrated, store, "tp_demo_v3", "W1", "b-w1")
    sid, writer = result.session_id, result.writer_id
    assert _stage(migrated, sid)[0] == "scripted_connecting"

    presented = _events(migrated, sid, "teammate_presented")
    assert [e["payload"]["seat"] for e in presented] == ["JT"]
    job = _job(migrated, sid, "connecting")
    assert job is not None and job["clock_basis"] == "wall" and job["status"] == "pending"
    # PM's join lands at the demo's 10.6 s wall mark.
    delta = job["due_wall_at"] - _events(migrated, sid, "teammate_presented")[0]["server_time"]
    assert 10_000 <= delta.total_seconds() * 1000 <= 11_500

    # Start is gated until the PM-join job commits (the demo's disabled button).
    out = _run(migrated, store, sid, writer, "ready", {}, _stage(migrated, sid)[1], accept=False)
    assert not out.accepted and out.reason == "connecting_incomplete"

    _force_wall_due(migrated, sid, "connecting")
    _tick(migrated, store)
    seats = {e["payload"]["seat"] for e in _events(migrated, sid, "teammate_presented")}
    assert seats == {"JT", "PM"}

    _run(migrated, store, sid, writer, "ready", {}, _stage(migrated, sid)[1])
    assert _stage(migrated, sid)[0] == "ready"
    # The active clock has NOT started during the post-start hold.
    with migrated.runtime.connect() as conn:
        assert (
            conn.execute(
                select(func.count())
                .select_from(s.clock_interval)
                .where(s.clock_interval.c.session_id == sid)
            ).scalar_one()
            == 0
        )

    _force_wall_due(migrated, sid, "task_open")
    _tick(migrated, store)
    assert _stage(migrated, sid)[0] == "task"
    assert _events(migrated, sid, "task_clock_started")


def test_task_open_replay_does_not_restart_clock(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_v3", "W2", "b-w2")
    sid = result.session_id
    # The task_open job committed inside _drive_to_task; re-dispatching a stale copy must be
    # a no-op (stage already past "ready") — one clock interval, one schedule.
    job = _job(migrated, sid, "task_open")
    assert job is not None and job["status"] == "committed"
    _tick(migrated, store, n=3)
    with migrated.runtime.connect() as conn:
        intervals = conn.execute(
            select(func.count())
            .select_from(s.clock_interval)
            .where(s.clock_interval.c.session_id == sid)
        ).scalar_one()
    assert intervals == 1
    assert len(_events(migrated, sid, "task_clock_started")) == 1


def test_ready_rejection_after_pm_join_via_advance_due(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """When wall time has passed but the worker hasn't run, the connecting job must be swept
    inline by ``advance_due`` inside the ready command — the participant is never stuck
    behind a lagging worker."""
    result = _drive_to_connecting(migrated, store, "tp_demo_v3", "W3", "b-w3")
    sid, writer = result.session_id, result.writer_id
    _force_wall_due(migrated, sid, "connecting")
    # No tick: the ready command itself must sweep the due connecting job. The sweep commits
    # a revision, so the client's stale expected_revision is correctly rejected — the client
    # re-syncs (sees PM connected), retries at the fresh revision and is admitted.
    out = _run(migrated, store, sid, writer, "ready", {}, _stage(migrated, sid)[1], accept=False)
    assert not out.accepted and out.reason == "stale_revision"
    seats = {e["payload"]["seat"] for e in _events(migrated, sid, "teammate_presented")}
    assert seats == {"JT", "PM"}
    _run(migrated, store, sid, writer, "ready", {}, _stage(migrated, sid)[1])
    assert _stage(migrated, sid)[0] == "ready"


# ----- INV-0704: bounded agent memory ---------------------------------------------------------


def test_agent_state_tracks_structured_request_lifecycle(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_v3", "M1", "b-m1")
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
    assert crate is not None
    request_id = uuid.UUID(str(out.result["request_id"]))
    responder = None
    for seat in ("JT", "PM"):
        st = _agent_state(migrated, sid, seat)
        if st and any(o["request_id"] == str(request_id) for o in st["outstanding_requests"]):
            responder = seat
    assert responder is not None, "no seat recorded the outstanding request"

    deliver = _job(migrated, sid, "deliver")
    assert deliver is not None
    _set_active(migrated, sid, int(deliver["due_active_ms"]) + 50)
    _tick(migrated, store, n=3)

    st = _agent_state(migrated, sid, responder)
    assert st is not None
    outstanding_ids = {o["request_id"] for o in st["outstanding_requests"]}
    assert str(request_id) not in outstanding_ids
    resolutions = {c.get("request_id"): c["resolution"] for c in st["commitments"]}
    assert resolutions.get(str(request_id)) == "delivered"
    assert int(st["delivered_high_water_seq"]) > 0


def test_scripted_request_memory_resolves_on_participant_share(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_v3", "M2", "b-m2")
    sid, writer = result.session_id, result.writer_id
    _set_active(migrated, sid, 40_500)  # first scripted request fires at active 40 s
    _tick(migrated, store)
    with migrated.runtime.connect() as conn:
        req = (
            conn.execute(
                select(s.request).where(
                    s.request.c.session_id == sid, s.request.c.requester.in_(("JT", "PM"))
                )
            )
            .mappings()
            .one()
        )
    seat = req["requester"]
    st = _agent_state(migrated, sid, seat)
    assert st is not None
    assert str(req["request_id"]) in {o["request_id"] for o in st["outstanding_requests"]}
    assert st["outstanding_requests"][0]["role"] == "requester"

    # The participant answering the scripted ask resolves the seat's outstanding entry.
    with migrated.runtime.connect() as conn:
        file_record = conn.execute(
            select(s.file_membership.c.record_id)
            .select_from(
                s.file_membership.join(
                    s.record,
                    (s.record.c.session_id == s.file_membership.c.session_id)
                    & (s.record.c.record_id == s.file_membership.c.record_id),
                )
            )
            .where(
                s.file_membership.c.session_id == sid,
                s.record.c.crate == req["crate"],
            )
            .limit(1)
        ).scalar_one()
    _run(
        migrated,
        store,
        sid,
        writer,
        "record_share",
        {"record_id": file_record},
        _stage(migrated, sid)[1],
    )
    st = _agent_state(migrated, sid, seat)
    assert str(req["request_id"]) not in {o["request_id"] for o in st["outstanding_requests"]}
    resolutions = {c.get("request_id"): c["resolution"] for c in st["commitments"]}
    assert resolutions.get(str(req["request_id"])) == "participant_share"


def test_open_episodes_expire_at_deadline(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = _drive_to_task(migrated, store, "pe_demo_v3", "M3", "b-m3")
    sid = result.session_id
    _set_active(migrated, sid, 40_500)
    _tick(migrated, store)
    _set_active(migrated, sid, 359_500)
    _tick(migrated, store)
    _set_active(migrated, sid, 360_100)
    _tick(migrated, store)
    assert _stage(migrated, sid)[0] == "answers_locked"
    with migrated.runtime.connect() as conn:
        open_episodes = conn.execute(
            select(func.count())
            .select_from(s.request_episode)
            .where(
                s.request_episode.c.session_id == sid,
                s.request_episode.c.closed_at.is_(None),
            )
        ).scalar_one()
    assert open_episodes == 0
    # The requesting seat's memory shows the expiry, not a dangling promise.
    for seat in ("JT", "PM"):
        st = _agent_state(migrated, sid, seat)
        if st is None:
            continue
        assert st["outstanding_requests"] == []
        if st["commitments"]:
            assert any(c["resolution"] == "expired" for c in st["commitments"])


# ----- INV-0703: generated-output validation -----------------------------------------------------


def _drive_social_reply_to_deliver(
    db: MigratedDatabase,
    store: ContentStore,
    pid: str,
    batch: str,
    provider: object | None,
) -> uuid.UUID:
    result = _drive_to_task(db, store, "pe_demo_v3", pid, batch)
    sid, writer = result.session_id, result.writer_id
    _run(
        db,
        store,
        sid,
        writer,
        "social_reply",
        {"body": "how are you doing?", "to": "JT"},
        _stage(db, sid)[1],
    )
    for _ in range(4):
        committed = scheduler.tick(
            db.runtime,
            store,
            worker_id="w1",
            provider=provider,
            limit=25,  # type: ignore[arg-type]
        )
        if committed == 0:
            break
    deliver = _job(db, sid, "deliver")
    assert deliver is not None
    _set_active(db, sid, int(deliver["due_active_ms"]) + 50)
    for _ in range(4):
        if (
            scheduler.tick(
                db.runtime,
                store,
                worker_id="w1",
                provider=provider,
                limit=25,  # type: ignore[arg-type]
            )
            == 0
        ):
            break
    return sid


def test_leaked_generated_text_falls_back_deterministically(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """A provider reply naming a hideout is validation_rejected and never displayed —
    the delivered line comes from the pinned fallback bank (INV-0703)."""
    sid = _drive_social_reply_to_deliver(
        migrated, store, "V1", "b-v1", _StubProvider("crate 4 is at the Sawmill now")
    )
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
    assert call["status"] == "validation_rejected"
    assert "hideout_leak" in call["validation"]["violations"]
    assert call["raw_response"] == "crate 4 is at the Sawmill now"
    assert out["fallback"] == "deterministic_script"
    assert out["shown_text"] != "crate 4 is at the Sawmill now"
    assert "Sawmill" not in out["shown_text"]


def test_clean_generated_text_is_delivered(migrated: MigratedDatabase, store: ContentStore) -> None:
    sid = _drive_social_reply_to_deliver(
        migrated, store, "V2", "b-v2", _StubProvider("doing fine, partway through mine")
    )
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
    assert call["status"] == "succeeded"
    assert call["validation"]["violations"] == []
    assert out["fallback"] == "none"
    assert out["shown_text"] == "doing fine, partway through mine"


# ----- INV-0702: draw persistence + golden ordering (INV-0707) ------------------------------------


def test_voice_and_fallback_draws_persisted(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """Every realized jobs-stream draw inside a deliver/generate commit lands in draw_log."""
    sid = _drive_social_reply_to_deliver(migrated, store, "V3", "b-v3", None)
    with migrated.runtime.connect() as conn:
        keys = {
            r["draw_key"]
            for r in conn.execute(
                select(s.draw_log).where(
                    s.draw_log.c.session_id == sid, s.draw_log.c.stream == "jobs"
                )
            ).mappings()
        }
    assert any("fallback" in k or "voice" in k for k in keys), keys


def test_golden_event_ordering(migrated: MigratedDatabase, store: ContentStore) -> None:
    """INV-0707: the reference session's typed event order matches the demo trace —
    connecting presentations precede clock start; the 300 s warning delivers exactly once
    with its declared ``scheduled_for_active_ms``, then the deadline freezes answers."""
    result = _drive_to_task(migrated, store, "pe_demo_v3", "G7", "b-g7")
    sid = result.session_id
    _set_active(migrated, sid, 299_500)
    _tick(migrated, store, n=8)
    _set_active(migrated, sid, 300_500)
    _tick(migrated, store, n=8)
    warnings = [
        e
        for e in _all_events(migrated, sid)
        if e["type"] == "message_delivered" and e["payload"]["body"] == "One minute left."
    ]
    assert len(warnings) == 1
    assert warnings[0]["payload"]["scheduled_for_active_ms"] == 300_000
    _set_active(migrated, sid, 360_100)
    _tick(migrated, store, n=8)
    types = [e["type"] for e in _all_events(migrated, sid)]

    def idx(name: str) -> int:
        return types.index(name)

    assert idx("task_clock_started") < idx("greeting_prompt") < idx("task_deadline")
    assert idx("task_deadline") < idx("answers_frozen")
    assert _stage(migrated, sid)[0] == "answers_locked"


# ----- INV-0706: paired-arm participant script -------------------------------------------------


def test_paired_arm_script_identical_event_stream(
    migrated: MigratedDatabase, store: ContentStore, tmp_path
) -> None:
    """Spec §7.7: the same synthetic participant script in both TP arms must produce the
    same event-type stream — the only legitimate divergence is the manipulation surface
    (the check-in's revealed seat values)."""
    # INV-0212 / BA-13: synthetic realizations register as registry attempts keyed by
    # (compatibility_key, seed, replicate) — completed cases are never silently rerun.
    registry = SyntheticRunRegistry(JsonlEventStore(tmp_path / "paired-arm-runs.jsonl"))
    attempts: list[tuple[CaseKey, int]] = []
    _seed_batch(migrated, store, "tp_demo_v3", "b-pair")
    sessions = []
    for pid in ("P1a", "P1b"):
        result = enter(
            migrated.runtime,
            store,
            query=_query(pid, "b-pair"),
            app_mode="qa",
            preview_key=None,
            current_manifest_sha256={},
            build_source_sha256=BUILD_SHA,
        )
        sid, writer = result.session_id, result.writer_id
        _run(
            migrated,
            store,
            sid,
            writer,
            "consent",
            {"action": "consented", "text_version": "consent.reference@v3"},
            1,
        )
        with migrated.runtime.connect() as conn:
            reg_row = conn.execute(
                select(s.session.c.compatibility_key, s.session.c.seed_hex).where(
                    s.session.c.session_id == sid
                )
            ).one()
        case = CaseKey(str(reg_row.compatibility_key), str(reg_row.seed_hex), 1)
        attempts.append((case, registry.start(case)))
        _run(migrated, store, sid, writer, "training_step", {"step": 1}, _stage(migrated, sid)[1])
        # BA-95: completion requires a committed practice share first.
        _run(
            migrated,
            store,
            sid,
            writer,
            "training_step",
            {"step": "practice", "action": "share", "record_id": "PRACTICE-K3-04"},
            _stage(migrated, sid)[1],
        )
        _run(
            migrated,
            store,
            sid,
            writer,
            "training_step",
            {"step": "practice", "action": "pick_location", "crate": 3, "location": "The Sawmill"},
            _stage(migrated, sid)[1],
        )

        _run(
            migrated,
            store,
            sid,
            writer,
            "training_step",
            {"step": "practice", "action": "ask", "text": "anything on crate 3?"},
            _stage(migrated, sid)[1],
        )
        _run(
            migrated,
            store,
            sid,
            writer,
            "training_step",
            {
                "step": "comprehension",
                "complete": True,
                "answers": dict(lifecycle.COMPREHENSION_KEY),
            },
            _stage(migrated, sid)[1],
        )
        _force_wall_due(migrated, sid, "connecting")
        _tick(migrated, store)
        rev = _stage(migrated, sid)[1]
        out = _run(migrated, store, sid, writer, "ready", {}, rev, accept=False)
        if not out.accepted:  # the connecting sweep may have bumped the revision
            _run(migrated, store, sid, writer, "ready", {}, _stage(migrated, sid)[1])
        _force_wall_due(migrated, sid, "task_open")
        _tick(migrated, store)
        sessions.append((sid, writer))

    with migrated.runtime.connect() as conn:
        arms = [
            conn.execute(select(s.session.c.arm).where(s.session.c.session_id == sid)).scalar_one()
            for sid, _ in sessions
        ]
    assert len(set(arms)) == 2, f"same-block enrollment should split arms, got {arms}"

    # Drive both through identical commands to the 60 s check-in.
    for sid, _writer in sessions:
        _run(
            migrated,
            store,
            sid,
            _writer,
            "answer_update",
            {"crate": 1, "location_id": "L9"},
            _stage(migrated, sid)[1],
        )
        _set_active(migrated, sid, 60_000)
        _tick(migrated, store)
        assert _stage(migrated, sid)[0] == "checkin_collect"

    streams = [[e["type"] for e in _all_events(migrated, sid)] for sid, _ in sessions]
    assert streams[0] == streams[1]
    # The manipulation surface diverges exactly where it should: the revealed seat values.
    reveals = [
        next(
            e["payload"]["assigned_values"]
            for e in _all_events(migrated, sid)
            if e["type"] == "checkin_reveal"
            or (e["type"] == "checkin_open" and e["payload"].get("assigned_values"))
        )
        for sid, _ in sessions
    ]
    assert reveals[0] != reveals[1]

    # Both realizations register as completed attempts; pending() is empty by
    # case identity — never "requested minus finished" (HB-08 lesson).
    for case, attempt in attempts:
        registry.finish(case, attempt, ok=True, detail="identical-stream paired arm")
    for case, _attempt in attempts:
        assert registry.state(case).done
    assert registry.pending(case.compatibility_key, [case.seed for case, _ in attempts], 1) == []


def _all_events(db: MigratedDatabase, sid: uuid.UUID) -> list[Mapping[str, object]]:
    with db.runtime.connect() as conn:
        return (
            conn.execute(select(s.event).where(s.event.c.session_id == sid).order_by(s.event.c.seq))
            .mappings()
            .all()
        )
