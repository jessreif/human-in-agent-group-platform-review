"""Provider contract shared by the future university-local adapter and the test doubles.

The contract is deliberately small in WP01; WP10a extends it with snapshot-grounded context,
validation and fallback records (spec §7.4-7.6). Every call is bounded by ``timeout_s`` and
results carry the provider/model identity actually used (spec §4.2 "store actual per-call
parameters and provider/model identity").
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol

Purpose = Literal["assistant", "social", "ai_qa"]
Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class ProviderRequest:
    purpose: Purpose
    prompt_id: str
    messages: tuple[Message, ...]
    params: Mapping[str, float | int | str] = field(default_factory=dict)
    request_id: str = ""


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    provider_id: str
    model_id: str
    latency_ms: int
    finish_reason: str
    params: Mapping[str, float | int | str]


class ProviderErrorKind(StrEnum):
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"


class ProviderError(RuntimeError):
    def __init__(
        self, kind: ProviderErrorKind, message: str = "", *, dispatched: bool = False
    ) -> None:
        super().__init__(message or kind.value)
        self.kind = kind
        # BA-71: whether a request actually went to the model before the failure —
        # a semaphore/budget rejection is a different operational signal than a
        # dispatched call that timed out.
        self.dispatched = dispatched


class TextProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def is_double(self) -> bool: ...

    def generate(self, request: ProviderRequest, *, timeout_s: float) -> ProviderResponse: ...


class ProviderNotAllowedError(RuntimeError):
    pass


def ensure_provider_allowed(app_mode: str, provider: TextProvider) -> None:
    """Provider gate for the session-serving stack (the worker).

    BA-35: ``APP_MODE`` is not a participant-data boundary — preview/QA sessions can
    carry a human tester's free text. Remote adapters (``is_remote``) may therefore
    never serve session traffic in *any* mode; they exist solely for offline,
    nonparticipant fixture comparisons constructed directly outside the worker.
    Doubles likewise never serve live sessions. The D05-approved path is the pinned
    loopback adapter; an authorized university AI service fallback would be a new adapter,
    gated by its institutional endpoint."""
    if getattr(provider, "is_remote", False):
        raise ProviderNotAllowedError(
            "remote providers can never serve session traffic in any mode"
        )
    if provider.is_double and app_mode == "live":
        raise ProviderNotAllowedError("provider doubles can never serve live sessions")
