"""Lifetime of ``GET /events/stream`` without a database or a 55 s wait.

The clock and sleeper are the same seam production uses when ``request.state``
does not override them. This module never opens that wait.
"""

from __future__ import annotations

import anyio

from app.api.participant import (
    STREAM_KEEPALIVE_S,
    STREAM_MAX_S,
    STREAM_POLL_S,
    _event_frames,
)


def test_stream_constants_unchanged() -> None:
    assert STREAM_MAX_S == 55
    assert STREAM_POLL_S == 1
    assert STREAM_KEEPALIVE_S == 15


def test_deadline_yields_stream_end() -> None:
    """Injected clock reaches the default deadline without sleeping in real time."""
    now = {"t": 0.0}
    sleeps: list[float] = []
    head_at: list[float] = []

    def clock() -> float:
        return now["t"]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now["t"] += seconds

    def read_head() -> tuple[int, str]:
        head_at.append(now["t"])
        return (4, "2026-01-01T00:00:00+00:00")

    async def collect() -> list[str]:
        chunks: list[str] = []
        async for chunk in _event_frames(
            fetch_rows=lambda _pos: [],
            read_head=read_head,
            cursor=0,
            clock=clock,
            sleep=sleep,
        ):
            chunks.append(chunk)
        return chunks

    frames = anyio.run(collect)
    assert frames[-1] == "event: stream_end\ndata: {}\n\n"
    assert frames[0].startswith("event: head\n")
    assert all(frame.startswith("event: ") for frame in frames)
    assert sleeps
    assert all(seconds == STREAM_POLL_S for seconds in sleeps)
    assert now["t"] == STREAM_MAX_S
    assert head_at == [0.0, 15.0, 30.0, 45.0]
    assert len(sleeps) == int(STREAM_MAX_S / STREAM_POLL_S)
