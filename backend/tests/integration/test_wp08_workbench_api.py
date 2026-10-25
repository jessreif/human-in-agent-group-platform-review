"""INV-0803/0805: HTTP-level coverage for the additive ``GET /workbench`` projection and
the cursor-based ``GET /events/stream`` feed against disposable PostgreSQL.

Stage driving uses the internal command machinery (as in ``test_world_persistence``);
the endpoints under test are exercised over HTTP exactly as the participant client does —
session + CSRF + writer cookies, cursor replay, and stream origin checks.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.engine import Engine

from app.api.participant import (
    _STREAM_STATE_DEADLINE_S,
    _STREAM_STATE_SLEEP,
    _STREAM_STATE_STOPPED,
)
from app.db import schema as s
from app.db.tx import Command, CommandResult, execute_command
from app.domain.manifests.store import ContentStore
from app.main import create_app
from app.services import credentials, lifecycle, scheduler
from app.services.entry import EntryResult, enter
from app.services.projection import project_event
from app.timing.clock import VirtualClock

from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration

BUILD_SHA = "ab" * 32
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


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _seed_batch(db: MigratedDatabase, store: ContentStore, batch_id: str) -> None:
    from app.domain.manifests.binding import bind
    from app.services.sessions import register_manifest

    bound = bind(store, "tp_demo_v3", mode="qa")
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


def _drive_to_task(db: MigratedDatabase, store: ContentStore, pid: str, batch: str) -> EntryResult:
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
    sid, writer = result.session_id, result.writer_id
    _run(
        db,
        store,
        sid,
        writer,
        "consent",
        {"action": "consented", "text_version": "consent.reference@v3"},
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

    def force_wall(kind: str) -> None:
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

    force_wall("connecting")
    _run(db, store, sid, writer, "ready", {}, _stage(db, sid)[1])
    force_wall("task_open")
    assert _stage(db, sid)[0] in ("task", "solo_review")
    return result


def _authed_client(
    make_settings: Any,
    db: MigratedDatabase,
    result: EntryResult,
    **settings_overrides: Any,
) -> TestClient:
    settings = make_settings(
        APP_MODE="qa",
        DATABASE_URL=db.dsns.runtime,
        PREVIEW_SIGNING_KEY="k" * 40,
        **settings_overrides,
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


def _noop_stream_sleep(_seconds: float) -> None:
    """No-op poll sleeper. Production still sleeps ``STREAM_POLL_S``."""
    return None


def _use_short_stream(client: TestClient, *, deadline_s: float = 0.0) -> None:
    """Install the test-only request.state seam. Not a query parameter."""
    client.app_state[_STREAM_STATE_SLEEP] = _noop_stream_sleep
    client.app_state[_STREAM_STATE_DEADLINE_S] = deadline_s


def _parse_sse(buf: str) -> list[dict[str, str]]:
    frames: list[dict[str, str]] = []
    for block in buf.split("\n\n"):
        if not block.strip():
            continue
        frame: dict[str, str] = {}
        for line in block.splitlines():
            if not line or line.startswith(":"):
                continue
            name, sep, value = line.partition(":")
            if not sep:
                continue
            if value.startswith(" "):
                value = value[1:]
            frame[name] = value
        if frame:
            frames.append(frame)
    return frames


def _read_stream_prefix(
    client: TestClient,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    max_chunks: int = 40,
) -> str:
    """Read replay and the first head frame.

    TestClient buffers until the app returns, so this is not streaming proof.
    Callers install a short deadline and a no-op sleeper first.
    """
    buf = ""
    with client.stream("GET", path, headers=headers) as resp:
        assert resp.status_code == 200
        for i, chunk in enumerate(resp.iter_text()):
            buf += chunk
            if "event: head" in buf or i >= max_chunks:
                break
    return buf


def test_workbench_projection_authorized_and_readonly(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    result = _drive_to_task(migrated, store, "WB1", "qa-wb1")
    client = _authed_client(make_settings, migrated, result)
    with client:
        before = client.get("/session").json()["session"]
        resp = client.get("/workbench")
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["stage"] in ("task", "solo_review")
        assert body["world_ready"] is True
        assert len(body["assigned_crates"]) == 4
        assert body["assigned_crates"] == sorted(body["assigned_crates"])
        assert len(body["locations"]) == 6
        assert body["file"], "the participant's own case file must be projected"
        for row in body["file"]:
            assert set(row) <= {
                "record_id",
                "crate",
                "stamp",
                "body",
                "event_type",
                "source",
                "shared_by",  # O-4: seat that first shared the card into chat
            }
        assert isinstance(body["channels"], list) and body["channels"]
        assert body["answers"] == {} and body["frozen_answers"] == {}

        # Authorized projection only — no experimental truth / arm / internals keys.
        keys: set[str] = set()
        _collect_keys(body, keys)
        assert not (keys & FORBIDDEN_KEYS), keys & FORBIDDEN_KEYS

        # GETs are read-only: no events committed, no revision bump.
        client.get("/workbench")
        client.get("/workbench")
        after = client.get("/session").json()["session"]
        assert after["next_event_seq"] == before["next_event_seq"]
        assert after["revision"] == before["revision"]


def test_workbench_requires_auth(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    _drive_to_task(migrated, store, "WB2", "qa-wb2")
    settings = make_settings(
        APP_MODE="qa",
        DATABASE_URL=migrated.dsns.runtime,
        PREVIEW_SIGNING_KEY="k" * 40,
    )
    app = create_app(settings, clock=VirtualClock(), engine=migrated.runtime, configure_logs=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/workbench").status_code == 401
        assert client.get("/events/stream").status_code == 401


def test_events_stream_replays_then_emits_head(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    result = _drive_to_task(migrated, store, "WB3", "qa-wb3")
    client = _authed_client(make_settings, migrated, result)
    with migrated.runtime.connect() as conn:
        rows = list(
            conn.execute(
                select(s.event)
                .where(s.event.c.session_id == result.session_id)
                .order_by(s.event.c.seq)
            ).mappings()
        )
    internal_seqs = [int(row["seq"]) for row in rows if project_event(row) is None]
    visible_seqs = [int(row["seq"]) for row in rows if project_event(row) is not None]
    if not internal_seqs:
        pytest.fail("drive_to_task produced no internal event; cannot prove cursor advance")
    if not visible_seqs:
        pytest.fail("drive_to_task produced no participant-visible event to replay")
    with client:
        # TestClient buffers until the app returns, so this is not streaming proof.
        # The short deadline and no-op sleeper are what keep it from waiting STREAM_MAX_S.
        _use_short_stream(client)
        buf = _read_stream_prefix(client, "/events/stream?after_seq=0")
        frames = _parse_sse(buf)
        ids = [int(frame["id"]) for frame in frames if "id" in frame]
        # Durable seq ids on each replayed event drive Last-Event-ID resume.
        assert ids == visible_seqs
        for seq in internal_seqs:
            assert seq not in ids  # internal event advances the cursor without an id: frame
        heads = [json.loads(frame["data"]) for frame in frames if frame.get("event") == "head"]
        assert heads
        head = heads[0]
        assert set(head) >= {"next_event_seq", "server_time", "cursor"}
        assert head["next_event_seq"] >= 1
        assert isinstance(head["server_time"], str) and head["server_time"]
        assert head["cursor"] >= max(internal_seqs)
        assert head["cursor"] >= max(visible_seqs)

        # Resuming at the scanned cursor replays nothing — only a head frame.
        buf2 = _read_stream_prefix(client, f"/events/stream?after_seq={head['cursor']}")
        resumed = _parse_sse(buf2)
        assert not any("id" in frame for frame in resumed)
        assert any(frame.get("event") == "head" for frame in resumed)

        # Last-Event-ID resume does not replay the delivered visible events.
        last_visible = visible_seqs[-1]
        buf3 = _read_stream_prefix(
            client,
            "/events/stream",
            headers={"Last-Event-ID": str(last_visible)},
        )
        by_id = _parse_sse(buf3)
        resumed_ids = [int(frame["id"]) for frame in by_id if "id" in frame]
        assert resumed_ids == []
        resume_heads = [
            json.loads(frame["data"]) for frame in by_id if frame.get("event") == "head"
        ]
        assert resume_heads
        later_internal = [seq for seq in internal_seqs if seq > last_visible]
        if later_internal:
            assert resume_heads[0]["cursor"] >= max(later_internal)


def _assert_engine_idle(engine: Engine) -> None:
    """The stream must not leave a pooled connection checked out or idle in a transaction."""
    assert engine.pool.checkedout() == 0
    with engine.connect() as conn:
        assert not conn.in_transaction()
        leaked = conn.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND state = 'idle in transaction'"
            )
        ).scalar_one()
    assert leaked == 0
    assert engine.pool.checkedout() == 0


def test_events_stream_disconnect_after_head(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """ASGI disconnect after the first head frame, not a buffered TestClient read.

    TestClient buffers until the app returns, so it cannot see an incremental chunk
    or stop the generator early. This drives the app with ``http.disconnect``.
    Starlette listens for that message only when the ASGI spec version is below 2.4.
    """
    result = _drive_to_task(migrated, store, "WB6", "qa-wb6")
    settings = make_settings(
        APP_MODE="qa",
        DATABASE_URL=migrated.dsns.runtime,
        PREVIEW_SIGNING_KEY="k" * 40,
    )
    app = create_app(settings, clock=VirtualClock(), engine=migrated.runtime, configure_logs=False)
    stopped: list[bool] = []
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/events/stream",
        "raw_path": b"/events/stream",
        "query_string": b"after_seq=0",
        "headers": [
            (b"host", b"testserver"),
            (
                b"cookie",
                f"{credentials.SESSION_COOKIE}={result.credential_token}".encode(),
            ),
        ],
        "scheme": "http",
        "client": ("127.0.0.1", 123),
        "server": ("testserver", 80),
        "state": {
            _STREAM_STATE_SLEEP: _noop_stream_sleep,
            # Short deadline: a missed disconnect cannot sit in STREAM_MAX_S.
            _STREAM_STATE_DEADLINE_S: 0.0,
            _STREAM_STATE_STOPPED: stopped,
        },
    }
    bodies: list[bytes] = []
    status: list[int] = []
    saw_chunk_before_return = False

    async def drive() -> None:
        nonlocal saw_chunk_before_return
        head_seen = anyio.Event()
        request_sent = False

        async def receive() -> dict[str, Any]:
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await head_seen.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            nonlocal saw_chunk_before_return
            if message["type"] == "http.response.start":
                status.append(int(message["status"]))
                return
            if message["type"] != "http.response.body":
                return
            body = message.get("body", b"")
            if not body:
                return
            if isinstance(body, str):
                body = body.encode()
            bodies.append(body)
            saw_chunk_before_return = True
            if b"event: head" in b"".join(bodies):
                head_seen.set()

        # 5 s fuse: a broken deadline injection must not run for STREAM_MAX_S.
        with anyio.fail_after(5):
            await app(scope, receive, send)

    started = time.perf_counter()
    anyio.run(drive)
    elapsed = time.perf_counter() - started
    assert elapsed < 5
    assert status == [200]
    assert saw_chunk_before_return
    payload = b"".join(bodies)
    assert b"event: head" in payload
    assert stopped == [True]
    _assert_engine_idle(migrated.runtime)


def test_events_stream_refuses_cross_origin(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    result = _drive_to_task(migrated, store, "WB4", "qa-wb4")
    client = _authed_client(make_settings, migrated, result)
    with client:
        resp = client.get("/events/stream", headers={"Origin": "https://evil.example"})
        assert resp.status_code == 403
        assert resp.json()["error"] == "bad_origin"


def test_http_answer_round_trip_through_workbench(
    migrated: MigratedDatabase, store: ContentStore, make_settings: Any
) -> None:
    """A command committed over HTTP is reflected in the projection and the event feed —
    the exact path the workbench's answer controls take."""
    result = _drive_to_task(migrated, store, "WB5", "qa-wb5")
    client = _authed_client(make_settings, migrated, result)
    with client:
        wb = client.get("/workbench").json()
        crate = wb["assigned_crates"][0]
        rev = client.get("/session").json()["session"]["revision"]
        cmd = client.post(
            "/commands",
            json={
                "kind": "answer_update",
                "command_id": str(uuid.uuid4()),
                "expected_revision": rev,
                "payload": {"crate": crate, "location_id": "The Sawmill"},
            },
            headers={credentials.CSRF_HEADER: "csrf-test-token"},
        )
        assert cmd.status_code == 200, cmd.json()
        assert cmd.json()["accepted"] is True

        wb2 = client.get("/workbench").json()
        assert wb2["answers"][str(crate)]["location_id"] == "The Sawmill"

        feed = client.get("/events", params={"after_seq": 0}).json()
        types = [e["type"] for e in feed["events"]]
        assert "answer_updated" in types

        # Stale expected_revision is refused with the conflict token the client resyncs on.
        stale = client.post(
            "/commands",
            json={
                "kind": "answer_update",
                "command_id": str(uuid.uuid4()),
                "expected_revision": rev,
                "payload": {"crate": crate, "location_id": "The Marina"},
            },
            headers={credentials.CSRF_HEADER: "csrf-test-token"},
        )
        assert stale.status_code == 409
        assert stale.json()["error"] == "stale_revision"
