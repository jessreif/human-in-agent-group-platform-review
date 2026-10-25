"""INV-0302/0303 schema rules that do not need a database (PostgreSQL tests are integration)."""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import ForeignKeyConstraint, PrimaryKeyConstraint, Table, UniqueConstraint

from app.db import schema as s
from app.db.engine import expected_schema_head

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
NOT_SESSION_OWNED = {
    # reference/enrollment aggregates
    "experiment_manifest",
    "batch",
    "batch_group",
    "batch_group_member",
    "participant",
    "session",
    "allocation",
    # interventions whose target is optional (a session or the whole study)
    "admin_audit",
    "redaction_review",
    # offline coding products (WP12a); session linkage is optional and non-owning
    "coding_run",
    "coding_result",
    "validation_gate",
    # identity-side auth material: supersession references credentials, not session rows
    "session_credential",
}


def _references_session(table: Table) -> bool:
    for constraint in table.constraints:
        if isinstance(constraint, ForeignKeyConstraint) and "session_id" in constraint.column_keys:
            return True
    return False


def test_schemas_are_app_and_identity_only() -> None:
    assert {t.schema for t in s.metadata.sorted_tables} == {"app", "identity"}
    assert [t.name for t in s.metadata.sorted_tables if t.schema == "identity"] == [
        "platform_link",
        "session_credential",
    ]


def test_every_session_owned_table_is_keyed_to_its_session() -> None:
    for table in s.metadata.sorted_tables:
        if table.name in NOT_SESSION_OWNED:
            continue
        assert "session_id" in table.c, table.name
        assert not table.c.session_id.nullable, table.name
        assert _references_session(table), table.name


def test_intra_session_references_are_composite() -> None:
    """Rows that point at other session-owned rows include session_id in the foreign key."""
    for table in s.metadata.sorted_tables:
        for constraint in table.constraints:
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            target = constraint.referred_table.name
            if target in NOT_SESSION_OWNED:
                continue
            assert "session_id" in constraint.column_keys, (table.name, target)


def test_uniqueness_contracts() -> None:
    def unique_sets(table: Table) -> set[tuple[str, ...]]:
        out: set[tuple[str, ...]] = set()
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint | PrimaryKeyConstraint):
                out.add(tuple(col.name for col in constraint.columns))
        out.update((column.name,) for column in table.columns if column.unique)
        return out

    assert ("session_id", "logical_job_key") in unique_sets(s.scheduled_job)
    assert ("command_id",) in unique_sets(s.command_receipt)
    assert ("session_id", "seq") in unique_sets(s.event)
    assert ("session_id", "seq") in unique_sets(s.outbox)
    assert ("session_id", "record_id") in unique_sets(s.file_membership)
    assert ("participant_id", "batch_id") in unique_sets(s.session)
    assert ("batch_id", "block_index", "slot_index") in unique_sets(s.allocation)
    # INV-0304/0305: one logical entity per session where the spec demands it
    assert ("session_id", "crate") in unique_sets(s.answer_final)
    assert ("session_id", "crate", "revision") in unique_sets(s.confidence_response)
    assert ("session_id",) in unique_sets(s.result)
    assert ("session_id",) in unique_sets(s.completion_receipt)
    assert ("session_id", "kind") in unique_sets(s.payment_entitlement)
    assert ("session_id", "snapshot_id", "attempt") in unique_sets(s.model_call)
    assert ("session_id", "seat") in unique_sets(s.agent_state)
    assert ("run_id", "name") in unique_sets(s.validation_gate)
    assert ("run_id", "subject_key") in unique_sets(s.coding_result)


def test_append_only_tables_exist_and_are_app_tables() -> None:
    names = {t.name for t in s.metadata.sorted_tables if t.schema == "app"}
    assert set(s.IMMUTABLE_TABLES) <= names
    assert "session" not in s.IMMUTABLE_TABLES and "scheduled_job" not in s.IMMUTABLE_TABLES


def test_spec_minimum_model_tables_exist() -> None:
    """Every aggregate named in spec §8.1's minimum relational model is implemented."""
    spec = (REPO / "docs" / "DESIGN_SPEC.md").read_text(encoding="utf-8")
    block = spec.split("### 8.1 Minimum relational model", 1)[1].split("### 8.2", 1)[0]
    named: set[str] = set()
    for line in block.splitlines():
        # first cell of each table row lists the aggregate names
        cell = line.split("|")[1] if line.startswith("|") else ""
        named.update(n.split(".", 1)[-1] for n in re.findall(r"`([a-z_.]+)`", cell))
    present = {t.name for t in s.metadata.sorted_tables}
    assert named <= present, f"spec §8.1 tables missing from schema: {sorted(named - present)}"


def test_coding_tables_have_no_runtime_grant_plan() -> None:
    """INV-0306: coding-run products are defined now but written only by the WP12a offline
    path; the runtime role is granted nothing on them."""
    for name in s.RUNTIME_NO_ACCESS:
        assert name in {t.name for t in s.metadata.sorted_tables}
        assert name not in s.IMMUTABLE_TABLES  # immutable constraints arrive with WP12a


def test_stages_match_the_spec_state_machine() -> None:
    spec = (REPO / "docs" / "DESIGN_SPEC.md").read_text(encoding="utf-8")
    block = spec.split("### 5.2 State machine", 1)[1].split("```", 2)[1]
    prose = {"text", "time", "pressure", "baseline", "review", "first", "peer", "evaluation"}
    named = set(re.findall(r"[a-z_]+", block)) - prose - {"for", "both", "arms"}
    for stage in named:
        assert stage in s.STAGES, stage
    for terminal in s.TERMINAL_STAGES:
        assert f"`{terminal}`" in spec


def test_latest_snapshot_equals_current_schema() -> None:
    """Each migration builds from a frozen snapshot; the newest one must match the models."""
    snapshots = sorted((BACKEND / "migrations" / "snapshots").glob("schema_*.py"))
    assert snapshots, "no schema snapshot"
    current = (BACKEND / "app" / "db" / "schema.py").read_text(encoding="utf-8")
    assert snapshots[-1].read_text(encoding="utf-8") == current, (
        f"{snapshots[-1].name} differs from app/db/schema.py: add a new migration + snapshot"
    )


def test_single_migration_head() -> None:
    assert expected_schema_head() == "0017_mixed_arm"
