"""Mechanical, nonvacuous release-evidence gate. Never changes enrollment."""

from __future__ import annotations

from collections.abc import Mapping
from itertools import product
from typing import Any

from app.services.generation_policy import identity


def validate_evidence(panel: Mapping[str, Any], evidence: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if evidence.get("panel_version") != panel["version"]:
        errors.append("panel_version")
    build = evidence.get("build", "")
    model = evidence.get("model", "")
    if len(build) != 64 or "@sha256:" not in model:
        errors.append("release_identity")
    if evidence.get("policy") != identity(build, model):
        errors.append("policy_identity")
    rows = evidence.get("results", [])
    indexed = {(r.get("profile"), r.get("arm"), r.get("seed"), r.get("probe")): r for r in rows}
    if len(indexed) != len(rows):
        errors.append("duplicate_rows")
    expected = set()
    for profile, arm, seed, probe in product(
        panel["profiles"], panel["arms"], panel["seeds"], panel["probes"]
    ):
        key = (profile, arm, seed, probe["id"])
        expected.add(key)
        row = indexed.get(key)
        label = f"{profile}/{arm}/{probe['id']}"
        if row is None:
            errors.append(f"missing:{label}")
            continue
        route = probe.get("route", panel["profiles"][profile])
        if (
            row.get("route") != route
            or row.get("scope") != probe["scope"]
            or row.get("adjudication") != "correct"
            or not row.get("displayed_output_id")
            or not row.get("message_id")
            or not row.get("render_receipt")
            or not row.get("snapshot_id")
            or not row.get("authorized_record_ids")
            or row.get("cohort") not in ("qa", "preview")
            or row.get("unexplained_gaps") != 0
        ):
            errors.append(f"incomplete_or_incorrect:{label}")
        if route == "generated":
            if (
                row.get("status") != "succeeded"
                or row.get("fallback") != "none"
                or not row.get("call_id")
                or row.get("supported_claims", 0) < 1
                or row.get("violations") != []
                or row.get("model") != model
                or not row.get("rendered_record_ids")
            ):
                errors.append(f"no_generated_delivery:{label}")
        elif row.get("call_id") is not None or row.get("provider_dispatched") is not False:
            errors.append(f"unexpected_dispatch:{label}")
    if set(indexed) != expected:
        errors.append("panel_coverage")
    regressions = evidence.get("regressions", {})
    for case in panel["required_regressions"]:
        if regressions.get(case) != "pass":
            errors.append(f"regression:{case}")
    return errors
