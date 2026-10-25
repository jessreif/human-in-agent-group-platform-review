"""Pooling policy for cross-session descriptive summaries (BA-104).

``app.domain.compat.compatibility_key`` defines the strict pooling boundary:
two sessions may contribute to a common estimate only when every component —
study, batch, manifest/bundle, the participant-facing behavior digest,
generator, content, instruments, behavior, prompts, model, metrics, mode,
timing — matches. The dashboard's
per-partition tables already honor it; the pooled ``cohort_analysis`` block
did not (it pooled every eligible session by mechanism-and-arm pair).

This module makes the boundary enforceable in pooled summaries:

- Sessions are grouped by ``compatibility_key``. Unkeyed sessions form their
  own pools keyed on the stored partition fields — they never silently join
  a keyed pool.
- Crossing compatibility keys requires a recorded **equivalence** in
  ``POOLING_EQUIVALENCES``: an explicit, version-controlled record naming the
  session fields that must all be identical and documenting which compat
  components are permitted to differ. No record → no crossing.
- A mechanism contrast (ai vs scripted) can never exist inside a pool:
  ``manifest_id`` is a compat component, so two mechanisms never share a key,
  and no equivalence record may name ``manifest_sha256``-relaxed membership
  without being written down here.

Records live in code deliberately: they are versioned, reviewable, and carry
rationale — adding or widening one is an edit a reviewer sees, not a runtime
knob.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.domain.manifests.canonical import content_hash


@dataclass(frozen=True)
class PoolingEquivalence:
    """An explicit, recorded permission to pool sessions whose compatibility
    keys differ *only* on the components this record waives.

    ``match_fields`` are session columns that must ALL be non-empty; sessions
    are pool-mates under this record when their values on every match field
    are equal — everything not listed is a permitted difference and must be
    named in ``permitted_differences`` (the compat components it waives).
    ``match_fields`` must include ``manifest_sha256`` and ``bundle_sha256``;
    the whole-repo ``build_source_sha256`` may be waived only by a record that
    requires BOTH ``behavior_source_sha256`` — the participant-facing-scoped
    digest (``scripts/source_bundle.py --behavior``), the actual compat-key
    build component — AND ``policy_identity``, the canonical hash of the
    session's pinned ``generation_policy_pinned`` event *excluding* its
    ``build`` field. Waiving the release digest then covers only ops churn —
    admin, export, dashboard, harness and test code — which cannot alter what
    a session observed or recorded. Sessions whose ``behavior_source_sha256``
    is NULL (pinned before the scoped digest existed and not backfilled) can
    never join.

    ``pins`` further narrows when a record applies at all: each
    ``(field, allowed_values)`` pair requires the session's value to be one of
    the listed values, else the record is skipped and evaluation falls
    through to later records. Use pins for one-off rebuild boundaries so a
    waiver of a build digest cannot silently apply to unrelated deploys."""

    name: str
    match_fields: tuple[str, ...]
    permitted_differences: tuple[str, ...]
    rationale: str
    recorded: str
    pins: tuple[tuple[str, tuple[str, ...]], ...] = ()


POOLING_EQUIVALENCES: tuple[PoolingEquivalence, ...] = (
    # First matching record wins — pinned records precede the general ones:
    # this one is reachable only inside the two behavior digests it names, so
    # placing it first cannot reroute sessions on any other build.
    PoolingEquivalence(
        name="v13-same-cell-rebuild",
        match_fields=(
            "study_key",
            "batch_id",
            "manifest_sha256",
            "bundle_sha256",
            "policy_identity",
            "arm",
            "mode",
            "timing_mode",
        ),
        permitted_differences=("build_source_sha256", "behavior_source_sha256"),
        rationale=(
            "One-off rebuild boundary for study.live.v13: the cb-1.10 "
            "codebook/registry deploy (dashboard_analysis + codebook "
            "MeasureSpec/changelog — zero session-facing change) bumped "
            "behavior_source_sha256 f31f5733 → cc7034aa mid-round and split "
            "each refilled cell into two pools. Pins restrict the waiver to "
            "exactly those two digests; batch_id in match_fields confines it "
            "further to within-cell pairs on identical manifest, bundle and "
            "resolved generation policy. Deliberately NOT batch-waiving so "
            "cross-cell boundaries stand. Retire once the v13 cohort closes."
        ),
        recorded="2026-10-15 owner direction (v13 top-off rebuild split)",
        pins=(
            (
                "behavior_source_sha256",
                (
                    "f31f573313e6985789eeb24210ea0bbafadc33f36697a58d21cb98326e4ed915",
                    "cc7034aa9c20f04eaf21b33592f362766ac92003159d9b107b6640c53e9b3784",
                ),
            ),
        ),
    ),
    # This broader record precedes
    # ``same-build-cross-batch`` because it strictly subsumes it.
    PoolingEquivalence(
        name="same-materials-cross-build",
        match_fields=(
            "study_key",
            "manifest_sha256",
            "bundle_sha256",
            "behavior_source_sha256",
            "policy_identity",
            "mode",
            "timing_mode",
        ),
        permitted_differences=("batch_id", "build_source_sha256"),
        rationale=(
            "Mid-round deployments rebuild build_source_sha256 even when no "
            "participant-facing component changes, which fragments one "
            "fielding round into separate compatibility partitions (v12 "
            "split each of its four cells across the 04b212ce -> 3565ee60 "
            "deploy boundary). The waiver is therefore scoped to the release "
            "digest alone: behavior_source_sha256 (the compat-key build "
            "component — a digest over only participant-facing source) must "
            "still be identical, so a real lifecycle/prompt/generator change "
            "deployed mid-round keeps its own pool, while admin, export, "
            "dashboard, harness and test churn merges. manifest_sha256, "
            "bundle_sha256 and policy_identity additionally pin the resolved "
            "materials and generation config. Sessions predating the scoped "
            "digest carry NULL here and can never join (migration 0016 "
            "backfills only the builds recorded behavior-equivalent). "
            "Supersedes same-build-cross-batch for sessions carrying a "
            "pinned policy identity."
        ),
        recorded="2026-10-15 owner direction; behavior-scoped 2026-10-16",
    ),
    PoolingEquivalence(
        name="same-build-cross-batch",
        match_fields=(
            "study_key",
            "manifest_sha256",
            "bundle_sha256",
            "build_source_sha256",
            "policy_identity",
            "mode",
            "timing_mode",
        ),
        permitted_differences=("batch_id",),
        rationale=(
            "batch_id is an administrative enrollment container, not study "
            "content. Sessions on the identical manifest hash, content bundle "
            "hash and application build under the same mode and timing are the "
            "same build of the same materials — the study.live.v10 pilot "
            "enrolled its N=4 across two member batches of exactly this kind, "
            "and the N=30 cohort may top up the same way. Coverage is "
            "transitive: manifest_sha256 pins the generator, validator and "
            "every artifact slot; bundle_sha256 pins the resolved artifact "
            "contents (content, instruments, prompts, voices, fallbacks) and "
            "the bound clock factor; build_source_sha256 pins the prompt "
            "builder, claim checker, fallback and metric/coding constants. "
            "``policy_identity`` is the canonical hash of the session's "
            "pinned ``generation_policy_pinned`` event (minus its ``build`` "
            "field, a session column) — the provider/model digest, inference "
            "parameters, prompt builder, checker, validator and fallback "
            "versions actually resolved at allocation — so the env-resolved "
            "compat components are proven identical, not waived. Sessions "
            "without a pinned identity can never join."
        ),
        recorded="2026-10-12 BA-104 audit remediation",
    ),
)


def equivalence_records() -> list[dict[str, Any]]:
    """Serialisable record metadata for the analysis response."""
    return [
        {
            "name": r.name,
            "match_fields": list(r.match_fields),
            "permitted_differences": list(r.permitted_differences),
            "pins": [[f, list(v)] for f, v in r.pins],
            "rationale": r.rationale,
            "recorded": r.recorded,
        }
        for r in POOLING_EQUIVALENCES
    ]


def pool_membership(sess: Mapping[str, Any]) -> tuple[str, str | None]:
    """``(pool_id, equivalence_record_name_or_None)`` for one session.

    An equivalence record applies only when every match field is populated —
    a session missing ``manifest_sha256`` can never borrow the cross-batch
    waiver. Otherwise the pool is the session's own compatibility key; an
    unkeyed session pools only with identical unkeyed partitions."""
    for rec in POOLING_EQUIVALENCES:
        if all(sess.get(f) not in (None, "") for f in rec.match_fields) and all(
            sess.get(f) in allowed for f, allowed in rec.pins
        ):
            pid = "eq:" + content_hash(
                {"record": rec.name, "fields": {f: sess.get(f) for f in rec.match_fields}}
            )
            return pid, rec.name
    ck = sess.get("compatibility_key")
    if isinstance(ck, str) and ck:
        return f"ck:{ck}", None
    return (
        "unkeyed:"
        + "|".join(
            str(sess.get(f) or "")
            for f in (
                "study_key",
                "batch_id",
                "manifest_sha256",
                "bundle_sha256",
                "build_source_sha256",
                "mode",
                "timing_mode",
            )
        ),
        None,
    )


def pool_id_of(sess: Mapping[str, Any]) -> str:
    """The pool this session's estimates may merge into."""
    return pool_membership(sess)[0]


def pool_label(pool_id: str) -> str:
    """Human-readable pool tag for the UI."""
    if pool_id.startswith("eq:"):
        return "equivalence-merged"
    if pool_id.startswith("ck:"):
        return "compatibility-key"
    return "unkeyed"
