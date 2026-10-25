"""Post-task instrument commands: item visibility and wording per flag posture,
``item_response``/``item_skip`` and instrument-stage completion (audit H2).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, insert, select, update

from app.db import schema as s
from app.db.tx import RejectionError, TxContext
from app.domain.manifests.artifacts import InstrumentBody
from app.domain.manifests.binding import BoundManifest
from app.domain.manifests.schema import SEATS
from app.services import (
    engines,
)
from app.services.lifecycle_core import INSTRUMENT_SLOTS, transition
from app.services.session_binding import (  # explicit re-exports: historical import path
    variant_for_manifest,
)

# BA-105: the open_ended follow-up stage is artifact-bound
# (``instruments.open_ended`` → ``open_ended.tp_followup@v1``) — wording, item
# ids, ordering and the required policy live in the reviewed artifact, not in
# code. Sessions on manifests without the pin have no open_ended stage at all
# (post_questionnaire → debrief); they no longer inherit one from shared code.
# ``open_ended_followup`` is the legacy instrument_id of the code-defined stage
# — v10-era rows keep it; new rows store the artifact id ``open_ended.tp_followup``.
OPEN_ENDED_INSTRUMENT_ID = "open_ended_followup"  # legacy id (v10 data)
OPEN_ENDED_INSTRUMENT_IDS = frozenset({OPEN_ENDED_INSTRUMENT_ID, "open_ended.tp_followup"})

# Frozen copy of the v10-era code-defined instrument — used ONLY to replay /
# analyze legacy ``open_ended_followup`` rows (admin replay, dashboard item
# expectations, codebook). New sessions resolve the bound artifact instead.
LEGACY_OPEN_ENDED_VERSION = 1
LEGACY_OPEN_ENDED_MAX_CHARS = 4000
LEGACY_OPEN_ENDED_ITEMS: tuple[dict[str, Any], ...] = (
    {
        "item_id": "crate_strategy",
        "skippable": False,
        "options": None,
        "free_text": True,
        "text": (
            "There was no single right way to approach this task, and people "
            "worked through it very differently. Briefly, how did you decide on "
            "a location for each of your crates?"
        ),
    },
    {
        "item_id": "source_reliance",
        "skippable": False,
        "options": None,
        "free_text": True,
        "text": (
            "When you weren't sure about a crate, how did you decide which "
            "source to rely on: your case file, the team chat, or the AI "
            "assistant?"
        ),
    },
)
ASSISTANT_INSTRUMENT_ITEMS = frozenset({"tp_post_04", "pe_post_04"})
# TIME_PRESSURE_ENABLED=0 drops the check-in follow-ups: tp_post_09 asks the
# participant whether the check-in made them feel rushed and tp_post_11 asks
# whether their teammates seemed rushed — both presuppose the manipulation.
PRESSURE_INSTRUMENT_ITEMS = frozenset({"tp_post_09", "tp_post_11"})
SOURCE_RELIANCE_NO_ASSISTANT_TEXT = (
    "When you weren't sure about a crate, how did you decide which source "
    "to rely on: your case file or the team chat?"
)


def instrument_item_visible(
    item_id: str, *, ai_assistant: bool, time_pressure: bool = True
) -> bool:
    """Whether an instrument item is served to participants under the flags."""
    if not ai_assistant and item_id in ASSISTANT_INSTRUMENT_ITEMS:
        return False
    return time_pressure or item_id not in PRESSURE_INSTRUMENT_ITEMS


def instrument_item_text(item_id: str, text: str, *, ai_assistant: bool) -> str:
    """Participant-facing item text under the flag — the open-ended source-
    reliance item loses the assistant clause when the feature is off (and only
    while the bound text still names it, so a reworded artifact passes through)."""
    if not ai_assistant and item_id == "source_reliance" and "AI assistant" in text:
        return SOURCE_RELIANCE_NO_ASSISTANT_TEXT
    return text


def instrument_items(
    bound: BoundManifest,
    stage: str,
    *,
    ai_assistant: bool = True,
    time_pressure: bool = True,
) -> tuple[list[dict[str, Any]], str | None]:
    """Declared items for an instrument stage, plus its applies_to if per-crate.

    Every instrument stage — including ``open_ended`` (BA-105) — resolves its
    items from the bound artifact. An unbound slot yields no items, so callers
    reject with ``instrument_unavailable`` rather than fabricating items.
    ``ai_assistant``/``time_pressure`` are the deployment flags: when off, the
    gated items are neither served nor required."""
    ref_slot = INSTRUMENT_SLOTS[stage]
    stored = bound.artifacts.get(ref_slot)
    if stored is None:
        return [], None
    body = stored.artifact.body
    if not isinstance(body, InstrumentBody):
        return [], None
    items = [
        {
            "item_id": i.item_id,
            "skippable": i.skippable,
            "options": None if i.options is None else list(i.options),
            "free_text": body.response.type == "free_text",
            "max_chars": body.response.max if body.response.type == "free_text" else None,
        }
        for i in body.items
        if instrument_item_visible(
            i.item_id, ai_assistant=ai_assistant, time_pressure=time_pressure
        )
    ]
    return items, getattr(body, "applies_to", None)


def advance_if_complete(
    ctx: TxContext,
    bound: BoundManifest,
    stage: str,
    *,
    ai_assistant: bool = True,
    time_pressure: bool = True,
) -> str | None:
    """Advance an instrument stage once every required response exists (answered or
    skipped — S5: skipping never blocks). Returns the new stage or None."""
    items, applies_to = instrument_items(
        bound, stage, ai_assistant=ai_assistant, time_pressure=time_pressure
    )
    if not items:
        raise RejectionError("instrument_unavailable")
    if stage == "confidence" or applies_to == "per_answered_crate":
        answered = ctx.conn.execute(
            select(func.count(func.distinct(s.confidence_response.c.crate))).where(
                s.confidence_response.c.session_id == ctx.session_id
            )
        ).scalar_one()
        crates = ctx.conn.execute(
            select(func.count(func.distinct(s.answer_revision.c.crate))).where(
                s.answer_revision.c.session_id == ctx.session_id
            )
        ).scalar_one()
        # per_answered_crate: with zero answered crates there is nothing to rate
        if crates > 0 and answered < crates:
            return None
    else:
        instrument_id = bound.artifacts[INSTRUMENT_SLOTS[stage]].key.rsplit("@v", 1)[0]
        have = {
            r.item_id
            for r in ctx.conn.execute(
                select(s.item_response.c.item_id).where(
                    s.item_response.c.session_id == ctx.session_id,
                    s.item_response.c.instrument_id == instrument_id,
                )
            ).all()
        }
        if applies_to == "per_teammate_seat":
            # INV-0905: every declared item must be answered/skipped for each teammate
            # seat; stored item ids carry the seat suffix ("<item_id>.<seat>").
            required = {f"{i['item_id']}.{seat}" for i in items for seat in SEATS}
        else:
            required = {i["item_id"] for i in items}
        if not required <= have:
            return None
    target = {
        "confidence": "peer_ratings"
        if variant_for_manifest(bound.manifest) == "pe"
        else "post_questionnaire",
        "peer_ratings": "post_questionnaire",
        # TP flows stop at the open-ended follow-ups on the way to debrief —
        # but only when the manifest binds ``instruments.open_ended`` (BA-105);
        # PE and unbound TP profiles keep the direct edge.
        "post_questionnaire": "debrief"
        if (
            variant_for_manifest(bound.manifest) == "pe"
            or bound.artifacts.get("instruments.open_ended") is None
        )
        else "open_ended",
        "open_ended": "debrief",
    }[stage]
    transition(ctx, bound, target)
    if target == "peer_ratings":
        # INV-0904: the peer-intro exposure surface is served as the stage opens.

        engines.commit_pe_exposures(ctx, bound, ("peer_intro",))
    ctx.append_event("instrument_complete", actor="system", payload={"stage": stage})
    return target


def item_response(
    ctx: TxContext,
    bound: BoundManifest,
    p: Mapping[str, Any],
    *,
    skip: bool,
    ai_assistant: bool,
    time_pressure: bool,
) -> dict[str, Any]:
    """Record or skip one instrument item response for the current instrument stage.
    Validates the item against the bound artifact and the session's flag posture,
    rejects confidence ratings for crates the participant never answered (INV-0905),
    keeps revisions append-only, and advances the stage once the instrument is
    complete."""
    item_id = p.get("item_id")
    crate = p.get("crate")
    value = p.get("value")
    if not isinstance(item_id, str):
        raise RejectionError("malformed_item")
    if ctx.stage == "confidence":
        if not isinstance(crate, int) or not (1 <= crate <= bound.manifest.task.n_crates):
            raise RejectionError("confidence_needs_crate")
        if not skip and not (isinstance(value, (int, float)) and 0 <= value <= 100):
            raise RejectionError("confidence_out_of_range")
        # INV-0905: confidence is per-answered-crate; a rating for a crate the
        # participant never answered is fabricated data — reject it.
        answered_crate = ctx.conn.execute(
            select(func.count())
            .select_from(s.answer_revision)
            .where(
                s.answer_revision.c.session_id == ctx.session_id,
                s.answer_revision.c.crate == crate,
            )
        ).scalar_one()
        if not answered_crate:
            raise RejectionError("confidence_not_applicable")
        if (
            skip
            and not instrument_items(
                bound, "confidence", ai_assistant=ai_assistant, time_pressure=time_pressure
            )[0][0]["skippable"]
        ):
            # 2026-10-08 (owner direction): required-item builds reject skips
            # server-side — hiding the affordance alone is not a control.
            raise RejectionError("skip_not_permitted")
        seq = ctx.append_event(
            "confidence_recorded",
            actor="participant",
            payload={"crate": crate, "skipped": skip},
        )
        art = bound.artifacts[INSTRUMENT_SLOTS["confidence"]]
        version = int(art.key.rsplit("@v", 1)[1])
        revision = (
            ctx.conn.execute(
                select(func.count())
                .select_from(s.confidence_response)
                .where(
                    s.confidence_response.c.session_id == ctx.session_id,
                    s.confidence_response.c.crate == crate,
                )
            ).scalar_one()
            + 1
        )
        ctx.conn.execute(
            insert(s.confidence_response).values(
                session_id=ctx.session_id,
                crate=crate,
                revision=revision,
                value=None if skip else value,
                status="skipped" if skip else "answered",
                instrument_version=version,
                event_seq=seq.seq,
            )
        )
        new_stage = advance_if_complete(
            ctx, bound, "confidence", ai_assistant=ai_assistant, time_pressure=time_pressure
        )
        return {"stage": new_stage or ctx.stage}

    # generic instrument item (peer_ratings / post_questionnaire / open_ended —
    # all resolve their instrument + items from the bound artifact, BA-105)
    stored_art = bound.artifacts.get(INSTRUMENT_SLOTS[ctx.stage])
    if stored_art is None:
        raise RejectionError("instrument_unavailable")
    instrument_id, version_text = stored_art.key.rsplit("@v", 1)
    declared = {
        i["item_id"]: i
        for i in instrument_items(
            bound, ctx.stage, ai_assistant=ai_assistant, time_pressure=time_pressure
        )[0]
    }
    if item_id not in declared:
        raise RejectionError("unknown_item")
    if skip and not declared[item_id]["skippable"]:
        # 2026-10-08 (owner direction): required-item builds reject skips
        # server-side — hiding the affordance alone is not a control.
        raise RejectionError("skip_not_permitted")
    if not skip and value is None:
        raise RejectionError("missing_value")
    if (
        declared[item_id]["free_text"]
        and not skip
        and (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > int(declared[item_id]["max_chars"])
        )
    ):
        raise RejectionError("invalid_open_response")
    # UX round 4, item 14: a choice item's value must be one of its declared
    # options — anything else is a fabricated response.
    options = declared[item_id].get("options")
    if options is not None and not skip and value not in options:
        raise RejectionError("unknown_option")
    seat = p.get("seat")
    applies_to = instrument_items(
        bound, ctx.stage, ai_assistant=ai_assistant, time_pressure=time_pressure
    )[1]
    if applies_to == "per_teammate_seat":
        # INV-0905: per-teammate-seat instruments persist one row per (item, seat); the
        # stored item id carries the seat suffix and the seat rides in the response.
        if seat not in SEATS:
            raise RejectionError("seat_required")
        item_id = f"{item_id}.{seat}"
    elif seat is not None:
        raise RejectionError("unexpected_seat")
    seq = ctx.append_event(
        "item_recorded",
        actor="participant",
        payload={
            "instrument": instrument_id,
            "item_id": item_id,
            "skipped": skip,
            "seat": seat,
        },
    )
    existing = ctx.conn.execute(
        select(s.item_response.c.revision_count).where(
            s.item_response.c.session_id == ctx.session_id,
            s.item_response.c.instrument_id == instrument_id,
            s.item_response.c.item_id == item_id,
        )
    ).first()
    response_payload = None if skip else {"value": value, "seat": seat}
    if existing is None:
        ctx.conn.execute(
            insert(s.item_response).values(
                session_id=ctx.session_id,
                instrument_id=instrument_id,
                item_id=item_id,
                instrument_version=int(version_text),
                response=response_payload,
                status="skipped" if skip else "answered",
                missing_reason=p.get("missing_reason"),
                revision_count=1,
                first_seq=seq.seq,
                last_seq=seq.seq,
            )
        )
    else:
        ctx.conn.execute(
            update(s.item_response)
            .where(
                s.item_response.c.session_id == ctx.session_id,
                s.item_response.c.instrument_id == instrument_id,
                s.item_response.c.item_id == item_id,
            )
            .values(
                response=response_payload,
                status="skipped" if skip else "answered",
                missing_reason=p.get("missing_reason"),
                revision_count=existing.revision_count + 1,
                last_seq=seq.seq,
                updated_at=ctx.now,
            )
        )
    new_stage = advance_if_complete(
        ctx, bound, ctx.stage, ai_assistant=ai_assistant, time_pressure=time_pressure
    )
    return {"stage": new_stage or ctx.stage}
