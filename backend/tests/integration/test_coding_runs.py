"""INV-1205/1208 integration: coding_run/coding_result persistence, immutability,
supersession, privileges and the analysis guard — on the disposable PostgreSQL harness
(INV-0104). The offline coding writer is represented by the migrator role (the schema
owner); runtime has no access and research is read-only.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.coding.corpus import persist_corpus_run
from app.coding.guard import GuardOverride, SemanticGuardError, require
from app.coding.runs import (
    CodingRunError,
    RunSpec,
    create_run,
    finish_run,
    insert_results,
    record_gate,
    start_run,
)
from app.coding.types import CODE_VERSION, VALIDATOR_VERSION, ResultRow
from app.db import schema as s
from app.domain.manifests.store import ContentStore
from app.export.semantic import semantic_coding_table
from tests.integration.conftest import MigratedDatabase

pytestmark = pytest.mark.integration

_SHA = "ab" * 32


def _spec(**kw: object) -> RunSpec:
    base = dict(
        corpus={"kind": "test"},
        corpus_sha256=_SHA,
        rubric_artifact_id="coding.rubric",
        rubric_version=1,
        prompt_artifact_id="coding.prompt",
        prompt_version=1,
        model="deterministic",
        model_digest=None,
        parameters={},
        code_version=CODE_VERSION,
        validator_version=VALIDATOR_VERSION,
        created_by="pytest",
    )
    base.update(kw)
    return RunSpec(**base)  # type: ignore[arg-type]


def test_run_binds_full_provenance(migrated: MigratedDatabase) -> None:
    with migrated.migrator.connect() as conn:
        run_id = create_run(conn, _spec())
        conn.commit()
    with migrated.research.connect() as conn:
        run = (
            conn.execute(select(s.coding_run).where(s.coding_run.c.run_id == run_id))
            .mappings()
            .one()
        )
    assert run["corpus_sha256"] == _SHA
    assert run["rubric_artifact_id"] == "coding.rubric" and run["rubric_version"] == 1
    assert run["prompt_artifact_id"] == "coding.prompt" and run["prompt_version"] == 1
    assert run["model"] == "deterministic" and run["model_digest"] is None
    assert run["code_version"] == CODE_VERSION
    assert run["validator_version"] == VALIDATOR_VERSION
    assert run["status"] == "pending"


def test_results_statuses_and_codes_constraint(migrated: MigratedDatabase) -> None:
    with migrated.migrator.connect() as conn:
        run_id = create_run(conn, _spec())
        start_run(conn, run_id)
        insert_results(
            conn,
            run_id,
            [
                ResultRow(subject_key="s1", status="coded", codes={"assertions": []}),
                ResultRow(subject_key="s2", status="not_coded", codes=None),
                ResultRow(subject_key="s3", status="abstained", codes=None),
                ResultRow(
                    subject_key="s4",
                    status="failed",
                    codes=None,
                    adjudication={"error": "boom"},
                ),
            ],
        )
        conn.commit()
        # 'coded' without codes is refused by insert_results' own guard
        with pytest.raises(CodingRunError):
            insert_results(conn, run_id, [ResultRow(subject_key="bad", status="coded", codes=None)])
        # and the table CHECK still guards raw SQL inserts (JSON null is not NULL)
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO app.coding_result (run_id, subject_key, status, codes) "
                    "VALUES (:r, 'raw', 'coded', NULL)"
                ),
                {"r": run_id},
            )
        conn.rollback()
    with migrated.research.connect() as conn:
        statuses: dict[str, str] = dict(
            conn.execute(
                select(s.coding_result.c.subject_key, s.coding_result.c.status).where(
                    s.coding_result.c.run_id == run_id
                )
            ).all()
        )
    assert statuses == {"s1": "coded", "s2": "not_coded", "s3": "abstained", "s4": "failed"}


def test_duplicate_subject_key_never_overwrites(migrated: MigratedDatabase) -> None:
    with migrated.migrator.connect() as conn:
        run_id = create_run(conn, _spec())
        row = ResultRow(subject_key="dup", status="coded", codes={"assertions": []})
        insert_results(conn, run_id, [row])
        conn.commit()
        with pytest.raises(IntegrityError):
            insert_results(conn, run_id, [row])
        conn.rollback()


def test_correction_is_a_superseding_row(migrated: MigratedDatabase) -> None:
    with migrated.migrator.connect() as conn:
        run_a = create_run(conn, _spec())
        insert_results(
            conn,
            run_a,
            [ResultRow(subject_key="k", status="coded", codes={"assertions": []})],
        )
        conn.commit()
        run_b = create_run(conn, _spec())
        conn.execute(
            insert(s.coding_result).values(
                run_id=run_b,
                subject_key="k",
                status="abstained",
                codes=None,
                adjudication={"correction": "gold review"},
                supersedes_run_id=run_a,
                supersedes_subject_key="k",
            )
        )
        conn.commit()
        # the composite FK requires the superseded row to exist
        with pytest.raises(IntegrityError):
            conn.execute(
                insert(s.coding_result).values(
                    run_id=run_b,
                    subject_key="dangling",
                    status="coded",
                    codes={},
                    supersedes_run_id=uuid.uuid4(),
                    supersedes_subject_key="ghost",
                )
            )
        conn.rollback()
    with migrated.research.connect() as conn:
        rows = conn.execute(
            select(
                s.coding_result.c.run_id,
                s.coding_result.c.status,
                s.coding_result.c.supersedes_run_id,
            ).where(s.coding_result.c.subject_key == "k")
        ).all()
    assert len(rows) == 2  # both versions preserved — nothing overwritten
    by_run = {str(r.run_id): r for r in rows}
    assert by_run[str(run_a)].status == "coded"
    assert by_run[str(run_b)].supersedes_run_id == run_a


def test_guard_blocks_incomplete_and_mixed(migrated: MigratedDatabase) -> None:
    with migrated.migrator.connect() as conn:
        run_a = create_run(conn, _spec())
        insert_results(
            conn, run_a, [ResultRow(subject_key="k", status="coded", codes={"assertions": []})]
        )
        finish_run(conn, run_a, status="completed", expected_subjects=2)
        run_b = create_run(conn, _spec())
        finish_run(conn, run_b, status="completed", expected_subjects=0)
        conn.commit()

        with pytest.raises(SemanticGuardError, match="coverage incomplete"):
            require(conn, [run_a])
        with pytest.raises(SemanticGuardError, match="mixed coding runs"):
            require(conn, [run_a, run_b])
        report = require(conn, [run_b])
        assert report.allowed
        # explicit, auditable override unblocks but is recorded
        override = GuardOverride(reason="dev smoke", decided_by="pytest")
        assert require(conn, [run_a], override=override).allowed


def test_semantic_export_labels_and_guard(migrated: MigratedDatabase) -> None:
    with migrated.migrator.connect() as conn:
        run_id = create_run(conn, _spec())
        insert_results(
            conn,
            run_id,
            [
                ResultRow(
                    subject_key="a",
                    status="coded",
                    codes={"assertions": [], "questions": [], "solicitations": []},
                ),
                ResultRow(subject_key="b", status="abstained", codes=None),
            ],
        )
        finish_run(conn, run_id, status="completed", expected_subjects=2)
        conn.commit()

        table, report = semantic_coding_table(conn, run_id)
        assert report.allowed
        outcome = {r["subject_key"]: r["semantic_outcome"] for r in table.rows}
        assert outcome == {"a": "coded", "b": "abstained"}
        coded_row = next(r for r in table.rows if r["subject_key"] == "a")
        assert coded_row["n_assertions"] == 0  # empty set, not missing
        assert coded_row["codes_json"]

        # a running/incomplete run blocks the export outright
        run2 = create_run(conn, _spec())
        conn.commit()
        with pytest.raises(SemanticGuardError):
            semantic_coding_table(conn, run2)


def test_validation_gate_records_pending_dev_gate(migrated: MigratedDatabase) -> None:
    """WP12a can record gate rows; real human-gold PASS remains WP12b — the dev gate
    stays pending with an honest adjudication."""
    with migrated.migrator.connect() as conn:
        run_id = create_run(conn, _spec())
        gate_id = record_gate(
            conn,
            run_id,
            name="held_out_human_gold",
            thresholds={"min_f1": 0.8},
            adjudication={
                "unavailable": "real held-out corpus is WP12b; dev corpus is not human gold"
            },
        )
        conn.commit()
        row = (
            conn.execute(select(s.validation_gate).where(s.validation_gate.c.gate_id == gate_id))
            .mappings()
            .one()
        )
    assert row["status"] == "pending" and row["decided_at"] is None


def test_runtime_role_cannot_touch_coding_tables(migrated: MigratedDatabase) -> None:
    with migrated.runtime.connect() as conn:
        with pytest.raises(ProgrammingError):
            conn.execute(select(s.coding_result)).all()
        conn.rollback()
        with pytest.raises(ProgrammingError):
            conn.execute(
                text("INSERT INTO app.coding_run (run_id, corpus) VALUES (gen_random_uuid(), '{}')")
            )
        conn.rollback()


def test_corpus_run_roundtrip(migrated: MigratedDatabase) -> None:
    """End-to-end: corpus artifact -> coding_run + one row per case -> guard allows."""
    store = ContentStore.load_default()
    with migrated.migrator.connect() as conn:
        run_id = persist_corpus_run(conn, store, created_by="pytest")
        conn.commit()
    with migrated.research.connect() as conn:
        run = (
            conn.execute(select(s.coding_run).where(s.coding_run.c.run_id == run_id))
            .mappings()
            .one()
        )
        assert run["status"] == "completed"
        assert run["coverage"]["complete"] is True
        n_results: int = conn.execute(
            text("SELECT count(*) FROM app.coding_result WHERE run_id = :r"),
            {"r": run_id},
        ).scalar_one()
        assert n_results == run["coverage"]["expected_subjects"] > 0
        report = require(conn, [run_id])
        assert report.allowed
