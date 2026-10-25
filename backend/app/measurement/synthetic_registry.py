"""Synthetic-run registry (INV-0212; hidden-bench HB-08 ``_count_done`` lesson).

A synthetic case is identified by ``(compatibility_key, seed, replicate)``. Each execution of a
case is an *attempt* with its own lineage (attempt number, parent attempt, status). Planning
work for ``N`` replicates returns exactly the cases that have no succeeded attempt, by identity
- never "run N minus the number of finished rows" - so a failed low-numbered replicate is
retried as that same replicate and a completed replicate is never rerun. An attempt left
``started`` by a crash is resumed as the same case with a new attempt linked to it.

The store is an append-only JSON-lines log (durable with fsync, safe to replay) behind a small
protocol; a PostgreSQL implementation can follow with WP03 without changing callers. Used by
INV-0509, INV-0706, INV-1008 and INV-1507.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class AttemptStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, order=True)
class CaseKey:
    compatibility_key: str
    seed: str
    replicate: int

    def __post_init__(self) -> None:
        if not self.compatibility_key.startswith("ck1:"):
            raise ValueError("cases are keyed by a full compatibility key (ck1:...)")
        if self.replicate < 1:
            raise ValueError("replicates are numbered from 1")


@dataclass(frozen=True)
class AttemptEvent:
    case: CaseKey
    attempt: int
    status: AttemptStatus
    parent_attempt: int | None = None
    detail: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "compatibility_key": self.case.compatibility_key,
                "seed": self.case.seed,
                "replicate": self.case.replicate,
                "attempt": self.attempt,
                "status": self.status.value,
                "parent_attempt": self.parent_attempt,
                "detail": self.detail,
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, line: str) -> AttemptEvent:
        data = json.loads(line)
        return cls(
            case=CaseKey(data["compatibility_key"], data["seed"], int(data["replicate"])),
            attempt=int(data["attempt"]),
            status=AttemptStatus(data["status"]),
            parent_attempt=data["parent_attempt"],
            detail=data.get("detail", ""),
        )


class EventStore(Protocol):
    def append(self, event: AttemptEvent) -> None: ...

    def events(self) -> Iterator[AttemptEvent]: ...


class MemoryEventStore:
    def __init__(self) -> None:
        self._events: list[AttemptEvent] = []

    def append(self, event: AttemptEvent) -> None:
        self._events.append(event)

    def events(self) -> Iterator[AttemptEvent]:
        return iter(list(self._events))


class JsonlEventStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, event: AttemptEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.to_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def events(self) -> Iterator[AttemptEvent]:
        if not self.path.is_file():
            return iter(())
        with self.path.open(encoding="utf-8") as handle:
            lines = [line for line in handle if line.strip()]
        return (AttemptEvent.from_json(line) for line in lines)


@dataclass(frozen=True)
class CaseState:
    case: CaseKey
    attempts: tuple[AttemptEvent, ...]

    @property
    def done(self) -> bool:
        return any(a.status is AttemptStatus.SUCCEEDED for a in self.attempts)

    @property
    def last_attempt(self) -> int:
        return max((a.attempt for a in self.attempts), default=0)

    @property
    def open_attempt(self) -> int | None:
        """An attempt that started but never finished (e.g. the process died)."""
        finished = {a.attempt for a in self.attempts if a.status is not AttemptStatus.STARTED}
        started = [a.attempt for a in self.attempts if a.status is AttemptStatus.STARTED]
        open_ = [n for n in started if n not in finished]
        return max(open_) if open_ else None


class RegistryError(RuntimeError):
    pass


class SyntheticRunRegistry:
    def __init__(self, store: EventStore) -> None:
        self.store = store

    def _states(self) -> dict[CaseKey, CaseState]:
        grouped: dict[CaseKey, list[AttemptEvent]] = {}
        for event in self.store.events():
            grouped.setdefault(event.case, []).append(event)
        return {case: CaseState(case, tuple(events)) for case, events in grouped.items()}

    def state(self, case: CaseKey) -> CaseState:
        return self._states().get(case, CaseState(case, ()))

    def pending(
        self, compatibility_key: str, seeds: Iterable[str], replicates: int
    ) -> list[CaseKey]:
        """Exactly the requested cases without a succeeded attempt, in stable order."""
        states = self._states()
        wanted = [
            CaseKey(compatibility_key, seed, r) for seed in seeds for r in range(1, replicates + 1)
        ]
        return [c for c in wanted if not (c in states and states[c].done)]

    def start(self, case: CaseKey) -> int:
        state = self.state(case)
        if state.done:
            raise RegistryError(f"{case} already succeeded; completed cases are never rerun")
        attempt = state.last_attempt + 1
        parent = state.open_attempt or (state.last_attempt or None)
        self.store.append(AttemptEvent(case, attempt, AttemptStatus.STARTED, parent))
        return attempt

    def finish(self, case: CaseKey, attempt: int, *, ok: bool, detail: str = "") -> None:
        state = self.state(case)
        if attempt not in {a.attempt for a in state.attempts}:
            raise RegistryError(f"{case} has no attempt {attempt}")
        if state.done:
            raise RegistryError(f"{case} already succeeded")
        if any(
            a.attempt == attempt and a.status is not AttemptStatus.STARTED for a in state.attempts
        ):
            raise RegistryError(f"{case} attempt {attempt} already finished")
        status = AttemptStatus.SUCCEEDED if ok else AttemptStatus.FAILED
        self.store.append(AttemptEvent(case, attempt, status, None, detail))

    def completed(self, compatibility_key: str) -> list[CaseKey]:
        return sorted(
            c
            for c, s in self._states().items()
            if c.compatibility_key == compatibility_key and s.done
        )
