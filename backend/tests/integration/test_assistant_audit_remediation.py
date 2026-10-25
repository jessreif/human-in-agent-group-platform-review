"""Permanent end-to-end regression tests for F01/F03/F04/F05/F09/F14."""

import pytest
from sqlalchemy import select, update

from app.db import schema as s
from app.domain.manifests.store import ContentStore
from app.services import output_claims, prompts, snapshots
from tests.integration.test_wp06_engines import (
    _drive_to_task,
    _job,
    _messages,
    _run,
    _set_active,
    _stage,
)
from tests.integration.test_wp10a_generation import (
    _CaptureProvider,
    _file_records,
    _model_calls,
    _shown,
    _tick_provider,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def store():
    return ContentStore.load_default()


def task(db, store, suffix):
    return _drive_to_task(
        db,
        store,
        "pe_demo_v3",
        f"TEST_AUDIT_{suffix}",
        f"audit-{suffix}",
        seed_hex="12" * 32,
    )


def ask(db, store, result, body, crate=None):
    payload = {"body": body}
    if crate is not None:
        payload["crate"] = crate
    _run(
        db,
        store,
        result.session_id,
        result.writer_id,
        "assistant_request",
        payload,
        _stage(db, result.session_id)[1],
    )
    return _job(db, result.session_id, "generate")


def pin_only(db, sid, job, ids):
    with db.runtime.begin() as conn:
        snap = snapshots.build_snapshot(
            conn,
            sid,
            seat="assistant",
            purpose="assistant_context",
            visible_event_seq=1,
            record_ids=ids,
            created_event_seq=1,
        )
        payload = dict(job["payload"], snapshot_id=str(snap))
        conn.execute(
            update(s.scheduled_job)
            .where(s.scheduled_job.c.job_id == job["job_id"])
            .values(payload=payload)
        )
    return snap


def test_destination_only_delivers_supported_claim(migrated, store):
    result = task(migrated, store, "destination")
    sid = result.session_id
    with migrated.runtime.connect() as conn:
        rec = (
            conn.execute(
                select(s.record).where(
                    s.record.c.session_id == sid,
                    s.record.c.event_type == "transit",
                )
            )
            .mappings()
            .first()
        )
    job = ask(migrated, store, result, f"Where is crate {rec['crate']}?", rec["crate"])
    snap = pin_only(migrated, sid, job, [rec["record_id"]])
    text = f"Crate {rec['crate']} is heading toward {rec['toward']}."
    with migrated.runtime.connect() as conn:
        manifest = conn.execute(
            select(s.knowledge_snapshot.c.record_manifest).where(
                s.knowledge_snapshot.c.session_id == sid, s.knowledge_snapshot.c.snapshot_id == snap
            )
        ).scalar_one()
        rows = prompts.load_manifest_records(conn, sid, manifest)
    assert rows[0]["toward"] == rec["toward"]
    assert (
        output_claims.check_assistant_output(text, crate=rec["crate"], records=rows).violations
        == ()
    )
    _tick_provider(migrated, store, _CaptureProvider(text))
    call = _model_calls(migrated, sid)[0]
    assert call["status"] == "succeeded"
    assert call["validation"]["violations"] == []
    deliver = _job(migrated, sid, "deliver")
    _set_active(migrated, sid, int(deliver["due_active_ms"]) + 50)
    _tick_provider(migrated, store, _CaptureProvider(text))
    assert _shown(migrated, sid)[0]["fallback"] == "none"


def test_duplicate_citations_leave_durable_outcome_and_delivery(migrated, store):
    result = task(migrated, store, "duplicates")
    sid = result.session_id
    rec = next(r for r in _file_records(migrated, sid) if r["event_type"] == "still_at")
    job = ask(migrated, store, result, f"Where is crate {rec['crate']}?", rec["crate"])
    rid = rec["record_id"]
    provider = _CaptureProvider(
        f"Entries {rid} and {rid} put crate {rec['crate']} at {rec['location_id']} yesterday."
    )
    _tick_provider(migrated, store, provider)
    assert provider.requests
    assert len(_model_calls(migrated, sid)) == 1
    assert _job(migrated, sid, "deliver") is not None
    with migrated.runtime.connect() as conn:
        status = conn.execute(
            select(s.scheduled_job.c.status).where(s.scheduled_job.c.job_id == job["job_id"])
        ).scalar_one()
    assert status == "committed"


def test_wrong_citation_gate_and_ledger_agree(migrated, store):
    result = task(migrated, store, "citations")
    sid = result.session_id
    rows = _file_records(migrated, sid)
    rec = next(r for r in rows if r["event_type"] == "still_at")
    wrong = next(r for r in rows if r["crate"] != rec["crate"])
    ask(migrated, store, result, f"Where is crate {rec['crate']}?", rec["crate"])
    text = (
        f"Entry {wrong['record_id']} puts crate {rec['crate']} at {rec['location_id']} yesterday."
    )
    _tick_provider(migrated, store, _CaptureProvider(text))
    call = _model_calls(migrated, sid)[0]
    assert call["status"] == "validation_rejected"
    assert all(c["verdict"] == "unsupported" for c in call["validation"]["claims"])
    with migrated.runtime.connect() as conn:
        ledger = (
            conn.execute(
                select(s.claim).where(
                    s.claim.c.session_id == sid, s.claim.c.model_call_id == call["call_id"]
                )
            )
            .mappings()
            .all()
        )
    assert ledger and all(c["support_status"] == "unsupported" for c in ledger)


# A fixed absent id: xdist workers must collect the same node ids.
@pytest.mark.parametrize("snapshot", ["00000000-0000-4000-8000-000000000099", "not-a-uuid"])
def test_missing_snapshot_delivers_nonfactual_failure(migrated, store, snapshot):
    result = task(migrated, store, "missing")
    sid = result.session_id
    job = ask(migrated, store, result, "Where is crate 3?", 3)
    with migrated.runtime.begin() as conn:
        payload = dict(job["payload"], snapshot_id=snapshot)
        conn.execute(
            update(s.scheduled_job)
            .where(s.scheduled_job.c.job_id == job["job_id"])
            .values(payload=payload)
        )
    provider = _CaptureProvider("not used")
    _tick_provider(migrated, store, provider)
    assert not provider.requests
    assert _model_calls(migrated, sid) == []
    deliver = _job(migrated, sid, "deliver")
    assert deliver is not None and deliver["payload"]["call_id"] is None
    _set_active(migrated, sid, int(deliver["due_active_ms"]) + 50)
    _tick_provider(migrated, store, provider)
    assert _shown(migrated, sid)[0]["call_id"] is None
    assert "verify the saved file" in _shown(migrated, sid)[0]["shown_text"]
    assert [
        m
        for m in _messages(migrated, sid)
        if m["channel"] == "assistant" and m["actor"] == "assistant"
    ]


def test_bare_followup_pins_subject(migrated, store):
    result = task(migrated, store, "bare")
    provider = _CaptureProvider("I only see the entries in your file.")
    ask(migrated, store, result, "What about 3?")
    _tick_provider(migrated, store, provider)
    assert provider.requests[0].messages[1].content.startswith("The question is about Crate 3")


def test_retry_limit_dead_letters_without_provider(migrated, store):
    result = task(migrated, store, "retry")
    sid = result.session_id
    job = ask(migrated, store, result, "Where is crate 3?", 3)
    with migrated.runtime.begin() as conn:
        conn.execute(
            update(s.scheduled_job)
            .where(s.scheduled_job.c.job_id == job["job_id"])
            .values(attempts=3)
        )
    provider = _CaptureProvider("not used")
    _tick_provider(migrated, store, provider)
    assert not provider.requests
    with migrated.runtime.connect() as conn:
        status = conn.execute(
            select(s.scheduled_job.c.status).where(s.scheduled_job.c.job_id == job["job_id"])
        ).scalar_one()
        episodes = (
            conn.execute(
                select(s.request_episode.c.status, s.request_episode.c.close_reason).where(
                    s.request_episode.c.session_id == sid
                )
            )
            .mappings()
            .all()
        )
        events = (
            conn.execute(select(s.event.c.type).where(s.event.c.session_id == sid)).scalars().all()
        )
    assert status == "failed"
    assert "generation_terminal_failure" in events
    assert any(e["close_reason"] == "generation_terminal_failure" for e in episodes)


def test_contents_gate_probe_never_calls_provider(migrated, store):
    result = task(migrated, store, "contents")
    sid = result.session_id
    provider = _CaptureProvider("not used")
    ask(migrated, store, result, "Where are the power tools?")
    _tick_provider(migrated, store, provider)
    assert provider.requests == []
    assert _model_calls(migrated, sid) == []
    deliver = _job(migrated, sid, "deliver")
    assert deliver["payload"]["fallback"] == "deterministic_contents_summary"
