"""World persistence: generate-or-load the session's immutable world snapshot
(INV-0504; spec §7.1, §8.1).

The generated world is written once, in the command transaction that first needs it
(``ready`` -> ``solo_review``/``task``): the canonical snapshot JSON + its hash, one
``record`` row per timeline entry, ``seat_holding`` rows for every seat (participant
included), ``file_membership`` rows marking the participant's initial file, the
persisted initial file order inside the snapshot, and every realized ``world``-stream
draw in ``draw_log``. Replay and later exports read those rows; they never re-run the
generator.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Connection, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import schema as s
from app.db.tx import RejectionError, db_now
from app.domain.manifests.binding import BoundManifest
from app.domain.manifests.canonical import canonical_bytes, sha256_hex
from app.domain.manifests.schema import SEATS
from app.domain.rng import DrawLog, RandomStreams
from app.domain.schedules import ScriptedRequestPlan, plan_file_order, plan_scripted_requests
from app.domain.world import (
    N_ASSIGNED,
    World,
    WorldSpec,
    build_world,
    deal_teammate_assignments,
    with_intel_budget,
    with_teammate_assignments,
    with_unassigned_coverage,
)
from app.services.sessions import record_draws
from app.timing.policy import resolve_policy


def world_content(
    world: World, file_order: tuple[str, ...], plans: Sequence[ScriptedRequestPlan] = ()
) -> dict[str, Any]:
    """Canonical snapshot payload: complete truth + holdings + teammate assignments +
    scripted-request plans. Server-side only — participant projections are built from
    the row tables with explicit allowlists (INV-0505)."""
    return {
        "generator": world.spec.generator,
        "world_id": str(world.world_id),
        "world_params": {
            "n_crates": world.spec.n_crates,
            "n_assigned": N_ASSIGNED,
            "crate_overlap": world.spec.overlap,
            "participant_intel_budget": world.spec.participant_intel_budget,
            "covered_unassigned": list(world.covered_unassigned),
        },
        "assigned": list(world.assigned),
        "role_of": {str(n): r for n, r in sorted(world.role_of.items())},
        "cutoff_of": {str(n): c for n, c in sorted(world.cutoff_of.items())},
        "crates": [
            {
                "num": c.num,
                "contents": c.contents,
                "truth": c.truth,
                "walk": list(c.walk),
                "role": c.role,
            }
            for c in world.crates
        ],
        "holdings": {k: list(v) for k, v in sorted(world.holdings.items())},
        "teammate_assignments": {
            seat: list(world.teammate_assignments[seat])
            for seat in sorted(world.teammate_assignments)
        },
        "requested_crates": list(world.requested_crates),
        "never_requested": list(world.never_requested),
        "scripted_requests": [
            {
                "index": p.index,
                "at_active_ms": p.at_active_ms,
                "seat": p.seat,
                "crate": p.crate,
                "target": p.target,
            }
            for p in plans
        ],
        "participant_decisive": {str(n): r for n, r in sorted(world.participant_decisive.items())},
        "initial_file_order": list(file_order),
    }


def generate_world(
    seed_hex: str, arm: str, bound: BoundManifest
) -> tuple[World, tuple[str, ...], tuple[ScriptedRequestPlan, ...], DrawLog]:
    """Pure world-generation pipeline shared by ``ensure_world`` and the
    pre-change fixture replay (audit G06): build → deal → plan requests →
    teammate assignments → unassigned coverage → thin-file trim → file order.
    Deterministic for a given ``seed_hex``/manifest/arm except the minted
    ``world_id``."""
    log = DrawLog()
    streams = RandomStreams(seed_hex, log)
    # 2026-10-15 crate-condition experiment: the manifest's ``task`` pins the
    # world shape — n_crates, the overlap level, and the optional thin-file
    # intel budget. Legacy ``demo_v1`` tasks map to the default spec.
    task = bound.manifest.task
    spec = WorldSpec(
        n_crates=task.n_crates,
        overlap=task.overlap,
        participant_intel_budget=task.participant_intel_budget,
        generator=task.generator,
    )
    world = build_world(streams, spec)
    policy = resolve_policy(bound, arm)
    # ``seat_plan`` builds (UX v7): teammate assignments are dealt before
    # planning — the same keyed ``seat_assign`` shuffle the legacy fill uses —
    # and the asks pick ordinals off each seat's own four crates. Non-independent
    # overlap levels always deal first too: the dealt sets carry the condition's
    # partition/partial/full constraints, so the manifest must use seat_plan
    # (schema cross-rule) for the asks to land on dealt crates.
    needs_deal = (
        policy.scripted_requests.get("seat_plan") is not None or spec.overlap != "independent"
    )
    dealt = deal_teammate_assignments(streams, spec, world.assigned) if needs_deal else None
    requests = plan_scripted_requests(
        policy,
        streams,
        assigned=world.assigned,
        unassigned=[n for n in range(1, spec.n_crates + 1) if n not in world.assigned],
        # ``assigned_own`` targets (2026-10-10): the planner needs the own-file
        # subset plus which crates each seat holds records on, so the asking
        # seat never already holds the crate it asks the participant about.
        own_assigned=[n for n in world.assigned if world.role_of[n] == "own"],
        held_crates={
            seat: frozenset(world.records[rid].crate for rid in world.holdings[seat])
            for seat in SEATS
        },
        seat_assignments=dealt,
    )
    world = with_teammate_assignments(
        streams, world, [(plan.seat, plan.crate) for plan in requests], dealt=dealt
    )
    # Unassigned coverage (D4/audit W03): the participant holds intel on a
    # *limited* set of unassigned crates under ``none`` — every unassigned
    # crate a scripted ask targets plus a seeded fill to at least four. The
    # request plan must be known first (it is, above); records on uncovered
    # unassigned crates redeal to seats. Other levels keep the legacy
    # all-unassigned rule — recorded identically so ``world_params`` always
    # names the true covered set.
    unassigned = [n for n in range(1, spec.n_crates + 1) if n not in world.assigned]
    if spec.overlap == "none":
        asked_unassigned = {p.crate for p in requests if p.crate not in world.assigned}
        need = max(4, len(asked_unassigned))
        fill = streams.shuffle(
            "world",
            [n for n in unassigned if n not in asked_unassigned],
            "coverage_fill",
        )
        covered = sorted(set(asked_unassigned) | set(fill[: need - len(asked_unassigned)]))
    else:
        covered = unassigned
    world = with_unassigned_coverage(streams, world, covered)
    # Thin-file condition: trim the participant's file to the pinned budget and
    # redeal the dropped records — after requests are planned so teammate-crate
    # singles prefer asked crates.
    world = with_intel_budget(streams, world, [p.crate for p in requests])
    file_order = plan_file_order(streams, world.holdings["participant"])
    return world, file_order, requests, log


def ensure_world(
    conn: Connection,
    *,
    session_id: uuid.UUID,
    seed_hex: str,
    arm: str,
    bound: BoundManifest,
) -> uuid.UUID:
    """Generate and persist the session world exactly once; returns ``world_id``.
    Idempotent: an existing snapshot is reused (resume-safe)."""
    existing = conn.execute(
        select(s.world_snapshot.c.world_id).where(s.world_snapshot.c.session_id == session_id)
    ).first()
    if existing is not None:
        return uuid.UUID(str(existing.world_id))

    world, file_order, requests, log = generate_world(seed_hex, arm, bound)
    spec = world.spec

    problems = _validate(world)
    if problems:
        raise RejectionError(f"generated world failed invariants: {problems[0]}")

    content = world_content(world, file_order, requests)
    content_sha = sha256_hex(canonical_bytes(content))
    conn.execute(
        insert(s.world_snapshot).values(
            world_id=world.world_id,
            session_id=session_id,
            generator=spec.generator,
            content=content,
            content_sha256=content_sha,
            created_at=db_now(conn),
        )
    )
    conn.execute(
        insert(s.record).values(
            [
                {
                    "session_id": session_id,
                    "record_id": e.record_id,
                    "world_id": world.world_id,
                    "crate": e.crate,
                    "stamp": e.stamp,
                    "body": e.body,
                    "event_type": e.event_type,
                    "location_id": e.hideout,
                    "toward": e.toward,
                    "timeline_ordinal": e.ordinal,
                    "content_sha256": sha256_hex(e.body.encode()),
                }
                for crate in world.crates
                for e in crate.entries
            ]
        )
    )
    conn.execute(
        insert(s.seat_holding).values(
            [
                {"session_id": session_id, "record_id": rid, "holder": holder}
                for holder, rids in world.holdings.items()
                for rid in rids
            ]
        )
    )
    now = db_now(conn)
    conn.execute(
        insert(s.file_membership).values(
            [
                {
                    "session_id": session_id,
                    "record_id": rid,
                    "source": "initial",
                    "acquired_at": now,
                }
                for rid in world.holdings["participant"]
            ]
        )
    )
    record_draws(conn, session_id, log.records)
    return world.world_id


def _validate(world: World) -> list[str]:
    from app.domain.world import validate_world

    return validate_world(world)


# ----- loading (projections, retrieval, scoring read from rows) ----------------------------------


def participant_file(conn: Connection, session_id: uuid.UUID) -> list[Mapping[str, Any]]:
    """The participant's file in its persisted initial order followed by added records:
    explicit allowlist fields only — never truth, roles, holdings or seat internals
    (INV-0505 participant projection)."""
    order = _initial_order(conn, session_id)
    rows = (
        conn.execute(
            select(
                s.record.c.record_id,
                s.record.c.crate,
                s.record.c.stamp,
                s.record.c.body,
                s.record.c.event_type,
                s.file_membership.c.source,
                s.file_membership.c.acquired_at,
            )
            .select_from(
                s.file_membership.join(
                    s.record,
                    (s.record.c.session_id == s.file_membership.c.session_id)
                    & (s.record.c.record_id == s.file_membership.c.record_id),
                )
            )
            .where(s.file_membership.c.session_id == session_id)
        )
        .mappings()
        .all()
    )
    by_id = {r["record_id"]: r for r in rows}
    ordered = [by_id.pop(rid) for rid in order if rid in by_id]
    ordered.extend(sorted(by_id.values(), key=lambda r: r["acquired_at"]))
    # O-4: "Shared by {seat}" provenance for auto-added cards — the teammate that
    # first delivered the record into chat. Participant re-shares are excluded so
    # they can never overwrite the seat attribution.
    shared_by = {
        str(r["record_id"]): str(r["sharer"])
        for r in conn.execute(
            select(s.share.c.record_id, s.share.c.sharer)
            .where(
                s.share.c.session_id == session_id,
                s.share.c.sharer != "participant",
            )
            .order_by(s.share.c.delivered_seq.desc())
        ).mappings()
    }
    return [
        {
            "record_id": r["record_id"],
            "crate": r["crate"],
            "stamp": r["stamp"],
            "body": r["body"],
            "event_type": r["event_type"],
            "source": r["source"],
            "shared_by": shared_by.get(str(r["record_id"])),
        }
        for r in ordered
    ]


def _initial_order(conn: Connection, session_id: uuid.UUID) -> list[str]:
    content = conn.execute(
        select(s.world_snapshot.c.content).where(s.world_snapshot.c.session_id == session_id)
    ).scalar_one_or_none()
    if content is None:
        raise RejectionError("world_missing")
    return list(content["initial_file_order"])


def snapshot_content(conn: Connection, session_id: uuid.UUID) -> dict[str, Any]:
    """The persisted canonical world snapshot (server-side truth + schedules)."""
    content = conn.execute(
        select(s.world_snapshot.c.content).where(s.world_snapshot.c.session_id == session_id)
    ).scalar_one_or_none()
    if content is None:
        raise RejectionError("world_missing")
    return dict(content)


def world_truth(conn: Connection, session_id: uuid.UUID) -> dict[int, str]:
    """crate -> truth hideout; scoring/research only, never participant-facing."""
    content = conn.execute(
        select(s.world_snapshot.c.content).where(s.world_snapshot.c.session_id == session_id)
    ).scalar_one_or_none()
    if content is None:
        raise RejectionError("world_missing")
    return {int(c["num"]): c["truth"] for c in content["crates"]}


def n_crates_for_session(conn: Connection, session_id: uuid.UUID) -> int:
    """The session world's crate count — ``world_params`` on overlap_v1
    snapshots, else the persisted crate list (legacy snapshots carry no params
    block)."""
    content = conn.execute(
        select(s.world_snapshot.c.content).where(s.world_snapshot.c.session_id == session_id)
    ).scalar_one_or_none()
    if content is None:
        raise RejectionError("world_missing")
    params = content.get("world_params") or {}
    return int(params.get("n_crates") or len(content["crates"]))


def task_shape_for_session(
    snapshot_content: Mapping[str, Any] | None,
    bound: BoundManifest | None,
) -> dict[str, Any]:
    """The administered task shape for participant/replay descriptors (audit S01).

    Resolution order: the persisted ``world_params`` first — the immutable
    shape the generator actually administered — then the bound manifest's
    ``task`` (sessions whose world is not generated yet), then the legacy
    8-crate independent defaults. Never the deployment flags."""
    params = (snapshot_content or {}).get("world_params") or {}
    if params.get("n_crates") is not None:
        return {
            "n_crates": int(params["n_crates"]),
            "n_assigned": int(params.get("n_assigned") or N_ASSIGNED),
            "crate_overlap": params.get("crate_overlap") or "independent",
            "intel_budget": params.get("participant_intel_budget"),
        }
    task = bound.manifest.task if bound is not None else None
    return {
        "n_crates": task.n_crates if task is not None else 8,
        "n_assigned": task.n_assigned if task is not None else N_ASSIGNED,
        "crate_overlap": task.overlap if task is not None else "independent",
        "intel_budget": task.participant_intel_budget if task is not None else None,
    }


def seat_record_ids(conn: Connection, session_id: uuid.UUID, seat: str) -> list[str]:
    """One seat's holdings (JT/PM/participant) — server-side use only."""
    return list(
        conn.execute(
            select(s.seat_holding.c.record_id).where(
                s.seat_holding.c.session_id == session_id,
                s.seat_holding.c.holder == seat,
            )
        ).scalars()
    )


def upsert_added_to_file(
    conn: Connection, session_id: uuid.UUID, record_id: str, acquired_event_seq: int
) -> bool:
    """Idempotent add-to-case-file: returns True iff this call inserted the row.
    The caller must have verified the record was legitimately delivered first.
    ``RETURNING`` is used rather than rowcount, which the driver under-reports
    for ``ON CONFLICT DO NOTHING``."""
    result = conn.execute(
        pg_insert(s.file_membership)
        .values(
            session_id=session_id,
            record_id=record_id,
            source="added_from_team",
            acquired_at=db_now(conn),
            acquired_event_seq=acquired_event_seq,
        )
        .on_conflict_do_nothing(index_elements=["session_id", "record_id"])
        .returning(s.file_membership.c.record_id)
    )
    return result.first() is not None
