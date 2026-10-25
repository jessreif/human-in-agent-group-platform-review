"""Build and version stamps (INV-0105).

The source-bundle checksum and build time are injected at image build time
(``scripts/source_bundle.py`` -> build args -> environment). A build without them reports
``unset`` rather than guessing, so health output can never masquerade as a pinned release.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app import __version__

UNSET = "unset"


@dataclass(frozen=True)
class BuildInfo:
    service_version: str
    source_sha256: str
    behavior_sha256: str
    build_time: str
    image_ref: str

    @property
    def pinned(self) -> bool:
        return self.source_sha256 != UNSET


def build_info() -> BuildInfo:
    source = os.environ.get("INVESTIGATORS_BUILD_SOURCE_SHA256", UNSET) or UNSET
    return BuildInfo(
        service_version=__version__,
        source_sha256=source,
        # Participant-facing-scoped digest (scripts/source_bundle.py --behavior): the
        # compatibility key pins this, so admin/export/dashboard/test churn cannot split
        # an analytic partition. ``source_sha256`` remains the whole-repo release identity.
        # Fallback: an unstamped run degrades to the full checksum — conservative, it
        # over-splits partitions rather than under-merging them.
        behavior_sha256=os.environ.get("INVESTIGATORS_BEHAVIOR_SOURCE_SHA256") or source,
        build_time=os.environ.get("INVESTIGATORS_BUILD_TIME", UNSET) or UNSET,
        image_ref=os.environ.get("INVESTIGATORS_IMAGE_REF", UNSET) or UNSET,
    )
