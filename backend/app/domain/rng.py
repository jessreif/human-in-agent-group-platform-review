"""Independent, keyed random streams (INV-0207, spec §4.2, §5.1).

Why keyed draws instead of a sequential generator: with a sequential PRNG, one extra chat turn
consumes a number and shifts every later draw, so participant behavior would redraw future
stimuli or the other seat's timing. Here every draw is a pure function of

    (session master seed, stream name, draw key)

computed as HMAC-SHA256. A draw's value never depends on how many other draws happened, in
which order, or in which arm. Streams are separated by name so the world, file order, rating
seat, schedules, allocation and per-job timing never share randomness.

The construction is fully specified here (no dependency on ``random``'s implementation), so a
persisted seed plus draw key reproduces the value on any Python version. Realized draws are
also recorded in a :class:`DrawLog`, persisted with the session (WP03), so replay never depends
on re-running generator code.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, TypeVar, get_args

from app.domain.manifests.canonical import canonical_bytes

Stream = Literal["assignment", "world", "order", "ratings", "schedules", "jobs"]
STREAMS: tuple[Stream, ...] = get_args(Stream)
KeyPart = str | int
_DOMAIN = b"investigators.rng/1"
_T = TypeVar("_T")


class SeedError(ValueError):
    pass


@dataclass(frozen=True)
class DrawRecord:
    stream: Stream
    key: tuple[KeyPart, ...]
    value: float


@dataclass
class DrawLog:
    """Realized draws in the order they were taken (for persistence and audit)."""

    records: list[DrawRecord] = field(default_factory=list)

    def for_stream(self, stream: Stream) -> list[DrawRecord]:
        return [r for r in self.records if r.stream == stream]

    def as_json(self) -> list[dict[str, object]]:
        return [{"stream": r.stream, "key": list(r.key), "value": r.value} for r in self.records]


def new_seed() -> str:
    """A fresh 256-bit master seed as hex (server-side only; never sent to participants)."""
    return secrets.token_hex(32)


class RandomStreams:
    def __init__(self, seed_hex: str, log: DrawLog | None = None) -> None:
        try:
            seed = bytes.fromhex(seed_hex)
        except ValueError:
            raise SeedError("seed must be hex") from None
        if len(seed) < 16:
            raise SeedError("seed must carry at least 128 bits")
        self._stream_keys = {
            name: hmac.new(seed, _DOMAIN + b"|stream|" + name.encode(), hashlib.sha256).digest()
            for name in STREAMS
        }
        self.log = log if log is not None else DrawLog()

    def uniform(self, stream: Stream, *key: KeyPart) -> float:
        """Uniform in [0, 1) with 53 bits of precision."""
        if stream not in self._stream_keys:
            raise SeedError(f"unknown stream {stream!r}")
        if not key:
            raise SeedError("every draw needs a key")
        digest = hmac.new(
            self._stream_keys[stream], canonical_bytes(list(key)), hashlib.sha256
        ).digest()
        value = (int.from_bytes(digest[:8], "big") >> 11) / float(1 << 53)
        self.log.records.append(DrawRecord(stream, tuple(key), value))
        return value

    def coin(self, stream: Stream, *key: KeyPart, p: float = 0.5) -> bool:
        return self.uniform(stream, *key) < p

    def weighted(self, stream: Stream, options: Sequence[tuple[_T, float]], *key: KeyPart) -> _T:
        total = sum(weight for _, weight in options)
        point = self.uniform(stream, *key) * total
        for option, weight in options:
            point -= weight
            if point < 0:
                return option
        return options[-1][0]

    def shuffle(self, stream: Stream, items: Sequence[_T], *key: KeyPart) -> tuple[_T, ...]:
        """Fisher-Yates with one keyed draw per position."""
        out = list(items)
        for i in range(len(out) - 1, 0, -1):
            j = int(self.uniform(stream, *key, "swap", i) * (i + 1))
            out[i], out[j] = out[j], out[i]
        return tuple(out)
