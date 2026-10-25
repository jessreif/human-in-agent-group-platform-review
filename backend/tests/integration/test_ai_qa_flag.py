"""AI_QA_ENABLED — disposable-DB acceptance runs.

Addition flag (new archetype): flag-on sessions route training completion
through the optional ``ai_qa`` stage; flag-off keeps the legacy direct
``training -> scripted_connecting`` edge.

- Flag off: no ai_qa stage, no qa_exchange rows, no qa_answer jobs; the
  session completes unchanged.
- Flag on + continue without asking: the always-active ``proceed`` advances
  to scripted_connecting with zero exchanges — a question is never required.
- Flag on + ask: qa_ask commits a pending exchange and plants qa_answer;
  the generation lane resolves it through the stub provider (status
  ``answered``) and through provider=None (deterministic ``fallback``).
- Replay/adminviews: the ai_qa page renders the committed exchanges.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select, update

from app.db import schema as s
from app.db.tx import execute_command
from app.domain.manifests.binding import bind
from app.domain.manifests.store import ContentStore
from app.flags import FeatureFlags
from app.providers.base import ProviderRequest, ProviderResponse
from app.services import credentials, lifecycle, qa, scheduler
from app.services.entry import EntryResult, enter
from harness.cli import _StubProvider
from harness.personas import NormalPersona, QaAskerPersona
from harness.runner import Runner
from tests.integration.test_wp08_workbench_api import (
    BUILD_SHA,
    _authed_client,
    _cmd,
    _query,
    _seed_batch,
    _stage,
)

from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration

MANIFEST = "tp_ai_v11"


def _runner(db: MigratedDatabase, *, ai_qa: bool, provider=None) -> Runner:
    return Runner(db, ContentStore.load_default(), provider=provider, ai_qa=ai_qa)


def _stages(db: MigratedDatabase, sid: uuid.UUID) -> list[str]:
    with db.runtime.connect() as conn:
        return [
            r.to_stage
            for r in conn.execute(
                select(s.stage_transition.c.to_stage)
                .where(s.stage_transition.c.session_id == sid)
                .order_by(s.stage_transition.c.revision)
            )
        ]


def _exchanges(db: MigratedDatabase, sid: uuid.UUID) -> list[dict]:
    with db.runtime.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                select(s.qa_exchange)
                .where(s.qa_exchange.c.session_id == sid)
                .order_by(s.qa_exchange.c.seq)
            ).mappings()
        ]


def _job_kinds(db: MigratedDatabase, sid: uuid.UUID) -> set[str]:
    with db.runtime.connect() as conn:
        return set(
            conn.execute(
                select(s.scheduled_job.c.kind).where(s.scheduled_job.c.session_id == sid)
            ).scalars()
        )


def test_flag_off_skips_ai_qa_stage(migrated: MigratedDatabase) -> None:
    runner = _runner(migrated, ai_qa=False, provider=_StubProvider())
    batch = f"aiqa-flag.off.{uuid.uuid4().hex[:8]}"
    runner.seed_batch(MANIFEST, batch)
    report = runner.run_session(MANIFEST, batch, NormalPersona(7), seed=7)
    assert report.stage == "completion", report.findings
    sid = uuid.UUID(report.session_id)
    stages = _stages(migrated, sid)
    assert "ai_qa" not in stages
    assert "scripted_connecting" in stages
    assert _exchanges(migrated, sid) == []
    assert "qa_answer" not in _job_kinds(migrated, sid)


def test_flag_on_continue_without_asking(migrated: MigratedDatabase) -> None:
    """Continue is always active: the default persona asks nothing and the
    session still completes through ai_qa -> scripted_connecting."""
    runner = _runner(migrated, ai_qa=True, provider=_StubProvider())
    batch = f"aiqa-flag.noask.{uuid.uuid4().hex[:8]}"
    runner.seed_batch(MANIFEST, batch)
    report = runner.run_session(MANIFEST, batch, NormalPersona(7), seed=7)
    assert report.stage == "completion", report.findings
    sid = uuid.UUID(report.session_id)
    stages = _stages(migrated, sid)
    assert "ai_qa" in stages
    assert stages.index("ai_qa") < stages.index("scripted_connecting")
    assert _exchanges(migrated, sid) == []
    assert "qa_answer" not in _job_kinds(migrated, sid)


def test_flag_on_question_answered_by_provider(migrated: MigratedDatabase) -> None:
    """qa_ask commits a pending exchange + plants qa_answer; the generation
    lane resolves it through the local provider as ``answered``."""
    runner = _runner(migrated, ai_qa=True, provider=_StubProvider())
    batch = f"aiqa-flag.ask.{uuid.uuid4().hex[:8]}"
    runner.seed_batch(MANIFEST, batch)
    report = runner.run_session(MANIFEST, batch, QaAskerPersona(7), seed=7)
    assert report.stage == "completion", report.findings
    sid = uuid.UUID(report.session_id)
    rows = _exchanges(migrated, sid)
    assert len(rows) == 1
    row = rows[0]
    assert row["question"] == "How do I ask my teammates for intel about a crate?"
    assert row["status"] == "answered"
    assert row["answer"] and row["answer"] != qa.FALLBACK_ANSWER
    assert row["provider"] == "sim-stub"
    assert row["answered_seq"] is not None and row["answered_at"] is not None
    audit = row["generation_audit"]
    assert audit["prompt_id"] == qa.PROMPT_ID
    assert len(audit["prompt_sha256"]) == 64
    assert audit["messages"][-1]["content"] == row["question"]
    assert audit["parameters"]["temperature"] == 0.2
    assert audit["parameters"]["max_tokens"] == qa.MAX_TOKENS
    assert audit["timeout_ms"] == qa.TIMEOUT_MS
    assert audit["dispatched"] is True
    assert "qa_answer" in _job_kinds(migrated, sid)
    # Admin replay: the pages payload carries the committed exchange for the
    # ai_qa page render.
    pages = (report.replay or {}).get("participant", {}).get("pages", {})
    assert pages.get("qa_exchanges") and pages["qa_exchanges"][0]["answer"] == row["answer"]


def _drive_to_ai_qa(
    db: MigratedDatabase,
    store: ContentStore,
    pid: str,
    batch: str,
    *,
    manifest_id: str = "tp_demo_v3",
    provider_model: str = lifecycle.UNRESOLVED_MODEL,
) -> EntryResult:
    """Entry -> consent -> training -> comprehension under ai_qa_enabled=True —
    the HTTP-level counterpart of the Runner drives above."""
    if manifest_id == "tp_demo_v3":
        _seed_batch(db, store, batch)
    else:
        Runner(db, store).seed_batch(manifest_id, batch)
    bound = bind(store, manifest_id, mode="qa")
    result = enter(
        db.runtime,
        store,
        query=_query(pid, batch),
        app_mode="qa",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=BUILD_SHA,
    )
    sid, writer = result.session_id, result.writer_id

    def run(kind: str, payload: dict[str, object]) -> None:
        out = execute_command(
            db.runtime,
            _cmd(sid, kind, payload, _stage(db, sid)[1]),
            lifecycle.handler_for(
                kind,
                store=store,
                behavior_sha=BUILD_SHA,
                writer=writer,
                flags=FeatureFlags(ai_qa=True),
                provider_model_digest=provider_model,
            ),
        )
        assert out.accepted, (kind, out.reason)

    run(
        "consent",
        {"action": "consented", "text_version": bound.artifacts["instruments.consent"].key},
    )
    run("training_step", {"step": 1})
    run(
        "training_step",
        {"step": "practice", "action": "share", "record_id": "PRACTICE-K3-04"},
    )
    run(
        "training_step",
        {"step": "practice", "action": "pick_location", "crate": 3, "location": "The Sawmill"},
    )
    run(
        "training_step",
        {"step": "practice", "action": "ask", "text": "anything on crate 3?"},
    )
    run(
        "training_step",
        {"step": "comprehension", "complete": True, "answers": dict(lifecycle.COMPREHENSION_KEY)},
    )
    assert _stage(db, sid)[0] == "ai_qa"
    return result


def test_flag_on_workbench_serves_qa_projection(
    migrated: MigratedDatabase, make_settings: Any
) -> None:
    """HTTP regression: ``GET /workbench`` on ``ai_qa`` must serve the ``qa``
    exchange list. The projection once ran on an already-closed connection
    (ResourceClosedError -> 500) because it sat outside ``engine.connect()`` —
    invisible to the flag-off path, which short-circuits before querying."""
    store = ContentStore.load_default()
    result = _drive_to_ai_qa(migrated, store, "WBQ", f"aiqa-wb.{uuid.uuid4().hex[:8]}")
    client = _authed_client(make_settings, migrated, result, AI_QA_ENABLED=True)
    with client:
        wb = client.get("/workbench")
        assert wb.status_code == 200
        body = wb.json()
        assert body["stage"] == "ai_qa"
        assert body["qa"] == []

        rev = client.get("/session").json()["session"]["revision"]
        cmd = client.post(
            "/commands",
            json={
                "kind": "qa_ask",
                "command_id": str(uuid.uuid4()),
                "expected_revision": rev,
                "payload": {"question": "How many crates do I answer for?"},
            },
            headers={credentials.CSRF_HEADER: "csrf-test-token"},
        )
        assert cmd.status_code == 200, cmd.json()
        assert cmd.json()["accepted"] is True

        wb2 = client.get("/workbench")
        assert wb2.status_code == 200
        exchanges = wb2.json()["qa"]
        assert [e["seq"] for e in exchanges] == [1]
        assert exchanges[0]["question"] == "How many crates do I answer for?"
        assert exchanges[0]["status"] == "pending"


def test_flag_on_question_falls_back_without_provider(migrated: MigratedDatabase) -> None:
    """No provider → the deterministic fallback resolves the exchange as
    ``fallback`` with the outcome recorded — never left pending."""
    runner = _runner(migrated, ai_qa=True, provider=None)
    batch = f"aiqa-flag.fallback.{uuid.uuid4().hex[:8]}"
    runner.seed_batch(MANIFEST, batch)
    report = runner.run_session(MANIFEST, batch, QaAskerPersona(7), seed=7)
    assert report.stage == "completion", report.findings
    sid = uuid.UUID(report.session_id)
    rows = _exchanges(migrated, sid)
    assert len(rows) == 1
    assert rows[0]["status"] == "fallback"
    assert rows[0]["answer"] == qa.FALLBACK_ANSWER
    assert rows[0]["outcome"] == "unavailable"
    assert rows[0]["generation_audit"]["dispatched"] is False
    assert rows[0]["generation_audit"]["timeout_ms"] == qa.TIMEOUT_MS


class CapturingProvider:
    provider_id = "qa-test"
    is_double = True
    is_remote = False
    model_id = "qa-test@sha256:" + "d" * 64

    def __init__(self) -> None:
        self.calls: list[tuple[ProviderRequest, float]] = []
        self.finish_reason = "stop"

    def generate(self, request: ProviderRequest, *, timeout_s: float) -> ProviderResponse:
        self.calls.append((request, timeout_s))
        return ProviderResponse(
            "You answer four crates.",
            self.provider_id,
            self.model_id,
            1,
            self.finish_reason,
            {**request.params, "served_digest": "d" * 64},
        )


@pytest.mark.parametrize("mismatch", ["none", "model", "build", "ops_only", "legacy", "invalid"])
def test_live_qa_checks_consent_pin_and_records_effective_parameters(
    migrated: MigratedDatabase, monkeypatch: pytest.MonkeyPatch, mismatch: str
) -> None:
    store = ContentStore.load_default()
    provider = CapturingProvider()
    result = _drive_to_ai_qa(
        migrated,
        store,
        "QA-PIN",
        f"aiqa-pin.{uuid.uuid4().hex[:8]}",
        manifest_id=MANIFEST,
        provider_model=provider.model_id,
    )
    sid = result.session_id
    outcome = execute_command(
        migrated.runtime,
        _cmd(sid, "qa_ask", {"question": "How many crates?"}, _stage(migrated, sid)[1]),
        lifecycle.handler_for(
            "qa_ask",
            store=store,
            behavior_sha=BUILD_SHA,
            writer=result.writer_id,
            flags=FeatureFlags(ai_qa=True),
        ),
    )
    assert outcome.accepted, outcome.reason
    with migrated.superuser.begin() as conn:
        conn.execute(
            update(s.session)
            .where(s.session.c.session_id == sid)
            .values(mode="live", behavior_source_sha256=BUILD_SHA)
        )
        job = (
            conn.execute(
                select(s.scheduled_job).where(
                    s.scheduled_job.c.session_id == sid, s.scheduled_job.c.kind == "qa_answer"
                )
            )
            .mappings()
            .one()
        )
        assert job["payload"]["timeout_ms"] == qa.TIMEOUT_MS
        assert job["payload"]["generation_policy"]["model"] == provider.model_id
        if mismatch == "legacy":
            payload = dict(job["payload"])
            del payload["generation_policy"]
            del payload["timeout_ms"]
            conn.execute(
                update(s.scheduled_job)
                .where(s.scheduled_job.c.job_id == job["job_id"])
                .values(payload=payload)
            )
    from app import version

    monkeypatch.setattr(
        version,
        "build_info",
        lambda: SimpleNamespace(
            # "build" = a participant-facing change (behavior digest churns);
            # "ops_only" = only the full release checksum churns — the same
            # participant code, so in-flight jobs must still dispatch.
            source_sha256="f" * 64 if mismatch in ("build", "ops_only") else BUILD_SHA,
            behavior_sha256="f" * 64 if mismatch == "build" else BUILD_SHA,
        ),
    )
    if mismatch == "model":
        provider.model_id = "different-model"
    if mismatch == "invalid":
        provider.finish_reason = "length"
    assert (
        scheduler.tick_generation(
            migrated.runtime,
            store,
            worker_id="qa-pin-test",
            provider=provider,
            flags=FeatureFlags(ai_assistant=False, time_pressure=False),
        )
        == 1
    )
    exchange = _exchanges(migrated, sid)[0]
    audit = exchange["generation_audit"]
    assert audit["flags"] == {"ai_assistant": False, "time_pressure": False}
    assert audit["timeout_ms"] == qa.TIMEOUT_MS
    if mismatch in ("model", "build"):
        assert provider.calls == []
        assert exchange["status"] == "fallback"
        assert exchange["outcome"] == "generation_policy_mismatch"
        assert audit["dispatched"] is False
    elif mismatch == "invalid":
        assert exchange["status"] == "fallback"
        assert exchange["outcome"] == "invalid_response"
        assert len(provider.calls) == 1
        assert audit["dispatched"] is True
        assert audit["parameters"]["served_digest"] == "d" * 64
    else:
        assert exchange["status"] == "answered"
        assert len(provider.calls) == 1
        assert provider.calls[0][1] == qa.TIMEOUT_MS / 1000
        assert audit["parameters"]["model_pin"] == provider.model_id
        assert audit["parameters"]["served_digest"] == "d" * 64
