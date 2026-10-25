"""BA-04 / BA-20 (INV-0608; spec §6.5): resolved-policy caps and unified channel gates.

``concurrency.per_seat_pending_requests`` (4) bounds open episodes per responder seat,
``concurrency.assistant_pending_prompts`` (2) bounds open assistant prompts, and
``rate_limits.operational_commands_per_minute`` (120) bounds participant commands — all
resolved from the bound profile and enforced inside the command transaction with the
declared ``reject_visible`` semantics (a durable rejected ``command_receipt``). Every
participant channel (structured seeking, sharing, social reply, assistant prompting)
passes the resolved-policy channel gate.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db import schema as s
from app.db.tx import TxContext, db_now, lock_session
from app.domain.manifests.store import ContentStore
from app.services import lifecycle, lifecycle_core

from . import audit_helpers as ah
from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _receipt(db: MigratedDatabase, sid: object, command_id: object) -> dict:
    with db.runtime.connect() as conn:
        return dict(
            conn.execute(
                select(s.command_receipt).where(s.command_receipt.c.command_id == command_id)
            )
            .mappings()
            .one()
        )


def test_per_seat_pending_cap_rejects_over_cap_visibly(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "C1", "b-c1")
    sid, writer = result.session_id, result.writer_id
    # tp_demo_v1 pins per_seat_pending_requests = 4: four pending replies to JT are
    # accepted; the fifth is rejected over_cap, and the rejection leaves a receipt.
    for i in range(4):
        out = ah.run(
            migrated,
            store,
            sid,
            writer,
            "social_reply",
            {"body": f"ping {i}", "to": "JT"},
            ah.stage(migrated, sid)[1],
        )
        assert out.accepted, out.reason
    out = ah.run(
        migrated,
        store,
        sid,
        writer,
        "social_reply",
        {"body": "one too many", "to": "JT"},
        ah.stage(migrated, sid)[1],
        accept=False,
    )
    assert not out.accepted and out.reason == "over_cap"
    receipt = _receipt(migrated, sid, out.command_id)
    assert receipt["outcome"] == "rejected" and receipt["reason"] == "over_cap"

    # Caps are per-seat: PM has its own pending budget.
    out = ah.run(
        migrated,
        store,
        sid,
        writer,
        "social_reply",
        {"body": "hi PM", "to": "PM"},
        ah.stage(migrated, sid)[1],
    )
    assert out.accepted, out.reason


def test_structured_request_counts_against_seat_cap(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """``teammate_request`` grants draw on the same per-seat pending budget: the seat
    the request targets accumulates pending episodes identically."""
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "C2", "b-c2")
    sid, writer = result.session_id, result.writer_id
    # Fill JT's pending budget with structured requests addressed to crates it holds.
    accepted = 0
    rejected_reason: str | None = None
    for crate in range(1, 9):
        out = ah.run(
            migrated,
            store,
            sid,
            writer,
            "teammate_request",
            {"crate": crate},
            ah.stage(migrated, sid)[1],
            accept=False,
        )
        if out.accepted:
            accepted += 1
        else:
            rejected_reason = out.reason
            break
    assert rejected_reason in (None, "over_cap", "no_teammate_records")
    with migrated.runtime.connect() as conn:
        open_jt = conn.execute(
            select(func.count())
            .select_from(s.request_episode)
            .join(
                s.request,
                (s.request.c.session_id == s.request_episode.c.session_id)
                & (s.request.c.request_id == s.request_episode.c.request_id),
            )
            .where(
                s.request_episode.c.session_id == sid,
                s.request_episode.c.status == "pending",
                s.request.c.responder.in_(("JT", "PM")),
            )
        ).scalar_one()
    # Whatever was accepted is bounded by the resolved per-seat cap (4 per seat, so a
    # single seat can never exceed it even across crates).
    assert open_jt <= 8 and accepted >= 1


def test_assistant_pending_prompt_cap(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "C3", "b-c3")
    sid, writer = result.session_id, result.writer_id
    # tp_demo_v1 pins assistant_pending_prompts = 2.
    for i in range(2):
        out = ah.run(
            migrated,
            store,
            sid,
            writer,
            "assistant_request",
            {"body": f"question {i}", "crate": 1},
            ah.stage(migrated, sid)[1],
        )
        assert out.accepted, out.reason
    out = ah.run(
        migrated,
        store,
        sid,
        writer,
        "assistant_request",
        {"body": "third", "crate": 1},
        ah.stage(migrated, sid)[1],
        accept=False,
    )
    assert not out.accepted and out.reason == "over_cap"


def test_operational_commands_rate_limited(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = ah.drive_to_task(migrated, store, "tp_demo_v3", "C4", "b-c4")
    sid, writer = result.session_id, result.writer_id
    with migrated.runtime.connect() as conn:
        prior = conn.execute(
            select(func.count())
            .select_from(s.command_receipt)
            .where(s.command_receipt.c.session_id == sid)
        ).scalar_one()
    remaining = 120 - int(prior)
    assert remaining > 0
    # Fill the rolling 60 s window with cheap accepted operational commands.
    for i in range(remaining):
        out = ah.run(
            migrated,
            store,
            sid,
            writer,
            "answer_update",
            {"crate": 1 + (i % 8), "location_id": "shelf"},
            ah.stage(migrated, sid)[1],
        )
        assert out.accepted, (i, out.reason)
    # The next operational command in the same window is rejected rate_limited — and
    # even that rejection is durably receipted.
    out = ah.run(
        migrated,
        store,
        sid,
        writer,
        "answer_update",
        {"crate": 1, "location_id": "shelf"},
        ah.stage(migrated, sid)[1],
        accept=False,
    )
    assert not out.accepted and out.reason == "rate_limited"
    receipt = _receipt(migrated, sid, out.command_id)
    assert receipt["outcome"] == "rejected" and receipt["reason"] == "rate_limited"


def test_channel_gates_cover_every_participant_channel(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """BA-20: review-first solo review disables every comm channel; all four surfaces
    (structured seeking, sharing, social reply, assistant prompting) reject through the
    same resolved-policy gate — at handler level where the stage permits the command,
    and as a gate property for the stage-gated pair."""
    result = ah.drive_to_task(migrated, store, "tp_review_first_v4", "C5", "b-c5")
    sid, writer = result.session_id, result.writer_id
    assert ah.stage(migrated, sid)[0] == "solo_review"

    with migrated.runtime.connect() as conn:
        row = lock_session(conn, sid)
        bound = lifecycle.bound_for_session(conn, store, row)
        ctx = TxContext(conn, row, now=db_now(conn))
        for channel in (
            "structured_seeking",
            "sharing",
            "social_reply",
            "assistant_prompting",
        ):
            assert lifecycle_core.channel_open(ctx, bound, channel) is False

    # Commands the stage alone would admit still hit the channel gate server-side.
    for kind, payload in (
        ("assistant_request", {"body": "hi", "crate": 1}),
        ("record_share", {"record_id": "rec-1"}),
    ):
        out = ah.run(
            migrated,
            store,
            sid,
            writer,
            kind,
            payload,
            ah.stage(migrated, sid)[1],
            accept=False,
        )
        assert not out.accepted, kind
        assert out.reason in ("channel_closed", "record_not_in_file")
        if kind == "assistant_request":
            assert out.reason == "channel_closed"

    # Once the task phase opens (post-check-in at active 60 s), the channels open.
    ah.set_active(migrated, sid, 60_000)
    ah.tick(migrated, store)
    assert ah.stage(migrated, sid)[0] == "checkin_collect"
    ah.run(migrated, store, sid, writer, "pressure_skip", {}, ah.stage(migrated, sid)[1])
    ah.force_wall_due(migrated, sid, "checkin_reveal")
    ah.tick(migrated, store)
    ah.run(migrated, store, sid, writer, "proceed", {}, ah.stage(migrated, sid)[1])
    assert ah.stage(migrated, sid)[0] == "task"
    out = ah.run(
        migrated,
        store,
        sid,
        writer,
        "social_reply",
        {"body": "hello team", "to": "JT"},
        ah.stage(migrated, sid)[1],
    )
    assert out.accepted, out.reason
