"""WP11 export unit tests (INV-1106..1110): formula-safe CSV, checksums, INV_FINISH
reconciliation, compatibility grouping and the historical-import contract — no database."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.export import csvsafe
from app.export.aggregates import fidelity_rates, group_key, incident_summary
from app.export.historical import (
    HISTORICAL_SCHEMA,
    import_historical,
    validate_bundle,
)
from app.export.legacy import EXCLUDED, RENAMED, UNAVAILABLE, mapping_for, reconcile
from tests.wp02 import fixture

# ----- formula-safe CSV ---------------------------------------------------------


@pytest.mark.parametrize("payload", ["=1+1", "+SUM(A1)", "-2*3", "\u22125", "@cmd", "\t=1"])
def test_formula_prefixes_are_escaped(payload: str) -> None:
    cell = csvsafe.safe_cell(payload)
    assert cell.startswith("'") and cell.endswith(payload)


@pytest.mark.parametrize("payload", [" value", "value ", "  spaced  ", "trailing\t"])
def test_leading_trailing_whitespace_is_escaped(payload: str) -> None:
    assert csvsafe.safe_cell(payload) == "'" + payload


def test_none_stays_empty_and_missing_is_not_fabricated() -> None:
    data = csvsafe.csv_bytes(["a", "b"], [{"a": None}, {"a": "x", "b": 2}])
    rows = list(csv.reader(io.StringIO(data.decode("utf-8"))))
    assert rows[1] == ["", ""]
    assert rows[2] == ["x", "2"]


def test_written_csv_is_parseable_and_safe(tmp_path) -> None:
    rows = [{"name": "=HYPERLINK('http://x')", "n": 7, "note": None}]
    sha = csvsafe.write_csv(tmp_path / "t.csv", ["name", "n", "note"], rows)
    raw = (tmp_path / "t.csv").read_bytes()
    assert sha == hashlib.sha256(raw).hexdigest()
    parsed = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
    assert parsed[0] == ["name", "n", "note"]
    assert parsed[1][0].startswith("'")
    assert parsed[1][2] == ""


def test_canonical_checksum_is_order_and_type_stable() -> None:
    a = [{"x": 1, "t": datetime(2026, 1, 1, tzinfo=UTC), "d": {"b": [2, 1]}}]
    b = [{"d": {"b": [2, 1]}, "x": 1, "t": datetime(2026, 1, 1, tzinfo=UTC)}]
    assert csvsafe.canonical_checksum(a) == csvsafe.canonical_checksum(b)
    assert csvsafe.canonical_checksum(a) != csvsafe.canonical_checksum([{**a[0], "x": 2}])


def test_columns_of_empty_table_keeps_preferred_header() -> None:
    assert csvsafe.columns_of([], preferred=["a", "b"]) == ["a", "b"]
    assert csvsafe.columns_of([{"b": 1, "a": 2}], preferred=["a"]) == ["a", "b"]


# ----- INV_FINISH reconciliation (INV-1107) ---------------------------------------


def _fixture_fields(study: str) -> list[str]:
    return fixture("demo_constants.json")[study]["inv_finish"]["embedded_data_fields"]


@pytest.mark.parametrize("study", ["time_pressure", "peer_evaluation"])
def test_every_inv_finish_field_is_reconciled(study: str) -> None:
    fields = _fixture_fields(study)
    assert fields, "fixture must list the embedded-data fields"
    mapping = reconcile(fields)
    assert set(mapping) == set(fields)
    for name, m in mapping.items():
        assert m.status, name
        assert m.source, name


def test_first_request_is_split_from_legacy_first_action() -> None:
    m = mapping_for("inv_first_request_sec")
    assert m is not None and m.status == RENAMED
    assert "first_teammate_request_ms" in m.note
    assert m.source.endswith("legacy_first_action_sec")


def test_initials_are_never_exported() -> None:
    m = mapping_for("inv_initials")
    assert m is not None and m.status == EXCLUDED


def test_client_telemetry_fields_are_explicitly_unavailable() -> None:
    for name in (
        "inv_window_blurs",
        "inv_seconds_away",
        "inv_idle_gaps",
        "inv_max_idle_gap",
        "inv_clicks_case",
        "inv_clicks_team",
        "inv_clicks_ai",
        "inv_case_scroll_pct",
        "inv_share_menu_opens",
        "inv_reply_opens",
    ):
        m = mapping_for(name)
        assert m is not None and m.status == UNAVAILABLE, name


def test_unknown_legacy_field_fails_closed() -> None:
    with pytest.raises(KeyError):
        reconcile(["inv_made_up_field"])


# ----- compatibility grouping + fidelity/incidents (INV-1108) ----------------------


def _sess(sid: str, ck: str | None, arm: str = "high") -> dict[str, Any]:
    return {
        "session_id": sid,
        "arm": arm,
        "compatibility_key": ck,
        "manifest_id": "tp_demo_v1",
        "manifest_sha256": "a" * 64,
        "binding_sha256": "b" * 64,
    }


def test_aggregates_never_pool_across_compatibility_keys() -> None:
    sessions = [_sess("s1", "ck1:" + "1" * 64), _sess("s2", "ck1:" + "2" * 64)]
    calls = [
        {
            "session_id": "s1",
            "snapshot_id": "x",
            "status": "fallback",
            "provider": "p",
            "model": "m",
        },
        {
            "session_id": "s2",
            "snapshot_id": "y",
            "status": "succeeded",
            "provider": "p",
            "model": "m",
        },
    ]
    rows = fidelity_rates(sessions, calls, [], [], [], filters={"batch_id": "b1"})
    keys = {r["compatibility_key"] for r in rows}
    assert len(keys) == 2
    for row in rows:
        assert row["filter"] == {"batch_id": "b1"}
        assert row["n_total"] == 1
    fallback_row = next(r for r in rows if r["n_fallback"] == 1)
    assert fallback_row["rate_fallback"] == 1.0


def test_unkeyed_sessions_form_their_own_group() -> None:
    s = _sess("s1", None)
    assert group_key(s).startswith("unkeyed:tp_demo_v1:")
    rows = fidelity_rates(
        [s],
        [
            {
                "session_id": "s1",
                "snapshot_id": "x",
                "status": "empty",
                "provider": "p",
                "model": "m",
            }
        ],
        [],
        [],
        [],
        filters={},
    )
    assert rows[0]["compatibility_key"].startswith("unkeyed:")
    assert rows[0]["rate_empty"] == 1.0


def test_zero_denominator_rate_is_null_not_zero() -> None:
    rows = fidelity_rates([_sess("s1", "ck1:" + "1" * 64)], [], [], [], [], filters={})
    assert rows == []  # nothing to rate -> no row, not a zero-rate row


def test_off_schedule_job_is_counted() -> None:
    sessions = [_sess("s1", "ck1:" + "1" * 64)]
    jobs = [
        {
            "session_id": "s1",
            "kind": "deliver",
            "status": "committed",
            "clock_basis": "wall",
            "due_wall_at": datetime(2026, 1, 1, tzinfo=UTC),
            "finished_at": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=5),
        },
    ]
    rows = fidelity_rates(sessions, [], [], jobs, [], filters={})
    assert rows[0]["n_off_schedule"] == 1


def test_incident_summary_groups_and_censors() -> None:
    sessions = [_sess("s1", "ck1:" + "1" * 64), _sess("s2", "ck1:" + "1" * 64)]
    incidents = [
        {
            "session_id": "s1",
            "clock_effect": "paused",
            "kind": "client_disconnect",
            "started_at": datetime(2026, 1, 1, tzinfo=UTC),
            "ended_at": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=30),
        },
        {
            "session_id": "s2",
            "clock_effect": "paused",
            "kind": "client_disconnect",
            "started_at": datetime(2026, 1, 1, tzinfo=UTC),
            "ended_at": None,
        },
    ]
    rows = incident_summary(sessions, incidents, filters={"study_key": "s2"})
    assert len(rows) == 1
    row = rows[0]
    assert row["n_incidents"] == 2 and row["n_sessions"] == 2
    assert row["n_open_censored"] == 1
    assert row["mean_duration_ms"] == 30_000  # closed intervals only
    assert row["filter"] == {"study_key": "s2"}


# ----- historical-import contract (INV-1110) ---------------------------------------


def _valid_bundle() -> dict[str, Any]:
    fields = {name: {"available": True} for name in _fixture_fields("peer_evaluation")}
    fields["inv_checkin_at"] = {"available": False}  # PE never had it
    row_fields = {"inv_condition": "eval", "inv_correct_total": 3, "inv_initials": "AB"}
    row_sha = hashlib.sha256(
        json.dumps(row_fields, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return {
        "schema": HISTORICAL_SCHEMA,
        "source_batch": {
            "batch_id": "qualtrics-s3-historical",
            "study": "s3",
            "generator": "qualtrics_raw_export",
            "source_file_sha256": "c" * 64,
        },
        "fields": fields,
        "rows": [{"source_row_id": "R_1", "fields": row_fields, "row_sha256": row_sha}],
    }


def test_valid_historical_bundle_passes_and_reports_not_applicable() -> None:
    bundle = _valid_bundle()
    report = validate_bundle(bundle)
    assert report.ok, report.problems
    result = import_historical(bundle)
    assert result.status == "not_applicable"
    assert result.import_sha256 is not None


def test_historical_bundle_rejects_fabricated_server_fields() -> None:
    bundle = _valid_bundle()
    bundle["rows"][0]["fields"]["seed_hex"] = "0" * 32
    bundle["rows"][0]["row_sha256"] = hashlib.sha256(
        json.dumps(bundle["rows"][0]["fields"], sort_keys=True).encode()
    ).hexdigest()
    report = validate_bundle(bundle)
    assert not report.ok
    assert any("seed_hex" in p for p in report.problems)


def test_historical_bundle_rejects_tampered_row() -> None:
    bundle = _valid_bundle()
    bundle["rows"][0]["fields"]["inv_correct_total"] = 99  # checksum now stale
    report = validate_bundle(bundle)
    assert not report.ok
    assert any("row_sha256" in p for p in report.problems)


def test_historical_bundle_rejects_undeclared_field() -> None:
    bundle = _valid_bundle()
    bundle["rows"][0]["fields"]["inv_checkin_at"] = 60  # declared unavailable
    bundle["rows"][0]["row_sha256"] = hashlib.sha256(
        json.dumps(bundle["rows"][0]["fields"], sort_keys=True).encode()
    ).hexdigest()
    report = validate_bundle(bundle)
    assert not report.ok
    assert any("not declared available" in p for p in report.problems)


def test_invalid_bundle_still_reports_not_applicable() -> None:
    result = import_historical({"schema": "wrong"})
    assert result.status == "not_applicable"
    assert result.validation is not None and not result.validation.ok
