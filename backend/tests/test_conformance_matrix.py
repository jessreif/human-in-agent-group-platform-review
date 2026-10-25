"""INV-0210: the 26-row timing conformance matrix is complete, owned and current."""

from __future__ import annotations

import re

from app.domain.manifests import cli
from app.timing.conformance import REQUIRED_DECISIONS, ROWS, path_exists
from tests.wp02 import PROFILES, REPO, store

SPEC_DIMENSIONS = (
    "Latency by source",
    "Distribution/variance",
    "Per-member latency",
    "Trajectory",
    "Delivery failure",
    "Partial visibility",
    "Ordering",
    "Rendering",
    "Presence",
    "Delay attribution",
    "AI referral",
    "Blocking",
    "Concurrency",
    "Time budget/visibility",
    "Deadline structure",
    "Warnings",
    "Rate/cooldown",
    "Scheduled injections",
    "Think time",
    "Two-stage replies",
    "Proactive sharing",
    "Contingent responsiveness",
    "Availability windows",
    "Chatter",
    "Length/typing",
    "Human plausibility",
)


def test_matrix_is_complete_and_current() -> None:
    assert [r.number for r in ROWS] == list(range(1, 27))
    assert tuple(r.dimension for r in ROWS) == SPEC_DIMENSIONS
    spec = (REPO / "docs" / "DESIGN_SPEC.md").read_text(encoding="utf-8")
    for dimension in SPEC_DIMENSIONS:
        assert f"| {dimension} |" in spec, dimension
    dumps = [store().manifests[m].manifest.model_dump(mode="json") for m in PROFILES]
    for row in ROWS:
        assert row.enforcement and row.tests, row.dimension
        for field in row.fields:
            for dump in dumps:
                assert path_exists(dump, field), (row.dimension, field)
        for test in row.tests:
            file, _, name = test.partition("::")
            source = (REPO / file).read_text(encoding="utf-8")
            assert re.search(rf"^def {re.escape(name)}\(", source, re.M), test
    by_name = {r.dimension: r for r in ROWS}
    for dimension, decision in REQUIRED_DECISIONS.items():
        assert decision in by_name[dimension].decisions, dimension
    docs = cli.generated_docs(store())
    for rel, text in docs.items():
        assert (REPO / rel).read_text(encoding="utf-8") == text, rel
