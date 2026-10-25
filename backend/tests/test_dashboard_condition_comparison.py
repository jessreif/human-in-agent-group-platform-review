"""``dashboard_analysis._condition_comparison`` — side-by-side pool columns.

The Findings comparison table gets one column per experimental condition —
a (manifest, arm) pair labeled manifest (arm) — plus one overall column
over the whole eligible cohort. This pins the contract: columns follow
condition membership regardless of compat-pool/build splits, and every cell
is a plain descriptive.
"""

from __future__ import annotations

import uuid

from app.services import dashboard_analysis


def _sess(manifest: str, ck: str, arm: str = "high") -> dict:
    return {
        "session_id": uuid.uuid4(),
        "compatibility_key": ck,
        "manifest_id": manifest,
        "arm": arm,
    }


def _scalar(value: float) -> dict:
    return {"value": value, "state": "measured", "denominator": None}


_DEFS = {"accuracy.correct_ownfile": {"unit": "ratio", "description": "own-file accuracy"}}


def test_columns_follow_conditions_and_label_manifest_arm() -> None:
    sessions = [
        _sess("tp_ai_ovnone_v3", "a" * 8),
        _sess("tp_ai_ovnone_v3", "a" * 8),
        _sess("tp_ai_ovsome_thin_v9", "b" * 8, arm="mixed"),
    ]
    scalars = {
        sessions[0]["session_id"]: {"accuracy.correct_ownfile": _scalar(1.0)},
        sessions[1]["session_id"]: {"accuracy.correct_ownfile": _scalar(0.0)},
        sessions[2]["session_id"]: {"accuracy.correct_ownfile": _scalar(0.5)},
    }
    out = dashboard_analysis._condition_comparison(sessions, scalars, _DEFS)

    assert out["n_sessions"] == 3
    labels = [c["label"] for c in out["columns"]]
    assert labels == ["ovnone v3 (high)", "ovsome_thin v9 (mixed)"]
    by_label = {c["label"]: c for c in out["columns"]}
    assert by_label["ovnone v3 (high)"]["n_sessions"] == 2
    assert by_label["ovsome_thin v9 (mixed)"]["manifests"] == ["tp_ai_ovsome_thin_v9"]
    assert by_label["ovsome_thin v9 (mixed)"]["mechanisms"] == ["ai"]
    # Condition columns carry the compat pool ids they span for visibility filters.
    assert by_label["ovnone v3 (high)"]["pool_ids"]

    (row,) = out["rows"]
    assert row["measure"] == "accuracy.correct_ownfile"
    assert row["overall"]["n"] == 3
    assert row["overall"]["mean"] == 0.5
    ov_cell = row["cells"][by_label["ovnone v3 (high)"]["pool_id"]]
    assert ov_cell["n"] == 2 and ov_cell["mean"] == 0.5
    some_cell = row["cells"][by_label["ovsome_thin v9 (mixed)"]["pool_id"]]
    assert some_cell["n"] == 1 and some_cell["mean"] == 0.5


def test_build_checksum_split_stays_one_condition_column() -> None:
    # v15 wave-1 regression: mid-fielding deploys stamp a new build checksum into
    # compatibility_key — two compat pools, same (manifest, arm). The comparison
    # must still show ONE condition column, not a duplicated manifest label.
    sessions = [
        _sess("tp_ai_ovsome_thin_v9", "a" * 8),
        _sess("tp_ai_ovsome_thin_v9", "b" * 8),  # post-deploy compat key, same arm
        _sess("tp_ai_ovsome_thin_v9", "c" * 8, arm="mixed"),
    ]
    scalars = {
        s["session_id"]: {"accuracy.correct_ownfile": _scalar(1.0)} for s in sessions
    }
    out = dashboard_analysis._condition_comparison(sessions, scalars, _DEFS)
    labels = sorted(c["label"] for c in out["columns"])
    assert labels == ["ovsome_thin v9 (high)", "ovsome_thin v9 (mixed)"]
    by_label = {c["label"]: c for c in out["columns"]}
    assert by_label["ovsome_thin v9 (high)"]["n_sessions"] == 2
    assert len(by_label["ovsome_thin v9 (high)"]["pool_ids"]) == 2


def test_unmeasured_sessions_count_sessions_not_values() -> None:
    sessions = [_sess("tp_ai_ovnone_v3", "c" * 8), _sess("tp_ai_ovnone_v3", "c" * 8)]
    scalars = {
        sessions[0]["session_id"]: {"accuracy.correct_ownfile": _scalar(1.0)},
        sessions[1]["session_id"]: {"accuracy.correct_ownfile": {"state": "missing"}},
    }
    out = dashboard_analysis._condition_comparison(sessions, scalars, _DEFS)
    cell = out["rows"][0]["cells"][out["columns"][0]["pool_id"]]
    assert cell["n_sessions"] == 2  # pool membership
    assert cell["n"] == 1  # measured values only


def test_legacy_sessions_stay_unkeyed_and_labeled() -> None:
    # A session with no compatibility key pools only by its unkeyed partition.
    legacy = {
        "session_id": uuid.uuid4(),
        "manifest_id": "tp_ai_ovnone_thin_v3",
        "study_key": "k",
        "batch_id": "b",
    }
    out = dashboard_analysis._condition_comparison([legacy], {}, _DEFS)
    assert out["columns"][0]["label"] == "ovnone_thin v3 (unassigned)"
    assert out["columns"][0]["pool_id"] == "tp_ai_ovnone_thin_v3|unassigned"
    assert out["rows"][0]["cells"][out["columns"][0]["pool_id"]]["n"] == 0
