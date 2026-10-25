"""Remote (off-host) model providers for OFFLINE fixture comparison only (spec §7.4 boundary).

These adapters exist so the lab can compare speed/reliability against the pinned local
model on generated, nonparticipant fixtures. They are deliberately bounded:

- **Never session-serving (BA-35).** ``is_remote = True``; ``ensure_provider_allowed``
  refuses them in every mode and ``run_generation`` refuses to send a session-grounded
  prompt to them. APP_MODE is not a participant-data boundary — preview/QA sessions can
  carry a human tester's free text, so no deployment mode may route session prompts
  off-host. These adapters may only be constructed and called directly by offline
  tooling, never through the worker's provider selection.
- **HTTPS + auth header only.** Endpoints must be ``https://``; keys travel in headers
  (``Authorization: Bearer`` / ``x-api-key``), never in URLs, and are registered with the
  log redactor at settings load. Errors carry only the request digest, never text.
- **Same audit contract.** Every call lands in ``model_call`` with ``provider_id``
  (``openai_chat`` / ``minimax_anthropic``) and the served model id, so off-host traffic
  is fully attributable.
- **No remote weight pinning.** Unlike the local adapter there is no digest to pin —
  ``model_id`` records the configured model name verbatim and ``model_pin`` requests must
  match it exactly, same as local.

Two providers:

- :class:`RemoteOpenAIProvider` — OpenAI-compatible ``POST {base}/chat/completions``.
- :class:`RemoteMiniMaxProvider` — Anthropic-messages-compatible ``POST {base}/v1/messages``
  (MiniMax's ``/anthropic`` endpoint speaks that shape; the system message moves to the
  top-level ``system`` field and ``max_tokens`` is required).
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.providers.base import (
    ProviderError,
    ProviderErrorKind,
    ProviderRequest,
    ProviderResponse,
)
from app.providers.local import MODEL_PIN_PARAM, request_digest

if TYPE_CHECKING:
    from app.settings import Settings

# (url, body, headers, timeout_s) -> (http_status, response_bytes). Injectable for tests.
Transport = Callable[[str, bytes, dict[str, str], float], tuple[int, bytes]]

_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_MAX_TOKENS = 400
_RESERVED = frozenset({"model", "messages", "stream", "system", "max_tokens", MODEL_PIN_PARAM})


class RemoteProviderConfigError(ValueError):
    """The configured remote endpoint/credential is malformed or missing."""


def _check_remote_base_url(base_url: str) -> str:
    """Normalize an https remote base URL or raise. No credentials, query, or fragment."""
    try:
        parsed = urllib.parse.urlsplit(base_url.strip())
    except ValueError:
        raise RemoteProviderConfigError("remote endpoint URL is malformed") from None
    if parsed.scheme != "https":
        raise RemoteProviderConfigError("remote endpoint must use https")
    if not parsed.hostname:
        raise RemoteProviderConfigError("remote endpoint has no host")
    if parsed.username is not None or parsed.password is not None:
        raise RemoteProviderConfigError("remote endpoint must not embed credentials")
    if parsed.query or parsed.fragment:
        raise RemoteProviderConfigError("remote endpoint must not carry a query or fragment")
    return f"https://{parsed.netloc}{parsed.path.rstrip('/')}"


def _urllib_transport(
    url: str, body: bytes, headers: dict[str, str], timeout_s: float
) -> tuple[int, bytes]:
    request = urllib.request.Request(  # noqa: S310
        url,
        data=body,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as resp:  # noqa: S310
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()


class _RemoteBase:
    """Shared machinery: pin check, bounded concurrency, timed POST, error mapping."""

    is_double = False
    is_remote = True
    supports_reasoning_effort = False
    _provider_id = "remote"

    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        api_key: str,
        max_concurrency: int,
        transport: Transport | None = None,
    ) -> None:
        if not model.strip():
            raise RemoteProviderConfigError("a model id is required for remote providers")
        if not api_key.strip():
            raise RemoteProviderConfigError("an api key is required for remote providers")
        self._base_url = _check_remote_base_url(base_url)
        self._model = model.strip()
        self._api_key = api_key
        self._semaphore = threading.BoundedSemaphore(max(1, max_concurrency))
        self._transport = transport or _urllib_transport

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def model_id(self) -> str:
        return self._model

    def _err(self, kind: ProviderErrorKind, detail: str, request: ProviderRequest) -> ProviderError:
        return ProviderError(kind, f"{detail} req={request_digest(request)[:16]}")

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _endpoint(self) -> str:
        raise NotImplementedError

    def _body(self, request: ProviderRequest) -> dict[str, Any]:
        raise NotImplementedError

    def _parse(self, raw: bytes, request: ProviderRequest) -> tuple[str, str]:
        raise NotImplementedError

    def generate(self, request: ProviderRequest, *, timeout_s: float) -> ProviderResponse:
        pin = request.params.get(MODEL_PIN_PARAM)
        if pin is not None and pin != self._model:
            raise self._err(ProviderErrorKind.INVALID_RESPONSE, "model pin mismatch", request)
        deadline = time.monotonic() + max(timeout_s, 0.0)
        if not self._semaphore.acquire(timeout=max(timeout_s, 0.001)):
            raise self._err(ProviderErrorKind.TIMEOUT, "concurrency limit reached", request)
        try:
            started = time.monotonic()
            body = json.dumps(self._body(request)).encode("utf-8")
            try:
                status, raw = self._transport(
                    self._endpoint(),
                    body,
                    self._headers(),
                    max(deadline - time.monotonic(), 0.001),
                )
            except TimeoutError:
                raise self._err(ProviderErrorKind.TIMEOUT, "call timed out", request) from None
            except urllib.error.URLError as exc:
                reason = getattr(exc, "reason", None)
                if isinstance(reason, (TimeoutError, socket.timeout)):
                    raise self._err(ProviderErrorKind.TIMEOUT, "call timed out", request) from None
                raise self._err(
                    ProviderErrorKind.UNAVAILABLE, "endpoint unreachable", request
                ) from None
            except OSError:
                raise self._err(
                    ProviderErrorKind.UNAVAILABLE, "endpoint unreachable", request
                ) from None
            latency_ms = round((time.monotonic() - started) * 1000)
            if status != 200:
                raise self._err(
                    ProviderErrorKind.UNAVAILABLE, f"endpoint returned http {status}", request
                )
            text, finish = self._parse(raw, request)
            return ProviderResponse(
                text=text,
                provider_id=self.provider_id,
                model_id=self._model,
                latency_ms=latency_ms,
                finish_reason=finish,
                params=dict(request.params),
            )
        finally:
            self._semaphore.release()

    def _served_ok(self, served: Any) -> bool:
        """Whether the echoed ``model`` name is compatible with the configured id.
        Remote APIs commonly echo a resolved snapshot name (``gpt-4.1-mini`` ->
        ``gpt-4.1-mini-2025-07-14``), so an exact match or a ``<id>-<suffix>`` form
        is accepted; anything else is treated as model drift."""
        if served is None:
            return True
        name = str(served)
        return name == self._model or name.startswith(self._model + "-")


class RemoteOpenAIProvider(_RemoteBase):
    """OpenAI-compatible chat-completions endpoint (``OPENAI_BASE_URL``/``OPENAI_API_KEY``)."""

    _provider_id = "openai_chat"

    def _endpoint(self) -> str:
        return f"{self._base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _body(self, request: ProviderRequest) -> dict[str, Any]:
        return {
            "model": self._model,
            "stream": False,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            **{k: v for k, v in request.params.items() if k not in _RESERVED},
        }

    def _parse(self, raw: bytes, request: ProviderRequest) -> tuple[str, str]:
        try:
            data: Any = json.loads(raw)
            served = data.get("model")
            if not self._served_ok(served):
                raise self._err(ProviderErrorKind.INVALID_RESPONSE, "served model differs", request)
            choice = data["choices"][0]
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("content not a string")
            finish = choice.get("finish_reason") or "stop"
            return content, str(finish)
        except ProviderError:
            raise
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError):
            raise self._err(
                ProviderErrorKind.INVALID_RESPONSE, "malformed completion body", request
            ) from None


class RemoteMiniMaxProvider(_RemoteBase):
    """Anthropic-messages-compatible endpoint (``MINIMAX_BASE_URL``/``MINIMAX_API_KEY``).

    The system prompt moves from the message list into the top-level ``system`` field and
    ``max_tokens`` is required; ``stop_reason`` maps onto ``finish_reason``.
    """

    _provider_id = "minimax_anthropic"

    def _endpoint(self) -> str:
        return f"{self._base_url}/v1/messages"

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._api_key, "anthropic-version": _ANTHROPIC_VERSION}

    def _body(self, request: ProviderRequest) -> dict[str, Any]:
        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        turns = [
            {"role": m.role, "content": m.content} for m in request.messages if m.role != "system"
        ]
        params = {k: v for k, v in request.params.items() if k not in _RESERVED}
        return {
            "model": self._model,
            "system": system,
            "messages": turns,
            "max_tokens": int(params.pop("max_tokens", _DEFAULT_MAX_TOKENS)),
            **params,
        }

    def _parse(self, raw: bytes, request: ProviderRequest) -> tuple[str, str]:
        try:
            data: Any = json.loads(raw)
            served = data.get("model")
            if not self._served_ok(served):
                raise self._err(ProviderErrorKind.INVALID_RESPONSE, "served model differs", request)
            blocks = data["content"]
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            finish = data.get("stop_reason") or "stop"
            return text, str(finish)
        except ProviderError:
            raise
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError):
            raise self._err(
                ProviderErrorKind.INVALID_RESPONSE, "malformed messages body", request
            ) from None


def remote_from_settings(settings: Settings) -> _RemoteBase | None:
    """Dispatch on ``MODEL_PROVIDER``: ``openai``/``minimax`` build their adapter,
    anything else returns ``None`` (auto/ollama/none are resolved by the caller)."""
    selected = settings.MODEL_PROVIDER
    if selected == "openai":
        return RemoteOpenAIProvider(
            settings.OPENAI_BASE_URL,
            model=settings.OPENAI_MODEL_ID,
            api_key=settings.OPENAI_API_KEY.get_secret_value(),
            max_concurrency=settings.LOCAL_MODEL_MAX_CONCURRENCY,
            transport=None,
        )
    if selected == "minimax":
        return RemoteMiniMaxProvider(
            settings.MINIMAX_BASE_URL,
            model=settings.MINIMAX_MODEL_ID,
            api_key=settings.MINIMAX_API_KEY.get_secret_value(),
            max_concurrency=settings.LOCAL_MODEL_MAX_CONCURRENCY,
            transport=None,
        )
    return None
