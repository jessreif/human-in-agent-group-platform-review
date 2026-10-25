"""BA-03 process-level proof: two scheduler workers and two API processes sharing one
PostgreSQL. ``test_audit_ba03_multiworker.py`` already races two engines in-process; this
file spawns real OS processes (``python -m app.workers.main`` / ``uvicorn
app.main:create_app``) so the SKIP-LOCKED claim path, command dedup, and re-entry races
hold across process boundaries, not just connection pools.

API listeners are inherited ``127.0.0.1`` port-0 sockets (uvicorn ``--fd``). A health
response is readiness only after that child holds the listening inode.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Row

from app.db import schema as s
from app.domain.manifests.store import ContentStore
from tests.test_process_reap import ProcessResources, process_holds_inode, socket_inode

from . import audit_helpers as ah
from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration

BACKEND_DIR = Path(__file__).resolve().parents[2]

# tp_demo_v3 scripted_requests times_active_s are 40, 150, and 240 (stored as ms).
_DUE_ACTIVE_MS = 40_000
_FUTURE_ACTIVE_MS = (150_000, 240_000)
_DUE_DEADLINE_S = 10.0
_API_READY_S = 30.0


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _clean_env(dsn: str, extra: dict[str, str]) -> dict[str, str]:
    """Strip every INVESTIGATORS_* var so a deployed .env can never bleed into the test."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("INVESTIGATORS_")}
    env.update(
        {
            "DATABASE_URL": dsn,
            "APP_MODE": "qa",
            "LOG_LEVEL": "WARNING",
            "PYTHONUNBUFFERED": "1",
            **extra,
        }
    )
    return env


def _uvicorn_args(fd: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:create_app",
        "--factory",
        "--fd",
        str(fd),
    ]


def _bind_or_fail(res: ProcessResources) -> socket.socket:
    try:
        return res.bind_loopback()
    except OSError as exc:
        pytest.fail(f"loopback bind failed: {exc}\n{res.redacted_tail()}")


def _wait_for_owned_api(
    res: ProcessResources, sock: socket.socket, proc: subprocess.Popen[bytes]
) -> None:
    """Ready only when THIS child holds the inherited listener and then answers /health."""
    host, port = sock.getsockname()[:2]
    inode = socket_inode(sock)
    deadline = time.monotonic() + _API_READY_S
    while time.monotonic() < deadline:
        rc = proc.poll()
        if rc is not None:
            pytest.fail(
                f"api process exited rc={rc} before listening on {host}:{port}\n"
                f"{res.redacted_tail()}"
            )
        if not process_holds_inode(proc.pid, inode):
            time.sleep(0.05)
            continue
        try:
            response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1, trust_env=False)
        except httpx.HTTPError:
            time.sleep(0.05)
            continue
        if response.status_code == 200:
            return
        time.sleep(0.05)
    pytest.fail(
        f"api pid={proc.pid} on {host}:{port} was not ready; "
        "a /health response from any other process is not readiness\n"
        f"{res.redacted_tail()}"
    )


def _scripted_snapshot(
    migrated: MigratedDatabase, sids: Sequence[uuid.UUID]
) -> tuple[list[Row[Any]], list[Row[Any]]]:
    with migrated.runtime.connect() as conn:
        jobs = list(
            conn.execute(
                select(
                    s.scheduled_job.c.session_id,
                    s.scheduled_job.c.due_active_ms,
                    s.scheduled_job.c.status,
                ).where(
                    s.scheduled_job.c.session_id.in_(sids),
                    s.scheduled_job.c.kind == "scripted_request",
                )
            ).all()
        )
        delivered = list(
            conn.execute(
                select(s.event.c.session_id, func.count())
                .where(
                    s.event.c.session_id.in_(sids),
                    s.event.c.type == "scripted_request_delivered",
                )
                .group_by(s.event.c.session_id)
            ).all()
        )
    return jobs, delivered


def _format_status(
    sids: Sequence[uuid.UUID], jobs: Sequence[Row[Any]], delivered: Sequence[Row[Any]]
) -> str:
    lines = ["scripted_request jobs:"]
    for sid, due_ms, status in sorted(jobs, key=lambda row: (str(row[0]), row[1] or -1, row[2])):
        lines.append(f"  session={sid} due_active_ms={due_ms} status={status}")
    counts = {sid: count for sid, count in delivered}
    lines.append("scripted_request_delivered:")
    for sid in sids:
        lines.append(f"  session={sid} n={counts.get(sid, 0)}")
    return "\n".join(lines)


def _due_outcome_ready(
    sids: Sequence[uuid.UUID], jobs: Sequence[Row[Any]], delivered: Sequence[Row[Any]]
) -> bool:
    """Each session has one delivery, or its 40s scripted_request job is committed."""
    counts = {sid: count for sid, count in delivered}
    status_by_due: dict[tuple[uuid.UUID, int], str] = {}
    for sid, due_ms, status in jobs:
        if due_ms is None:
            continue
        status_by_due[(sid, int(due_ms))] = status
    for sid in sids:
        if counts.get(sid, 0) == 1 or status_by_due.get((sid, _DUE_ACTIVE_MS)) == "committed":
            continue
        return False
    return True


def test_two_worker_processes_commit_due_jobs_exactly_once(
    migrated: MigratedDatabase, store: ContentStore, tmp_path: Path
) -> None:
    sids = [
        ah.drive_to_task(migrated, store, "tp_demo_v3", f"MP{i}", f"b-mp{i}").session_id
        for i in range(4)
    ]
    for sid in sids:
        ah.set_active(migrated, sid, 40_500)  # first scripted request due at 40 s active

    with ProcessResources() as res:
        procs: list[subprocess.Popen[bytes]] = []
        for i in range(2):
            procs.append(
                res.start(
                    [sys.executable, "-m", "app.workers.main"],
                    _clean_env(
                        migrated.dsns.runtime,
                        {"INVESTIGATORS_WORKER_HEARTBEAT": str(tmp_path / f"hb-{i}.json")},
                    ),
                    tmp_path / f"worker-{i}.log",
                    cwd=BACKEND_DIR,
                )
            )
        deadline = time.monotonic() + _DUE_DEADLINE_S
        while time.monotonic() < deadline:
            for proc in procs:
                rc = proc.poll()
                if rc is not None:
                    jobs, delivered = _scripted_snapshot(migrated, sids)
                    pytest.fail(
                        f"worker exited rc={rc}\n"
                        f"{_format_status(sids, jobs, delivered)}\n"
                        f"{res.redacted_tail()}"
                    )
            jobs, delivered = _scripted_snapshot(migrated, sids)
            if _due_outcome_ready(sids, jobs, delivered):
                break
            time.sleep(0.05)
        else:
            jobs, delivered = _scripted_snapshot(migrated, sids)
            pytest.fail(
                "due scripted_request was not delivered or committed within "
                f"{_DUE_DEADLINE_S:.0f}s\n"
                f"{_format_status(sids, jobs, delivered)}\n"
                f"{res.redacted_tail()}"
            )

        with migrated.runtime.connect() as conn:
            for sid in sids:
                commits = conn.execute(
                    select(s.scheduled_job.c.logical_job_key, func.count())
                    .where(
                        s.scheduled_job.c.session_id == sid,
                        s.scheduled_job.c.status == "committed",
                    )
                    .group_by(s.scheduled_job.c.logical_job_key)
                    .having(func.count() > 1)
                ).all()
                assert not commits, f"duplicate logical job keys: {commits}"
                delivered_n = conn.execute(
                    select(func.count())
                    .select_from(s.event)
                    .where(
                        s.event.c.session_id == sid,
                        s.event.c.type == "scripted_request_delivered",
                    )
                ).scalar_one()
                assert delivered_n == 1, f"session {sid}: {delivered_n} request deliveries (want 1)"
                scripted = conn.execute(
                    select(s.scheduled_job.c.due_active_ms, s.scheduled_job.c.status).where(
                        s.scheduled_job.c.session_id == sid,
                        s.scheduled_job.c.kind == "scripted_request",
                    )
                ).all()
                observed = sorted((int(due), status) for due, status in scripted)
                assert observed == [
                    (_DUE_ACTIVE_MS, "committed"),
                    (_FUTURE_ACTIVE_MS[0], "pending"),
                    (_FUTURE_ACTIVE_MS[1], "pending"),
                ], observed


def test_two_api_processes_dedup_entry_and_commands(
    migrated: MigratedDatabase, store: ContentStore, tmp_path: Path
) -> None:
    with ProcessResources() as res:
        listeners: list[tuple[socket.socket, subprocess.Popen[bytes]]] = []
        for index in range(2):
            sock = _bind_or_fail(res)
            proc = res.start(
                _uvicorn_args(sock.fileno()),
                _clean_env(migrated.dsns.runtime, {"PREVIEW_SIGNING_KEY": "aa" * 32}),
                tmp_path / f"api-{index}.log",
                cwd=BACKEND_DIR,
                pass_fds=(sock.fileno(),),
            )
            listeners.append((sock, proc))

        ah.seed_batch(migrated, store, "tp_demo_v3", "b-api2")
        clients: list[httpx.Client] = []
        for sock, proc in listeners:
            _wait_for_owned_api(res, sock, proc)
            _host, port = sock.getsockname()[:2]
            clients.append(res.http_client(f"http://127.0.0.1:{port}"))

        # Entry race: same platform identifiers hitting both API processes at once.
        # The winner opens the session; the loser waits on the advisory lock, re-reads
        # the link, and is refused (spec §5.1 — a panel ID is never a credential).
        params = ah.query("APIDUP1", "b-api2")
        with ThreadPoolExecutor(max_workers=2) as pool:
            entries = list(
                pool.map(lambda i: clients[i].post("/entry", params=params, timeout=15), (0, 1))
            )
        statuses = sorted(r.status_code for r in entries)
        assert statuses == [200, 403], [r.text for r in entries]
        refused = next(r for r in entries if r.status_code == 403)
        assert refused.json()["error"] == "entry_refused", refused.text
        sid = uuid.UUID(next(r for r in entries if r.status_code == 200).json()["session_id"])

        with migrated.runtime.connect() as conn:
            sessions = conn.execute(
                select(func.count()).select_from(s.session).where(s.session.c.session_id == sid)
            ).scalar_one()
            assert sessions == 1
            revision = conn.execute(
                select(s.session.c.revision).where(s.session.c.session_id == sid)
            ).scalar_one()

        # Share the winner's credential jar across both API processes — the second
        # client is "the same browser" as far as the server can tell.
        winner = next(c for c, r in zip(clients, entries, strict=True) if r.status_code == 200)
        jar = dict(winner.cookies.items())
        for client in clients:
            for name, value in jar.items():
                client.cookies.set(name, value)
        writer_id = jar["inv_writer"]
        csrf = jar["inv_csrf"]

        # Command race: identical command_id to both processes; exactly one application.
        command_id = uuid.uuid4().hex
        payload = {"action": "consented", "text_version": "consent.reference@v3"}

        def post_command(client: httpx.Client) -> httpx.Response:
            return client.post(
                "/commands",
                json={
                    "command_id": command_id,
                    "kind": "consent",
                    "expected_revision": revision,
                    "payload": payload,
                },
                headers={"X-CSRF-Token": csrf, "x-writer-id": writer_id},
                timeout=15,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(post_command, clients))
        assert all(r.status_code == 200 for r in responses), [r.text for r in responses]

        with migrated.runtime.connect() as conn:
            committed = conn.execute(
                select(func.count())
                .select_from(s.command_receipt)
                .where(
                    s.command_receipt.c.session_id == sid,
                    s.command_receipt.c.command_id == uuid.UUID(command_id),
                )
            ).scalar_one()
            assert committed == 1
            consent_events = conn.execute(
                select(func.count())
                .select_from(s.event)
                .where(s.event.c.session_id == sid, s.event.c.type == "consent_recorded")
            ).scalar_one()
            assert consent_events == 1
