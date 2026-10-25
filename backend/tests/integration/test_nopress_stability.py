"""Neutral allocation remains safe across repeated deployment flag changes."""

from __future__ import annotations

import uuid
from collections import Counter
from typing import Any

import pytest
from sqlalchemy import select, update

from app.db import schema as s
from app.db.tx import TxContext, db_now
from app.domain.manifests.binding import bind
from app.domain.manifests.store import ContentStore
from app.flags import FeatureFlags
from app.services import adminops, adminviews, dashboard, dashboard_ops, lifecycle, scheduler
from app.services.entry import EntryResult
from harness import clockwork
from harness.cli import _StubProvider
from harness.personas import NormalPersona
from harness.runner import Runner, SessionReport

from .conftest import MigratedDatabase
from .test_ai_qa_flag import CapturingProvider
from .test_time_pressure_flag import PRESSURE_ITEMS, _events, _item_rows, _jobs
from .test_wp08_workbench_api import _authed_client

pytestmark = pytest.mark.integration
MANIFEST = "tp_ai_v11"


def _consent(runner: Runner, batch: str) -> EntryResult:
    entry = runner.enroll(batch, f"QA-neutral-{uuid.uuid4().hex}")
    bound = bind(runner.store, MANIFEST, mode="qa")
    result = runner.send(
        entry.session_id,
        entry.writer_id,
        "consent",
        {"action": "consented", "text_version": bound.artifacts["instruments.consent"].key},
        runner._revision(entry.session_id),
    )
    assert result.accepted, result.reason
    return entry


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("initial_pressure", [False, True])
def test_allocation_blocks_survive_repeated_flag_changes(
    migrated: MigratedDatabase, grouped: bool, initial_pressure: bool
) -> None:
    store = ContentStore.load_default()
    runner = Runner(migrated, store)
    batch = f"neutral.blocks.{uuid.uuid4().hex[:8]}"
    members = [batch + ".a", batch + ".b"] if grouped else [batch]
    for member in members:
        runner.seed_batch(MANIFEST, member)
    if grouped:
        with migrated.runtime.begin() as conn:
            conn.execute(
                update(s.batch).where(s.batch.c.batch_id.in_(members)).values(enrollment_open=False)
            )
        adminops.create_batch_group(
            migrated.runtime, store, actor="test", group_id=batch, batch_ids=members, mode="qa"
        )
        adminops.set_group_enrollment(
            migrated.runtime, batch, open_=True, actor="test", reason="disposable regression"
        )

    snapshots: dict[uuid.UUID, tuple[Any, ...]] = {}
    # Includes the original 2/8-neutral-consent failures, partial blocks and
    # repeated off→on→off→on transitions, without resetting any historical rows.
    for enabled, n in (
        (initial_pressure, 8 if grouped else 2),
        (not initial_pressure, 1),
        (initial_pressure, 1),
        (not initial_pressure, 8),
        (initial_pressure, 8),
    ):
        runner.time_pressure = enabled
        for _ in range(n):
            entry = _consent(runner, batch)
            with migrated.runtime.connect() as conn:
                row = conn.execute(
                    select(
                        s.allocation.c.batch_id,
                        s.allocation.c.arm,
                        s.allocation.c.block_index,
                        s.allocation.c.slot_index,
                    ).where(s.allocation.c.session_id == entry.session_id)
                ).one()
                assert row.arm in (("high", "low") if enabled else ("nopress",))
                for sid, old in snapshots.items():
                    assert (
                        tuple(
                            conn.execute(
                                select(
                                    s.allocation.c.batch_id,
                                    s.allocation.c.arm,
                                    s.allocation.c.block_index,
                                    s.allocation.c.slot_index,
                                ).where(s.allocation.c.session_id == sid)
                            ).one()
                        )
                        == old
                    )
                snapshots[entry.session_id] = tuple(row)

        if grouped:
            with migrated.runtime.connect() as conn:
                for flag in (False, True):
                    balance = adminops.group_balance(conn, batch, time_pressure_enabled=flag)
                    assert balance["allocated"] == len(snapshots)
                    assert sum(c["allocated"] for c in balance["cells"]) == len(snapshots)
                    actual = Counter((r[0], r[1]) for r in snapshots.values())
                    for cell in balance["cells"]:
                        assert cell["allocated"] == actual[(cell["batch_id"], cell["arm"])]
                        assert cell["allocatable"] is (
                            cell["arm"] in (("high", "low") if flag else ("nopress",))
                        )

    blocks: dict[int, list[tuple[Any, ...]]] = {}
    for row in snapshots.values():
        blocks.setdefault(row[2], []).append(row)
    for rows in blocks.values():
        neutral = rows[0][1] == "nopress"
        cells = {(b, a) for b in members for a in (("nopress",) if neutral else ("high", "low"))}
        realized = {(r[0], r[1]) for r in rows}
        assert realized <= cells
        assert len(realized) == len(rows)  # never repeat a cell within a block
        assert sorted(r[3] for r in rows) == list(range(len(rows)))
        if len(rows) == len(cells):
            assert realized == cells
    positions = [(r[0], r[2], r[3]) for r in snapshots.values()]
    assert len(set(positions)) == len(positions)


@pytest.mark.parametrize("manifest", [MANIFEST, "tp_review_first_v10"])
def test_neutral_consent_completes_after_pressure_reenabled(
    migrated: MigratedDatabase, manifest: str
) -> None:
    class ReenableAfterConsent(Runner):
        def send(self, *args: Any, **kwargs: Any) -> Any:
            result = super().send(*args, **kwargs)
            if args[2] == "consent":
                assert result.accepted
                self.time_pressure = True
            return result

    runner = ReenableAfterConsent(
        migrated, ContentStore.load_default(), provider=_StubProvider(), time_pressure=False
    )
    batch = f"neutral.complete.{uuid.uuid4().hex[:8]}"
    runner.seed_batch(manifest, batch)
    report = runner.run_session(manifest, batch, NormalPersona(7), seed=7)
    assert report.stage == "completion", report.findings
    assert report.arm == "nopress"
    sid = uuid.UUID(report.session_id)
    assert not scheduler.PRESSURE_JOB_KINDS & _jobs(migrated, sid).keys()
    assert not {"checkin_open", "checkin_reveal", "warning_committed"} & _events(migrated, sid)
    assert not PRESSURE_ITEMS & _item_rows(migrated, sid)
    pages = (report.replay or {})["participant"]["pages"]
    assert pages["time_pressure"] is False
    assert _jobs(migrated, sid)["deadline"] == ["committed"]
    if manifest == "tp_review_first_v10":
        assert _jobs(migrated, sid)["solo_review_end"] == ["committed"]


def test_neutral_workbench_commands_and_pending_readiness_stay_neutral(
    migrated: MigratedDatabase, make_settings: Any
) -> None:
    store = ContentStore.load_default()
    runner = Runner(migrated, store, provider=_StubProvider(), time_pressure=False)
    batch = f"neutral.http.{uuid.uuid4().hex[:8]}"
    bound = runner.seed_batch(MANIFEST, batch)
    entry = _consent(runner, batch)
    runner.time_pressure = True
    sid = entry.session_id
    report = SessionReport(str(sid), "neutral", 0, MANIFEST, None, "training", None, 0)
    runner._drive_to_task(sid, entry.writer_id, bound, report)
    client = _authed_client(make_settings, migrated, entry, TIME_PRESSURE_ENABLED=True)
    with client:
        response = client.get("/workbench")
        assert response.status_code == 200, response.text
        assert response.json()["time_pressure"] is False
    clockwork.advance_active(migrated.runtime, sid, 61_000)
    runner._tick()
    with migrated.runtime.connect() as conn:
        assert (
            conn.execute(
                select(s.session.c.stage).where(s.session.c.session_id == sid)
            ).scalar_one()
            == "task"
        )
    replay = adminviews.replay(
        migrated.runtime,
        sid,
        include_restricted=True,
        store=store,
        flags=FeatureFlags(time_pressure=True),
    )
    assert replay is not None and replay["participant"]["pages"]["time_pressure"] is False
    # A staged questionnaire tests the same wire and command rules before
    # completion proves an era. Only served, neutral items must be required.
    with migrated.superuser.begin() as conn:
        conn.execute(
            update(s.session)
            .where(s.session.c.session_id == sid)
            .values(stage="post_questionnaire")
        )
    with client:
        response = client.get("/workbench")
        assert response.status_code == 200, response.text
        assert not PRESSURE_ITEMS & {i["item_id"] for i in response.json()["instrument"]["items"]}
    for item in PRESSURE_ITEMS:
        rejected = runner.send(
            sid,
            entry.writer_id,
            "item_response",
            {"item_id": item, "value": 4},
            runner._revision(sid),
        )
        assert not rejected.accepted and rejected.reason == "unknown_item"
    with migrated.runtime.connect() as conn:
        readiness = dashboard_ops.readiness(
            conn,
            store,
            dashboard.DashboardFilter(batch_id=batch, mode="qa"),
            audit_scope="all",
            feature_flags=FeatureFlags(time_pressure=True),
        )
    assert readiness["audit"]["rows"][0]["item_coverage"]["absent_expected"] == len(
        lifecycle.instrument_items(bound, "post_questionnaire", time_pressure=False)[0]
    )
    for item in lifecycle.instrument_items(bound, "post_questionnaire", time_pressure=False)[0]:
        value = item["options"][0] if item.get("options") else 4
        accepted = runner.send(
            sid,
            entry.writer_id,
            "item_response",
            {"item_id": item["item_id"], "value": value},
            runner._revision(sid),
        )
        assert accepted.accepted, accepted.reason
    with migrated.runtime.connect() as conn:
        assert (
            conn.execute(
                select(s.session.c.stage).where(s.session.c.session_id == sid)
            ).scalar_one()
            != "post_questionnaire"
        )


def test_neutral_straggler_pressure_jobs_and_commands_fail_closed(
    migrated: MigratedDatabase,
) -> None:
    runner = Runner(migrated, ContentStore.load_default(), time_pressure=False)
    batch = f"neutral.straggler.{uuid.uuid4().hex[:8]}"
    bound = runner.seed_batch(MANIFEST, batch)
    entry = _consent(runner, batch)
    sid = entry.session_id
    with migrated.superuser.begin() as conn:
        row = conn.execute(select(s.session).where(s.session.c.session_id == sid)).mappings().one()
        ctx = TxContext(conn, row, now=db_now(conn))
        for kind in scheduler.PRESSURE_JOB_KINDS:
            outcome = scheduler.dispatch(
                ctx,
                bound,
                {"kind": kind, "logical_job_key": f"straggler.{kind}"},
                flags=FeatureFlags(time_pressure=True),
            )
            assert outcome == "suppressed"
        conn.execute(
            update(s.session).where(s.session.c.session_id == sid).values(stage="checkin_collect")
        )
    runner.time_pressure = True
    for command in ("pressure_response", "pressure_skip"):
        rejected = runner.send(sid, entry.writer_id, command, {"value": 5}, runner._revision(sid))
        assert not rejected.accepted and rejected.reason == "time_pressure_disabled"
    assert not {"checkin_open", "checkin_reveal", "warning_committed"} & _events(migrated, sid)


def test_neutral_qa_prompt_and_audit_remain_neutral_after_reenable(
    migrated: MigratedDatabase,
) -> None:
    provider = CapturingProvider()
    store = ContentStore.load_default()
    runner = Runner(migrated, store, provider=provider, time_pressure=False, ai_qa=True)
    batch = f"neutral.qa.{uuid.uuid4().hex[:8]}"
    bound = runner.seed_batch(MANIFEST, batch)
    entry = _consent(runner, batch)
    runner.time_pressure = True
    sid = entry.session_id
    for payload in (
        {"step": 1},
        {"step": "practice", "action": "share", "record_id": "PRACTICE-K3-04"},
        {"step": "practice", "action": "pick_location", "crate": 3, "location": "The Sawmill"},
        {"step": "practice", "action": "ask", "text": "anything on crate 3?"},
        {
            "step": "comprehension",
            "complete": True,
            "answers": lifecycle.comprehension_key(
                ai_assistant=True, overlap=bound.manifest.task.overlap
            ),
        },
    ):
        result = runner.send(sid, entry.writer_id, "training_step", payload, runner._revision(sid))
        assert result.accepted, result.reason
    result = runner.send(
        sid, entry.writer_id, "qa_ask", {"question": "Is there a check-in?"}, runner._revision(sid)
    )
    assert result.accepted, result.reason
    assert (
        scheduler.tick_generation(
            migrated.runtime,
            store,
            worker_id="neutral-qa",
            provider=provider,
            flags=FeatureFlags(time_pressure=True),
        )
        == 1
    )
    assert len(provider.calls) == 1
    assert "check-in pop-up" not in provider.calls[0][0].messages[0].content
    with migrated.runtime.connect() as conn:
        audit = conn.execute(
            select(s.qa_exchange.c.generation_audit).where(s.qa_exchange.c.session_id == sid)
        ).scalar_one()
    assert audit["flags"]["time_pressure"] is False
