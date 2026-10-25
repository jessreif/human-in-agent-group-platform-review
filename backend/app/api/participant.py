"""Participant-facing API: entry, session snapshot, event feed, commands, completion.

Mutating endpoints run through :func:`app.db.tx.execute_command` (idempotent command
receipts, expected-revision checks, atomic event+outbox writes). Authentication is the
opaque ``inv_session`` bearer cookie issued at entry; every mutating request must also
echo the ``inv_csrf`` cookie in ``X-CSRF-Token``, and commands that mutate the session
must come from the holder of the writer lease named in ``x-writer-id`` — the shared
``inv_writer`` cookie is honored only on the ``resume``/``takeover`` claim paths.
"""

from __future__ import annotations

import contextlib
import json
import math
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, cast
from urllib.parse import urlsplit

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine
from starlette.concurrency import run_in_threadpool

from app.db import schema as s
from app.db.tx import (
    Command,
    CommandConflictError,
    CommandResult,
    DueAdvancer,
    RejectionError,
    SessionNotFoundError,
    TxContext,
    db_now,
    events_after,
    execute_command,
    lock_session,
)
from app.domain.manifests.store import ContentStore
from app.flags import FeatureFlags
from app.services import (
    activetime,
    credentials,
    lifecycle,
    scheduler,
    session_binding,
    workbench,
)
from app.services.entry import EntryError, EntryResult, enter
from app.services.preview import PreviewError
from app.services.projection import (
    project_event,
    session_head,
    session_snapshot,
)
from app.services.telemetry_ingest import (
    TELEMETRY_KINDS as TELEMETRY_KINDS,
)
from app.services.telemetry_ingest import (
    record_delivery_receipt,
    telemetry_items,
)
from app.settings import Settings
from app.timing.policy import PolicyError, resolve_policy

router = APIRouter()

WRITER_COOKIE = "inv_writer"
WRITER_HEADER = "x-writer-id"


def _problem(status: int, kind: str, detail: str, **extra: Any) -> JSONResponse:
    return JSONResponse(
        {"error": kind, "detail": detail, **extra},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _engine(request: Request) -> Engine:
    return cast(Engine, request.app.state.engine)


def _store(request: Request) -> ContentStore:
    return cast(ContentStore, request.app.state.content_store)


def _build_sha(request: Request) -> str:
    return cast(str, request.app.state.build.source_sha256)


def _behavior_sha(request: Request) -> str:
    """Participant-facing-scoped digest — the compatibility-key build component
    (``scripts/source_bundle.py --behavior``). Admin/ops code churn cannot split an
    analytic partition; the full ``build_source_sha256`` still stamps every session row."""
    return cast(str, request.app.state.build.behavior_sha256)


def _model_digest(request: Request) -> str:
    """Configured provider/model pin for the compatibility key (D05).

    Normalized the same way the provider adapter stores it; an unconfigured model
    maps to UNRESOLVED_MODEL so the live gate still fails closed."""
    from app.domain.compat import UNRESOLVED_MODEL

    raw = _settings(request).LOCAL_MODEL_DIGEST.removeprefix("sha256:").strip()
    return f"{_settings(request).LOCAL_MODEL_ID}@sha256:{raw}" if raw else UNRESOLVED_MODEL


def _preview_key(settings: Settings) -> bytes | None:
    raw = settings.PREVIEW_SIGNING_KEY.get_secret_value().strip()
    return raw.encode("utf-8") if raw else None


def _manifest_pins(store: ContentStore) -> dict[str, str]:
    return {mid: stored.sha256 for mid, stored in store.manifests.items()}


# ----- authentication / authorization ---------------------------------------------------------


def _authenticate(
    request: Request,
) -> tuple[uuid.UUID, Mapping[str, Any]] | JSONResponse:
    """Resolve the session cookie to a locked-free session row, or a problem response."""
    token = request.cookies.get(credentials.SESSION_COOKIE)
    if not token:
        return _problem(401, "unauthenticated", "no session credential")
    engine = _engine(request)
    with engine.connect() as conn:
        session_id = credentials.authenticate(conn, token)
        if session_id is None:
            return _problem(401, "unauthenticated", "invalid or revoked credential")
        row = (
            conn.execute(select(s.session).where(s.session.c.session_id == session_id))
            .mappings()
            .first()
        )
        if row is None:
            return _problem(410, "session_gone", "session not found")
        return session_id, dict(row)


def _check_csrf(request: Request) -> JSONResponse | None:
    cookie = request.cookies.get(credentials.CSRF_COOKIE)
    header = request.headers.get(credentials.CSRF_HEADER)
    if not cookie or not header or cookie != header:
        return _problem(403, "bad_csrf", "csrf token mismatch")
    return None


def _writer_id(request: Request, *, allow_cookie: bool = False) -> uuid.UUID | None:
    """The presented writer-lease id.

    ``x-writer-id`` is the explicit per-tab identity (sessionStorage-backed) — the only
    proof ordinary mutations accept. The profile-shared ``inv_writer`` cookie is honored
    only on the lease-claim paths (``allow_cookie=True``: ``resume``/``takeover``
    bootstrap before the tab's storage is populated). After a takeover that cookie names
    the *new* holder in every tab, so a superseded tab that lost its header must never
    write through it (BA-26)."""
    raw = request.headers.get(WRITER_HEADER)
    if raw is None and allow_cookie:
        raw = request.cookies.get(WRITER_COOKIE)
    try:
        return uuid.UUID(raw) if raw else None
    except ValueError:
        return None


def _check_writer(session: Mapping[str, Any], writer: uuid.UUID | None) -> JSONResponse | None:
    """Pre-transaction lease gate (the in-handler check under the row lock stays
    authoritative). ``writer`` must be the identity this request is authorized to
    present — header-derived for ordinary mutations, cookie-or-header only on the
    claim paths (BA-26)."""
    holder = session["writer_id"]
    if holder is None:
        return None  # no lease yet (pre-open)
    if writer != holder:
        return _problem(409, "writer_lease_held", "another writer holds the session")
    return None


def _set_session_cookies(resp: JSONResponse, result: EntryResult) -> None:
    resp.set_cookie(
        credentials.SESSION_COOKIE,
        result.credential_token,
        **credentials.cookie_settings(),
    )
    resp.set_cookie(
        credentials.CSRF_COOKIE,
        credentials.csrf_token(),
        **credentials.csrf_cookie_settings(),
    )
    resp.set_cookie(WRITER_COOKIE, str(result.writer_id), **credentials.csrf_cookie_settings())


# ----- endpoints --------------------------------------------------------------------------------


@router.post("/entry")
def post_entry(request: Request) -> Any:
    """Enrollment: query-string platform params (or a signed ``pt`` preview descriptor)."""
    settings = _settings(request)
    store = _store(request)
    query = {k: v for k, v in request.query_params.items()}
    try:
        result = enter(
            _engine(request),
            store,
            query=query,
            app_mode=settings.APP_MODE.value,
            preview_key=_preview_key(settings),
            current_manifest_sha256=_manifest_pins(store),
            build_source_sha256=_build_sha(request),
            behavior_source_sha256=_behavior_sha(request),
            # BA-29: the live credential is what authorizes a same-batch re-entry —
            # a browser that transiently failed GET /session still holds the cookie,
            # so it must reach enter() or the participant is refused recovery.
            presented_token=request.cookies.get(credentials.SESSION_COOKIE),
            connect_completion=settings.CONNECT_COMPLETION,
            time_pressure_enabled=settings.TIME_PRESSURE_ENABLED,
            feature_flags=settings.feature_flags(),
        )
    except (EntryError, PreviewError) as exc:
        status = getattr(exc, "status", 403)
        reason = getattr(exc, "reason", str(exc))
        return _problem(status, "entry_refused", reason)
    resp = JSONResponse(
        {
            "session_id": str(result.session_id),
            "resumed": result.resumed,
            "stage": result.stage,
        },
        status_code=200,
    )
    _set_session_cookies(resp, result)
    return resp


def _advance_due(store: ContentStore, *, flags: FeatureFlags) -> DueAdvancer:
    """Run due active-time transitions (check-in open, deadline freeze) inside this command's
    session lock — a lagging scheduler can never let an answer slip past the 360 s freeze
    (spec §6.2). ``flags.time_pressure`` is the deployment flag — under 0, pressure jobs
    are suppressed rather than dispatched."""

    def advance(ctx: TxContext) -> None:
        bound = session_binding.bound_for_session(ctx.conn, store, ctx.session)
        scheduler.advance_due(ctx, bound, flags=flags, store=store)

    return advance


def _clock_state(request: Request, session: Mapping[str, Any]) -> dict[str, Any]:
    """Server-derived clock fields for the browser's interpolated countdown (spec §6.1):
    ``server_now`` + ``active_ms`` + ``budget_ms`` are authoritative; the client only
    interpolates and resynchronizes on reconnect."""
    with _engine(request).connect() as conn:
        now = db_now(conn)
        active = activetime.active_ms(conn, session["session_id"], now, default=0)
        running = activetime.is_running(conn, session["session_id"])
        budget_ms = None
        if session["arm"] is not None:
            try:
                bound = session_binding.bound_for_session(conn, _store(request), session)
                budget_ms = resolve_policy(bound, session["arm"]).budget_ms
            except (RejectionError, PolicyError):
                budget_ms = None  # pin drift surfaces elsewhere; the clock fields still hold
    return {
        "server_now": now.isoformat(),
        "active_ms": active,
        "budget_ms": budget_ms,
        "clock_running": running,
    }


@router.get("/session")
def get_session(request: Request) -> Any:
    auth = _authenticate(request)
    if isinstance(auth, JSONResponse):
        return auth
    _sid, session = auth
    with _engine(request).connect() as conn:
        return {
            "session": session_snapshot(conn, session),
            "clock": _clock_state(request, session),
        }


@router.get("/events")
def get_events(request: Request, after_seq: int = 0, limit: int = 500) -> Any:
    auth = _authenticate(request)
    if isinstance(auth, JSONResponse):
        return auth
    session_id, session = auth
    rows = events_after(_engine(request), session_id, after_seq, limit=limit)
    events = [p for p in (project_event(r) for r in rows) if p is not None]
    # BA-17: the poll cursor advances by the last *scanned* event seq, not only the
    # last visible one — an internal-only run must not be rescanned every poll, and
    # the cursor never jumps past events a concurrent commit added after the scan.
    scanned_seq = int(rows[-1]["seq"]) if rows else after_seq
    return {
        "events": events,
        "scanned_seq": scanned_seq,
        "next_event_seq": session["next_event_seq"],
    }


# ----- INV-0805: cursor-based server-sent event stream -----------------------------------------
#
# The stream replays committed participant-visible events after the caller's cursor (the same
# scan ``GET /events`` performs), then polls the ledger on a short interval and emits new
# events with ``id: <seq>`` so browser ``EventSource`` reconnects resume at ``Last-Event-ID``.
# A ``head`` frame carries the committed high-water mark so the client can distinguish
# filtered internal seqs from a genuine gap. The stream is a bounded read: it never claims
# jobs, advances transitions or triggers model work.
STREAM_POLL_S = 1.0
STREAM_KEEPALIVE_S = 15.0
STREAM_MAX_S = 55.0
STREAM_MAX_LIMIT = 2000


def _check_stream_origin(request: Request) -> JSONResponse | None:
    """Event-stream requests must come from the deployment origin (spec §5.1). An absent
    ``Origin`` header means a non-browser tool and is allowed; a mismatched one is refused.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return None
    host = request.headers.get("host") or request.url.hostname or ""
    if urlsplit(origin).netloc != host:
        return _problem(403, "bad_origin", "cross-origin event stream refused")
    return None


# Test seam on ``request.state``. Production never sets these, and they are not
# query parameters, so a participant cannot shorten the stream.
_STREAM_STATE_CLOCK = "events_stream_clock"
_STREAM_STATE_SLEEP = "events_stream_sleep"
_STREAM_STATE_DEADLINE_S = "events_stream_deadline_s"
_STREAM_STATE_STOPPED = "events_stream_stopped"

_Clock = Callable[[], float]
_Sleeper = Callable[[float], None]
_FetchRows = Callable[[int], list[Mapping[str, Any]]]
_ReadHead = Callable[[], tuple[int, str]]


def _stream_timing(request: Request) -> tuple[_Clock, _Sleeper, float]:
    """Clock, sleeper, and deadline. Unset state keeps the production defaults."""
    clock = cast(_Clock | None, getattr(request.state, _STREAM_STATE_CLOCK, None))
    sleeper = cast(_Sleeper | None, getattr(request.state, _STREAM_STATE_SLEEP, None))
    deadline_s = cast(float | None, getattr(request.state, _STREAM_STATE_DEADLINE_S, None))
    return (
        time.monotonic if clock is None else clock,
        time.sleep if sleeper is None else sleeper,
        STREAM_MAX_S if deadline_s is None else float(deadline_s),
    )


async def _event_frames(
    *,
    fetch_rows: _FetchRows,
    read_head: _ReadHead,
    cursor: int,
    clock: _Clock = time.monotonic,
    sleep: _Sleeper = time.sleep,
    deadline_s: float = STREAM_MAX_S,
    poll_s: float = STREAM_POLL_S,
    keepalive_s: float = STREAM_KEEPALIVE_S,
) -> AsyncIterator[str]:
    """Replay, then poll until ``deadline_s`` on ``clock``.

    Database reads and the poll sleeper run in worker threads so the event loop
    can observe a client disconnect. The sleeper is abandoned on cancel: it holds
    no connection. Fetches are not abandoned, so a disconnect mid-query waits
    until that connection scope exits.
    """
    pos = cursor
    deadline = clock() + deadline_s
    next_keepalive = 0.0
    while True:
        for row in await anyio.to_thread.run_sync(fetch_rows, pos):
            pos = max(pos, int(row["seq"]))
            projected = project_event(row)
            if projected is None:
                continue  # filtered internal event; ``id`` is not emitted for it
            # Unnamed ``message`` frames: EventSource.onmessage delivers them; the
            # event ``id`` drives Last-Event-ID resume after reconnect.
            yield f"id: {projected['seq']}\ndata: {json.dumps(projected)}\n\n"
        now = clock()
        if now >= next_keepalive:
            head, server_time = await anyio.to_thread.run_sync(read_head)
            head_payload = {
                "next_event_seq": head,
                "server_time": server_time,
                "cursor": pos,
            }
            yield f"event: head\ndata: {json.dumps(head_payload)}\n\n"
            next_keepalive = now + keepalive_s
        if now >= deadline:
            # Bound the request lifetime; EventSource reconnects and resumes the cursor.
            yield "event: stream_end\ndata: {}\n\n"
            return
        await anyio.to_thread.run_sync(sleep, poll_s, abandon_on_cancel=True)


@router.get("/events/stream")
def get_events_stream(request: Request, after_seq: int = 0, limit: int = 500) -> Any:
    auth_fail = _check_stream_origin(request)
    if auth_fail is not None:
        return auth_fail
    auth = _authenticate(request)
    if isinstance(auth, JSONResponse):
        return auth
    session_id, _session = auth
    engine = _engine(request)

    cursor = max(0, after_seq)
    # EventSource reconnects carry Last-Event-ID with the last delivered seq.
    last_event_id = request.headers.get("last-event-id")
    if last_event_id:
        with contextlib.suppress(ValueError):
            cursor = max(cursor, int(last_event_id))
    page = max(1, min(limit, STREAM_MAX_LIMIT))
    clock, sleeper, deadline_s = _stream_timing(request)
    stopped = cast(list[bool] | None, getattr(request.state, _STREAM_STATE_STOPPED, None))

    def fetch_rows(pos: int) -> list[Mapping[str, Any]]:
        return events_after(engine, session_id, pos, limit=page)

    def read_head() -> tuple[int, str]:
        with engine.connect() as conn:
            return session_head(conn, session_id)

    async def frames() -> AsyncIterator[str]:
        try:
            async for chunk in _event_frames(
                fetch_rows=fetch_rows,
                read_head=read_head,
                cursor=cursor,
                clock=clock,
                sleep=sleeper,
                deadline_s=deadline_s,
            ):
                yield chunk
        finally:
            # Planted by tests. Runs when the generator returns or is closed on disconnect.
            if stopped is not None:
                stopped.append(True)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/workbench", response_model=None)
def get_workbench(request: Request) -> workbench.WorkbenchView | JSONResponse:
    auth = _authenticate(request)
    if isinstance(auth, JSONResponse):
        return auth
    session_id, session = auth
    return workbench.workbench_view(
        _engine(request), _store(request), session_id, session, flags=_settings(request).flags()
    )


# Command-envelope bounds (BA-49): a participant-facing POST must never reach the
# transaction layer with an unvalidated body — predictable client defects map to
# stable 400s, never ``internal_error``.
_COMMAND_BODY_MAX_BYTES = 64 * 1024
_COMMAND_PAYLOAD_MAX_CHARS = 32 * 1024
_REVISION_MAX = 2**31 - 1  # session.revision is int4
_CLIENT_MS_MAX = 2**53 - 1  # beyond float precision a client clock is meaningless


class _BadCommandError(ValueError):
    """Expected client-side defect → stable 400, never an internal error."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


async def _bounded_body(request: Request, limit: int = _COMMAND_BODY_MAX_BYTES) -> bytes:
    """Read the request body with a hard cap — the limit applies DURING the read,
    so an oversized body is rejected without buffering it whole (BA-49)."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            declared = limit + 1
        if declared > limit:
            raise _BadCommandError("command_too_large", "request body exceeds the size limit")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise _BadCommandError("command_too_large", "request body exceeds the size limit")
        chunks.append(chunk)
    return b"".join(chunks)


async def _command_envelope(
    request: Request,
    *,
    default_revision: int,
    default_kind: str | None = None,
) -> tuple[str, uuid.UUID, int, Mapping[str, Any], int | None]:
    """Parse and validate a command envelope before anything touches the DB.

    Returns ``(kind, command_id, expected_revision, payload, client_monotonic_ms)``.
    Raises ``_BadCommandError`` for every predictable client defect: oversized bodies,
    malformed JSON, non-object bodies, missing/unknown-typed fields, invalid UUIDs,
    and out-of-range revisions (BA-49).
    """
    raw = await _bounded_body(request)
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _BadCommandError("malformed_json", "body must be a JSON object") from None
    if not isinstance(body, Mapping):
        raise _BadCommandError("malformed_command", "body must be a JSON object")

    kind_raw = body.get("kind", default_kind)
    if not isinstance(kind_raw, str) or not kind_raw:
        raise _BadCommandError("malformed_command", "kind is required")
    kind = kind_raw

    command_id_raw = body.get("command_id")
    if command_id_raw is None:
        command_id = uuid.uuid4()
    elif isinstance(command_id_raw, str):
        try:
            command_id = uuid.UUID(command_id_raw)
        except ValueError:
            raise _BadCommandError("malformed_command", "command_id must be a UUID") from None
    else:
        raise _BadCommandError("malformed_command", "command_id must be a UUID string")

    rev_raw = body.get("expected_revision", default_revision)
    if rev_raw is None or isinstance(rev_raw, bool) or not isinstance(rev_raw, int):
        raise _BadCommandError("malformed_command", "expected_revision must be an integer")
    if rev_raw < 0 or rev_raw > _REVISION_MAX:
        raise _BadCommandError("malformed_command", "expected_revision out of range")

    payload_raw = body.get("payload")
    if payload_raw is None:
        payload: Mapping[str, Any] = {}
    elif isinstance(payload_raw, Mapping):
        payload = payload_raw
    else:
        raise _BadCommandError("malformed_command", "payload must be an object")
    if len(json.dumps(payload)) > _COMMAND_PAYLOAD_MAX_CHARS:
        raise _BadCommandError("command_too_large", "payload exceeds the size limit")

    client_ms_raw = body.get("client_monotonic_ms")
    client_ms: int | None
    if client_ms_raw is None:
        client_ms = None
    elif isinstance(client_ms_raw, bool):
        raise _BadCommandError(
            "malformed_command", "client_monotonic_ms must be a non-negative number"
        )
    elif isinstance(client_ms_raw, int):
        # Range-check BEFORE any float conversion — an unbounded int would
        # OverflowError inside math.isfinite and surface as a 500 (BA-49).
        if not 0 <= client_ms_raw <= _CLIENT_MS_MAX:
            raise _BadCommandError("malformed_command", "client_monotonic_ms out of range")
        client_ms = client_ms_raw
    elif (
        isinstance(client_ms_raw, float)
        and math.isfinite(client_ms_raw)
        and 0.0 <= client_ms_raw <= _CLIENT_MS_MAX
    ):
        client_ms = int(client_ms_raw)
    else:
        raise _BadCommandError(
            "malformed_command", "client_monotonic_ms must be a non-negative number"
        )

    return kind, command_id, rev_raw, payload, client_ms


@router.post("/commands")
async def post_commands(request: Request) -> Any:
    csrf = _check_csrf(request)
    if csrf is not None:
        return csrf
    auth = await run_in_threadpool(_authenticate, request)
    if isinstance(auth, JSONResponse):
        return auth
    session_id, session = auth
    try:
        kind, command_id, expected_revision, payload, client_ms = await _command_envelope(
            request, default_revision=int(session["revision"])
        )
    except _BadCommandError as exc:
        return _problem(400, exc.reason, exc.detail)

    # Writer-lease gate (BA-26): mutating commands must name the holder in
    # ``x-writer-id`` — the profile-shared cookie is never sufficient. ``resume``/
    # ``takeover`` are the claim paths and may still establish identity from the
    # entry-granted cookie.
    claim = kind in ("resume", "takeover")
    writer = _writer_id(request, allow_cookie=claim)
    if not claim:
        denied = _check_writer(session, writer)
        if denied is not None:
            return denied

    store = _store(request)
    command = Command(
        command_id=command_id,
        session_id=session_id,
        kind=kind,
        expected_revision=expected_revision,
        payload=payload,
        client_monotonic_ms=client_ms,
    )
    try:
        handler = lifecycle.handler_for(
            kind,
            store=store,
            behavior_sha=_behavior_sha(request),
            writer=writer,
            provider_model_digest=_model_digest(request),
            flags=_settings(request).flags(),
        )
        # execute_command does synchronous DB work — run it off the event loop so
        # a slow transaction cannot starve every other coroutine (BA-46).
        result = await run_in_threadpool(
            execute_command,
            _engine(request),
            command,
            handler,
            advance_due=_advance_due(store, flags=_settings(request).flags()),
        )
    except SessionNotFoundError:
        return _problem(410, "session_gone", "session not found")
    except CommandConflictError:
        return _problem(409, "command_conflict", "command_id already used for a different command")
    except RejectionError as exc:
        return _problem(409, exc.reason, "command rejected")

    if kind in ("resume", "takeover") and result.accepted and "writer_id" in result.result:
        resp = JSONResponse(_command_payload(result))
        resp.set_cookie(
            WRITER_COOKIE,
            result.result["writer_id"],
            **credentials.csrf_cookie_settings(),
        )
        return resp
    if not result.accepted:
        return _problem(409, result.reason or "rejected", "command rejected")
    # Renew the holder's lease so ordinary activity keeps the session (best effort —
    # a lost renewal surfaces as writer_lease_held on the next mutating command).
    if writer is not None and not claim:

        def _renew() -> None:
            with _engine(request).begin() as conn:
                credentials.renew_writer(conn, session_id, writer, now=db_now(conn))

        await run_in_threadpool(_renew)
    return _command_payload(result)


def _command_payload(result: CommandResult) -> dict[str, Any]:
    return {
        "command_id": str(result.command_id),
        "accepted": result.accepted,
        "reason": result.reason,
        "committed_revision": result.committed_revision,
        "result": dict(result.result),
        "event_seqs": list(result.event_seqs),
        "replayed": result.replayed,
    }


@router.post("/complete")
async def post_complete(request: Request) -> Any:
    """Reconcile the session to ``completion`` and write the entitlement records."""
    csrf = _check_csrf(request)
    if csrf is not None:
        return csrf
    auth = await run_in_threadpool(_authenticate, request)
    if isinstance(auth, JSONResponse):
        return auth
    session_id, session = auth
    try:
        _kind, command_id, expected_revision, payload, client_ms = await _command_envelope(
            request,
            default_revision=int(session["revision"]),
            default_kind="complete",
        )
    except _BadCommandError as exc:
        return _problem(400, exc.reason, exc.detail)

    # ``/complete`` is an ordinary mutation, not a lease claim: the writer must be
    # named in ``x-writer-id`` — the shared cookie alone never suffices (BA-26).
    writer = _writer_id(request)
    denied = _check_writer(session, writer)
    if denied is not None:
        return denied

    store = _store(request)
    handler = lifecycle.completion_handler(
        store=store,
        writer=writer,
        connect_completion=_settings(request).CONNECT_COMPLETION,
        prolific_code=_settings(request).COMPLETION_CODE or None,
        bonus_payment_enabled=_settings(request).BONUS_PAYMENT_ENABLED,
    )
    command = Command(
        command_id=command_id,
        session_id=session_id,
        kind="complete",
        expected_revision=expected_revision,
        payload=payload,
        client_monotonic_ms=client_ms,
    )
    try:
        result = await run_in_threadpool(
            execute_command,
            _engine(request),
            command,
            handler,
            advance_due=_advance_due(store, flags=_settings(request).flags()),
        )
    except SessionNotFoundError:
        return _problem(410, "session_gone", "session not found")
    except CommandConflictError:
        return _problem(409, "command_conflict", "command_id already used for a different command")
    except RejectionError as exc:
        return _problem(409, exc.reason, "command rejected")
    if not result.accepted:
        return _problem(409, result.reason or "rejected", "completion refused")
    return _command_payload(result)


@router.post("/telemetry")
async def post_telemetry(request: Request) -> Any:
    """Bounded client telemetry (spec §8.2): delivery receipts, visibility, item timing.

    Mutating endpoint — CSRF-gated like every other participant POST (BA-15)."""
    csrf = _check_csrf(request)
    if csrf is not None:
        return csrf
    auth = await run_in_threadpool(_authenticate, request)
    if isinstance(auth, JSONResponse):
        return auth
    session_id, _session = auth
    try:
        raw = await _bounded_body(request)
    except _BadCommandError as exc:
        return _problem(400, "telemetry_too_large", exc.detail)
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _problem(400, "malformed_json", "body must be a JSON object")
    if not isinstance(body, Mapping):
        return _problem(400, "malformed_command", "body must be a JSON object")
    items = telemetry_items(body)
    engine = _engine(request)

    def _persist() -> None:
        with engine.begin() as conn:
            row = lock_session(conn, session_id)
            now = db_now(conn)
            # Persist-time task stamp for the whole batch (2026-10-12): telemetry
            # inserts previously left active_ms NULL, making dwell/scroll events
            # unalignable to task time without post-hoc server_time mapping.
            # client_ms in payload remains the honest per-item occurrence time.
            active = activetime.active_ms(conn, session_id, now)
            next_seq = int(row["next_event_seq"])
            for item in items:
                # One durable event per telemetry item; per-item client_ms — never the
                # envelope's timestamp applied to the whole batch (BA-05).
                conn.execute(
                    insert(s.event).values(
                        session_id=session_id,
                        seq=next_seq,
                        event_id=uuid.uuid4(),
                        stage=row["stage"],
                        actor="participant",
                        type=f"telemetry_{item['kind']}",
                        # Telemetry does not bump session.revision (only
                        # next_event_seq below), so its events carry the revision
                        # this transaction committed — unchanged. A +1 here tells
                        # clients a revision that never exists and wedges their
                        # monotonic expected_revision one ahead (BA-33).
                        revision=row["revision"],
                        server_time=now,
                        active_ms=active,
                        payload={
                            "client_ms": item["client_ms"],
                            "client_epoch": item["client_epoch"],
                            "data": item["data"],
                        },
                    )
                )
                if item["kind"] in ("receive", "render", "render_first_final"):
                    record_delivery_receipt(
                        conn,
                        session_id,
                        kind=str(item["kind"]),
                        data=item["data"],
                        client_ms=item["client_ms"],
                        telemetry_seq=next_seq,
                        now=now,
                    )
                next_seq += 1
            conn.execute(
                update(s.session)
                .where(s.session.c.session_id == session_id)
                .values(next_event_seq=next_seq)
            )

    try:
        # Synchronous batch insert — off the event loop (BA-46).
        await run_in_threadpool(_persist)
    except SessionNotFoundError:
        return _problem(410, "session_gone", "session not found")
    return {"ok": True}


__all__ = ["router"]
