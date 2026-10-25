"""Delay sampling with explicit location and truncation semantics (INV-0211; INV-0609 extends
the validation of realized distributions).

- ``location: median`` sets exp(mu) = location_s; ``location: mean`` sets the *untruncated*
  mean, mu = ln(location_s) - sigma^2 / 2. Truncation can move the realized mean either way;
  that is reported, never hidden (spec §6.5).
- ``clamp`` pins an out-of-range draw to the bound (the demos' ``delay(lo, hi)``);
  ``reject`` redraws up to ``max_attempts`` and then applies ``on_exhaustion``.
- Draws come from :class:`~app.domain.rng.RandomStreams` keys, so each attempt is reproducible
  and independent of every other job's draws.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from app.domain.manifests.schema import Distribution, Fixed, Lognormal

UniformSource = Callable[[str], float]
"""``source(label) -> U[0,1)``, bound to one job/seat/component key by the caller."""


@dataclass(frozen=True)
class Draw:
    seconds: float
    raw_seconds: float
    attempts: int
    truncated: bool
    method: str

    @property
    def ms(self) -> int:
        return round(self.seconds * 1000)


def lognormal_mu(dist: Lognormal) -> float:
    if dist.location == "median":
        return math.log(dist.location_s)
    return math.log(dist.location_s) - dist.sigma**2 / 2


def _standard_normal(u1: float, u2: float) -> float:
    # Box-Muller; 1 - u maps [0, 1) onto (0, 1] so log() is finite.
    return math.sqrt(-2.0 * math.log(1.0 - u1)) * math.cos(2.0 * math.pi * u2)


def sample(dist: Distribution, source: UniformSource) -> Draw:
    if isinstance(dist, Fixed):
        return Draw(dist.seconds, dist.seconds, 0, False, "fixed")
    mu = lognormal_mu(dist)
    lo, hi = dist.truncation.lo_s, dist.truncation.hi_s
    if dist.truncation.method == "clamp":
        raw = math.exp(mu + dist.sigma * _standard_normal(source("u1"), source("u2")))
        value = min(hi, max(lo, raw))
        return Draw(value, raw, 1, value != raw, "clamp")
    attempts = dist.truncation.max_attempts or 1
    raw = 0.0
    for attempt in range(1, attempts + 1):
        z = _standard_normal(source(f"a{attempt}.u1"), source(f"a{attempt}.u2"))
        raw = math.exp(mu + dist.sigma * z)
        if lo <= raw <= hi:
            return Draw(raw, raw, attempt, False, "reject")
    # on_exhaustion == "clamp" is the only supported exhaustion rule
    return Draw(min(hi, max(lo, raw)), raw, attempts, True, "reject_exhausted_clamp")
