"""Session → bound-manifest reconstruction and stage-variant lookup (audit H2).

A leaf module: depends only on the schema, the manifest domain and the stage
graph, so ``lifecycle`` (commands) and ``scheduler`` (jobs) can both import it at
module level. Before 2026-10-17 these lived in ``lifecycle`` and the scheduler
reached them through function-local imports, hiding a lifecycle↔scheduler cycle.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Connection

from app.db import schema as s
from app.db.tx import RejectionError
from app.domain import stage_graph as sg
from app.domain.manifests.binding import BoundManifest
from app.domain.manifests.schema import Manifest
from app.domain.manifests.store import ContentStore, StoredArtifact


def bound_for_session(
    conn: Connection, store: ContentStore, session: Mapping[str, Any]
) -> BoundManifest:
    """Rebuild the bound manifest from the session's *pinned* hashes, not the current
    publication — a republished profile must not change what a live session is running."""
    row = (
        conn.execute(
            select(s.experiment_manifest).where(
                s.experiment_manifest.c.manifest_id == session["manifest_id"]
            )
        )
        .mappings()
        .one()
    )
    if row["manifest_sha256"] != session["manifest_sha256"]:
        raise RejectionError("manifest_pin_drift")
    # Strict mode: JSONB round-trips tuples as lists, so validate from JSON text.
    manifest = Manifest.model_validate_json(json.dumps(row["content"]))
    artifacts: dict[str, StoredArtifact] = {}
    for slot, (key, sha) in session["binding"]["artifacts"].items():
        stored = store.artifacts.get(key)
        if stored is None or stored.sha256 != sha:
            raise RejectionError("artifact_pin_drift")
        artifacts[slot] = stored
    overlay: StoredArtifact | None = None
    overlay_pair = session["binding"].get("qa_overlay")
    if overlay_pair is not None:
        overlay = store.artifacts.get(overlay_pair[0])
        if overlay is None or overlay.sha256 != overlay_pair[1]:
            raise RejectionError("artifact_pin_drift")
    return BoundManifest(
        manifest=manifest,
        manifest_sha256=row["manifest_sha256"],
        bundle_sha256=row["bundle_sha256"],
        artifacts=artifacts,
        mode=session["mode"],
        timing_mode=session["timing_mode"],
        qa_overlay=overlay,
        launch_eligible=bool(session["binding"].get("launch_eligible")),
    )


def variant_for_manifest(manifest: Manifest) -> sg.StageVariant:
    first = manifest.channels.phases[0].name if manifest.channels.phases else None
    return sg.variant_for(manifest.study_key, first)
