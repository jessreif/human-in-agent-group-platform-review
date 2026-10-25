"""Full required-build tail plus independent delivered-fidelity SQL recomputation."""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import select, text

from app.db import schema as s
from app.domain.manifests.store import ContentStore
from app.export.study import _participant_wide
from harness.cli import _StubProvider
from harness.personas import Action, NormalPersona
from harness.runner import Runner

from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration


class _SocialPersona(NormalPersona):
    def on_task(self, view, bound):
        if not self.mem.get("risk_social"):
            self.mem["risk_social"] = True
            return [Action("social_reply", {"body": "How are you organizing your notes?"})]
        return super().on_task(view, bound)


@pytest.mark.parametrize(
    "manifest_id", ["tp_ai_v2", "tp_scripted_v4", "tp_ai_v3", "tp_scripted_v5"]
)
def test_launch_manifest_reaches_durable_completion_and_exports_fidelity(
    migrated: MigratedDatabase, manifest_id: str
) -> None:
    runner = Runner(migrated, ContentStore.load_default(), provider=_StubProvider())
    batch = f"risk.{manifest_id}"
    runner.seed_batch(manifest_id, batch)
    report = runner.run_session(manifest_id, batch, _SocialPersona(11), seed=11)
    assert report.stage == "completion", report.findings
    sid = uuid.UUID(report.session_id)
    with migrated.runtime.connect() as conn:
        receipt = (
            conn.execute(
                select(s.completion_receipt).where(s.completion_receipt.c.session_id == sid)
            )
            .mappings()
            .one()
        )
        assert receipt["outcome"] == "completed"
        assert receipt["handoff"]["completion_code"]
        assert not conn.execute(
            select(s.item_response.c.item_id).where(
                s.item_response.c.session_id == sid, s.item_response.c.status == "skipped"
            )
        ).all()
        if manifest_id in ("tp_ai_v3", "tp_scripted_v5"):
            requests = (
                conn.execute(
                    select(s.message.c.body)
                    .join(
                        s.request,
                        (s.message.c.session_id == s.request.c.session_id)
                        & (s.message.c.request_id == s.request.c.request_id),
                    )
                    .where(
                        s.message.c.session_id == sid,
                        s.request.c.requester.in_(("JT", "PM")),
                        s.message.c.scheduled_for_active_ms.in_((80_000, 180_000, 270_000)),
                    )
                )
                .scalars()
                .all()
            )
            assert len(requests) == 3
            assert len({re.sub(r"\d+", "{n}", line) for line in requests}) == 3
        session = dict(
            conn.execute(select(s.session).where(s.session.c.session_id == sid)).mappings().one()
        )
        wide = _participant_wide(conn, [session])[0]
        independent = conn.execute(
            text(
                "SELECT count(*) AS total, count(*) FILTER "
                "(WHERE o.fallback = 'none' AND c.status = 'succeeded') AS generated "
                "FROM app.displayed_output o JOIN app.message m "
                "ON (m.session_id, m.message_id) = (o.session_id, o.message_id) "
                "LEFT JOIN app.model_call c "
                "ON (c.session_id, c.call_id) = (o.session_id, o.call_id) "
                "WHERE o.session_id = :sid AND m.actor IN ('JT','PM') "
                "AND m.channel = 'team' AND m.status = 'delivered'"
            ),
            {"sid": sid},
        ).one()
        assert wide["teammate_reply_denominator"] == independent.total
        assert independent.total > 0
        assert wide["teammate_generated_replies"] == independent.generated
        assert wide["teammate_condition"] == (
            "ai" if manifest_id.startswith("tp_ai") else "scripted"
        )
        assert wide["teammate_generated_reply_share"] == (
            independent.generated / independent.total
            if manifest_id.startswith("tp_ai") and independent.total
            else None
        )
