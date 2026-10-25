from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.main import create_app
from app.providers.base import (
    Message,
    ProviderError,
    ProviderErrorKind,
    ProviderNotAllowedError,
    ProviderRequest,
    ensure_provider_allowed,
)
from app.providers.doubles import (
    DelayedProvider,
    DeterministicProvider,
    FailingProvider,
    ScriptedProvider,
)
from app.settings import Settings
from app.timing.clock import (
    SystemClock,
    VirtualClock,
    VirtualClockForbiddenError,
    ensure_clock_allowed,
)

LIVE_DSN = "postgresql://u:real-password@127.0.0.1:1/investigators_db"


def _request(text: str = "What does my file say about Crate 3?") -> ProviderRequest:
    return ProviderRequest(
        purpose="assistant",
        prompt_id="assistant.v0",
        messages=(Message("system", "grounded"), Message("user", text)),
        params={"temperature": 0},
    )


def test_virtual_clock_is_manual_and_monotonic() -> None:
    clock = VirtualClock(datetime(2026, 9, 28, 12, 0, tzinfo=UTC))
    clock.advance(1.5)
    assert clock.now() == datetime(2026, 9, 28, 12, 0, 1, 500000, tzinfo=UTC)
    assert clock.monotonic() == 1.5
    with pytest.raises(ValueError, match="backwards"):
        clock.advance(-1)
    with pytest.raises(ValueError, match="timezone-aware"):
        VirtualClock(datetime(2026, 1, 1))


def test_system_clock_is_utc() -> None:
    assert SystemClock().now().tzinfo is UTC
    assert SystemClock.is_virtual is False


def test_virtual_clock_forbidden_in_live_mode(make_settings: Callable[..., Settings]) -> None:
    ensure_clock_allowed("qa", VirtualClock())
    with pytest.raises(VirtualClockForbiddenError):
        ensure_clock_allowed("live", VirtualClock())
    with pytest.raises(VirtualClockForbiddenError):
        create_app(
            make_settings(APP_MODE="live", DATABASE_URL=LIVE_DSN),
            clock=VirtualClock(),
            configure_logs=False,
        )


def test_deterministic_provider_is_stable_and_input_sensitive() -> None:
    a, b = DeterministicProvider(), DeterministicProvider()
    first = a.generate(_request(), timeout_s=5)
    assert first.text == b.generate(_request(), timeout_s=5).text
    assert first.text != a.generate(_request("Crate 4?"), timeout_s=5).text
    assert first.provider_id == "double:deterministic"
    assert first.params == {"temperature": 0}
    assert len(a.calls) == 2


def test_scripted_provider_order_errors_and_exhaustion() -> None:
    provider = ScriptedProvider(["one", ProviderError(ProviderErrorKind.INVALID_RESPONSE), "two"])
    assert provider.generate(_request(), timeout_s=1).text == "one"
    with pytest.raises(ProviderError) as info:
        provider.generate(_request(), timeout_s=1)
    assert info.value.kind is ProviderErrorKind.INVALID_RESPONSE
    assert provider.generate(_request(), timeout_s=1).text == "two"
    with pytest.raises(ProviderError, match="exhausted"):
        provider.generate(_request(), timeout_s=1)


def test_failing_provider() -> None:
    with pytest.raises(ProviderError) as info:
        FailingProvider(ProviderErrorKind.TIMEOUT).generate(_request(), timeout_s=1)
    assert info.value.kind is ProviderErrorKind.TIMEOUT


def test_delayed_provider_uses_virtual_time_and_bounds_calls() -> None:
    clock = VirtualClock()
    fast = DelayedProvider(DeterministicProvider(), clock, latency_s=2.5)
    response = fast.generate(_request(), timeout_s=10)
    assert response.latency_ms == 2500
    assert clock.monotonic() == 2.5
    slow = DelayedProvider(DeterministicProvider(), clock, latency_s=lambda _r: 30.0)
    with pytest.raises(ProviderError) as info:
        slow.generate(_request(), timeout_s=8)
    assert info.value.kind is ProviderErrorKind.TIMEOUT
    assert clock.monotonic() == 10.5  # advanced only to the deadline


def test_doubles_are_refused_in_live_mode() -> None:
    ensure_provider_allowed("preview", DeterministicProvider())
    with pytest.raises(ProviderNotAllowedError):
        ensure_provider_allowed("live", DeterministicProvider())


def test_doubles_have_no_network_imports() -> None:
    source = (Path(__file__).parents[1] / "app" / "providers" / "doubles.py").read_text()
    for forbidden in ("import socket", "httpx", "requests", "urllib", "aiohttp"):
        assert forbidden not in source
