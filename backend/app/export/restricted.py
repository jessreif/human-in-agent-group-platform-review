"""Restricted export products (INV-1109, spec §9.2, §11).

- :func:`payment_export` — the only export that may read ``identity.platform_link``. It must
  run as ``investigators_compensation`` and returns linkage + entitlement + outcome only;
  the table allowlist is a constant, so a future session-owned table can never leak into it
  by inheritance (the boundary is the list, not a filter). Bonus cents are additionally
  reported as currency units per spec §9.2.
- :func:`sharing_bundle` — identity-free sharing bundle. All cells emit verbatim
  (2026-10-16 owner direction: no exportable file carries ``withheld_unreviewed``;
  no personal identifiers are stored and the PI reviews open-ended responses in
  these files). The bundle contains no initials field (``inv_initials`` is a
  survey-side field the server never stores) and no platform identifiers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Connection, Engine

from app.db import schema as s
from app.export.csvsafe import canonical_checksum

# The complete payment-export surface (spec §9.2 "linkage, entitlement and outcome" and
# result for scoring context). Anything else session-owned is out of scope by construction.
PAYMENT_TABLES = (
    s.platform_link,  # identity.platform_link — compensation role only
    s.payment_entitlement,
    s.completion_receipt,
    s.result,
)

# Free-text columns in session-owned tables. Exports emit them verbatim
# (2026-10-16 owner direction); the registry exists so the redaction-queue admin
# surface (``adminviews.redaction_queue``) can enumerate text-bearing cells.
FREE_TEXT_COLUMNS: dict[str, tuple[str, ...]] = {
    "message": ("body",),
    "model_call": ("input", "raw_response"),
    "request": (),  # structured targets only; no free-text column today
    "displayed_output": ("shown_text",),  # model-authored text shown to the participant
    "redaction_review": ("notes",),
    "admin_audit": ("payload",),
}
BUNDLE_TABLES = (
    s.message,
    s.share,
    s.request,
    s.request_episode,
    s.delivery_receipt,
    s.displayed_output,
)


@dataclass
class BundleTable:
    name: str
    rows: list[dict[str, Any]]


@dataclass
class SharingBundle:
    tables: dict[str, BundleTable] = field(default_factory=dict)
    filters: dict[str, Any] = field(default_factory=dict)


def payment_export(
    engine: Engine,
    *,
    session_ids: list[uuid.UUID] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Batch compensation view over one snapshot.

    Identical scope to :func:`app.db.export.export_payment_linkage`: exactly
    ``PAYMENT_TABLES``. Must run as ``investigators_compensation`` — other roles lack the
    ``identity.platform_link`` grant and the query fails rather than degrading. The
    compensation role cannot read ``app.session``, so scoping is by ``session_ids``;
    without it the export covers every session that has any payment-domain row.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    with engine.connect() as conn:
        conn = conn.execution_options(isolation_level="REPEATABLE READ")
        sids = session_ids
        if sids is None:
            found: set[uuid.UUID] = set()
            for table in PAYMENT_TABLES:
                found.update(r[0] for r in conn.execute(select(table.c.session_id)).all())
            sids = sorted(found)
        for table in PAYMENT_TABLES:
            rows = conn.execute(select(table).where(table.c.session_id.in_(sids))).mappings().all()
            out[table.name] = [dict(r) for r in rows]
        out["payment_summary"] = _payment_summary(out)
    return out


def _payment_summary(out: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """One row per session: linkage + entitlement + outcome, bonus in currency units."""
    links = {str(r["session_id"]): r for r in out.get("platform_link", [])}
    receipts = {str(r["session_id"]): r for r in out.get("completion_receipt", [])}
    results = {str(r["session_id"]): r for r in out.get("result", [])}
    entitlements: dict[str, dict[str, Any]] = {}
    for e in out.get("payment_entitlement", []):
        entitlements.setdefault(str(e["session_id"]), {})[e["kind"]] = e
    rows: list[dict[str, Any]] = []
    for sid in sorted(links | receipts | results | entitlements):
        link, receipt, result = links.get(sid), receipts.get(sid), results.get(sid)
        ents = entitlements.get(sid, {})
        base, bonus = ents.get("base", {}), ents.get("bonus", {})
        bonus_cents = bonus.get("cents", result.get("bonus_cents") if result else 0) or 0
        rows.append(
            {
                "session_id": sid,
                "platform": (link or {}).get("platform"),
                "platform_pid": (link or {}).get("platform_pid"),
                "study_ref": (link or {}).get("study_ref"),
                "session_ref": (link or {}).get("session_ref"),
                "completion_outcome": (receipt or {}).get("outcome"),
                "result_status": (result or {}).get("status"),
                "base_status": base.get("status"),
                "base_cents": base.get("cents"),
                "bonus_status": bonus.get("status"),
                "bonus_cents": bonus_cents,
                # spec §9.2: bonus reported in currency, cents retained alongside
                "bonus_amount": (Decimal(int(bonus_cents)) / 100),
                "base_paid_ref": base.get("paid_ref"),
                "bonus_paid_ref": bonus.get("paid_ref"),
            }
        )
    return rows


def sharing_bundle(
    conn: Connection,
    *,
    session_ids: list[uuid.UUID],
) -> SharingBundle:
    """Identity-free sharing bundle for ``session_ids`` (caller supplies the connection —
    run under the same REPEATABLE READ snapshot as the rest of an export).

    Every cell is verbatim (2026-10-16 owner direction). Initials are never
    emitted — there is no initials column in any exported table — and no
    platform identifiers are included.
    """
    bundle = SharingBundle(filters={"session_ids": [str(i) for i in session_ids]})
    for table in BUNDLE_TABLES:
        rows = (
            conn.execute(select(table).where(table.c.session_id.in_(session_ids))).mappings().all()
        )
        bundle.tables[table.name] = BundleTable(name=table.name, rows=[dict(r) for r in rows])
    return bundle


def bundle_checksums(bundle: SharingBundle) -> dict[str, str]:
    return {name: canonical_checksum(t.rows) for name, t in bundle.tables.items()}
