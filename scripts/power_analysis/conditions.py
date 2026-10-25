"""Supported condition cells for the existing single-cell power models."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any


def require_single_condition(
    rows: Iterable[Mapping[str, Any]],
) -> list[tuple[str, int, int | None]]:
    cells = set()
    for row in rows:
        budget = row.get("intel_budget")
        normalized = None if budget is None or math.isnan(float(budget)) else int(budget)
        cells.add((str(row["crate_overlap"]), int(row["n_crates"]), normalized))
    ordered = sorted(cells, key=lambda cell: (cell[0], cell[1], -1 if cell[2] is None else cell[2]))
    if len(ordered) > 1:
        raise ValueError(
            f"mixed crate-condition cells {ordered}: condition-aware models are not implemented; "
            "condition_spec cannot authorize pooling. Run one condition cell at a time."
        )
    return ordered
