"""Task-stage communication commands: teammate requests, record shares, social replies,
assistant requests and greetings, plus generation scheduling (audit H2).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, insert, select, update

from app.db import schema as s
from app.db.tx import EventRef, RejectionError, TxContext
from app.domain.manifests.artifacts import VoiceBankArtifact
from app.domain.manifests.binding import BoundManifest
from app.domain.manifests.schema import Seat
from app.domain.rng import DrawLog, RandomStreams
from app.domain.schedules import plan_job_draws, seat_for_unprompted_ack
from app.services import (
    activetime,
    engines,
    retrieval,
    scheduler,
    snapshots,
    worlds,
)
from app.services.lifecycle_core import (
    channel_open,
    enforce_pending_cap,
    participant_message,
    resolved_policy,
)
from app.services.sessions import record_draws
from app.timing.policy import resolve_policy


def teammate_request(ctx: TxContext, bound: BoundManifest, p: Mapping[str, Any]) -> dict[str, Any]:
    """Structured intel request (INV-0506/0604): the request, its pending episode, the
    accepted-time retrieval snapshot and the scheduled delivery job are committed
    atomically; the job must return the lead record pinned by that snapshot."""
    crate = p.get("crate")
    if not isinstance(crate, int) or not (1 <= crate <= bound.manifest.task.n_crates):
        raise RejectionError("malformed_request")

    if not channel_open(ctx, bound, "structured_seeking"):
        raise RejectionError("channel_closed")
    selected = retrieval.select_for_crate(ctx.conn, ctx.session_id, crate)
    if selected is None:
        raise RejectionError("no_teammate_records")
    seat, _lead = selected
    enforce_pending_cap(
        ctx,
        resolved_policy(ctx, bound),
        responder=seat,
        limit_field="per_seat_pending_requests",
    )
    grant_ms = activetime.active_ms(ctx.conn, ctx.session_id, ctx.now)
    seq = ctx.append_event(
        "teammate_requested",
        actor="participant",
        active_ms=grant_ms,
        payload={"crate": crate, "responder": seat},
    )
    request_id = uuid.uuid4()
    ctx.conn.execute(
        insert(s.request).values(
            session_id=ctx.session_id,
            request_id=request_id,
            requester="participant",
            responder=seat,
            crate=crate,
            target="latest",
            kind="structured",
            command_id=ctx.command_id,
            accepted_seq=seq.seq,
            accepted_active_ms=grant_ms,
            accepted_at=ctx.now,
        )
    )
    ctx.conn.execute(
        insert(s.request_episode).values(
            session_id=ctx.session_id,
            episode_id=uuid.uuid4(),
            request_id=request_id,
            status="pending",
            opened_at=ctx.now,
        )
    )
    # The structured ask is a real chat message too (UX feedback: the picker must place a
    # visible request in the channel, as the fielded demo does) — linked to its request
    # and carrying the command's active-axis grant so per-command lookups stay pinned.
    participant_message(
        ctx,
        body=f"Requesting intel on Crate {crate}.",
        request_id=request_id,
        crate=crate,
        active_ms=grant_ms,
    )
    # INV-0704: the responder seat owes this response; keep it in auditable agent memory.

    engines.note_request(
        ctx.conn,
        ctx.session_id,
        request_id=request_id,
        requester="participant",
        responder=seat,
        crate=crate,
        at_active_ms=grant_ms,
    )
    snapshot_id = retrieval.snapshot_for_request(
        ctx.conn,
        ctx.session_id,
        crate,
        accepted_event_seq=seq.seq,
        parent_command_id=ctx.command_id,
    )
    plan = scheduler.schedule_response(
        ctx,
        bound,
        request_key=str(request_id),
        response_class="structured_record",
        seat=seat,
        grant_ms=grant_ms,
        generation=None,
        # UX round 4, item 9: the delivered share body is the header-only form;
        # the record rides the attachment, so the schedule counts just the header.
        body_chars=len(f"Sharing intel on Crate {crate}"),
        parent_event_id=seq.event_id,
        payload={
            "request_id": str(request_id),
            "snapshot_id": str(snapshot_id),
            "crate": crate,
            "seat": seat,
        },
    )
    return {"request_id": str(request_id), "snapshot_id": str(snapshot_id), "plan": plan}


def removed_add_to_file(
    _ctx: TxContext, _bound: BoundManifest, _p: Mapping[str, Any]
) -> dict[str, Any]:
    """O-4 (2026-10-03): delivered cards auto-join the file on delivery — the manual
    Add-to-case-file step is gone. Kept as a named rejection for stale clients."""
    raise RejectionError("add_to_file_removed")


def record_share(ctx: TxContext, bound: BoundManifest, p: Mapping[str, Any]) -> dict[str, Any]:
    """Participant-initiated share of an own-file record to the team chat (spec §7.3):
    only records already in the participant's file may be shared."""
    record_id = p.get("record_id")
    if not isinstance(record_id, str):
        raise RejectionError("malformed_share")
    if not channel_open(ctx, bound, "sharing"):
        raise RejectionError("channel_closed")
    member = ctx.conn.execute(
        select(s.file_membership.c.source).where(
            s.file_membership.c.session_id == ctx.session_id,
            s.file_membership.c.record_id == record_id,
        )
    ).first()
    if member is None:
        raise RejectionError("record_not_in_file")
    # BA-100: only initially-owned records may be shared — a teammate-delivered
    # (``added_from_team``) card must not be re-shared by the participant, on the
    # command as well as on the Share button (plan §4).
    if member.source != "initial":
        raise RejectionError("record_not_shareable")
    crate: int = ctx.conn.execute(
        select(s.record.c.crate).where(
            s.record.c.session_id == ctx.session_id,
            s.record.c.record_id == record_id,
        )
    ).scalar_one()
    # Dedupe: a second share of the same record must not post a duplicate chat
    # message (UX round 2, item 6). Idempotent success keeps replay safe.
    already = ctx.conn.execute(
        select(s.share.c.share_id).where(
            s.share.c.session_id == ctx.session_id,
            s.share.c.sharer == "participant",
            s.share.c.record_id == record_id,
        )
    ).first()
    if already is not None:
        return {"shared": True, "already": True}
    seq = ctx.append_event(
        "record_shared",
        actor="participant",
        payload={"record_id": record_id, "crate": crate},
    )
    # A share can answer a teammate's scripted ask on the same crate (spec §7.2): close the
    # most recent matching open episode and link the share to it.
    prompted = (
        ctx.conn.execute(
            select(s.request_episode.c.episode_id, s.request_episode.c.request_id)
            .select_from(
                s.request_episode.join(
                    s.request,
                    (s.request.c.session_id == s.request_episode.c.session_id)
                    & (s.request.c.request_id == s.request_episode.c.request_id),
                )
            )
            .where(
                s.request_episode.c.session_id == ctx.session_id,
                s.request_episode.c.closed_at.is_(None),
                s.request.c.responder == "participant",
                s.request.c.crate == crate,
            )
            .order_by(s.request_episode.c.opened_at.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    prompted_request_id = None if prompted is None else prompted["request_id"]
    if prompted is not None:
        ctx.conn.execute(
            update(s.request_episode)
            .where(s.request_episode.c.episode_id == prompted["episode_id"])
            .values(
                status="resolved",
                substantive_event_seq=seq.seq,
                closed_event_seq=seq.seq,
                closed_at=ctx.now,
                close_reason="participant_share",
            )
        )
        from app.services import activetime as _at

        engines.resolve_request(
            ctx.conn,
            ctx.session_id,
            request_id=prompted["request_id"],
            resolution="participant_share",
            seq=seq.seq,
            at_active_ms=_at.active_ms(ctx.conn, ctx.session_id, ctx.now),
        )
    share_id = uuid.uuid4()
    ctx.conn.execute(
        insert(s.share).values(
            session_id=ctx.session_id,
            share_id=share_id,
            sharer="participant",
            record_id=record_id,
            recipients=["JT", "PM"],
            prompted_by_request_id=prompted_request_id,
            source="participant_action",
            delivered_seq=seq.seq,
            delivered_at=ctx.now,
        )
    )
    _maybe_acknowledge(
        ctx, bound, share_id=share_id, crate=crate, prompted=prompted_request_id, seq=seq
    )
    return {
        "shared": True,
        "prompted_request_id": None if prompted_request_id is None else str(prompted_request_id),
    }


def _maybe_acknowledge(
    ctx: TxContext,
    bound: BoundManifest,
    *,
    share_id: uuid.UUID,
    crate: int,
    prompted: uuid.UUID | None,
    seq: Any,
) -> None:
    """PE acknowledgment draw (REF-PE-03): the requesting seat thanks a prompted share at its
    own probability; an unprompted share draws a weighted seat. Skipped draws are recorded —
    never silently absent (spec §6.5 censoring note)."""
    policy = resolve_policy(bound, ctx.session["arm"])
    ack = policy.teammates.get("acknowledgments")
    if ack is None:
        return
    log = DrawLog()
    streams = RandomStreams(ctx.session["seed_hex"], log)
    share_key = str(share_id)
    if prompted is not None:
        requesting: str = ctx.conn.execute(
            select(s.request.c.requester).where(
                s.request.c.session_id == ctx.session_id,
                s.request.c.request_id == prompted,
            )
        ).scalar_one()
        seat, kind = str(requesting), "prompted"
    else:
        seat, kind = seat_for_unprompted_ack(policy, streams, share_key), "unprompted"
    probability = float(ack["probabilities"][seat][kind])
    if streams.uniform("jobs", "acknowledgment", share_key, "fire") >= probability:
        record_draws(ctx.conn, ctx.session_id, log.records)
        ctx.append_event(
            "acknowledgment_skipped",
            actor="scheduler",
            payload={"seat": seat, "kind": kind, "probability": probability, "share_id": share_key},
            parent_event_id=seq.event_id,
        )
        return
    record_draws(ctx.conn, ctx.session_id, log.records)
    grant_ms = activetime.active_ms(ctx.conn, ctx.session_id, ctx.now)
    scheduler.schedule_response(
        ctx,
        bound,
        request_key=f"ack.{share_id}",
        response_class="acknowledgment",
        seat=seat,
        grant_ms=grant_ms,
        generation=None,
        parent_event_id=seq.event_id,
        payload={
            "seat": seat,
            "crate": crate,
            "request_id": None if prompted is None else str(prompted),
        },
    )


def _schedule_generate(
    ctx: TxContext,
    bound: BoundManifest,
    *,
    request_key: str,
    response_class: str,
    seat: str,
    grant_ms: int,
    snapshot_id: uuid.UUID,
    payload: Mapping[str, Any],
    force_deterministic: bool = False,
) -> uuid.UUID:
    """Persist the sampled timing draws and schedule the wall-clock ``generate`` job whose
    commit derives the concrete ``deliver`` job (spec §6.3 concurrent lifecycle)."""
    policy = resolve_policy(bound, ctx.session["arm"])
    cls = policy.class_policy(response_class, seat)
    logical = f"generate.{request_key}"
    log = DrawLog()
    streams = RandomStreams(ctx.session["seed_hex"], log)
    draws = plan_job_draws(cls, streams, logical_job_key=logical)
    record_draws(ctx.conn, ctx.session_id, log.records)
    deterministic_only = force_deterministic or _generation_mode(bound) == "deterministic_only"
    generation_policy = ctx.conn.execute(
        select(s.event.c.payload)
        .where(s.event.c.session_id == ctx.session_id, s.event.c.type == "generation_policy_pinned")
        .order_by(s.event.c.seq)
        .limit(1)
    ).scalar_one_or_none()

    job_id, _created = ctx.schedule_job(
        logical,
        kind="generate",
        due_wall_at=ctx.now,
        claimable_at=ctx.now,
        payload={
            "request_key": request_key,
            "response_class": response_class,
            "seat": seat,
            "grant_ms": grant_ms,
            "snapshot_id": str(snapshot_id),
            "deterministic_only": deterministic_only,
            "generation_policy": generation_policy,
            "timeout_ms": cls.timeout_ms,
            "components": {
                "delay_ms": draws.components.delay_ms,
                "platform_ms": draws.components.platform_ms,
                "think_ms": draws.components.think_ms,
                "floor_ms": draws.components.floor_ms,
            },
            **dict(payload),
        },
    )
    return job_id


def _generation_mode(bound: BoundManifest) -> str:
    return bound.manifest.teammates.social_generation


def _prompt_ref(bound: BoundManifest, slot: str) -> tuple[str, str, str]:
    """(artifact key, sha256, prompt text) for a pinned prompt artifact slot."""
    stored = bound.artifacts.get(slot)
    if stored is None:
        return "", "", ""
    body = getattr(stored.artifact, "body", None)
    return stored.key, stored.sha256, getattr(body, "text", "")


# BA-75: the ordinary per-seat pending cap exists to bound generated replies —
# a mandatory truthful disclosure must not queue behind it, so probes get their
# own bounded budget instead: at most this many *pending probe disclosures*
# per seat. Beyond it the probe is still logged (message + automation_probe
# event + session flag); that seat just doesn't stack another scheduled line.
MAX_PENDING_PROBE_DISCLOSURES = 3


def _pending_probe_disclosures(ctx: TxContext, seat: str) -> int:
    """Unfinished probe-disclosure deliveries for one seat (BA-75) — pending
    *and* claimed: a claimed job is still an undelivered line, and counting
    only pending rows would let a burst of probes over-schedule while the
    worker holds the earlier ones."""
    return int(
        ctx.conn.execute(
            select(func.count())
            .select_from(s.scheduled_job)
            .where(
                s.scheduled_job.c.session_id == ctx.session_id,
                s.scheduled_job.c.kind == "deliver",
                s.scheduled_job.c.status.in_(("pending", "claimed")),
                s.scheduled_job.c.payload["seat"].astext == seat,
                s.scheduled_job.c.payload["probe_response"].astext == "true",
            )
        ).scalar_one()
    )


def _seat_over_cap(ctx: TxContext, policy: Any, *, responder: str) -> bool:
    """Non-raising pending-cap check (O-2): under broadcast routing a capped seat
    drops out of the responder set instead of sinking the whole message."""
    concurrency = policy.controls.get("concurrency") or {}
    limit = concurrency.get("per_seat_pending_requests")
    if limit is None:
        return False
    pending = select(s.request_episode.c.request_id).where(
        s.request_episode.c.session_id == ctx.session_id,
        s.request_episode.c.status == "pending",
    )
    open_count = ctx.conn.execute(
        select(func.count())
        .select_from(s.request)
        .where(
            s.request.c.session_id == ctx.session_id,
            s.request.c.responder == responder,
            s.request.c.request_id.in_(pending),
        )
    ).scalar_one()
    return int(open_count) >= int(limit)


def _open_team_request(
    ctx: TxContext,
    seat: str,
    seq: EventRef,
    grant_ms: int,
    *,
    crate: int | None,
    target: str,
) -> uuid.UUID:
    """The request + pending episode + auditable agent-memory note for one
    responder seat (O-2): broadcast messages open one episode per responder."""
    request_id = uuid.uuid4()
    ctx.conn.execute(
        insert(s.request).values(
            session_id=ctx.session_id,
            request_id=request_id,
            requester="participant",
            responder=seat,
            crate=crate,
            target=target,
            kind="free_text",
            command_id=ctx.command_id,
            accepted_seq=seq.seq,
            accepted_active_ms=grant_ms,
            accepted_at=ctx.now,
        )
    )
    ctx.conn.execute(
        insert(s.request_episode).values(
            session_id=ctx.session_id,
            episode_id=uuid.uuid4(),
            request_id=request_id,
            status="pending",
            opened_at=ctx.now,
        )
    )
    engines.note_request(
        ctx.conn,
        ctx.session_id,
        request_id=request_id,
        requester="participant",
        responder=seat,
        crate=crate,
        at_active_ms=grant_ms,
    )
    return request_id


def _schedule_grounded_no_holdings(
    ctx: TxContext,
    bound: BoundManifest,
    request_id: uuid.UUID,
    seat: str,
    seq: EventRef,
    grant_ms: int,
    mid: uuid.UUID,
    text: str,
    crate: int,
) -> None:
    """Schedule the deterministic "nothing on that crate" line for one seat —
    the response a typed ask gets when the answering seat holds no records."""
    line = engines.grounded_no_holdings(seat, crate)
    scheduler.schedule_response(
        ctx,
        bound,
        request_key=str(request_id),
        response_class="social_reply",
        seat=seat,
        grant_ms=grant_ms,
        generation=None,
        body_chars=len(line),
        parent_event_id=seq.event_id,
        payload={
            "request_id": str(request_id),
            "parent_message_id": str(mid),
            "participant_text": text,
            "body": line,
            "verbatim": True,
            "grounded": "no_holdings",
            "crate": crate,
            "episode_request_id": str(request_id),
        },
    )


def social_reply(ctx: TxContext, bound: BoundManifest, p: Mapping[str, Any]) -> dict[str, Any]:
    """Participant free-text to the team (O-2/O-1/O-3): durable message, then one
    response episode per responder seat.

    Routing: an explicit ``to`` or seat initials in the text address that seat;
    broadcast tokens or no address reach both seats. A per-seat pending cap drops
    only that seat. Automation probes get the fixed disclosure line; typed intel
    asks in ``nl_only`` produce each seat's own-latest card share (or a grounded
    no-holdings line); assignment asks get grounded per-seat statements; all
    other text goes through the generation lifecycle per seat."""
    body = p.get("body")
    to = p.get("to")
    if not isinstance(body, str) or not body.strip():
        raise RejectionError("malformed_reply")
    if to is not None and to not in ("JT", "PM"):
        raise RejectionError("malformed_reply")
    if not channel_open(ctx, bound, "social_reply"):
        raise RejectionError("channel_closed")
    text = body.strip()
    seats = sorted(engines.addressed_seats(text, to if isinstance(to, str) else None))
    policy = resolved_policy(ctx, bound)
    is_probe = engines.is_automation_probe(text)
    # O-11: a third-person probe is disclosed by the seat it is *about* ("is pm
    # a robot" → PM answers for itself), not by the addressee.
    targets = engines.probed_seats(text, frozenset(seats)) if is_probe else frozenset(seats)
    if is_probe:
        # BA-75: a recognized probe must ALWAYS be logged and can never be
        # refused — a mandatory truthful disclosure may not queue behind the
        # ordinary pending-request cap. Abuse stays bounded by a separate
        # per-seat budget of *pending probe disclosures*: beyond it the probe
        # is still recorded (message + event + session flag) while that seat
        # simply doesn't schedule another line.
        responders = [
            seat
            for seat in sorted(targets)
            if _pending_probe_disclosures(ctx, seat) < MAX_PENDING_PROBE_DISCLOSURES
        ]
    else:
        responders = [
            seat for seat in sorted(targets) if not _seat_over_cap(ctx, policy, responder=seat)
        ]
        if not responders:
            raise RejectionError("over_cap")
    parent = p.get("parent_message_id")
    grant_ms = activetime.active_ms(ctx.conn, ctx.session_id, ctx.now)
    mid, seq = participant_message(
        ctx,
        body=text,
        parent_message_id=uuid.UUID(parent) if isinstance(parent, str) else None,
        payload_extra={"probe_near_miss": True}
        if (not is_probe and engines.probe_near_miss(text))
        else None,
    )

    if is_probe:
        # O-3: the fixed truthful disclosure per responder seat, recorded so the
        # pilot can measure probe frequency and the export can flag the session.
        request_ids = []
        log = DrawLog()
        streams = RandomStreams(ctx.session["seed_hex"], log)
        lines = {
            seat: engines.identity_line(ctx.conn, streams, bound, seat, ctx.session_id)
            for seat in responders
        }
        record_draws(ctx.conn, ctx.session_id, log.records)
        for seat in responders:
            request_id = _open_team_request(ctx, seat, seq, grant_ms, crate=None, target="social")
            line = lines[seat]
            scheduler.schedule_response(
                ctx,
                bound,
                request_key=str(request_id),
                response_class="social_reply",
                seat=seat,
                grant_ms=grant_ms,
                generation=None,
                body_chars=len(line),
                parent_event_id=seq.event_id,
                payload={
                    "request_id": str(request_id),
                    "parent_message_id": str(mid),
                    "participant_text": text,
                    "body": line,
                    "verbatim": True,
                    "probe_response": True,
                    "episode_request_id": str(request_id),
                },
            )
            request_ids.append(str(request_id))
        ctx.append_event(
            "automation_probe",
            actor="participant",
            active_ms=grant_ms,
            payload={
                "message_id": str(mid),
                "seats": responders,
                "probed": sorted(targets),
                "request_ids": request_ids,
            },
            parent_event_id=seq.event_id,
        )
        return {"request_ids": request_ids, "message_id": str(mid)}

    mode = bound.manifest.interaction_mode
    # O-12: bare-number follow-ups ("what about 3?", "#5") resolve a crate for
    # ask detection only — ``crate_mentioned`` stays strict for other readers.
    # BA-64: a multi-clause message resolves the crate in its asking clause —
    # "i have crate 3. what about crate 4?" asks about 4, not 3.
    # 2026-10-10 revision: ``ask_crates`` recovers every named crate (explicit
    # refs plus bare digits) so multi-crate asks fan out instead of resolving a
    # single crate — or none — and falling through to a social acknowledgement.
    n_crates = bound.manifest.task.n_crates
    crate = engines.ask_crate_mentioned(text, n_crates) or engines.followup_crate_mentioned(
        text, n_crates
    )
    ask_crates = engines.crates_referenced(text, n_crates) or ((crate,) if crate else ())

    if mode == "nl_only" and engines.wants_intel_ask(text, ask_crates):
        # O-1/O-2, revised: each asked crate gets exactly one response.
        # Addressed asks keep per-seat semantics — the seat shares its own
        # latest. Broadcast asks deliver one card: the newest record across
        # both seats' holdings, attributed to its holder (the same selection
        # the structured request picker makes), so the decisive entry is the
        # last thing read rather than buried under a stale corroboration. A
        # crate nobody holds gets one grounded no-holdings line.
        request_ids = []
        for crate in ask_crates[: engines.FANOUT_CAP]:
            if len(responders) == 1:
                seat = responders[0]
                # The cap was checked once at acceptance; each fan-out request
                # opens a pending episode, so re-check per crate — an over-cap
                # crate drops out like a capped seat in a broadcast.
                if _seat_over_cap(ctx, policy, responder=seat):
                    continue
                request_id = _open_team_request(
                    ctx, seat, seq, grant_ms, crate=crate, target="latest"
                )
                ctx.append_event(
                    "teammate_requested",
                    actor="participant",
                    active_ms=grant_ms,
                    payload={"crate": crate, "responder": seat, "via": "typed"},
                    parent_event_id=seq.event_id,
                )
                lead = retrieval.seat_latest_for_crate(ctx.conn, ctx.session_id, seat, crate)
                if lead is not None:
                    snapshot_id = retrieval.snapshot_for_seat_request(
                        ctx.conn,
                        ctx.session_id,
                        seat,
                        crate,
                        accepted_event_seq=seq.seq,
                        parent_command_id=ctx.command_id,
                    )
                    scheduler.schedule_response(
                        ctx,
                        bound,
                        request_key=str(request_id),
                        response_class="structured_record",
                        seat=seat,
                        grant_ms=grant_ms,
                        generation=None,
                        body_chars=len(f"Sharing intel on Crate {crate}"),
                        parent_event_id=seq.event_id,
                        payload={
                            "request_id": str(request_id),
                            "snapshot_id": str(snapshot_id),
                            "crate": crate,
                            "seat": seat,
                        },
                    )
                else:
                    _schedule_grounded_no_holdings(
                        ctx, bound, request_id, seat, seq, grant_ms, mid, text, crate
                    )
                request_ids.append(str(request_id))
                continue
            selected = retrieval.select_for_crate(ctx.conn, ctx.session_id, crate)
            # The newest-holder seat may have filled its pending cap earlier in
            # this fan-out (or before the message) — then the other under-cap
            # responder shares its own latest instead of the ask going silent.
            share_seat: Seat | None = None
            if selected is not None:
                order = [selected[0], *(s for s in responders if s != selected[0])]
                share_seat = next(
                    (c for c in order if not _seat_over_cap(ctx, policy, responder=c)),
                    None,
                )
                if share_seat == selected[0]:
                    seat_scope = "cross"
                elif (
                    share_seat is not None
                    and retrieval.seat_latest_for_crate(ctx.conn, ctx.session_id, share_seat, crate)
                    is not None
                ):
                    seat_scope = "seat"
                else:
                    # The under-cap fallback holds nothing on the crate — its
                    # truthful line is "my file has nothing on it".
                    seat_scope = "none"
            else:
                under = [s for s in responders if not _seat_over_cap(ctx, policy, responder=s)]
                share_seat = under[0] if under else None
                seat_scope = "none"
            if share_seat is None:
                continue
            request_id = _open_team_request(
                ctx, share_seat, seq, grant_ms, crate=crate, target="latest"
            )
            ctx.append_event(
                "teammate_requested",
                actor="participant",
                active_ms=grant_ms,
                payload={"crate": crate, "responder": share_seat, "via": "typed"},
                parent_event_id=seq.event_id,
            )
            if seat_scope != "none":
                if seat_scope == "cross":
                    snapshot_id = retrieval.snapshot_for_request(
                        ctx.conn,
                        ctx.session_id,
                        crate,
                        accepted_event_seq=seq.seq,
                        parent_command_id=ctx.command_id,
                    )
                else:
                    snapshot_id = retrieval.snapshot_for_seat_request(
                        ctx.conn,
                        ctx.session_id,
                        share_seat,
                        crate,
                        accepted_event_seq=seq.seq,
                        parent_command_id=ctx.command_id,
                    )
                scheduler.schedule_response(
                    ctx,
                    bound,
                    request_key=str(request_id),
                    response_class="structured_record",
                    seat=share_seat,
                    grant_ms=grant_ms,
                    generation=None,
                    body_chars=len(f"Sharing intel on Crate {crate}"),
                    parent_event_id=seq.event_id,
                    payload={
                        "request_id": str(request_id),
                        "snapshot_id": str(snapshot_id),
                        "crate": crate,
                        "seat": share_seat,
                    },
                )
            else:
                _schedule_grounded_no_holdings(
                    ctx, bound, request_id, share_seat, seq, grant_ms, mid, text, crate
                )
            request_ids.append(str(request_id))
        return {"request_ids": request_ids, "message_id": str(mid)}

    if mode != "nl_only" and engines.is_intel_ask(text, crate):
        # BA-53: in structured mode the request picker is the measurement
        # instrument — a typed intel ask gets the deterministic picker-redirect
        # line per responder seat, never a generated reply.
        request_ids = []
        log = DrawLog()
        streams = RandomStreams(ctx.session["seed_hex"], log)
        redirect_lines: dict[str, str] = {}
        for seat in responders:
            # BA-63: the ask was already classified — pin the picker-redirect
            # pool instead of re-deriving it from a word scan that misses the
            # newer ask forms ("what about 3?", "#5"). UX round 6, items 6/8:
            # the sibling draw avoids near-paraphrasing the first seat's line —
            # simultaneous draws can't see each other in delivered_teammate_lines.
            redirect_lines[seat] = engines.social_fallback(
                ctx.conn,
                streams,
                bound,
                seat,
                text,
                ctx.session_id,
                nl_only=False,
                force_when="asks_intel",
                avoid=list(redirect_lines.values()),
            )
        record_draws(ctx.conn, ctx.session_id, log.records)
        for seat in responders:
            request_id = _open_team_request(ctx, seat, seq, grant_ms, crate=None, target="social")
            line = redirect_lines[seat]
            scheduler.schedule_response(
                ctx,
                bound,
                request_key=str(request_id),
                response_class="social_reply",
                seat=seat,
                grant_ms=grant_ms,
                generation=None,
                body_chars=len(line),
                parent_event_id=seq.event_id,
                payload={
                    "request_id": str(request_id),
                    "parent_message_id": str(mid),
                    "participant_text": text,
                    "body": line,
                    "verbatim": True,
                    "redirect": "structured_request",
                    "episode_request_id": str(request_id),
                },
            )
            request_ids.append(str(request_id))
        return {"request_ids": request_ids, "message_id": str(mid)}

    if engines.is_assignment_ask(text, bound.manifest.task.n_crates):
        # O-1: "what are you on?" — each responder seat states its own assigned
        # crates, rendered from the persisted world (never model text).
        assignments = worlds.snapshot_content(ctx.conn, ctx.session_id).get(
            "teammate_assignments", {}
        )
        request_ids = []
        for seat in responders:
            request_id = _open_team_request(ctx, seat, seq, grant_ms, crate=None, target="social")
            repeat = engines.assignment_answers_delivered(ctx.conn, ctx.session_id, seat) > 0
            line = engines.grounded_assignment(seat, list(assignments.get(seat, ())), repeat=repeat)
            scheduler.schedule_response(
                ctx,
                bound,
                request_key=str(request_id),
                response_class="social_reply",
                seat=seat,
                grant_ms=grant_ms,
                generation=None,
                body_chars=len(line),
                parent_event_id=seq.event_id,
                payload={
                    "request_id": str(request_id),
                    "parent_message_id": str(mid),
                    "participant_text": text,
                    "body": line,
                    "verbatim": True,
                    "grounded": "assignment",
                    "episode_request_id": str(request_id),
                },
            )
            request_ids.append(str(request_id))
        return {"request_ids": request_ids, "message_id": str(mid)}

    if engines.is_greeting(text):
        # UX round 6, item 5: typed greetings never reach generation — the teammate
        # voice contract rejects salutation openers, so greeting replies were
        # reliably validation-rejected and the session fell back to unrelated
        # status lines. Draw the pinned greeting voice lines directly (the same
        # replies the greeting buttons produce).
        request_ids = []
        for seat in responders:
            request_id = _open_team_request(ctx, seat, seq, grant_ms, crate=None, target="social")
            scheduler.schedule_response(
                ctx,
                bound,
                request_key=str(request_id),
                response_class="social_greeting",
                seat=seat,
                grant_ms=grant_ms,
                generation=None,
                parent_event_id=seq.event_id,
                payload={
                    "request_id": str(request_id),
                    "seat": seat,
                    "voice_kind": "greetings",
                    "parent_message_id": str(mid),
                    "participant_text": text,
                    "episode_request_id": str(request_id),
                },
            )
            request_ids.append(str(request_id))
        return {"request_ids": request_ids, "message_id": str(mid)}

    if mode == "nl_only" and engines.unresolved_intel_ask(text, bound.manifest.task.n_crates):
        # 2026-10-10 revision: an intel-flavoured message naming no crate cannot
        # produce a card share — each responder delivers the deterministic
        # "name the crate" invite (the asks_intel_nl pool) instead of a
        # generated acknowledgement that absorbs the ask. Identical in all
        # cells; the bare-crate follow-up classifier turns the participant's
        # "crate 3" reply into the real request.
        request_ids = []
        log = DrawLog()
        streams = RandomStreams(ctx.session["seed_hex"], log)
        invite_lines: dict[str, str] = {}
        for seat in responders:
            invite_lines[seat] = engines.social_fallback(
                ctx.conn,
                streams,
                bound,
                seat,
                text,
                ctx.session_id,
                nl_only=True,
                force_when="asks_intel_nl",
                avoid=list(invite_lines.values()),
            )
        record_draws(ctx.conn, ctx.session_id, log.records)
        for seat in responders:
            request_id = _open_team_request(ctx, seat, seq, grant_ms, crate=None, target="social")
            line = invite_lines[seat]
            scheduler.schedule_response(
                ctx,
                bound,
                request_key=str(request_id),
                response_class="social_reply",
                seat=seat,
                grant_ms=grant_ms,
                generation=None,
                body_chars=len(line),
                parent_event_id=seq.event_id,
                payload={
                    "request_id": str(request_id),
                    "parent_message_id": str(mid),
                    "participant_text": text,
                    "body": line,
                    "verbatim": True,
                    "grounded": "asks_intel_nl",
                    "episode_request_id": str(request_id),
                },
            )
            request_ids.append(str(request_id))
        return {"request_ids": request_ids, "message_id": str(mid)}

    key, sha, prompt_text = _prompt_ref(bound, "teammates.social_prompt")
    request_ids = []
    for seat in responders:
        request_id = _open_team_request(ctx, seat, seq, grant_ms, crate=None, target="social")
        snapshot_id = snapshots.build_snapshot(
            ctx.conn,
            ctx.session_id,
            seat=seat,
            purpose="social_reply",
            visible_event_seq=seq.seq,
            record_ids=worlds.seat_record_ids(ctx.conn, ctx.session_id, seat),
            created_event_seq=seq.seq,
            parent_command_id=ctx.command_id,
        )
        _schedule_generate(
            ctx,
            bound,
            request_key=str(request_id),
            response_class="social_reply",
            seat=seat,
            grant_ms=grant_ms,
            snapshot_id=snapshot_id,
            payload={
                "request_id": str(request_id),
                "parent_message_id": str(mid),
                "participant_text": text,
                "prompt_id": key,
                "prompt_sha256": sha,
                "prompt": prompt_text,
                "provider_params": {"seat": seat},
            },
        )
        request_ids.append(str(request_id))
    return {"request_ids": request_ids, "message_id": str(mid)}


def assistant_request(
    ctx: TxContext,
    bound: BoundManifest,
    p: Mapping[str, Any],
    *,
    ai_assistant: bool,
) -> dict[str, Any]:
    """Assistant prompt: the assistant sees only the participant's file, pinned at accept
    (INV-0507); later additions can never widen its context."""
    # Feature flag: when the deployment disables the assistant the command is
    # refused outright — the channel never opens for these sessions.
    if not ai_assistant:
        raise RejectionError("assistant_disabled")
    body = p.get("body")
    crate = p.get("crate")
    if body is not None and (not isinstance(body, str) or len(body) > 600):
        raise RejectionError("malformed_request")
    if crate is not None and not (
        isinstance(crate, int) and 1 <= crate <= bound.manifest.task.n_crates
    ):
        raise RejectionError("malformed_request")
    if not channel_open(ctx, bound, "assistant_prompting"):
        raise RejectionError("channel_closed")
    if bound.manifest.interaction_mode == "nl_only" and not (
        isinstance(body, str) and body.strip()
    ):
        # FR-1: natural-language-only profiles have no per-crate assistant control —
        # a crate-only (structured) ask is refused, never silently phrased for them.
        raise RejectionError("structured_request_disabled")
    enforce_pending_cap(
        ctx,
        resolved_policy(ctx, bound),
        responder="assistant",
        limit_field="assistant_pending_prompts",
    )
    try:
        crate = engines.assistant_scope(str(body or ""), crate, bound.manifest.task.n_crates)
    except ValueError:
        raise RejectionError("malformed_request") from None
    grant_ms = activetime.active_ms(ctx.conn, ctx.session_id, ctx.now)
    seq = ctx.append_event(
        "assistant_requested",
        actor="participant",
        active_ms=grant_ms,
        payload={"crate": crate, "body": body},
    )
    request_id = uuid.uuid4()
    ctx.conn.execute(
        insert(s.request).values(
            session_id=ctx.session_id,
            request_id=request_id,
            requester="participant",
            responder="assistant",
            crate=crate,
            target="social",
            kind="free_text",
            command_id=ctx.command_id,
            accepted_seq=seq.seq,
            accepted_active_ms=grant_ms,
            accepted_at=ctx.now,
        )
    )
    ctx.conn.execute(
        insert(s.request_episode).values(
            session_id=ctx.session_id,
            episode_id=uuid.uuid4(),
            request_id=request_id,
            status="pending",
            opened_at=ctx.now,
        )
    )
    # Surface the participant's own prompt in the assistant channel (UX feedback): the
    # typed text verbatim, or the structured crate ask phrased as the demo does.
    ask_text = body.strip() if isinstance(body, str) and body.strip() else None
    if ask_text is None and crate is not None:
        ask_text = f"What does my file say about Crate {crate}?"
    if ask_text is not None:
        participant_message(
            ctx,
            body=ask_text,
            channel="assistant",
            request_id=request_id,
            crate=crate,
            active_ms=grant_ms,
        )
    own: list[str] = list(
        ctx.conn.execute(
            select(s.file_membership.c.record_id).where(
                s.file_membership.c.session_id == ctx.session_id
            )
        )
        .scalars()
        .all()
    )
    snapshot_id = snapshots.build_snapshot(
        ctx.conn,
        ctx.session_id,
        seat="assistant",
        purpose="assistant_context",
        visible_event_seq=seq.seq,
        record_ids=list(own),
        created_event_seq=seq.seq,
        parent_command_id=ctx.command_id,
    )
    key, sha, text = _prompt_ref(bound, "assistant.prompt")
    # BA-92: a recognized contents question resolves deterministically *before*
    # model dispatch — the crate→item map is fixed world structure the model
    # could only restate (or partially restate), and the deterministic answer
    # carries the deterministic_contents_summary provenance tag.
    text_body = str(body or "")
    contents_route = p.get("crate") is None and engines.is_contents_question(
        text_body, bound.manifest.task.n_crates
    )
    _schedule_generate(
        ctx,
        bound,
        request_key=str(request_id),
        response_class="assistant",
        seat="assistant",
        grant_ms=grant_ms,
        snapshot_id=snapshot_id,
        payload={
            "request_id": str(request_id),
            "crate": crate,
            "participant_text": body,
            "prompt_id": key,
            "prompt_sha256": sha,
            "prompt": text,
            "contents_route": contents_route,
        },
        force_deterministic=contents_route,
    )
    return {"request_id": str(request_id), "snapshot_id": str(snapshot_id)}


def greeting(ctx: TxContext, bound: BoundManifest, p: Mapping[str, Any]) -> dict[str, Any]:
    """The participant's greeting (the demo's "Let your teammates know you are here" buttons):
    their message plus one scheduled reply per seat on the social_greeting class."""
    text = p.get("text")
    options = set()
    stored = bound.artifacts.get("teammates.voice_bank")
    if stored is not None and isinstance(stored.artifact, VoiceBankArtifact):
        options = set(stored.artifact.body.seats["JT"].greetings)
    if not isinstance(text, str) or (options and text not in options):
        raise RejectionError("malformed_greeting")
    grant_ms = activetime.active_ms(ctx.conn, ctx.session_id, ctx.now)
    mid, seq = participant_message(ctx, body=text, parent_message_id=None)
    for seat in ("JT", "PM"):
        scheduler.schedule_response(
            ctx,
            bound,
            request_key=f"greeting.{seat}.{mid}",
            response_class="social_greeting",
            seat=seat,
            grant_ms=grant_ms,
            generation=None,
            parent_event_id=seq.event_id,
            payload={"seat": seat, "voice_kind": "greetings", "parent_message_id": str(mid)},
        )
    return {"message_id": str(mid)}
