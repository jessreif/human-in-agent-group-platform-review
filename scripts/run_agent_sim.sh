#!/usr/bin/env bash
# Agent-participant simulation runs (harness/*):
#   Provisions an ephemeral PostgreSQL container (tmpfs, random password/port,
#   removed on exit) — the same disposable shape as test_backend.sh — then runs
#   python -m harness.cli with the given arguments. The destructive-test guard
#   still proves the DSN differs from the live runtime DSN (read from ../.env).
#
# Examples:
#   scripts/run_agent_sim.sh --manifest tp_demo_v5 --personas normal,asker,idler
#   scripts/run_agent_sim.sh --manifest tp_demo_v5 --personas prober --seed 7
#   AGENT_SIM_PROVIDER=stub scripts/run_agent_sim.sh --personas normal
#
# Live-model runs need the worker's network namespace (Ollama is loopback-only
# there); use --provider ollama only via docker exec on investigators-worker.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}/backend"

uv sync --frozen --quiet

export INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS=1
export INVESTIGATORS_LIVE_ENV_FILE="${ROOT}/.env"
INVESTIGATORS_EXTRA_LIVE_DSNS="$(sed -n 's/^DATABASE_URL=//p' "${ROOT}/.env.example" | head -n1)"
export INVESTIGATORS_EXTRA_LIVE_DSNS

# BA-87: an explicit --dsn selects its own (still guard-checked) target — do not
# spawn an unused ephemeral container for it.
HAS_DSN_ARG=0
for arg in "$@"; do
  case "$arg" in --dsn|--dsn=*) HAS_DSN_ARG=1 ;; esac
done

# --out is repo-root-relative: the CLI runs from backend/, so a relative value
# would otherwise write evidence under backend/docs/ (inside the source bundle,
# moving the bundle hash every run). Rewrite it to an absolute path.
ARGS=()
SKIP_NEXT=0
for arg in "$@"; do
  if [ "${SKIP_NEXT}" = 1 ]; then
    case "$arg" in /*) ;; *) arg="${ROOT}/${arg}" ;; esac
    ARGS+=("${arg}")
    SKIP_NEXT=0
    continue
  fi
  case "$arg" in
    --out) ARGS+=("${arg}"); SKIP_NEXT=1 ;;
    --out=/*) ARGS+=("${arg}") ;;
    --out=*) ARGS+=("--out=${ROOT}/${arg#--out=}") ;;
    *) ARGS+=("${arg}") ;;
  esac
done
set -- "${ARGS[@]}"

if [ -z "${INVESTIGATORS_TEST_DATABASE_URL:-}" ] && [ "${HAS_DSN_ARG}" = 0 ]; then
  PG_IMAGE="${INVESTIGATORS_TEST_PG_IMAGE:-postgres:15-alpine@sha256:f7d23353e1b15400d22ebe31189f4d314b87a4c129cc400c8c2d8d4ca127bf81}"
  NAME="investigators-simdb-$$-${RANDOM}"
  PASS="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
  cleanup() { docker rm -f "${NAME}" >/dev/null 2>&1 || true; }
  trap cleanup EXIT
  docker run -d --rm --name "${NAME}" --label investigators.disposable=true \
    -e POSTGRES_USER=investigators_test -e POSTGRES_PASSWORD="${PASS}" \
    -e POSTGRES_DB=investigators_test_db \
    --tmpfs /var/lib/postgresql/data:rw -p 127.0.0.1::5432 "${PG_IMAGE}" >/dev/null
  PORT="$(docker port "${NAME}" 5432/tcp | head -n1 | awk -F: '{print $NF}')"
  for _ in $(seq 1 60); do
    if docker exec "${NAME}" pg_isready -h 127.0.0.1 -U investigators_test -d investigators_test_db >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  export INVESTIGATORS_TEST_DATABASE_URL="postgresql://investigators_test:${PASS}@127.0.0.1:${PORT}/investigators_test_db"
fi

uv run python -m harness.cli "$@"
