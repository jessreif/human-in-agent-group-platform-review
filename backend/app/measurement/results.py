"""Core result types for the WP11 measurement layer (INV-1101..1105; spec §9).

Every measure is a *pure function of committed database rows* — nothing here ever reads
client-reported timing and nothing writes back. Each measure reports:

- ``state``: ``measured`` when a value exists; ``undefined_denominator`` when the declared
  denominator is zero or absent (the measure does not exist for this session — it is not 0);
  ``missing_data`` when the required committed records are absent (kept missing, never
  imputed); ``not_applicable`` when the construct is structurally unavailable for this
  study/session (e.g. pressure check-in on a PE profile).
- ``denominator`` + ``denominator_kind``: what the measure is a share of (participants,
  crates, records, requests, assertions, transitions, ...), per spec §9.3.
- ``missing_rule``: the codebook rule name describing how missing/undefined states are
  produced, so a zero in an export can never silently be a missing value.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class MeasureState(StrEnum):
    MEASURED = "measured"
    UNDEFINED_DENOMINATOR = "undefined_denominator"
    MISSING_DATA = "missing_data"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class Measure:
    """One named measure value with its state, denominator and missing-data rule."""

    name: str
    state: MeasureState
    value: int | float | str | bool | None
    numerator: int | None = None
    denominator: int | None = None
    denominator_kind: str = ""
    missing_rule: str = ""

    @property
    def measured(self) -> bool:
        return self.state is MeasureState.MEASURED

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "value": self.value,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "denominator_kind": self.denominator_kind,
            "missing_rule": self.missing_rule,
        }


def count_measure(
    name: str,
    n: int,
    *,
    unit: str,
    missing_rule: str,
) -> Measure:
    """A plain count of committed records (denominator = the records themselves)."""
    return Measure(
        name=name,
        state=MeasureState.MEASURED,
        value=n,
        numerator=n,
        denominator=n,
        denominator_kind=unit,
        missing_rule=missing_rule,
    )


def ratio_measure(
    name: str,
    numerator: int,
    denominator: int | None,
    *,
    denominator_kind: str,
    missing_rule: str,
) -> Measure:
    """A proportion. A zero/None denominator yields ``undefined_denominator`` — an
    explicit undefined result, never 0 and never a crash (spec §9.1 seeking allocation)."""
    if denominator is None or denominator == 0:
        return Measure(
            name=name,
            state=MeasureState.UNDEFINED_DENOMINATOR,
            value=None,
            numerator=numerator,
            denominator=denominator,
            denominator_kind=denominator_kind,
            missing_rule=missing_rule,
        )
    return Measure(
        name=name,
        state=MeasureState.MEASURED,
        value=numerator / denominator,
        numerator=numerator,
        denominator=denominator,
        denominator_kind=denominator_kind,
        missing_rule=missing_rule,
    )


def optional_measure(
    name: str,
    value: int | float | str | None,
    *,
    missing_rule: str,
    unit: str = "",
) -> Measure:
    """A scalar that is ``missing_data`` when the underlying committed record is absent."""
    return Measure(
        name=name,
        state=MeasureState.MISSING_DATA if value is None else MeasureState.MEASURED,
        value=value,
        denominator_kind=unit,
        missing_rule=missing_rule,
    )
