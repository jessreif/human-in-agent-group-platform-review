"""INV-0504/0505/0506/0508: world persistence, participant projections, structured
retrieval snapshots and add-to-file authorization against disposable PostgreSQL."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, insert, select, update

from app.db import schema as s
from app.db.export import export_session
from app.db.tx import Command, CommandResult, execute_command
from app.domain.manifests.store import ContentStore
from app.services import claims, lifecycle, retention, retrieval, scheduler, worlds
from app.services.entry import EntryResult, enter

from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration

BUILD_SHA = "ab" * 32


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _seed_batch(
    db: MigratedDatabase, store: ContentStore, batch_id: str, manifest_id: str = "tp_demo_v3"
) -> None:
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


def _run(
    db: MigratedDatabase,
    store: ContentStore,
    sid: uuid.UUID,
    writer: uuid.UUID,
    kind: str,
    payload: dict[str, object],
    revision: int,
) -> CommandResult:
    out = execute_command(
        db.runtime,
        _cmd(sid, kind, payload, revision),
        lifecycle.handler_for(kind, store=store, behavior_sha=BUILD_SHA, writer=writer),
    )
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
    pid: str = "W1",
    batch: str = "qa-w1",
    manifest_id: str = "tp_demo_v3",
) -> EntryResult:
    _seed_batch(db, store, batch, manifest_id)
    from app.domain.manifests.binding import bind

    consent_key = bind(store, manifest_id, mode="qa").artifacts["instruments.consent"].key
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
        {"action": "consented", "text_version": consent_key},
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
    # INV-0701: connecting presentation + post-start hold are wall jobs.
    for kind in ("connecting",):
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
    scheduler.tick(db.runtime, store, worker_id="t1", provider=None, limit=25)
    _run(db, store, sid, writer, "ready", {}, _stage(db, sid)[1])
    with db.runtime.begin() as conn:
        conn.execute(
            update(s.scheduled_job)
            .where(
                s.scheduled_job.c.session_id == sid,
                s.scheduled_job.c.kind == "task_open",
                s.scheduled_job.c.status == "pending",
            )
            .values(due_wall_at=func.now(), claimable_at=func.now())
        )
    scheduler.tick(db.runtime, store, worker_id="t1", provider=None, limit=25)
    assert _stage(db, sid)[0] == "task"
    return result


def test_world_persisted_at_ready(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = _drive_to_task(migrated, store)
    sid = result.session_id
    with migrated.runtime.connect() as conn:
        snap = (
            conn.execute(select(s.world_snapshot).where(s.world_snapshot.c.session_id == sid))
            .mappings()
            .one()
        )
        assert snap["generator"] == "investigators.world/demo_v1"
        content: dict[str, Any] = snap["content"]
        assert len(content["crates"]) == 8 and len(content["assigned"]) == 4
        n_records = conn.execute(
            select(func.count()).select_from(s.record).where(s.record.c.session_id == sid)
        ).scalar_one()
        assert n_records > 0
        n_holdings = conn.execute(
            select(func.count())
            .select_from(s.seat_holding)
            .where(s.seat_holding.c.session_id == sid)
        ).scalar_one()
        assert n_holdings == n_records  # every record is held exactly once
        member = conn.execute(
            select(func.count())
            .select_from(s.file_membership)
            .where(s.file_membership.c.session_id == sid, s.file_membership.c.source == "initial")
        ).scalar_one()
        assert member == len(content["initial_file_order"])
        n_draws = conn.execute(
            select(func.count())
            .select_from(s.draw_log)
            .where(s.draw_log.c.session_id == sid, s.draw_log.c.stream == "world")
        ).scalar_one()
        assert n_draws > 0


def test_participant_projection_has_no_truth_or_labels(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "W2", "qa-w2")
    sid = result.session_id
    with migrated.runtime.connect() as conn:
        file_rows = worlds.participant_file(conn, sid)
        content: dict[str, Any] = conn.execute(
            select(s.world_snapshot.c.content).where(s.world_snapshot.c.session_id == sid)
        ).scalar_one()
    assert file_rows, "participant file must not be empty"
    # O-4: shared_by is participant-facing provenance ("Shared by JT"), not truth.
    allowed = {"record_id", "crate", "stamp", "body", "event_type", "source", "shared_by"}
    truth = {c["truth"] for c in content["crates"]}
    for row in file_rows:
        assert set(row) <= allowed
        # no role labels or truth field anywhere in the projection
        assert "truth" not in row and "role" not in row
    # the projection's records are exactly file membership, in persisted order first
    assert [r["record_id"] for r in file_rows[: len(content["initial_file_order"])]] == [
        rid for rid in content["initial_file_order"]
    ]
    assert truth  # truth exists server-side, just never projected


def test_teammate_request_pins_snapshot(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = _drive_to_task(migrated, store, "W3", "qa-w3")
    sid = result.session_id
    out = _run(
        migrated,
        store,
        sid,
        result.writer_id,
        "teammate_request",
        {"crate": 1},
        _stage(migrated, sid)[1],
    )
    request_id = uuid.UUID(out.result["request_id"])
    snapshot_id = uuid.UUID(out.result["snapshot_id"])
    with migrated.runtime.connect() as conn:
        req = (
            conn.execute(select(s.request).where(s.request.c.request_id == request_id))
            .mappings()
            .one()
        )
        assert req["kind"] == "structured" and req["crate"] == 1
        ep: str = conn.execute(
            select(s.request_episode.c.status).where(s.request_episode.c.request_id == request_id)
        ).scalar_one()
        assert ep == "pending"
        snap = (
            conn.execute(
                select(s.knowledge_snapshot).where(
                    s.knowledge_snapshot.c.snapshot_id == snapshot_id
                )
            )
            .mappings()
            .one()
        )
        assert snap["purpose"] == "structured_retrieval"
        lead = retrieval.lead_from_manifest(conn, sid, snap["record_manifest"])
        assert lead is not None and lead[0] == req["responder"]


def test_seat_plan_world_deals_assignments_before_planning(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """UX v7: tp_scripted_v12 deals teammate assignments upfront and each
    scripted ask picks an ordinal off its own seat's dealt crates — the
    persisted snapshot carries asks only on the asker's beat."""
    result = _drive_to_task(migrated, store, "WSP", "qa-wsp", "tp_scripted_v12")
    sid = result.session_id
    with migrated.runtime.connect() as conn:
        content: dict[str, Any] = conn.execute(
            select(s.world_snapshot.c.content).where(s.world_snapshot.c.session_id == sid)
        ).scalar_one()
    assignments: dict[str, list[int]] = content["teammate_assignments"]
    assert all(len(v) == 4 for v in assignments.values())
    plans = sorted(content["scripted_requests"], key=lambda p: p["index"])
    assert [p["at_active_ms"] for p in plans] == [45_000, 100_000, 155_000, 210_000, 245_000]
    assert [p["seat"] for p in plans] == ["JT", "PM", "JT", "JT", "PM"]
    for p in plans:
        assert p["crate"] in assignments[p["seat"]]
        assert p["target"] == "seat_plan"


def test_add_to_file_command_is_gone_and_upsert_is_delivery_scoped(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """O-4 (2026-10-03): the manual ``add_to_file`` command was removed — the stage
    gate refuses the kind outright — and ``upsert_added_to_file`` is what delivery
    calls; it stays idempotent and marks ``added_from_team`` provenance."""
    result = _drive_to_task(migrated, store, "W4", "qa-w4")
    sid = result.session_id
    with migrated.runtime.connect() as conn:
        teammate_record = (
            conn.execute(
                select(s.seat_holding.c.record_id).where(
                    s.seat_holding.c.session_id == sid,
                    s.seat_holding.c.holder != "participant",
                    # holdings can overlap: pick a record the participant does not
                    # already own — the upsert correctly no-ops on initial rows.
                    s.seat_holding.c.record_id.not_in(
                        select(s.file_membership.c.record_id).where(
                            s.file_membership.c.session_id == sid
                        )
                    ),
                )
            )
            .scalars()
            .first()
        )
    rev = _stage(migrated, sid)[1]
    out = execute_command(
        migrated.runtime,
        _cmd(sid, "add_to_file", {"record_id": teammate_record}, rev),
        lifecycle.handler_for(
            "add_to_file", store=store, behavior_sha=BUILD_SHA, writer=result.writer_id
        ),
    )
    assert not out.accepted
    # a committed share is what files the record now — same write the
    # ``structured_record`` delivery path performs (O-4), via the idempotent upsert
    with migrated.superuser.begin() as conn:
        seq: int = conn.execute(
            select(s.session.c.next_event_seq).where(s.session.c.session_id == sid)
        ).scalar_one()
        conn.execute(
            insert(s.event).values(
                session_id=sid,
                seq=seq,
                event_id=uuid.uuid4(),
                stage="task",
                type="record_shared",
                actor="JT",
                payload={"record_id": teammate_record},
                server_time=func.now(),
            )
        )
        conn.execute(
            insert(s.share).values(
                session_id=sid,
                share_id=uuid.uuid4(),
                sharer="JT",
                record_id=teammate_record,
                recipients=["participant"],
                source="agent_proactive",
                delivered_seq=seq,
                delivered_at=func.now(),
            )
        )
        conn.execute(
            s.session.update().where(s.session.c.session_id == sid).values(next_event_seq=seq + 1)
        )
        assert teammate_record is not None
        assert worlds.upsert_added_to_file(conn, sid, teammate_record, seq) is True
        # idempotent: a second delivery-side upsert is a no-op
        assert worlds.upsert_added_to_file(conn, sid, teammate_record, seq) is False
    # the record now counts as delivered+in_file on the transfer ledger
    with migrated.runtime.connect() as conn:
        ledger = claims.transfer_ledger(conn, sid)
        pf = {r["record_id"]: r for r in worlds.participant_file(conn, sid)}
    assert "delivered" in ledger[teammate_record] and "in_file" in ledger[teammate_record]
    # O-4: the file projection names the sharer for the "Shared by" tag.
    assert pf[teammate_record]["source"] == "added_from_team"
    assert pf[teammate_record]["shared_by"] == "JT"


def test_participant_share_requires_membership(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "W5", "qa-w5")
    sid = result.session_id
    with migrated.runtime.connect() as conn:
        not_mine = (
            conn.execute(
                select(s.seat_holding.c.record_id).where(
                    s.seat_holding.c.session_id == sid,
                    s.seat_holding.c.holder != "participant",
                )
            )
            .scalars()
            .first()
        )
    out = execute_command(
        migrated.runtime,
        _cmd(sid, "record_share", {"record_id": not_mine}, _stage(migrated, sid)[1]),
        lifecycle.handler_for(
            "record_share", store=store, behavior_sha=BUILD_SHA, writer=result.writer_id
        ),
    )
    assert not out.accepted and out.reason == "record_not_in_file"
    with migrated.runtime.connect() as conn:
        own = worlds.participant_file(conn, sid)[0]["record_id"]
    out = _run(
        migrated,
        store,
        sid,
        result.writer_id,
        "record_share",
        {"record_id": own},
        _stage(migrated, sid)[1],
    )
    assert out.result["shared"] is True


def test_participant_share_dedupes(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = _drive_to_task(migrated, store, "W7", "qa-w7")
    sid = result.session_id
    with migrated.runtime.connect() as conn:
        own = worlds.participant_file(conn, sid)[0]["record_id"]
    _run(
        migrated,
        store,
        sid,
        result.writer_id,
        "record_share",
        {"record_id": own},
        _stage(migrated, sid)[1],
    )
    again = _run(
        migrated,
        store,
        sid,
        result.writer_id,
        "record_share",
        {"record_id": own},
        _stage(migrated, sid)[1],
    )
    assert again.result["already"] is True
    with migrated.runtime.connect() as conn:
        share_rows = conn.execute(
            select(func.count())
            .select_from(s.share)
            .where(s.share.c.session_id == sid, s.share.c.sharer == "participant")
        ).scalar_one()
        share_events = conn.execute(
            select(func.count())
            .select_from(s.event)
            .where(
                s.event.c.session_id == sid,
                s.event.c.type == "record_shared",
                s.event.c.actor == "participant",
            )
        ).scalar_one()
    assert share_rows == 1 and share_events == 1


def _enter_instructions(
    db: MigratedDatabase, store: ContentStore, pid: str, batch: str
) -> EntryResult:
    _seed_batch(db, store, batch)
    result = enter(
        db.runtime,
        store,
        query=_query(pid, batch),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    _run(
        db,
        store,
        result.session_id,
        result.writer_id,
        "consent",
        {"action": "consented", "text_version": "consent.reference@v3"},
        1,
    )
    assert _stage(db, result.session_id)[0] == "instructions"
    return result


def test_set_initials_stored_locked_and_validated(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _enter_instructions(migrated, store, "W8", "qa-w8")
    sid, writer = result.session_id, result.writer_id
    out = _run(
        migrated, store, sid, writer, "set_initials", {"initials": "ab"}, _stage(migrated, sid)[1]
    )
    # the receipt never echoes the value — participant_display holds it
    assert out.result == {"set": True}
    with migrated.runtime.connect() as conn:
        row = conn.execute(
            select(s.participant_display.c.initials).where(
                s.participant_display.c.session_id == sid
            )
        ).one()
        assert row.initials == "AB"
    # resubmitting the same value is an accepted no-op
    _run(migrated, store, sid, writer, "set_initials", {"initials": "AB"}, _stage(migrated, sid)[1])
    # changing initials is rejected; malformed values are rejected
    out = execute_command(
        migrated.runtime,
        _cmd(sid, "set_initials", {"initials": "cd"}, _stage(migrated, sid)[1]),
        lifecycle.handler_for("set_initials", store=store, behavior_sha=BUILD_SHA, writer=writer),
    )
    assert not out.accepted and out.reason == "initials_locked"
    out = execute_command(
        migrated.runtime,
        _cmd(sid, "set_initials", {"initials": "A1"}, _stage(migrated, sid)[1]),
        lifecycle.handler_for("set_initials", store=store, behavior_sha=BUILD_SHA, writer=writer),
    )
    assert not out.accepted and out.reason == "malformed_initials"


def test_initials_excluded_from_export_and_purged_on_tombstone(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _enter_instructions(migrated, store, "W9", "qa-w9")
    sid, writer = result.session_id, result.writer_id
    _run(migrated, store, sid, writer, "set_initials", {"initials": "JC"}, _stage(migrated, sid)[1])
    bundle = export_session(migrated.runtime, sid)
    assert "participant_display" not in bundle
    assert "JC" not in str(bundle)
    with migrated.runtime.begin() as conn:
        retention.apply_deletion(
            conn, sid, actor="test:cleanup", reason="t", scope="participant", dry_run=False
        )
    with migrated.runtime.connect() as conn:
        remaining = conn.execute(
            select(func.count())
            .select_from(s.participant_display)
            .where(s.participant_display.c.session_id == sid)
        ).scalar_one()
    assert remaining == 0
