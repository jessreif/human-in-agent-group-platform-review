"""Typed domain vocabulary over the schema's value sets (audit M4).

Kept out of ``schema.py`` so that file stays byte-identical to its migration
snapshot (``test_latest_snapshot_equals_current_schema``): these are Python
types and named constants, not DDL.
"""

from __future__ import annotations

from typing import Literal

from app.db.schema import DATA_USE_CHOICES

DataUseChoice = Literal["use_all", "use_no_free_text", "withdraw_all"]
USE_ALL: DataUseChoice = "use_all"
# Choices that keep a completed session in the research set (withdraw_all does not).
ELIGIBLE_DATA_USE: tuple[DataUseChoice, ...] = ("use_all", "use_no_free_text")

assert set(DATA_USE_CHOICES) == {"use_all", "use_no_free_text", "withdraw_all"}, (
    "DataUseChoice drifted from schema.DATA_USE_CHOICES"
)
