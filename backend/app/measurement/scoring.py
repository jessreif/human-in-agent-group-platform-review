"""Answer scoring (INV-1102; spec §9.1 accuracy).

Scoring reads the *frozen* ``answer_final`` rows plus the world truth pinned in the
immutable ``world_snapshot`` — it has **no authority to revise answers**: the functions
here accept only a read path (``SessionRows``/``Connection`` used for SELECT) and emit
values; the integration suite proves this by scoring through the read-only
``research`` role and by snapshotting the answer tables before and after.

Semantics (spec §9.1 Accuracy):
- correct assigned crates / 4, own-file correct / 2, team-dependent correct / 2;
- a missing answer is retained as missing and counts as incorrect in the displayed
  total; answered-only accuracy is reported separately and is ``undefined_denominator``
  when nothing was answered;
- crates never assigned carry no accuracy contribution;
- a session whose answers were never frozen (abandoned/withdrawn/technical stop before
  the deadline) reports ``missing_data`` per unanswered assigned crate — the measure
  distinguishes "answered wrong" from "never reached freezing".
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import Connection

from app.measurement.results import Measure, ratio_measure
from app.measurement.rows import SessionRows, load_session_rows

# Version stamp for this scoring definition; any change to the truth comparison,
# missing-handling or denominators must bump it (codebook INV-1101).
SCORING_VERSION = "wp11_score.v1"

CrateState = Literal["answered_correct", "answered_incorrect", "unfrozen", "missing"]


@dataclass(frozen=True)
class CrateScore:
    crate: int
    assigned: bool
    role: str | None  # own / team / other
    truth: str | None
    final_location: str | None
    final_revision: int | None
    state: CrateState
    correct: bool | None  # None when missing/unfrozen (not scored as an answer)


@dataclass(frozen=True)
class SessionScore:
    scoring_version: str
    per_crate: tuple[CrateScore, ...]
    correct_total: Measure  # correct / 4 assigned
    correct_ownfile: Measure  # correct / 2 own-file assigned
    correct_teamdep: Measure  # correct / 2 team-dependent assigned
    accuracy_answered_only: Measure  # correct / answered assigned
    answered_count: int
    missing_count: int
    unfrozen_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "scoring_version": self.scoring_version,
            "per_crate": [c.__dict__ | {"correct": c.correct} for c in self.per_crate],
            "correct_total": self.correct_total.as_dict(),
            "correct_ownfile": self.correct_ownfile.as_dict(),
            "correct_teamdep": self.correct_teamdep.as_dict(),
            "accuracy_answered_only": self.accuracy_answered_only.as_dict(),
            "answered_count": self.answered_count,
            "missing_count": self.missing_count,
            "unfrozen_count": self.unfrozen_count,
        }


def score_rows(rows: SessionRows) -> SessionScore:
    """Score one session from its committed-row projection. Pure; performs no I/O."""
    truth = rows.truth
    roles = rows.role_of
    assigned = rows.assigned_crates
    finals = {int(f["crate"]): f for f in rows.answer_finals}
    revised = {int(r["crate"]) for r in rows.answer_revisions}

    per_crate: list[CrateScore] = []
    for crate in sorted(rows.truth):
        final = finals.get(crate)
        is_assigned = crate in assigned
        if final is not None:
            correct = str(final["location_id"]) == truth.get(crate)
            state: CrateState = "answered_correct" if correct else "answered_incorrect"
            scored: bool | None = correct
        elif crate in revised:
            state, scored = "unfrozen", None
        else:
            state, scored = "missing", None
        per_crate.append(
            CrateScore(
                crate=crate,
                assigned=is_assigned,
                role=roles.get(crate),
                truth=truth.get(crate),
                final_location=None if final is None else str(final["location_id"]),
                final_revision=None if final is None else int(final["revision"]),
                state=state,
                correct=scored if is_assigned else None,
            )
        )

    def _correct(pred: Any) -> int:
        return sum(
            1 for c in per_crate if c.assigned and c.correct is True and (pred is None or pred(c))
        )

    n_answered = sum(1 for c in per_crate if c.assigned and c.correct is not None)
    n_missing = sum(1 for c in per_crate if c.assigned and c.state == "missing")
    n_unfrozen = sum(1 for c in per_crate if c.assigned and c.state == "unfrozen")
    n_ownfile = sum(1 for c in per_crate if c.assigned and c.role == "own")
    n_teamdep = sum(1 for c in per_crate if c.assigned and c.role == "team")

    return SessionScore(
        scoring_version=SCORING_VERSION,
        per_crate=tuple(per_crate),
        # Displayed totals count missing/unfrozen assigned crates as incorrect.
        correct_total=ratio_measure(
            "accuracy.correct_total",
            _correct(None),
            len(assigned),
            denominator_kind="assigned_crates",
            missing_rule="missing_counts_incorrect",
        ),
        correct_ownfile=ratio_measure(
            "accuracy.correct_ownfile",
            _correct(lambda c: c.role == "own"),
            n_ownfile,
            denominator_kind="ownfile_crates",
            missing_rule="missing_counts_incorrect",
        ),
        correct_teamdep=ratio_measure(
            "accuracy.correct_teamdep",
            _correct(lambda c: c.role == "team"),
            n_teamdep,
            denominator_kind="teamdep_crates",
            missing_rule="missing_counts_incorrect",
        ),
        accuracy_answered_only=ratio_measure(
            "accuracy.answered_only",
            _correct(None),
            n_answered,
            denominator_kind="answered_assigned_crates",
            missing_rule="undefined_when_none_answered",
        ),
        answered_count=n_answered,
        missing_count=n_missing,
        unfrozen_count=n_unfrozen,
    )


def score_session(conn: Connection, session_id: uuid.UUID) -> SessionScore:
    """Load committed rows and score. SELECTs only — safe on the read-only research
    role, which is the proof that scoring can never revise answers."""
    return score_rows(load_session_rows(conn, session_id))


def score_snapshot(
    finals: Mapping[int, str],
    truth: Mapping[int, str],
    role_of: Mapping[int, str],
    assigned: tuple[int, ...],
    revised: frozenset[int] = frozenset(),
) -> SessionScore:
    """Pure convenience for callers that already hold the four facts per crate
    (used by unit tests and by future export joins)."""
    rows = SessionRows(
        session_id=uuid.UUID(int=0),
        world_content={
            "assigned": list(assigned),
            "role_of": {str(k): v for k, v in role_of.items()},
            "crates": [{"num": n, "truth": t} for n, t in truth.items()],
            "participant_decisive": {},
        },
        answer_finals=tuple(
            {
                "crate": c,
                "revision": 1,
                "location_id": loc,
                "finalized_seq": 1,
            }
            for c, loc in finals.items()
        ),
        answer_revisions=tuple(
            {"crate": c, "revision": 1, "location_id": "", "accepted_seq": 1} for c in revised
        ),
    )
    return score_rows(rows)
