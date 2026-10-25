"""BA-96..BA-102 audit remediation acceptance (fielded manifest tp_review_first_v10).

BA-96: the fielded profile actually calls the approved local model — a live v10
session records digest-pinned ``local_openai_chat`` ``model_call`` rows for a
non-contents social reply and a non-contents assistant ask (the amendment's
generated-contribution treatment, not ``deterministic_only``).

BA-97: the consent-v4 rule is the entitlement rule — ``completed`` and
``technical_stop`` keep the $4.00 base; ``abandoned``, ``withdrawn`` and
``declined`` (early leave) forfeit it.

BA-99: the live gate is approved-only — v10 binds approved successors; v9
(binding reference artifacts) fails live.

BA-100: ``record_share`` rejects non-``initial`` file memberships server-side.

BA-101: export trees are written owner/group-only (0750/0640), never 0777.
"""

from __future__ import annotations

import json
import stat
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, insert, select, update

from app.db import schema as s
from app.db.tx import execute_command
from app.domain.manifests.binding import BindError, bind
from app.domain.manifests.store import ContentStore
from app.providers.base import ProviderResponse
from app.services import lifecycle, scheduler
from app.services.entry import EntryResult, enter
from app.services.sessions import register_manifest
from tests.integration import audit_helpers as ah

from .conftest import MigratedDatabase

# No module-level integration mark. Only cases that take ``migrated`` are
# integration. Binding, consent text, and export file mode do not open a
# database. Do not put the module marker back.


V10 = "tp_review_first_v10"
DEPLOYED_MODEL_ID = "gemma4:26b"
DEPLOYED_DIGEST = "5571076f3d70050487b26b341705799e0ab29b808164f90d20d4cf84f699d251"


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _seed_live(db: MigratedDatabase, store: ContentStore, batch: str) -> None:
    bound = bind(store, V10, mode="live")
    with db.superuser.begin() as conn:
        register_manifest(conn, bound)
        conn.execute(
            insert(s.batch).values(
                batch_id=batch,
                study_key=bound.manifest.study_key,
                manifest_id=bound.manifest_id,
                mode="live",
                enrollment_open=True,
            )
        )


def _live_enter(db: MigratedDatabase, store: ContentStore, pid: str, batch: str) -> EntryResult:
    _seed_live(db, store, batch)
    return enter(
        db.runtime,
        store,
        query=ah.query(pid, batch),
        app_mode="live",
        preview_key=None,
        current_manifest_sha256={},
        build_source_sha256=ah.BUILD_SHA,
    )


def _live_run(
    db: MigratedDatabase,
    store: ContentStore,
    sid: uuid.UUID,
    writer: uuid.UUID,
    kind: str,
    payload: dict[str, Any],
    revision: int,
    *,
    accept: bool = True,
) -> Any:
    out = execute_command(
        db.runtime,
        ah.cmd(sid, kind, payload, revision),
        lifecycle.handler_for(
            kind,
            store=store,
            behavior_sha=ah.BUILD_SHA,
            writer=writer,
            provider_model_digest=f"{DEPLOYED_MODEL_ID}@sha256:{DEPLOYED_DIGEST}",
        ),
        advance_due=None,
    )
    if accept:
        assert out.accepted, (kind, out.reason)
    return out


def _live_consent(db: MigratedDatabase, store: ContentStore, result: EntryResult) -> None:
    consent_key = bind(store, V10, mode="live").artifacts["instruments.consent"].key
    _live_run(
        db,
        store,
        result.session_id,
        result.writer_id,
        "consent",
        {"action": "consented", "text_version": consent_key},
        1,
    )


def _drive_live_to_task(
    db: MigratedDatabase, store: ContentStore, pid: str, batch: str
) -> EntryResult:
    """Live entry → consent → training → connecting → solo_review → check-in →
    task, on the fielded manifest (the full review-first pre-task flow)."""
    result = _live_enter(db, store, pid, batch)
    sid, writer = result.session_id, result.writer_id
    _live_consent(db, store, result)
    _live_run(db, store, sid, writer, "training_step", {"step": 1}, ah.stage(db, sid)[1])
    _live_run(
        db,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "share", "record_id": "PRACTICE-K3-04"},
        ah.stage(db, sid)[1],
    )
    _live_run(
        db,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "pick_location", "crate": 3, "location": "The Sawmill"},
        ah.stage(db, sid)[1],
    )

    _live_run(
        db,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "ask", "text": "anything on crate 3?"},
        ah.stage(db, sid)[1],
    )
    _live_run(
        db,
        store,
        sid,
        writer,
        "training_step",
        {
            "step": "comprehension",
            "complete": True,
            "answers": dict(lifecycle.COMPREHENSION_KEY),
        },
        ah.stage(db, sid)[1],
    )
    ah.drive_connecting(db, store, sid, writer)
    assert ah.stage(db, sid)[0] == "solo_review"
    # review-first: solo review ends at the 60 s phase boundary → check-in, then
    # the check-in hands off to the team task phase.
    ah.set_active(db, sid, 60_000)
    ah.tick(db, store)
    assert ah.stage(db, sid)[0] == "checkin_collect"
    ah.run(db, store, sid, writer, "pressure_skip", {}, ah.stage(db, sid)[1])
    ah.force_wall_due(db, sid, "checkin_reveal")
    ah.tick(db, store)
    ah.run(db, store, sid, writer, "proceed", {}, ah.stage(db, sid)[1])
    assert ah.stage(db, sid)[0] == "task"
    return result


def _entitlement(db: MigratedDatabase, sid: uuid.UUID) -> Mapping[str, Any] | None:
    with db.runtime.connect() as conn:
        return (
            conn.execute(
                select(
                    s.payment_entitlement.c.status,
                    s.payment_entitlement.c.cents,
                    s.payment_entitlement.c.policy_artifact_id,
                    s.payment_entitlement.c.policy_version,
                ).where(
                    s.payment_entitlement.c.session_id == sid,
                    s.payment_entitlement.c.kind == "base",
                )
            )
            .mappings()
            .one_or_none()
        )


class _LocalProvider:
    """Provider double reporting the deployed local identity (BA-96 acceptance)."""

    provider_id = "local_openai_chat"
    is_double = True
    model_id = f"{DEPLOYED_MODEL_ID}@sha256:{DEPLOYED_DIGEST}"
    model_digest = DEPLOYED_DIGEST

    def __init__(self, text: str) -> None:
        self._text = text
        self.requests: list[Any] = []

    def generate(self, request: Any, *, timeout_s: float) -> ProviderResponse:
        self.requests.append(request)
        return ProviderResponse(
            text=self._text,
            provider_id=self.provider_id,
            model_id=self.model_id,
            latency_ms=2,
            finish_reason="stop",
            params={**dict(request.params), "model_digest": self.model_digest},
        )


def _tick_provider(db: MigratedDatabase, store: ContentStore, provider: object, n: int = 6) -> None:
    for _ in range(n):
        if (
            scheduler.tick(db.runtime, store, worker_id="w1", provider=provider, limit=25)  # type: ignore[arg-type]
            == 0
        ):
            break


def _model_calls(db: MigratedDatabase, sid: uuid.UUID) -> list[Mapping[str, Any]]:
    with db.runtime.connect() as conn:
        return (
            conn.execute(
                select(s.model_call, s.knowledge_snapshot.c.purpose).select_from(
                    s.model_call.join(
                        s.knowledge_snapshot,
                        (s.knowledge_snapshot.c.session_id == s.model_call.c.session_id)
                        & (s.knowledge_snapshot.c.snapshot_id == s.model_call.c.snapshot_id),
                    )
                )
            )
            .mappings()
            .all()
        )


# ----- BA-99: live gate is approved-only --------------------------------------


def test_live_bind_is_approved_only(store: ContentStore) -> None:
    """v10 binds live with all-approved artifacts; v9 — which binds the reference
    versions — fails live, while still binding preview/QA (BA-99 remedy)."""
    bound = bind(store, V10, mode="live")
    assert bound.launch_eligible
    statuses = {a.key: a.artifact.status for a in bound.artifacts.values()}
    assert statuses and set(statuses.values()) == {"approved"}, statuses
    with pytest.raises(BindError, match="unapproved artifacts"):
        bind(store, "tp_review_first_v9", mode="live")
    # The reference versions remain bindable in preview/QA for lab work.
    bind(store, "tp_review_first_v9", mode="preview")
    bind(store, "tp_review_first_v9", mode="qa")


def test_v10_fields_the_model_path(store: ContentStore) -> None:
    """The fielded assistant block names the deployed provider/model/digest and the
    real validator (not the WP10a-pending placeholders), and social generation is
    model-based with a pinned prompt (BA-96)."""
    m = bind(store, V10, mode="live").manifest
    assert m.teammates.social_generation == "model_with_validated_fallback"
    assert m.teammates.social_prompt is not None
    assert m.assistant.provider == f"local_openai_chat/{DEPLOYED_MODEL_ID}@sha256:{DEPLOYED_DIGEST}"
    assert m.assistant.validator_version == "teammate_output_v7"


# ----- BA-96: a live session on the fielded manifest records model calls -------


@pytest.mark.integration
def test_live_v10_social_and_assistant_calls_are_model_calls(
    migrated: MigratedDatabase, store: ContentStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disposable-DB acceptance (BA-96): a live session on the fielded manifest
    records digest-pinned ``local_openai_chat`` ``model_call`` rows for a
    non-contents social reply and a non-contents assistant ask."""
    # The consent-pinned generation policy (F13) must match the worker's build
    # identity — in deployment this is the stamped image env; here it is BUILD_SHA.
    monkeypatch.setenv("INVESTIGATORS_BUILD_SOURCE_SHA256", ah.BUILD_SHA)
    result = _drive_live_to_task(migrated, store, "BA96A", "ba96-live")
    sid, writer = result.session_id, result.writer_id
    provider = _LocalProvider("ok sounds good")
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "social_reply",
        {"body": "how are you doing?", "to": "JT"},
        ah.stage(migrated, sid)[1],
    )
    # a non-contents ask (crate-scoped) routes to the model, not the deterministic
    # contents summary
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "assistant_request",
        {"body": "where was crate 1 last seen?", "crate": 1},
        ah.stage(migrated, sid)[1],
    )
    _tick_provider(migrated, store, provider)
    calls = _model_calls(migrated, sid)
    purposes = {str(c["purpose"]) for c in calls}
    assert len(calls) >= 2, f"expected >=2 model calls, got {len(calls)} ({purposes})"
    by_purpose = {str(c["purpose"]): c for c in calls}
    for purpose in ("social_reply", "assistant_context"):
        call = by_purpose[purpose]
        assert call["provider"] == "local_openai_chat"
        assert call["model_digest"] == DEPLOYED_DIGEST
        assert call["model"] == f"{DEPLOYED_MODEL_ID}@sha256:{DEPLOYED_DIGEST}"
        assert call["status"] in ("succeeded", "validation_rejected")


# ----- BA-97: entitlement matrix is the consent-v4 rule ------------------------


def test_consent_sentence_matches_entitlement_rule(store: ContentStore) -> None:
    """Rendered consent states the same rule the ledger applies (BA-97): finish
    to be paid; skipping questions is unpaid-relevant never; leaving early is
    unpaid. ``technical_stop`` paying is more generous than the consent's
    participant-obligation clause, never less."""
    bound = bind(store, V10, mode="live")
    body = bound.artifacts["instruments.consent"].artifact.body
    text = json.dumps(body.model_dump(mode="json"))
    assert "reach the end of the study and click Submit" in text
    assert "skip questions during the study and doing so will not affect your payment" in text
    assert "leave the study early, you will not be paid" in text


@pytest.mark.integration
def test_entitlement_completed_pays_base(migrated: MigratedDatabase, store: ContentStore) -> None:
    result = _live_enter(migrated, store, "BA97C", "ba97-done")
    sid, writer = result.session_id, result.writer_id
    _live_consent(migrated, store, result)
    with migrated.runtime.begin() as conn:
        conn.execute(update(s.session).where(s.session.c.session_id == sid).values(stage="debrief"))
    ah.run(migrated, store, sid, writer, "debrief_ack", {}, ah.stage(migrated, sid)[1])
    ah.run(
        migrated,
        store,
        sid,
        writer,
        "data_use_choice",
        {"choice": "use_all"},
        ah.stage(migrated, sid)[1],
    )
    out = execute_command(
        migrated.runtime,
        ah.cmd(sid, "complete", {}, ah.stage(migrated, sid)[1]),
        lifecycle.completion_handler(store=store, writer=writer),
        advance_due=None,
    )
    assert out.accepted, out.reason
    ent = _entitlement(migrated, sid)
    assert ent is not None
    assert ent["status"] == "pending" and ent["cents"] == 400
    assert ent["policy_artifact_id"] == "payment.s5_base_only" and ent["policy_version"] == 3


@pytest.mark.integration
def test_entitlement_abandoned_forfeits(migrated: MigratedDatabase, store: ContentStore) -> None:
    """Browser-close abandonment is leaving early — the base is recorded ineligible."""
    result = _live_enter(migrated, store, "BA97A", "ba97-aban")
    _live_consent(migrated, store, result)
    swept = lifecycle.sweep_abandoned(migrated.runtime, store, idle_window_s=0)
    assert swept >= 1
    ent = _entitlement(migrated, result.session_id)
    assert ent is not None and ent["status"] == "ineligible" and ent["cents"] == 0


@pytest.mark.integration
def test_entitlement_withdrawn_forfeits(migrated: MigratedDatabase, store: ContentStore) -> None:
    """Withdrawing is leaving early — consent-v4 says it is unpaid."""
    result = _drive_live_to_task(migrated, store, "BA97W", "ba97-wd")
    out = ah.run(
        migrated,
        store,
        result.session_id,
        result.writer_id,
        "withdraw",
        {},
        ah.stage(migrated, result.session_id)[1],
    )
    assert out.result.get("outcome") in ("withdrawn", "declined")
    ent = _entitlement(migrated, result.session_id)
    assert ent is not None and ent["status"] == "ineligible" and ent["cents"] == 0


@pytest.mark.integration
def test_entitlement_technical_stop_pays_base(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """A check-in inactivity timeout is system-initiated (the participant did not
    leave early): policy artifact technical_stop=base_paid keeps the base — which
    also makes consent's 'skipping questions does not affect payment' literally
    true for an unanswered check-in."""
    result = _live_enter(migrated, store, "BA97T", "ba97-tech")
    sid, writer = result.session_id, result.writer_id
    _live_consent(migrated, store, result)
    rev = ah.stage(migrated, sid)[1]
    _live_run(migrated, store, sid, writer, "training_step", {"step": 1}, rev)
    _live_run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "share", "record_id": "PRACTICE-K3-04"},
        ah.stage(migrated, sid)[1],
    )
    _live_run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "pick_location", "crate": 3, "location": "The Sawmill"},
        ah.stage(migrated, sid)[1],
    )

    _live_run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "ask", "text": "anything on crate 3?"},
        ah.stage(migrated, sid)[1],
    )
    _live_run(
        migrated,
        store,
        sid,
        writer,
        "training_step",
        {
            "step": "comprehension",
            "complete": True,
            "answers": dict(lifecycle.COMPREHENSION_KEY),
        },
        ah.stage(migrated, sid)[1],
    )
    ah.drive_connecting(migrated, store, sid, writer)
    ah.set_active(migrated, sid, 60_000)
    ah.tick(migrated, store)
    assert ah.stage(migrated, sid)[0] == "checkin_collect"
    ah.force_wall_due(migrated, sid, "checkin_inactivity")
    ah.tick(migrated, store)
    assert ah.stage(migrated, sid)[0] == "technical_stop"
    ent = _entitlement(migrated, sid)
    assert ent is not None
    assert ent["status"] == "pending" and ent["cents"] == 400


@pytest.mark.integration
def test_entitlement_declined_is_ineligible(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _live_enter(migrated, store, "BA97D", "ba97-decl")
    consent_key = bind(store, V10, mode="live").artifacts["instruments.consent"].key
    _live_run(
        migrated,
        store,
        result.session_id,
        result.writer_id,
        "consent",
        {"action": "declined", "text_version": consent_key},
        1,
    )
    ent = _entitlement(migrated, result.session_id)
    assert ent is not None and ent["status"] == "ineligible" and ent["cents"] == 0


# ----- BA-100: server enforces the no-re-share rule ----------------------------


@pytest.mark.integration
def test_record_share_requires_initial_source(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """A teammate-delivered (added_from_team) card cannot be re-shared on the
    command — the rejection lands before any share row is written — while an
    initial record still shares (BA-100)."""
    result = _drive_live_to_task(migrated, store, "BA100", "ba100-share")
    sid, writer = result.session_id, result.writer_id
    with migrated.runtime.begin() as conn:
        world_id = conn.execute(
            select(s.record.c.world_id).where(s.record.c.session_id == sid).limit(1)
        ).scalar_one()
        conn.execute(
            insert(s.record).values(
                session_id=sid,
                record_id="TEAM-CARD-1",
                world_id=world_id,
                crate=1,
                stamp="t0",
                body="a teammate-delivered card",
                event_type="arrival",
                location_id="L1",
                timeline_ordinal=999,
                content_sha256="0" * 64,
            )
        )
        conn.execute(
            insert(s.file_membership).values(
                session_id=sid, record_id="TEAM-CARD-1", source="added_from_team"
            )
        )
    denied = ah.run(
        migrated,
        store,
        sid,
        writer,
        "record_share",
        {"record_id": "TEAM-CARD-1"},
        ah.stage(migrated, sid)[1],
        accept=False,
    )
    assert not denied.accepted and denied.reason == "record_not_shareable"
    with migrated.runtime.connect() as conn:
        shared = conn.execute(
            select(func.count())
            .select_from(s.share)
            .where(s.share.c.session_id == sid, s.share.c.record_id == "TEAM-CARD-1")
        ).scalar_one()
        assert shared == 0
    # an initial record still shares
    with migrated.runtime.connect() as conn:
        initial = conn.execute(
            select(s.file_membership.c.record_id)
            .where(
                s.file_membership.c.session_id == sid,
                s.file_membership.c.source == "initial",
            )
            .limit(1)
        ).scalar_one()
    accepted = ah.run(
        migrated,
        store,
        sid,
        writer,
        "record_share",
        {"record_id": initial},
        ah.stage(migrated, sid)[1],
    )
    assert accepted.result.get("shared") is True


# ----- BA-101: export trees are never world-accessible -------------------------


def test_export_tree_modes_are_owner_group_only(tmp_path: Path) -> None:
    """BA-101: the writer pins 0750 dirs / 0640 files regardless of caller umask."""
    from app.export.csvsafe import export_dir, write_csv

    out = tmp_path / "study-test"
    export_dir(out)
    write_csv(out / "session.csv", ["a"], [{"a": 1}])
    d_mode = stat.S_IMODE(out.stat().st_mode)
    f_mode = stat.S_IMODE((out / "session.csv").stat().st_mode)
    assert d_mode == 0o750, oct(d_mode)
    assert f_mode == 0o640, oct(f_mode)
    assert not (f_mode & 0o007) and not (d_mode & 0o027)
