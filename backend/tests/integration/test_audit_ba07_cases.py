"""BA-07 (spec §6.1/§6.4): named scheduler/clock edge-case verifications.

- paused interval: active-time jobs due during a check-in pause stay deferred, a fresh
  worker/engine pair on the same DB (a restart) still sees them deferred, and after the
  resume they become due on the same active axis;
- crossed ordering: two jobs due at the same active ms commit in a deterministic order
  (the ``(claimable_at, job_id)`` claim tiebreak);
- commit-side timing: a job claimed before the deadline but committed while still
  inside the budget commits exactly once; one committed after the budget suppresses;
- mid-check-in outage: a ``server_outage`` incident overlapping a paused interval is
  recorded ``counted`` with the active elapsed frozen at the pause point.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text, update

from app.db import schema as s
from app.db.engine import make_engine
from app.db.tx import JobLeaseLostError, claim_jobs, finish_job
from app.domain.manifests.store import ContentStore
from app.services import activetime, scheduler
from app.workers.main import record_outage_incidents

from . import audit_helpers as ah
from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def test_pause_defers_active_jobs_across_restart_then_resume(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "P1", "b-p1")
    sid, writer = result.session_id, result.writer_id
    ah.set_active(migrated, sid, 60_000)
    ah.tick(migrated, store)
    assert ah.stage(migrated, sid)[0] == "checkin_collect"
    # The clock is paused: remaining active-basis jobs (next scripted request at
    # 150 s) cannot come due while the paused interval is open.
    ah.set_active(migrated, sid, 80_500)  # no-op on a paused interval — proves freeze
    with migrated.runtime.connect() as conn:
        interval = activetime.open_interval(conn, sid)
        assert interval is not None and interval["kind"] == "paused"

    # Simulated restart: a brand-new engine + content store over the same DB sees the
    # persisted paused interval and commits nothing (BA-07 restart-during-pause).
    engine2 = make_engine(migrated.dsns.runtime)
    store2 = ContentStore.load_default()
    try:
        assert (
            scheduler.tick(engine2, store2, worker_id="w-restarted", provider=None, limit=25) == 0
        )
        jobs = ah.jobs(migrated, sid)
        pending = {
            j["kind"]
            for j in jobs
            if j["clock_basis"] == "active_task" and j["status"] == "pending"
        }
        assert "scripted_request" in pending

        # Mid-check-in outage (BA-07): an incident overlapping the pause is recorded
        # "counted" with the active elapsed frozen at the pause point.
        with migrated.runtime.connect() as conn:
            now = conn.execute(select(func.now())).scalar_one()
        record_outage_incidents(
            engine2,
            started_at=now - timedelta(seconds=90),
            ended_at=now,
            surfaces="persistence,scheduler",
        )
        with migrated.runtime.connect() as conn:
            incident = (
                conn.execute(
                    select(s.incident_interval).where(s.incident_interval.c.session_id == sid)
                )
                .mappings()
                .one()
            )
        assert incident["clock_effect"] == "counted"
        # The paused interval freezes active elapsed at the pause commit point (the
        # check-in opens at >=60 s; real-clock drift past 60_000 is expected — the
        # invariant is that start and end carry the same frozen bound).
        assert incident["active_ms_at_start"] == incident["active_ms_at_end"]
        assert 60_000 <= incident["active_ms_at_end"] < 61_000

        # Resume the check-in on the restarted worker: deferred jobs come due on the
        # same active axis afterwards.
        ah.run(migrated, store, sid, writer, "pressure_skip", {}, ah.stage(migrated, sid)[1])
        assert ah.stage(migrated, sid)[0] == "checkin_wait"
        ah.force_wall_due(migrated, sid, "checkin_reveal")
        assert (
            scheduler.tick(engine2, store2, worker_id="w-restarted", provider=None, limit=25) >= 1
        )
        assert ah.stage(migrated, sid)[0] == "checkin_reveal"
        ah.run(migrated, store, sid, writer, "proceed", {}, ah.stage(migrated, sid)[1])
        assert ah.stage(migrated, sid)[0] == "task"
        # tp_demo_v1 scripted requests are at active 40/150/240 s — the 40 s one
        # committed before the pause; resuming to 150 s commits the second.
        ah.set_active(migrated, sid, 150_500)
        scheduler.tick(engine2, store2, worker_id="w-restarted", provider=None, limit=25)
    finally:
        engine2.dispose()
    delivered = ah.events(migrated, sid, "scripted_request_delivered")
    assert len(delivered) == 2  # both due scripted requests committed, once each


def test_crossed_ordering_commits_deterministically(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """Jobs due at the same active ms commit in the ``(claimable_at, job_id)``
    claim order — the deterministic tiebreak the export relies on."""
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "P2", "b-p2")
    sid = result.session_id
    with migrated.runtime.begin() as conn:
        conn.execute(
            update(s.scheduled_job)
            .where(
                s.scheduled_job.c.session_id == sid,
                s.scheduled_job.c.kind == "scripted_request",
            )
            .values(due_active_ms=40_000)
        )
    ah.set_active(migrated, sid, 40_500)
    with migrated.runtime.begin() as conn:
        # Equal claim-priority forces the job_id tiebreak to decide commit order.
        conn.execute(
            update(s.scheduled_job)
            .where(
                s.scheduled_job.c.session_id == sid,
                s.scheduled_job.c.kind == "scripted_request",
            )
            .values(claimable_at=func.now())
        )
    ah.tick(migrated, store, n=3)
    with migrated.runtime.connect() as conn:
        rows = (
            conn.execute(
                select(
                    s.event.c.seq,
                    s.event.c.job_id,
                    s.scheduled_job.c.status,
                )
                .select_from(
                    s.event.join(s.scheduled_job, s.scheduled_job.c.job_id == s.event.c.job_id)
                )
                .where(
                    s.event.c.session_id == sid,
                    s.event.c.type == "scripted_request_delivered",
                )
                .order_by(s.event.c.seq)
            )
            .mappings()
            .all()
        )
    # tp_demo_v1 scripts three requests (40/150/240 s) — all forced to the same due
    # instant, so all three commit, once each, in deterministic claim order.
    assert len(rows) == 3
    assert all(r["status"] == "committed" for r in rows)
    observed = [uuid.UUID(str(r["job_id"])) for r in rows]
    assert observed == sorted(observed)  # claimable_at equal -> job_id order


def test_commit_late_inside_budget_then_lease_guard(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """A job claimed at its due instant but committed +11 s later — still inside the
    task budget — commits exactly once; a second finish attempt hits the lease guard."""
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "P3", "b-p3")
    sid = result.session_id
    ah.set_active(migrated, sid, 40_500)
    claimed = claim_jobs(migrated.runtime, worker_id="w-late", lease_s=300.0, limit=10)
    claimed_job = next(c for c in claimed if c.kind == "scripted_request")
    ah.set_active(migrated, sid, 51_500)  # +11 s active, still < 360 s budget
    status, _ = finish_job(
        migrated.runtime,
        claimed_job,
        worker_id="w-late",
        handler=scheduler.make_job_handler(store=store, generation=None),
    )
    assert status == "committed"
    assert len(ah.events(migrated, sid, "scripted_request_delivered")) == 1
    with pytest.raises(JobLeaseLostError):
        finish_job(
            migrated.runtime,
            claimed_job,
            worker_id="w-late",
            handler=scheduler.make_job_handler(store=store, generation=None),
        )
    assert len(ah.events(migrated, sid, "scripted_request_delivered")) == 1


def test_claimed_pre_deadline_suppresses_post_deadline(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """The commit-side guard wins over the claim-side check: a job claimed while due
    but committed after the active budget is suppressed, never delivered."""
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "P4", "b-p4")
    sid = result.session_id
    ah.set_active(migrated, sid, 40_500)
    claimed = claim_jobs(migrated.runtime, worker_id="w-dead", lease_s=300.0, limit=10)
    claimed_job = next(c for c in claimed if c.kind == "scripted_request")
    ah.set_active(migrated, sid, 361_000)  # past the 360 s budget at commit time
    status, _ = finish_job(
        migrated.runtime,
        claimed_job,
        worker_id="w-dead",
        handler=scheduler.make_job_handler(store=store, generation=None),
    )
    assert status == "suppressed"
    assert len(ah.events(migrated, sid, "scripted_request_delivered")) == 0
    with migrated.runtime.connect() as conn:
        job_row = conn.execute(
            select(s.scheduled_job.c.status).where(s.scheduled_job.c.job_id == claimed_job.job_id)
        ).scalar_one()
    assert job_row == "suppressed"


def test_restart_at_299_yields_exactly_one_warning(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """INV-0213 verify clause: a worker restart on the boundary still produces exactly
    one one-minute warning — the durable job, not the process, owns the commit."""
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "P5", "b-p5")
    sid, writer = result.session_id, result.writer_id
    # Complete the 60 s check-in so the warning is the only remaining active-time job.
    ah.set_active(migrated, sid, 60_000)
    ah.tick(migrated, store)
    assert ah.stage(migrated, sid)[0] == "checkin_collect"
    ah.run(migrated, store, sid, writer, "pressure_skip", {}, ah.stage(migrated, sid)[1])
    assert ah.stage(migrated, sid)[0] == "checkin_wait"
    ah.force_wall_due(migrated, sid, "checkin_reveal")
    ah.tick(migrated, store)
    assert ah.stage(migrated, sid)[0] == "checkin_reveal"
    ah.run(migrated, store, sid, writer, "proceed", {}, ah.stage(migrated, sid)[1])
    assert ah.stage(migrated, sid)[0] == "task"
    ah.set_active(migrated, sid, 299_500)

    # Restart mid-boundary: a fresh engine + store over the same DB.
    engine2 = make_engine(migrated.dsns.runtime)
    store2 = ContentStore.load_default()
    try:
        # 299.5 s: backdated scripted-request jobs commit on the restarted worker,
        # but the 300 s warning is still strictly before its boundary.
        scheduler.tick(engine2, store2, worker_id="w-r2", provider=None, limit=25)
        assert len(ah.events(migrated, sid, "warning_committed")) == 0
        ah.set_active(migrated, sid, 300_000)
        scheduler.tick(engine2, store2, worker_id="w-r2", provider=None, limit=25)
        scheduler.tick(engine2, store2, worker_id="w-r2", provider=None, limit=25)
    finally:
        engine2.dispose()
    warnings = ah.events(migrated, sid, "warning_committed")
    assert len(warnings) == 1
    with migrated.runtime.connect() as conn:
        bodies = conn.execute(
            select(func.count())
            .select_from(s.message)
            .where(s.message.c.session_id == sid, s.message.c.body == "One minute left.")
        ).scalar_one()
    assert bodies == 1


def test_clock_speed_factor_accelerates_active_elapsed(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """BA-14: a bound ``clock_speed_factor`` (reachable only via ``accelerated_qa``
    bindings) multiplies active-elapsed accrual, so active-basis jobs come due sooner
    in wall time — accelerated QA sessions actually run fast."""
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "P6", "b-p6")
    sid, writer = result.session_id, result.writer_id
    ah.set_active(migrated, sid, 60_000)
    ah.tick(migrated, store)
    ah.run(migrated, store, sid, writer, "pressure_skip", {}, ah.stage(migrated, sid)[1])
    ah.force_wall_due(migrated, sid, "checkin_reveal")
    ah.tick(migrated, store)
    ah.run(migrated, store, sid, writer, "proceed", {}, ah.stage(migrated, sid)[1])
    assert ah.stage(migrated, sid)[0] == "task"

    # Inject a QA-overlay-style factor into the persisted binding (the production
    # path binds != 1 only under accelerated_qa; this tests runtime consumption).
    with migrated.runtime.begin() as conn:
        conn.execute(
            text(
                "UPDATE app.session SET binding = jsonb_set("
                "binding::jsonb, '{clock_speed_factor}', '10'::jsonb) "
                "WHERE session_id = :sid"
            ),
            {"sid": sid},
        )
        conn.execute(
            text(
                "UPDATE app.clock_interval SET active_ms_at_start = 0, "
                "started_at = clock_timestamp() - interval '16 seconds' "
                "WHERE session_id = :sid AND ended_at IS NULL AND kind = 'running'"
            ),
            {"sid": sid},
        )
        # claimable_at is the factor-aware wall estimate baked at schedule time; a
        # session bound with the factor from the start would have near-past hints —
        # refresh them to match that end-state.
        conn.execute(
            text(
                "UPDATE app.scheduled_job SET claimable_at = clock_timestamp() "
                "WHERE session_id = :sid AND status = 'pending' "
                "AND clock_basis = 'active_task'"
            ),
            {"sid": sid},
        )
    elapsed = ah.active(migrated, sid)
    assert 155_000 <= elapsed <= 165_000  # 16 s wall * factor 10
    ah.tick(migrated, store)
    # Requests scripted at 40 s and 150 s active are due under acceleration.
    assert len(ah.events(migrated, sid, "scripted_request_delivered")) == 2
