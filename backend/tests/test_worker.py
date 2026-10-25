from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from app.settings import Settings
from app.timing.clock import VirtualClock, VirtualClockForbiddenError
from app.workers import healthcheck
from app.workers import main as worker


def test_worker_writes_heartbeats_and_stops(
    make_settings: Callable[..., Settings],
    tmp_path: Path,
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heartbeat = tmp_path / "hb.json"
    monkeypatch.setattr(worker, "HEARTBEAT_INTERVAL_S", 0.0)  # virtual clock never advances
    state = worker.run(
        make_settings(),
        stop=threading.Event(),
        clock=virtual_clock,
        heartbeat_path=heartbeat,
        poll_s=0,
        max_beats=2,
    )
    assert state.beats == 2
    payload = json.loads(heartbeat.read_text())
    assert payload["beats"] == 2
    assert payload["database_reachable"] is False
    assert payload["jobs_committed"] == 0
    assert payload["outage_started_at"] is not None
    assert payload["ts"] == virtual_clock.now().isoformat()


def test_worker_honours_stop_event(make_settings: Callable[..., Settings], tmp_path: Path) -> None:
    stop = threading.Event()
    stop.set()
    state = worker.run(make_settings(), stop=stop, heartbeat_path=tmp_path / "hb.json")
    assert state.beats == 0


def test_generation_thread_does_not_outlive_run(
    make_settings: Callable[..., Settings],
    tmp_path: Path,
    virtual_clock: VirtualClock,
) -> None:
    """BA-45: ``run`` can return without ``stop`` ever being set (``max_beats``).
    The dedicated generation thread must still stop — otherwise it keeps claiming
    jobs against an engine the caller believes is finished."""
    state = worker.run(
        make_settings(),
        stop=threading.Event(),
        clock=virtual_clock,
        heartbeat_path=tmp_path / "hb.json",
        poll_s=0,
        max_beats=1,
    )
    assert state.beats == 1
    for _ in range(50):
        if not any(
            t.name == "investigators-generation" and t.is_alive() for t in threading.enumerate()
        ):
            break
        time.sleep(0.01)
    else:
        pytest.fail("generation thread outlived worker.run")


def test_worker_refuses_virtual_clock_in_live_mode(
    make_settings: Callable[..., Settings], tmp_path: Path
) -> None:
    live = make_settings(
        APP_MODE="live", DATABASE_URL="postgresql://u:pw@127.0.0.1:1/investigators_db"
    )
    with pytest.raises(VirtualClockForbiddenError):
        worker.run(
            live, stop=threading.Event(), clock=VirtualClock(), heartbeat_path=tmp_path / "hb"
        )


def test_worker_refuses_remote_provider_in_every_mode(
    make_settings: Callable[..., Settings], tmp_path: Path
) -> None:
    """BA-35: even if a remote adapter is injected, the session-serving worker refuses
    to start with it — APP_MODE is not a participant-data boundary."""
    from app.providers.base import ProviderNotAllowedError

    class _Remote:
        provider_id = "openai_chat"
        is_remote = True
        is_double = False

        def generate(self, request: object, *, timeout_s: float) -> object:
            raise AssertionError("session prompt must never reach a remote provider")

    for mode in ("preview", "qa", "live"):
        settings = make_settings(
            APP_MODE=mode, DATABASE_URL="postgresql://u:pw@127.0.0.1:1/investigators_db"
        )
        with pytest.raises(ProviderNotAllowedError):
            worker.run(
                settings,
                stop=threading.Event(),
                provider=_Remote(),  # type: ignore[arg-type]
                heartbeat_path=tmp_path / "hb",
            )


def test_healthcheck_uses_heartbeat_age(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    heartbeat = tmp_path / "hb.json"
    monkeypatch.setattr(healthcheck, "HEARTBEAT_PATH", heartbeat)
    assert healthcheck.main() == 1
    heartbeat.write_text(json.dumps({"generation_progress_at": time.time()}))
    assert healthcheck.main() == 0
    stale = time.time() - 10 * worker.HEARTBEAT_INTERVAL_S
    import os

    os.utime(heartbeat, (stale, stale))
    assert healthcheck.main() == 1


def test_worker_main_exits_2_on_bad_configuration() -> None:
    assert worker.main() == 2
