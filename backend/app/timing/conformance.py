"""Executable timing conformance matrix (INV-0210, spec §6.6).

One row per §6.6 dimension (25 taxonomy considerations plus the think-time registry dimension).
Each row names the manifest field that carries the baseline (or states that no switch exists),
the runtime enforcement point, the tests that demonstrate it and the owning decision. The
Markdown file ``docs/TIMING_CONFORMANCE_MATRIX.md`` is *generated* from this table plus the
bound profiles, and a test fails if the file is stale, a field path does not exist in every
profile, a cited test does not exist, or a row that needs an owner decision does not cite it.

Runtime enforcement that belongs to later work packages is named with its task ID; the matrix
records the owner rather than claiming the behavior already runs.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from app.domain.manifests.binding import BoundManifest

Summary = Callable[[dict[str, Any]], str]


def _get(data: Any, path: str) -> list[Any]:
    """Values at a dotted path; ``*`` matches every key of a mapping."""
    nodes = [data]
    for part in path.split("."):
        nxt: list[Any] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if part == "*":
                nxt.extend(node.values())
            elif part in node:
                nxt.append(node[part])
        nodes = nxt
    return nodes


def path_exists(data: dict[str, Any], path: str) -> bool:
    return bool(_get(data, path))


def _compact(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _field(path: str) -> Summary:
    def summary(m: dict[str, Any]) -> str:
        values = _get(m, path)
        return _compact(values[0] if len(values) == 1 else values)

    return summary


def _per_class(attr: str) -> Summary:
    def summary(m: dict[str, Any]) -> str:
        classes = m["timing"]["response_classes"]
        return ", ".join(f"{name}={_compact(c[attr])}" for name, c in sorted(classes.items()))

    return summary


def _mechanisms(m: dict[str, Any]) -> str:
    classes = m["timing"]["response_classes"]
    return ", ".join(f"{n}={c['mechanism']}" for n, c in sorted(classes.items()))


def _seat_overrides(m: dict[str, Any]) -> str:
    classes = m["timing"]["response_classes"]
    with_overrides = sorted(n for n, c in classes.items() if c["seat_overrides"])
    return "per-seat: " + (
        ", ".join(with_overrides) if with_overrides else "none (both seats equal)"
    )


def _delay_shapes(m: dict[str, Any]) -> str:
    out = []
    for name, c in sorted(m["timing"]["response_classes"].items()):
        shapes = []
        for d in c["delay"]:
            if d["family"] == "fixed":
                shapes.append(f"fixed {d['seconds']}s")
            else:
                t = d["truncation"]
                shapes.append(
                    f"LN {d['location']} {d['location_s']}s sigma {d['sigma']} "
                    f"{t['method']}[{t['lo_s']},{t['hi_s']}]"
                )
        out.append(f"{name}: " + " + ".join(shapes))
    return "; ".join(out)


def _channels(m: dict[str, Any]) -> str:
    return "; ".join(
        f"{p['name']} {p['from_active_s']}-{p['until_active_s']}s: {len(p['enabled'])} channels"
        for p in m["channels"]["phases"]
    )


def _schedule(m: dict[str, Any]) -> str:
    sr = m["timing"]["scripted_requests"]
    return f"active {sr['times_active_s']} {sr['targets']} seats={sr['seat_policy']}"


def _budget(m: dict[str, Any]) -> str:
    c = m["timing"]["checkin"]
    pause = "no pause" if c is None else f"check-in pause at {c['at_active_s']}s"
    return f"{m['timing']['active_budget_s']}s active, countdown visible, {pause}"


def _chatter(m: dict[str, Any]) -> str:
    tm = m["teammates"]
    ack = "acks" if tm["acknowledgments"] else "no acks"
    xt = "crosstalk" if tm["crosstalk"] else "no crosstalk"
    return f"{ack}, {xt}, social={tm['social_generation']}"


def _fixed(text: str) -> Summary:
    return lambda _m: text


@dataclass(frozen=True)
class Row:
    number: int
    dimension: str
    fields: tuple[str, ...]
    summary: Summary
    enforcement: str
    tests: tuple[str, ...]
    decisions: tuple[str, ...] = ()
    note: str = ""


_T = "backend/tests/"
ROWS: tuple[Row, ...] = (
    Row(
        1,
        "Latency by source",
        ("timing.response_classes",),
        _mechanisms,
        "app.timing.policy.resolve_policy -> ResolvedPolicy.class_policy(class, source); "
        "scheduler consumption INV-0604",
        (_T + "test_timing_policy.py::test_resolver_is_single_source_for_classes_and_channels",),
        ("D10",),
    ),
    Row(
        2,
        "Distribution/variance",
        ("timing.response_classes.*.delay",),
        _delay_shapes,
        "app.timing.distributions.sample (clamp vs reject, mean vs median); "
        "realized-distribution validation INV-0609",
        (
            _T + "test_timing_policy.py::test_clamp_and_reject_semantics",
            _T + "test_timing_policy.py::test_mean_vs_median_location",
        ),
        ("D10",),
    ),
    Row(
        3,
        "Per-member latency",
        ("timing.response_classes.*.seat_overrides",),
        _seat_overrides,
        "resolve_policy resolves every teammate class per seat; seat draws keyed per job "
        "(app.domain.schedules.plan_job_draws); balance audit INV-0708",
        (
            _T + "test_timing_policy.py::test_seat_overrides_resolve_per_seat",
            _T + "test_rng_streams.py::test_rating_seat_independent_of_request_sender",
        ),
        ("D07", "D10"),
    ),
    Row(
        4,
        "Trajectory",
        ("timing.trajectory",),
        _field("timing.trajectory"),
        "schema admits only 'constant'; any trigger-based change is rejected at bind",
        (_T + "test_manifest_contracts.py::test_unknown_and_dormant_fields_are_rejected",),
        ("D10",),
    ),
    Row(
        5,
        "Delivery failure",
        ("timing.delivery_failure_injection",),
        _field("timing.delivery_failure_injection"),
        "schema admits only 'none'; real incidents classified by INV-0612, deadline "
        "suppression by INV-0607",
        (_T + "test_manifest_contracts.py::test_unknown_and_dormant_fields_are_rejected",),
        ("D14",),
    ),
    Row(
        6,
        "Partial visibility",
        ("timing.visibility",),
        _field("timing.visibility"),
        "schema admits only simultaneous delivery; recipient projection INV-0505",
        (_T + "test_manifest_contracts.py::test_unknown_and_dormant_fields_are_rejected",),
    ),
    Row(
        7,
        "Ordering",
        ("timing.ordering",),
        _field("timing.ordering"),
        "resolved into ResolvedPolicy.controls; ordering/eligibility separation INV-0606",
        (_T + "test_timing_policy.py::test_controls_resolved_from_manifest",),
        ("D10",),
    ),
    Row(
        8,
        "Rendering",
        ("timing.response_classes.*.rendering",),
        _per_class("rendering"),
        "schema admits only complete delivery (streaming rejected at bind); render receipts "
        "INV-0807",
        (_T + "test_manifest_contracts.py::test_unknown_and_dormant_fields_are_rejected",),
    ),
    Row(
        9,
        "Presence",
        ("timing.response_classes.*.presence_cue",),
        _per_class("presence_cue"),
        "resolve_policy per class/source; indicator timing INV-0610",
        (_T + "test_timing_policy.py::test_resolver_is_single_source_for_classes_and_channels",),
        ("D07",),
    ),
    Row(
        10,
        "Delay attribution",
        ("timing.delay_attribution",),
        _field("timing.delay_attribution"),
        "schema admits only 'none'; no improvised excuses in script banks (INV-0703)",
        (_T + "test_manifest_contracts.py::test_unknown_and_dormant_fields_are_rejected",),
        ("D07",),
    ),
    Row(
        11,
        "AI referral",
        ("teammates.assistant_referral",),
        _field("teammates.assistant_referral"),
        "schema admits only 'none' (proposed default); assistant grounding INV-1003",
        (_T + "test_manifest_contracts.py::test_unknown_and_dormant_fields_are_rejected",),
        ("D07",),
    ),
    Row(
        12,
        "Blocking",
        ("channels.phases",),
        _channels,
        "ResolvedPolicy.channels_open(active_ms); command gating INV-0602/0903",
        (_T + "test_timing_policy.py::test_review_first_channels_closed_during_review",),
        ("D01",),
    ),
    Row(
        13,
        "Concurrency",
        ("timing.concurrency",),
        _field("timing.concurrency"),
        "resolved caps with visible rejection; enforcement INV-0608",
        (_T + "test_timing_policy.py::test_controls_resolved_from_manifest",),
        ("D10",),
    ),
    Row(
        14,
        "Time budget/visibility",
        ("timing.active_budget_s", "timing.countdown_visible", "timing.checkin"),
        _budget,
        "schema allows 300 or 360 active s per profile; active-time ledger INV-0601/0602",
        (_T + "test_manifest_contracts.py::test_all_profile_arm_combinations_bind",),
        ("D01",),
    ),
    Row(
        15,
        "Deadline structure",
        ("timing.deadline_structure", "timing.early_submission"),
        _field("timing.deadline_structure"),
        "single global freeze, no early-submission endpoint; atomic freeze INV-0607",
        (_T + "test_manifest_contracts.py::test_unknown_and_dormant_fields_are_rejected",),
        ("D01",),
    ),
    Row(
        16,
        "Warnings",
        ("timing.warnings",),
        _field("timing.warnings"),
        "app.timing.warnings (plan + commit decision); job execution INV-0603/0611",
        (
            _T + "test_warning_contract.py::test_warning_due_at_active_300_boundary",
            _T + "test_warning_contract.py::test_warning_identical_across_arms_and_profiles",
        ),
        ("D10",),
    ),
    Row(
        17,
        "Rate/cooldown",
        ("timing.rate_limits",),
        _field("timing.rate_limits"),
        "experimental budget 'unlimited' only; operational limit resolved; enforcement INV-0608",
        (_T + "test_timing_policy.py::test_controls_resolved_from_manifest",),
        ("D10",),
    ),
    Row(
        18,
        "Scheduled injections",
        ("timing.scripted_requests",),
        _schedule,
        "app.domain.schedules.plan_scripted_requests; job execution INV-0701",
        (_T + "test_timing_policy.py::test_scripted_schedules_baseline_and_review_first",),
        ("D01",),
    ),
    Row(
        19,
        "Think time",
        ("timing.response_classes.*.think",),
        _per_class("think"),
        "app.timing.mechanisms.plan_schedule keeps think, computation and typing separate",
        (_T + "test_timing_policy.py::test_concurrent_generation_overlaps_wait",),
        ("D10",),
    ),
    Row(
        20,
        "Two-stage replies",
        ("timing.two_stage",),
        _field("timing.two_stage"),
        "schema fixes: acknowledgment neither resets the schedule nor fulfils a request; "
        "paired-arm check INV-0706",
        (_T + "test_manifest_contracts.py::test_unknown_and_dormant_fields_are_rejected",),
    ),
    Row(
        21,
        "Proactive sharing",
        ("timing.proactive_sharing", "teammates.unsolicited_requests"),
        _field("timing.proactive_sharing"),
        "schema admits only 'none'; off-policy content audit INV-0703",
        (_T + "test_manifest_contracts.py::test_unknown_and_dormant_fields_are_rejected",),
        ("D07",),
    ),
    Row(
        22,
        "Contingent responsiveness",
        ("timing.contingent_responsiveness",),
        _field("timing.contingent_responsiveness"),
        "schema admits only 'none'; per-job keyed draws cannot depend on sharing "
        "(test); paired scripts INV-0706",
        (_T + "test_rng_streams.py::test_chat_volume_and_arm_do_not_change_draws",),
    ),
    Row(
        23,
        "Availability windows",
        ("timing.availability_windows",),
        _field("timing.availability_windows"),
        "schema admits only 'always_available'; outages are incidents (INV-0612)",
        (_T + "test_manifest_contracts.py::test_unknown_and_dormant_fields_are_rejected",),
        ("D14",),
    ),
    Row(
        24,
        "Chatter",
        ("teammates.crosstalk", "teammates.acknowledgments"),
        _chatter,
        "resolved teammate policy; seeded ack/crosstalk jobs INV-0702",
        (_T + "test_manifest_contracts.py::test_paired_arms_differ_only_in_manipulation",),
        ("D07",),
    ),
    Row(
        25,
        "Length/typing",
        ("timing.response_classes.*.typing",),
        _per_class("typing"),
        "app.timing.mechanisms.typing_ms; length finalization INV-0605",
        (_T + "test_timing_policy.py::test_demo_social_additive_adds_model_latency",),
        ("D10",),
    ),
    Row(
        26,
        "Human plausibility",
        (),
        _fixed("no runtime switch"),
        "unsupported as a manifest field: plausibility comes from usability evidence (INV-1609)",
        (_T + "test_conformance_matrix.py::test_matrix_is_complete_and_current",),
        ("D13",),
        note="No imported universal delay target; separate usability work feeds D10.",
    ),
)

REQUIRED_DECISIONS = {
    "AI referral": "D07",
    "Presence": "D07",
    "Warnings": "D10",
    "Distribution/variance": "D10",
}


def render_markdown(bound: Iterable[BoundManifest]) -> str:
    profiles = list(bound)
    dumps = {b.manifest_id: b.manifest.model_dump(mode="json") for b in profiles}
    lines = [
        "# Timing conformance matrix (INV-0210)",
        "",
        "Generated by `uv run python -m app.domain.manifests.cli docs --write` from "
        "`backend/app/timing/conformance.py` and the published profiles; "
        "`tests/test_conformance_matrix.py` fails when this file is stale. "
        'One row per spec §6.6 dimension. "Rejected at bind" means the manifest schema '
        "admits only the baseline value, so a dormant treatment cannot be stored without "
        "behavior (INV-0206). Enforcement points that name a later task are owned there and "
        "are not yet runtime evidence.",
        "",
        "| # | Dimension | Manifest field | Baseline per profile | Runtime enforcement | "
        "Tests | Decision |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in ROWS:
        field = ", ".join(f"`{f}`" for f in row.fields) or "unsupported (no switch)"
        baseline = "<br>".join(
            f"**{mid}**: {row.summary(dump)}".replace("|", "\\|") for mid, dump in dumps.items()
        )
        tests = "<br>".join(f"`{t.removeprefix(_T)}`" for t in row.tests)
        decisions = ", ".join(row.decisions) or "-"
        enforcement = row.enforcement + (f" {row.note}" if row.note else "")
        lines.append(
            f"| {row.number} | {row.dimension} | {field} | {baseline} | {enforcement} | "
            f"{tests} | {decisions} |"
        )
    return "\n".join(lines) + "\n"
