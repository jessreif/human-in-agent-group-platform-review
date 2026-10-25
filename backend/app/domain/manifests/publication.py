"""Immutable profile publication (INV-0201, spec §4 "Editing a manifest creates a new
immutable version").

``content/published/manifests.json`` records, per manifest ID, the SHA-256 of the materialized
manifest and of its full artifact bundle. Publishing only *adds* entries: an existing entry can
never be rewritten, so editing a published manifest or any artifact it references makes
``bind`` fail until a new manifest version (and new artifact versions) are published.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.manifests.store import (
    PUBLISHED_INDEX,
    ContentError,
    ContentStore,
    PublishedEntry,
    PublishedIndex,
)


class PublicationError(ContentError):
    pass


def verify_publication(store: ContentStore) -> list[str]:
    """Problems for published entries that no longer match the content tree."""
    problems = []
    for manifest_id, entry in sorted(store.published.manifests.items()):
        stored = store.manifests.get(manifest_id)
        if stored is None:
            problems.append(f"{manifest_id}: published but its manifest file is gone")
            continue
        if stored.sha256 != entry.manifest_sha256:
            problems.append(f"{manifest_id}: manifest edited after publication")
            continue
        try:
            bundle = store.bundle_sha256(manifest_id)
        except ContentError as exc:
            problems.extend(exc.problems)
            continue
        if bundle != entry.bundle_sha256:
            problems.append(f"{manifest_id}: referenced artifact edited after publication")
    return problems


def unpublished(store: ContentStore) -> list[str]:
    return sorted(set(store.manifests) - set(store.published.manifests))


def publish(store: ContentStore, *, published_on: str) -> tuple[PublishedIndex, list[str]]:
    """Return the index with every unpublished manifest added. Refuses if any existing entry
    no longer matches (that requires a new version, never an overwrite)."""
    problems = verify_publication(store)
    if problems:
        raise PublicationError(problems)
    entries = dict(store.published.manifests)
    added = []
    for manifest_id in unpublished(store):
        entries[manifest_id] = PublishedEntry(
            manifest_sha256=store.manifests[manifest_id].sha256,
            bundle_sha256=store.bundle_sha256(manifest_id),
            published_on=published_on,
        )
        added.append(manifest_id)
    return PublishedIndex(schema_version="investigators.published/1", manifests=entries), added


def write_index(root: Path, index: PublishedIndex) -> None:
    path = root / PUBLISHED_INDEX
    path.parent.mkdir(parents=True, exist_ok=True)
    data = index.model_dump(mode="json")
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
