"""Enrollment: platform adapters, eligibility, and the entry transaction (INV-0401/0403/0404/0406).

Query-string platform parameters are **linkage only** — they name the participant's panel
identity for the platform-link record; they never act as credentials (spec §5.1). Session
authorization comes solely from the issued cookie credential.

Adapters
--------
Each platform declares which query parameter carries the participant ID and which carry the
platform's study/session references (stored on ``platform_link`` for payout reconciliation).
Batch selection uses the ``batch`` parameter; it must name an existing, open ``batch``
whose mode matches the deployment mode. Preview links carry a signed ``pt`` descriptor
instead (``services.preview``); they never touch allocation, eligibility or payment.

Eligibility (INV-0404, D08 pending)
-----------------------------------
Repeat participation is checked against ``identity.platform_link``: a prior link for the same
(platform, platform_pid) in *any* Investigators batch means this person already entered.
Re-entry into the *same* batch resumes that session **only when the request carries that
session's live credential** (the issued ``inv_session`` token, INV-0407): a query-string
panel ID is linkage, never a credential (spec §5.1 / CL-S5.1-01), so a re-entry that matches
(platform, pid, batch) but presents no valid live credential is refused with
``credential_recovery_required`` and recorded in ``admin_audit`` — credential loss routes
through the controlled recovery process (INV-1305 scope), never through the panel ID.
Entry into a different batch is rejected as ``repeat_participation`` — the proposed D08
default (prevent repeats across both studies) is applied until D08 resolves otherwise.
No cross-platform deduplication is promised.

One entry transaction creates: manifest registration, batch pin, participant, session,
platform link (live/qa), credential, writer lease, the entry→consent stage transition and the
``session_opened`` event — all-or-nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Connection, Engine

from app.db import schema as s
from app.db.tx import db_now
from app.domain.manifests.binding import BoundManifest, bind
from app.domain.manifests.schema import ArtifactRef, parse_mode
from app.domain.manifests.store import ContentStore
from app.domain.rng import new_seed
from app.services import credentials, enrollment
from app.services.allocation import AllocationError, allocate
from app.services.preview import verify_descriptor
from app.services.sessions import create_session_rows
from app.settings import ConnectCompletion

PLATFORMS = ("prolific", "connect", "lab")
# platform -> (participant id param, study ref param, session ref param)
ADAPTER_PARAMS: dict[str, tuple[str, str | None, str | None]] = {
    "prolific": ("PROLIFIC_PID", "STUDY_ID", "SESSION_ID"),
    "connect": ("participantId", "projectId", "assignmentId"),
    "lab": ("LAB_PID", None, None),
}
PREVIEW_PARAM = "pt"
BATCH_PARAM = "batch"


class EntryError(ValueError):
    """Entry refused; ``reason`` is a stable code for the UI (never echoes submitted data)."""

    def __init__(self, reason: str, status: int = 403) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


@dataclass(frozen=True)
class Linkage:
    platform: str
    platform_pid: str
    study_ref: str | None
    session_ref: str | None


@dataclass(frozen=True)
class EntryResult:
    session_id: uuid.UUID
    credential_token: str
    writer_id: uuid.UUID
    resumed: bool
    stage: str


def extract_linkage(query: Mapping[str, str]) -> Linkage:
    """Identify the platform by its participant-ID parameter; reject ambiguity."""
    normalized = dict(query)
    for canonical, legacy in (
        ("participantId", "participant_id"),
        ("projectId", "project_id"),
        ("assignmentId", "submission_id"),
    ):
        values = {query[k].strip() for k in (canonical, legacy) if query.get(k, "").strip()}
        if len(values) > 1:
            raise EntryError("conflicting_platform_parameters", 422)
        if values:
            normalized[canonical] = values.pop()
    found: list[Linkage] = []
    for platform, (pid_key, study_key, session_key) in ADAPTER_PARAMS.items():
        pid = normalized.get(pid_key)
        if pid and pid.strip():
            found.append(
                Linkage(
                    platform,
                    pid.strip(),
                    normalized.get(study_key) if study_key else None,
                    normalized.get(session_key) if session_key else None,
                )
            )
    if not found:
        raise EntryError("missing_platform_id", 422)
    if len(found) > 1:
        raise EntryError("ambiguous_platform_id", 422)
    return found[0]


def _existing_link(
    conn: Connection, platform: str, pid: str
) -> tuple[uuid.UUID, str, str | None] | None:
    row = conn.execute(
        select(s.platform_link.c.session_id, s.session.c.batch_id, s.session.c.batch_group_id)
        .select_from(
            s.platform_link.join(s.session, s.platform_link.c.session_id == s.session.c.session_id)
        )
        .where(
            s.platform_link.c.platform == platform,
            s.platform_link.c.platform_pid == pid,
        )
    ).first()
    return None if row is None else (row.session_id, row.batch_id, row.batch_group_id)


def _live_credential(conn: Connection, presented_token: str | None, session_id: uuid.UUID) -> bool:
    """Whether the request's bearer token resolves to THIS session and is still live."""
    if not presented_token:
        return False
    return credentials.authenticate(conn, presented_token) == session_id


def _resume(
    conn: Connection, session_id: uuid.UUID, *, token: str, now: datetime
) -> tuple[str, uuid.UUID]:
    """Authenticated same-batch re-entry (INV-0407): the request proved possession of the
    session's live credential, so the credential is kept — never superseded — and the
    requester takes a fresh writer lease and keeps the stage. Returns (token, writer)."""
    # BA-30: the session row is locked BEFORE revision/next_event_seq are read — the
    # same session-row-first order every command transaction follows — so the resume
    # event append, lease takeover and sequence bump all commit under that one lock.
    # An unlocked read would let a concurrent command append an event in between and
    # strand this transaction on a duplicate (session_id, seq).
    row = (
        conn.execute(
            select(s.session.c.stage, s.session.c.revision, s.session.c.next_event_seq)
            .where(s.session.c.session_id == session_id)
            .with_for_update()
        )
        .mappings()
        .one()
    )
    writer = credentials.force_takeover(conn, session_id, now=now)
    revision = row["revision"] + 1
    conn.execute(
        insert(s.event).values(
            session_id=session_id,
            seq=row["next_event_seq"],
            event_id=uuid.uuid4(),
            stage=row["stage"],
            actor="system",
            type="session_resumed",
            revision=revision,
            server_time=now,
            payload={},
        )
    )
    conn.execute(insert(s.outbox).values(session_id=session_id, seq=row["next_event_seq"]))
    conn.execute(
        update(s.session)
        .where(s.session.c.session_id == session_id)
        .values(revision=revision, next_event_seq=row["next_event_seq"] + 1)
    )
    return token, writer


def _audit_refused_reentry(
    conn: Connection, *, session_id: uuid.UUID | None, platform: str, batch_id: str, reason: str
) -> None:
    """Record a refused re-entry attempt (spec §5.1: linkage is never authorization). The
    audit row carries the platform and batch only — never the panel ID itself."""
    conn.execute(
        insert(s.admin_audit).values(
            audit_id=uuid.uuid4(),
            session_id=session_id,
            actor="system",
            action="reentry_refused",
            payload={"platform": platform, "batch_id": batch_id, "reason": reason},
        )
    )


def _open_session(
    conn: Connection,
    *,
    bound: BoundManifest,
    batch_id: str,
    research_pid: str,
    seed_hex: str,
    build_source_sha256: str,
    behavior_source_sha256: str | None,
    arm: str | None,
    now: datetime,
    batch_group_id: str | None = None,
    feature_flags: Mapping[str, bool] | None = None,
) -> uuid.UUID:
    """Create participant + session + entry->consent transition + opened event."""
    session_id = create_session_rows(
        conn,
        bound=bound,
        batch_id=batch_id,
        research_pid=research_pid,
        seed_hex=seed_hex,
        build_source_sha256=build_source_sha256,
        behavior_source_sha256=behavior_source_sha256,
        arm=arm,
        batch_group_id=batch_group_id,
    )
    conn.execute(
        insert(s.stage_transition).values(
            transition_id=uuid.uuid4(),
            session_id=session_id,
            from_stage="entry",
            to_stage="consent",
            revision=1,
            occurred_at=now,
        )
    )
    conn.execute(
        insert(s.event).values(
            session_id=session_id,
            seq=1,
            event_id=uuid.uuid4(),
            stage="consent",
            actor="system",
            type="session_opened",
            revision=1,
            server_time=now,
            # feature_flags: deployment posture at entry — per-session provenance so
            # exports/analytics can reconstruct which gates served this session even
            # after a mid-cohort flag flip (2026-10-15 export-flags work).
            payload={
                "mode": bound.mode,
                "timing_mode": bound.timing_mode,
                "feature_flags": dict(feature_flags) if feature_flags else None,
            },
        )
    )
    conn.execute(insert(s.outbox).values(session_id=session_id, seq=1))
    conn.execute(
        update(s.session)
        .where(s.session.c.session_id == session_id)
        .values(stage="consent", revision=1, next_event_seq=2, stage_entered_at=now)
    )
    return session_id


def enter(
    engine: Engine,
    conn_store: ContentStore,
    *,
    query: Mapping[str, str],
    app_mode: str,
    preview_key: bytes | None,
    current_manifest_sha256: Mapping[str, str],
    build_source_sha256: str,
    behavior_source_sha256: str | None = None,
    presented_token: str | None = None,
    seed_hex: str | None = None,
    connect_completion: Mapping[str, ConnectCompletion] | None = None,
    time_pressure_enabled: bool = True,
    feature_flags: Mapping[str, bool] | None = None,
) -> EntryResult:
    """Process one entry request. Raises EntryError/PreviewError on refusal.

    ``presented_token`` is the caller's current ``inv_session`` bearer token, when the
    request carries one: a same-batch re-entry resumes only when it authenticates to the
    linked session (spec §5.1 — a panel ID alone never recovers a credential).
    ``seed_hex`` is a test-only master-seed override (harness replay); it is refused
    in ``live`` mode so production enrollment always draws a fresh seed.
    """
    if seed_hex is not None and app_mode == "live":
        raise EntryError("seed_override_forbidden", 403)
    from app.services.preview import guard_entry_overrides

    guard_entry_overrides(query)

    preview_token = query.get(PREVIEW_PARAM)
    refused: str | None = None
    with engine.begin() as conn:
        now = db_now(conn)
        if preview_token is not None:
            if preview_key is None:
                raise EntryError("previews_not_configured", 403)
            grant = verify_descriptor(
                preview_key,
                preview_token,
                app_mode=app_mode,
                now=now,
                current_manifest_sha256=current_manifest_sha256,
            )
            descriptor = grant.descriptor
            # BA-31: serialize first use of one signed descriptor on its nonce. Two
            # simultaneous entries both miss the ``preview-<nonce>`` participant and
            # would race _open_session into uq_participant_research_pid (a 500); the
            # transaction-scoped advisory lock makes the loser wait for the winner's
            # commit, then re-read below and take the resume path.
            conn.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtextextended(f"inv-preview:{descriptor.nonce}", 0)
                    )
                )
            )
            overlay = None
            if descriptor.qa_overlay is not None:
                name, _, version = descriptor.qa_overlay.rpartition("@v")
                overlay = ArtifactRef(artifact_id=name, version=int(version))
            bound = bind(
                conn_store,
                descriptor.manifest_id,
                mode=descriptor.mode,
                timing_mode=descriptor.timing_mode,
                qa_overlay=overlay,
            )
            # batch.batch_id admits [a-z0-9_.-] only — ':' is not a legal character.
            batch_id = f"preview.{descriptor.manifest_id}.{descriptor.arm}"
            # A descriptor is itself the credential: re-presenting it resumes the same
            # preview session (signed, expiring, nonce-scoped) instead of violating
            # uq_participant_research_pid with a second session.
            prior = conn.execute(
                select(s.session.c.session_id)
                .select_from(
                    s.participant.join(
                        s.session, s.participant.c.participant_id == s.session.c.participant_id
                    )
                )
                .where(s.participant.c.research_pid == f"preview-{descriptor.nonce}")
            ).first()
            if prior is not None:
                # BA-39: cleanup tombstones the nonce's session — a cleaned-up link is
                # revoked and can neither resume nor re-mint a session.
                if credentials.is_tombstoned(conn, prior.session_id):
                    # The refusal audit must outlive this rollback — record it on a
                    # separate transaction before raising.
                    with engine.begin() as audit_conn:
                        _audit_refused_reentry(
                            audit_conn,
                            session_id=prior.session_id,
                            platform="preview",
                            batch_id=batch_id,
                            reason="session_deleted",
                        )
                    raise EntryError("session_deleted", 403)
                _, token = credentials.issue(conn, prior.session_id, kind="primary")
                token, writer = _resume(conn, prior.session_id, token=token, now=now)
                return EntryResult(prior.session_id, token, writer, resumed=True, stage="resumed")
            session_id = _open_session(
                conn,
                bound=bound,
                batch_id=batch_id,
                research_pid=f"preview-{descriptor.nonce}",
                seed_hex=descriptor.seed,
                build_source_sha256=build_source_sha256,
                behavior_source_sha256=behavior_source_sha256,
                arm=descriptor.arm,
                now=now,
                feature_flags=feature_flags,
            )
            _, token = credentials.issue(conn, session_id, kind="primary")
            writer = credentials.acquire_writer(conn, session_id, now=now)
            return EntryResult(session_id, token, writer, resumed=False, stage="consent")

        # Live/QA entry: batch + platform linkage
        batch_id = query.get(BATCH_PARAM) or ""
        if not batch_id:
            raise EntryError("missing_batch", 422)
        linkage = extract_linkage(query)

        batch: Mapping[Any, Any] | None = (
            conn.execute(select(s.batch).where(s.batch.c.batch_id == batch_id))
            .mappings()
            .one_or_none()
        )
        # A batch group is entered by its group id; the session opens on the first member's
        # manifest (identical consent across members) and gets its cell at consent.
        group_id: str | None = None
        members: list[str] = []
        if batch is None:
            group = (
                conn.execute(select(s.batch_group).where(s.batch_group.c.group_id == batch_id))
                .mappings()
                .one_or_none()
            )
            if group is not None:
                members = list(
                    conn.execute(
                        select(s.batch_group_member.c.batch_id)
                        .where(s.batch_group_member.c.group_id == batch_id)
                        .order_by(s.batch_group_member.c.position)
                    ).scalars()
                )
            if group is None or not members:
                raise EntryError("unknown_batch", 403)
            batch = {
                **group,
                "manifest_id": conn.execute(
                    select(s.batch.c.manifest_id).where(s.batch.c.batch_id == members[0])
                ).scalar_one(),
            }
            group_id = batch_id
        if batch["mode"] != app_mode:
            raise EntryError("batch_mode_mismatch", 403)

        # Serialize first use of one platform identity. Two simultaneous entries both
        # miss ``_existing_link`` and would race ``_open_session`` into
        # ``uq_participant_research_pid`` (a 500); the transaction-scoped advisory lock
        # makes the loser wait for the winner's commit, then re-read below and take the
        # re-entry path — which refuses without a live credential (spec §5.1).
        conn.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(f"inv-entry:{linkage.platform}:{linkage.platform_pid}", 0)
                )
            )
        )
        existing = _existing_link(conn, linkage.platform, linkage.platform_pid)
        if existing is not None:
            prior_session, prior_batch, prior_group = existing
            if batch_id in (prior_batch, prior_group):
                # BA-39: a tombstoned session is gone by contract — re-entry is refused
                # and audited rather than resumed.
                if credentials.is_tombstoned(conn, prior_session):
                    _audit_refused_reentry(
                        conn,
                        session_id=prior_session,
                        platform=linkage.platform,
                        batch_id=batch_id,
                        reason="session_deleted",
                    )
                    refused = "session_deleted"
                elif _live_credential(conn, presented_token, prior_session):
                    token, writer = _resume(
                        conn, prior_session, token=presented_token or "", now=now
                    )
                    return EntryResult(prior_session, token, writer, resumed=True, stage="resumed")
                else:
                    # Spec §5.1: a matching panel ID is linkage, never a credential. Without
                    # a live session credential the re-entry is refused and audited — recovery
                    # is the controlled researcher-mediated path (INV-1305), not PID-only
                    # supersession of the participant's live credential.
                    _audit_refused_reentry(
                        conn,
                        session_id=prior_session,
                        platform=linkage.platform,
                        batch_id=batch_id,
                        reason="credential_recovery_required",
                    )
                    refused = "credential_recovery_required"
            else:
                _audit_refused_reentry(
                    conn,
                    session_id=prior_session,
                    platform=linkage.platform,
                    batch_id=batch_id,
                    reason="repeat_participation",
                )
                refused = "repeat_participation"

        if refused is not None:
            pass  # audit row commits below; the EntryError is raised outside the block
        else:
            if not batch["enrollment_open"]:
                raise EntryError("batch_closed", 403)
            if (
                group_id is None
                and conn.execute(
                    select(func.count())
                    .select_from(s.batch_group_member)
                    .where(s.batch_group_member.c.batch_id == batch_id)
                ).scalar_one()
            ):
                raise EntryError("batch_in_group", 403)
            if group_id is not None:
                from app.services.allocation import group_cells

                try:
                    policy = enrollment.load_policy(batch)
                    if policy is not None:
                        enrollment.validate_linkage(policy, linkage.platform, linkage.platform_pid)
                        cells, _ = group_cells(
                            conn, group_id, time_pressure_enabled=time_pressure_enabled
                        )
                        enrollment.validate_cells(policy, cells)
                        if not any(
                            c.available for c in enrollment.progress(conn, group_id, policy)
                        ):
                            raise enrollment.EnrollmentError("batch_at_capacity")
                except enrollment.EnrollmentError as exc:
                    raise EntryError(str(exc), 403) from exc
            # Completion is resolved by the session's (member) batch at the end, so a group
            # needs every member configured — checked here so nobody is stranded at the end.
            if linkage.platform == "connect" and any(
                b not in (connect_completion or {}) for b in (members or [batch_id])
            ):
                raise EntryError("connect_completion_not_configured", 503)

            bound = bind(conn_store, batch["manifest_id"], mode=parse_mode(app_mode))
            session_id = _open_session(
                conn,
                bound=bound,
                batch_id=members[0] if group_id else batch_id,
                batch_group_id=group_id,
                research_pid=f"{linkage.platform}:{linkage.platform_pid}",
                seed_hex=seed_hex if seed_hex is not None else new_seed(),
                build_source_sha256=build_source_sha256,
                behavior_source_sha256=behavior_source_sha256,
                arm=None,
                now=now,
                feature_flags=feature_flags,
            )
            conn.execute(
                insert(s.platform_link).values(
                    link_id=uuid.uuid4(),
                    session_id=session_id,
                    platform=linkage.platform,
                    platform_pid=linkage.platform_pid,
                    study_ref=linkage.study_ref,
                    session_ref=linkage.session_ref,
                )
            )
            _, token = credentials.issue(conn, session_id, kind="primary")
            writer = credentials.acquire_writer(conn, session_id, now=now)
            return EntryResult(session_id, token, writer, resumed=False, stage="consent")
    if refused is not None:
        raise EntryError(refused, 403)
    raise EntryError("entry_refused", 403)  # pragma: no cover - unreachable


__all__ = [
    "ADAPTER_PARAMS",
    "BATCH_PARAM",
    "PLATFORMS",
    "PREVIEW_PARAM",
    "AllocationError",
    "EntryError",
    "EntryResult",
    "Linkage",
    "allocate",
    "enter",
    "extract_linkage",
]
