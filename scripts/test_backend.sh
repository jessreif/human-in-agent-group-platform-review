#!/usr/bin/env bash
# Backend gates (INV-0102/0104/0107):
#   1. ruff format --check, ruff check, mypy --strict, unit tests (no database, no provider)
#   2. integration tests against EPHEMERAL PostgreSQL containers: tmpfs data
#      directory, random password, random loopback port, removed on exit.
#      One container per pytest-xdist worker. The destructive-test guard still
#      proves each one differs from the real runtime DSN (read from ../.env
#      without printing it).
# Nothing here touches the host PostgreSQL cluster or any live database.
#
# Worker count defaults to 4. Do not use -n auto: this host reports 80
# processors, and one server per processor is not the gate. Each worker's
# URL is INVESTIGATORS_TEST_DATABASE_URL_GW<n> (plus GW0 copied to
# INVESTIGATORS_TEST_DATABASE_URL for a non-xdist run). URLs are not echoed.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PG_IMAGE="${INVESTIGATORS_TEST_PG_IMAGE:-postgres:15-alpine@sha256:f7d23353e1b15400d22ebe31189f4d314b87a4c129cc400c8c2d8d4ca127bf81}"
WORKERS="${INVESTIGATORS_TEST_WORKERS:-4}"
if ! [[ "${WORKERS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "INVESTIGATORS_TEST_WORKERS must be a positive integer" >&2
  exit 2
fi
cd "${ROOT}/backend"

uv sync --frozen --quiet
uv run ruff format --check .
uv run ruff check .
uv run mypy
# Unit tests do not open a database. --dist loadfile keeps one module on one worker.
uv run pytest -q -n "${WORKERS}" --dist loadfile -m "not integration"

declare -a NAMES=()
declare -a PASSES=()
cleanup() {
  local i
  for i in "${!NAMES[@]}"; do
    docker rm -f "${NAMES[$i]}" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT

for ((i = 0; i < WORKERS; i++)); do
  name="investigators-testdb-$$-${i}-${RANDOM}"
  pass="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
  NAMES+=("${name}")
  PASSES+=("${pass}")
  docker run -d --rm --name "${name}" --label investigators.disposable=true \
    -e POSTGRES_USER=investigators_test -e POSTGRES_PASSWORD="${pass}" \
    -e POSTGRES_DB=investigators_test_db \
    --tmpfs /var/lib/postgresql/data:rw -p 127.0.0.1::5432 "${PG_IMAGE}" >/dev/null
done

for ((i = 0; i < WORKERS; i++)); do
  ready=0
  for _ in $(seq 1 60); do
    if docker exec "${NAMES[$i]}" pg_isready -h 127.0.0.1 -U investigators_test -d investigators_test_db >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done
  if [[ "${ready}" != 1 ]]; then
    echo "disposable postgres worker ${i} did not become ready" >&2
    exit 1
  fi
  port="$(docker port "${NAMES[$i]}" 5432/tcp | head -n1 | awk -F: '{print $NF}')"
  export "INVESTIGATORS_TEST_DATABASE_URL_GW${i}=postgresql://investigators_test:${PASSES[$i]}@127.0.0.1:${port}/investigators_test_db"
done

export INVESTIGATORS_TEST_DATABASE_URL="${INVESTIGATORS_TEST_DATABASE_URL_GW0}"
export INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS=1
export INVESTIGATORS_REQUIRE_INTEGRATION=1
export INVESTIGATORS_LIVE_ENV_FILE="${ROOT}/.env"
# The template DSN names the canonical live database even when .env is unreadable.
INVESTIGATORS_EXTRA_LIVE_DSNS="$(sed -n 's/^DATABASE_URL=//p' "${ROOT}/.env.example" | head -n1)"
export INVESTIGATORS_EXTRA_LIVE_DSNS
uv run pytest -q -n "${WORKERS}" --dist loadfile -m integration
