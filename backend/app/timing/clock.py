"""Clock abstraction (INV-0103 "test-only virtual clock access").

Production code asks a :class:`Clock` for time; it never calls ``datetime.now``/``time.time``
directly. :class:`SystemClock` is the only clock accepted in ``live`` mode. :class:`VirtualClock`
exists for deterministic tests: it can be injected through ``create_app(clock=...)`` or the
worker entry point, never through an HTTP endpoint or environment variable.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    @property
    def is_virtual(self) -> bool: ...

    def now(self) -> datetime:
        """Current UTC wall time (timezone-aware)."""
        ...

    def monotonic(self) -> float:
        """Monotonic seconds for measuring intervals."""
        ...


class SystemClock:
    is_virtual = False

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()


class VirtualClock:
    """Deterministic, manually advanced clock for tests. Thread-safe."""

    is_virtual = True

    def __init__(self, start: datetime | None = None) -> None:
        start = start or datetime(2026, 1, 1, tzinfo=UTC)
        if start.tzinfo is None:
            raise ValueError("VirtualClock start must be timezone-aware")
        self._now = start.astimezone(UTC)
        self._mono = 0.0
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def monotonic(self) -> float:
        with self._lock:
            return self._mono

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("a virtual clock cannot move backwards")
        with self._lock:
            self._now += timedelta(seconds=seconds)
            self._mono += seconds


class VirtualClockForbiddenError(RuntimeError):
    pass


def ensure_clock_allowed(app_mode: str, clock: Clock) -> None:
    if clock.is_virtual and app_mode == "live":
        raise VirtualClockForbiddenError("a virtual clock can never be used in live mode")
