"""Audited admin/operator actions (INV-1302, INV-1305, spec §11).

Every action in this module writes an ``app.admin_audit`` row in the same transaction as
the effect it describes — there is no silent path. The deliberate gaps are the security
boundary: no function here mutates ``session.arm``/``binding`` (assignment), the world
record/truth tables, or any frozen-answer table (``answer_revision``, ``answer_final``,
``confidence_response`` — append-only anyway), so an operator cannot edit condition,
truth or frozen answers even by mistake.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Connection, Engine

from app.db import schema as s
from app.db.tx import RejectionError, SessionNotFoundError, TxContext, db_now, lock_session
from app.domain.manifests.binding import BindError, BoundManifest, bind
from app.domain.manifests.schema import parse_mode
from app.domain.manifests.store import ContentStore
from app.flags import DEFAULT_FLAGS, FeatureFlags
from app.services import activetime, credentials, engines, enrollment, scheduler
from app.services.outcomes import record_terminal_outcome
from app.services.session_binding import bound_for_session

INCIDENT_KINDS = ("server_outage", "client_disconnect", "worker_restart", "other")
CLOCK_EFFECTS = ("paused", "excluded", "counted")
ENTITLEMENT_DECISIONS = ("approved", "void", "ineligible")


def _scripted_teammates_disabled(manifest: BoundManifest, mode: str, enabled: bool) -> bool:
    """Owner direction 2026-10-11 feature flag: the scripted-teammate family
    (``teammates.social_generation: deterministic_only``) is excluded from
    live-mode fielding unless the deployment enables it. Preview/QA are
    unaffected so scripted profiles stay previewable and sim-testable."""
    return (
        mode == "live"
        and not enabled
        and manifest.manifest.teammates.social_generation == "deterministic_only"
    )


def _stored_scripted_manifest(conn: Connection, manifest_id: str) -> bool:
    """Same check for callers that never bind: read the pinned manifest row."""
    content = conn.execute(
        select(s.experiment_manifest.c.content).where(
            s.experiment_manifest.c.manifest_id == manifest_id
        )
    ).scalar_one_or_none()
    if not isinstance(content, Mapping):
        return False
    return content.get("teammates", {}).get("social_generation") == "deterministic_only"


def _crate_overlap_disabled(manifest: BoundManifest, mode: str, enabled: bool) -> bool:
    """Owner direction 2026-10-15 feature flag: manifests whose ``task`` block
    pins a non-legacy ``crate_overlap`` level (none/some/full — the 12/8/4-crate
    between-subject conditions) are excluded from live-mode fielding unless the
    deployment enables it. Preview/QA are unaffected so the condition profiles
    stay previewable and sim-testable."""
    overlap = manifest.manifest.task.crate_overlap
    return mode == "live" and not enabled and overlap not in (None, "independent")


def _crate_number_disabled(manifest: BoundManifest, mode: str, enabled: bool) -> bool:
    """Same flag family (2026-10-15): manifests pinning
    ``task.participant_intel_budget`` (the thin ~20-record case file) are
    excluded from live-mode fielding unless enabled."""
    return (
        mode == "live"
        and not enabled
        and manifest.manifest.task.participant_intel_budget is not None
    )


def _stored_task_fields(conn: Connection, manifest_id: str) -> Mapping[str, Any]:
    """The pinned ``task`` block for callers that never bind."""
    content = conn.execute(
        select(s.experiment_manifest.c.content).where(
            s.experiment_manifest.c.manifest_id == manifest_id
        )
    ).scalar_one_or_none()
    if not isinstance(content, Mapping):
        return {}
    task = content.get("task")
    return task if isinstance(task, Mapping) else {}


class AdminActionError(ValueError):
    """Action refused; ``reason`` is a stable code surfaced to the admin UI."""

    def __init__(self, reason: str, status: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


def audit(
    conn: Connection,
    *,
    actor: str,
    action: str,
    session_id: uuid.UUID | None = None,
    payload: Mapping[str, Any] | None = None,
) -> None:
    """One append-only audit row. ``payload`` must already be sanitized (no secrets,
    no panel IDs beyond what the action itself is about)."""
    conn.execute(
        insert(s.admin_audit).values(
            audit_id=uuid.uuid4(),
            session_id=session_id,
            actor=actor,
            action=action,
            payload=dict(payload or {}),
        )
    )


def _session_or_404(conn: Connection, session_id: uuid.UUID) -> Mapping[str, Any]:
    try:
        return lock_session(conn, session_id)
    except SessionNotFoundError:
        raise AdminActionError("session_not_found", 404) from None


# ----- technical stop (operator) -------------------------------------------------------------


def technical_stop(
    engine: Engine,
    store: ContentStore,
    session_id: uuid.UUID,
    *,
    actor: str,
    reason: str,
    notes: str = "",
    bonus_payment_enabled: bool = True,
) -> dict[str, Any]:
    """Operator technical stop (INV-1305): terminal transition with reason
    ``operator_stop``, clock closed, open episodes expired, pending jobs resolved and the
    terminal outcome (receipt + base entitlement) persisted — the same durable shape as
    the abandonment sweep. Never touches assignment, truth or answers."""
    with engine.begin() as conn:
        row = _session_or_404(conn, session_id)
        if row["stage"] == "completion" or row["stage"] in s.TERMINAL_STAGES:
            raise AdminActionError("session_terminal")
        try:
            bound = bound_for_session(conn, store, row)
        except RejectionError as exc:
            raise AdminActionError(exc.reason) from None
        now = db_now(conn)
        ctx = TxContext(conn, row, now=now)
        from_stage = ctx.stage
        ctx.set_stage("technical_stop", reason="operator_stop")
        seq = ctx.append_event(
            "technical_stop",
            actor="researcher",
            payload={"outcome": "operator_stop"},
        )
        final = activetime.close(conn, session_id, now)
        open_episodes = conn.execute(
            select(s.request_episode.c.request_id).where(
                s.request_episode.c.session_id == session_id,
                s.request_episode.c.closed_at.is_(None),
            )
        ).all()
        for (rid,) in open_episodes:
            conn.execute(
                update(s.request_episode)
                .where(
                    s.request_episode.c.session_id == session_id,
                    s.request_episode.c.request_id == rid,
                )
                .values(
                    status="expired",
                    closed_event_seq=seq.seq,
                    closed_at=now,
                    close_reason="technical_stop",
                )
            )
            engines.resolve_request(
                conn,
                session_id,
                request_id=rid,
                resolution="expired",
                seq=seq.seq,
                at_active_ms=final,
            )
        scheduler.sweep_active_jobs(ctx, final_active_ms=0 if final is None else final)
        conn.execute(
            update(s.scheduled_job)
            .where(
                s.scheduled_job.c.session_id == session_id,
                s.scheduled_job.c.status.in_(("pending", "claimed")),
                s.scheduled_job.c.clock_basis == "wall",
            )
            .values(
                status="cancelled",
                lease_owner=None,
                lease_expires_at=None,
                outcome_reason="technical_stop",
                finished_at=now,
                finished_revision=ctx.revision + (1 if ctx.dirty else 0),
            )
        )
        entitlement = record_terminal_outcome(
            ctx,
            bound,
            outcome="technical_stop",
            final_event_seq=seq.seq,
            store=store,
            bonus_payment_enabled=bonus_payment_enabled,
            reconciled={
                "reason": reason,
                "actor": actor,
                "stopped_from_stage": from_stage,
                "last_active_ms": final,
            },
        )
        ctx._flush_session()
        audit(
            conn,
            actor=actor,
            action="technical_stop",
            session_id=session_id,
            payload={"reason": reason, "notes": notes, "from_stage": from_stage},
        )
    return {"stage": "technical_stop", "entitlement": entitlement}


def credential_recovery(
    engine: Engine, session_id: uuid.UUID, *, actor: str, reason: str
) -> dict[str, Any]:
    """Support-mediated credential recovery (INV-0407/INV-1305): revoke every live
    credential and issue one recovery token, returned exactly once in the response —
    never logged, never stored raw (only its SHA-256 persists). A tombstoned session
    has no recoverable identity — recovery is refused (BA-39)."""
    with engine.begin() as conn:
        _session_or_404(conn, session_id)
        if credentials.is_tombstoned(conn, session_id):
            raise AdminActionError("session_deleted", 409)
        _credential_id, token = credentials.supersede(conn, session_id, reason="admin_recovery")
        audit(
            conn,
            actor=actor,
            action="credential_recovery",
            session_id=session_id,
            payload={"reason": reason},  # the token is deliberately absent
        )
    return {"session_id": str(session_id), "credential_token": token}


# ----- incident intervals (operator) ------------------------------------------------------------


def open_incident(
    engine: Engine,
    session_id: uuid.UUID,
    *,
    actor: str,
    kind: str,
    clock_effect: str,
    notes: str = "",
) -> dict[str, Any]:
    if kind not in INCIDENT_KINDS:
        raise AdminActionError("bad_incident_kind", 422)
    if clock_effect not in CLOCK_EFFECTS:
        raise AdminActionError("bad_clock_effect", 422)
    incident_id = uuid.uuid4()
    with engine.begin() as conn:
        _session_or_404(conn, session_id)
        now = db_now(conn)
        active = activetime.active_ms(conn, session_id, now, default=0)
        conn.execute(
            insert(s.incident_interval).values(
                session_id=session_id,
                incident_id=incident_id,
                kind=kind,
                clock_effect=clock_effect,
                started_at=now,
                active_ms_at_start=active,
                notes=notes or None,
            )
        )
        audit(
            conn,
            actor=actor,
            action="incident_open",
            session_id=session_id,
            payload={"incident_id": str(incident_id), "kind": kind, "clock_effect": clock_effect},
        )
    return {"incident_id": str(incident_id), "status": "open"}


def close_incident(
    engine: Engine,
    session_id: uuid.UUID,
    incident_id: uuid.UUID,
    *,
    actor: str,
    notes: str = "",
) -> dict[str, Any]:
    with engine.begin() as conn:
        _session_or_404(conn, session_id)
        now = db_now(conn)
        active = activetime.active_ms(conn, session_id, now, default=0)
        result = conn.execute(
            update(s.incident_interval)
            .where(
                s.incident_interval.c.session_id == session_id,
                s.incident_interval.c.incident_id == incident_id,
                s.incident_interval.c.ended_at.is_(None),
            )
            .values(ended_at=now, active_ms_at_end=active)
        )
        if result.rowcount != 1:
            raise AdminActionError("incident_not_open", 404)
        if notes:
            conn.execute(
                update(s.incident_interval)
                .where(
                    s.incident_interval.c.session_id == session_id,
                    s.incident_interval.c.incident_id == incident_id,
                )
                .values(notes=notes)
            )
        audit(
            conn,
            actor=actor,
            action="incident_close",
            session_id=session_id,
            payload={"incident_id": str(incident_id)},
        )
    return {"incident_id": str(incident_id), "status": "closed"}


# ----- batches: creation validation + enrollment pause/resume -----------------------------------


def create_batch(
    engine: Engine,
    store: ContentStore,
    *,
    actor: str,
    batch_id: str,
    manifest_id: str,
    mode: str,
    flags: FeatureFlags = DEFAULT_FLAGS,
) -> dict[str, Any]:
    """Create an enrollment batch pinned to a manifest (INV-1302 batch validation).

    The manifest must exist in the content store and *bind* cleanly — binding resolves
    every artifact ref, so a batch can never be opened against a broken manifest
    (launch validation). Enrollment always starts closed; opening is a separate audited
    action. Immutable manifest comparison is enforced by ``register_manifest``:
    re-registering the same id with different content raises ``ManifestMismatchError``.
    """
    if mode not in s.MODES:
        raise AdminActionError("bad_mode", 422)
    stored = store.manifests.get(manifest_id)
    if stored is None:
        raise AdminActionError("unknown_manifest", 404)
    try:
        bound: BoundManifest = bind(store, manifest_id, mode=parse_mode(mode))
    except BindError as exc:
        raise AdminActionError("manifest_bind_failed", 422) from exc
    if _scripted_teammates_disabled(bound, mode, flags.scripted_teammates):
        raise AdminActionError("scripted_teammates_disabled", 422)
    if _crate_overlap_disabled(bound, mode, flags.crate_overlap):
        raise AdminActionError("crate_overlap_disabled", 422)
    if _crate_number_disabled(bound, mode, flags.crate_number):
        raise AdminActionError("crate_number_disabled", 422)
    with engine.begin() as conn:
        exists = (
            conn.execute(
                select(func.count()).select_from(s.batch).where(s.batch.c.batch_id == batch_id)
            ).scalar_one()
            or conn.execute(
                select(func.count())
                .select_from(s.batch_group)
                .where(s.batch_group.c.group_id == batch_id)
            ).scalar_one()
        )
        if exists:
            raise AdminActionError("batch_exists", 409)
        try:
            from app.services.sessions import register_manifest

            register_manifest(conn, bound)
        except ValueError as exc:  # ManifestMismatchError / BatchMismatchError
            raise AdminActionError(f"manifest_mismatch:{exc.__class__.__name__}", 409) from exc
        conn.execute(
            insert(s.batch).values(
                batch_id=batch_id,
                study_key=bound.manifest.study_key,
                manifest_id=manifest_id,
                mode=mode,
                enrollment_open=False,
            )
        )
        audit(
            conn,
            actor=actor,
            action="batch_create",
            payload={
                "batch_id": batch_id,
                "manifest_id": manifest_id,
                "manifest_sha256": bound.manifest_sha256,
                "mode": mode,
            },
        )
    return {
        "batch_id": batch_id,
        "enrollment_open": False,
        "manifest_sha256": bound.manifest_sha256,
    }


def set_enrollment(
    engine: Engine,
    batch_id: str,
    *,
    open_: bool,
    actor: str,
    reason: str,
    flags: FeatureFlags = DEFAULT_FLAGS,
) -> dict[str, Any]:
    """Enrollment pause/resume per batch (spec §11). Audited either way. A batch-group
    member is never opened directly — its sessions arrive only through the group link."""
    with engine.begin() as conn:
        if open_:
            row = (
                conn.execute(
                    select(s.batch.c.manifest_id, s.batch.c.mode).where(
                        s.batch.c.batch_id == batch_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise AdminActionError("unknown_batch", 404)
            if (
                row["mode"] == "live"
                and _stored_scripted_manifest(conn, row["manifest_id"])
                and not flags.scripted_teammates
            ):
                raise AdminActionError("scripted_teammates_disabled", 422)
            if row["mode"] == "live" and (not flags.crate_overlap or not flags.crate_number):
                task = _stored_task_fields(conn, row["manifest_id"])
                overlap = task.get("crate_overlap")
                if not flags.crate_overlap and overlap is not None and overlap != "independent":
                    raise AdminActionError("crate_overlap_disabled", 422)
                if not flags.crate_number and task.get("participant_intel_budget") is not None:
                    raise AdminActionError("crate_number_disabled", 422)
        if (
            open_
            and conn.execute(
                select(func.count())
                .select_from(s.batch_group_member)
                .where(s.batch_group_member.c.batch_id == batch_id)
            ).scalar_one()
        ):
            raise AdminActionError("batch_in_group", 409)
        result = conn.execute(
            update(s.batch).where(s.batch.c.batch_id == batch_id).values(enrollment_open=open_)
        )
        if result.rowcount != 1:
            raise AdminActionError("unknown_batch", 404)
        audit(
            conn,
            actor=actor,
            action="enrollment_set",
            payload={"batch_id": batch_id, "open": open_, "reason": reason},
        )
    return {"batch_id": batch_id, "enrollment_open": open_}


def create_batch_group(
    engine: Engine,
    store: ContentStore,
    *,
    actor: str,
    group_id: str,
    batch_ids: list[str],
    mode: str,
    flags: FeatureFlags = DEFAULT_FLAGS,
    enrollment_target: enrollment.EnrollmentTarget | None = None,
) -> dict[str, Any]:
    """Create a batch group: one platform link randomizing across member batches in balanced
    blocks of every (member x arm) cell. Members must already exist, share study and mode,
    be closed for direct entry, belong to no other group, and bind the same consent artifact
    (sessions consent on the first member's copy before their cell is drawn). Enrollment
    starts closed."""
    if mode not in s.MODES:
        raise AdminActionError("bad_mode", 422)
    if len(batch_ids) < 2 or len(set(batch_ids)) != len(batch_ids):
        raise AdminActionError("bad_members", 422)
    if enrollment_target is not None and mode != "live":
        raise AdminActionError("quota_requires_live_group", 422)
    with engine.begin() as conn:
        if (
            conn.execute(
                select(func.count()).select_from(s.batch).where(s.batch.c.batch_id == group_id)
            ).scalar_one()
            or conn.execute(
                select(func.count())
                .select_from(s.batch_group)
                .where(s.batch_group.c.group_id == group_id)
            ).scalar_one()
        ):
            raise AdminActionError("batch_exists", 409)
        rows = {
            r["batch_id"]: r
            for r in conn.execute(select(s.batch).where(s.batch.c.batch_id.in_(batch_ids)))
            .mappings()
            .all()
        }
        if set(rows) != set(batch_ids):
            raise AdminActionError("unknown_batch", 404)
        if {r["mode"] for r in rows.values()} != {mode} or len(
            {r["study_key"] for r in rows.values()}
        ) != 1:
            raise AdminActionError("member_mismatch", 422)
        if any(r["enrollment_open"] for r in rows.values()):
            raise AdminActionError("member_open", 409)
        if conn.execute(
            select(func.count())
            .select_from(s.batch_group_member)
            .where(s.batch_group_member.c.batch_id.in_(batch_ids))
        ).scalar_one():
            raise AdminActionError("member_in_group", 409)
        if (
            enrollment_target is not None
            and conn.execute(
                select(func.count())
                .select_from(s.session)
                .where(s.session.c.batch_id.in_(batch_ids))
            ).scalar_one()
        ):
            raise AdminActionError("quota_members_not_empty", 409)
        consents = set()
        for b in batch_ids:
            try:
                bound = bind(store, rows[b]["manifest_id"], mode=parse_mode(mode))
            except BindError as exc:
                raise AdminActionError("manifest_bind_failed", 422) from exc
            if _scripted_teammates_disabled(bound, mode, flags.scripted_teammates):
                raise AdminActionError("scripted_teammates_disabled", 422)
            if _crate_overlap_disabled(bound, mode, flags.crate_overlap):
                raise AdminActionError("crate_overlap_disabled", 422)
            if _crate_number_disabled(bound, mode, flags.crate_number):
                raise AdminActionError("crate_number_disabled", 422)
            art = bound.artifacts.get("instruments.consent")
            consents.add(art.key if art is not None else None)
        if len(consents) != 1 or None in consents:
            raise AdminActionError("consent_mismatch", 422)
        study_key = next(iter(rows.values()))["study_key"]
        conn.execute(
            insert(s.batch_group).values(
                group_id=group_id, study_key=study_key, mode=mode, enrollment_open=False
            )
        )
        for position, b in enumerate(batch_ids):
            conn.execute(
                insert(s.batch_group_member).values(
                    group_id=group_id, batch_id=b, position=position
                )
            )
        policy = None
        if enrollment_target is not None:
            from app.services.allocation import group_cells

            cells, _ = group_cells(conn, group_id, time_pressure_enabled=flags.time_pressure)
            policy = enrollment.make_policy(enrollment_target, cells).model_dump(mode="json")
            conn.execute(
                update(s.batch_group)
                .where(s.batch_group.c.group_id == group_id)
                .values(enrollment_policy=policy)
            )
        audit(
            conn,
            actor=actor,
            action="batch_group_create",
            payload={
                "group_id": group_id,
                "batch_ids": batch_ids,
                "mode": mode,
                **({"enrollment_policy": policy} if policy is not None else {}),
            },
        )
    return {
        "group_id": group_id,
        "batch_ids": batch_ids,
        "enrollment_open": False,
        **({"enrollment_policy": policy} if policy is not None else {}),
    }


def set_group_enrollment(
    engine: Engine,
    group_id: str,
    *,
    open_: bool,
    actor: str,
    reason: str,
    flags: FeatureFlags = DEFAULT_FLAGS,
) -> dict[str, Any]:
    """Open or close a batch group's enrollment under its row lock. Opening re-validates
    the quota policy against the member cells, refuses when every cell already met its
    target (``quota_target_reached``), and refuses live groups whose manifests need a
    disabled flag (scripted teammates, crate overlap or crate number). Audited as
    ``enrollment_set``."""
    with engine.begin() as conn:
        group = (
            conn.execute(
                select(s.batch_group).where(s.batch_group.c.group_id == group_id).with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if group is None:
            raise AdminActionError("unknown_batch", 404)
        if open_:
            from app.services.allocation import group_cells

            try:
                policy = enrollment.load_policy(group)
                if policy is not None:
                    cells, _ = group_cells(
                        conn, group_id, time_pressure_enabled=flags.time_pressure
                    )
                    enrollment.validate_cells(policy, cells)
                    if all(
                        c.completed >= c.target for c in enrollment.progress(conn, group_id, policy)
                    ):
                        raise enrollment.EnrollmentError("quota_target_reached")
            except enrollment.EnrollmentError as exc:
                raise AdminActionError(str(exc), 409) from exc
            if group["mode"] == "live" and (
                not flags.scripted_teammates or not flags.crate_overlap or not flags.crate_number
            ):
                member_manifests: list[str] = list(
                    conn.execute(
                        select(s.batch.c.manifest_id)
                        .select_from(
                            s.batch_group_member.join(
                                s.batch,
                                s.batch_group_member.c.batch_id == s.batch.c.batch_id,
                            )
                        )
                        .where(s.batch_group_member.c.group_id == group_id)
                    )
                    .scalars()
                    .all()
                )
                if not flags.scripted_teammates and any(
                    _stored_scripted_manifest(conn, m) for m in member_manifests
                ):
                    raise AdminActionError("scripted_teammates_disabled", 422)
                if not flags.crate_overlap or not flags.crate_number:
                    tasks = [_stored_task_fields(conn, m) for m in member_manifests]
                    if not flags.crate_overlap and any(
                        (o := t.get("crate_overlap")) is not None and o != "independent"
                        for t in tasks
                    ):
                        raise AdminActionError("crate_overlap_disabled", 422)
                    if not flags.crate_number and any(
                        t.get("participant_intel_budget") is not None for t in tasks
                    ):
                        raise AdminActionError("crate_number_disabled", 422)
        result = conn.execute(
            update(s.batch_group)
            .where(s.batch_group.c.group_id == group_id)
            .values(enrollment_open=open_)
        )
        if result.rowcount != 1:
            raise AdminActionError("unknown_batch", 404)
        audit(
            conn,
            actor=actor,
            action="enrollment_set",
            payload={"group_id": group_id, "open": open_, "reason": reason},
        )
    return {"group_id": group_id, "enrollment_open": open_}


def raise_enrollment_target(
    engine: Engine,
    group_id: str,
    *,
    target_completers_per_cell: int,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    """Raise a live group's per-cell completer target (wave top-off).

    The only supported mutation of an ``enrollment_policy``: cells, algorithm,
    platform and ``test_only`` stay immutable, and the target may only *increase*
    (a decrease could strand already-allocated participants above quota). Runs
    under the same ``FOR UPDATE`` group-row lock that allocation and completion
    serialize on, re-validates the whole policy through ``EnrollmentPolicy``, and
    writes an ``enrollment_target_update`` audit row with the authenticated actor.
    Enrollment open/closed state is untouched — reopen separately.
    """
    with engine.begin() as conn:
        group = (
            conn.execute(
                select(s.batch_group).where(s.batch_group.c.group_id == group_id).with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if group is None:
            raise AdminActionError("unknown_batch", 404)
        try:
            policy = enrollment.load_policy(group)
        except enrollment.EnrollmentError as exc:
            raise AdminActionError(str(exc), 409) from exc
        if policy is None:
            raise AdminActionError("no_enrollment_policy", 409)
        previous = policy.target_completers_per_cell
        if target_completers_per_cell <= previous:
            raise AdminActionError("target_must_increase", 422)
        try:
            raised = enrollment.EnrollmentPolicy.model_validate(
                {
                    **policy.model_dump(mode="json"),
                    "target_completers_per_cell": target_completers_per_cell,
                }
            )
        except ValueError as exc:
            raise AdminActionError("invalid_target", 422) from exc
        conn.execute(
            update(s.batch_group)
            .where(s.batch_group.c.group_id == group_id)
            .values(enrollment_policy=raised.model_dump(mode="json"))
        )
        audit(
            conn,
            actor=actor,
            action="enrollment_target_update",
            payload={
                "group_id": group_id,
                "previous_target_completers_per_cell": previous,
                "target_completers_per_cell": target_completers_per_cell,
                "reason": reason,
            },
        )
    return {
        "group_id": group_id,
        "previous_target_completers_per_cell": previous,
        "target_completers_per_cell": target_completers_per_cell,
        "total_target": len(raised.cells) * target_completers_per_cell,
    }


def group_balance(
    conn: Connection, group_id: str, *, time_pressure_enabled: bool = True
) -> dict[str, Any]:
    """Allocations per (member batch, arm) cell — the balance a launch check reads."""
    from app.services.allocation import GroupCell, group_cells

    cells, _ = group_cells(conn, group_id, time_pressure_enabled=time_pressure_enabled)
    group = (
        conn.execute(select(s.batch_group).where(s.batch_group.c.group_id == group_id))
        .mappings()
        .one()
    )
    policy = enrollment.load_policy(group)
    quota_cells: dict[tuple[str, str], enrollment.CellProgress] = {}
    extra: dict[str, Any] = {}
    configuration_matches = True
    if policy is not None:
        quota_cells = {c.cell.key: c for c in enrollment.progress(conn, group_id, policy)}
        try:
            enrollment.validate_cells(policy, cells)
        except enrollment.EnrollmentError:
            configuration_matches = False
        cells = [GroupCell(c.batch_id, c.manifest_id, c.arm) for c in policy.cells]
        extra["quota"] = {
            "platform": policy.platform,
            "test_only": policy.test_only,
            "target_completers_per_cell": policy.target_completers_per_cell,
            "total_target": len(policy.cells) * policy.target_completers_per_cell,
            "completed": sum(c.completed for c in quota_cells.values()),
            "active": sum(c.active for c in quota_cells.values()),
            "available": sum(c.available for c in quota_cells.values())
            if configuration_matches
            else 0,
            "target_met": all(c.completed >= c.target for c in quota_cells.values()),
            "configuration_matches": configuration_matches,
        }
    counts = {
        (r.batch_id, r.arm): r.n
        for r in conn.execute(
            select(s.allocation.c.batch_id, s.allocation.c.arm, func.count().label("n"))
            .where(s.allocation.c.batch_id.in_([c.batch_id for c in cells]))
            .group_by(s.allocation.c.batch_id, s.allocation.c.arm)
        ).all()
    }
    allocatable = {(c.batch_id, c.arm) for c in cells} if configuration_matches else set()
    listed = {(c.batch_id, c.arm) for c in cells}
    manifests = {c.batch_id: c.manifest_id for c in cells}
    cells.extend(
        GroupCell(batch_id, manifests[batch_id], arm)
        for batch_id, arm in sorted(counts)
        if (batch_id, arm) not in listed
    )
    return {
        "cells": [
            {
                "batch_id": c.batch_id,
                "manifest_id": c.manifest_id,
                "arm": c.arm,
                "allocated": counts.get((c.batch_id, c.arm), 0),
                "allocatable": (c.batch_id, c.arm) in allocatable,
                **(
                    quota_cells[(c.batch_id, c.arm)].as_dict()
                    if (c.batch_id, c.arm) in quota_cells
                    else {}
                ),
            }
            for c in cells
        ],
        "allocated": sum(counts.values()),
        **extra,
    }


# ----- entitlement decisions (researcher decides; compensation marks paid) -----------------------


def decide_entitlement(
    engine: Engine,
    session_id: uuid.UUID,
    kind: str,
    *,
    decision: str,
    actor: str,
    note: str = "",
) -> dict[str, Any]:
    """Researcher entitlement decision. Compensation eligibility is independent of the
    data-use choice (``outcomes.withdrawal_payment`` is pinned ``unaffected``), so this
    never consults ``data_use_choice``."""
    if decision not in ENTITLEMENT_DECISIONS:
        raise AdminActionError("bad_decision", 422)
    with engine.begin() as conn:
        now = db_now(conn)
        current = (
            conn.execute(
                select(s.payment_entitlement.c.status, s.session.c.mode, s.session.c.batch_group_id)
                .join(s.session, s.session.c.session_id == s.payment_entitlement.c.session_id)
                .where(
                    s.payment_entitlement.c.session_id == session_id,
                    s.payment_entitlement.c.kind == kind,
                )
            )
            .mappings()
            .one_or_none()
        )
        if current is None:
            raise AdminActionError("entitlement_not_found", 404)
        if decision == "approved" and current["mode"] != "live":
            raise AdminActionError("non_live_session", 409)
        if decision == "approved" and enrollment.is_test_group(conn, current["batch_group_id"]):
            raise AdminActionError("test_session", 409)
        if current["status"] == "paid":
            raise AdminActionError("entitlement_paid", 409)
        conn.execute(
            update(s.payment_entitlement)
            .where(
                s.payment_entitlement.c.session_id == session_id,
                s.payment_entitlement.c.kind == kind,
            )
            .values(status=decision, decided_by=actor, decided_at=now)
        )
        audit(
            conn,
            actor=actor,
            action="entitlement_decision",
            session_id=session_id,
            payload={
                "kind": kind,
                "from": current["status"],
                "to": decision,
                "note": note,
            },
        )
    return {"session_id": str(session_id), "kind": kind, "status": decision}


def mark_paid(
    engine: Engine,
    session_id: uuid.UUID,
    kind: str,
    *,
    paid_ref: str,
    actor: str,
) -> dict[str, Any]:
    """Compensation role marks an approved entitlement paid. Only the columns the
    ``investigators_compensation`` DB role may update are touched
    (``status``/``paid_at``/``paid_ref``, schema COMPENSATION_UPDATE_COLUMNS)."""
    if not paid_ref:
        raise AdminActionError("missing_paid_ref", 422)
    with engine.begin() as conn:
        now = db_now(conn)
        current = (
            conn.execute(
                select(s.payment_entitlement.c.status, s.session.c.mode)
                .join(s.session, s.session.c.session_id == s.payment_entitlement.c.session_id)
                .where(
                    s.payment_entitlement.c.session_id == session_id,
                    s.payment_entitlement.c.kind == kind,
                )
            )
            .mappings()
            .one_or_none()
        )
        if current is None:
            raise AdminActionError("entitlement_not_found", 404)
        if current["mode"] != "live":
            raise AdminActionError("non_live_session", 409)
        if current["status"] != "approved":
            raise AdminActionError("entitlement_not_approved", 409)
        conn.execute(
            update(s.payment_entitlement)
            .where(
                s.payment_entitlement.c.session_id == session_id,
                s.payment_entitlement.c.kind == kind,
            )
            .values(status="paid", paid_at=now, paid_ref=paid_ref)
        )
        audit(
            conn,
            actor=actor,
            action="entitlement_paid",
            session_id=session_id,
            payload={"kind": kind, "paid_ref": paid_ref},
        )
    return {"session_id": str(session_id), "kind": kind, "status": "paid"}
