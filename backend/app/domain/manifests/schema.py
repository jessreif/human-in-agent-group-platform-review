"""Experiment manifest contract, schema ``investigators.manifest/1`` (INV-0201/0204/0206).

Design rules (spec §4, §4.2, §6.5-6.6):

- Every model forbids unknown fields and is strict (no string-to-number coercion).
- A field exists only if the engine implements every value it accepts. Baseline controls whose
  alternatives are dormant treatments (trajectory changes, synthetic failure, staggered
  visibility, delay attribution, reciprocity, availability windows, ...) are typed as a single
  ``Literal``: any other value is rejected at bind rather than stored without behavior.
- Cross-field rules that a single field cannot express live in ``Manifest._cross_rules``.
- Descriptive text (``title``, ``notes``) is carried and hashed with the manifest but never
  read by the policy resolver, so it cannot change behavior.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

SCHEMA_VERSION = "investigators.manifest/1"
# Allowed active task budgets. 360 s is every profile published through 2026-10-08;
# 300 s is the shortened task of the 2026-10-09 pilot revision. Field-level bounds use the
# maximum; the exact per-profile budget is enforced by the manifest cross-rules.
ACTIVE_BUDGETS_S: tuple[int, ...] = (300, 360)
ACTIVE_BUDGET_S = max(ACTIVE_BUDGETS_S)
ActiveBudget = Literal[300, 360]

Seat = Literal["JT", "PM"]
SEATS: tuple[Seat, Seat] = ("JT", "PM")
StudyKey = Literal["investigators_time_pressure", "investigators_peer_eval"]
ArmName = Literal["high", "low", "eval", "control", "mixed"]
Mode = Literal["preview", "qa", "live"]
MODE_VALUES: tuple[Mode, ...] = ("preview", "qa", "live")


def parse_mode(value: str) -> Mode:
    """Narrow an untyped mode string (settings, DB rows, request bodies) to
    ``Mode`` at the boundary — raises ``ValueError`` for anything else (audit L2)."""
    for mode in MODE_VALUES:
        if value == mode:
            return mode
    raise ValueError(f"unknown mode {value!r}")


MaterialStatus = Literal["reference", "proposed_unapproved", "approved", "unresolved_placeholder"]
DecisionId = Literal[
    "D01", "D02", "D03", "D04", "D05", "D06", "D07", "D08", "D09", "D10", "D11", "D12", "D13", "D14"
]
Channel = Literal[
    "file_work",
    "answer_drafting",
    "structured_seeking",
    "sharing",
    "social_reply",
    "assistant_prompting",
]
ALL_CHANNELS: tuple[Channel, ...] = (
    "file_work",
    "answer_drafting",
    "structured_seeking",
    "sharing",
    "social_reply",
    "assistant_prompting",
)
ResponseClass = Literal[
    "structured_record",
    "social_greeting",
    "social_reply",
    "assistant",
    "acknowledgment",
    "crosstalk",
]
TEAMMATE_CLASSES: frozenset[str] = frozenset(
    {"structured_record", "social_greeting", "social_reply", "acknowledgment", "crosstalk"}
)
GENERATIVE_CLASSES: frozenset[str] = frozenset({"social_reply", "assistant"})
Mechanism = Literal[
    "demo_structured_delay_length", "demo_social_additive", "demo_assistant_floor", "concurrent"
]
DEMO_MECHANISMS: frozenset[str] = frozenset(
    {"demo_structured_delay_length", "demo_social_additive", "demo_assistant_floor"}
)
# Every arm name a study has ever declared on a manifest. Additive only: earlier
# eras' manifests keep validating because their declared arms remain in the
# vocabulary. ``mixed`` (2026-10-17, v15): the paired teammate-pressure arm where
# one seat is scripted high-workload and the other low, replacing the
# both-teammates-low arm on v15+ manifests (high/low manifests stay loadable).
STUDY_ARM_VOCABULARY: dict[str, tuple[ArmName, ...]] = {
    "investigators_time_pressure": ("high", "low", "mixed"),
    "investigators_peer_eval": ("eval", "control"),
}

# The arm coverage the development coding corpus must contain — the arm set the
# corpus lineage was minted against (v14 era: no ``mixed`` cases exist yet, so
# coverage is subset- not equality-checked and a later corpus version may add
# mixed cases without invalidating this one).
CORPUS_REQUIRED_ARMS: dict[str, tuple[ArmName, ...]] = {
    "investigators_time_pressure": ("high", "low"),
    "investigators_peer_eval": ("eval", "control"),
}

# Allocation sentinel — not a manifest arm. ``session.arm``/``allocation.arm``
# record it when TIME_PRESSURE_ENABLED=0 collapses the time-pressure axis to a
# single neutral cell: the study stops pretending to randomize a manipulation
# the deployment does not deliver. Manifests keep declaring their paired arms
# (paired arms differ only under ``manipulation.arm``), so ``resolve_policy``
# canonicalizes this to the manifest's first arm for every mechanical field.
# Kept out of ``ArmName`` so the generated manifest JSON schema never admits it.
AllocationArm = Literal["high", "low", "eval", "control", "mixed", "nopress"]
NO_PRESSURE_ARM: AllocationArm = "nopress"


def allocation_arms(
    study_key: str,
    declared_arms: Sequence[ArmName],
    *,
    time_pressure_enabled: bool = True,
) -> tuple[AllocationArm, ...]:
    """The arms a deployment actually randomizes into for one bound manifest.
    Allocation follows the manifest's own ``condition_set`` (v15 manifests
    declare high/mixed; earlier TP manifests keep high/low), so an arm can
    never be drawn for a manifest that does not declare it. Under
    TIME_PRESSURE_ENABLED=0 TP allocation draws the single neutral cell
    instead of pretending to split an arm that delivers nothing."""
    if study_key == "investigators_time_pressure" and not time_pressure_enabled:
        return (NO_PRESSURE_ARM,)
    return tuple(declared_arms)


_ARTIFACT_ID = r"^[a-z0-9_]+(\.[a-z0-9_]+)+$"
_MANIFEST_ID = r"^[a-z0-9_]+_v[0-9]+$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ArtifactRef(Strict):
    artifact_id: str = Field(pattern=_ARTIFACT_ID)
    version: int = Field(ge=1)

    @property
    def key(self) -> str:
        return f"{self.artifact_id}@v{self.version}"


class DecisionRef(Strict):
    decision: DecisionId
    status: Literal["unresolved", "resolved"]


# ----- distributions ---------------------------------------------------------------------------


class Truncation(Strict):
    """``clamp`` pins out-of-range draws to the bound; ``reject`` redraws (spec §6.5: these have
    different distributions and are recorded as such)."""

    method: Literal["clamp", "reject"]
    lo_s: float = Field(ge=0)
    hi_s: float = Field(gt=0)
    max_attempts: int | None = Field(default=None, ge=1, le=100)
    on_exhaustion: Literal["clamp"] | None = None

    @model_validator(mode="after")
    def _rules(self) -> Truncation:
        if self.lo_s >= self.hi_s:
            raise ValueError("truncation lo_s must be < hi_s")
        if self.method == "reject" and (self.max_attempts is None or self.on_exhaustion is None):
            raise ValueError("reject truncation needs max_attempts and on_exhaustion")
        if self.method == "clamp" and (self.max_attempts or self.on_exhaustion):
            raise ValueError("clamp truncation takes no max_attempts/on_exhaustion")
        return self


class Lognormal(Strict):
    family: Literal["lognormal"]
    location: Literal["median", "mean"]
    location_s: float = Field(gt=0, le=600)
    sigma: float = Field(gt=0, le=2)
    truncation: Truncation


class Fixed(Strict):
    family: Literal["fixed"]
    seconds: float = Field(ge=0, le=600)


Distribution = Annotated[Lognormal | Fixed, Field(discriminator="family")]


class Typing(Strict):
    per_char_ms: int = Field(ge=0, le=200)
    cap_ms: int = Field(ge=0, le=60_000)


class SeatOverride(Strict):
    delay: tuple[Distribution, ...] = Field(min_length=1, max_length=4)


class ClassPolicy(Strict):
    """Timing policy for one response class (spec §4.2, §6.3, §6.5)."""

    mechanism: Mechanism
    delay: tuple[Distribution, ...] = Field(min_length=1, max_length=4)
    seat_overrides: dict[Seat, SeatOverride] = Field(default_factory=dict)
    typing: Typing | None = None
    platform_delay: Distribution | None = None
    think: Distribution | None = None
    floor: Distribution | None = None
    timeout_ms: int | None = Field(default=None, ge=1_000, le=60_000)
    fallback: Literal["deterministic_script", "deterministic_file_summary", "none"]
    rendering: Literal["complete"]
    presence_cue: Literal["none", "typing_indicator"]

    @model_validator(mode="after")
    def _mechanism_requirements(self) -> ClassPolicy:
        m = self.mechanism
        if m == "demo_structured_delay_length":
            if self.typing is None:
                raise ValueError("demo_structured_delay_length needs typing")
            if self.floor or self.platform_delay or self.think or self.timeout_ms:
                raise ValueError("demo_structured_delay_length takes only delay and typing")
        elif m == "demo_social_additive":
            if self.typing is None or self.timeout_ms is None:
                raise ValueError("demo_social_additive needs typing and timeout_ms")
            if self.floor or self.platform_delay or self.think:
                raise ValueError("demo_social_additive takes no floor/platform_delay/think")
        elif m == "demo_assistant_floor":
            if self.floor is None or self.timeout_ms is None:
                raise ValueError("demo_assistant_floor needs floor and timeout_ms")
            if self.typing or self.platform_delay or self.think:
                raise ValueError("demo_assistant_floor takes no typing/platform_delay/think")
        elif self.floor is not None:
            raise ValueError("concurrent has no floor; express waits as delay components")
        return self


# ----- timing ----------------------------------------------------------------------------------


class Checkin(Strict):
    at_active_s: int = Field(ge=1, lt=ACTIVE_BUDGET_S)
    collect_wall_ms: int = Field(ge=0, le=30_000)
    inactivity_cap_wall_s: int = Field(ge=30, le=600)
    inactivity_outcome: Literal["technical_stop/checkin_incomplete"]
    # UX round 3 (item 5): ``false`` removes the participant skip — the server then
    # refuses ``pressure_skip``. Earlier published profiles keep ``true``.
    own_rating_skippable: bool
    resume: Literal["explicit"]


class SeatPick(Strict):
    """One ``seat_plan`` slot (UX v7, 2026-10-11): the named seat asks the
    participant about an ordinal crate of its own dealt four-crate assignment —
    ``pick`` indexes the sorted list (0 = lowest, -1 = highest)."""

    seat: Seat
    pick: int = Field(ge=-4, le=3)


class ScriptedRequests(Strict):
    script: ArtifactRef
    times_active_s: tuple[int, ...] = Field(min_length=1, max_length=10)
    # ``assigned_own`` (2026-10-10 revision): an assigned-target ask constrained
    # to the participant's own-file crates, asked by a seat holding no records
    # on it — the fielded ``assigned`` target provably asked about the
    # participant's team-dependent crates (16/27 live sessions) with a seat
    # that usually held the decisive records itself.
    targets: tuple[Literal["unassigned", "assigned", "assigned_own"], ...] | None = None
    # ``seat_plan`` (UX v7): seat and crate are both explicit per slot — the
    # named seat asks about the ``pick``-th crate of its own assignment, dealt
    # before planning so the asks are always on the asker's own beat.
    seat_plan: tuple[SeatPick, ...] | None = None
    seat_policy: Literal["shuffle_fixed_multiset", "coin_majority_shuffle", "seat_plan"]
    seat_multiset: tuple[Seat, ...] | None = None
    status: MaterialStatus

    @model_validator(mode="after")
    def _rules(self) -> ScriptedRequests:
        times = self.times_active_s
        if any(b <= a for a, b in pairwise(times)):
            raise ValueError("scripted request times must strictly increase")
        if times[0] < 1 or times[-1] >= ACTIVE_BUDGET_S:
            raise ValueError("scripted request times must lie inside the active budget")
        # The exact bound (the profile's own budget) is a manifest cross-rule.
        if self.seat_policy == "seat_plan":
            if self.seat_plan is None or len(self.seat_plan) != len(times):
                raise ValueError("seat_plan needs one seat/pick slot per request time")
            if self.targets is not None or self.seat_multiset is not None:
                raise ValueError("seat_plan carries the seats — omit targets/seat_multiset")
        else:
            if self.targets is None or len(self.targets) != len(times):
                raise ValueError("one target per scripted request time")
            if self.seat_plan is not None:
                raise ValueError("seat_plan only pairs with seat_policy 'seat_plan'")
            if self.seat_policy == "shuffle_fixed_multiset":
                if self.seat_multiset is None or len(self.seat_multiset) != len(times):
                    raise ValueError("shuffle_fixed_multiset needs one seat per request")
            elif self.seat_multiset is not None:
                raise ValueError("coin_majority_shuffle derives its multiset; omit seat_multiset")
        return self


class TwoStage(Strict):
    acknowledgment_resets_substantive_schedule: Literal[False]
    acknowledgment_counts_as_fulfillment: Literal[False]


class Concurrency(Strict):
    per_seat_pending_requests: int = Field(ge=1, le=10)
    assistant_pending_prompts: int = Field(ge=1, le=10)
    over_cap: Literal["reject_visible"]


class RateLimits(Strict):
    experimental_request_budget: Literal["unlimited"]
    operational_commands_per_minute: int = Field(ge=10, le=600)


class Timing(Strict):
    active_budget_s: ActiveBudget
    clock_basis: Literal["active_task"]
    countdown_visible: Literal[True]
    checkin: Checkin | None
    scripted_requests: ScriptedRequests
    response_classes: dict[ResponseClass, ClassPolicy]
    ordering: Literal["scheduled_chronological"]
    deadline_structure: Literal["single_global_freeze"]
    early_submission: Literal[False]
    trajectory: Literal["constant"]
    delivery_failure_injection: Literal["none"]
    visibility: Literal["simultaneous_all_recipients"]
    delay_attribution: Literal["none"]
    contingent_responsiveness: Literal["none"]
    availability_windows: Literal["always_available"]
    proactive_sharing: Literal["none"]
    two_stage: TwoStage
    concurrency: Concurrency
    rate_limits: RateLimits
    warnings: tuple[ArtifactRef, ...] = Field(min_length=1)


class ChannelPhase(Strict):
    name: Literal["task", "solo_review"]
    from_active_s: int = Field(ge=0)
    until_active_s: int = Field(gt=0)
    enabled: tuple[Channel, ...] = Field(min_length=1)


class Channels(Strict):
    phases: tuple[ChannelPhase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _contiguous(self) -> Channels:
        cursor = 0
        for phase in self.phases:
            if phase.from_active_s != cursor or phase.until_active_s <= phase.from_active_s:
                raise ValueError("channel phases must be contiguous from active 0")
            if len(set(phase.enabled)) != len(phase.enabled):
                raise ValueError("duplicate channel in a phase")
            cursor = phase.until_active_s
        if cursor not in ACTIVE_BUDGETS_S:
            raise ValueError("channel phases must cover the whole active budget")
        return self

    def phase_at(self, active_s: float) -> ChannelPhase:
        for phase in self.phases:
            if phase.from_active_s <= active_s < phase.until_active_s:
                return phase
        return self.phases[-1]


# ----- manipulation ----------------------------------------------------------------------------


class PressureArm(Strict):
    kind: Literal["pressure_values"]
    teammate_values: tuple[int, int]

    @model_validator(mode="after")
    def _values(self) -> PressureArm:
        low, high = self.teammate_values
        if not (1 <= low <= high <= 7):
            raise ValueError("teammate_values must be (lower, higher) within 1..7")
        return self


class EvaluationArm(Strict):
    kind: Literal["evaluation_exposure"]
    exposure_copy: ArtifactRef


ArmManipulation = Annotated[PressureArm | EvaluationArm, Field(discriminator="kind")]


class ArmSpec(Strict):
    arm: ArmName
    allocation_weight: Literal[1]
    manipulation: ArmManipulation


class PressureManipulation(Strict):
    kind: Literal["pressure_checkin"]
    copy_ref: ArtifactRef
    higher_seat_policy: Literal["independent_fair_coin"]
    rng_stream: Literal["ratings"]
    status: MaterialStatus
    # 2026-10-09 pilot revision: optional arm-specific teammate chat remarks
    # (``pressure_remarks`` artifact). Absent on every earlier profile and omitted from the
    # dump when unset, so their content hashes and bundle pins are unchanged.
    remarks: ArtifactRef | None = None

    @model_serializer(mode="wrap")
    def _omit_unset_remarks(
        self,
        handler: SerializerFunctionWrapHandler,
        info: SerializationInfo,  # noqa: ARG002 - pydantic validator hook
    ) -> dict[str, object]:
        data: dict[str, object] = handler(self)
        if self.remarks is None:
            data.pop("remarks", None)
        return data


class EvaluationManipulation(Strict):
    kind: Literal["evaluation_exposure"]
    surfaces: tuple[Literal["intro", "callout", "strip", "system_message", "peer_intro"], ...]
    status: MaterialStatus


Manipulation = Annotated[PressureManipulation | EvaluationManipulation, Field(discriminator="kind")]


# ----- teammates, assistant, instruments, outcomes ---------------------------------------------


class AckProbability(Strict):
    prompted: float = Field(ge=0, le=1)
    unprompted: float = Field(ge=0, le=1)


class AckPolicy(Strict):
    probabilities: dict[Seat, AckProbability]
    unprompted_seat_weights: dict[Seat, float]
    prompted_seat: Literal["requesting_seat"]

    @model_validator(mode="after")
    def _rules(self) -> AckPolicy:
        if set(self.probabilities) != set(SEATS) or set(self.unprompted_seat_weights) != set(SEATS):
            raise ValueError("acknowledgment policy must cover both seats")
        if abs(sum(self.unprompted_seat_weights.values()) - 1.0) > 1e-9:
            raise ValueError("unprompted_seat_weights must sum to 1")
        return self


class CrosstalkPolicy(Strict):
    speaker: Literal["other_seat"]
    answered_rule: Literal["committed_share_since_request"]


class Teammates(Strict):
    voice_bank: ArtifactRef | None
    fallback_bank: ArtifactRef
    social_prompt: ArtifactRef | None
    social_generation: Literal["model_with_validated_fallback", "deterministic_only"]
    acknowledgments: AckPolicy | None
    crosstalk: CrosstalkPolicy | None
    assistant_referral: Literal["none"]
    unsolicited_requests: Literal["none"]
    status: MaterialStatus


class Assistant(Strict):
    # ``univ_local_unresolved_d05`` marks preview-era profiles whose provider was never
    # pinned; a fielded profile names the real adapter and the digest-qualified served
    # model identity — ``<provider>/<model>@sha256:<digest>`` — so a model change can
    # never ride an existing live manifest (BA-96; the compat boundary forbids it).
    provider: Literal[
        "univ_local_unresolved_d05",
        "local_openai_chat",
        "local_openai_chat/gemma4:26b@sha256:5571076f3d70050487b26b341705799e0ab29b808164f90d20d4cf84f699d251",
    ]
    source_access: Literal["participant_file_at_accepted_request"]
    prompt: ArtifactRef
    validator_version: Literal["validator.pending_wp10a", "teammate_output_v7"]
    fallback: Literal["deterministic_file_summary"]
    model_change_within_session: Literal[False]


class Instruments(Strict):
    consent: ArtifactRef
    training: ArtifactRef
    confidence: ArtifactRef
    peer_ratings: ArtifactRef | None
    post_task: ArtifactRef
    debrief: ArtifactRef
    # BA-105: the open-ended follow-up stage is artifact-pinned. ``None`` on
    # every profile published before the binding existed — those manifests run
    # post_questionnaire → debrief with no code-injected stage.
    open_ended: ArtifactRef | None = None


class Outcomes(Strict):
    payment_policy: ArtifactRef
    completion_statuses: tuple[
        Literal["completed", "declined", "withdrawn", "technical_stop", "abandoned"], ...
    ]
    # ``unaffected`` = a withdrawn session keeps its base entitlement;
    # ``forfeits_base`` = leaving early forfeits it (the consent-v4 rule).
    withdrawal_payment: Literal["unaffected", "forfeits_base"]
    ratings_contingent_pay: Literal[False]


class Task(Strict):
    # 2026-10-15 overlap experiment: ``n_crates`` widens from the fixed 8 to the
    # bounded 4|8|12 the condition levels need; ``crate_overlap`` and
    # ``participant_intel_budget`` are new optional pins, absent (and omitted
    # from the dump) on every earlier profile so content hashes are unchanged.
    n_crates: int = Field(ge=4, le=12)
    n_hideouts: Literal[6]
    n_assigned: Literal[4]
    n_own_file: Literal[2]
    n_team_dependent: Literal[2]
    crate_overlap: Literal["independent", "none", "some", "full"] | None = None
    participant_intel_budget: Literal[20] | None = None
    generator: Literal["investigators.world/demo_v1", "investigators.world/overlap_v1"]
    difficulty_version: Literal[1]

    @model_serializer(mode="wrap")
    def _omit_unset_task_fields(
        self,
        handler: SerializerFunctionWrapHandler,
        info: SerializationInfo,  # noqa: ARG002 - pydantic validator hook
    ) -> dict[str, object]:
        data: dict[str, object] = handler(self)
        if self.crate_overlap is None:
            data.pop("crate_overlap", None)
        if self.participant_intel_budget is None:
            data.pop("participant_intel_budget", None)
        return data

    @property
    def overlap(self) -> Literal["independent", "none", "some", "full"]:
        """Effective overlap level — absent pins mean the legacy regime."""
        return self.crate_overlap or "independent"


class Allocation(Strict):
    method: Literal["permuted_block"]
    block_size: Literal[2, 4, 6]
    strata: tuple[Literal["study_key", "batch_id"], ...]
    rng_stream: Literal["assignment"]


# ----- manifest --------------------------------------------------------------------------------


InteractionMode = Literal["structured_and_nl", "nl_only"]


class Interaction(Strict):
    """UX round 3 (FR-1) feature flag: how the participant asks teammates and the
    assistant. ``structured_and_nl`` offers the per-crate request controls alongside
    free text; ``nl_only`` removes them — ``structured_seeking`` may not open in any
    phase and assistant asks must carry typed text. Arm-invariant by construction (it
    is not under ``condition_set``), so both arms of a profile always share it."""

    mode: InteractionMode


class Manifest(Strict):
    schema_version: Literal["investigators.manifest/1"]
    manifest_id: str = Field(pattern=_MANIFEST_ID)
    manifest_version: int = Field(ge=1)
    study_key: StudyKey
    profile_kind: Literal["reference", "proposed_pilot"]
    material_status: MaterialStatus
    title: str = Field(min_length=1, max_length=200)
    notes: tuple[str, ...] = ()
    decisions: tuple[DecisionRef, ...]
    modes_allowed: tuple[Mode, ...] = Field(min_length=1)
    allocation_policy: Allocation
    condition_set: tuple[ArmSpec, ...] = Field(min_length=2, max_length=2)
    task: Task
    timing: Timing
    channels: Channels
    manipulation: Manipulation
    teammates: Teammates
    assistant: Assistant
    instruments: Instruments
    outcomes: Outcomes
    # Absent on every profile published before UX round 3 — they behave exactly as
    # ``structured_and_nl``. Omitted from the dump when unset so their content hashes
    # (and therefore publication + bundle pins) are unchanged.
    interaction: Interaction | None = None

    @model_serializer(mode="wrap")
    def _omit_unset_interaction(
        self,
        handler: SerializerFunctionWrapHandler,
        info: SerializationInfo,  # noqa: ARG002 - pydantic validator hook
    ) -> dict[str, object]:
        data: dict[str, object] = handler(self)
        if self.interaction is None:
            data.pop("interaction", None)
        return data

    @property
    def interaction_mode(self) -> InteractionMode:
        return "structured_and_nl" if self.interaction is None else self.interaction.mode

    @model_validator(mode="after")
    def _cross_rules(self) -> Manifest:
        problems: list[str] = []
        if self.interaction_mode == "nl_only" and any(
            "structured_seeking" in phase.enabled for phase in self.channels.phases
        ):
            problems.append("nl_only profiles may not open structured_seeking in any phase")
        if not self.manifest_id.endswith(f"_v{self.manifest_version}"):
            problems.append("manifest_id must end with _v<manifest_version>")
        arms = tuple(spec.arm for spec in self.condition_set)
        if len(set(arms)) != len(arms) or not set(arms) <= set(
            STUDY_ARM_VOCABULARY[self.study_key]
        ):
            problems.append(
                f"{self.study_key} arms must be distinct members of "
                f"{STUDY_ARM_VOCABULARY[self.study_key]}"
            )
        tp = self.study_key == "investigators_time_pressure"
        want_arm_kind = "pressure_values" if tp else "evaluation_exposure"
        if any(spec.manipulation.kind != want_arm_kind for spec in self.condition_set):
            problems.append(f"every arm manipulation must be {want_arm_kind}")
        if self.manipulation.kind != ("pressure_checkin" if tp else "evaluation_exposure"):
            problems.append("study-level manipulation kind does not match study_key")
        if tp and self.timing.checkin is None:
            problems.append("time-pressure profiles need a check-in")
        if not tp and self.timing.checkin is not None:
            problems.append("peer-evaluation profiles have no check-in (spec §2.2)")
        if tp and self.instruments.peer_ratings is not None:
            problems.append("time-pressure profiles collect no peer ratings")
        if not tp and self.instruments.peer_ratings is None:
            problems.append("peer-evaluation profiles collect peer ratings in both arms")
        if self.allocation_policy.block_size % len(arms):
            problems.append("allocation block_size must be a multiple of the arm count")
        # timing mechanisms (spec §6.5)
        for name, policy in self.timing.response_classes.items():
            if policy.mechanism in DEMO_MECHANISMS and self.profile_kind != "reference":
                problems.append(f"{name}: demo mechanism {policy.mechanism} is reference-only")
            if name in GENERATIVE_CLASSES and policy.timeout_ms is None:
                problems.append(f"{name}: generative class needs timeout_ms")
            if name not in TEAMMATE_CLASSES and policy.seat_overrides:
                problems.append(f"{name}: seat_overrides apply to teammate classes only")
        for required in ("structured_record", "social_greeting", "social_reply", "assistant"):
            if required not in self.timing.response_classes:
                problems.append(f"missing response class {required}")
        tm = self.teammates
        if ("acknowledgment" in self.timing.response_classes) != (tm.acknowledgments is not None):
            problems.append("acknowledgment class and teammates.acknowledgments go together")
        if ("crosstalk" in self.timing.response_classes) != (tm.crosstalk is not None):
            problems.append("crosstalk class and teammates.crosstalk go together")
        if (tm.social_generation == "model_with_validated_fallback") != (
            tm.social_prompt is not None
        ):
            problems.append("social_prompt is required exactly when social generation uses a model")
        if tm.acknowledgments is not None and tm.voice_bank is None:
            problems.append("acknowledgments need a voice_bank")
        # channels and schedules — every active-time anchor sits inside this profile's budget
        budget = self.timing.active_budget_s
        if self.channels.phases[-1].until_active_s != budget:
            problems.append("channel phases must cover the whole active budget")
        if self.timing.scripted_requests.times_active_s[-1] >= budget:
            problems.append("scripted request times must lie inside the active budget")
        if self.timing.checkin is not None and self.timing.checkin.at_active_s >= budget:
            problems.append("check-in must fall inside the active budget")
        for at in self.timing.scripted_requests.times_active_s:
            if "sharing" not in self.channels.phase_at(at).enabled:
                problems.append(f"scripted request at active {at} falls where sharing is closed")
        checkin = self.timing.checkin
        if checkin is not None:
            boundaries = {p.from_active_s for p in self.channels.phases}
            if len(self.channels.phases) > 1 and checkin.at_active_s not in boundaries:
                problems.append("check-in must start on a channel-phase boundary")
        # The one-minute warning contract pins the artifact identity, not a frozen
        # version: v1 is immutable history, and approved successors (verbatim wording,
        # unchanged trigger) satisfy the same spec §4.1 contract.
        if not any(
            ref.artifact_id == "warning.one_minute_left" and ref.version >= 1
            for ref in self.timing.warnings
        ):
            problems.append("every profile carries warning.one_minute_left (spec §4.1)")
        # task-shape cross-rules (2026-10-15 overlap experiment): the new task
        # pins only ride the parameterized ``overlap_v1`` generator, the crate
        # count must match the overlap level, and a full-overlap world has no
        # unassigned crates for scripted ``unassigned`` targets to draw on.
        task = self.task
        overlap = task.overlap
        if task.generator == "investigators.world/demo_v1":
            if task.crate_overlap is not None or task.participant_intel_budget is not None:
                problems.append(
                    "demo_v1 manifests may not set crate_overlap/participant_intel_budget"
                )
        elif task.crate_overlap is None and task.participant_intel_budget is None:
            problems.append(
                "overlap_v1 manifests must set crate_overlap or participant_intel_budget"
            )
        expected_crates = {"independent": 8, "some": 8, "none": 12, "full": 4}[overlap]
        if task.n_crates != expected_crates:
            problems.append(f"crate_overlap {overlap!r} needs n_crates {expected_crates}")
        if overlap == "full":
            targets = self.timing.scripted_requests.targets
            if targets is not None and "unassigned" in targets:
                problems.append(
                    "full-overlap worlds have no unassigned crates — "
                    "drop 'unassigned' scripted targets"
                )
        if overlap != "independent" and self.timing.scripted_requests.seat_policy != "seat_plan":
            problems.append(
                "overlap levels need seat_plan scripted requests — target pools "
                "assume the legacy 8-crate unassigned semantics"
            )
        # D03/audit S03: 20 is the only designed budget — the generator's
        # split rule (4/crate assigned + singles; 5/crate under full) is
        # defined for exactly that total. Accepting an arbitrary range
        # would let a manifest declare a file size the generator cannot
        # honor exactly. A second value needs its own designed split.
        if task.participant_intel_budget is not None and task.participant_intel_budget != 20:
            problems.append(
                "participant_intel_budget supports only the designed value 20 for this release"
            )
        # launch eligibility cannot be claimed silently (spec §4.1, plan WP02 exit)
        if "live" in self.modes_allowed:
            if self.profile_kind != "proposed_pilot" or self.material_status != "approved":
                problems.append("live mode needs an approved proposed_pilot profile")
            if any(d.status != "resolved" for d in self.decisions):
                problems.append("live mode needs every linked decision resolved")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def arm_spec(self, arm: str) -> ArmSpec:
        for spec in self.condition_set:
            if spec.arm == arm:
                return spec
        raise KeyError(f"{self.manifest_id} has no arm {arm!r}")

    @property
    def arms(self) -> tuple[ArmName, ...]:
        return tuple(spec.arm for spec in self.condition_set)
