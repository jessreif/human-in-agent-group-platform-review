"""The approved local-model adapter (INV-1001; spec §7.4 "Assistant grounding and provider
boundary").

Transport choice (documented per the WP10a task): the adapter speaks the
**OpenAI-compatible chat-completions** API — ``POST {base_url}/v1/chat/completions`` with
``{"model": ..., "messages": [...], "stream": false, **params}`` — because the
:class:`~app.providers.base.ProviderRequest` contract is already chat-shaped and every
candidate local server (Ollama's OpenAI endpoint, llama.cpp's ``llama-server``, vLLM)
implements it. The demo relay is never contacted; there is no fallback to any external
host inside the adapter.

Safety properties enforced here:

- **Loopback only.** ``base_url`` must be ``http://`` with a literal loopback host
  (``localhost``, ``127.0.0.0/8`` or ``::1``); anything else — external hosts, non-http
  schemes, userinfo, query strings — is rejected at construction, so configuration can
  never silently point inference off-host.
- **Bounded concurrency.** A ``threading.BoundedSemaphore`` caps in-flight calls; a call
  that cannot enter within its own timeout fails as ``timeout`` (it then takes the
  deterministic fallback — the participant's clock is never extended).
- **Per-call timeout.** The call's ``timeout_s`` bounds both the semaphore wait and the
  HTTP read.
- **Model/digest pinning.** The adapter pins ``model`` + ``model_digest`` at construction
  and reports ``model_id = "<model>@sha256:<digest>"`` verbatim on every response. A
  request may carry ``params["model_pin"]`` (set by ``scheduler.run_generation``); when
  present it must equal this instance's pinned ``model_id`` — a session generation job can
  never silently drift to a different model or digest.
- **Served-identity verification (BA-40).** A weight digest is **mandatory** — an
  adapter without a pinned ``model_digest`` cannot be constructed, so verification
  can never be silently bypassed by an empty setting. The adapter asks the server
  what it is actually serving — ``GET {base}/api/tags`` (Ollama) — before every
  generation call, and refuses to generate when the served manifest digest differs
  from the pin. ``/api/tags`` ``digest`` is the served *manifest* digest: the
  manifest content-addresses the weight blobs, so a same-tag/different-weights
  replacement cannot forge it. The verified served digest is recorded per call as
  ``served_digest`` on the response params (landing in ``model_call.parameters``),
  and startup verification in ``provider_from_settings`` fails closed on a confirmed
  mismatch.
- **Redacted diagnostics.** Errors name only the request's SHA-256 digest — prompt and
  message text never appear in exceptions or logs.
"""

from __future__ import annotations

import errno
import ipaddress
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.domain.manifests.canonical import canonical_bytes, sha256_hex
from app.providers.base import (
    ProviderError,
    ProviderErrorKind,
    ProviderRequest,
    ProviderResponse,
)

if TYPE_CHECKING:
    from app.settings import Settings

PROVIDER_ID = "local_openai_chat"
MODEL_PIN_PARAM = "model_pin"
# Params we manage ourselves and never forward into the request body.
_RESERVED_PARAMS = frozenset({"model", "messages", "stream", MODEL_PIN_PARAM})
_LOOPBACK_NAMES = frozenset({"localhost", "localhost."})

# (url, body, timeout_s) -> (http_status, response_bytes). Injectable for tests; the
# production transport is a plain urllib POST (stdlib only — no new dependency).
Transport = Callable[[str, bytes, float], tuple[int, bytes]]
# (url, timeout_s) -> (http_status, response_bytes) — GET transport for the Ollama
# model listing used by served-identity verification (BA-40).
GetTransport = Callable[[str, float], tuple[int, bytes]]


class ProviderConfigError(ValueError):
    """The configured endpoint/model is not an approved local endpoint."""


# Errnos where the request provably never reached the server (BA-71): an
# explicit refusal, an unreachable network/host, or a name-resolution failure
# mean nothing was dispatched — the audit ``dispatched`` flag must say so.
_NEVER_DISPATCHED_ERRNOS = frozenset({errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH})


def _reached_wire(exc: BaseException) -> bool:
    """Whether the request plausibly reached the provider (BA-71). A refusal or
    name/network failure means nothing left; resets, timeouts and HTTP-level
    failures stay ``True`` — the request may already have gone out."""
    reason = getattr(exc, "reason", exc)  # URLError wraps the real cause
    return not (
        isinstance(reason, (ConnectionRefusedError, socket.gaierror))
        or (isinstance(reason, OSError) and reason.errno in _NEVER_DISPATCHED_ERRNOS)
    )


def request_digest(request: ProviderRequest) -> str:
    """A log-safe request fingerprint: message *hashes* and meta, never message text."""
    payload = {
        "purpose": request.purpose,
        "prompt_id": request.prompt_id,
        "request_id": request.request_id,
        "messages": [
            {"role": m.role, "sha256": sha256_hex(m.content.encode("utf-8"))}
            for m in request.messages
        ],
        "params": dict(sorted(request.params.items())),
    }
    return sha256_hex(canonical_bytes(payload))


def _check_base_url(base_url: str) -> str:
    """Normalize a loopback-only http base URL or raise :class:`ProviderConfigError`."""
    try:
        parsed = urllib.parse.urlsplit(base_url.strip())
    except ValueError:
        raise ProviderConfigError("model endpoint URL is malformed") from None
    if parsed.scheme != "http":
        raise ProviderConfigError("model endpoint must use plain http on loopback")
    host = (parsed.hostname or "").lower()
    try:
        ip = ipaddress.ip_address(host)
        loopback = ip.is_loopback
    except ValueError:
        # A hostname, not a literal IP: only "localhost" is trusted to resolve to
        # loopback; any other name could resolve off-host.
        loopback = host in _LOOPBACK_NAMES
    if not loopback:
        raise ProviderConfigError("model endpoint must be a loopback host")
    if parsed.username is not None or parsed.password is not None:
        raise ProviderConfigError("model endpoint must not embed credentials")
    if parsed.query or parsed.fragment:
        raise ProviderConfigError("model endpoint must not carry a query or fragment")
    port = parsed.port or 80
    # Rebuild the base URL from the checked parts only.
    shown_host = f"[{host}]" if ":" in host else host
    return f"http://{shown_host}:{port}"


# Bound on any response body this adapter will materialize — a pathological or
# misbehaving endpoint must not exhaust worker memory (BA-48).
_RESPONSE_MAX_BYTES = 4 * 1024 * 1024
# Per-chunk read size for the deadline-aware body reader.
_READ_CHUNK = 64 * 1024


def _sock_for(resp: Any) -> Any:
    """The raw socket under an ``urllib`` response, for per-chunk read timeouts.

    ``urlopen`` wraps ``http.client.HTTPResponse`` in ``addinfourl`` (and HTTPError
    responses look the same): ``.fp`` is the HTTPResponse, whose ``.fp`` is a
    buffered reader over ``socket.SocketIO`` — ``raw._sock`` is the socket."""
    http_resp = getattr(resp, "fp", resp)
    buffered = getattr(http_resp, "fp", http_resp)
    return getattr(getattr(buffered, "raw", None), "_sock", None)


def _read_bounded(resp: Any, deadline: float) -> bytes:
    """Read the body under ONE whole-call deadline and the size bound (BA-48).

    A single ``read()`` inherits only the per-operation socket timeout — a body
    trickled one byte at a time under that interval would occupy the call far
    past its budget. Instead: ``read1`` returns after at most one socket recv,
    the socket timeout is shrunk to the remaining budget before each chunk, the
    deadline is re-checked between chunks, and expiry raises ``TimeoutError``
    while the caller's ``finally`` closes the response/socket."""
    chunks: list[bytes] = []
    total = 0
    limit = _RESPONSE_MAX_BYTES + 1
    read1 = getattr(resp, "read1", None) or getattr(getattr(resp, "fp", None), "read1", None)
    while total < limit:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("response body read exceeded the call budget")
        sock = _sock_for(resp)
        if sock is not None:
            sock.settimeout(remaining)
        if read1 is not None:
            chunk = read1(min(_READ_CHUNK, limit - total))
        else:  # pragma: no cover - addinfourl/HTTPResponse always expose read1
            chunk = resp.read(min(_READ_CHUNK, limit - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _urllib_transport(url: str, body: bytes, timeout_s: float) -> tuple[int, bytes]:
    # loopback-only http: the URL is checked at construction; S310 guards uncontrolled
    # URLs, which this provably is not. ``timeout_s`` is the WHOLE remaining call
    # budget — connection, request write, and the trickle-bounded body read share
    # one deadline (BA-48).
    deadline = time.monotonic() + timeout_s
    request = urllib.request.Request(  # noqa: S310
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        resp = urllib.request.urlopen(request, timeout=timeout_s)  # noqa: S310
    except urllib.error.HTTPError as exc:
        try:
            return int(exc.code), _read_bounded(exc, deadline)
        finally:
            exc.close()
    try:
        return int(resp.status), _read_bounded(resp, deadline)
    finally:
        resp.close()


def _urllib_get_transport(url: str, timeout_s: float) -> tuple[int, bytes]:
    # Same loopback-only guarantee: the URL is derived from the checked base URL.
    deadline = time.monotonic() + timeout_s
    request = urllib.request.Request(url, method="GET")  # noqa: S310
    try:
        resp = urllib.request.urlopen(request, timeout=timeout_s)  # noqa: S310
    except urllib.error.HTTPError as exc:
        try:
            return int(exc.code), _read_bounded(exc, deadline)
        finally:
            exc.close()
    try:
        return int(resp.status), _read_bounded(resp, deadline)
    finally:
        resp.close()


class LocalChatProvider:
    """Loopback-only OpenAI-compatible text provider (INV-1001)."""

    is_double = False
    is_remote = False
    # Ollama's OpenAI endpoint accepts reasoning_effort=none to skip thinking-mode
    # hidden tokens (gemma4/qwen3); the scheduler injects it for providers that opt in.
    supports_reasoning_effort = True

    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        model_digest: str = "",
        max_concurrency: int = 4,
        transport: Transport | None = None,
        get_transport: GetTransport | None = None,
    ) -> None:
        if not model.strip():
            raise ProviderConfigError("a pinned model id is required")
        # BA-40: the weight digest is mandatory — an unpinned adapter cannot exist,
        # so a missing LOCAL_MODEL_DIGEST fails closed instead of bypassing the
        # served-identity check.
        if not model_digest.strip():
            raise ProviderConfigError(
                "a pinned model digest is required (LOCAL_MODEL_DIGEST); session "
                "generation without served-weight verification is not allowed"
            )
        if not (model_digest.startswith("sha256:") or len(model_digest) == 64):
            raise ProviderConfigError(
                "model_digest must be a sha256 hex digest (optionally 'sha256:'-prefixed)"
            )
        self._base_url = _check_base_url(base_url)
        self._endpoint = f"{self._base_url}/v1/chat/completions"
        self._tags_endpoint = f"{self._base_url}/api/tags"
        self._model = model.strip()
        self._digest = model_digest.removeprefix("sha256:")
        self._model_id = f"{self._model}@sha256:{self._digest}"
        self._semaphore = threading.BoundedSemaphore(max(1, max_concurrency))
        # BA-70: calls actually executing inside the transport — the honest
        # provider-load signal for model_call covariates (claimed-but-waiting
        # jobs are a different quantity).
        self._inflight_calls = 0
        self._transport = transport or _urllib_transport
        self._get_transport = get_transport or _urllib_get_transport

    @property
    def provider_id(self) -> str:
        return PROVIDER_ID

    @property
    def model_id(self) -> str:
        """The pinned ``<model>@sha256:<digest>`` identity recorded verbatim per call."""
        return self._model_id

    @property
    def model_digest(self) -> str:
        return self._digest

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def inflight_calls(self) -> int:
        """Calls currently executing inside the provider (BA-70)."""
        return self._inflight_calls

    def _err(
        self,
        kind: ProviderErrorKind,
        detail: str,
        request: ProviderRequest,
        *,
        dispatched: bool = False,
    ) -> ProviderError:
        # Prompt/record text is never included — only the request digest and meta.
        return ProviderError(
            kind, f"{detail} req={request_digest(request)[:16]}", dispatched=dispatched
        )

    def served_model_digest(self, timeout_s: float = 5.0) -> str:
        """The manifest digest the server reports under the pinned model tag.

        ``GET /api/tags`` (Ollama) lists one entry per served tag; its ``digest`` is
        the manifest digest — the manifest content-addresses the weight blobs, so the
        value is the served weight identity a same-tag replacement cannot forge.
        Raises :class:`ProviderError` (``unavailable``/``invalid_response``) when the
        listing cannot be read and :class:`ProviderConfigError` when the pinned tag is
        absent from it."""
        try:
            status, raw = self._get_transport(self._tags_endpoint, timeout_s)
        except (TimeoutError, urllib.error.URLError, OSError):
            raise ProviderError(
                ProviderErrorKind.UNAVAILABLE, "model listing unreachable"
            ) from None
        if status != 200:
            raise ProviderError(
                ProviderErrorKind.UNAVAILABLE, f"model listing returned http {status}"
            )
        try:
            if len(raw) > _RESPONSE_MAX_BYTES:
                raise ValueError("listing size")
            data: Any = json.loads(raw)
            entries = data["models"]
            assert isinstance(entries, list)
        except (ValueError, UnicodeError, RecursionError, KeyError, TypeError, AssertionError):
            raise ProviderError(
                ProviderErrorKind.INVALID_RESPONSE, "malformed model listing"
            ) from None
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if self._model in (entry.get("model"), entry.get("name")):
                digest = str(entry.get("digest") or "").removeprefix("sha256:").lower()
                if not digest:
                    raise ProviderConfigError(f"served tag {self._model!r} reports no digest")
                return digest
        raise ProviderConfigError(f"no served tag matches pinned model {self._model!r}")

    def verify_served_identity(self, timeout_s: float = 5.0) -> str:
        """Fail-closed check (BA-40): the digest actually served under the pinned tag
        must equal the configured ``LOCAL_MODEL_DIGEST``. Returns the verified digest.

        Raises :class:`ProviderConfigError` when the served digest is missing or
        differs from the pin (construction guarantees a pin exists);
        :class:`ProviderError` propagates for a listing that cannot be read (an
        outage is not a provenance mismatch)."""
        served = self.served_model_digest(timeout_s)
        if served != self._digest:
            raise ProviderConfigError("served model digest does not match the pinned digest")
        return served

    def generate(self, request: ProviderRequest, *, timeout_s: float) -> ProviderResponse:
        pin = request.params.get(MODEL_PIN_PARAM)
        if pin is not None and pin != self._model_id:
            raise self._err(ProviderErrorKind.INVALID_RESPONSE, "model pin mismatch", request)
        # The call's whole budget covers every blocking phase (BA-48): identity
        # verification, the queue wait, and the HTTP exchange all draw from the
        # same deadline — each phase recomputes the remainder and a spent budget
        # fails immediately rather than quietly restarting the clock.
        deadline = time.monotonic() + max(timeout_s, 0.0)

        def remaining() -> float:
            return deadline - time.monotonic()

        # BA-40: the pinned digest is a claim about served weights — verify it
        # against the server's own model listing before handing over a grounded
        # prompt, unconditionally. A confirmed mismatch fails closed; an unreadable
        # listing is an outage (same fallback path as an unreachable endpoint).
        try:
            served = self.verify_served_identity(timeout_s=max(remaining(), 0.001))
        except ProviderConfigError:
            raise self._err(
                ProviderErrorKind.INVALID_RESPONSE,
                "served model digest does not match the pin",
                request,
            ) from None
        except ProviderError as exc:
            raise self._err(exc.kind, "served-identity check failed", request) from None
        if remaining() <= 0:
            raise self._err(
                ProviderErrorKind.TIMEOUT, "call budget exhausted before dispatch", request
            )
        if not self._semaphore.acquire(timeout=max(remaining(), 0.001)):
            raise self._err(ProviderErrorKind.TIMEOUT, "concurrency limit reached", request)
        self._inflight_calls += 1
        try:
            started = time.monotonic()
            body = json.dumps(
                {
                    "model": self._model,
                    "stream": False,
                    "messages": [{"role": m.role, "content": m.content} for m in request.messages],
                    **{k: v for k, v in request.params.items() if k not in _RESERVED_PARAMS},
                }
            ).encode("utf-8")
            budget_left = remaining()
            if budget_left <= 0:
                raise self._err(
                    ProviderErrorKind.TIMEOUT,
                    "call budget exhausted before the request",
                    request,
                )
            try:
                status, raw = self._transport(self._endpoint, body, budget_left)
            except TimeoutError:
                raise self._err(
                    ProviderErrorKind.TIMEOUT, "call timed out", request, dispatched=True
                ) from None
            except urllib.error.URLError as exc:
                reason = getattr(exc, "reason", None)
                if isinstance(reason, (TimeoutError, socket.timeout)):
                    raise self._err(
                        ProviderErrorKind.TIMEOUT, "call timed out", request, dispatched=True
                    ) from None
                raise self._err(
                    ProviderErrorKind.UNAVAILABLE,
                    "endpoint unreachable",
                    request,
                    dispatched=_reached_wire(exc),
                ) from None
            except OSError as exc:
                raise self._err(
                    ProviderErrorKind.UNAVAILABLE,
                    "endpoint unreachable",
                    request,
                    dispatched=_reached_wire(exc),
                ) from None
            latency_ms = round((time.monotonic() - started) * 1000)
            # A transport that returns after the deadline — or a body that blows the
            # bound — is a failed call, not a slower-than-expected success (BA-48).
            if time.monotonic() > deadline:
                raise self._err(
                    ProviderErrorKind.TIMEOUT,
                    "response arrived past the call budget",
                    request,
                    dispatched=True,
                )
            if len(raw) > _RESPONSE_MAX_BYTES:
                raise self._err(
                    ProviderErrorKind.INVALID_RESPONSE,
                    "response exceeds the size bound",
                    request,
                    dispatched=True,
                )
            if status != 200:
                raise self._err(
                    ProviderErrorKind.UNAVAILABLE,
                    f"endpoint returned http {status}",
                    request,
                    dispatched=True,
                )
            text, finish = self._parse(raw, request)
            out_params = dict(request.params)
            # BA-40: the verified served identity, recorded verbatim on the call.
            out_params["served_digest"] = f"sha256:{served}"
            return ProviderResponse(
                text=text,
                provider_id=self.provider_id,
                model_id=self._model_id,
                latency_ms=latency_ms,
                finish_reason=finish,
                params=out_params,
            )
        finally:
            self._inflight_calls -= 1
            self._semaphore.release()

    def _parse(self, raw: bytes, request: ProviderRequest) -> tuple[str, str]:
        """Extract (text, finish_reason); anything else is ``invalid_response``."""
        try:
            data: Any = json.loads(raw)
            served = data.get("model")
            if served is not None and served != self._model:
                raise self._err(
                    ProviderErrorKind.INVALID_RESPONSE,
                    "served model differs",
                    request,
                    dispatched=True,
                )
            choice = data["choices"][0]
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("content not a string")
            if "\x00" in content:
                raise ValueError("invalid storage character")
            content.encode("utf-8", errors="strict")
            finish = choice.get("finish_reason") or "stop"
            if finish != "stop":
                raise ValueError("incomplete completion")
            return content, str(finish)
        except ProviderError:
            raise
        except (
            ValueError,
            UnicodeError,
            RecursionError,
            KeyError,
            IndexError,
            TypeError,
            AttributeError,
        ):
            raise self._err(
                ProviderErrorKind.INVALID_RESPONSE,
                "malformed completion body",
                request,
                dispatched=True,
            ) from None


def provider_from_settings(settings: Settings) -> LocalChatProvider | None:
    """Build the pinned local provider from settings; ``None`` keeps the deterministic-only
    default (``provider=None`` in the worker loop), which is also the provider-outage
    behavior under D14.

    BA-40: the served identity is verified at startup — a missing
    ``LOCAL_MODEL_DIGEST`` fails closed at construction, a confirmed
    same-tag/different-digest mismatch fails closed here; a listing that cannot
    be read yet is an outage, not a mismatch — the adapter still builds and every
    ``generate`` re-verifies the pin before use."""
    base = settings.LOCAL_MODEL_BASE_URL
    if base is None:
        return None
    provider = LocalChatProvider(
        base,
        model=settings.LOCAL_MODEL_ID,
        model_digest=settings.LOCAL_MODEL_DIGEST,
        max_concurrency=settings.LOCAL_MODEL_MAX_CONCURRENCY,
    )
    try:
        provider.verify_served_identity()
    except ProviderError as exc:
        if exc.kind is not ProviderErrorKind.UNAVAILABLE:
            raise
        # Unreachable listing = outage path; per-call checks still guard.
    return provider
