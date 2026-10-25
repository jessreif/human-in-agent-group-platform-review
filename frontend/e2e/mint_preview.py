"""Mint signed preview entry paths for the opt-in browser scenarios.

The signing key comes from ``PREVIEW_SIGNING_KEY`` in the environment of the
disposable API. This script writes paths only. It does not print the token.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from app.domain.manifests.store import ContentStore
from app.services.preview import issue_descriptor

MANIFEST_ID = "tp_demo_v3"
COUNT = 5


def main() -> None:
    raw = os.environ.get("PREVIEW_SIGNING_KEY", "").strip()
    if len(raw.encode("utf-8")) < 32:
        raise SystemExit("preview signing key is missing or shorter than 32 bytes")
    out = Path(sys.argv[1])
    store = ContentStore.load_default()
    manifest_ids = os.environ.get("INVESTIGATORS_E2E_MANIFESTS", MANIFEST_ID).split(",")
    count = int(os.environ.get("INVESTIGATORS_E2E_LINKS_PER_MANIFEST", str(COUNT)))
    if not 1 <= count <= 20 or not 1 <= len(manifest_ids) <= 20:
        raise SystemExit("bounded E2E link count required")
    lines: list[str] = []
    for manifest_id in manifest_ids:
        stored = store.manifests[manifest_id]
        for _ in range(count):
            token = issue_descriptor(
                raw.encode("utf-8"),
                manifest_id=manifest_id,
                manifest_sha256=stored.sha256,
                arm="high",
                seed="cd" * 16,
                mode="preview",
                timing_mode="faithful",
                now=datetime.now(UTC),
                lifetime=timedelta(hours=2),
                issuer="e2e",
            )
            lines.append("/investigators/?pt=" + quote(token, safe=""))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
