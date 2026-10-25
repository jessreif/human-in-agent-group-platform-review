"""Identity retention, deletion tombstones and safe cleanup (INV-1306/1307/1308).

Approved schedule (spec §11, S5): platform IDs are deleted after review/payment, within
48 hours of study closing. ``platform_link.purge_after`` records that deadline per link.

Deletion model — deliberately honest about the schema's guarantees:

- ``deletable_now``  session-owned *mutable* app tables (message, model_call, jobs, …).
  ``apply_deletion`` removes these inside the caller's transaction.
- ``privileged``     append-only research tables (``IMMUTABLE_TABLES``: event, record,
  answer/final, result, …) and ``identity.*`` rows. The append-only trigger fires for
  every role and the runtime role holds no DELETE on ``identity`` — physical removal of
  these rows is the "separately migrated mechanism" the schema names; the tombstone the
  service writes is its work order. A privileged runner (scripts/retention_purge.py with
  the migrator DSN) performs the identity deletes; append-only research rows await the
  migrated mechanism. Each deletion attempt runs in its own savepoint, so a denied table
  is recorded ``blocked`` instead of aborting the transaction.
- ``kept``           ``payment_entitlement``/``completion_receipt``/``result`` and the
  ``data_use_choice`` row itself: compensation eligibility is independent of the
  data-use choice (spec §11), so a ``withdraw_all`` deletion never removes the payment
  record.

Tombstones are ``admin_audit`` rows (``deletion_tombstone``) — append-only, backed up
with the database. After a restore, ``reapply_tombstones`` re-runs the deletion for every
tombstoned session whose rows reappeared, before the data is usable again (spec §11).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from app.db import schema as s
from app.db.export import SESSION_TABLES
from app.db.tx import db_now
from app.services.adminops import audit

# S5: platform-ID deletion after review/payment, within 48 h of study closing.
PLATFORM_PURGE_DELAY = timedelta(hours=48)

TOMBSTONE_ACTION = "deletion_tombstone"
REAPPLY_ACTION = "deletion_reapplied"
DRY_RUN_ACTION = "deletion_dry_run"  # audited but never replayed by reapply_tombstones

# Session-owned app tables the runtime role may DELETE from (mutable, not append-only),
# in child-before-parent order so FKs never block the sweep.
DELETABLE_SESSION_TABLES = (
    "delivery_receipt",
    "outbox",
    "manipulation_exposure",
    "request_episode",
    "model_call",
    "message",
    "item_response",
    # AI_QA_ENABLED: participant free text (questions + model answers) — mutable
    # by design (pending -> resolved UPDATE), purged with the mutable set.
    "qa_exchange",
    "incident_interval",
    "file_membership",
    "seat_holding",
    "agent_state",
    "clock_interval",
    "scheduled_job",
    # Identity-adjacent display data — purged with the mutable set, never exported.
    "participant_display",
)

# Compensation independence (INV-1307): never deleted by a data-use disposition.
KEEP_COMPENSATION_TABLES = ("payment_entitlement", "completion_receipt", "result")
# The withdrawal choice itself is the disposition record — it stays.
KEEP_PROVENANCE_TABLES = ("data_use_choice",)
# identity.* handled separately (runtime has no DELETE there).
IDENTITY_TABLES = ("session_credential", "platform_link")


class RetentionError(ValueError):
    def __init__(self, reason: str, status: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


def _table(name: str) -> Any:
    return s.metadata.tables[f"{s.APP}.{name}"]


def try_savepoint(conn: Connection, fn: Any) -> tuple[bool, Any]:
    """Run ``fn`` inside a savepoint; on any DB error (denied grant, append-only trigger)
    roll back to the savepoint and report blocked. Returns (ok, result-or-None)."""
    sp = conn.begin_nested()
    try:
        result = fn()
    except DBAPIError:
        sp.rollback()
        return False, None
    sp.commit()
    return True, result


def deletion_plan(conn: Connection, session_id: uuid.UUID) -> dict[str, Any]:
    """Classify every session-owned row: deletable now, privileged-only, or kept."""
    tables: dict[str, dict[str, Any]] = {}
    for name in sorted(SESSION_TABLES):
        table = _table(name)
        n = conn.execute(
            select(func.count()).select_from(table).where(table.c.session_id == session_id)
        ).scalar_one()
        if name in KEEP_COMPENSATION_TABLES:
            disposition = "kept_compensation"
        elif name in KEEP_PROVENANCE_TABLES:
            disposition = "kept_disposition_record"
        elif name in DELETABLE_SESSION_TABLES:
            disposition = "deletable_now"
        else:
            disposition = "privileged"  # append-only: migrated mechanism deletes these
        tables[name] = {"rows": int(n), "disposition": disposition}
    identity: dict[str, dict[str, Any]] = {}
    for name in IDENTITY_TABLES:
        table = s.metadata.tables[f"{s.IDENTITY}.{name}"]
        ok, n = try_savepoint(
            conn,
            lambda table=table: conn.execute(
                select(func.count()).select_from(table).where(table.c.session_id == session_id)
            ).scalar_one(),
        )
        identity[name] = (
            {"rows": int(n), "disposition": "privileged"}
            if ok
            else {"rows": None, "disposition": "privileged", "status": "unavailable"}
        )
    return {
        "session_id": str(session_id),
        "tables": tables,
        "identity": identity,
        "allocation_slots": int(
            conn.execute(
                select(func.count())
                .select_from(s.allocation)
                .where(s.allocation.c.session_id == session_id)
            ).scalar_one()
        ),
    }


def pending_purge_rows(conn: Connection, session_id: uuid.UUID) -> dict[str, int]:
    """Rows still present that a session's deletion targets — the honest "awaiting
    privileged purge" count (BA-39). Compensation and disposition records are kept by
    design and never counted as pending."""
    counts = {"mutable": 0, "privileged": 0, "identity": 0}
    privileged_names = sorted(
        set(SESSION_TABLES)
        - set(DELETABLE_SESSION_TABLES)
        - set(KEEP_COMPENSATION_TABLES)
        - set(KEEP_PROVENANCE_TABLES)
    )
    for name in DELETABLE_SESSION_TABLES:
        counts["mutable"] += int(
            conn.execute(
                select(func.count())
                .select_from(_table(name))
                .where(_table(name).c.session_id == session_id)
            ).scalar_one()
        )
    for name in privileged_names:
        counts["privileged"] += int(
            conn.execute(
                select(func.count())
                .select_from(_table(name))
                .where(_table(name).c.session_id == session_id)
            ).scalar_one()
        )
    for name in IDENTITY_TABLES:
        table = s.metadata.tables[f"{s.IDENTITY}.{name}"]
        ok, n = try_savepoint(
            conn,
            lambda table=table: conn.execute(
                select(func.count()).select_from(table).where(table.c.session_id == session_id)
            ).scalar_one(),
        )
        # Unreadable identity rows cannot be proven purged — count conservatively.
        counts["identity"] += int(n) if ok else 1
    counts["total"] = counts["mutable"] + counts["privileged"] + counts["identity"]
    return counts


def _delete_session_mutable(
    conn: Connection, session_id: uuid.UUID
) -> tuple[dict[str, int], list[str]]:
    """Delete each mutable session table in its own savepoint. A table whose rows are
    still referenced by append-only provenance (``displayed_output`` FKs into
    ``model_call`` and ``message``) is reported ``blocked`` like the privileged set —
    one FK-blocked table must never abort the whole sweep (BA-34)."""
    deleted: dict[str, int] = {}
    blocked: list[str] = []
    for name in DELETABLE_SESSION_TABLES:
        table = _table(name)
        ok, res = try_savepoint(
            conn,
            lambda table=table: conn.execute(delete(table).where(table.c.session_id == session_id)),
        )
        if not ok:
            blocked.append(name)
        elif res.rowcount:
            deleted[name] = int(res.rowcount)
    # Release the allocation slot (UPDATE, not delete — the slot record is immutable
    # bookkeeping for the batch, not participant content).
    conn.execute(
        update(s.allocation)
        .where(s.allocation.c.session_id == session_id)
        .values(session_id=None, reserved_at=None)
    )
    return deleted, blocked


def _delete_session_privileged(conn: Connection, session_id: uuid.UUID) -> dict[str, Any]:
    """Attempt privileged-scope deletes (identity + append-only tables) each in its own
    savepoint; denied tables are reported ``blocked``, never silently skipped."""
    outcome: dict[str, Any] = {"deleted": {}, "blocked": []}

    def _attempt(table: Any) -> tuple[bool, Any]:
        return try_savepoint(
            conn,
            lambda: conn.execute(delete(table).where(table.c.session_id == session_id)),
        )

    for name in IDENTITY_TABLES:
        ok, res = _attempt(s.metadata.tables[f"{s.IDENTITY}.{name}"])
        if ok:
            if res.rowcount:
                outcome["deleted"][name] = int(res.rowcount)
        else:
            outcome["blocked"].append(name)
    for name in sorted(
        set(SESSION_TABLES)
        - set(DELETABLE_SESSION_TABLES)
        - set(KEEP_COMPENSATION_TABLES)
        - set(KEEP_PROVENANCE_TABLES)
    ):
        ok, res = _attempt(_table(name))
        if ok:
            if res.rowcount:
                outcome["deleted"][name] = int(res.rowcount)
        else:
            outcome["blocked"].append(name)
    return outcome


def apply_deletion(
    conn: Connection,
    session_id: uuid.UUID,
    *,
    actor: str,
    reason: str,
    scope: str,
    dry_run: bool = True,
    reapply: bool = False,
) -> dict[str, Any]:
    """Delete a session's participant-data rows and record the tombstone.

    ``dry_run`` (the API default) returns the plan without touching a row. A live run
    removes the deletable tables, attempts the privileged set savepoint-by-savepoint,
    and always writes the ``deletion_tombstone`` audit row — the tombstone is what a
    restore procedure replays (``reapply_tombstones``)."""
    exists = conn.execute(
        select(func.count()).select_from(s.session).where(s.session.c.session_id == session_id)
    ).scalar_one()
    if not exists:
        raise RetentionError("session_not_found", 404)
    plan = deletion_plan(conn, session_id)
    applied: dict[str, Any] = {"dry_run": True, "deleted": {}, "blocked": []}
    if not dry_run:
        deleted, mutable_blocked = _delete_session_mutable(conn, session_id)
        applied["deleted"].update(deleted)
        applied["mutable_blocked"] = mutable_blocked
        applied["privileged"] = _delete_session_privileged(conn, session_id)
        applied["dry_run"] = False
    # Only a real deletion earns the ``deletion_tombstone`` action — ``reapply_tombstones``
    # replays exactly that action after a restore, so a dry run must never mint one.
    action = DRY_RUN_ACTION if dry_run else (REAPPLY_ACTION if reapply else TOMBSTONE_ACTION)
    audit(
        conn,
        actor=actor,
        action=action,
        session_id=session_id,
        payload={
            "reason": reason,
            "scope": scope,
            "dry_run": dry_run,
            "deleted": applied["deleted"],
            "blocked": applied.get("mutable_blocked", [])
            + applied.get("privileged", {}).get("blocked", []),
        },
    )
    return {"plan": plan, "applied": applied}


def reapply_tombstones(conn: Connection, *, actor: str = "system:restore") -> list[str]:
    """After a restore, re-run every recorded deletion tombstone: any session whose
    deletable rows reappeared is re-deleted and gets a ``deletion_reapplied`` audit row.
    Returns the session ids reapplied this pass (spec §11 restore requirement)."""
    tombstoned = [
        r.session_id
        for r in conn.execute(
            select(s.admin_audit.c.session_id)
            .where(
                s.admin_audit.c.action == TOMBSTONE_ACTION,
                s.admin_audit.c.session_id.is_not(None),
            )
            .distinct()
        ).all()
    ]
    reapplied: list[str] = []
    for sid in tombstoned:
        remaining = 0
        for name in DELETABLE_SESSION_TABLES:
            remaining += conn.execute(
                select(func.count())
                .select_from(_table(name))
                .where(_table(name).c.session_id == sid)
            ).scalar_one()
        ok_links, link_count = try_savepoint(
            conn,
            lambda sid=sid: conn.execute(
                select(func.count())
                .select_from(s.platform_link)
                .where(s.platform_link.c.session_id == sid)
            ).scalar_one(),
        )
        if ok_links:
            remaining += int(link_count)
        else:
            # Identity rows are the privileged side of the tombstone; if we cannot even
            # count them we cannot prove the deletion held — re-apply conservatively.
            remaining += 1
        if not remaining:
            continue
        _redeleted, mutable_blocked = _delete_session_mutable(conn, sid)
        privileged = _delete_session_privileged(conn, sid)
        audit(
            conn,
            actor=actor,
            action=REAPPLY_ACTION,
            session_id=sid,
            payload={
                "reason": "tombstone_reapply_after_restore",
                "redeleted_rows": remaining,
                "blocked": privileged["blocked"],
                # BA-39: mutable-table blocks (e.g. FK-held model_call/message) are
                # part of the reapply record, not silently dropped.
                "mutable_blocked": mutable_blocked,
            },
        )
        reapplied.append(str(sid))
    return reapplied


# ----- platform-ID retention schedule -------------------------------------------------------------


def schedule_platform_purge(
    conn: Connection, *, study_close_at: datetime, actor: str
) -> dict[str, Any]:
    """Set ``platform_link.purge_after = study_close + 48h`` (S5) for every unscheduled
    link. Runtime lacks UPDATE on identity — a denial is reported as
    ``blocked_privileged`` and audited rather than silently skipped. Call inside an open
    transaction (``engine.begin()``); identity access is savepoint-guarded."""
    purge_after = study_close_at + PLATFORM_PURGE_DELAY
    ok_count, res_count = try_savepoint(
        conn,
        lambda: conn.execute(
            select(func.count())
            .select_from(s.platform_link)
            .where(s.platform_link.c.purge_after.is_(None))
        ).scalar_one(),
    )
    planned = int(res_count) if ok_count and res_count is not None else None

    def _apply() -> Any:
        return conn.execute(
            update(s.platform_link)
            .where(s.platform_link.c.purge_after.is_(None))
            .values(purge_after=purge_after)
        )

    ok, res = try_savepoint(conn, _apply)
    result = {
        "purge_after": purge_after.isoformat(),
        "planned_links": planned,
        "status": "applied" if ok else "blocked_privileged",
        "applied_links": int(res.rowcount) if ok and res is not None else 0,
    }
    audit(
        conn,
        actor=actor,
        action="retention_schedule",
        payload={**result},
    )
    return result


def purge_due_links(conn: Connection, *, actor: str, dry_run: bool = True) -> dict[str, Any]:
    """Delete platform links whose retention deadline passed (S5 schedule). Dry-run is
    the default; a live run reports the deleted count or ``blocked_privileged``."""
    now = db_now(conn)
    ok_sel, sel = try_savepoint(
        conn,
        lambda: conn.execute(
            select(s.platform_link.c.link_id).where(
                s.platform_link.c.purge_after.is_not(None),
                s.platform_link.c.purge_after <= now,
            )
        ).all(),
    )
    due_ids = [r[0] for r in sel] if ok_sel and sel is not None else []
    select_blocked = not ok_sel
    deleted = 0
    status = "blocked_privileged" if select_blocked else "dry_run"
    if not dry_run and due_ids:
        ok, res = try_savepoint(
            conn,
            lambda: conn.execute(
                delete(s.platform_link).where(s.platform_link.c.link_id.in_(due_ids))
            ),
        )
        status = "applied" if ok else "blocked_privileged"
        deleted = int(res.rowcount) if ok and res is not None else 0
    elif not dry_run and not select_blocked:
        status = "applied"
    audit(
        conn,
        actor=actor,
        action="retention_purge",
        payload={
            "dry_run": dry_run,
            "due_links": len(due_ids),
            "deleted_links": deleted,
            "status": status,
        },
    )
    return {"due_links": len(due_ids), "deleted_links": deleted, "status": status}


# ----- cleanup scope resolution ------------------------------------------------------------------


def cleanup_sessions(
    conn: Connection, *, scope: str, batch_id: str | None = None
) -> list[uuid.UUID]:
    """Sessions eligible for cleanup in ``scope`` (preview/qa/live mode)."""
    if scope not in s.MODES:
        raise RetentionError("bad_scope", 422)
    q = select(s.session.c.session_id).where(s.session.c.mode == scope)
    if batch_id:
        q = q.where(s.session.c.batch_id == batch_id)
    return [r[0] for r in conn.execute(q).all()]


def apply_cleanup(
    conn: Connection,
    *,
    scope: str,
    actor: str,
    reason: str,
    batch_id: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Cleanup over a scope (preview/QA by default; live requires the caller's explicit
    confirmation upstream). Each session gets the standard deletion + tombstone."""
    sids = cleanup_sessions(conn, scope=scope, batch_id=batch_id)
    per_session = []
    for sid in sids:
        per_session.append(
            apply_deletion(conn, sid, actor=actor, reason=reason, scope=scope, dry_run=dry_run)
        )
    return {
        "scope": scope,
        "batch_id": batch_id,
        "dry_run": dry_run,
        "n_sessions": len(sids),
        "sessions": per_session,
    }
