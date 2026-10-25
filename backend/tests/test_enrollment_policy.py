"""Completion-target configuration is explicit, bounded, and versioned."""

import pytest
from pydantic import ValidationError

from app.services.allocation import GroupCell
from app.services.enrollment import (
    EnrollmentError,
    EnrollmentTarget,
    load_policy,
    make_policy,
    validate_cells,
)


@pytest.mark.parametrize("target", [0, -1, True, 4.5, "4", 10001])
def test_target_is_a_bounded_strict_integer(target: object) -> None:
    with pytest.raises(ValidationError):
        EnrollmentTarget.model_validate(
            {"platform": "prolific", "target_completers_per_cell": target}
        )


def test_policy_freezes_exact_cells_and_rejects_configuration_drift() -> None:
    cells = [GroupCell("a", "first", "nopress"), GroupCell("b", "second", "nopress")]
    policy = make_policy(EnrollmentTarget(platform="prolific", target_completers_per_cell=4), cells)
    assert (
        load_policy({"mode": "live", "enrollment_policy": policy.model_dump(mode="json")}) == policy
    )
    validate_cells(policy, cells)
    with pytest.raises(EnrollmentError, match="quota_configuration_changed"):
        validate_cells(policy, [GroupCell("a", "first", "high"), cells[1]])
    with pytest.raises(ValidationError, match="duplicate policy cell"):
        make_policy(policy, [cells[0], cells[0]])


@pytest.mark.parametrize("raw", [{}, {"version": 2}, [], "unrecognized"])
def test_malformed_stored_policies_fail_closed(raw: object) -> None:
    with pytest.raises(EnrollmentError, match="quota_policy_invalid"):
        load_policy({"mode": "live", "enrollment_policy": raw})


def test_absent_policy_preserves_existing_group_behavior() -> None:
    assert load_policy({"mode": "live"}) is None
    assert load_policy({"mode": "qa", "enrollment_policy": None}) is None
