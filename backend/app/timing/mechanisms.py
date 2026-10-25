"""Per-class ``timing.mechanism`` schedules (INV-0211, spec §6.3, §6.5).

All times are active-task milliseconds on one axis; the check-in pause is handled by the
active-time ledger (WP06), not here. Each function turns a resolved class policy, the
persisted sampled components and the observed generation readiness into an explicit plan:

- ``concurrent`` (v2): ``scheduled = grant + platform + think + delay + typing`` and
  ``eligible = max(scheduled, ready)``. Generation overlaps the imposed wait: 3 s generation
  with a 9 s wait is eligible at 9 s, 12 s generation at 12 s.
- ``demo_structured_delay_length`` (reference): ``grant + delay + min(cap, chars*per_char)``;
  the body is selected before timing, so there is no readiness term.
- ``demo_social_additive`` (reference, S2/S3 thread replies): the wait starts *after* the model
  answers (or times out into the fallback): ``ready + delay + typing`` — model latency adds.
- ``demo_assistant_floor`` (reference): display floor measured from the ask, not added to model
  latency: ``max(grant + floor, ready)``.

For generative classes a missing/late/failed generation resolves at ``grant + timeout`` into
the class fallback; the plan records that as ``fallback_reason`` (never silently).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.domain.manifests.schema import GENERATIVE_CLASSES, Typing

if TYPE_CHECKING:
    from app.timing.policy import ResolvedClassPolicy


@dataclass(frozen=True)
class SampledComponents:
    """Persisted draws for one job (never resampled on retry; spec §6.2)."""

    delay_ms: int
    platform_ms: int = 0
    think_ms: int = 0
    floor_ms: int = 0


@dataclass(frozen=True)
class Generation:
    """Observed generation outcome, on the active axis. ``ready_ms=None`` means none arrived."""

    ready_ms: int | None
    failed: bool = False
    body_chars: int = 0


@dataclass(frozen=True)
class SchedulePlan:
    mechanism: str
    grant_ms: int
    typing_ms: int
    scheduled_ms: int
    effective_ready_ms: int | None
    eligible_ms: int
    readiness_overrun_ms: int
    fallback_reason: Literal["timeout", "generation_failed"] | None


class MechanismError(ValueError):
    pass


def typing_ms(typing: Typing | None, chars: int) -> int:
    if typing is None:
        return 0
    return min(typing.cap_ms, max(0, chars) * typing.per_char_ms)


def _effective_ready(
    policy: ResolvedClassPolicy, grant_ms: int, generation: Generation | None
) -> tuple[int | None, Literal["timeout", "generation_failed"] | None]:
    if policy.response_class not in GENERATIVE_CLASSES:
        if generation is not None and generation.ready_ms is not None:
            raise MechanismError(f"{policy.response_class} has no generation step")
        return None, None
    if policy.timeout_ms is None:
        raise MechanismError(f"{policy.response_class} needs a timeout")
    deadline = grant_ms + policy.timeout_ms
    if generation is None or generation.ready_ms is None:
        return deadline, "timeout"
    if generation.failed:
        return min(generation.ready_ms, deadline), "generation_failed"
    if generation.ready_ms > deadline:
        return deadline, "timeout"
    return generation.ready_ms, None


def plan_schedule(
    policy: ResolvedClassPolicy,
    grant_ms: int,
    components: SampledComponents,
    generation: Generation | None = None,
    *,
    body_chars: int | None = None,
) -> SchedulePlan:
    """Compute the declared schedule. ``body_chars`` is the final (validated or fallback) body
    length; for generative classes it defaults to ``generation.body_chars``."""
    if grant_ms < 0:
        raise MechanismError("grant must be on the active axis (>= 0)")
    chars = body_chars
    if chars is None:
        chars = generation.body_chars if generation is not None else 0
    t_ms = typing_ms(policy.typing, chars)
    ready, reason = _effective_ready(policy, grant_ms, generation)
    mechanism = policy.mechanism
    if mechanism == "concurrent":
        scheduled = (
            grant_ms + components.platform_ms + components.think_ms + components.delay_ms + t_ms
        )
        eligible = scheduled if ready is None else max(scheduled, ready)
    elif mechanism == "demo_structured_delay_length":
        scheduled = grant_ms + components.delay_ms + t_ms
        eligible = scheduled
    elif mechanism == "demo_social_additive":
        if ready is None:
            raise MechanismError("demo_social_additive applies to generative classes")
        scheduled = ready + components.delay_ms + t_ms
        eligible = scheduled
    elif mechanism == "demo_assistant_floor":
        if ready is None:
            raise MechanismError("demo_assistant_floor applies to generative classes")
        scheduled = grant_ms + components.floor_ms
        eligible = max(scheduled, ready)
    else:  # pragma: no cover - the schema admits no other value
        raise MechanismError(f"unsupported mechanism {mechanism}")
    overrun = max(0, eligible - scheduled)
    return SchedulePlan(mechanism, grant_ms, t_ms, scheduled, ready, eligible, overrun, reason)
