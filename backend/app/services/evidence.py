"""Versioned semantic record identity for newly accepted snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.domain.manifests.canonical import content_hash
from app.domain.world import CRATE_CONTENTS, HIDEOUTS


@dataclass(frozen=True)
class EvidenceRecord:
    record_id: str
    crate: int
    body: str
    stamp: str
    event_type: str
    location_id: str
    toward: str | None
    timeline_ordinal: int

    @classmethod
    def parse(cls, row: Mapping[str, Any]) -> EvidenceRecord:
        record = cls(**{key: row[key] for key in cls.__dataclass_fields__})
        if (
            record.crate not in range(1, len(CRATE_CONTENTS) + 1)
            or record.location_id not in HIDEOUTS
            or record.event_type not in ("arrival", "still_at", "departure", "transit")
            or (record.event_type == "transit" and record.toward not in HIDEOUTS)
            or (record.event_type != "transit" and record.toward is not None)
            or not record.body
            or not record.stamp
            or record.timeline_ordinal < 1
        ):
            raise ValueError("invalid evidence record")
        return record

    def digest(self) -> str:
        return content_hash({"version": "semantic_record_v1", **vars(self)})
