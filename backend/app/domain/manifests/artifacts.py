"""Versioned content artifacts, schema ``investigators.artifact/1`` (INV-0204, INV-0213).

Every manipulation surface, warning, script bank, fallback copy, instrument, prompt, payment
policy and QA timing overlay is a separately versioned artifact ``<artifact_id>@v<version>``
stored as ``content/<dir>/<artifact_id>.v<version>.json``. Manifests reference artifacts; they
never inline copy. Editing an artifact means publishing a new version (``publication.py``).

The four teammate-policy layers stay separate (spec §4.2, hidden-bench §3.6):
factual service (world/retrieval, WP05; no artifact here), interaction schedule (manifest
``timing``), conversational realization (``request_script``, ``voice_bank``, ``fallback_bank``,
``prompt``) and manipulation surfaces (``checkin_copy``, ``exposure_copy``).
"""

from __future__ import annotations

from itertools import pairwise
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, model_serializer, model_validator

from app.domain.manifests.schema import (
    ACTIVE_BUDGET_S,
    ActiveBudget,
    ArmName,
    DecisionId,
    MaterialStatus,
    Seat,
    Strict,
)

ARTIFACT_SCHEMA_VERSION = "investigators.artifact/1"
_ARTIFACT_ID = r"^[a-z0-9_]+(\.[a-z0-9_]+)+$"


class SourceAnchor(Strict):
    """Where the content comes from: a behavior/defect ID in the reference fixtures, a demo line
    range, a spec section, or ``authored`` for new proposed wording."""

    ref: str = Field(min_length=1, max_length=120)


class _Envelope(Strict):
    schema_version: Literal["investigators.artifact/1"]
    artifact_id: str = Field(pattern=_ARTIFACT_ID)
    version: int = Field(ge=1)
    status: MaterialStatus
    decisions: tuple[DecisionId, ...] = ()
    sources: tuple[SourceAnchor, ...] = Field(min_length=1)
    notes: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.artifact_id}@v{self.version}"


# ----- warning ---------------------------------------------------------------------------------


class WarningTrigger(Strict):
    clock_basis: Literal["active_task"]
    at_active_s: int = Field(ge=1, lt=ACTIVE_BUDGET_S)


class LowTimeState(Strict):
    from_active_s: int = Field(ge=0, lt=ACTIVE_BUDGET_S)
    until_active_s: ActiveBudget
    style: Literal["low_time"]


class WarningDelivery(Strict):
    per_session: Literal[1]
    logical_job_key: str = Field(pattern=r"^warning\.[a-z0-9_]+\.v[0-9]+$")
    when_task_closed: Literal["suppress_and_log"]
    waits_on: tuple[()] = ()
    pauses_with: Literal["active_task_clock"]


class WarningExposure(Strict):
    record: Literal["warning_exposure"]
    receipts: tuple[Literal["commit", "receive", "render"], ...]


class WarningBody(Strict):
    text: str = Field(min_length=1, max_length=200)
    sender: Literal["system"]
    teammate_voice: Literal[False]
    arm_invariant: Literal[True]
    trigger: WarningTrigger
    low_time_state: LowTimeState
    delivery: WarningDelivery
    exposure: WarningExposure

    @model_validator(mode="after")
    def _rules(self) -> WarningBody:
        if self.low_time_state.from_active_s != self.trigger.at_active_s:
            raise ValueError("low_time state starts when the warning fires")
        if set(self.exposure.receipts) != {"commit", "receive", "render"}:
            raise ValueError("warning exposure records commit, receive and render receipts")
        return self


class WarningArtifact(_Envelope):
    kind: Literal["warning"]
    body: WarningBody

    @model_validator(mode="after")
    def _job_key(self) -> WarningArtifact:
        if self.body.delivery.logical_job_key != f"{self.artifact_id}.v{self.version}":
            raise ValueError("warning logical_job_key must be <artifact_id>.v<version>")
        return self


# ----- conversational realization -------------------------------------------------------------


class RequestScriptBody(Strict):
    templates: tuple[str, ...] = Field(min_length=1)
    selection: Literal["single_template", "uniform_per_request", "uniform_without_replacement"]

    @model_validator(mode="after")
    def _placeholder(self) -> RequestScriptBody:
        if any("{n}" not in t for t in self.templates):
            raise ValueError("request templates must contain {n}")
        if self.selection == "uniform_without_replacement" and len(
            {t.strip().casefold() for t in self.templates}
        ) != len(self.templates):
            raise ValueError("without-replacement request templates must be distinct")
        return self


class RequestScriptArtifact(_Envelope):
    kind: Literal["request_script"]
    body: RequestScriptBody


class SeatVoice(Strict):
    greetings: tuple[str, ...] = Field(min_length=1)
    thanks: tuple[str, ...] = ()
    no_news: tuple[str, ...] = ()
    after_share: tuple[str, ...] = ()


class VoiceBankBody(Strict):
    seats: dict[Seat, SeatVoice]
    repeat_policy: Literal["no_repeat_until_exhausted", "uniform_with_replacement"]

    @model_validator(mode="after")
    def _both(self) -> VoiceBankBody:
        if set(self.seats) != {"JT", "PM"}:
            raise ValueError("voice banks cover both seats")
        return self


class VoiceBankArtifact(_Envelope):
    kind: Literal["voice_bank"]
    body: VoiceBankBody


class FallbackRule(Strict):
    # ``asks_intel_nl`` (O-1): nl_only variant of the ask redirect — invites a typed
    # ask instead of pointing at the picker. ``identity_probe`` (O-3): the fixed
    # truthful disclosure for "are you a bot?" probes.
    when: Literal[
        "gives_location",
        "offers_help",
        "social",
        # v15 (2026-10-18): workload-level variants of the social pool — a seat
        # that reported 6/7 must not sound relaxed. Selected by the speaker's
        # recorded check-in level; absent pools fall back to "social".
        "social_high",
        "social_low",
        "asks_intel",
        "asks_intel_nl",
        "identity_probe",
        "default",
    ]
    lines: tuple[str, ...] = Field(min_length=1)


class FallbackBankBody(Strict):
    classifier: Literal["demo_keyword_v1", "neutral_keyword_v2"]
    rules: tuple[FallbackRule, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _default_last(self) -> FallbackBankBody:
        whens = [r.when for r in self.rules]
        if len(set(whens)) != len(whens) or whens[-1] != "default":
            raise ValueError("fallback rules are unique and end with a default rule")
        return self


class FallbackBankArtifact(_Envelope):
    kind: Literal["fallback_bank"]
    body: FallbackBankBody


class PromptBody(Strict):
    purpose: Literal["assistant", "social"]
    text: str = Field(min_length=1)
    placeholders: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _placeholders(self) -> PromptBody:
        for name in self.placeholders:
            if "{" + name + "}" not in self.text:
                raise ValueError(f"declared placeholder {name} is not used")
        return self


class PromptArtifact(_Envelope):
    kind: Literal["prompt"]
    body: PromptBody


# ----- manipulation surfaces ------------------------------------------------------------------


class CheckinCopyBody(Strict):
    question: str
    scale_min_label: str
    scale_max_label: str
    note: str
    collecting: str
    reveal_title: str
    reveal_note: str
    resume_label: str


class CheckinCopyArtifact(_Envelope):
    kind: Literal["checkin_copy"]
    body: CheckinCopyBody


class PressureRemark(Strict):
    """One teammate chat line reinforcing the arm's workload level.

    ``speaker`` is positional: ``first`` is the seat drawn by the per-session fair coin,
    ``second`` is the other seat — so speaker identity is balanced, never fixed to JT.

    ``mixed_lines`` (v15, 2026-10-17): under the ``mixed`` arm the two seats carry
    different scripted workload levels, so the delivered line is keyed by the
    *speaker's* level — ``{"high": ..., "low": ...}`` — resolved at delivery from
    the session's recorded check-in ``assigned_values``. ``lines`` keeps
    whole-arm text for the paired-level arms (high/low/eval/control); "mixed" is
    not a valid ``lines`` key because one line cannot describe both seats."""

    at_active_s: int = Field(ge=1, lt=ACTIVE_BUDGET_S)
    speaker: Literal["first", "second"]
    lines: dict[ArmName, str]
    mixed_lines: dict[Literal["high", "low"], str] | None = None
    # v15 (2026-10-18): under ``mixed``, pin WHICH level speaks this remark —
    # the check-in exchange leads heavy, the warning-time progress exchange
    # leads light. None = position default (first->high, second->low).
    speaker_level: Literal["high", "low"] | None = None

    @model_validator(mode="after")
    def _lines(self) -> PressureRemark:
        if any(not (1 <= len(t.strip()) <= 200) for t in self.lines.values()):
            raise ValueError("remark lines must be 1..200 characters")
        if "mixed" in self.lines:
            raise ValueError("lines cannot key 'mixed' — use mixed_lines (per-seat level)")
        if self.mixed_lines is not None:
            if set(self.mixed_lines) != {"high", "low"}:
                raise ValueError("mixed_lines must carry exactly {high, low} keys")
            if any(not (1 <= len(t.strip()) <= 200) for t in self.mixed_lines.values()):
                raise ValueError("remark mixed_lines must be 1..200 characters")
        return self

    @model_serializer(mode="wrap")
    def _omit_unset_mixed_lines(self, handler: Any) -> dict[str, Any]:
        # Remarks without mixed_lines keep their pre-v15 content hash.
        data: dict[str, Any] = handler(self)
        if self.mixed_lines is None:
            data.pop("mixed_lines", None)
        if self.speaker_level is None:
            data.pop("speaker_level", None)
        return data


class PressureRemarksBody(Strict):
    """Fixed, arm-specific teammate remarks in the team chat (2026-10-09 pilot revision).

    Deterministic bound copy delivered identically in the AI and scripted teammate builds:
    it never passes through generation, so the generated-output gate does not apply.
    Delivered on the active clock, so a check-in pause cannot move it."""

    remarks: tuple[PressureRemark, ...] = Field(min_length=1, max_length=6)
    seat_order: Literal["fair_coin_per_session"]
    channel: Literal["team"]

    @model_validator(mode="after")
    def _rules(self) -> PressureRemarksBody:
        times = [r.at_active_s for r in self.remarks]
        if any(b <= a for a, b in pairwise(times)):
            raise ValueError("remark times must strictly increase")
        arm_sets = {frozenset(r.lines) for r in self.remarks}
        if len(arm_sets) != 1:
            raise ValueError("every remark must carry lines for the same arms")
        mixed_sets = {r.mixed_lines is not None for r in self.remarks}
        if len(mixed_sets) != 1:
            raise ValueError("every remark must carry mixed_lines, or none may")
        return self


class PressureRemarksArtifact(_Envelope):
    kind: Literal["pressure_remarks"]
    body: PressureRemarksBody


class ExposureCopyBody(Strict):
    arm: Literal["eval", "control"]
    format: Literal["demo_html_reference", "plain_text"]
    intro: str
    callout: str
    strip: str
    system_message: str
    peer_intro: str


class ExposureCopyArtifact(_Envelope):
    kind: Literal["exposure_copy"]
    body: ExposureCopyBody


# ----- instruments and materials --------------------------------------------------------------


class ItemResponse(Strict):
    # ``free_text`` (BA-105): min/max carry character bounds — used by the
    # open_ended follow-up instrument, whose items are validated as non-blank
    # prose within ``max`` rather than numeric values.
    type: Literal["likert", "slider", "free_text"]
    min: int
    max: int
    min_label: str
    max_label: str
    counts_only_after_input: Literal[True]

    @model_validator(mode="after")
    def _range(self) -> ItemResponse:
        if self.min >= self.max:
            raise ValueError("item response min must be < max")
        return self


class Item(Strict):
    item_id: str = Field(pattern=r"^[a-z0-9_]+$")
    text: str = Field(min_length=1)
    skippable: bool
    # UX round 4 (item 14): an optional fixed-choice list turns the item into a
    # categorical pick (the stored ``value`` is the chosen option's text) rather
    # than a point on the instrument's likert/slider scale.
    options: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _options(self) -> Item:
        if self.options is not None:
            if len(self.options) < 2:
                raise ValueError("choice items need at least two options")
            if any(not o.strip() for o in self.options):
                raise ValueError("choice options must be non-empty")
            if len(set(self.options)) != len(self.options):
                raise ValueError("choice options must be unique")
        return self

    @model_serializer(mode="wrap")
    def _omit_empty_options(self, handler: Any) -> dict[str, Any]:
        # Published v1 instruments were hashed without an ``options`` key —
        # serializing ``options: null`` would change their content hash, so the
        # key is omitted unless the item actually declares choices.
        data: dict[str, Any] = handler(self)
        if self.options is None:
            data.pop("options", None)
        return data


class InstrumentBody(Strict):
    applies_to: Literal["session", "per_answered_crate", "per_teammate_seat"]
    order: Literal["fixed"]
    response: ItemResponse
    items: tuple[Item, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> InstrumentBody:
        ids = [i.item_id for i in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("item_ids must be unique")
        return self


class InstrumentArtifact(_Envelope):
    kind: Literal["instrument"]
    body: InstrumentBody


class MaterialSection(Strict):
    """A titled or untitled run of paragraphs inside published participant copy."""

    title: str | None = None
    paragraphs: tuple[str, ...] = Field(min_length=1)


class MaterialText(Strict):
    """Literal participant-facing copy carried by a ``material_reference`` version (BA-37).

    When present, the served copy IS this body — the client renders it verbatim and never
    substitutes component literals. ``None`` marks an unresolved placeholder version whose
    wording is still a pointer to ``reference_copy``.
    """

    heading: str | None = None
    subheading: str | None = None
    sections: tuple[MaterialSection, ...] = Field(min_length=1)
    question: str | None = None
    accept_label: str | None = None
    decline_label: str | None = None


class MaterialReferenceBody(Strict):
    """A participant-facing material. Unresolved versions only name where the reference copy
    lives and which decision freezes it; published versions also carry the literal copy in
    ``text``. Placeholders bind in preview/QA only, never in live."""

    material: Literal["consent", "training", "debrief"]
    reference_copy: str = Field(min_length=1)
    freeze_decision: DecisionId
    text: MaterialText | None = None

    # ``text`` is omitted from the canonical dump when absent: artifact hashes are computed
    # over ``model_dump``, so serializing ``text: null`` on pre-BA-37 placeholder versions
    # would silently re-hash them and invalidate every published bundle that binds them.
    @model_serializer(mode="wrap")
    def _omit_absent_text(self, handler: Any) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if data.get("text") is None:
            data.pop("text", None)
        return data


class MaterialReferenceArtifact(_Envelope):
    kind: Literal["material_reference"]
    body: MaterialReferenceBody


class BonusPolicy(Strict):
    type: Literal["none", "per_correct_assigned_crate"]
    cents_per_correct: int = Field(default=0, ge=0)
    max_cents: int = Field(default=0, ge=0)


class PaymentPolicyBody(Strict):
    currency: Literal["USD"]
    base_cents: int | None = Field(ge=0)
    bonus: BonusPolicy
    activatable: bool
    technical_stop: Literal["unresolved_d04", "base_paid", "base_withheld"]


class PaymentPolicyArtifact(_Envelope):
    kind: Literal["payment_policy"]
    body: PaymentPolicyBody


class QaTimingOverlayBody(Strict):
    clock_speed_factor: int = Field(ge=2, le=60)
    analytic_eligible: Literal[False]
    allowed_modes: tuple[Literal["qa"], ...]


class QaTimingOverlayArtifact(_Envelope):
    kind: Literal["qa_timing_overlay"]
    body: QaTimingOverlayBody


# ----- offline semantic coding (WP12a; spec §9.4) -----------------------------------------------
#
# Coding content lives in ``content/coding/`` and is never referenced by a manifest: it binds
# offline coding runs (``coding_run.rubric_artifact_id`` / ``prompt_artifact_id``), not sessions.
# The model-assisted path is dormant pending D05 — ``coding_prompt`` artifacts carry
# ``dormant_until`` and can never satisfy a live binding requirement.

CODING_CATEGORIES = (
    "explicit_statement",
    "question",
    "inference",
    "negation",
    "ambiguity",
    "solicitation",
    "repetition",
    "stance",
)


class RubricExample(Strict):
    text: str = Field(min_length=1)
    codes: tuple[str, ...] = Field(min_length=1)
    note: str = ""


class RubricCategory(Strict):
    category_id: str = Field(pattern=r"^[a-z0-9_]+$")
    definition: str = Field(min_length=1)
    examples: tuple[RubricExample, ...] = Field(min_length=1)


class CodingRubricBody(Strict):
    """The versioned coding rubric (``coding.rubric@v1``). Declarative: it fixes the code
    vocabulary, span model and adjudication rules; the deterministic coder implements them
    under ``code_version`` and the corpus pins expected-vs-actual behavior (INV-1209)."""

    code_schema: Literal["investigators.coding/1"]
    subject_statuses: tuple[Literal["coded", "not_coded", "failed", "abstained"], ...]
    assertion_values: tuple[Literal["asserted", "uncertain", "hedged", "negated"], ...]
    event_types: tuple[Literal["arrival", "still_at", "departure", "transit"], ...]
    stance_values: tuple[
        Literal["endorse", "question", "contradict", "uncertain", "no_stance"], ...
    ]
    novelty_values: tuple[
        Literal[
            "first_introduction",
            "self_repetition",
            "other_source_repetition",
            "attachment",
            "unresolved",
        ],
        ...,
    ]
    span_kinds: tuple[Literal["crate", "location", "time", "evidence"], ...]
    categories: tuple[RubricCategory, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _categories(self) -> CodingRubricBody:
        ids = [c.category_id for c in self.categories]
        if set(ids) != set(CODING_CATEGORIES) or len(ids) != len(ids):
            raise ValueError(f"rubric must define exactly {CODING_CATEGORIES}")
        return self


class CodingRubricArtifact(_Envelope):
    kind: Literal["coding_rubric"]
    body: CodingRubricBody


class CodingPromptBody(Strict):
    """The model-coding prompt: a config point only. ``dormant_until`` names the decision
    (D05) that must resolve before any model-assisted run may exist."""

    purpose: Literal["coding"]
    dormant_until: Literal["D05"]
    text: str = Field(min_length=1)


class CodingPromptArtifact(_Envelope):
    kind: Literal["coding_prompt"]
    body: CodingPromptBody


class CorpusAttachment(Strict):
    """An authenticated attachment on a corpus subject: exact evidence with provenance
    that bypasses semantic matching (spec §9.4, INV-1202)."""

    record_id: str = Field(min_length=1)
    crate: int = Field(ge=1, le=8)
    location_id: str | None = None
    event_type: Literal["arrival", "still_at", "departure", "transit"]


class CorpusHistoryItem(Strict):
    """One already-visible history entry for a corpus case: a delivered/held record or a
    prior broadcast message. ``scope`` marks recipient visibility — ``participant_file``
    items are in the participant's own file and never entered the shared channel."""

    kind: Literal["record", "message"]
    seq: int = Field(ge=0)
    actor: str = Field(min_length=1)
    scope: Literal["broadcast", "participant_file"]
    record_id: str | None = None
    crate: int | None = Field(default=None, ge=1, le=8)
    location_id: str | None = None
    event_type: Literal["arrival", "still_at", "departure", "transit"] | None = None
    body: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> CorpusHistoryItem:
        if self.kind == "record":
            if self.record_id is None or self.crate is None or self.event_type is None:
                raise ValueError("record history needs record_id, crate and event_type")
        elif self.body is None:
            raise ValueError("message history needs body")
        return self


class CorpusSubject(Strict):
    seq: int = Field(ge=1)
    actor: Literal["participant", "JT", "PM", "assistant", "system"]
    channel: Literal["team", "assistant", "system"]
    body: str = ""
    context_crate: int | None = Field(default=None, ge=1, le=8)
    attachments: tuple[CorpusAttachment, ...] = ()


class ExpectedAssertion(Strict):
    """Expected fields of one coded assertion, in subject order. Only declared fields are
    compared — anything omitted is deliberately unchecked."""

    crate: int | None = Field(default=None, ge=1, le=8)
    location_id: str | None = None
    event_type: Literal["arrival", "still_at", "departure", "transit"] | None = None
    assertion: Literal["asserted", "uncertain", "hedged", "negated"] | None = None
    stance: Literal["endorse", "question", "contradict", "uncertain", "no_stance"] | None = None
    inference: bool | None = None
    ambiguous: bool | None = None
    attribution: str | None = None
    temporal_challenge: bool | None = None
    off_crate: bool | None = None
    novelty: (
        Literal[
            "first_introduction",
            "self_repetition",
            "other_source_repetition",
            "attachment",
            "unresolved",
        ]
        | None
    ) = None
    novel_to_recipient: bool | None = None
    span_text: str | None = None


class CorpusExpected(Strict):
    status: Literal["coded", "not_coded", "failed", "abstained"]
    assertions: tuple[ExpectedAssertion, ...] = ()
    n_questions: int = Field(default=0, ge=0)
    n_solicitations: int = Field(default=0, ge=0)
    solicitation_crates: tuple[int, ...] = ()
    acknowledgment_only: bool | None = None


class CorpusCase(Strict):
    case_id: str = Field(pattern=r"^dev\.[a-z0-9_.]+$")
    study_key: Literal["investigators_time_pressure", "investigators_peer_eval"]
    arm: Literal["high", "low", "eval", "control", "mixed"]
    source: Literal["synthetic", "staff_authored", "generated", "fallback"]
    adversarial: bool = False
    blinding_note: str | None = None
    history: tuple[CorpusHistoryItem, ...] = ()
    subject: CorpusSubject
    expected: CorpusExpected

    @model_validator(mode="after")
    def _arm(self) -> CorpusCase:
        from app.domain.manifests.schema import STUDY_ARM_VOCABULARY

        if self.arm not in STUDY_ARM_VOCABULARY[self.study_key]:
            raise ValueError(f"{self.arm} is not an arm of {self.study_key}")
        if any(h.seq >= self.subject.seq for h in self.history):
            raise ValueError("history items must precede the subject seq")
        return self


class CodingCorpusBody(Strict):
    """The blinded development corpus (INV-1203). ``corpus_kind`` is ``development``
    forever — real held-out pilot text is a different artifact lineage (WP12b) and its
    metrics are never reported together with this set."""

    corpus_kind: Literal["development"]
    blinding_policy: str = Field(min_length=1)
    unavoidable_unmasking: tuple[str, ...] = ()
    cases: tuple[CorpusCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _coverage(self) -> CodingCorpusBody:
        from app.domain.manifests.schema import CORPUS_REQUIRED_ARMS

        ids = [c.case_id for c in self.cases]
        if len(set(ids)) != len(ids):
            raise ValueError("case_ids must be unique")
        covered = {(c.study_key, c.arm) for c in self.cases}
        wanted = {(k, a) for k, arms in CORPUS_REQUIRED_ARMS.items() for a in arms}
        if not wanted <= covered:
            raise ValueError(f"corpus must cover every required study/arm {sorted(wanted)}")
        for source in ("synthetic", "staff_authored", "generated", "fallback"):
            if not any(c.source == source for c in self.cases):
                raise ValueError(f"corpus needs at least one {source} case")
        if not any(c.adversarial for c in self.cases):
            raise ValueError("corpus needs at least one adversarial case")
        return self


class CodingCorpusArtifact(_Envelope):
    kind: Literal["coding_corpus"]
    body: CodingCorpusBody


Artifact = Annotated[
    WarningArtifact
    | RequestScriptArtifact
    | VoiceBankArtifact
    | FallbackBankArtifact
    | PromptArtifact
    | CheckinCopyArtifact
    | PressureRemarksArtifact
    | ExposureCopyArtifact
    | InstrumentArtifact
    | MaterialReferenceArtifact
    | PaymentPolicyArtifact
    | QaTimingOverlayArtifact
    | CodingRubricArtifact
    | CodingPromptArtifact
    | CodingCorpusArtifact,
    Field(discriminator="kind"),
]
ARTIFACT_ADAPTER: TypeAdapter[Artifact] = TypeAdapter(Artifact)
