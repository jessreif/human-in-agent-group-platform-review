"""Deterministic provider doubles (INV-0104).

None of these doubles touches the network. Simulated latency advances a :class:`VirtualClock`
instead of sleeping, so tests can exercise timeout and ordering behavior deterministically.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import asdict
from threading import Lock

from app.providers.base import (
    ProviderError,
    ProviderErrorKind,
    ProviderRequest,
    ProviderResponse,
    TextProvider,
)
from app.timing.clock import VirtualClock

DOUBLE_PREFIX = "double:"


def request_fingerprint(request: ProviderRequest) -> str:
    payload = {
        "purpose": request.purpose,
        "prompt_id": request.prompt_id,
        "messages": [asdict(m) for m in request.messages],
        "params": dict(sorted(request.params.items())),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _Double:
    is_double = True
    model_id = "none"

    def __init__(self, name: str) -> None:
        self._provider_id = DOUBLE_PREFIX + name
        self.calls: list[ProviderRequest] = []
        self._lock = Lock()

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def _record(self, request: ProviderRequest) -> None:
        with self._lock:
            self.calls.append(request)

    def _response(
        self, request: ProviderRequest, text: str, latency_ms: int = 0
    ) -> ProviderResponse:
        return ProviderResponse(
            text=text,
            provider_id=self.provider_id,
            model_id=self.model_id,
            latency_ms=latency_ms,
            finish_reason="stop",
            params=dict(request.params),
        )


class DeterministicProvider(_Double):
    """Same request + seed -> same text, always."""

    def __init__(self, seed: str = "investigators-wp01") -> None:
        super().__init__("deterministic")
        self._seed = seed

    def generate(self, request: ProviderRequest, *, timeout_s: float) -> ProviderResponse:  # noqa: ARG002 - Provider protocol signature
        self._record(request)
        digest = hashlib.sha256((self._seed + request_fingerprint(request)).encode()).hexdigest()
        return self._response(request, f"[{request.purpose} double {digest[:12]}]")


class ScriptedProvider(_Double):
    """Returns queued texts (or raises queued errors) in order; exhausting the script fails."""

    def __init__(self, script: Iterable[str | ProviderError]) -> None:
        super().__init__("scripted")
        self._script = list(script)

    def generate(self, request: ProviderRequest, *, timeout_s: float) -> ProviderResponse:  # noqa: ARG002 - Provider protocol signature
        self._record(request)
        with self._lock:
            if not self._script:
                raise ProviderError(ProviderErrorKind.UNAVAILABLE, "scripted provider exhausted")
            item = self._script.pop(0)
        if isinstance(item, ProviderError):
            raise item
        return self._response(request, item)


class FailingProvider(_Double):
    def __init__(self, kind: ProviderErrorKind = ProviderErrorKind.UNAVAILABLE) -> None:
        super().__init__(f"failing-{kind.value}")
        self._kind = kind

    def generate(self, request: ProviderRequest, *, timeout_s: float) -> ProviderResponse:  # noqa: ARG002 - Provider protocol signature
        self._record(request)
        raise ProviderError(self._kind)


class DelayedProvider(_Double):
    """Wraps another double and simulates latency on a virtual clock.

    ``latency_s`` may be a constant or a function of the request. If the simulated latency
    exceeds ``timeout_s`` the clock advances only to the deadline and a timeout is raised,
    mirroring a bounded real call (spec §7.4 "bounded calls").
    """

    def __init__(
        self,
        inner: TextProvider,
        clock: VirtualClock,
        latency_s: float | Callable[[ProviderRequest], float],
    ) -> None:
        super().__init__(f"delayed({inner.provider_id.removeprefix(DOUBLE_PREFIX)})")
        self._inner = inner
        self._clock = clock
        self._latency = latency_s

    def generate(self, request: ProviderRequest, *, timeout_s: float) -> ProviderResponse:
        self._record(request)
        latency = self._latency(request) if callable(self._latency) else self._latency
        if latency > timeout_s:
            self._clock.advance(timeout_s)
            raise ProviderError(ProviderErrorKind.TIMEOUT, f"exceeded {timeout_s}s")
        self._clock.advance(latency)
        inner = self._inner.generate(request, timeout_s=timeout_s - latency)
        return self._response(request, inner.text, latency_ms=round(latency * 1000))
