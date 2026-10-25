"""INV-0212: synthetic-run registry keyed by compatibility key + seed + replicate."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.measurement.synthetic_registry import (
    AttemptStatus,
    CaseKey,
    JsonlEventStore,
    MemoryEventStore,
    RegistryError,
    SyntheticRunRegistry,
)

CK = "ck1:" + "a" * 64
OTHER_CK = "ck1:" + "b" * 64


def test_failed_low_replicate_never_causes_completed_replicate_to_rerun() -> None:
    """hidden-bench _count_done lesson: counting finished rows would rerun replicate 3."""
    reg = SyntheticRunRegistry(MemoryEventStore())
    cases = reg.pending(CK, ["s1"], 3)
    assert [c.replicate for c in cases] == [1, 2, 3]
    outcomes = {1: False, 2: True, 3: True}
    for case in cases:
        attempt = reg.start(case)
        reg.finish(case, attempt, ok=outcomes[case.replicate])
    pending = reg.pending(CK, ["s1"], 3)
    assert pending == [CaseKey(CK, "s1", 1)]  # exactly the failed one, by identity
    with pytest.raises(RegistryError, match="never rerun"):
        reg.start(CaseKey(CK, "s1", 3))
    retry = reg.start(pending[0])
    assert retry == 2
    assert reg.state(pending[0]).attempts[-1].parent_attempt == 1
    reg.finish(pending[0], retry, ok=True)
    assert reg.pending(CK, ["s1"], 3) == []
    assert reg.completed(CK) == [CaseKey(CK, "s1", r) for r in (1, 2, 3)]


def test_crash_resumes_the_exact_unfinished_case_with_lineage() -> None:
    reg = SyntheticRunRegistry(MemoryEventStore())
    case = CaseKey(CK, "s9", 2)
    first = reg.start(case)  # process dies before finish
    assert reg.state(case).open_attempt == first
    assert reg.pending(CK, ["s9"], 2) == [CaseKey(CK, "s9", 1), case]
    resumed = reg.start(case)
    assert resumed == first + 1
    assert reg.state(case).attempts[-1].parent_attempt == first
    reg.finish(case, resumed, ok=True)
    with pytest.raises(RegistryError):
        reg.finish(case, resumed, ok=True)


def test_attempts_cannot_be_finished_twice_or_invented() -> None:
    reg = SyntheticRunRegistry(MemoryEventStore())
    case = CaseKey(CK, "s1", 1)
    with pytest.raises(RegistryError, match="no attempt"):
        reg.finish(case, 1, ok=True)
    attempt = reg.start(case)
    reg.finish(case, attempt, ok=False)
    with pytest.raises(RegistryError, match="already finished"):
        reg.finish(case, attempt, ok=True)


def test_compatibility_keys_separate_runs() -> None:
    reg = SyntheticRunRegistry(MemoryEventStore())
    case = CaseKey(CK, "s1", 1)
    reg.finish(case, reg.start(case), ok=True)
    assert reg.pending(OTHER_CK, ["s1"], 1) == [CaseKey(OTHER_CK, "s1", 1)]
    with pytest.raises(ValueError, match="compatibility key"):
        CaseKey("tp_demo_v1", "s1", 1)
    with pytest.raises(ValueError, match="numbered from 1"):
        CaseKey(CK, "s1", 0)


def test_jsonl_store_is_durable_across_processes(tmp_path: Path) -> None:
    path = tmp_path / "registry" / "runs.jsonl"
    reg = SyntheticRunRegistry(JsonlEventStore(path))
    case = CaseKey(CK, "s1", 1)
    reg.finish(case, reg.start(case), ok=True)
    reopened = SyntheticRunRegistry(JsonlEventStore(path))
    assert reopened.state(case).done
    assert [a.status for a in reopened.state(case).attempts] == [
        AttemptStatus.STARTED,
        AttemptStatus.SUCCEEDED,
    ]
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
