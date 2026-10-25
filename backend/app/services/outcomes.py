"""Durable terminal outcomes: the base payment entitlement and the
non-completion ``completion_receipt`` (spec §8.1; audit H2).

A leaf module shared by ``lifecycle`` (withdraw/decline/completion paths),
``scheduler`` (check-in inactivity) and ``adminops`` (technical stop), so none of
them has to import another to record a disposition.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import insert

from app.db import schema as s
from app.db.tx import RejectionError, TxContext
from app.domain.manifests.binding import BoundManifest
from app.domain.manifests.store import ContentStore
from app.services.enrollment import is_test_group

NO_BONUS_PAYMENT_ARTIFACT_KEY = "payment.s5_nobonus@v1"


def entitlement_for(
    ctx: TxContext,
    bound: BoundManifest,
    mode: str,
    outcome: str = "completed",
    *,
    store: ContentStore | None = None,
    bonus_payment_enabled: bool = True,
) -> dict[str, Any]:
    """Persist the base ``payment_entitlement`` for a finished session (spec §8.1).

    The disposition rule is read from the bound materials, not hardcoded (BA-97 /
    D04): ``completed`` always earns the base; ``technical_stop`` earns it when the
    policy artifact declares ``base_paid`` (a system-terminated session is not the
    participant leaving early); ``withdrawn`` follows the manifest's
    ``outcomes.withdrawal_payment`` (``unaffected`` keeps the base, ``forfeits_base``
    does not); ``abandoned`` and ``declined`` are early departures that the consent
    leaves unpaid. The row always records the pinned policy artifact so ops
    reconciles from ledger, never from nothing. Entitlements are ``pending`` only
    for live sessions whose bound payment policy is activatable; preview/QA and
    non-activatable policies record ``ineligible``.

    BONUS_PAYMENT_ENABLED=0 substitutes the ``payment.s5_nobonus`` policy (300-cent
    flat base, no $0.50 bonus) and records that artifact on the row, so the ledger
    names the policy that actually computed the session's payment."""
    ref = bound.manifest.outcomes.payment_policy
    artifact = bound.artifacts.get("outcomes.payment_policy")
    policy_id, policy_version = ref.artifact_id, ref.version
    body = artifact.artifact.body if artifact is not None else None
    if not bonus_payment_enabled:
        alternate = (
            store.artifacts.get(NO_BONUS_PAYMENT_ARTIFACT_KEY) if store is not None else None
        )
        if alternate is None:
            # A flag-off deployment without the nobonus policy is an operator
            # incident — refuse rather than record a payment under a policy that
            # still folds the bonus into the base.
            raise RejectionError("bonus_payment_policy_unavailable")
        policy_id = alternate.artifact.artifact_id
        policy_version = alternate.artifact.version
        body = alternate.artifact.body
    cents = int(getattr(body, "base_cents", None) or 0) if body is not None else 0
    activatable = bool(getattr(body, "activatable", False)) if body is not None else False
    tech_pays = getattr(body, "technical_stop", None) == "base_paid"
    withdrawal_pays = bound.manifest.outcomes.withdrawal_payment == "unaffected"
    if is_test_group(ctx.conn, ctx.session.get("batch_group_id")):
        mode = "qa"
    owed = (
        outcome == "completed"
        or (outcome == "technical_stop" and tech_pays)
        or (outcome == "withdrawn" and withdrawal_pays)
    )
    status = "pending" if (owed and mode == "live" and activatable and cents > 0) else "ineligible"
    ctx.conn.execute(
        insert(s.payment_entitlement).values(
            session_id=ctx.session_id,
            kind="base",
            cents=cents if status == "pending" else 0,
            policy_artifact_id=policy_id,
            policy_version=policy_version,
            status=status,
        )
    )
    return {"kind": "base", "status": status, "cents": cents if status == "pending" else 0}


def record_terminal_outcome(
    ctx: TxContext,
    bound: BoundManifest,
    *,
    outcome: str,
    final_event_seq: int | None = None,
    reconciled: Mapping[str, Any] | None = None,
    store: ContentStore | None = None,
    bonus_payment_enabled: bool = True,
) -> dict[str, Any]:
    """Persist the durable terminal outcome for a non-completion disposition (spec §8.1):
    the ``completion_receipt`` — whose ``outcome`` is the disposition — and the base
    ``payment_entitlement``, in the same transaction as the terminal stage transition.
    ``final_event_seq`` anchors the receipt on an existing event; when none is given a
    ``session_terminated`` event is appended first. Returns the entitlement summary."""
    entitlement = entitlement_for(
        ctx,
        bound,
        str(ctx.session["mode"]),
        outcome,
        store=store,
        bonus_payment_enabled=bonus_payment_enabled,
    )
    if final_event_seq is None:
        final_event_seq = ctx.append_event(
            "session_terminated", actor="system", payload={"outcome": outcome}
        ).seq
    ctx.conn.execute(
        insert(s.completion_receipt).values(
            session_id=ctx.session_id,
            outcome=outcome,
            reconciled={
                "final_stage": outcome,
                "entitlement": entitlement,
                **dict(reconciled or {}),
            },
            handoff=None,
            final_event_seq=final_event_seq,
        )
    )
    return entitlement
