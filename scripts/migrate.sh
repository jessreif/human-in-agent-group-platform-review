#!/usr/bin/env bash
# Apply or inspect Investigators migrations on investigators_db as investigators_migrator
# (INV-0301). Runs Alembic inside the stamped backend image so the migrations executed are the
# ones shipped with the build.
#
#   scripts/migrate.sh current     # show the database revision (default)
#   scripts/migrate.sh upgrade     # upgrade to the build's single head
#   scripts/migrate.sh history     # list revisions in this build
#
# The migrator DSN lives ONLY in ./.env.migrate (owner-created,
# mode 640, same posture as .env; never printed, never committed, never given to the API/worker
# containers, which receive .env only):
#
#   INVESTIGATORS_MIGRATION_DATABASE_URL=postgresql://investigators_migrator:<password>@host.docker.internal:5432/investigators_db
#
# Build the image first (scripts/build_images.sh). This script prints no secrets.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT}/.env.migrate"
IMAGE="${INVESTIGATORS_BACKEND_IMAGE:-investigators-backend:local}"
ACTION="${1:-current}"

if [[ ! -r "${ENV_FILE}" ]]; then
  echo "missing or unreadable ${ENV_FILE}; create it with INVESTIGATORS_MIGRATION_DATABASE_URL (see header)" >&2
  exit 2
fi
if ! grep -q '^INVESTIGATORS_MIGRATION_DATABASE_URL=postgresql://investigators_migrator:' "${ENV_FILE}"; then
  echo "${ENV_FILE} must set INVESTIGATORS_MIGRATION_DATABASE_URL for investigators_migrator" >&2
  exit 2
fi
case "${ACTION}" in
  current) ARGS=(current) ;;
  upgrade) ARGS=(upgrade head) ;;
  history) ARGS=(history) ;;
  *) echo "usage: $0 [current|upgrade|history]" >&2; exit 2 ;;
esac

docker image inspect "${IMAGE}" >/dev/null 2>&1 || {
  echo "image ${IMAGE} not found; run scripts/build_images.sh" >&2
  exit 2
}
echo "image $(docker image inspect --format '{{.Id}}' "${IMAGE}") | alembic ${ARGS[*]}"
exec docker run --rm --read-only --tmpfs /tmp:size=16m \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --add-host host.docker.internal:host-gateway \
  --env-file "${ENV_FILE}" \
  "${IMAGE}" alembic "${ARGS[@]}"
