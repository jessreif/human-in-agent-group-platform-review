"""Bind a published manifest into a complete executable configuration (INV-0201/0206/0209).

A bound manifest is what an enrollment pins: the materialized manifest, every artifact it
references (by key and content hash), the deployment mode and the timing mode. Binding fails
closed with every problem listed. It enforces:

- the manifest and its exact artifact bundle match the immutable publication index;
- the requested mode is allowed by the manifest;
- every artifact reference resolves to the expected kind;
- live mode only with approved materials, activatable payment and faithful timing — unresolved
  launch choices can therefore never become live defaults (plan WP02 exit);
- accelerated timing only as an explicit QA overlay in ``qa`` mode, never analytic.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from app.domain.manifests.artifacts import (
    FallbackBankArtifact,
    PaymentPolicyArtifact,
    PressureRemarksArtifact,
    QaTimingOverlayArtifact,
    RequestScriptArtifact,
)
from app.domain.manifests.canonical import content_hash
from app.domain.manifests.schema import ArtifactRef, Manifest, Mode
from app.domain.manifests.store import ContentError, ContentStore, StoredArtifact, bundle_hash

TimingMode = Literal["faithful", "accelerated_qa"]
TIMING_MODE_VALUES: tuple[TimingMode, ...] = ("faithful", "accelerated_qa")


def parse_timing_mode(value: str) -> TimingMode:
    """Narrow an untyped timing-mode string to ``TimingMode`` (audit L2)."""
    for timing_mode in TIMING_MODE_VALUES:
        if value == timing_mode:
            return timing_mode
    raise ValueError(f"unknown timing_mode {value!r}")


class BindError(ContentError):
    """The manifest cannot be bound for the requested mode."""


@dataclass(frozen=True)
class BoundManifest:
    manifest: Manifest
    manifest_sha256: str
    bundle_sha256: str
    artifacts: Mapping[str, StoredArtifact]
    mode: Mode
    timing_mode: TimingMode
    qa_overlay: StoredArtifact | None
    launch_eligible: bool

    @property
    def manifest_id(self) -> str:
        return self.manifest.manifest_id

    @property
    def analytic_eligible(self) -> bool:
        """Only faithful live sessions can ever count analytically (spec §5.1, INV-0406)."""
        return self.mode == "live" and self.timing_mode == "faithful"

    @property
    def clock_speed_factor(self) -> int:
        if self.qa_overlay is None:
            return 1
        overlay = self.qa_overlay.artifact
        assert isinstance(overlay, QaTimingOverlayArtifact)
        return overlay.body.clock_speed_factor

    def artifact(self, slot: str) -> StoredArtifact:
        return self.artifacts[slot]

    def binding_record(self) -> dict[str, Any]:
        """The JSON record an enrollment persists alongside its session (WP03)."""
        return {
            "manifest_id": self.manifest_id,
            "manifest_sha256": self.manifest_sha256,
            "bundle_sha256": self.bundle_sha256,
            "mode": self.mode,
            "timing_mode": self.timing_mode,
            "qa_overlay": None
            if self.qa_overlay is None
            else [self.qa_overlay.key, self.qa_overlay.sha256],
            "artifacts": {slot: [a.key, a.sha256] for slot, a in sorted(self.artifacts.items())},
            "launch_eligible": self.launch_eligible,
            # QA overlays only (live bindings reject factor != 1); the persisted record
            # lets the raw-SQL due-ness paths apply the same wall->active scaling the
            # resolved policy carries (BA-14).
            "clock_speed_factor": self.clock_speed_factor,
        }

    @property
    def binding_sha256(self) -> str:
        return content_hash(self.binding_record())


def bind(
    store: ContentStore,
    manifest_id: str,
    *,
    mode: Mode,
    timing_mode: TimingMode = "faithful",
    qa_overlay: ArtifactRef | None = None,
) -> BoundManifest:
    stored = store.manifests.get(manifest_id)
    if stored is None:
        raise BindError([f"unknown manifest {manifest_id!r}"])
    manifest = stored.manifest
    problems: list[str] = []
    artifacts, ref_problems = store.resolve_refs(manifest)
    problems.extend(ref_problems)
    request_script = artifacts.get("timing.scripted_requests.script")
    if (
        request_script is not None
        and isinstance(request_script.artifact, RequestScriptArtifact)
        and request_script.artifact.body.selection == "uniform_without_replacement"
        and len(request_script.artifact.body.templates)
        < len(manifest.timing.scripted_requests.times_active_s)
    ):
        problems.append(f"{manifest_id}: not enough distinct templates for scripted requests")
    remarks = artifacts.get("manipulation.remarks")
    if remarks is not None and isinstance(remarks.artifact, PressureRemarksArtifact):
        budget = manifest.timing.active_budget_s
        # Under ``mixed`` each seat carries its own scripted level, so per-arm
        # ``lines`` only need to cover the manifest's non-mixed arms; the mixed
        # arm's text lives in per-seat-level ``mixed_lines``.
        declared = set(manifest.arms)
        want_lines = declared - {"mixed"}
        for r in remarks.artifact.body.remarks:
            # v15: extras for arms the manifest does not declare are inert
            # (never resolved) — require coverage of the declared non-mixed
            # arms, tolerate the rest (a remarks artifact may ship lines for
            # vocabulary arms it serves across manifest versions).
            if not want_lines <= set(r.lines):
                problems.append(f"{manifest_id}: remark lines must cover {sorted(want_lines)}")
            if "mixed" in declared and r.mixed_lines is None:
                problems.append(f"{manifest_id}: mixed arm requires mixed_lines on every remark")
            if r.at_active_s >= budget:
                problems.append(f"{manifest_id}: remark at active {r.at_active_s} is past budget")
            elif "social_reply" not in manifest.channels.phase_at(r.at_active_s).enabled:
                problems.append(
                    f"{manifest_id}: remark at active {r.at_active_s} falls where chat is closed"
                )
    bundle = bundle_hash(stored.sha256, artifacts) if not ref_problems else ""

    published = store.published.manifests.get(manifest_id)
    if published is None:
        problems.append(f"{manifest_id}: not published; run the publish command first")
    else:
        if published.manifest_sha256 != stored.sha256:
            problems.append(
                f"{manifest_id}: manifest changed after publication; publish a new version"
            )
        if bundle and published.bundle_sha256 != bundle:
            problems.append(
                f"{manifest_id}: a referenced artifact changed after publication; "
                "publish new artifact and manifest versions"
            )

    if mode not in manifest.modes_allowed:
        problems.append(
            f"{manifest_id}: mode {mode!r} not allowed (allowed {manifest.modes_allowed})"
        )

    # INV-0705: the demo fallback bank's historical busy/stuck wording is a pressure confound
    # (spec §7.3). Proposed-pilot manifests must bind neutral-classifier wording; the demo
    # classifier is reserved for reference profiles.
    fb = artifacts.get("teammates.fallback_bank")
    if (
        manifest.profile_kind == "proposed_pilot"
        and fb is not None
        and isinstance(fb.artifact, FallbackBankArtifact)
        and fb.artifact.body.classifier == "demo_keyword_v1"
    ):
        problems.append(
            f"{manifest_id}: proposed_pilot cannot bind the demo fallback bank "
            "(pressure-confounding wording); use a neutral-classifier bank"
        )

    # Live binds approved material only (BA-99): ``reference`` marks demo/reference
    # wording that is bindable in preview/QA but is not an approval; fielding it live
    # requires an approved successor version.
    all_approved = all(a.artifact.status == "approved" for a in artifacts.values())
    decisions_resolved = all(d.status == "resolved" for d in manifest.decisions)
    payment = artifacts.get("outcomes.payment_policy")
    payment_ok = (
        payment is not None
        and isinstance(payment.artifact, PaymentPolicyArtifact)
        and payment.artifact.body.activatable
    )
    if mode == "live":
        if not all_approved:
            unapproved = sorted(
                a.key for a in artifacts.values() if a.artifact.status != "approved"
            )
            problems.append(f"{manifest_id}: live mode with unapproved artifacts {unapproved}")
        if not payment_ok:
            problems.append(f"{manifest_id}: live mode needs an activatable payment policy")
        if timing_mode != "faithful" or qa_overlay is not None:
            problems.append(f"{manifest_id}: live sessions always use faithful timing")

    overlay: StoredArtifact | None = None
    if timing_mode == "accelerated_qa":
        if mode != "qa":
            problems.append("accelerated timing is available only in qa mode")
        if qa_overlay is None:
            problems.append("accelerated timing needs an explicit qa_timing_overlay artifact")
        else:
            overlay = store.artifacts.get(qa_overlay.key)
            if overlay is None or not isinstance(overlay.artifact, QaTimingOverlayArtifact):
                problems.append(f"{qa_overlay.key} is not a qa_timing_overlay artifact")
                overlay = None
            elif mode not in overlay.artifact.body.allowed_modes:
                problems.append(f"{qa_overlay.key} is not allowed in mode {mode!r}")
    elif qa_overlay is not None:
        problems.append("faithful timing takes no qa overlay")

    if problems:
        raise BindError(problems)
    launch_eligible = (
        manifest.profile_kind == "proposed_pilot"
        and manifest.material_status == "approved"
        and "live" in manifest.modes_allowed
        and all_approved
        and decisions_resolved
        and payment_ok
    )
    return BoundManifest(
        manifest=manifest,
        manifest_sha256=stored.sha256,
        bundle_sha256=bundle,
        artifacts=dict(artifacts),
        mode=mode,
        timing_mode=timing_mode,
        qa_overlay=overlay,
        launch_eligible=launch_eligible,
    )
