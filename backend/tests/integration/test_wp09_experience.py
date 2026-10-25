"""INV-0901..0910: WP09 participant-experience behavior against disposable PostgreSQL.

Covered here, beyond the WP06-WP08 plumbing tests:

- TP check-in: own rating + skip, 3.5 s wall-clock collection, exact scripted teammate
  values + seat mapping, reveal receipts, explicit resume and a paused active clock.
- tp_review_first: solo_review channel lock, answer drafting, then the check-in hands
  off to the task phase at active second 60.
- PE: arm-bound exposure surfaces with durable receipts and never-fabricated control
  surfaces; no TP pause can exist in a PE session.
- Instruments: per-seat peer ratings, confidence missing-vs-answered, item/skip
  semantics and answer rejection after freeze.
- Exits: withdrawal preserves records; same-batch re-entry resumes.
- Completion: receipt + deterministic handoff code, retrievable via ``GET /session``.
- Projection: bound material + practice world served stage-scoped, no arm leak.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select, text, update

from app.db import schema as s
from app.db.tx import Command, CommandResult, TxContext, execute_command
from app.domain.manifests.store import ContentStore
from app.main import create_app
from app.services import activetime, credentials, lifecycle, scheduler
from app.services.entry import EntryResult, enter
from app.services.preview import issue_descriptor
from app.timing.clock import VirtualClock

from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration

BUILD_SHA = "ab" * 32
KEY = b"k" * 32


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


def _preview_entry(
    db: MigratedDatabase,
    store: ContentStore,
    manifest_id: str,
    arm: str,
    *,
    nonce_seed: str,
) -> EntryResult:
    """Pinned-arm preview session — deterministic arm without burning an allocation."""
    stored = store.manifests[manifest_id]
    token = issue_descriptor(
        KEY,
        manifest_id=manifest_id,
        manifest_sha256=stored.sha256,
        arm=arm,  # type: ignore[arg-type]
        seed=nonce_seed * 16,
        mode="preview",
        timing_mode="faithful",
        now=datetime.now(UTC),
        lifetime=timedelta(hours=1),
        issuer="test",
    )
    return enter(
        db.runtime,
        store,
        query={"pt": token},
        app_mode="preview",
        preview_key=KEY,
        current_manifest_sha256={manifest_id: stored.sha256},
        build_source_sha256=BUILD_SHA,
    )


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


def _set_active(db: MigratedDatabase, sid: uuid.UUID, active_ms: int) -> None:
    """Jump the session's open running interval to ``active_ms`` elapsed."""
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
                " WHERE session_id = :sid AND status = 'pending' AND clock_basis = 'active_task'"
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


def _exposures(db: MigratedDatabase, sid: uuid.UUID) -> list[Mapping[str, Any]]:
    with db.runtime.connect() as conn:
        return (
            conn.execute(
                select(s.manipulation_exposure)
                .where(s.manipulation_exposure.c.session_id == sid)
                .order_by(s.manipulation_exposure.c.scheduled_at)
            )
            .mappings()
            .all()
        )


def _events(db: MigratedDatabase, sid: uuid.UUID) -> list[Mapping[str, Any]]:
    with db.runtime.connect() as conn:
        return (
            conn.execute(select(s.event).where(s.event.c.session_id == sid).order_by(s.event.c.seq))
            .mappings()
            .all()
        )


def _drive_to_task(
    db: MigratedDatabase,
    store: ContentStore,
    manifest_id: str,
    pid: str,
    batch: str,
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
    )
    sid, writer = result.session_id, result.writer_id
    from app.domain.manifests.binding import bind

    bound = bind(store, manifest_id, mode="qa")
    _run(
        db,
        store,
        sid,
        writer,
        "consent",
        {
            "action": "consented",
            "text_version": bound.artifacts["instruments.consent"].key,
        },
        1,
    )
    _run(db, store, sid, writer, "training_step", {"step": "instructions"}, _stage(db, sid)[1])
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
    _force_wall_due(db, sid, "connecting")
    _tick(db, store)
    _run(db, store, sid, writer, "ready", {}, _stage(db, sid)[1])
    _force_wall_due(db, sid, "task_open")
    _tick(db, store)
    assert _stage(db, sid)[0] in ("task", "solo_review")
    return result


def _drive_preview_to_task(
    db: MigratedDatabase, store: ContentStore, manifest_id: str, arm: str, seed_hex: str
) -> EntryResult:
    result = _preview_entry(db, store, manifest_id, arm, nonce_seed=seed_hex)
    sid, writer = result.session_id, result.writer_id
    _run(
        db,
        store,
        sid,
        writer,
        "consent",
        {"action": "consented", "text_version": "consent.reference@v3"},
        _stage(db, sid)[1],
    )
    _run(db, store, sid, writer, "training_step", {"step": "instructions"}, _stage(db, sid)[1])
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
    _force_wall_due(db, sid, "connecting")
    _tick(db, store)
    _run(db, store, sid, writer, "ready", {}, _stage(db, sid)[1])
    _force_wall_due(db, sid, "task_open")
    _tick(db, store)
    return result


def _authed_client(make_settings: Any, db: MigratedDatabase, result: EntryResult) -> TestClient:
    settings = make_settings(
        APP_MODE="preview",
        DATABASE_URL=db.dsns.runtime,
        PREVIEW_SIGNING_KEY="k" * 40,
    )
    app = create_app(settings, clock=VirtualClock(), engine=db.runtime, configure_logs=False)
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set(credentials.SESSION_COOKIE, result.credential_token, path="/")
    client.cookies.set(credentials.CSRF_COOKIE, "csrf-test-token", path="/")
    # The shared cookie stays in the jar (as in a real browser profile) while the
    # per-tab header carries the writer identity the server now requires (BA-26).
    client.cookies.set("inv_writer", str(result.writer_id), path="/")
    client.headers["x-writer-id"] = str(result.writer_id)
    return client


def _collect_keys(node: Any, out: set[str]) -> None:
    if isinstance(node, Mapping):
        for key, value in node.items():
            out.add(str(key))
            _collect_keys(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_keys(item, out)


FORBIDDEN_KEYS = {
    "arm",
    "condition",
    "truth",
    "seed",
    "role",
    "provider",
    "model",
    "debug",
    "answer_key",
    "holdings",
}


# ----- INV-0902: TP check-in -----------------------------------------------------------------


def test_tp_checkin_pause_reveal_receipts_and_resume(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_v3", "CHK1", "qa-chk1")
    sid, writer = result.session_id, result.writer_id

    # Open the check-in at active second 60 — the job is on the active clock.
    _set_active(migrated, sid, 60_000)
    _tick(migrated, store)
    assert _stage(migrated, sid)[0] == "checkin_collect"

    # The active clock is paused across the check-in.
    assert _open_interval(migrated, sid)["kind"] == "paused"  # type: ignore[index]
    active_at_open = _active(migrated, sid)

    exposures = _exposures(migrated, sid)
    assert len(exposures) == 1 and exposures[0]["kind"] == "pressure_checkin"
    assert exposures[0]["status"] == "committed"
    assigned = dict(exposures[0]["assigned_values"])
    # Exact scripted teammate values for the session's arm, seat-mapped.
    assert set(assigned) == {"JT", "PM"}
    with migrated.runtime.connect() as conn:
        arm = conn.execute(
            select(s.session.c.arm).where(s.session.c.session_id == sid)
        ).scalar_one()
    manifest = store.manifests["tp_demo_v3"].manifest
    expected = sorted(manifest.arm_spec(arm).manipulation.teammate_values)
    assert sorted(assigned.values()) == expected

    # Own rating, then the 3.5 s collection presentation is a wall-clock job.
    out = _run(
        migrated, store, sid, writer, "pressure_response", {"value": 5}, _stage(migrated, sid)[1]
    )
    assert _stage(migrated, sid)[0] == "checkin_wait"
    assert out.accepted
    reveal = next(j for j in _jobs(migrated, sid) if j["kind"] == "checkin_reveal")
    exposure = _exposures(migrated, sid)[0]
    assert exposure["own_rating"] == 5 and exposure["status"] == "acknowledged"
    assert reveal["due_wall_at"] is not None  # wall-clock, not active

    # Active time must not advance while the collection presentation holds.
    assert _active(migrated, sid) == active_at_open
    _force_wall_due(migrated, sid, "checkin_reveal")
    _tick(migrated, store)
    assert _stage(migrated, sid)[0] == "checkin_reveal"
    exposure = _exposures(migrated, sid)[0]
    assert exposure["rendered_seq"] is not None and exposure["status"] == "rendered"
    assert _active(migrated, sid) == active_at_open  # still paused

    reveal_event = next(e for e in _events(migrated, sid) if e["type"] == "checkin_reveal")
    assert dict(reveal_event["payload"]["assigned_values"]) == assigned
    assert reveal_event["payload"]["own_rating"] == 5

    # Explicit resume restarts the active clock in the same transaction.
    _run(migrated, store, sid, writer, "proceed", {}, _stage(migrated, sid)[1])
    assert _stage(migrated, sid)[0] == "task"
    assert _open_interval(migrated, sid)["kind"] == "running"  # type: ignore[index]


def test_tp_checkin_skip_and_inactivity_stop(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_v3", "CHK2", "qa-chk2")
    sid, writer = result.session_id, result.writer_id
    _set_active(migrated, sid, 60_000)
    _tick(migrated, store)
    _run(migrated, store, sid, writer, "pressure_skip", {}, _stage(migrated, sid)[1])
    exposure = _exposures(migrated, sid)[0]
    assert exposure["own_rating"] is None and exposure["status"] == "acknowledged"
    reveal_event = None
    _force_wall_due(migrated, sid, "checkin_reveal")
    _tick(migrated, store)
    reveal_event = next(e for e in _events(migrated, sid) if e["type"] == "checkin_reveal")
    assert reveal_event["payload"]["own_rating"] is None
    _run(migrated, store, sid, writer, "proceed", {}, _stage(migrated, sid)[1])
    assert _stage(migrated, sid)[0] == "task"


# ----- INV-0903: review-first -----------------------------------------------------------------


@pytest.mark.parametrize(
    "arm", ["high", "low"]
)  # BA-11: pinned-arm coverage, not random allocation
def test_review_first_solo_review_locks_channels(
    migrated: MigratedDatabase, store: ContentStore, arm: str
) -> None:
    result = _drive_preview_to_task(
        migrated, store, "tp_review_first_v4", arm, "ab" if arm == "high" else "cd"
    )
    sid, writer = result.session_id, result.writer_id
    assert _stage(migrated, sid)[0] == "solo_review"

    # Answer drafting is allowed; the locked channels reject at commit time.
    out = _run(
        migrated,
        store,
        sid,
        writer,
        "answer_update",
        {"crate": 1, "location_id": "The Sawmill"},
        _stage(migrated, sid)[1],
    )
    assert out.accepted
    out = _run(
        migrated,
        store,
        sid,
        writer,
        "assistant_request",
        {"body": "help", "crate": 1},
        _stage(migrated, sid)[1],
        accept=False,
    )
    assert not out.accepted and out.reason == "channel_closed"
    out = _run(
        migrated,
        store,
        sid,
        writer,
        "teammate_request",
        {"crate": 1},
        _stage(migrated, sid)[1],
        accept=False,
    )
    assert not out.accepted and out.reason == "wrong_stage"

    # At active 60 the check-in fires inside solo_review, pauses the clock, and hands
    # off to the task phase on explicit resume.
    _set_active(migrated, sid, 60_000)
    _tick(migrated, store)
    assert _stage(migrated, sid)[0] == "checkin_collect"
    _run(migrated, store, sid, writer, "pressure_skip", {}, _stage(migrated, sid)[1])
    _force_wall_due(migrated, sid, "checkin_reveal")
    _tick(migrated, store)
    _run(migrated, store, sid, writer, "proceed", {}, _stage(migrated, sid)[1])
    assert _stage(migrated, sid)[0] == "task"

    # Scripted requests were scheduled on the post-review axis (80/180/270 s), not the
    # reference profile's 40/150/240.
    scripted = sorted(
        int(j["due_active_ms"]) for j in _jobs(migrated, sid) if j["kind"] == "scripted_request"
    )
    assert scripted == [80_000, 180_000, 270_000]
    assert _active(migrated, sid) < 80_000  # 300 s of task time remain after the check-in


# ----- INV-0904: PE exposure -----------------------------------------------------------------


@pytest.mark.parametrize("manifest_id", ["pe_demo_v3", "pe_pilot_v4"])  # BA-11: both PE profiles
def test_pe_eval_exposure_receipts_and_system_message(
    migrated: MigratedDatabase, store: ContentStore, manifest_id: str
) -> None:
    result = _drive_preview_to_task(
        migrated, store, manifest_id, "eval", "e1" if manifest_id == "pe_demo_v3" else "f1"
    )
    sid, _writer = result.session_id, result.writer_id
    assert _stage(migrated, sid)[0] == "task"

    kinds = [e["kind"] for e in _exposures(migrated, sid)]
    assert kinds == ["pe_intro", "pe_callout", "pe_strip", "pe_system_message"]
    for e in _exposures(migrated, sid):
        assert e["status"] == "committed" and e["artifact_id"] == "exposure.pe_eval_demo"

    # The bound system message is delivered as a real committed message at task open.
    with migrated.runtime.connect() as conn:
        msgs = (
            conn.execute(
                select(s.message).where(
                    s.message.c.session_id == sid, s.message.c.channel == "system"
                )
            )
            .mappings()
            .all()
        )
    assert any("rate how helpful" in m["body"] for m in msgs)

    # PE sessions have no check-in machinery at all.
    assert not [j for j in _jobs(migrated, sid) if j["kind"].startswith("checkin")]


@pytest.mark.parametrize("manifest_id", ["pe_demo_v3", "pe_pilot_v4"])  # BA-11
def test_pe_control_no_fabricated_exposure(
    migrated: MigratedDatabase, store: ContentStore, manifest_id: str
) -> None:
    result = _drive_preview_to_task(
        migrated, store, manifest_id, "control", "c1" if manifest_id == "pe_demo_v3" else "d1"
    )
    sid = result.session_id
    kinds = [e["kind"] for e in _exposures(migrated, sid)]
    # The control copy leaves strip/system_message empty — nothing is committed or shown.
    assert kinds == ["pe_intro", "pe_callout"]
    for e in _exposures(migrated, sid):
        assert e["artifact_id"] == "exposure.pe_control_demo"
    with migrated.runtime.connect() as conn:
        n_system = conn.execute(
            select(func.count())
            .select_from(s.message)
            .where(s.message.c.session_id == sid, s.message.c.channel == "system")
        ).scalar_one()
    assert n_system == 0


# ----- INV-0905: instruments after the freeze -------------------------------------------------


def _complete_checkin_if_tp(
    db: MigratedDatabase, store: ContentStore, manifest_id: str, sid: uuid.UUID, writer: uuid.UUID
) -> None:
    """Drive TP sessions through the 60 s check-in so the clock can reach the deadline.

    Responds with a rating rather than skipping — UX round 4 profiles (v6+)
    refuse ``pressure_skip`` (UX round 3, item 5)."""
    if manifest_id.startswith("pe_"):
        return
    _set_active(db, sid, 60_000)
    _tick(db, store)
    assert _stage(db, sid)[0] == "checkin_collect"
    _run(db, store, sid, writer, "pressure_response", {"value": 5}, _stage(db, sid)[1])
    _force_wall_due(db, sid, "checkin_reveal")
    _tick(db, store)
    assert _stage(db, sid)[0] == "checkin_reveal"
    _run(db, store, sid, writer, "proceed", {}, _stage(db, sid)[1])
    assert _stage(db, sid)[0] == "task"


def _drive_to_confidence(
    db: MigratedDatabase,
    store: ContentStore,
    manifest_id: str,
    pid: str,
    batch: str,
    *,
    arm: str | None = None,
    answer_crates: tuple[int, ...] = (),
) -> EntryResult:
    if arm is None:
        result = _drive_to_task(db, store, manifest_id, pid, batch)
    else:
        result = _drive_preview_to_task(db, store, manifest_id, arm, "a1")
    sid, writer = result.session_id, result.writer_id
    _complete_checkin_if_tp(db, store, manifest_id, sid, writer)
    for crate in answer_crates:
        _run(
            db,
            store,
            sid,
            writer,
            "answer_update",
            {"crate": crate, "location_id": "The Marina"},
            _stage(db, sid)[1],
        )
    _set_active(db, sid, 360_000)
    _tick(db, store)
    assert _stage(db, sid)[0] == "answers_locked"
    _run(db, store, sid, writer, "proceed", {}, _stage(db, sid)[1])
    assert _stage(db, sid)[0] == "confidence"
    return result


def test_answer_updates_rejected_after_freeze(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_confidence(
        migrated, store, "tp_demo_v3", "FR1", "qa-fr1", answer_crates=(1,)
    )
    sid, writer = result.session_id, result.writer_id
    out = _run(
        migrated,
        store,
        sid,
        writer,
        "answer_update",
        {"crate": 2, "location_id": "The Marina"},
        _stage(migrated, sid)[1],
        accept=False,
    )
    assert not out.accepted and out.reason == "wrong_stage"


def test_confidence_missing_vs_answered_and_zero_crate_proceed(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    # Answered crates: one answered, one skipped, one deliberately missing-until-last.
    result = _drive_to_confidence(
        migrated, store, "tp_demo_v3", "CF1", "qa-cf1", answer_crates=(1, 2, 3)
    )
    sid, writer = result.session_id, result.writer_id
    rev = _stage(migrated, sid)[1]
    _run(
        migrated,
        store,
        sid,
        writer,
        "item_response",
        {"item_id": "confidence", "crate": 1, "value": 80},
        rev,
    )
    _run(
        migrated,
        store,
        sid,
        writer,
        "item_skip",
        {"item_id": "confidence", "crate": 2},
        _stage(migrated, sid)[1],
    )
    # A rating for a crate that was never answered is rejected — never fabricated.
    out = _run(
        migrated,
        store,
        sid,
        writer,
        "item_response",
        {"item_id": "confidence", "crate": 8, "value": 50},
        _stage(migrated, sid)[1],
        accept=False,
    )
    assert not out.accepted and out.reason == "confidence_not_applicable"
    assert _stage(migrated, sid)[0] == "confidence"  # crate 3 still pending
    _run(
        migrated,
        store,
        sid,
        writer,
        "item_skip",
        {"item_id": "confidence", "crate": 3},
        _stage(migrated, sid)[1],
    )
    assert _stage(migrated, sid)[0] == "post_questionnaire"  # tp has no peer_ratings
    with migrated.runtime.connect() as conn:
        rows = (
            conn.execute(
                select(
                    s.confidence_response.c.crate,
                    s.confidence_response.c.status,
                    s.confidence_response.c.value,
                ).where(s.confidence_response.c.session_id == sid)
            )
            .mappings()
            .all()
        )
    by_crate = {r["crate"]: r for r in rows}
    assert by_crate[1]["status"] == "answered" and by_crate[1]["value"] == 80
    assert by_crate[2]["status"] == "skipped" and by_crate[2]["value"] is None
    assert by_crate[3]["status"] == "skipped"

    # Zero answered crates -> no pending items; explicit proceed advances.
    result2 = _drive_to_confidence(migrated, store, "tp_demo_v3", "CF2", "qa-cf2")
    sid2, writer2 = result2.session_id, result2.writer_id
    _run(migrated, store, sid2, writer2, "proceed", {}, _stage(migrated, sid2)[1])
    assert _stage(migrated, sid2)[0] == "post_questionnaire"


@pytest.mark.parametrize("manifest_id", ["tp_ai_v2", "tp_scripted_v4"])
def test_required_items_reject_skips(
    migrated: MigratedDatabase, store: ContentStore, manifest_id: str
) -> None:
    """2026-10-08 (owner direction): the launch builds bind required-only
    instruments — the server refuses every skip and gates proceeds."""
    result = _drive_to_confidence(
        migrated, store, manifest_id, "RQ1", f"qa-rq-{manifest_id}", answer_crates=(1,)
    )
    sid, writer = result.session_id, result.writer_id

    # Confidence: skip rejected; proceed refused while the item is unanswered.
    out = _run(
        migrated,
        store,
        sid,
        writer,
        "item_skip",
        {"item_id": "confidence", "crate": 1},
        _stage(migrated, sid)[1],
        accept=False,
    )
    assert not out.accepted and out.reason == "skip_not_permitted"
    out = _run(
        migrated,
        store,
        sid,
        writer,
        "proceed",
        {},
        _stage(migrated, sid)[1],
        accept=False,
    )
    assert not out.accepted and out.reason == "instrument_incomplete"
    _run(
        migrated,
        store,
        sid,
        writer,
        "item_response",
        {"item_id": "confidence", "crate": 1, "value": 80},
        _stage(migrated, sid)[1],
    )
    assert _stage(migrated, sid)[0] == "post_questionnaire"

    # Questionnaire: same rejection, and proceeding needs every item answered.
    out = _run(
        migrated,
        store,
        sid,
        writer,
        "item_skip",
        {"item_id": "tp_post_01"},
        _stage(migrated, sid)[1],
        accept=False,
    )
    assert not out.accepted and out.reason == "skip_not_permitted"
    out = _run(
        migrated,
        store,
        sid,
        writer,
        "proceed",
        {},
        _stage(migrated, sid)[1],
        accept=False,
    )
    assert not out.accepted and out.reason == "instrument_incomplete"


@pytest.mark.parametrize("manifest_id", ["tp_ai_v2", "tp_scripted_v4"])
def test_checkin_pressure_skip_rejected_on_launch_builds(
    migrated: MigratedDatabase, store: ContentStore, manifest_id: str
) -> None:
    """own_rating_skippable=false on the launch builds — the server refuses
    ``pressure_skip`` even though the UI shows no skip control."""
    result = _drive_to_task(migrated, store, manifest_id, "PS1", f"qa-ps-{manifest_id}")
    sid, writer = result.session_id, result.writer_id
    _set_active(migrated, sid, 60_000)
    _tick(migrated, store)
    assert _stage(migrated, sid)[0] == "checkin_collect"
    out = _run(
        migrated,
        store,
        sid,
        writer,
        "pressure_skip",
        {},
        _stage(migrated, sid)[1],
        accept=False,
    )
    assert not out.accepted and out.reason == "skip_not_permitted"


def test_peer_ratings_persist_per_seat(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = _drive_to_confidence(
        migrated, store, "pe_demo_v3", "PR1", "x", arm="control", answer_crates=(1,)
    )
    sid, writer = result.session_id, result.writer_id
    _run(
        migrated,
        store,
        sid,
        writer,
        "item_skip",
        {"item_id": "confidence", "crate": 1},
        _stage(migrated, sid)[1],
    )
    assert _stage(migrated, sid)[0] == "peer_ratings"
    # The peer-intro exposure receipt committed as the stage opened.
    kinds = [e["kind"] for e in _exposures(migrated, sid)]
    assert "peer_intro" in kinds

    # Per-seat responses: one row per (item, seat); both seats required.
    out = _run(
        migrated,
        store,
        sid,
        writer,
        "item_response",
        {"item_id": "peer_helpfulness", "value": 6},
        _stage(migrated, sid)[1],
        accept=False,
    )
    assert not out.accepted and out.reason == "seat_required"
    _run(
        migrated,
        store,
        sid,
        writer,
        "item_response",
        {"item_id": "peer_helpfulness", "value": 6, "seat": "JT"},
        _stage(migrated, sid)[1],
    )
    assert _stage(migrated, sid)[0] == "peer_ratings"  # PM still pending
    _run(
        migrated,
        store,
        sid,
        writer,
        "item_response",
        {"item_id": "peer_helpfulness", "value": 4, "seat": "PM"},
        _stage(migrated, sid)[1],
    )
    assert _stage(migrated, sid)[0] == "post_questionnaire"
    with migrated.runtime.connect() as conn:
        rows = (
            conn.execute(
                select(s.item_response.c.item_id, s.item_response.c.response).where(
                    s.item_response.c.session_id == sid
                )
            )
            .mappings()
            .all()
        )
    by_item = {r["item_id"]: r["response"] for r in rows}
    assert by_item["peer_helpfulness.JT"]["value"] == 6
    assert by_item["peer_helpfulness.PM"]["value"] == 4


def test_post_task_items_in_order_and_debrief_to_completion(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_confidence(
        migrated, store, "tp_demo_v3", "PQ1", "qa-pq1", answer_crates=(1,)
    )
    sid, writer = result.session_id, result.writer_id
    _run(
        migrated,
        store,
        sid,
        writer,
        "item_skip",
        {"item_id": "confidence", "crate": 1},
        _stage(migrated, sid)[1],
    )
    assert _stage(migrated, sid)[0] == "post_questionnaire"
    # Declared order: tp_post_01..09; answer each in turn — the suspicion item precedes
    # debrief by construction of the stage graph.
    item_ids = [f"tp_post_{i:02d}" for i in range(1, 10)]
    for item_id in item_ids:
        out = _run(
            migrated,
            store,
            sid,
            writer,
            "item_skip",
            {"item_id": item_id},
            _stage(migrated, sid)[1],
        )
        assert out.accepted
    # BA-105: tp_demo_v3 binds no instruments.open_ended — the last skip lands
    # on debrief directly (artifact-bound coverage lives on tp_ai_v13).
    assert _stage(migrated, sid)[0] == "debrief"

    _run(migrated, store, sid, writer, "debrief_ack", {}, _stage(migrated, sid)[1])
    assert _stage(migrated, sid)[0] == "data_use_choice"
    _run(
        migrated,
        store,
        sid,
        writer,
        "data_use_choice",
        {"choice": "use_no_free_text"},
        _stage(migrated, sid)[1],
    )
    out = execute_command(
        migrated.runtime,
        _cmd(sid, "complete", {}, _stage(migrated, sid)[1]),
        lifecycle.completion_handler(store=store, writer=writer),
        advance_due=_advance(store),
    )
    assert out.accepted and out.result["stage"] == "completion"
    code = out.result["completion_code"]
    assert code == "PLACEHOLDER-CODE"
    with migrated.runtime.connect() as conn:
        receipt = (
            conn.execute(
                select(s.completion_receipt).where(s.completion_receipt.c.session_id == sid)
            )
            .mappings()
            .one()
        )
    assert receipt["handoff"]["completion_code"] == code


def test_pre_debrief_choice_item_validates_options(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """UX round 4, item 14: post_task.tp_demo@v2 ends with the teammate-nature
    question — off-list values reject (``unknown_option``), a declared option
    records, and debrief follows it immediately."""
    result = _drive_to_confidence(
        migrated, store, "tp_demo_v6", "PC1", "qa-pc1", answer_crates=(1,)
    )
    sid, writer = result.session_id, result.writer_id
    _run(
        migrated,
        store,
        sid,
        writer,
        "item_skip",
        {"item_id": "confidence", "crate": 1},
        _stage(migrated, sid)[1],
    )
    assert _stage(migrated, sid)[0] == "post_questionnaire"
    for item_id in [f"tp_post_{i:02d}" for i in range(1, 10)]:
        out = _run(
            migrated,
            store,
            sid,
            writer,
            "item_skip",
            {"item_id": item_id},
            _stage(migrated, sid)[1],
        )
        assert out.accepted
    out = _run(
        migrated,
        store,
        sid,
        writer,
        "item_response",
        {"item_id": "tp_post_10", "value": "they were robots"},
        _stage(migrated, sid)[1],
        accept=False,
    )
    assert not out.accepted and out.reason == "unknown_option"
    _run(
        migrated,
        store,
        sid,
        writer,
        "item_response",
        {"item_id": "tp_post_10", "value": "All were human"},
        _stage(migrated, sid)[1],
    )
    # BA-105: tp_demo_v6 binds no instruments.open_ended — the choice answer
    # completes the instrument and debrief follows it immediately.
    assert _stage(migrated, sid)[0] == "debrief"
    with migrated.runtime.connect() as conn:
        row = (
            conn.execute(
                select(s.item_response.c.response).where(
                    s.item_response.c.session_id == sid,
                    s.item_response.c.item_id == "tp_post_10",
                )
            )
            .mappings()
            .one()
        )
    assert row["response"]["value"] == "All were human"


# ----- INV-0907/0908: completion recovery + exits ----------------------------------------------


def test_completion_code_survives_reload_via_session_endpoint(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    result = _drive_to_confidence(
        migrated, store, "tp_demo_v3", "CP1", "qa-cp1", answer_crates=(1,)
    )
    sid, writer = result.session_id, result.writer_id
    _run(
        migrated,
        store,
        sid,
        writer,
        "item_skip",
        {"item_id": "confidence", "crate": 1},
        _stage(migrated, sid)[1],
    )
    for i in range(1, 10):
        _run(
            migrated,
            store,
            sid,
            writer,
            "item_skip",
            {"item_id": f"tp_post_{i:02d}"},
            _stage(migrated, sid)[1],
        )
    # BA-105: tp_demo_v3 binds no instruments.open_ended — post goes straight
    # to debrief.
    _run(migrated, store, sid, writer, "debrief_ack", {}, _stage(migrated, sid)[1])
    _run(
        migrated,
        store,
        sid,
        writer,
        "data_use_choice",
        {"choice": "use_all"},
        _stage(migrated, sid)[1],
    )
    client = _authed_client(make_settings, migrated, result)
    with client:
        rev = client.get("/session").json()["session"]["revision"]
        resp = client.post(
            "/complete",
            json={"command_id": str(uuid.uuid4()), "expected_revision": rev},
            headers={credentials.CSRF_HEADER: "csrf-test-token"},
        )
        assert resp.status_code == 200, resp.text
        code = resp.json()["result"]["completion_code"]
        # Refresh: the snapshot re-serves the same code — no dead end.
        snap = client.get("/session").json()["session"]
        assert snap["stage"] == "completion"
        assert snap["completion_code"] == code


def test_withdraw_mid_task_preserves_records(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_v3", "WD1", "qa-wd1")
    sid, writer = result.session_id, result.writer_id
    _run(
        migrated,
        store,
        sid,
        writer,
        "answer_update",
        {"crate": 1, "location_id": "The Marina"},
        _stage(migrated, sid)[1],
    )
    out = _run(migrated, store, sid, writer, "withdraw", {}, _stage(migrated, sid)[1])
    assert out.accepted and _stage(migrated, sid)[0] == "withdrawn"
    with migrated.runtime.connect() as conn:
        session = (
            conn.execute(select(s.session).where(s.session.c.session_id == sid)).mappings().one()
        )
        assert session["terminal_reason"] == "participant_withdrawal"
        # Records are preserved; no manipulation exposure fabricated for an early exit.
        n_answers = conn.execute(
            select(func.count())
            .select_from(s.answer_revision)
            .where(s.answer_revision.c.session_id == sid)
        ).scalar_one()
        assert n_answers == 1
        assert _exposures(migrated, sid) == []


def test_same_batch_reentry_resumes_session(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_v3", "RE1", "qa-re1")
    sid = result.session_id
    # Re-entry presents the live session credential — a panel ID alone is linkage,
    # never a credential (spec §5.1 / BA-01).
    again = enter(
        migrated.runtime,
        store,
        query=_query("RE1", "qa-re1"),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
        presented_token=result.credential_token,
    )
    assert again.resumed and again.session_id == sid
    assert _stage(migrated, sid)[0] == "task"  # stage restored from the ledger


# ----- INV-0901: bound material + practice world projection -----------------------------------


def test_workbench_serves_material_and_practice_without_arm_leak(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    result = _preview_entry(migrated, store, "pe_demo_v3", "control", nonce_seed="ab")
    sid, writer = result.session_id, result.writer_id
    client = _authed_client(make_settings, migrated, result)
    with client:
        wb = client.get("/workbench").json()
        # Consent stage: the bound material_reference descriptor is served.
        assert wb["material"]["artifact_key"] == "consent.reference@v3"
        assert wb["material"]["body"]["material"] == "consent"
        # v3 carries literal Appendix B copy pending D06 freeze — proposed, not a
        # pointer placeholder.
        assert wb["material"]["status"] == "proposed_unapproved"
        assert "#XXXX-XXXX" in json.dumps(wb["material"]["body"])

        # Practice world during training: fixed records, separate namespace.
        _run(
            migrated,
            store,
            sid,
            writer,
            "consent",
            {"action": "consented", "text_version": "consent.reference@v3"},
            _stage(migrated, sid)[1],
        )
        # PE control: instructions exposes intro + callout (neutral copy), nothing else.
        wb = client.get("/workbench").json()
        assert set(wb["exposure"]) == {"intro", "callout"}
        assert "questionnaire" in wb["exposure"]["intro"]
        assert "rate" not in wb["exposure"]["intro"]

        _run(
            migrated,
            store,
            sid,
            writer,
            "training_step",
            {"step": "instructions"},
            _stage(migrated, sid)[1],
        )
        wb = client.get("/workbench").json()
        assert wb["material"]["artifact_key"] == "training.reference@v1"
        practice = wb["practice"]
        assert practice is not None
        ids = [r["record_id"] for r in practice["records"]]
        # UX round 4 (item 3): one record to share is enough practice.
        assert ids == ["PRACTICE-K3-04"]

        # No practice interaction may touch analytic tables.
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
        with migrated.runtime.connect() as conn:
            for table in (s.record, s.file_membership, s.answer_revision, s.request):
                n = conn.execute(
                    select(func.count()).select_from(table).where(table.c.session_id == sid)
                ).scalar_one()
                assert n == 0
            n_events = conn.execute(
                select(func.count())
                .select_from(s.event)
                .where(s.event.c.session_id == sid, s.event.c.type == "training_step")
            ).scalar_one()
            assert n_events >= 2

        # The whole projection stays free of arm/condition/truth internals.
        keys: set[str] = set()
        _collect_keys(wb, keys)
        assert not (keys & FORBIDDEN_KEYS)


def test_practice_complete_requires_a_committed_share(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """BA-95: a share command that never committed must not satisfy the required
    practice action — `complete` refuses without a durable share step, the stage
    holds, and a subsequent real share + complete advances normally."""
    result = _preview_entry(migrated, store, "tp_demo_v6", "high", nonce_seed="ba95")
    sid, writer = result.session_id, result.writer_id
    _run(
        migrated,
        store,
        sid,
        writer,
        "consent",
        {"action": "consented", "text_version": "consent.reference@v3"},
        _stage(migrated, sid)[1],
    )
    _run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {"step": "instructions"},
        _stage(migrated, sid)[1],
    )

    # Complete without any share attempt: refused, no stage transition.
    refused = _run(
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
        accept=False,
    )
    assert not refused.accepted
    assert refused.reason == "practice_share_required"
    assert _stage(migrated, sid)[0] == "training"

    # The audit's scenario: a share command rejected at the envelope layer (stale
    # revision — the frontend's failed-share path) commits nothing, so a later
    # complete must still refuse.
    _run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "share", "record_id": "PRACTICE-K3-04"},
        _stage(migrated, sid)[1] + 99,
        accept=False,
    )
    _run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "pick_location", "crate": 3, "location": "The Sawmill"},
        _stage(migrated, sid)[1] + 99,
        accept=False,
    )

    _run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "ask", "text": "anything on crate 3?"},
        _stage(migrated, sid)[1] + 99,
        accept=False,
    )
    blocked = _run(
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
        accept=False,
    )
    assert not blocked.accepted
    assert blocked.reason == "practice_share_required"
    assert _stage(migrated, sid)[0] == "training"

    # Retry path: a real committed share, then complete advances to connecting.
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
    done = _run(
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
    assert done.result["stage"] == "scripted_connecting"
    assert _stage(migrated, sid)[0] == "scripted_connecting"

    # The practice namespace stays out of analytic rows throughout.
    with migrated.runtime.connect() as conn:
        for table in (s.record, s.file_membership, s.answer_revision, s.request):
            n = conn.execute(
                select(func.count()).select_from(table).where(table.c.session_id == sid)
            ).scalar_one()
            assert n == 0


def test_practice_complete_requires_a_zero_miss_comprehension_pass(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """Practice comprehension gate: `complete` refuses until a zero-miss
    comprehension event is committed, every attempt is recorded with its
    `missed` list (returned to the client for highlighting), and a wrong-but-
    completing submission is accepted and recorded without advancing."""
    result = _preview_entry(migrated, store, "tp_demo_v6", "high", nonce_seed="c0de")
    sid, writer = result.session_id, result.writer_id
    _run(
        migrated,
        store,
        sid,
        writer,
        "consent",
        {"action": "consented", "text_version": "consent.reference@v3"},
        _stage(migrated, sid)[1],
    )
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

    # Completing without any comprehension attempt refuses; stage holds.
    refused = _run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {"step": 2, "complete": True},
        _stage(migrated, sid)[1],
        accept=False,
    )
    assert not refused.accepted
    assert refused.reason == "comprehension_required"
    assert _stage(migrated, sid)[0] == "training"

    # A missed answer returns the item ids for client-side highlighting and is
    # committed as a durable event payload.
    attempt = _run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {
            "step": "comprehension",
            "answers": {**dict(lifecycle.COMPREHENSION_KEY), "same_crates": True},
        },
        _stage(migrated, sid)[1],
    )
    assert attempt.result["missed"] == ["same_crates"]
    assert _stage(migrated, sid)[0] == "training"
    with migrated.runtime.connect() as conn:
        row = conn.execute(
            select(s.event.c.payload)
            .where(
                s.event.c.session_id == sid,
                s.event.c.type == "training_step",
                s.event.c.payload["step"].astext == "comprehension",
            )
            .order_by(s.event.c.seq.desc())
        ).first()
    assert row is not None
    assert row[0]["missed"] == ["same_crates"]
    assert row[0]["answers"]["same_crates"] is True

    # A complete that itself carries misses is still recorded (the attempt is
    # durable) but does not advance — the client retries off `missed`.
    held = _run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {
            "step": "comprehension",
            "complete": True,
            "answers": {"partial_intel": False},
        },
        _stage(migrated, sid)[1],
    )
    assert held.result["stage"] == "training"
    assert "partial_intel" in held.result["missed"]
    assert _stage(migrated, sid)[0] == "training"

    # Malformed answers are rejected without recording a pass.
    malformed = _run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {"step": "comprehension", "answers": "yes"},
        _stage(migrated, sid)[1],
        accept=False,
    )
    assert not malformed.accepted
    assert malformed.reason == "malformed_comprehension"

    # Zero-miss retry passes (missed empty), and complete advances normally.
    passed = _run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {"step": "comprehension", "answers": dict(lifecycle.COMPREHENSION_KEY)},
        _stage(migrated, sid)[1],
    )
    assert passed.result["missed"] == []
    done = _run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {"step": 2, "complete": True},
        _stage(migrated, sid)[1],
    )
    assert done.result["stage"] == "scripted_connecting"
    assert _stage(migrated, sid)[0] == "scripted_connecting"


def test_practice_gate_follows_the_bound_training_version(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """BA-106: the practice gate is bound-version aware — a session pinned to
    ``training.reference@v5`` (``tp_ai_v11``, the v10 cohort build) still
    refuses completion without the typed ask its copy promised, while a v6
    session (``tp_ai_v13``, UX_Feedback_v9) completes on the share alone. The
    same build executes both contracts."""
    from app.domain.manifests.binding import bind

    def _drive(manifest_id: str, nonce: str) -> tuple[uuid.UUID, uuid.UUID]:
        result = _preview_entry(migrated, store, manifest_id, "high", nonce_seed=nonce)
        sid, writer = result.session_id, result.writer_id
        consent_key = bind(store, manifest_id, mode="preview").artifacts["instruments.consent"].key
        _run(
            migrated,
            store,
            sid,
            writer,
            "consent",
            {"action": "consented", "text_version": consent_key},
            _stage(migrated, sid)[1],
        )
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
        return sid, writer

    def _complete(sid: uuid.UUID, writer: uuid.UUID):
        return _run(
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
            accept=False,
        )

    # v11 / training v5: share + zero-miss comprehension, no ask — refused.
    assert lifecycle.practice_ask_required(bind(store, "tp_ai_v11", mode="qa"))
    sid, writer = _drive("tp_ai_v11", "ba106")
    refused = _complete(sid, writer)
    assert not refused.accepted
    assert refused.reason == "practice_ask_required"
    assert _stage(migrated, sid)[0] == "training"
    # A committed practice ask satisfies the v5 contract.
    _run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "ask", "text": "anything on crate 3?"},
        _stage(migrated, sid)[1],
    )
    done = _complete(sid, writer)
    assert done.accepted and done.result["stage"] == "scripted_connecting"

    # v13 / training v6: share alone advances — the ask stays optional.
    assert not lifecycle.practice_ask_required(bind(store, "tp_ai_v13", mode="qa"))
    sid, writer = _drive("tp_ai_v13", "ba107")
    done = _complete(sid, writer)
    assert done.accepted and done.result["stage"] == "scripted_connecting"
