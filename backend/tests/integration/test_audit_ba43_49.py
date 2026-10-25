"""Regression coverage for the 2026-10-02 audit addendum (BA-44..BA-49).

Each test inverts an audit reproduction that previously demonstrated the defect:

- BA-44: ``data_use_choice`` is idempotent — a committed choice retries to an
  accepted no-op instead of a primary-key violation, and a conflicting choice is
  refused without touching the recorded row.
- BA-45: ``scheduler.tick`` finishes every pure-dispatch job before any provider
  generation runs, and extends the claimed lease to cover the blocking phase, so
  a shared batch lease can no longer strand a warning behind a slow generate.
- BA-46: the participant mutation endpoints run synchronous persistence off the
  event loop — another coroutine must be able to proceed while a command's DB
  work is still in flight.
- BA-48: the local provider enforces ONE end-to-end budget across identity
  verification, semaphore acquisition, and the HTTP exchange — a spent budget
  fails immediately and oversized responses are refused.
- BA-49: malformed command envelopes map to stable 400/409 rejections — never
  ``internal_error`` — and commit no state.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api import participant
from app.db import schema as s
from app.db import tx
from app.domain.manifests.store import ContentStore
from app.flags import FeatureFlags
from app.main import create_app
from app.providers.base import ProviderError, ProviderErrorKind, ProviderRequest
from app.providers.local import LocalChatProvider
from app.services import credentials, lifecycle, scheduler
from app.timing.clock import VirtualClock

from . import audit_helpers as ah
from .conftest import MigratedDatabase

# No module-level integration mark. Cases that open a database carry
# ``pytest.mark.integration``. Database-free cases stay on the unit selection.
# ``medium`` marks the real-semaphore wait and the loopback trickle server.
# Do not put the module marker back.


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _authed_client(make_settings: Any, db: MigratedDatabase, result: Any) -> TestClient:
    settings = make_settings(
        APP_MODE="qa",
        DATABASE_URL=db.dsns.runtime,
        PREVIEW_SIGNING_KEY="k" * 40,
    )
    app = create_app(settings, clock=VirtualClock(), engine=db.runtime, configure_logs=False)
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set(credentials.SESSION_COOKIE, result.credential_token, path="/")
    client.cookies.set(credentials.CSRF_COOKIE, "csrf-test-token", path="/")
    client.cookies.set("inv_writer", str(result.writer_id), path="/")
    return client


def _csrf() -> dict[str, str]:
    return {credentials.CSRF_HEADER: "csrf-test-token"}


# ----- BA-44: idempotent data_use_choice ------------------------------------


def _drive_to_data_use(db: MigratedDatabase, store: ContentStore, pid: str, batch: str):
    """task → SQL jump to debrief → debrief_ack → data_use_choice stage."""
    result = ah.drive_to_task(db, store, "tp_demo_v3", pid, batch)
    sid, writer = result.session_id, result.writer_id
    with db.runtime.begin() as conn:
        conn.execute(
            s.session.update().where(s.session.c.session_id == sid).values(stage="debrief")
        )
    ah.run(db, store, sid, writer, "debrief_ack", {}, ah.stage(db, sid)[1])
    assert ah.stage(db, sid)[0] == "data_use_choice"
    return result


@pytest.mark.integration
def test_data_use_choice_retry_is_idempotent(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_data_use(migrated, store, "DU1", "b-du1")
    sid, writer = result.session_id, result.writer_id
    first = ah.run(
        migrated,
        store,
        sid,
        writer,
        "data_use_choice",
        {"choice": "use_all"},
        ah.stage(migrated, sid)[1],
    )
    second = ah.run(
        migrated,
        store,
        sid,
        writer,
        "data_use_choice",
        {"choice": "use_all"},
        ah.stage(migrated, sid)[1],
    )
    assert first.result.get("choice") == "use_all"
    # The retry commits an accepted no-op — one row, one event, no PK violation.
    assert second.accepted and second.result.get("already") is True
    with migrated.runtime.connect() as conn:
        rows = conn.execute(
            select(func.count())
            .select_from(s.data_use_choice)
            .where(s.data_use_choice.c.session_id == sid)
        ).scalar_one()
    assert rows == 1
    assert len(ah.events(migrated, sid, "data_use_recorded")) == 1


@pytest.mark.integration
def test_data_use_choice_conflict_is_refused(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_data_use(migrated, store, "DU2", "b-du2")
    sid, writer = result.session_id, result.writer_id
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "data_use_choice",
        {"choice": "use_all"},
        ah.stage(migrated, sid)[1],
    )
    conflict = ah.run(
        migrated,
        store,
        sid,
        writer,
        "data_use_choice",
        {"choice": "use_no_free_text"},
        ah.stage(migrated, sid)[1],
        accept=False,
    )
    assert not conflict.accepted
    assert conflict.reason == "choice_locked"
    with migrated.runtime.connect() as conn:
        kept = conn.execute(
            select(s.data_use_choice.c.choice).where(s.data_use_choice.c.session_id == sid)
        ).scalar_one()
    assert kept == "use_all"


# ----- BA-45: timed dispatch before generation; per-job generation lease -----


def _fake_job(kind: str, payload: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        kind=kind,
        job_id=uuid.uuid4(),
        row={"payload": payload or {}},
        session_id=uuid.uuid4(),
        attempts=0,
    )


def _claims_with_filters(
    jobs: list[SimpleNamespace],
) -> Any:
    """A ``claim_jobs`` fake that honors the BA-45 pass filters: the dispatch
    pass claims with ``defer_provider_generation=True`` and the generation pass
    with ``kinds=["generate"]`` — each job is claimable by exactly one pass."""

    def claim(
        engine: Any,
        *,
        worker_id: str,
        lease_s: float,
        limit: int,
        kinds: Any = None,
        defer_provider_generation: bool = False,
    ) -> list[SimpleNamespace]:
        out = list(jobs)
        if kinds is not None:
            out = [j for j in out if j.kind in kinds]
        if defer_provider_generation:
            out = [
                j
                for j in out
                if not (j.kind == "generate" and not j.row["payload"].get("deterministic_only"))
            ]
        return out

    return claim


def test_tick_dispatches_timing_jobs_before_generation() -> None:
    """Invert the audit reproduction: the warning finishes at elapsed 0, not at
    elapsed 50 behind two 25 s generations — and the passes claim separately so
    a job becoming due mid-generation is picked up without waiting."""
    elapsed = [0.0]
    delivered: list[tuple[str, float]] = []
    extended: list[tuple[uuid.UUID, float]] = []
    jobs = [_fake_job("generate"), _fake_job("generate"), _fake_job("warning")]
    engine = SimpleNamespace(connect=lambda: nullcontext(None))

    def generate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        elapsed[0] += 25
        return {}

    def finish(engine: Any, job: Any, **kwargs: Any):
        delivered.append((job.kind, elapsed[0]))
        return "committed", ()

    def extend(engine: Any, job: Any, *, worker_id: str, lease_s: float) -> Any:
        extended.append((job.job_id, lease_s))
        return job

    with (
        patch.object(tx, "claim_jobs", side_effect=_claims_with_filters(jobs)),
        patch.object(tx, "extend_job_lease", side_effect=extend),
        patch.object(tx, "finish_job", side_effect=finish),
        patch.object(scheduler, "run_generation", side_effect=generate),
    ):
        assert scheduler.tick(engine, None, worker_id="audit", provider=None) == 3
    assert [kind for kind, _ in delivered] == ["warning", "generate", "generate"]
    assert delivered[0][1] == 0  # warning committed before any generation ran
    assert len(extended) == 2  # each generation job re-leased for its blocking phase


@pytest.mark.integration
def test_dispatch_claim_never_sees_provider_generation(migrated: MigratedDatabase) -> None:
    """The deferred claim is enforced at the SQL level: a due provider generate
    job and a due warning coexist, and ``defer_provider_generation=True`` claims
    only the warning — the generate row stays pending for the dedicated pass."""
    result = ah.drive_to_task(migrated, ContentStore.load_default(), "tp_demo_v3", "DF1", "b-df1")
    sid = result.session_id
    gen_job_id = uuid.uuid4()
    with migrated.runtime.begin() as conn:
        now = conn.execute(select(func.now())).scalar_one()
        conn.execute(
            s.scheduled_job.insert().values(
                session_id=sid,
                job_id=gen_job_id,
                logical_job_key="audit-generate",
                kind="generate",
                clock_basis="wall",
                due_wall_at=now,
                claimable_at=now,
                payload={"timeout_ms": 25000},
                status="pending",
            )
        )
    ah.set_active(migrated, sid, 300_000)  # surface the due warning job too

    claimed = tx.claim_jobs(
        migrated.runtime,
        worker_id="w-df",
        lease_s=5.0,
        limit=10,
        defer_provider_generation=True,
    )
    assert gen_job_id not in [c.job_id for c in claimed]  # provider job untouched
    with migrated.runtime.connect() as conn:
        status = conn.execute(
            select(s.scheduled_job.c.status).where(
                s.scheduled_job.c.job_id == gen_job_id,
            )
        ).scalar_one()
    assert status == "pending"  # still claimable by the generation pass

    # ...and the generation pass claims it under kinds=["generate"].
    gen_claimed = tx.claim_jobs(
        migrated.runtime, worker_id="w-g", lease_s=5.0, limit=10, kinds=["generate"]
    )
    assert gen_job_id in [c.job_id for c in gen_claimed]
    assert all(c.kind == "generate" for c in gen_claimed)


def test_tick_skips_a_generate_job_whose_lease_was_lost() -> None:
    finished: list[str] = []
    jobs = [_fake_job("warning"), _fake_job("generate")]
    engine = SimpleNamespace(connect=lambda: nullcontext(None))

    def finish(engine: Any, job: Any, **kwargs: Any):
        finished.append(job.kind)
        return "committed", ()

    with (
        patch.object(tx, "claim_jobs", side_effect=_claims_with_filters(jobs)),
        patch.object(
            tx,
            "extend_job_lease",
            side_effect=tx.JobLeaseLostError("claimed by another worker"),
        ),
        patch.object(tx, "finish_job", side_effect=finish),
        patch.object(scheduler, "run_generation") as gen,
    ):
        assert scheduler.tick(engine, None, worker_id="audit", provider=None) == 1
    assert finished == ["warning"]
    gen.assert_not_called()  # no wasted provider call under a dead lease


@pytest.mark.integration
def test_extend_job_lease_requires_ownership(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """The extension is authorized exactly like finish_job — a re-claimed job
    cannot be extended by its former owner."""
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "LX1", "b-lx1")
    sid = result.session_id
    ah.set_active(migrated, sid, 300_000)  # make the warning job due
    claimed = tx.claim_jobs(migrated.runtime, worker_id="w-ext", lease_s=5.0, limit=5)
    assert claimed
    job = claimed[0]
    moved = tx.extend_job_lease(migrated.runtime, job, worker_id="w-ext", lease_s=120.0)
    with migrated.runtime.connect() as conn:
        expiry = conn.execute(
            select(s.scheduled_job.c.lease_expires_at).where(s.scheduled_job.c.job_id == job.job_id)
        ).scalar_one()
    assert abs((expiry - moved.lease_expires_at).total_seconds()) < 2
    assert moved.lease_expires_at > job.lease_expires_at
    # A different worker cannot extend — ownership check holds.
    with pytest.raises(tx.JobLeaseLostError):
        tx.extend_job_lease(migrated.runtime, job, worker_id="w-other", lease_s=120.0)


# ----- BA-46: persistence off the event loop ---------------------------------


def test_async_command_does_not_block_other_coroutines() -> None:
    """The audit reproduction observed ``['db_returned', 'heartbeat']`` — the DB
    transaction blocked the loop. With the threadpool the heartbeat completes
    while the synchronous command is still sleeping."""

    async def scenario() -> list[str]:
        timeline: list[str] = []

        async def heartbeat() -> None:
            await asyncio.sleep(0.01)
            timeline.append("heartbeat")

        async def body() -> bytes:
            return json.dumps(
                {"kind": "resume", "command_id": str(uuid.uuid4()), "expected_revision": 0}
            ).encode()

        async def stream():
            yield await body()

        request = SimpleNamespace(body=body, stream=stream, headers={}, cookies={})

        def execute(*args: Any, **kwargs: Any) -> Any:
            time.sleep(0.08)
            timeline.append("db_returned")
            return SimpleNamespace(accepted=False, reason="fixture")

        with (
            patch.object(participant, "_check_csrf", return_value=None),
            patch.object(
                participant,
                "_authenticate",
                return_value=(uuid.uuid4(), {"revision": 0, "writer_id": None}),
            ),
            patch.object(participant, "_store", return_value=None),
            patch.object(participant, "_build_sha", return_value="fixture"),
            patch.object(participant, "_behavior_sha", return_value="fixture"),
            patch.object(participant, "_model_digest", return_value="fixture"),
            patch.object(
                participant,
                "_settings",
                return_value=SimpleNamespace(flags=FeatureFlags),
            ),
            patch.object(participant.lifecycle, "handler_for", return_value=None),
            patch.object(participant, "_engine", return_value=None),
            patch.object(participant, "execute_command", side_effect=execute),
        ):
            task = asyncio.create_task(heartbeat())
            await asyncio.sleep(0)
            resp = await participant.post_commands(request)
            await task
            assert resp.status_code == 409
        return timeline

    assert asyncio.run(scenario()) == ["heartbeat", "db_returned"]


# ----- BA-49: envelope validation --------------------------------------------

_MALFORMED: list[tuple[Any, str]] = [
    ([], "malformed_command"),
    ("text", "malformed_command"),
    (42, "malformed_command"),
    (None, "malformed_json"),
    ({"kind": "resume", "expected_revision": "abc"}, "malformed_command"),
    ({"kind": "resume", "expected_revision": None}, "malformed_command"),
    ({"kind": "resume", "expected_revision": -1}, "malformed_command"),
    ({"kind": "resume", "expected_revision": 2**40}, "malformed_command"),
    ({"kind": "resume", "expected_revision": True}, "malformed_command"),
    ({"kind": "resume", "command_id": "not-a-uuid"}, "malformed_command"),
    ({"kind": "resume", "command_id": 42}, "malformed_command"),
    ({"kind": "resume", "payload": [1, 2]}, "malformed_command"),
    ({"kind": "resume", "payload": {"x": "y" * 40_000}}, "command_too_large"),
    ({"kind": "resume", "client_monotonic_ms": -5}, "malformed_command"),
    ({"kind": "resume", "client_monotonic_ms": "now"}, "malformed_command"),
]


@pytest.mark.integration
def test_malformed_commands_reject_with_stable_errors(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "MV1", "b-mv1")
    client = _authed_client(make_settings, migrated, result)
    sid = result.session_id
    with migrated.runtime.connect() as conn:
        receipts_before = conn.execute(
            select(func.count())
            .select_from(s.command_receipt)
            .where(s.command_receipt.c.session_id == sid)
        ).scalar_one()
        revision_before = conn.execute(
            select(s.session.c.revision).where(s.session.c.session_id == sid)
        ).scalar_one()
    with client:
        for body, reason in _MALFORMED:
            resp = client.post("/commands", json=body, headers=_csrf())
            assert resp.status_code == 400, (body, resp.text)
            assert resp.json()["error"] == reason
        # Malformed JSON never reaches json.loads' object check.
        resp = client.post(
            "/commands",
            content=b"{not json",
            headers={**_csrf(), "content-type": "application/json"},
        )
        assert resp.status_code == 400 and resp.json()["error"] == "malformed_json"
        # An unknown kind is an expected rejection, not an internal error.
        resp = client.post(
            "/commands",
            json={"kind": "no_such_command", "expected_revision": ah.stage(migrated, sid)[1]},
            headers={**_csrf(), "x-writer-id": str(result.writer_id)},
        )
        assert resp.status_code == 409 and resp.json()["error"] == "unknown_command"
        # /complete shares the same envelope rules.
        resp = client.post("/complete", json={"expected_revision": "soon"}, headers=_csrf())
        assert resp.status_code == 400 and resp.json()["error"] == "malformed_command"
        resp = client.post("/complete", json=[1, 2], headers=_csrf())
        assert resp.status_code == 400
    # Nothing committed: no receipts beyond the drive-up ones, revision untouched.
    with migrated.runtime.connect() as conn:
        receipts_after = conn.execute(
            select(func.count())
            .select_from(s.command_receipt)
            .where(s.command_receipt.c.session_id == sid)
        ).scalar_one()
        revision_after = conn.execute(
            select(s.session.c.revision).where(s.session.c.session_id == sid)
        ).scalar_one()
    assert receipts_after == receipts_before
    assert revision_after == revision_before


@pytest.mark.integration
def test_malformed_telemetry_rejects_cleanly(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "MV2", "b-mv2")
    client = _authed_client(make_settings, migrated, result)
    with client:
        for body in ([1, 2], "text", None):
            resp = client.post("/telemetry", json=body, headers=_csrf())
            assert resp.status_code == 400, (body, resp.text)
            assert resp.json()["error"] != "internal_error"
        resp = client.post(
            "/telemetry",
            content=b"\xff\xfe{bad",
            headers={**_csrf(), "content-type": "application/json"},
        )
        assert resp.status_code == 400 and resp.json()["error"] == "malformed_json"


# ----- BA-48: one end-to-end budget through local inference -------------------


def _request() -> ProviderRequest:
    return ProviderRequest(
        purpose="assistant", prompt_id="audit", messages=(), params={}, request_id="audit"
    )


def _provider(**kwargs: Any) -> LocalChatProvider:
    return LocalChatProvider(
        "http://127.0.0.1:1",
        model="fixture",
        model_digest="a" * 64,
        **kwargs,
    )


def _listing_ok(url: str, timeout: float) -> tuple[int, bytes]:
    return 200, json.dumps({"models": [{"name": "fixture", "digest": "a" * 64}]}).encode()


def test_identity_check_consumes_the_shared_budget() -> None:
    """A 9 s identity check inside a 10 s call leaves ~1 s for the semaphore —
    not a fresh 10 s (the audit observed a 19 s total)."""
    elapsed = [0.0]
    waits: list[float] = []

    def listing(url: str, timeout: float) -> tuple[int, bytes]:
        elapsed[0] += 9
        return _listing_ok(url, timeout)

    provider = _provider(get_transport=listing)

    class Semaphore:
        def acquire(self, timeout: float) -> bool:
            waits.append(timeout)
            elapsed[0] += timeout
            return False

        def release(self) -> None:  # pragma: no cover
            pass

    provider._semaphore = Semaphore()  # type: ignore[assignment]
    with (
        patch("app.providers.local.time.monotonic", side_effect=lambda: elapsed[0]),
        pytest.raises(ProviderError) as exc,
    ):
        provider.generate(_request(), timeout_s=10)
    assert exc.value.kind == ProviderErrorKind.TIMEOUT
    assert waits == [pytest.approx(1)]
    assert elapsed[0] == pytest.approx(10)


def test_exhausted_budget_fails_before_the_semaphore() -> None:
    """Identity verification that consumes the whole budget must fail the call
    immediately — no semaphore wait, no request."""
    elapsed = [0.0]
    calls: list[str] = []

    def listing(url: str, timeout: float) -> tuple[int, bytes]:
        elapsed[0] += 10
        return _listing_ok(url, timeout)

    def post(url: str, body: bytes, timeout: float) -> tuple[int, bytes]:
        calls.append("post")
        return 200, b"{}"

    provider = _provider(get_transport=listing, transport=post)
    with (
        patch("app.providers.local.time.monotonic", side_effect=lambda: elapsed[0]),
        pytest.raises(ProviderError) as exc,
    ):
        provider.generate(_request(), timeout_s=10)
    assert exc.value.kind == ProviderErrorKind.TIMEOUT
    assert calls == []


def test_late_response_is_a_timeout_not_a_result() -> None:
    """A transport that resolves past the deadline is a failure — a slow answer
    must never land as if it made the budget."""
    elapsed = [0.0]

    def post(url: str, body: bytes, timeout: float) -> tuple[int, bytes]:
        elapsed[0] += timeout + 1  # ignores the budget it was given
        return 200, json.dumps(
            {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}
        ).encode()

    provider = _provider(get_transport=_listing_ok, transport=post)
    with (
        patch("app.providers.local.time.monotonic", side_effect=lambda: elapsed[0]),
        pytest.raises(ProviderError) as exc,
    ):
        provider.generate(_request(), timeout_s=5)
    assert exc.value.kind == ProviderErrorKind.TIMEOUT


def test_oversized_response_is_refused() -> None:
    def post(url: str, body: bytes, timeout: float) -> tuple[int, bytes]:
        return 200, b"x" * (4 * 1024 * 1024 + 1)

    provider = _provider(get_transport=_listing_ok, transport=post)
    with pytest.raises(ProviderError) as exc:
        provider.generate(_request(), timeout_s=5)
    assert exc.value.kind == ProviderErrorKind.INVALID_RESPONSE


@pytest.mark.medium
def test_semaphore_contention_uses_remaining_budget() -> None:
    """Two callers share one semaphore; the loser's wait is bounded by what the
    call has left, not a fresh timeout."""
    provider = _provider(
        get_transport=_listing_ok, transport=lambda *a: (200, b"{}"), max_concurrency=1
    )
    assert provider._semaphore.acquire(timeout=1)  # hold the only-ish slot
    elapsed = [0.0]
    with patch("app.providers.local.time.monotonic", side_effect=lambda: elapsed[0]):
        with pytest.raises(ProviderError) as exc:
            provider.generate(_request(), timeout_s=2)
        # BoundedSemaphore acquire(timeout=2) waited the real remainder, not a
        # fresh budget — the failure is TIMEOUT either way.
        assert exc.value.kind == ProviderErrorKind.TIMEOUT
    provider._semaphore.release()


# ----- BA-46 (gap): authentication itself must not block the loop ------------


def test_delayed_auth_does_not_block_other_coroutines() -> None:
    """The verification gap: ``_authenticate`` performs synchronous DB work.
    Called directly inside an async endpoint it blocked the loop before the
    threadpool offload of the command body — a delayed-auth reproduction wedged
    an unrelated heartbeat. Now auth itself is offloaded."""

    async def scenario() -> list[str]:
        timeline: list[str] = []

        async def heartbeat() -> None:
            await asyncio.sleep(0.01)
            timeline.append("heartbeat")

        async def body() -> bytes:
            return json.dumps(
                {"kind": "resume", "command_id": str(uuid.uuid4()), "expected_revision": 0}
            ).encode()

        async def stream():
            yield await body()

        request = SimpleNamespace(body=body, stream=stream, headers={}, cookies={})

        def auth(request: Any) -> Any:
            time.sleep(0.08)  # the synchronous authenticate's slow DB round-trip
            timeline.append("auth_returned")
            return uuid.uuid4(), {"revision": 0, "writer_id": None}

        def execute(*args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(
                accepted=False, reason="fixture", result={}, event_seqs=(), replayed=False
            )

        with (
            patch.object(participant, "_check_csrf", return_value=None),
            patch.object(participant, "_authenticate", side_effect=auth),
            patch.object(participant, "_store", return_value=None),
            patch.object(participant, "_build_sha", return_value="fixture"),
            patch.object(participant, "_behavior_sha", return_value="fixture"),
            patch.object(participant, "_model_digest", return_value="fixture"),
            patch.object(
                participant,
                "_settings",
                return_value=SimpleNamespace(flags=FeatureFlags),
            ),
            patch.object(participant.lifecycle, "handler_for", return_value=None),
            patch.object(participant, "_engine", return_value=None),
            patch.object(participant, "execute_command", side_effect=execute),
        ):
            task = asyncio.create_task(heartbeat())
            await asyncio.sleep(0)
            resp = await participant.post_commands(request)
            await task
            assert resp.status_code == 409
        return timeline

    # The heartbeat completes WHILE the synchronous authenticate is still
    # sleeping in the threadpool — not after it returns.
    assert asyncio.run(scenario()) == ["heartbeat", "auth_returned"]


# ----- BA-48 (gap): a trickled body is bounded by the whole-call deadline -----


def _trickle_server(chunks: int, interval_s: float) -> tuple[Any, str]:
    """An http server that delivers a valid-but-glacial response: headers fast,
    then one byte per ``interval_s`` — the audit's trickle reproduction."""
    import http.server
    import threading

    body = json.dumps(
        {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
    ).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            step = max(1, len(body) // chunks)
            for i in range(0, len(body), step):
                try:
                    self.wfile.write(body[i : i + step])
                    self.wfile.flush()
                except OSError:
                    break  # client closed on deadline
                time.sleep(interval_s)

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


@pytest.mark.medium
def test_trickled_body_respects_the_total_call_budget() -> None:
    """Invert the audit's loopback reproduction: a response that sends headers
    immediately then one byte per 50 ms previously held the call ~1.9 s inside a
    0.2 s budget. The deadline-aware read must stop near the budget and close."""
    from app.providers import local as local_mod

    server, base = _trickle_server(chunks=80, interval_s=0.05)
    try:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            local_mod._urllib_transport(f"{base}/v1/chat/completions", b"{}", 0.2)
        elapsed = time.monotonic() - started
        # Near the budget — not the audit's observed ~10x overrun.
        assert elapsed < 1.0, f"trickle held the call {elapsed:.2f}s"
    finally:
        server.shutdown()
        server.server_close()


def test_read_bounded_fake_resp_obeys_deadline() -> None:
    """Unit-level: the reader raises TimeoutError once the deadline passes, even
    though each individual read returns promptly (the trickle shape)."""
    from app.providers import local as local_mod

    elapsed = [0.0]

    class Resp:
        fp = None

        def read1(self, n: int) -> bytes:
            elapsed[0] += 0.05  # one prompt recv per call, slowly
            return b"x"

        def read(self, n: int) -> bytes:  # pragma: no cover
            return self.read1(n)

    with (
        patch("app.providers.local.time.monotonic", side_effect=lambda: elapsed[0]),
        pytest.raises(TimeoutError),
    ):
        local_mod._read_bounded(Resp(), deadline=0.2)


# ----- BA-49 (gaps): remaining malformed-input 500 paths ----------------------


@pytest.mark.integration
def test_huge_client_clock_and_other_numeric_edges_reject_400(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """A client_monotonic_ms far outside int8/float-precision range is a stable
    400 — never a driver overflow or an internal error."""
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "MV3", "b-mv3")
    client = _authed_client(make_settings, migrated, result)
    with client:
        for value in (10**40, -(10**40), 2**63, True):
            resp = client.post(
                "/commands",
                json={"kind": "resume", "client_monotonic_ms": value},
                headers=_csrf(),
            )
            assert resp.status_code == 400, (value, resp.text)
            assert resp.json()["error"] == "malformed_command"
        # Non-finite floats aren't JSON-serializable client-side — send the raw
        # literals a hand-rolled client could emit (Python's json.loads parses them).
        for literal in ("Infinity", "-Infinity", "NaN"):
            resp = client.post(
                "/commands",
                content=f'{{"kind": "resume", "client_monotonic_ms": {literal}}}'.encode(),
                headers={**_csrf(), "content-type": "application/json"},
            )
            assert resp.status_code == 400, (literal, resp.text)
            assert resp.json()["error"] == "malformed_command"
        # An in-range int still flows through validation — the response is the
        # command outcome (success or an expected rejection), never a 500.
        resp = client.post(
            "/commands",
            json={"kind": "resume", "client_monotonic_ms": 12345},
            headers=_csrf(),
        )
        assert resp.status_code != 400
        assert resp.status_code != 500
        assert resp.json().get("error") != "internal_error"


@pytest.mark.integration
def test_telemetry_with_pathological_item_fields_never_500s(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """Array/object kinds, unhashable delivery_kind values, and out-of-range
    seqs/client clocks are dropped or degraded — no 500, no events written."""
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "MV4", "b-mv4")
    client = _authed_client(make_settings, migrated, result)
    sid = result.session_id
    bodies = [
        {"kind": ["render"], "data": {}},
        {"kind": {"bad": "shape"}, "data": {}},
        {
            "kind": "batch",
            "data": {
                "events": [
                    {"kind": ["render"], "data": {}},
                    # Unhashable delivery_kind — `not in frozenset` used to TypeError.
                    {"kind": "render", "data": {"delivery_kind": ["initial"], "seq": 1}},
                    # Out-of-int8 seq — the bind used to DataError.
                    {"kind": "receive", "data": {"seq": 10**30}},
                    # A client clock outside int8 — degraded to NULL, not a 500.
                    {"kind": "focus", "client_monotonic_ms": 10**40, "data": {}},
                    {"kind": "focus", "client_monotonic_ms": -(10**40), "data": {}},
                    # Chat paints participant shares as ``share-<seq>``. That
                    # id is not a message UUID; the receipt lookup must not
                    # DataError and roll back the share sitting beside it.
                    {"kind": "render", "data": {"message_id": "share-3", "channel": "team"}},
                    {
                        "kind": "share_attempt",
                        "data": {"record_id": "C8-5", "via": "drag", "result": "sent"},
                    },
                ]
            },
        },
    ]
    with client:
        for body in bodies:
            resp = client.post("/telemetry", json=body, headers=_csrf())
            assert resp.status_code == 200, (body, resp.text)
            assert resp.json() == {"ok": True}
    with migrated.runtime.connect() as conn:
        telemetry_events = conn.execute(
            select(func.count())
            .select_from(s.event)
            .where(
                s.event.c.session_id == sid,
                s.event.c.type.like("telemetry_%"),
            )
        ).scalar_one()
    # render + receive (receipt path degrades, event still lands) + two focus
    # + the non-UUID render and the share beside it. Array/object kinds
    # never produced events.
    assert telemetry_events == 6


@pytest.mark.integration
def test_conflicting_complete_command_id_is_a_stable_conflict(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """Reusing a committed command_id under a different payload through
    /complete returns 409 command_conflict — the same mapping /commands has —
    instead of a 500."""
    result = _drive_to_data_use(migrated, store, "MV5", "b-mv5")
    sid, writer = result.session_id, result.writer_id
    client = _authed_client(make_settings, migrated, result)
    # Commit a normal command under a chosen id.
    command_id = str(uuid.uuid4())
    tx.execute_command(
        migrated.runtime,
        tx.Command(
            command_id=uuid.UUID(command_id),
            session_id=sid,
            kind="data_use_choice",
            expected_revision=ah.stage(migrated, sid)[1],
            payload={"choice": "use_all"},
        ),
        lifecycle.handler_for(
            "data_use_choice",
            store=store,
            behavior_sha="fixture",
            writer=writer,
        ),
    )
    headers = {**_csrf(), "x-writer-id": str(writer)}
    with client:
        resp = client.post(
            "/complete",
            json={
                "command_id": command_id,
                "expected_revision": ah.stage(migrated, sid)[1],
                "payload": {"different": True},
            },
            headers=headers,
        )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "command_conflict"


@pytest.mark.integration
def test_oversized_body_rejected_without_full_buffering(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """The 64 KiB cap applies DURING the read: an oversized body is rejected
    from the content-length header alone — no buffering, no 500."""
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "MV6", "b-mv6")
    client = _authed_client(make_settings, migrated, result)
    with client:
        resp = client.post(
            "/commands",
            content=b" " * (70 * 1024),
            headers={**_csrf(), "content-type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "command_too_large"
        # Chunked (no content-length): the stream aborts past the cap too.
        resp = client.post(
            "/commands",
            content=b" " * (70 * 1024),
            headers={
                **_csrf(),
                "content-type": "application/json",
                "transfer-encoding": "chunked",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] in ("command_too_large", "malformed_json")
