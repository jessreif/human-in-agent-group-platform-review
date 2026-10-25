#!/usr/bin/env bash
# Build the three Investigators images with the source-bundle checksum stamp (INV-0105/0106).
# Does not start or stop any container.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
INVESTIGATORS_BUILD_SOURCE_SHA256="$(python3 scripts/source_bundle.py)"
INVESTIGATORS_BEHAVIOR_SOURCE_SHA256="$(python3 scripts/source_bundle.py --behavior)"
INVESTIGATORS_BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export INVESTIGATORS_BUILD_SOURCE_SHA256 INVESTIGATORS_BEHAVIOR_SOURCE_SHA256 INVESTIGATORS_BUILD_TIME
echo "source_sha256=${INVESTIGATORS_BUILD_SOURCE_SHA256} behavior_sha256=${INVESTIGATORS_BEHAVIOR_SOURCE_SHA256} build_time=${INVESTIGATORS_BUILD_TIME}"
# Per-build documentation (plan §17.2 FR-2/FR-3): the click-through mock and the technical
# appendix are regenerated for, and stamped with, this exact source bundle. Both live
# under docs/, outside the bundle, so writing them never changes the stamp.
python3 scripts/build_clickthrough.py
uv run --project backend --frozen python scripts/build_tech_appendix.py
docker compose build "$@"
for image in investigators-backend:local investigators-ui:local; do
  echo "${image} $(docker image inspect --format '{{.Id}}' "${image}")"
done
