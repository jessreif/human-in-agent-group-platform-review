"""Full compatibility keys (INV-0208, spec §4.2, hidden-bench HB-08).

A compatibility key identifies everything that must match before two sessions or synthetic
runs may be pooled: study, batch, manifest and content bundle, the participant-facing
behavior digest, generator, content and instrument artifacts, the executable behavior
policy, prompts/validator, the provider/model digest and inference parameters, metric and
coding versions, and the timing mode. Equal model names or arm labels are never sufficient.

The build component is the *participant-facing-scoped* digest
(``scripts/source_bundle.py --behavior`` → ``INVESTIGATORS_BEHAVIOR_SOURCE_SHA256``), not the
whole-repo release checksum: dashboard, export, admin, harness and test code cannot alter
what a session observed or recorded, so their churn must not split an analytic partition.
The full ``build_source_sha256`` remains a session column for release provenance.

The arm is deliberately *not* a component: both arms of a profile share one key, and their
permitted difference is checked separately by the paired-arm diff (``manifests/diff.py``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.domain.manifests.binding import BoundManifest
from app.domain.manifests.canonical import content_hash
from app.timing.policy import ResolvedPolicy
from app.version import UNSET

KEY_PREFIX = "ck1:"
UNRESOLVED_MODEL = "unresolved:D05"
_CONTENT_SLOTS = ("timing.", "teammates.voice_bank", "teammates.fallback_bank", "manipulation.")
_INSTRUMENT_SLOTS = ("instruments.",)
_PROMPT_SLOTS = ("teammates.social_prompt", "assistant.prompt")


class CompatibilityError(ValueError):
    pass


@dataclass(frozen=True)
class CompatibilityKey:
    components: Mapping[str, str]

    @property
    def key(self) -> str:
        return KEY_PREFIX + content_hash(dict(self.components))

    def differing(self, other: CompatibilityKey) -> list[str]:
        names = set(self.components) | set(other.components)
        return sorted(n for n in names if self.components.get(n) != other.components.get(n))


def _slot_hash(bound: BoundManifest, prefixes: tuple[str, ...]) -> str:
    chosen = {
        slot: [a.key, a.sha256]
        for slot, a in sorted(bound.artifacts.items())
        if slot.startswith(prefixes)
        and not slot.startswith("condition_set.")  # arm copy belongs to the manipulation hash
    }
    return content_hash(chosen)


def compatibility_key(
    bound: BoundManifest,
    policy: ResolvedPolicy,
    *,
    batch_id: str,
    behavior_source_sha256: str,
    provider_model_digest: str = UNRESOLVED_MODEL,
    inference_params: Mapping[str, Any] | None = None,
    metric_version: str = "metrics.unpublished",
    coding_version: str = "coding.unavailable",
) -> CompatibilityKey:
    if policy.manifest_id != bound.manifest_id:
        raise CompatibilityError("policy was resolved from a different manifest")
    if bound.mode == "live" and behavior_source_sha256 == UNSET:
        raise CompatibilityError("live sessions need a pinned behavior checksum")
    if bound.mode == "live" and provider_model_digest == UNRESOLVED_MODEL:
        raise CompatibilityError("live sessions need a resolved provider/model digest (D05)")
    m = bound.manifest
    # BA-41: the grounded prompt builder is part of the pooling boundary — a prompt
    # change (e.g. the off-task redirect) must never pool sessions across versions.
    # Lazy import: services -> domain layering keeps this constant out of module import.
    from app.services.generation_policy import FALLBACK_VERSION, INFERENCE_POLICY
    from app.services.output_claims import CHECKER_VERSION
    from app.services.prompts import PROMPT_BUILDER_VERSION

    return CompatibilityKey(
        {
            "study_key": m.study_key,
            "batch_id": batch_id,
            "manifest_id": m.manifest_id,
            "manifest_sha256": bound.manifest_sha256,
            "bundle_sha256": bound.bundle_sha256,
            "behavior_source_sha256": behavior_source_sha256,
            "generator": f"{m.task.generator}#difficulty{m.task.difficulty_version}",
            "content": _slot_hash(bound, _CONTENT_SLOTS),
            "instruments": _slot_hash(bound, _INSTRUMENT_SLOTS),
            "behavior_policy": policy.behavior_policy_hash,
            "prompts": _slot_hash(bound, _PROMPT_SLOTS),
            "prompt_builder": PROMPT_BUILDER_VERSION,
            "claim_checker": CHECKER_VERSION,
            "fallback_policy": FALLBACK_VERSION,
            "validator": m.assistant.validator_version,
            "provider_model": provider_model_digest,
            "inference_params": content_hash(
                dict(INFERENCE_POLICY if inference_params is None else inference_params)
            ),
            "metrics": metric_version,
            "coding": coding_version,
            "mode": bound.mode,
            "timing_mode": bound.timing_mode,
            "clock_speed_factor": str(bound.clock_speed_factor),
        }
    )
