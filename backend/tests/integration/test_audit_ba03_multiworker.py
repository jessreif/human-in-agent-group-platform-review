"""BA-03 (spec §6.4): multi-worker claim/commit races and worker-death recovery.

Two workers racing the same durable schedule must commit each due job exactly once
(``SKIP LOCKED`` claims + session-lock commit authorization); a worker that dies after
claiming leaves a lease that expires, after which another worker commits the same job —
still exactly once.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.engine import Engine

from app.db import schema as s
from app.db.engine import make_engine
from app.db.tx import JobLeaseLostError, claim_jobs, finish_job
from app.domain.manifests.store import ContentStore
from app.services import scheduler

from . import audit_helpers as ah
from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def test_racing_workers_commit_due_jobs_exactly_once(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "MW1", "b-mw1")
    sid = result.session_id
    ah.set_active(migrated, sid, 40_500)  # first scripted request due at active 40 s
    engine_a = make_engine(migrated.dsns.runtime)
    engine_b = make_engine(migrated.dsns.runtime)
    barrier = threading.Barrier(2)

    def run_worker(engine: Engine, worker_id: str) -> None:
        barrier.wait()
        for _ in range(40):
            scheduler.tick(engine, store, worker_id=worker_id, provider=None, limit=10)
            time.sleep(0.005)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(run_worker, engine_a, "worker-a"),
                pool.submit(run_worker, engine_b, "worker-b"),
            ]
            # future.result() re-raises any worker failure in the main thread.
            for f in futures:
                f.result(timeout=120)
    finally:
        engine_a.dispose()
        engine_b.dispose()

    with migrated.runtime.connect() as conn:
        committed = conn.execute(
            select(func.count())
            .select_from(s.scheduled_job)
            .where(
                s.scheduled_job.c.session_id == sid,
                s.scheduled_job.c.kind == "scripted_request",
                s.scheduled_job.c.status == "committed",
            )
        ).scalar_one()
        delivered = conn.execute(
            select(func.count())
            .select_from(s.event)
            .where(
                s.event.c.session_id == sid,
                s.event.c.type == "scripted_request_delivered",
            )
        ).scalar_one()
        messages = conn.execute(
            select(func.count())
            .select_from(s.message)
            .where(s.message.c.session_id == sid, s.message.c.channel == "team")
        ).scalar_one()
        # No committed job was ever double-executed even under the race.
        double_finished = conn.execute(
            select(func.count())
            .select_from(s.scheduled_job)
            .where(
                s.scheduled_job.c.session_id == sid,
                s.scheduled_job.c.status == "committed",
                s.scheduled_job.c.attempts > 1,
            )
        ).scalar_one()
    assert committed == 1 and delivered == 1 and messages == 1
    assert double_finished == 0


def test_worker_death_mid_claim_recovers_via_lease_expiry(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "MW2", "b-mw2")
    sid = result.session_id
    ah.set_active(migrated, sid, 40_500)

    # Worker A claims the due job then "dies" — it never calls finish_job.
    claimed = claim_jobs(migrated.runtime, worker_id="dead-worker", lease_s=300.0, limit=10)
    assert any(c.kind == "scripted_request" for c in claimed)
    with migrated.runtime.begin() as conn:
        # A dead worker's lease simply ages out; simulate elapsed lease time.
        conn.execute(
            update(s.scheduled_job)
            .where(s.scheduled_job.c.session_id == sid, s.scheduled_job.c.status == "claimed")
            .values(lease_expires_at=func.now())
        )
    # Worker B picks the expired claim up and commits it exactly once.
    scheduler.tick(migrated.runtime, store, worker_id="worker-b", provider=None, limit=10)
    scheduler.tick(migrated.runtime, store, worker_id="worker-b", provider=None, limit=10)
    with migrated.runtime.connect() as conn:
        job_rows = (
            conn.execute(
                select(s.scheduled_job.c.status, s.scheduled_job.c.attempts).where(
                    s.scheduled_job.c.session_id == sid,
                    s.scheduled_job.c.kind == "scripted_request",
                    s.scheduled_job.c.due_active_ms == 40_000,
                )
            )
            .mappings()
            .all()
        )
        delivered = conn.execute(
            select(func.count())
            .select_from(s.event)
            .where(
                s.event.c.session_id == sid,
                s.event.c.type == "scripted_request_delivered",
            )
        ).scalar_one()
    assert len(job_rows) == 1 and job_rows[0]["status"] == "committed"
    assert job_rows[0]["attempts"] == 2  # dead claim + recovered claim
    assert delivered == 1


def test_stale_lease_owner_cannot_finish(migrated: MigratedDatabase, store: ContentStore) -> None:
    """A second worker cannot commit under the first worker's live lease: finish_job
    re-verifies lease ownership under the job row lock."""
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "MW3", "b-mw3")
    sid = result.session_id
    ah.set_active(migrated, sid, 40_500)
    claimed = claim_jobs(migrated.runtime, worker_id="owner", lease_s=300.0, limit=10)
    claimed_job = next(c for c in claimed if c.kind == "scripted_request")
    with pytest.raises(JobLeaseLostError):
        finish_job(
            migrated.runtime,
            claimed_job,
            worker_id="impostor",
            handler=scheduler.make_job_handler(store=store, generation=None),
        )
