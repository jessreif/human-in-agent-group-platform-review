"""BA-13 (INV-0706/0212): two-stage acknowledgment behavior, behaviorally verified.

The manifest schema pins ``acknowledgment_resets_substantive_schedule`` and
``acknowledgment_counts_as_fulfillment`` as ``Literal[False]`` — a schema pin is a
promise, not a behavior. These tests exercise the real job/episode machinery:

- an acknowledgment delivery does not move the substantive ``deliver`` job's due
  instant and does not close the request episode;
- the episode stays open (``pending``) until the substantive response commits.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, update

from app.db import schema as s
from app.domain.manifests.store import ContentStore
from app.domain.rng import RandomStreams

from . import audit_helpers as ah
from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _episode(db: MigratedDatabase, sid: uuid.UUID):
    with db.runtime.connect() as conn:
        return (
            conn.execute(
                select(s.request_episode)
                .where(s.request_episode.c.session_id == sid)
                .order_by(s.request_episode.c.opened_at.desc())
            )
            .mappings()
            .first()
        )


def _deliver_jobs(db: MigratedDatabase, sid: uuid.UUID, response_class: str):
    return [
        j
        for j in ah.jobs(db, sid)
        if j["kind"] == "deliver" and (j["payload"] or {}).get("response_class") == response_class
    ]


def test_acknowledgment_does_not_reset_or_fulfill_substantive(
    migrated: MigratedDatabase, store: ContentStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = ah.drive_to_task(migrated, store, "pe_demo_v3", "TS1", "b-ts1")
    sid, writer = result.session_id, result.writer_id

    # Open a structured request: the episode is pending and the substantive
    # ``deliver`` job is scheduled at its sampled due instant.
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "teammate_request",
        {"crate": 1},
        ah.stage(migrated, sid)[1],
    )
    substantive = _deliver_jobs(migrated, sid, "structured_record")
    assert len(substantive) == 1 and substantive[0]["status"] == "pending"
    due_before = int(substantive[0]["due_active_ms"])

    # Force the REF-PE-03 acknowledgment draw to fire (probabilities are all >0).
    monkeypatch.setattr(RandomStreams, "uniform", lambda self, *a: 0.0)
    with migrated.runtime.connect() as conn:
        record_id = conn.execute(
            select(s.record.c.record_id)
            .select_from(
                s.file_membership.join(
                    s.record,
                    (s.record.c.session_id == s.file_membership.c.session_id)
                    & (s.record.c.record_id == s.file_membership.c.record_id),
                )
            )
            .where(s.file_membership.c.session_id == sid, s.record.c.crate == 1)
            .limit(1)
        ).scalar_one()
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "record_share",
        {"record_id": record_id},
        ah.stage(migrated, sid)[1],
    )
    acks = _deliver_jobs(migrated, sid, "acknowledgment")
    # The share is unprompted (it answers no teammate ask) — it still draws an
    # acknowledgment against the requesting-seat weights under REF-PE-03.
    assert len(acks) == 1, "the share must schedule exactly one acknowledgment job"

    # The substantive job's due instant is unchanged by scheduling the acknowledgment.
    assert int(_deliver_jobs(migrated, sid, "structured_record")[0]["due_active_ms"]) == due_before

    # Commit the acknowledgment first (pin its due below the substantive due).
    with migrated.runtime.begin() as conn:
        conn.execute(
            update(s.scheduled_job)
            .where(s.scheduled_job.c.job_id == acks[0]["job_id"])
            .values(due_active_ms=max(1, due_before - 1_000), claimable_at=func.now())
        )
    ah.set_active(migrated, sid, due_before - 500)
    ah.tick(migrated, store)
    acks = _deliver_jobs(migrated, sid, "acknowledgment")
    assert acks[0]["status"] == "committed"

    # Acknowledgment delivered ≠ fulfillment: the episode remains open with no
    # substantive seq and no close reason; the substantive job is still pending
    # at its original due instant.
    ep = _episode(migrated, sid)
    assert ep is not None
    assert ep["closed_at"] is None and ep["substantive_event_seq"] is None
    assert ep["status"] in ("pending", "acknowledged", "responded")
    sub = _deliver_jobs(migrated, sid, "structured_record")[0]
    assert sub["status"] == "pending" and int(sub["due_active_ms"]) == due_before

    # The substantive delivery resolves the episode with its own event seq.
    ah.set_active(migrated, sid, due_before + 500)
    ah.tick(migrated, store)
    ep = _episode(migrated, sid)
    assert ep["status"] == "resolved" and ep["close_reason"] == "delivered"
    assert ep["substantive_event_seq"] is not None and ep["closed_at"] is not None
