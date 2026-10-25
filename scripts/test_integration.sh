#!/usr/bin/env bash
# Focused integration run for development: ONE ephemeral PostgreSQL container
# (tmpfs, random password, random loopback port, removed on exit) and only the
# pytest paths/args you pass. Same isolation guarantees as scripts/test_backend.sh
# — never touches the host cluster or the live database — but no lint/unit
# phase and no xdist. The release gate is still scripts/check.sh.
#
#   scripts/test_integration.sh tests/integration/test_completion_targets.py -k target
#
# Run tests/test_wp11_integration.py on its own: it re-imports the integration
# conftest fixtures at tests/ level (see the F811 note in backend/pyproject.toml),
# so collecting it in the same non-xdist process as tests/integration/* makes
# the later module's fixtures error at setup. The xdist gate is unaffected.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PG_IMAGE="${INVESTIGATORS_TEST_PG_IMAGE:-postgres:15-alpine@sha256:f7d23353e1b15400d22ebe31189f4d314b87a4c129cc400c8c2d8d4ca127bf81}"
if [[ $# -eq 0 ]]; then
  echo "usage: $0 <pytest paths/args...>" >&2
  exit 2
fi
cd "${ROOT}/backend"

name="investigators-testdb-focus-$$-${RANDOM}"
pass="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
trap 'docker rm -f "${name}" >/dev/null 2>&1 || true' EXIT
docker run -d --rm --name "${name}" --label investigators.disposable=true \
  -e POSTGRES_USER=investigators_test -e POSTGRES_PASSWORD="${pass}" \
  -e POSTGRES_DB=investigators_test_db \
  --tmpfs /var/lib/postgresql/data:rw -p 127.0.0.1::5432 "${PG_IMAGE}" >/dev/null
for _ in $(seq 1 60); do
  docker exec "${name}" pg_isready -h 127.0.0.1 -U investigators_test -d investigators_test_db \
    >/dev/null 2>&1 && break
  sleep 1
done
port="$(docker port "${name}" 5432/tcp | head -n1 | awk -F: '{print $NF}')"
export INVESTIGATORS_TEST_DATABASE_URL="postgresql://investigators_test:${pass}@127.0.0.1:${port}/investigators_test_db"
export INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS=1
export INVESTIGATORS_REQUIRE_INTEGRATION=1
export INVESTIGATORS_LIVE_ENV_FILE="${ROOT}/.env"
INVESTIGATORS_EXTRA_LIVE_DSNS="$(sed -n 's/^DATABASE_URL=//p' "${ROOT}/.env.example" | head -n1)"
export INVESTIGATORS_EXTRA_LIVE_DSNS
uv run pytest -q -p no:cacheprovider -m integration "$@"
