"""The single executable policy resolver (INV-0205, spec §4.2, Timing F4).

``resolve_policy(bound, arm)`` is the only way runtime components obtain source/class/seat
timing, length/typing, cues, availability (channel phases), caps, failure handling and
permissions. Nothing downstream reads manifest fields directly, so what the manifest says and
what the scheduler/generators execute cannot drift apart (plan §7, first risk row).

The result splits into:
- ``behavior``: everything that must be identical across the two arms of a profile;
- ``manipulation``: the arm-specific surface (pressure values or evaluation copy).
Hashes of both feed the compatibility key (INV-0208) and the paired-arm diff.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.domain.manifests.artifacts import (
    CheckinCopyArtifact,
    ExposureCopyArtifact,
    PressureRemarksArtifact,
    RequestScriptArtifact,
    WarningArtifact,
)
from app.domain.manifests.binding import BoundManifest
from app.domain.manifests.canonical import content_hash
from app.domain.manifests.schema import (
    NO_PRESSURE_ARM,
    SEATS,
    TEAMMATE_CLASSES,
    ClassPolicy,
    Distribution,
    Typing,
)
from app.timing.warnings import WarningPlan, warning_plan

ASSISTANT_SOURCE = "assistant"


class PolicyError(ValueError):
    pass


def time_pressure_for_arm(arm: object, *, enabled: bool) -> bool:
    """A neutral consent allocation stays neutral across deployment flag changes."""
    return enabled and arm != NO_PRESSURE_ARM


def _dist(d: Distribution | None) -> Any:
    return None if d is None else d.model_dump(mode="json")


@dataclass(frozen=True)
class ResolvedClassPolicy:
    response_class: str
    source: str
    mechanism: str
    delay: tuple[Distribution, ...]
    typing: Typing | None
    platform_delay: Distribution | None
    think: Distribution | None
    floor: Distribution | None
    timeout_ms: int | None
    fallback: str
    rendering: str
    presence_cue: str
    clock_basis: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "mechanism": self.mechanism,
            "delay": [_dist(d) for d in self.delay],
            "typing": None if self.typing is None else self.typing.model_dump(mode="json"),
            "platform_delay": _dist(self.platform_delay),
            "think": _dist(self.think),
            "floor": _dist(self.floor),
            "timeout_ms": self.timeout_ms,
            "fallback": self.fallback,
            "rendering": self.rendering,
            "presence_cue": self.presence_cue,
            "clock_basis": self.clock_basis,
        }


@dataclass(frozen=True)
class ResolvedPhase:
    name: str
    from_ms: int
    until_ms: int
    enabled: frozenset[str]


@dataclass(frozen=True)
class ResolvedPolicy:
    manifest_id: str
    study_key: str
    profile_kind: str
    arm: str
    budget_ms: int
    clock_basis: str
    clock_speed_factor: int
    analytic_eligible: bool
    checkin: Mapping[str, Any] | None
    phases: tuple[ResolvedPhase, ...]
    classes: Mapping[tuple[str, str], ResolvedClassPolicy]
    scripted_requests: Mapping[str, Any]
    warnings: tuple[WarningPlan, ...]
    controls: Mapping[str, Any]
    teammates: Mapping[str, Any]
    manipulation: Mapping[str, Any]
    _behavior_cache: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    # ----- queries used by runtime components ---------------------------------------------------
    def class_policy(self, response_class: str, source: str) -> ResolvedClassPolicy:
        try:
            return self.classes[(response_class, source)]
        except KeyError:
            raise PolicyError(
                f"{self.manifest_id}: response class {response_class!r} is not supported "
                f"for source {source!r}"
            ) from None

    def channels_open(self, active_ms: float) -> frozenset[str]:
        for phase in self.phases:
            if phase.from_ms <= active_ms < phase.until_ms:
                return phase.enabled
        return frozenset()  # at/after the budget nothing is open

    def interaction_ms(self, active_ms: int) -> int:
        """Active time elapsed while help channels were open (``solo_review`` excluded)."""
        closed = sum(
            max(0, min(active_ms, p.until_ms) - p.from_ms)
            for p in self.phases
            if p.name == "solo_review" and p.from_ms < active_ms
        )
        return active_ms - closed

    # ----- hashing ------------------------------------------------------------------------------
    def behavior(self) -> dict[str, Any]:
        if not self._behavior_cache:
            self._behavior_cache.update(
                {
                    "manifest_id": self.manifest_id,
                    "study_key": self.study_key,
                    "clock": {
                        "budget_ms": self.budget_ms,
                        "clock_basis": self.clock_basis,
                        "clock_speed_factor": self.clock_speed_factor,
                        "checkin": None if self.checkin is None else dict(self.checkin),
                    },
                    "channels": [
                        [p.name, p.from_ms, p.until_ms, sorted(p.enabled)] for p in self.phases
                    ],
                    "classes": {
                        f"{cls}/{src}": policy.as_dict()
                        for (cls, src), policy in sorted(self.classes.items())
                    },
                    "scripted_requests": dict(self.scripted_requests),
                    "warnings": [w.as_dict() for w in self.warnings],
                    "controls": dict(self.controls),
                    "teammates": dict(self.teammates),
                }
            )
        return dict(self._behavior_cache)

    @property
    def behavior_policy_hash(self) -> str:
        return content_hash(self.behavior())

    @property
    def manipulation_hash(self) -> str:
        return content_hash(dict(self.manipulation))

    def as_dict(self) -> dict[str, Any]:
        return {"behavior": self.behavior(), "manipulation": {"arm": dict(self.manipulation)}}


def _resolve_class(name: str, policy: ClassPolicy, source: str, clock: str) -> ResolvedClassPolicy:
    delay = policy.delay
    for seat, override in policy.seat_overrides.items():
        if seat == source:
            delay = override.delay
    return ResolvedClassPolicy(
        response_class=name,
        source=source,
        mechanism=policy.mechanism,
        delay=tuple(delay),
        typing=policy.typing,
        platform_delay=policy.platform_delay,
        think=policy.think,
        floor=policy.floor,
        timeout_ms=policy.timeout_ms,
        fallback=policy.fallback,
        rendering=policy.rendering,
        presence_cue=policy.presence_cue,
        clock_basis=clock,
    )


def resolve_policy(bound: BoundManifest, arm: str) -> ResolvedPolicy:
    m = bound.manifest
    neutral = arm == NO_PRESSURE_ARM
    if neutral and m.study_key != "investigators_time_pressure":
        raise PolicyError(f"{m.manifest_id} cannot allocate {arm!r}")
    policy_arm = m.arms[0] if neutral else arm
    if policy_arm not in m.arms:
        raise PolicyError(f"{m.manifest_id} has no arm {arm!r}; allowed {m.arms}")
    timing = m.timing
    budget_ms = timing.active_budget_s * 1000

    classes: dict[tuple[str, str], ResolvedClassPolicy] = {}
    for name, policy in timing.response_classes.items():
        sources = SEATS if name in TEAMMATE_CLASSES else (ASSISTANT_SOURCE,)
        for source in sources:
            classes[(name, source)] = _resolve_class(name, policy, source, timing.clock_basis)

    warnings = []
    for i, _ref in enumerate(timing.warnings):
        stored = bound.artifact(f"timing.warnings[{i}]")
        assert isinstance(stored.artifact, WarningArtifact)
        warnings.append(warning_plan(stored.artifact, stored.sha256, budget_ms))

    script = bound.artifact("timing.scripted_requests.script")
    assert isinstance(script.artifact, RequestScriptArtifact)
    sr = timing.scripted_requests
    scripted = {
        "times_ms": [t * 1000 for t in sr.times_active_s],
        "targets": None if sr.targets is None else list(sr.targets),
        "seat_plan": (
            None
            if sr.seat_plan is None
            else [{"seat": p.seat, "pick": p.pick} for p in sr.seat_plan]
        ),
        "seat_policy": sr.seat_policy,
        "seat_multiset": None if sr.seat_multiset is None else list(sr.seat_multiset),
        "script": [script.key, script.sha256],
        "clock_basis": timing.clock_basis,
    }

    checkin = None
    if timing.checkin is not None and not neutral:
        c = timing.checkin
        checkin = {
            "at_active_ms": c.at_active_s * 1000,
            "collect_wall_ms": c.collect_wall_ms,
            "inactivity_cap_wall_ms": c.inactivity_cap_wall_s * 1000,
            "inactivity_outcome": c.inactivity_outcome,
            "own_rating_skippable": c.own_rating_skippable,
            "resume": c.resume,
        }

    phases = tuple(
        ResolvedPhase(p.name, p.from_active_s * 1000, p.until_active_s * 1000, frozenset(p.enabled))
        for p in m.channels.phases
    )

    controls: dict[str, Any] = {
        "ordering": timing.ordering,
        "deadline_structure": timing.deadline_structure,
        "early_submission": timing.early_submission,
        "trajectory": timing.trajectory,
        "delivery_failure_injection": timing.delivery_failure_injection,
        "visibility": timing.visibility,
        "delay_attribution": timing.delay_attribution,
        "contingent_responsiveness": timing.contingent_responsiveness,
        "availability_windows": timing.availability_windows,
        "proactive_sharing": timing.proactive_sharing,
        "two_stage": timing.two_stage.model_dump(mode="json"),
        "concurrency": timing.concurrency.model_dump(mode="json"),
        "rate_limits": timing.rate_limits.model_dump(mode="json"),
        "countdown_visible": timing.countdown_visible,
        # UX round 3 (FR-1): arm-invariant interaction-mode feature flag.
        "interaction_mode": m.interaction_mode,
    }

    tm = m.teammates

    def ref_hash(slot: str) -> list[str] | None:
        stored = bound.artifacts.get(slot)
        return None if stored is None else [stored.key, stored.sha256]

    teammates = {
        "social_generation": tm.social_generation,
        "assistant_referral": tm.assistant_referral,
        "unsolicited_requests": tm.unsolicited_requests,
        "acknowledgments": None
        if tm.acknowledgments is None
        else tm.acknowledgments.model_dump(mode="json"),
        "crosstalk": None if tm.crosstalk is None else tm.crosstalk.model_dump(mode="json"),
        "voice_bank": ref_hash("teammates.voice_bank"),
        "fallback_bank": ref_hash("teammates.fallback_bank"),
        "social_prompt": ref_hash("teammates.social_prompt"),
        "assistant": {
            "provider": m.assistant.provider,
            "source_access": m.assistant.source_access,
            "prompt": ref_hash("assistant.prompt"),
            "validator_version": m.assistant.validator_version,
            "fallback": m.assistant.fallback,
            "model_change_within_session": m.assistant.model_change_within_session,
        },
    }

    # Only genuinely arm-specific surfaces go into ``manipulation``; study-level manipulation
    # settings shared by both arms (check-in copy, seat policy, exposure surfaces) are behavior,
    # so the paired-arm diff can demand that nothing else differs.
    arm_manipulation = m.arm_spec(policy_arm).manipulation
    manipulation: dict[str, Any] = {
        "arm": arm,
        "kind": "none" if neutral else arm_manipulation.kind,
    }
    study = m.manipulation
    if neutral:
        warnings = []
    elif study.kind == "pressure_checkin":
        assert arm_manipulation.kind == "pressure_values"
        manipulation["teammate_values"] = list(arm_manipulation.teammate_values)
        copy = bound.artifact("manipulation.copy_ref")
        assert isinstance(copy.artifact, CheckinCopyArtifact)
        controls["pressure_checkin"] = {
            "copy": [copy.key, copy.sha256],
            "higher_seat_policy": study.higher_seat_policy,
            "rng_stream": study.rng_stream,
        }
        remarks = bound.artifacts.get("manipulation.remarks")
        if remarks is not None:
            assert isinstance(remarks.artifact, PressureRemarksArtifact)
            rb = remarks.artifact.body
            # Shared schedule is behavior; only the arm's own wording is manipulation, so the
            # paired-arm diff still confines every difference to ``manipulation.arm``.
            controls["pressure_remarks"] = {
                "artifact": [remarks.key, remarks.sha256],
                "seat_order": rb.seat_order,
                "channel": rb.channel,
                "schedule": [
                    [r.at_active_s * 1000, r.speaker]
                    if r.speaker_level is None
                    else [r.at_active_s * 1000, r.speaker, r.speaker_level]
                    for r in rb.remarks
                ],
            }
            if policy_arm == "mixed":
                # Per-seat levels differ under ``mixed``: carry each remark's
                # {high, low} pair and let ``_pressure_remark`` pick by the
                # speaker seat's recorded check-in level at delivery time.
                if any(r.mixed_lines is None for r in rb.remarks):
                    raise ValueError(
                        f"{m.manifest_id} arm 'mixed' requires mixed_lines on "
                        f"every remark of {remarks.key}"
                    )
                manipulation["remark_lines"] = [
                    dict(r.mixed_lines) for r in rb.remarks if r.mixed_lines is not None
                ]
            else:
                manipulation["remark_lines"] = [r.lines[policy_arm] for r in rb.remarks]
    else:
        assert arm_manipulation.kind == "evaluation_exposure"
        exposure = bound.artifact(f"condition_set.{arm}.manipulation.exposure_copy")
        assert isinstance(exposure.artifact, ExposureCopyArtifact)
        manipulation["exposure_copy"] = [exposure.key, exposure.sha256]
        controls["evaluation_surfaces"] = list(study.surfaces)

    return ResolvedPolicy(
        manifest_id=m.manifest_id,
        study_key=m.study_key,
        profile_kind=m.profile_kind,
        arm=arm,
        budget_ms=budget_ms,
        clock_basis=timing.clock_basis,
        clock_speed_factor=bound.clock_speed_factor,
        analytic_eligible=bound.analytic_eligible,
        checkin=checkin,
        phases=phases,
        classes=classes,
        scripted_requests=scripted,
        warnings=tuple(warnings),
        controls=controls,
        teammates=teammates,
        manipulation=manipulation,
    )
