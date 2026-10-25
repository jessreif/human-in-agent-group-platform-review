"""Worker entry point (INV-0101/0603/0612): ``python -m app.workers.main``.

The worker claims due scheduled jobs and commits them under the session lock
(``scheduler.tick``). Generation provider calls run *outside* the lock between claim and
commit; ``provider=None`` records the model call as unavailable and the deterministic
fallback delivers, which is also exactly how a provider outage behaves under D14 — the
participant's clock is never silently extended.

D14 technical-incident policy (selected treatment pending; mechanism implemented): when the
database is unreachable the worker flags the outage window, and on recovery writes one
``incident_interval`` per session whose open clock interval overlapped it. ``clock_effect``
is ``counted`` — the active ledger already counts outage wall-time and nothing is added back.
A worker restart needs no incident row: lease expiry recovers claimed jobs and the persisted
schedule is unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import sys
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import FrameType
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from app.db import schema as s
from app.db.engine import make_engine, probe_database
from app.db.tx import PersistenceUnavailableError
from app.domain.manifests.store import ContentStore
from app.logging_config import configure_logging
from app.providers.base import TextProvider, ensure_provider_allowed
from app.providers.select import provider_from_settings
from app.services import activetime, lifecycle, scheduler
from app.settings import ConfigurationError, Settings, load_settings
from app.timing.clock import Clock, SystemClock, ensure_clock_allowed
from app.version import build_info

logger = logging.getLogger("investigators.worker")
# Container /tmp is a private tmpfs (docker-compose.yml); the file holds no secrets.
_DEFAULT_HEARTBEAT = "/tmp/investigators-worker-heartbeat.json"  # noqa: S108
HEARTBEAT_PATH = Path(os.environ.get("INVESTIGATORS_WORKER_HEARTBEAT", _DEFAULT_HEARTBEAT))
HEARTBEAT_INTERVAL_S = 10.0
# BA-08: the wall-clock abandonment sweep runs on a slower cadence than the job tick —
# a 30 s poll is far inside the 30-minute inactivity window it enforces.
ABANDONMENT_SWEEP_INTERVAL_S = 30.0
DEFAULT_POLL_S = 0.5
DEFAULT_LEASE_S = 30.0
DEFAULT_CLAIM_LIMIT = 10


@dataclass
class WorkerState:
    beats: int = 0
    jobs_committed: int = 0
    database_reachable: bool | None = None
    outage_started_at: str | None = None
    generation_progress_at: float = 0.0


def write_heartbeat(path: Path, clock: Clock, state: WorkerState) -> None:
    payload = {
        "ts": clock.now().isoformat(),
        "pid": os.getpid(),
        "beats": state.beats,
        "database_reachable": state.database_reachable,
        "jobs_committed": state.jobs_committed,
        "generation_progress_at": state.generation_progress_at,
        "outage_started_at": state.outage_started_at,
        "source_sha256": build_info().source_sha256,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def _active_at(interval: Mapping[str, Any], at: datetime, *, factor: float = 1.0) -> int:
    """Active elapsed for one ledger interval at a wall instant: running intervals count
    ``(at - started_at) * factor`` on top of ``active_ms_at_start`` (BA-14); paused
    intervals are frozen."""
    base = int(interval["active_ms_at_start"])
    if interval["kind"] != "running":
        return base
    started: datetime = interval["started_at"]
    delta = max(0.0, (at - started).total_seconds() * 1000 * factor)
    return base + round(delta)


def record_outage_incidents(
    engine: Engine,
    *,
    started_at: datetime,
    ended_at: datetime,
    surfaces: str,
) -> int:
    """One ``incident_interval`` per session whose open clock interval overlapped the outage
    (D14/INV-0612). ``clock_effect='counted'``: the outage wall-time already counted as active
    time while running — the ledger never silently extends the participant's clock."""
    ci = s.clock_interval
    with engine.begin() as conn:
        rows = (
            conn.execute(
                select(
                    ci.c.session_id,
                    ci.c.kind,
                    ci.c.started_at,
                    ci.c.active_ms_at_start,
                    s.session.c.binding,
                )
                .select_from(ci.join(s.session, s.session.c.session_id == ci.c.session_id))
                .where(
                    ci.c.ended_at.is_(None),
                    ci.c.started_at <= ended_at,
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            existing = conn.execute(
                select(s.incident_interval.c.incident_id).where(
                    s.incident_interval.c.session_id == row["session_id"],
                    s.incident_interval.c.kind == "server_outage",
                    s.incident_interval.c.started_at == started_at,
                )
            ).first()
            if existing is not None:
                continue  # a second worker already recorded this outage window
            conn.execute(
                insert(s.incident_interval).values(
                    session_id=row["session_id"],
                    incident_id=uuid.uuid4(),
                    kind="server_outage",
                    clock_effect="counted",
                    started_at=started_at,
                    ended_at=ended_at,
                    active_ms_at_start=_active_at(
                        dict(row),
                        started_at,
                        factor=activetime.speed_factor_for({"binding": row["binding"]}),
                    ),
                    active_ms_at_end=_active_at(
                        dict(row),
                        ended_at,
                        factor=activetime.speed_factor_for({"binding": row["binding"]}),
                    ),
                    notes=f"surfaces={surfaces}",
                )
            )
    return len(rows)


def run(
    settings: Settings,
    *,
    stop: threading.Event,
    clock: Clock | None = None,
    engine: Engine | None = None,
    store: ContentStore | None = None,
    provider: TextProvider | None = None,
    worker_id: str | None = None,
    heartbeat_path: Path = HEARTBEAT_PATH,
    poll_s: float = DEFAULT_POLL_S,
    lease_s: float = DEFAULT_LEASE_S,
    claim_limit: int = DEFAULT_CLAIM_LIMIT,
    max_beats: int | None = None,
) -> WorkerState:
    clock = clock or SystemClock()
    ensure_clock_allowed(settings.APP_MODE.value, clock)
    engine = engine or make_engine(
        settings.DATABASE_URL.get_secret_value(),
        connect_timeout_seconds=settings.READINESS_DB_TIMEOUT_SECONDS,
    )
    store = store or ContentStore.load_default()
    # INV-1001: when no provider is injected, build the pinned loopback adapter if the
    # settings configure one; otherwise the worker stays deterministic-only (spec §7.4).
    provider = provider if provider is not None else provider_from_settings(settings)
    if provider is not None:
        ensure_provider_allowed(settings.APP_MODE.value, provider)
    worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
    state = WorkerState()
    outage_start: datetime | None = None
    logger.info(
        "worker started",
        extra={
            "settings": settings.describe(),
            "worker_id": worker_id,
            "provider": None if provider is None else provider.provider_id,
        },
    )

    # BA-45: provider generation runs on its own thread. The main loop's dispatch
    # pass never claims a provider-needing job (defer_provider_generation at
    # claim time), so a warning that becomes due mid-generation is picked up on
    # the next poll — it never waits behind a model call. Both passes share the
    # worker_id (lease ownership) and ``finish_job`` remains the sole commit
    # point under the session lock.
    # ``run`` can exit without ``stop`` ever being set (``max_beats``, an
    # exception, or a caller that returns early in tests). The generation loop
    # therefore also watches ``gen_stop``, which the ``finally`` below always
    # sets — otherwise a daemon thread would keep claiming jobs against an
    # engine the caller believes is finished.
    gen_stop = threading.Event()

    def _generation_loop() -> None:
        state.generation_progress_at = time.time()
        while not stop.is_set() and not gen_stop.is_set():
            try:
                state.jobs_committed += scheduler.tick_generation(
                    engine,
                    store,
                    worker_id=worker_id,
                    provider=provider,
                    lease_s=lease_s,
                    limit=1,
                    flags=settings.flags(),
                )
                state.generation_progress_at = time.time()
            except (DBAPIError, PersistenceUnavailableError):
                pass  # outage accounting stays in the main loop
            except Exception:
                logger.exception("generation tick failed")
            gen_stop.wait(poll_s)

    generation_thread = threading.Thread(
        target=_generation_loop, name="investigators-generation", daemon=True
    )
    generation_thread.start()
    try:
        last_beat = 0.0
        last_abandonment_sweep = 0.0
        while not stop.is_set():
            try:
                reachable = probe_database(engine).reachable
            except Exception:  # noqa: BLE001 - a probe failure is itself an outage
                reachable = False
            state.database_reachable = reachable
            if reachable:
                committed = 0
                tick_failed = False
                try:
                    committed = scheduler.tick_dispatch(
                        engine,
                        store,
                        worker_id=worker_id,
                        lease_s=lease_s,
                        limit=claim_limit,
                        flags=settings.flags(),
                    )
                except (DBAPIError, PersistenceUnavailableError):
                    reachable = False
                    tick_failed = True
                except Exception:
                    logger.exception("scheduler tick failed")
                state.jobs_committed += committed
                if not tick_failed and (
                    clock.now().timestamp() - last_abandonment_sweep >= ABANDONMENT_SWEEP_INTERVAL_S
                ):
                    try:
                        abandoned = lifecycle.sweep_abandoned(
                            engine, store, bonus_payment_enabled=settings.BONUS_PAYMENT_ENABLED
                        )
                    except DBAPIError:
                        reachable = False
                    except Exception:
                        logger.exception("abandonment sweep failed")
                    else:
                        last_abandonment_sweep = clock.now().timestamp()
                        if abandoned:
                            logger.info("sessions abandoned", extra={"count": abandoned})
                if outage_start is not None and not tick_failed:
                    now = clock.now()
                    try:
                        n = record_outage_incidents(
                            engine,
                            started_at=outage_start,
                            ended_at=now,
                            surfaces="persistence,scheduler",
                        )
                        logger.info(
                            "outage incidents recorded",
                            extra={"sessions": n, "seconds": (now - outage_start).total_seconds()},
                        )
                    except DBAPIError:
                        reachable = False  # still down; try again next loop
                    else:
                        outage_start = None
                        state.outage_started_at = None
            if not reachable and outage_start is None:
                outage_start = clock.now()
                state.outage_started_at = outage_start.isoformat()
                logger.warning("persistence outage started")
            now_mono = clock.now().timestamp()
            if now_mono - last_beat >= HEARTBEAT_INTERVAL_S or state.beats == 0:
                state.beats += 1
                write_heartbeat(heartbeat_path, clock, state)
                last_beat = now_mono
            if max_beats is not None and state.beats >= max_beats:
                break
            stop.wait(poll_s)
    finally:
        gen_stop.set()
        generation_thread.join(timeout=max(poll_s, 1.0) + 5.0)
        engine.dispose()
        logger.info(
            "worker stopped", extra={"beats": state.beats, "jobs_committed": state.jobs_committed}
        )
    return state


def main() -> int:
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        configure_logging("investigators-worker")
        logger.critical(str(exc))
        return 2
    configure_logging("investigators-worker", settings.LOG_LEVEL)
    stop = threading.Event()

    def _stop(signum: int, _frame: FrameType | None) -> None:
        logger.info("stop requested", extra={"signal": signal.Signals(signum).name})
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    run(settings, stop=stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
