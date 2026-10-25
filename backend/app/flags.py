"""Deployment feature flags as one typed, immutable value (audit H4/C2).

Before 2026-10-17 each flag travelled as its own ``bool`` keyword through dozens
of signatures, and several command handlers took them *positionally*, so two
swapped booleans type-checked. Build one ``FeatureFlags`` from ``Settings`` at the
request/worker boundary and pass it down whole.

Defaults mirror the historical per-function defaults exactly (everything on
except the optional AI Q&A stage), so a caller that passes no flags behaves as it
always did. ``as_dict`` keeps the ``<name>_enabled`` keys that ``session_opened``
payloads and export stamps have always recorded.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any


@dataclass(frozen=True, slots=True, kw_only=True)
class FeatureFlags:
    scripted_teammates: bool = True
    ai_assistant: bool = True
    crate_overlap: bool = True
    crate_number: bool = True
    time_pressure: bool = True
    ai_qa: bool = False
    bonus_payment: bool = True

    def as_dict(self) -> dict[str, bool]:
        """The recorded posture: ``{"<flag>_enabled": bool}`` in a stable order."""
        return {f"{k}_enabled": v for k, v in asdict(self).items()}

    def with_(self, **changes: Any) -> FeatureFlags:
        """A copy with some flags changed — tests and previews use this."""
        return replace(self, **changes)


DEFAULT_FLAGS = FeatureFlags()
