"""Immutable student-preview successors complete without changing historical pins."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.db import schema as s
from app.domain.manifests.binding import bind
from app.domain.manifests.store import ContentStore
from app.services import adminops, lifecycle
from harness.cli import _StubProvider
from harness.personas import QaAskerPersona
from harness.runner import Runner

from . import test_wp09_experience as ah
from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration
PAIRS = [
    ("tp_ai_v13", "tp_ai_v14"),
    *[
        (f"tp_ai_ov{overlap}{thin}_v2", f"tp_ai_ov{overlap}{thin}_v3")
        for overlap in ("none", "some", "full")
        for thin in ("", "_thin")
    ],
]


@pytest.mark.parametrize(("old", "new"), PAIRS)
def test_preview_successor_preserves_task_and_completes_with_shared_code(
    migrated: MigratedDatabase, old: str, new: str
) -> None:
    store = ContentStore.load_default()
    before = bind(store, old, mode="preview")
    after = bind(store, new, mode="preview")
    assert before.artifacts["instruments.consent"].artifact.version == 8
    assert after.artifacts["instruments.consent"].artifact.version == 9
    assert after.artifacts["instruments.training"].artifact.version == 8
    assert before.manifest.task == after.manifest.task
    assert before.manifest.timing == after.manifest.timing
    assert before.manifest.teammates == after.manifest.teammates
    old_copy = before.artifacts["instruments.consent"].artifact.model_dump(mode="json")["body"][
        "text"
    ]
    new_copy = after.artifacts["instruments.consent"].artifact.model_dump(mode="json")["body"][
        "text"
    ]
    paragraphs = new_copy["sections"][0]["paragraphs"]
    assert len(paragraphs) == len(old_copy["sections"][0]["paragraphs"]) - 1
    assert any("To be compensated you need to reach the end" in p for p in paragraphs)
    assert not any(p.startswith("To be paid for the study,") for p in paragraphs)

    runner = Runner(migrated, store, provider=_StubProvider(), time_pressure=False, ai_qa=True)
    batch = f"student.ux.{uuid.uuid4().hex[:8]}"
    runner.seed_batch(new, batch)
    report = runner.run_session(new, batch, QaAskerPersona(7), seed=7)
    assert report.stage == "completion", report.findings
    assert not report.failed, report.findings
    sid = uuid.UUID(report.session_id)
    with migrated.runtime.connect() as conn:
        handoff = conn.execute(
            select(s.completion_receipt.c.handoff).where(s.completion_receipt.c.session_id == sid)
        ).scalar_one()
        assert handoff["completion_code"] == "PLACEHOLDER-CODE"
        assert handoff["receipt_code"].startswith("INV-")
        assert conn.execute(
            select(s.qa_exchange.c.status).where(s.qa_exchange.c.session_id == sid)
        ).scalar_one() in ("answered", "fallback")


@pytest.mark.parametrize("manifest_id", [pair[1] for pair in PAIRS])
def test_preview_completion_on_live_server_is_ineligible_and_cannot_be_paid(
    migrated: MigratedDatabase, manifest_id: str
) -> None:
    from sqlalchemy import update

    from app.db.tx import execute_command

    store = ContentStore.load_default()
    result = ah._preview_entry(migrated, store, manifest_id, "high", nonce_seed="ab")
    sid, writer = result.session_id, result.writer_id
    bound = bind(store, manifest_id, mode="preview")
    ah._run(
        migrated,
        store,
        sid,
        writer,
        "consent",
        {"action": "consented", "text_version": bound.artifacts["instruments.consent"].key},
        ah._stage(migrated, sid)[1],
    )
    # Only this disposable completion-boundary fixture jumps the task.
    with migrated.runtime.begin() as conn:
        conn.execute(update(s.session).where(s.session.c.session_id == sid).values(stage="debrief"))
    ah._run(migrated, store, sid, writer, "debrief_ack", {}, ah._stage(migrated, sid)[1])
    ah._run(
        migrated,
        store,
        sid,
        writer,
        "data_use_choice",
        {"choice": "use_all"},
        ah._stage(migrated, sid)[1],
    )
    out = execute_command(
        migrated.runtime,
        ah._cmd(sid, "complete", {}, ah._stage(migrated, sid)[1]),
        lifecycle.completion_handler(store=store, writer=writer),
    )
    assert out.accepted, out.reason
    assert out.result["completion_code"] == "PLACEHOLDER-CODE"
    assert out.result["entitlement"]["status"] == "ineligible"
    assert out.result["entitlement"]["cents"] == 0
    # Reproduce historical bad statuses without rewriting any actual study row.
    with migrated.superuser.begin() as conn:
        conn.execute(
            update(s.payment_entitlement)
            .where(s.payment_entitlement.c.session_id == sid)
            .values(status="pending", cents=350)
        )
    with pytest.raises(adminops.AdminActionError, match="non_live_session"):
        adminops.decide_entitlement(
            migrated.runtime, sid, "base", decision="approved", actor="test"
        )
    with migrated.superuser.begin() as conn:
        conn.execute(
            update(s.payment_entitlement)
            .where(s.payment_entitlement.c.session_id == sid)
            .values(status="approved")
        )
    with pytest.raises(adminops.AdminActionError, match="non_live_session"):
        adminops.mark_paid(migrated.runtime, sid, "base", paid_ref="disposable", actor="test")


@pytest.mark.parametrize("kind", ["render", "render_first_final"])
def test_own_share_render_receipts_resolve_only_verified_session_shares(
    migrated: MigratedDatabase, make_settings: Any, kind: str
) -> None:
    store = ContentStore.load_default()
    result = ah._drive_preview_to_task(migrated, store, "tp_demo_v3", "high", "ab")
    sid, writer = result.session_id, result.writer_id
    with migrated.runtime.connect() as conn:
        rid = conn.execute(
            select(s.file_membership.c.record_id)
            .where(s.file_membership.c.session_id == sid)
            .limit(1)
        ).scalar_one()
    shared = ah._run(
        migrated,
        store,
        sid,
        writer,
        "record_share",
        {"record_id": rid},
        ah._stage(migrated, sid)[1],
    )
    assert shared.accepted, shared.reason
    with migrated.runtime.connect() as conn:
        seq = conn.execute(
            select(s.share.c.delivered_seq).where(s.share.c.session_id == sid)
        ).scalar_one()
    with ah._authed_client(make_settings, migrated, result) as client:
        for identifier in (
            f"share-{seq}",
            "share-999999999999",
            "share-9999999999999999999",
            "share-NaN",
            "unresolvable-id",
        ):
            response = client.post(
                "/telemetry",
                headers={"x-csrf-token": "csrf-test-token"},
                json={
                    "kind": kind,
                    "client_ms": 100,
                    "data": {"message_id": identifier},
                },
            )
            assert response.status_code == 200, response.text
    with migrated.runtime.connect() as conn:
        receipt = conn.execute(
            select(s.delivery_receipt.c.rendered_at).where(
                s.delivery_receipt.c.session_id == sid, s.delivery_receipt.c.seq == seq
            )
        ).scalar_one()
        assert receipt is not None
