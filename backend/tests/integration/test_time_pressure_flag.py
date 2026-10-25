"""TIME_PRESSURE_ENABLED=0 — disposable-DB acceptance runs.

The flag removes the low/high time-pressure manipulation in every mode while
keeping the neutral task clock:

- Round-robin TP session completes with zero pressure jobs — no check-in
  stages, warning, or pressure_remark rows — and no tp_post_09/11 items.
- The deadline job still runs (the task budget is not the manipulation).
- Review-first sessions leave ``solo_review`` via the neutral
  ``solo_review_end`` job at the phase boundary — the check-in's structural
  role — then complete normally.
- Flag-on control: the same manifests keep the full manipulation.

Each run also exercises the replay/adminviews era: flag-off sessions report
``time_pressure: false`` pages; flag-on history still reports true.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select

from app.db import schema as s
from app.domain.manifests.store import ContentStore
from app.flags import FeatureFlags
from app.services import activetime, scheduler
from harness import clockwork
from harness.cli import _StubProvider
from harness.personas import NormalPersona
from harness.runner import Runner, SessionReport

from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration

TP_MANIFEST = "tp_ai_v11"
# v10 is the last review-first profile with an actual solo_review phase
# (0-60s); v11 is task-first despite the name.
REVIEW_FIRST_MANIFEST = "tp_review_first_v10"
CHECKIN_STAGES = {"checkin_collect", "checkin_wait", "checkin_reveal"}
PRESSURE_ITEMS = {"tp_post_09", "tp_post_11"}


def _run(
    db: MigratedDatabase, manifest_id: str, *, time_pressure: bool, seed: int = 7
) -> tuple[uuid.UUID, object]:
    runner = Runner(
        db, ContentStore.load_default(), provider=_StubProvider(), time_pressure=time_pressure
    )
    batch = f"tp-flag.{manifest_id}.{'on' if time_pressure else 'off'}.{uuid.uuid4().hex[:8]}"
    runner.seed_batch(manifest_id, batch)
    report = runner.run_session(manifest_id, batch, NormalPersona(seed), seed=seed)
    assert report.stage == "completion", report.findings
    return uuid.UUID(report.session_id), report


def _stages(db: MigratedDatabase, sid: uuid.UUID) -> list[str]:
    with db.runtime.connect() as conn:
        return [
            r.to_stage
            for r in conn.execute(
                select(s.stage_transition.c.to_stage)
                .where(s.stage_transition.c.session_id == sid)
                .order_by(s.stage_transition.c.revision)
            )
        ]


def _jobs(db: MigratedDatabase, sid: uuid.UUID) -> dict[str, list[str]]:
    with db.runtime.connect() as conn:
        out: dict[str, list[str]] = {}
        for kind, status in conn.execute(
            select(s.scheduled_job.c.kind, s.scheduled_job.c.status).where(
                s.scheduled_job.c.session_id == sid
            )
        ):
            out.setdefault(kind, []).append(status)
        return out


def _events(db: MigratedDatabase, sid: uuid.UUID) -> set[str]:
    with db.runtime.connect() as conn:
        return set(
            conn.execute(select(s.event.c.type).where(s.event.c.session_id == sid)).scalars()
        )


def _item_rows(db: MigratedDatabase, sid: uuid.UUID) -> set[str]:
    with db.runtime.connect() as conn:
        return {
            str(r).split(".")[0]
            for r in conn.execute(
                select(s.item_response.c.item_id).where(s.item_response.c.session_id == sid)
            ).scalars()
        }


def test_flag_off_round_robin_completes_without_pressure(migrated: MigratedDatabase) -> None:
    sid, report = _run(migrated, TP_MANIFEST, time_pressure=False)
    stages = _stages(migrated, sid)
    assert not CHECKIN_STAGES & set(stages)
    jobs = _jobs(migrated, sid)
    # Every manipulation-only kind is absent — never scheduled, never suppressed.
    assert not (scheduler.PRESSURE_JOB_KINDS & set(jobs))
    # The neutral schedule is intact: deadline ran, clock closed normally.
    assert jobs.get("deadline") == ["committed"]
    assert "solo_review_end" not in jobs  # round-robin has no review window
    events = _events(migrated, sid)
    assert "task_clock_started" in events and "task_deadline" in events
    assert not {"checkin_open", "checkin_reveal", "warning_committed"} & events
    assert not PRESSURE_ITEMS & _item_rows(migrated, sid)
    replay_pages = (report.replay or {}).get("participant", {}).get("pages", {})
    assert replay_pages.get("time_pressure") is False


def test_flag_off_review_first_exits_solo_review_to_task(migrated: MigratedDatabase) -> None:
    """The check-in is the solo_review exit gate in flag-on review-first
    flows; under the flag the bare solo_review_end job takes that role at the
    same active-time boundary."""
    sid, report = _run(migrated, REVIEW_FIRST_MANIFEST, time_pressure=False)
    stages = _stages(migrated, sid)
    assert "solo_review" in stages and "task" in stages
    assert stages.index("solo_review") < stages.index("task")
    assert not CHECKIN_STAGES & set(stages)
    jobs = _jobs(migrated, sid)
    assert jobs.get("solo_review_end") == ["committed"]
    assert not (scheduler.PRESSURE_JOB_KINDS & set(jobs))
    assert "solo_review_ended" in _events(migrated, sid)
    assert "stage_proceeded" in _events(migrated, sid)
    assert not PRESSURE_ITEMS & _item_rows(migrated, sid)
    replay_pages = (report.replay or {}).get("participant", {}).get("pages", {})
    assert replay_pages.get("time_pressure") is False


def test_flag_on_control_keeps_full_manipulation(migrated: MigratedDatabase) -> None:
    """Same manifest, flag on: check-in, warning, remarks, and the pressure
    items all land — the flag is a filter, not a content change."""
    sid, report = _run(migrated, TP_MANIFEST, time_pressure=True)
    stages = _stages(migrated, sid)
    assert set(stages) >= CHECKIN_STAGES
    jobs = _jobs(migrated, sid)
    assert "checkin" in jobs and "warning" in jobs and "pressure_remark" in jobs
    assert "solo_review_end" not in jobs  # never scheduled under flag-on
    events = _events(migrated, sid)
    assert "checkin_open" in events and "checkin_reveal" in events
    assert PRESSURE_ITEMS & _item_rows(migrated, sid)
    replay_pages = (report.replay or {}).get("participant", {}).get("pages", {})
    assert replay_pages.get("time_pressure") is True


def test_straggler_pressure_jobs_are_suppressed(migrated: MigratedDatabase) -> None:
    """A pressure job pending across a flag flip (scheduled flag-on, dispatched
    flag-off) lands 'suppressed' with the audit event instead of firing."""
    runner = Runner(
        migrated,
        ContentStore.load_default(),
        provider=_StubProvider(),
        time_pressure=True,
    )
    batch = f"tp-flag.flip.{uuid.uuid4().hex[:8]}"
    bound = runner.seed_batch(TP_MANIFEST, batch)
    entry = runner.enroll(batch, "QA-pressure-flip")
    sid = entry.session_id

    def _ctx(conn) -> object:
        from app.db.tx import TxContext, db_now

        row = conn.execute(select(s.session).where(s.session.c.session_id == sid)).mappings().one()
        return TxContext(conn, row, now=db_now(conn))

    # Plant a pending pressure job directly, as a flag-on schedule would have.
    with migrated.superuser.begin() as conn:
        scheduler._schedule_active(
            _ctx(conn),
            "checkin.straggler",
            kind="checkin",
            due_active_ms=0,
        )
    # Dispatch it under the flag-off view: suppressed, not fired.
    with migrated.superuser.begin() as conn:
        ctx = _ctx(conn)
        row = (
            conn.execute(
                select(s.scheduled_job).where(
                    s.scheduled_job.c.session_id == sid,
                    s.scheduled_job.c.logical_job_key == "checkin.straggler",
                )
            )
            .mappings()
            .one()
        )
        outcome = scheduler.dispatch(
            ctx,
            bound,
            dict(row),
            flags=FeatureFlags(time_pressure=False),
        )
        assert outcome == "suppressed"
    with migrated.runtime.connect() as conn:
        evt = conn.execute(
            select(func.count())
            .select_from(s.event)
            .where(
                s.event.c.session_id == sid,
                s.event.c.type == "pressure_job_suppressed",
            )
        ).scalar_one()
        assert evt == 1


def test_mid_checkin_flag_flip_rescues_session(migrated: MigratedDatabase) -> None:
    """A session wedged in a check-in stage when the flag drops is carried back
    to ``task`` by the suppressing dispatch — the ``checkin_inactivity`` kill is
    itself a pressure job, so without the rescue the participant is stranded
    (every participant command is stage-gated to the task surface)."""
    runner_on = Runner(
        migrated,
        ContentStore.load_default(),
        provider=_StubProvider(),
        time_pressure=True,
    )
    batch = f"tp-flag.rescue.{uuid.uuid4().hex[:8]}"
    bound = runner_on.seed_batch(TP_MANIFEST, batch)
    entry = runner_on.enroll(batch, "QA-rescue")
    sid, writer = entry.session_id, entry.writer_id
    report = SessionReport(
        session_id=str(sid),
        persona="rescue",
        seed=0,
        manifest_id=TP_MANIFEST,
        arm=None,
        stage="enrolled",
        terminal_reason=None,
        active_ms=0,
    )
    runner_on._drive_to_task(sid, writer, bound, report)

    # Push the active clock past the check-in trigger; the check-in opens and
    # pauses the task clock, queuing the checkin_inactivity kill.
    clockwork.advance_active(migrated.runtime, sid, 61_000)
    runner_on._tick()
    with migrated.runtime.connect() as conn:
        stage = conn.execute(
            select(s.session.c.stage).where(s.session.c.session_id == sid)
        ).scalar_one()
    assert stage in CHECKIN_STAGES

    # Flag drops: the pending kill dispatches under flag-off — suppression
    # rescues the wedged session instead of stranding it.
    runner_off = Runner(
        migrated,
        ContentStore.load_default(),
        provider=_StubProvider(),
        time_pressure=False,
    )
    clockwork.force_wall_due(migrated.runtime, sid, "checkin_inactivity")
    runner_off._tick()

    with migrated.runtime.connect() as conn:
        stage = conn.execute(
            select(s.session.c.stage).where(s.session.c.session_id == sid)
        ).scalar_one()
    assert stage == "task"
    events = _events(migrated, sid)
    assert "pressure_job_suppressed" in events
    assert "checkin_rescinded" in events
    assert "stage_proceeded" in events
    with migrated.runtime.connect() as conn:
        assert activetime.is_running(conn, sid)


def test_flag_off_allocates_single_nopress_cell(migrated: MigratedDatabase) -> None:
    """Owner direction 2026-10-14: under the flag the mint/allocation structure
    no longer pretends a pressure arm — consent draws the single ``nopress``
    cell (block size 1), the allocation row and the ``assigned`` event record
    it, and the session still completes end-to-end because ``resolve_policy``
    canonicalizes the sentinel to the manifest's first arm."""
    sid, report = _run(migrated, TP_MANIFEST, time_pressure=False)
    with migrated.runtime.connect() as conn:
        assert (
            conn.execute(select(s.session.c.arm).where(s.session.c.session_id == sid)).scalar_one()
            == "nopress"
        )
        alloc = (
            conn.execute(
                select(
                    s.allocation.c.arm, s.allocation.c.block_index, s.allocation.c.slot_index
                ).where(s.allocation.c.session_id == sid)
            )
            .mappings()
            .one()
        )
        assert (alloc["arm"], alloc["block_index"], alloc["slot_index"]) == ("nopress", 0, 0)
        assigned = conn.execute(
            select(s.event.c.payload["arm"].as_string()).where(
                s.event.c.session_id == sid, s.event.c.type == "assigned"
            )
        ).scalar_one()
        assert assigned == "nopress"
    # Reported arm surfaces carry the honest value too.
    assert report.arm == "nopress"


def test_flag_off_combos_and_mint_use_nopress_arm(
    migrated: MigratedDatabase, make_settings: Any
) -> None:
    """The Links page mints one arm-neutral combo per TP manifest under the
    flag: ``arm=nopress`` — demo links drop the ``_{high,low}`` segment, and
    POST /admin/previews rejects the retired arms. Peer-eval arms are a
    different axis and stay mintable."""
    from tests.integration.test_wp13_admin import _admin_client, _post

    client = _admin_client(
        make_settings,
        migrated,
        TIME_PRESSURE_ENABLED=False,
        AI_ASSISTANT_ENABLED=False,
        CRATE_OVERLAP_ENABLED=True,
        CRATE_NUMBER_ENABLED=True,
        AI_QA_ENABLED=True,
    )
    body = client.get("/admin/preview-combos").json()
    assert body["time_pressure_enabled"] is False
    tp = [c for c in body["combos"] if c["manifest_id"] == TP_MANIFEST]
    assert [c["arm"] for c in tp] == ["nopress"]
    # A gated (overlap+thin) build stays linked from archive — and its link
    # drops the arm segment entirely under the collapsed cell.
    gated = [c for c in body["combos"] if c["manifest_id"] == "tp_ai_ovfull_thin_v2"]
    assert [c["arm"] for c in gated] == ["nopress"]
    (url,) = {c["demo_url"] for c in gated}
    assert url == "demos/investigators_ovfull_thin_noassist_nopress_aiqa_demo.html"
    pe = {c["arm"] for c in body["combos"] if c["manifest_id"] == "pe_demo_nl_v3"}
    assert pe == {"eval", "control"}

    ok = _post(client, "/admin/previews", {"manifest_id": TP_MANIFEST, "arm": "nopress"})
    assert ok.status_code == 200, ok.text
    bad = _post(client, "/admin/previews", {"manifest_id": TP_MANIFEST, "arm": "high"})
    assert bad.status_code == 422, bad.text


def test_flag_flip_before_review_boundary_opens_task(migrated: MigratedDatabase) -> None:
    """The old check-in remains the exit job when the schedule predates the flag flip."""
    from app.services.projection import project_event

    store = ContentStore.load_default()
    runner = Runner(migrated, store, provider=_StubProvider(), time_pressure=True)
    batch = f"tp-flag.review-flip.{uuid.uuid4().hex[:8]}"
    bound = runner.seed_batch(REVIEW_FIRST_MANIFEST, batch)
    entry = runner.enroll(batch, "QA-review-flip")
    sid = entry.session_id
    report = SessionReport(
        session_id=str(sid),
        persona="review-flip",
        seed=0,
        manifest_id=REVIEW_FIRST_MANIFEST,
        arm=None,
        stage="enrolled",
        terminal_reason=None,
        active_ms=0,
    )
    runner._drive_to_task(sid, entry.writer_id, bound, report)
    clockwork.advance_active(migrated.runtime, sid, 61_000)
    scheduler.tick_dispatch(
        migrated.runtime,
        store,
        worker_id="review-flip-test",
        flags=FeatureFlags(time_pressure=False),
    )
    with migrated.runtime.connect() as conn:
        assert (
            conn.execute(
                select(s.session.c.stage).where(s.session.c.session_id == sid)
            ).scalar_one()
            == "task"
        )
        rows = (
            conn.execute(
                select(s.event).where(
                    s.event.c.session_id == sid, s.event.c.type == "stage_proceeded"
                )
            )
            .mappings()
            .all()
        )
        projected = [project_event(row) for row in rows]
    assert any(event and event["payload"]["to"] == "task" for event in projected)
