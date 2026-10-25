"""Mint signed preview descriptors for the eight current profile/arm combinations.

Only manifests bound to the current (latest literal) consent copy are minted —
superseded/pointer-bound lineages are skipped; issue them via /admin/previews if a
reproduction link is needed (BA-37).

Runs inside the api container (``docker compose exec investigators-api``) so it
shares the deployed env (PREVIEW_SIGNING_KEY) and the baked content store.
Prints one entry URL per combination. Descriptors never consume live
allocations or create payment rows (services/preview.py, INV-1302/0209).

Usage:
  docker compose exec investigators-api python /srv/investigators/scripts/mint_previews.py
  # or bind-mount scripts/ — the file is also runnable under backend uv:
  cd backend && uv run python ../scripts/mint_previews.py --content ../content
"""

from __future__ import annotations

import argparse
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/srv/investigators")  # container layout; harmless locally
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.domain.manifests.store import ContentStore  # noqa: E402
from app.services.preview import issue_descriptor  # noqa: E402
from app.settings import load_settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--content", default=None, help="content root (default: baked/default)")
    ap.add_argument("--env-file", default="./.env")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--base", default="/investigators/")
    args = ap.parse_args()

    settings = load_settings(env_file=args.env_file)
    key = settings.PREVIEW_SIGNING_KEY.get_secret_value().encode()
    if not key.strip():
        print("PREVIEW_SIGNING_KEY is empty; previews disabled (fail closed)", file=sys.stderr)
        return 2

    store = ContentStore(Path(args.content)) if args.content else ContentStore.load_default()
    now = datetime.now(timezone.utc)
    lifetime = timedelta(hours=args.hours)

    # BA-37: mint only manifests bound to the *current* consent copy (the latest
    # consent.reference version carrying literal text); pointer-only ancestors and
    # superseded literal versions stay issuable via /admin/previews for reproduction.
    latest_consent = max(
        (
            s.artifact.version
            for s in store.artifacts.values()
            if s.artifact.artifact_id == "consent.reference"
            and getattr(s.artifact.body, "text", None)
        ),
        default=0,
    )

    for manifest_id in sorted(store.manifests):
        stored = store.manifests[manifest_id]
        resolved, _problems = store.resolve_refs(stored.manifest)
        consent = resolved.get("instruments.consent")
        if not (
            consent is not None
            and consent.artifact.version == latest_consent
            and getattr(consent.artifact.body, "text", None) is not None
        ):
            print(f"{manifest_id}\t(skipped — legacy consent binding)", file=sys.stderr)
            continue
        arms = stored.manifest.arms
        for arm in arms:
            token = issue_descriptor(
                key,
                manifest_id=manifest_id,
                manifest_sha256=stored.sha256,
                arm=arm,
                seed=secrets.token_hex(16),
                mode="preview",
                timing_mode="faithful",
                now=now,
                lifetime=lifetime,
                issuer="lab-walkthrough",
            )
            print(f"{manifest_id}\t{arm}\t{args.base}?pt={token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
