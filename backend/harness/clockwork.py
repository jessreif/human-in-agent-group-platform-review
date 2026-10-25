"""Time and job drivers for the simulation harness.

Active time is driven by rewriting the open running interval's ``active_ms_at_start``
(the same mechanism the integration suite uses); wall-clock jobs are made due by
resetting ``claimable_at``/``due_wall_at``. ``claimable_at`` is only a wall estimate
for active-clock jobs, so it is reset alongside every active jump.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, text, update
from sqlalchemy.engine import Engine

from app.db import schema as s
from app.domain.manifests.store import ContentStore
from app.flags import DEFAULT_FLAGS, FeatureFlags
from app.services import activetime, scheduler


def set_active(engine: Engine, sid: uuid.UUID, active_ms: int) -> None:
    """Jump the session's open running interval to ``active_ms`` elapsed."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE app.clock_interval "
                "SET active_ms_at_start = :ms, started_at = clock_timestamp() "
                " WHERE session_id = :sid AND ended_at IS NULL AND kind = 'running'"
            ),
            {"ms": active_ms, "sid": sid},
        )
        conn.execute(
            text(
                "UPDATE app.scheduled_job SET claimable_at = clock_timestamp() "
                " WHERE session_id = :sid AND status IN ('pending') "
                " AND clock_basis = 'active_task'"
            ),
            {"sid": sid},
        )


def active(engine: Engine, sid: uuid.UUID) -> int:
    with engine.connect() as conn:
        return activetime.active_ms(
            conn, sid, conn.execute(text("SELECT clock_timestamp()")).scalar_one()
        )


def advance_active(engine: Engine, sid: uuid.UUID, delta_ms: int) -> int:
    """Advance active time by ``delta_ms``; returns the new elapsed value."""
    now = active(engine, sid)
    set_active(engine, sid, now + delta_ms)
    return now + delta_ms


def force_wall_due(engine: Engine, sid: uuid.UUID, kind: str) -> None:
    """Make every pending wall-basis job of ``kind`` immediately claimable."""
    with engine.begin() as conn:
        conn.execute(
            update(s.scheduled_job)
            .where(
                s.scheduled_job.c.session_id == sid,
                s.scheduled_job.c.kind == kind,
                s.scheduled_job.c.status == "pending",
            )
            .values(due_wall_at=func.now(), claimable_at=func.now())
        )


def tick(
    engine: Engine,
    store: ContentStore,
    *,
    provider: Any = None,
    worker: str = "sim-worker",
    rounds: int = 6,
    flags: FeatureFlags = DEFAULT_FLAGS,
) -> int:
    """Drive due jobs until none commit in a pass (deliveries, transitions, generation)."""
    total = 0
    for _ in range(rounds):
        committed = scheduler.tick(
            engine,
            store,
            worker_id=worker,
            provider=provider,
            limit=25,
            flags=flags,
        )
        if committed == 0:
            break
        total += committed
    return total
