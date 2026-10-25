"""Persist WP02 artifacts with sessions (INV-0302; completes the persistence half of INV-0207).

- :func:`register_manifest` stores a bound manifest once; a second registration must carry the
  identical hashes (manifests are immutable, spec §4).
- :func:`ensure_batch` creates a batch pinned to one manifest and mode.
- :func:`create_session` pins the enrollment: manifest/bundle/binding hashes, the full binding
  record, mode, timing mode, seed and build checksum. Assignment (arm, compatibility key),
  consent and allocation are WP04 (INV-0403) and happen through commands.
- :func:`record_draws` persists realized random draws; re-recording a key with a different
  value is refused, so replay never silently diverges from what the session experienced.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from app.db import schema as s
from app.domain.manifests.binding import BoundManifest
from app.domain.manifests.canonical import canonical_bytes
from app.domain.rng import DrawRecord


class ManifestMismatchError(ValueError):
    pass


class BatchMismatchError(ValueError):
    pass


class DrawMismatchError(ValueError):
    pass


def register_manifest(conn: Connection, bound: BoundManifest) -> None:
    m = bound.manifest
    conn.execute(
        pg_insert(s.experiment_manifest)
        .values(
            manifest_id=m.manifest_id,
            manifest_sha256=bound.manifest_sha256,
            bundle_sha256=bound.bundle_sha256,
            schema_version=m.schema_version,
            study_key=m.study_key,
            profile_kind=m.profile_kind,
            material_status=m.material_status,
            content=m.model_dump(mode="json"),
            artifacts={slot: [a.key, a.sha256] for slot, a in sorted(bound.artifacts.items())},
        )
        .on_conflict_do_nothing(index_elements=["manifest_id"])
    )
    stored = conn.execute(
        select(
            s.experiment_manifest.c.manifest_sha256, s.experiment_manifest.c.bundle_sha256
        ).where(s.experiment_manifest.c.manifest_id == m.manifest_id)
    ).one()
    if (stored.manifest_sha256, stored.bundle_sha256) != (
        bound.manifest_sha256,
        bound.bundle_sha256,
    ):
        raise ManifestMismatchError(f"{m.manifest_id} is registered with different content")


def ensure_batch(conn: Connection, batch_id: str, bound: BoundManifest) -> None:
    conn.execute(
        pg_insert(s.batch)
        .values(
            batch_id=batch_id,
            study_key=bound.manifest.study_key,
            manifest_id=bound.manifest_id,
            mode=bound.mode,
        )
        .on_conflict_do_nothing(index_elements=["batch_id"])
    )
    stored = conn.execute(
        select(s.batch.c.manifest_id, s.batch.c.mode).where(s.batch.c.batch_id == batch_id)
    ).one()
    if (stored.manifest_id, stored.mode) != (bound.manifest_id, bound.mode):
        raise BatchMismatchError(
            f"batch {batch_id} is pinned to {stored.manifest_id}/{stored.mode}"
        )


def create_session_rows(
    conn: Connection,
    *,
    bound: BoundManifest,
    batch_id: str,
    research_pid: str,
    seed_hex: str,
    build_source_sha256: str,
    behavior_source_sha256: str | None = None,
    arm: str | None = None,
    batch_group_id: str | None = None,
) -> uuid.UUID:
    """Insert participant + session inside the caller's transaction (WP04 entry composes this
    with platform linkage, credential issuance and the entry->consent transition)."""
    session_id = uuid.uuid4()
    participant_id = uuid.uuid4()
    register_manifest(conn, bound)
    ensure_batch(conn, batch_id, bound)
    conn.execute(
        s.participant.insert().values(participant_id=participant_id, research_pid=research_pid)
    )
    conn.execute(
        s.session.insert().values(
            session_id=session_id,
            participant_id=participant_id,
            batch_id=batch_id,
            batch_group_id=batch_group_id,
            manifest_id=bound.manifest_id,
            manifest_sha256=bound.manifest_sha256,
            bundle_sha256=bound.bundle_sha256,
            binding_sha256=bound.binding_sha256,
            binding=bound.binding_record(),
            study_key=bound.manifest.study_key,
            mode=bound.mode,
            timing_mode=bound.timing_mode,
            arm=arm,
            seed_hex=seed_hex,
            build_source_sha256=build_source_sha256,
            # Participant-facing-scoped digest (the compatibility-key build component);
            # NULL on rows predating the scoped digest — pooling treats that as strict.
            # Callers without a stamped scoped digest fall back to the full build
            # checksum: an unstamped build can only over-split, never under-merge.
            behavior_source_sha256=behavior_source_sha256 or build_source_sha256,
        )
    )
    return session_id


def rebind_session(
    conn: Connection, session_id: uuid.UUID, *, batch_id: str, bound: BoundManifest
) -> None:
    """Move a not-yet-assigned batch-group session onto its allocated member batch and that
    batch's manifest binding (consent-time cell assignment). Only legal before any
    manifest-dependent state exists: the session must have no arm yet."""
    register_manifest(conn, bound)
    ensure_batch(conn, batch_id, bound)
    result = conn.execute(
        s.session.update()
        .where(s.session.c.session_id == session_id, s.session.c.arm.is_(None))
        .values(
            batch_id=batch_id,
            manifest_id=bound.manifest_id,
            manifest_sha256=bound.manifest_sha256,
            bundle_sha256=bound.bundle_sha256,
            binding_sha256=bound.binding_sha256,
            binding=bound.binding_record(),
        )
    )
    if result.rowcount != 1:
        raise ValueError("rebind_after_assignment")


def create_session(
    engine: Engine,
    *,
    bound: BoundManifest,
    batch_id: str,
    research_pid: str,
    seed_hex: str,
    build_source_sha256: str,
    behavior_source_sha256: str | None = None,
) -> uuid.UUID:
    with engine.begin() as conn:
        return create_session_rows(
            conn,
            bound=bound,
            batch_id=batch_id,
            research_pid=research_pid,
            seed_hex=seed_hex,
            build_source_sha256=build_source_sha256,
            behavior_source_sha256=behavior_source_sha256,
        )


def _key_text(record: DrawRecord) -> str:
    return canonical_bytes(list(record.key)).decode("utf-8")


def record_draws(conn: Connection, session_id: uuid.UUID, draws: Iterable[DrawRecord]) -> int:
    """Persist realized draws; returns the number of new rows. Identical re-records are no-ops;
    a different value for an existing key raises :class:`DrawMismatchError`."""
    wanted: dict[tuple[str, str], float] = {}
    for record in draws:
        key = (record.stream, _key_text(record))
        if key in wanted and wanted[key] != record.value:
            raise DrawMismatchError(f"conflicting values for draw {key}")
        wanted[key] = record.value
    if not wanted:
        return 0
    rows: list[dict[str, Any]] = [
        {"session_id": session_id, "stream": st, "draw_key": k, "value": v}
        for (st, k), v in wanted.items()
    ]
    inserted = conn.execute(
        pg_insert(s.draw_log)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["session_id", "stream", "draw_key"])
        .returning(s.draw_log.c.draw_key)
    ).all()
    existing = conn.execute(
        select(s.draw_log.c.stream, s.draw_log.c.draw_key, s.draw_log.c.value).where(
            s.draw_log.c.session_id == session_id,
            tuple_(s.draw_log.c.stream, s.draw_log.c.draw_key).in_(list(wanted)),
        )
    ).all()
    for row in existing:
        if row.value != wanted[(row.stream, row.draw_key)]:
            raise DrawMismatchError(
                f"draw {row.stream}:{row.draw_key} was recorded with another value"
            )
    return len(inserted)
