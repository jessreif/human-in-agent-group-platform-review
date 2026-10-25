"""Typed claims and the evidence-transfer ledger (INV-0508; spec §7.6).

A claim is a factual assertion about a record/timeline — never the record itself.
Support evaluation is source-backed: the claim's (crate, location, event_type,
evidence time) must be supported by its cited source records, with
departure/transit/staleness handled conservatively. The transfer ledger derives each
record's position on the branching chain

    world -> held -> eligible -> selected -> delivered -> rendered
                 +-> added to file -> assistant context -> referenced -> answer revision

with bypassed stages and never-arrived states preserved, so repetition across
channels still counts as one underlying record.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any, Literal

from sqlalchemy import Connection, insert, select

from app.db import schema as s
from app.db.tx import db_now

Assertion = Literal["asserted", "uncertain", "hedged", "negated"]
ClaimIssuer = Literal["participant", "JT", "PM", "assistant", "system", "coder"]


# where a claim's location/status combination fails support evaluation
class SupportVerdict(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    PARTIAL = "partial"


def evaluate_support(
    claim: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
) -> SupportVerdict:
    """Source-backed evaluation of one claim against its cited records.

    ``sources`` are the cited record rows (crate, hideout-ish body not parsed — the
    structured event fields are authoritative): each must agree on the claim's crate;
    support requires a record of matching crate whose event semantics license the
    assertion:

    - ``asserted``/``hedged`` ``arrival`` or ``still_at`` at ``location_id``: a
      confirming record exists and no *later* cited record contradicts it
      (departure/transit away) -> supported; contradiction -> unsupported.
    - ``asserted`` ``departure``/``transit``: a matching cited record exists.
    - ``uncertain``: matching cited evidence exists but a fresher cited record
      supersedes it -> partial.
    - wrong-crate citations or empty sources: unsupported — a permitted noun
      alone never licenses a claim (spec §7.6).
    """
    crate = claim["crate"]
    location = claim["location_id"]
    etype = claim["event_type"]
    assertion = claim["assertion"]
    if assertion == "negated":
        # negated claims are logged for audit; support evaluation does not apply
        return SupportVerdict.UNSUPPORTED
    matching = [r for r in sources if r["crate"] == crate]
    if not matching:
        return SupportVerdict.UNSUPPORTED
    matching.sort(key=lambda r: r["timeline_ordinal"])
    confirms = [
        r
        for r in matching
        if r["location_id"] == location and r["event_type"] in ("arrival", "still_at")
    ]
    last = matching[-1]
    evidence_time = claim.get("evidence_time")
    if evidence_time:
        confirms = [r for r in confirms if r.get("stamp") == evidence_time]
    if etype == "arrival":
        confirms = [r for r in confirms if r["event_type"] == "arrival"]
    if etype in ("arrival", "still_at"):
        if not confirms:
            return SupportVerdict.UNSUPPORTED
        if claim.get("historical") and evidence_time:
            return SupportVerdict.SUPPORTED
        newest_confirmer = confirms[-1]
        superseded = last["timeline_ordinal"] > newest_confirmer["timeline_ordinal"] and (
            last["event_type"] in ("departure", "transit") or last["location_id"] != location
        )
        if assertion == "uncertain" and superseded:
            return SupportVerdict.PARTIAL
        if assertion == "asserted" and superseded:
            # claiming "still at X" when the same sources show it left
            return SupportVerdict.UNSUPPORTED
        return SupportVerdict.SUPPORTED
    # departure / transit claims
    if etype == "transit":
        # A transit record's location_id is the ORIGIN and ``toward`` the destination —
        # "heading toward X" asserts the destination leg, so either endpoint of a
        # recorded transit supports the claim (RCA-2026-10-09: matching origin only
        # auto-rejected every correct destination mention).
        exact = [
            r
            for r in matching
            if r["event_type"] == "transit"
            and (
                (claim.get("role") != "destination" and r["location_id"] == location)
                or (claim.get("role") != "origin" and r.get("toward") == location)
            )
        ]
    else:
        # A transit leg out of X is itself a recorded departure from X — "left X"
        # stays supported when the file shows transit whose origin is X.
        exact = [
            r
            for r in matching
            if r["location_id"] == location
            and (r["event_type"] == etype or r["event_type"] == "transit")
        ]
    if evidence_time:
        exact = [r for r in exact if r.get("stamp") == evidence_time]
    if not exact:
        return SupportVerdict.UNSUPPORTED
    return (
        SupportVerdict.SUPPORTED
        if assertion in ("asserted", "uncertain")
        else SupportVerdict.PARTIAL
    )


def register_claim(
    conn: Connection,
    session_id: uuid.UUID,
    *,
    crate: int,
    location_id: str,
    event_type: str,
    assertion: Assertion,
    issuer: ClaimIssuer,
    source_record_ids: Sequence[str] = (),
    source_event_seqs: Sequence[int] = (),
    evidence_time: str | None = None,
    snapshot_id: uuid.UUID | None = None,
    model_call_id: uuid.UUID | None = None,
    context_event_seq: int | None = None,
    revises_claim_id: uuid.UUID | None = None,
    evaluate: bool = True,
    historical: bool = False,
    evaluated_verdict: str | None = None,
    role: str | None = None,
) -> uuid.UUID:
    """Persist one typed claim plus its lineage rows; support is evaluated against the
    cited records when ``evaluate`` (coder/offline claims may register unevaluated).
    ``historical`` marks an anchored retelling the output checker already classified —
    it forwards to ``evaluate_support`` so the ledger verdict cannot disagree with the
    gate that delivered (or rejected) the text."""
    sources: list[Mapping[str, Any]] = []
    if evaluate and source_record_ids:
        sources = [
            dict(r)
            for r in conn.execute(
                select(s.record).where(
                    s.record.c.session_id == session_id,
                    s.record.c.record_id.in_(list(source_record_ids)),
                )
            )
            .mappings()
            .all()
        ]
    verdict: str | None = None
    if evaluate:
        verdict = evaluate_support(
            {
                "crate": crate,
                "location_id": location_id,
                "event_type": event_type,
                "assertion": assertion,
                "historical": historical,
                "evidence_time": evidence_time,
                "role": role,
            },
            sources,
        ).value
    if evaluated_verdict is not None:
        verdict = SupportVerdict(evaluated_verdict).value
    claim_id = uuid.uuid4()
    conn.execute(
        insert(s.claim).values(
            session_id=session_id,
            claim_id=claim_id,
            crate=crate,
            location_id=location_id,
            event_type=event_type,
            evidence_time=evidence_time,
            assertion=assertion,
            support_status=verdict or "unevaluated",
            issuer=issuer,
            snapshot_id=snapshot_id,
            model_call_id=model_call_id,
            context_event_seq=context_event_seq,
            revises_claim_id=revises_claim_id,
            created_at=db_now(conn),
        )
    )
    for rid in dict.fromkeys(source_record_ids):
        conn.execute(
            insert(s.claim_source).values(
                session_id=session_id,
                claim_source_id=uuid.uuid4(),
                claim_id=claim_id,
                source_kind="record",
                record_id=rid,
            )
        )
    for seq in dict.fromkeys(source_event_seqs):
        conn.execute(
            insert(s.claim_source).values(
                session_id=session_id,
                claim_source_id=uuid.uuid4(),
                claim_id=claim_id,
                source_kind="event",
                event_seq=seq,
            )
        )
    return claim_id


# ----- evidence-transfer ledger -------------------------------------------------------------


class TransferStage(StrEnum):
    WORLD = "world"  # exists in the generated world
    HELD = "held"  # initial seat holding
    ELIGIBLE = "eligible"  # inside an accepted-request snapshot candidate set
    SELECTED = "selected"  # the snapshot's lead record
    DELIVERED = "delivered"  # share/message committed for the participant
    RENDERED = "rendered"  # participant delivery receipt
    IN_FILE = "in_file"  # added to case file (separate branch from DELIVERED)
    IN_CONTEXT = "in_context"  # inside an assistant_context snapshot
    REFERENCED = "referenced"  # cited by a claim
    IN_ANSWER = "in_answer"  # answer revision followed the reference
    NEVER = "never"  # never reached the participant


def transfer_ledger(conn: Connection, session_id: uuid.UUID) -> dict[str, list[str]]:
    """Per-record stage reachability; a record may hold several stages at once and the
    file/context branch is kept separate from delivery (spec §7.6)."""
    ledger: dict[str, list[str]] = {}

    def mark(rid: str, stage: TransferStage) -> None:
        ledger.setdefault(rid, [])
        if stage.value not in ledger[rid]:
            ledger[rid].append(stage.value)

    for rid, holder in conn.execute(
        select(s.seat_holding.c.record_id, s.seat_holding.c.holder).where(
            s.seat_holding.c.session_id == session_id
        )
    ):
        mark(rid, TransferStage.HELD)
        if holder == "participant":
            # initial holdings are in the file from the start
            mark(rid, TransferStage.DELIVERED)
            mark(rid, TransferStage.IN_FILE)
    for rid in conn.execute(
        select(s.file_membership.c.record_id).where(
            s.file_membership.c.session_id == session_id,
            s.file_membership.c.source == "added_from_team",
        )
    ).scalars():
        mark(rid, TransferStage.IN_FILE)
    for rid, _sharer in conn.execute(
        select(s.share.c.record_id, s.share.c.sharer).where(s.share.c.session_id == session_id)
    ):
        mark(rid, TransferStage.DELIVERED)
    # rendered: records on messages whose delivery event got a rendered receipt
    for rid in conn.execute(
        select(s.message.c.record_id)
        .select_from(
            s.delivery_receipt.join(
                s.message,
                (s.message.c.session_id == s.delivery_receipt.c.session_id)
                & (s.message.c.committed_seq == s.delivery_receipt.c.seq),
            )
        )
        .where(
            s.delivery_receipt.c.session_id == session_id,
            s.delivery_receipt.c.rendered_at.is_not(None),
            s.message.c.record_id.is_not(None),
        )
    ).scalars():
        mark(rid, TransferStage.RENDERED)
    for row in conn.execute(
        select(
            s.knowledge_snapshot.c.snapshot_id,
            s.knowledge_snapshot.c.purpose,
            s.knowledge_snapshot.c.record_manifest,
        ).where(s.knowledge_snapshot.c.session_id == session_id)
    ).mappings():
        ids = [m["record_id"] for m in row["record_manifest"]]
        if row["purpose"] == "structured_retrieval":
            for rid in ids:
                mark(rid, TransferStage.ELIGIBLE)
            from app.services.retrieval import lead_from_manifest

            lead = lead_from_manifest(conn, session_id, row["record_manifest"])
            if lead is not None:
                mark(lead[1], TransferStage.SELECTED)
        elif row["purpose"] == "assistant_context":
            for rid in ids:
                mark(rid, TransferStage.IN_CONTEXT)
    for rid in conn.execute(
        select(s.claim_source.c.record_id).where(
            s.claim_source.c.session_id == session_id,
            s.claim_source.c.record_id.is_not(None),
        )
    ).scalars():
        mark(rid, TransferStage.REFERENCED)
    # never-arrived: teammate-held records that never reached delivered or in_file
    all_held: set[str] = set(
        conn.execute(
            select(s.seat_holding.c.record_id).where(
                s.seat_holding.c.session_id == session_id,
                s.seat_holding.c.holder != "participant",
            )
        ).scalars()
    )
    for rid in all_held:
        stages = ledger.get(rid, [])
        if (
            TransferStage.DELIVERED.value not in stages
            and TransferStage.IN_FILE.value not in stages
        ):
            mark(rid, TransferStage.NEVER)
    return ledger
