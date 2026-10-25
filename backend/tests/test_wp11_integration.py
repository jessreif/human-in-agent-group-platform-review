"""WP11 exports on real PostgreSQL (INV-1106..1109): consistent-snapshot study export,
restricted payment scope, sharing-bundle review boundary and fidelity/incident tables.

These live at ``tests/`` level (not ``tests/integration/``) so the WP11 invocation
``pytest -m integration tests/test_wp11*`` collects them; the disposable-database fixtures
are imported from the integration conftest.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db import schema as s
from app.export.restricted import PAYMENT_TABLES, payment_export, sharing_bundle
from app.export.study import EXPORT_SCHEMA_VERSION, export_study, write_export
from app.services.worlds import ensure_world
from tests.integration.conftest import (  # noqa: F401  (re-exported fixtures)
    MigratedDatabase,
    bootstrapped,
    cluster,
    disposable_database,
    disposable_engine,
    migrated,
)
from tests.integration.test_persistence_schema import _denied, _new_session
from tests.integration.test_research_records import _accepted_command
from tests.wp02 import SEED, bound

pytestmark = pytest.mark.integration

ZERO = "0" * 64
CK1 = "ck1:" + "1" * 64
CK2 = "ck1:" + "2" * 64


def _session_with_world(db: MigratedDatabase, arm: str = "high", ck: str = CK1) -> uuid.UUID:
    session_id = _new_session(db)
    with db.runtime.begin() as conn:
        ensure_world(
            conn,
            session_id=session_id,
            seed_hex=SEED,
            arm=arm,
            bound=bound("tp_demo_v1"),
        )
        conn.execute(
            s.session.update()
            .where(s.session.c.session_id == session_id)
            .values(arm=arm, compatibility_key=ck)
        )
    return session_id


def test_study_export_tables_snapshot_and_manifest(migrated: MigratedDatabase, tmp_path) -> None:
    session_id = _session_with_world(migrated)
    _accepted_command(migrated, session_id)
    # identity row exists in the DB but must never reach the research export
    with migrated.runtime.begin() as conn:
        conn.execute(
            s.platform_link.insert().values(
                link_id=uuid.uuid4(),
                session_id=session_id,
                platform="prolific",
                platform_pid="P-SECRET-WP11",
            )
        )
    export = export_study(migrated.research)
    names = set(export.tables)
    expected = {
        "participant_wide",
        "crate_long",
        "fidelity_rate",
        "incident_summary",
        "model_audit",
        "evidence_transfer",
        "session",
        "event",
        "message",
        "request",
        "request_episode",
        "share",
        "delivery_receipt",
        "model_call",
        "displayed_output",
        "context_snapshot",
        "claim",
        "claim_source",
        "answer_revision",
        "answer_final",
        "confidence_response",
        "item_response",
        "exposure",
        "incident_interval",
        "scheduled_job",
        "command_receipt",
        "stage_transition",
        "clock_interval",
        "consent_event",
        "result",
        "data_use_choice",
        "world_snapshot",
        "record",
        "seat_holding",
        "file_membership",
        "draw_log",
        "agent_state",
        "outbox",
        "experiment_manifest",
        "batch",
    }
    assert expected <= names
    # payment-domain and identity tables are never in a research export
    assert "platform_link" not in names
    assert "payment_entitlement" not in names
    assert "completion_receipt" not in names
    assert "P-SECRET-WP11" not in str({n: [r for r in t.rows] for n, t in export.tables.items()})

    wide = export.tables["participant_wide"].rows
    assert len(wide) == 1
    row = wide[0]
    assert row["inv_condition"] == "high"
    assert row["compatibility_key"] == CK1
    assert row["arm_provenance"] == "configured"
    assert row["inv_events"] == len(export.tables["event"].rows)

    crates = export.tables["crate_long"].rows
    assert len(crates) == 8
    assert sum(1 for c in crates if c["assigned"]) == 4
    assert all(c["truth"] for c in crates)
    assert all(c["truth_provenance"] == "simulated" for c in crates)
    assert sum(1 for c in crates if c["role"] == "other") == 4  # unassigned crates
    assert sum(1 for c in crates if c["role"] == "own") == 2
    assert sum(1 for c in crates if c["role"] == "team") == 2

    manifest = write_export(export, tmp_path)
    assert manifest["schema_version"] == EXPORT_SCHEMA_VERSION
    assert manifest["snapshot_isolation"] == "REPEATABLE READ"
    for name, table in export.tables.items():
        entry = manifest["tables"][name]
        assert entry["row_count"] == len(table.rows)
        assert entry["rows_sha256"] == table.checksum
        assert entry["schema_version"] == EXPORT_SCHEMA_VERSION
        assert entry["filter"] == export.filters
        assert (tmp_path / entry["file"]).is_file()
        if name in {
            "participant_wide",
            "crate_long",
            "fidelity_rate",
            "incident_summary",
            "model_audit",
            "evidence_transfer",
        }:
            assert entry["metric_version"]
    csv_text = (tmp_path / "participant_wide.csv").read_text(encoding="utf-8")
    assert "P-SECRET-WP11" not in csv_text


def test_export_stamps_feature_flags(migrated: MigratedDatabase, tmp_path) -> None:
    """2026-10-15: every CSV row carries the deployment flag columns, the manifest
    records the block, and per-session ``flags_at_entry`` joins the flags recorded
    in the session's own session_opened payload (null when unrecorded)."""
    session_id = _session_with_world(migrated)
    recorded = {"ai_qa_enabled": True, "time_pressure_enabled": False}
    with migrated.runtime.begin() as conn:
        conn.execute(
            s.event.insert().values(
                session_id=session_id,
                seq=900,
                event_id=uuid.uuid4(),
                stage="consent",
                actor="system",
                type="session_opened",
                revision=1,
                server_time=datetime.now(UTC),
                payload={
                    "mode": "preview",
                    "timing_mode": "faithful",
                    "feature_flags": recorded,
                },
            )
        )
    flags = {
        "scripted_teammates_enabled": False,
        "ai_assistant_enabled": False,
        "crate_overlap_enabled": True,
        "crate_number_enabled": True,
        "time_pressure_enabled": False,
        "ai_qa_enabled": True,
    }
    export = export_study(migrated.research, feature_flags=flags)
    assert export.feature_flags == flags
    for table in export.tables.values():
        for row in table.rows:
            assert row["flag_ai_qa_enabled"] is True
            assert row["flag_time_pressure_enabled"] is False
    assert export.tables["session"].rows[0]["flags_at_entry"] == recorded
    assert export.tables["participant_wide"].rows[0]["flags_at_entry"] == recorded

    manifest = write_export(export, tmp_path)
    assert manifest["feature_flags"] == flags
    header = (tmp_path / "session.csv").read_text(encoding="utf-8").splitlines()[0]
    for name in flags:
        assert f"flag_{name}" in header
    assert "flags_at_entry" in header


def test_export_without_flags_stays_legible(migrated: MigratedDatabase) -> None:
    """No flags supplied → no flag columns and null flags_at_entry (the legacy
    caller path stays backward-compatible)."""
    _session_with_world(migrated)
    export = export_study(migrated.research)
    assert export.feature_flags == {}
    assert "flag_ai_qa_enabled" not in export.tables["session"].rows[0]


def test_export_reads_committed_snapshot(migrated: MigratedDatabase) -> None:
    """A second export after a new commit sees it; the first export's numbers don't move."""
    session_id = _session_with_world(migrated)
    first = export_study(migrated.research, session_ids=[session_id])
    before = len(first.tables["event"].rows)
    _accepted_command(migrated, session_id)
    second = export_study(migrated.research, session_ids=[session_id])
    assert len(second.tables["event"].rows) == before + 1
    assert first.filters == {"session_ids": [str(session_id)]}


def test_payment_export_is_compensation_scoped(migrated: MigratedDatabase) -> None:
    session_id = _new_session(migrated)
    with migrated.runtime.begin() as conn:
        conn.execute(
            s.platform_link.insert().values(
                link_id=uuid.uuid4(),
                session_id=session_id,
                platform="prolific",
                platform_pid="PP-1",
            )
        )
        conn.execute(
            s.payment_entitlement.insert().values(
                session_id=session_id,
                kind="base",
                cents=800,
                policy_artifact_id="payment.s5",
                policy_version=1,
            )
        )
        conn.execute(
            s.payment_entitlement.insert().values(
                session_id=session_id,
                kind="bonus",
                cents=150,
                policy_artifact_id="payment.s5",
                policy_version=1,
            )
        )
        conn.execute(
            s.result.insert().values(
                session_id=session_id,
                scoring_version="v1",
                correct_total=4,
                correct_teamdep=2,
                correct_ownfile=2,
                status="scored",
                bonus_cents=150,
                finalized_seq=1,
            )
        )
    out = payment_export(migrated.compensation)
    # exactly the linkage+entitlement+outcome allowlist plus the derived summary — no
    # other session-owned table can appear (INV-1109)
    assert set(out) == {
        "platform_link",
        "payment_entitlement",
        "completion_receipt",
        "result",
        "payment_summary",
    }
    assert {t.name for t in PAYMENT_TABLES} == {
        "platform_link",
        "payment_entitlement",
        "completion_receipt",
        "result",
    }
    summary = next(r for r in out["payment_summary"] if r["session_id"] == str(session_id))
    assert summary["platform_pid"] == "PP-1"
    assert summary["bonus_amount"] == Decimal("1.5")
    assert summary["result_status"] == "scored"
    # other roles cannot even run it — the identity read is denied, not filtered
    _denied(migrated.runtime, "SELECT * FROM identity.platform_link")
    _denied(migrated.research, "SELECT * FROM identity.platform_link")


def test_sharing_bundle_free_text_boundary(migrated: MigratedDatabase) -> None:
    session_id = _session_with_world(migrated)
    message_id = uuid.uuid4()
    with migrated.runtime.begin() as conn:
        conn.execute(
            s.message.insert().values(
                session_id=session_id,
                message_id=message_id,
                actor="participant",
                channel="team",
                body="free text with a name: Alice",
                accepted_seq=1,
            )
        )
    with migrated.research.connect() as conn:
        conn = conn.execution_options(isolation_level="REPEATABLE READ")
        bundle = sharing_bundle(conn, session_ids=[session_id])
    row = bundle.tables["message"].rows[0]
    # 2026-10-16 owner direction: verbatim emission — no withheld/scrubbed markers
    # in any exportable file; redaction_review rows never gate bundle cells.
    assert row["body"] == "free text with a name: Alice"
    assert "body_text_status" not in row
    # no initials field exists anywhere in the bundle
    assert not any("initial" in c for c in row)

    # a recorded review changes nothing — the cell stays verbatim
    with migrated.migrator.begin() as conn:
        conn.execute(
            s.redaction_review.insert().values(
                review_id=uuid.uuid4(),
                session_id=session_id,
                kind="free_text",
                source_ref=f"message:{session_id}:{message_id}",
                scrubbed_ref="scrubbed/m-1.txt",
                scrubbed_sha256=ZERO,
                reviewer="staff",
            )
        )
    with migrated.research.connect() as conn:
        assert conn.execute(select(s.redaction_review)).first() is not None
        bundle = sharing_bundle(conn, session_ids=[session_id])
    row = bundle.tables["message"].rows[0]
    assert row["body"] == "free text with a name: Alice"
    assert "body_text_status" not in row


def test_fidelity_and_incident_tables(migrated: MigratedDatabase) -> None:
    session_id = _session_with_world(migrated)
    snapshot_id, call_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with migrated.runtime.begin() as conn:
        conn.execute(
            s.knowledge_snapshot.insert().values(
                session_id=session_id,
                snapshot_id=snapshot_id,
                seat="assistant",
                purpose="assistant_context",
                visible_event_seq=0,
                record_manifest=[],
                created_event_seq=1,
            )
        )
        conn.execute(
            s.model_call.insert().values(
                session_id=session_id,
                call_id=call_id,
                snapshot_id=snapshot_id,
                provider="local",
                model="m1",
                prompt_sha256=ZERO,
                input={},
                status="fallback",
            )
        )
        conn.execute(
            s.scheduled_job.insert().values(
                session_id=session_id,
                job_id=job_id,
                logical_job_key="j1",
                kind="deliver",
                clock_basis="wall",
                due_wall_at=datetime(2026, 1, 1, tzinfo=UTC),
                claimable_at=datetime(2026, 1, 1, tzinfo=UTC),
                status="committed",
                finished_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=10),
                finished_revision=1,
            )
        )
        conn.execute(
            s.incident_interval.insert().values(
                session_id=session_id,
                incident_id=uuid.uuid4(),
                kind="client_disconnect",
                clock_effect="paused",
            )
        )
    export = export_study(migrated.research, session_ids=[session_id])
    fid = export.tables["fidelity_rate"].rows
    assert any(r["source"] == "model_call:assistant_context" and r["n_fallback"] == 1 for r in fid)
    assert any(r["source"] == "job:deliver" and r["n_off_schedule"] == 1 for r in fid)
    assert all(r["compatibility_key"] == CK1 for r in fid)
    inc = export.tables["incident_summary"].rows
    assert inc and inc[0]["n_open_censored"] == 1
    assert inc[0]["clock_effect"] == "paused"
    assert inc[0]["mean_duration_ms"] is None  # open interval: censored, not zero


def test_aggregates_do_not_pool_across_keys(migrated: MigratedDatabase) -> None:
    a = _session_with_world(migrated, ck=CK1)
    b = _session_with_world(migrated, ck=CK2)
    for sid in (a, b):
        with migrated.runtime.begin() as conn:
            conn.execute(
                s.incident_interval.insert().values(
                    session_id=sid,
                    incident_id=uuid.uuid4(),
                    kind="server_outage",
                    clock_effect="counted",
                )
            )
    export = export_study(migrated.research, session_ids=[a, b])
    groups = {r["compatibility_key"] for r in export.tables["incident_summary"].rows}
    assert groups == {CK1, CK2}
