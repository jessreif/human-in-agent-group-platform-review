#!/usr/bin/env bash
# Disposable API + worker + PostgreSQL for the opt-in participant browser scenarios.
# Not used by scripts/check.sh. Does not read the live .env. Does not print DSNs,
# passwords, or preview tokens.
#
#   frontend/e2e/stack.sh start
#   frontend/e2e/stack.sh stop
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"
STATE="${HERE}/.stack-state"
ENTRIES="${HERE}/.entries"
PORT_FILE="${HERE}/.api-port"
INDEX_FILE="${HERE}/.entry-index"

kill_tree() {
  local pid="$1" child
  [[ -n "${pid}" ]] || return 0
  for child in $(ps -o pid= --ppid "${pid}" 2>/dev/null); do
    kill_tree "${child}"
  done
  kill "${pid}" >/dev/null 2>&1 || true
}

stop_stack() {
  if [[ -f "${STATE}" ]]; then
    # shellcheck disable=SC1090
    source "${STATE}"
    kill_tree "${pid_api:-}"
    kill_tree "${pid_worker:-}"
    kill_tree "${pid_vite:-}"
    kill_tree "${pid_proxy:-}"
    if [[ -n "${container:-}" ]]; then docker rm -f "${container}" >/dev/null 2>&1 || true; fi
    if [[ -n "${tmpdir:-}" && -d "${tmpdir}" ]]; then rm -rf "${tmpdir}"; fi
  fi
  rm -f "${STATE}" "${ENTRIES}" "${PORT_FILE}" "${INDEX_FILE}" "${HERE}/.cut-port" "${HERE}/.cut-status"
}

if [[ "${1:-}" == "stop" ]]; then
  stop_stack
  exit 0
fi
if [[ "${1:-}" == "telemetry" ]]; then
  [[ -f "${STATE}" ]] || { echo "e2e stack is not up" >&2; exit 1; }
  # shellcheck disable=SC1090
  source "${STATE}"
  # Kinds, record ids, and the share "via" field only. No payloads, no DSN.
  # The newest session only. Every preview entry is the same world, so an
  # earlier session's view would otherwise satisfy a later scenario.
  docker exec "${container}" psql -q -t -A -h 127.0.0.1 -U investigators_user -d investigators_e2e_db \
    -c "SELECT type || ' ' || COALESCE(payload->'data'->>'record_id','') || ' ' || COALESCE(payload->'data'->>'via','') FROM app.event WHERE session_id = (SELECT session_id FROM app.event ORDER BY server_time DESC, seq DESC LIMIT 1) AND (type LIKE 'telemetry_%' OR type = 'session_opened') ORDER BY seq;"
  exit 0
fi
if [[ "${1:-}" != "start" ]]; then
  echo "usage: stack.sh start|stop" >&2
  exit 2
fi

stop_stack

PG_IMAGE="$(python3 -c '
import re, pathlib, sys
text = pathlib.Path(sys.argv[1]).read_text()
match = re.search(r"postgres:15-alpine@sha256:[0-9a-f]+", text)
if not match:
    raise SystemExit("postgres image pin not found in scripts/test_backend.sh")
print(match.group(0))
' "${ROOT}/scripts/test_backend.sh")"

container="investigators-testdb-$$-e2e-${RANDOM}"
pass="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
migrator_pass="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
runtime_pass="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
preview_key="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
tmpdir="$(mktemp -d)"
chmod 700 "${tmpdir}"
# Build while postgres starts. The browser scenarios serve this bundle, not
# the dev server (see e2e/dev_server.sh).
(
  cd "${ROOT}/frontend"
  npm run build
) >"${tmpdir}/build.log" 2>&1 &
pid_build=$!

redact() {
  sed -e "s/${pass}/[redacted]/g" \
      -e "s/${migrator_pass}/[redacted]/g" \
      -e "s/${runtime_pass}/[redacted]/g" \
      -e "s/${preview_key}/[redacted]/g"
}

fail() {
  echo "e2e stack: $1" >&2
  kill_tree "${pid_api:-}"
  kill_tree "${pid_worker:-}"
  kill_tree "${pid_vite:-}"
  kill_tree "${pid_proxy:-}"
  if [[ -f "${tmpdir}/bootstrap.log" ]]; then
    echo "--- bootstrap (redacted) ---" >&2
    redact < "${tmpdir}/bootstrap.log" | tail -n 30 >&2 || true
  fi
  if [[ -f "${tmpdir}/migrate.log" ]]; then
    echo "--- migrate (redacted) ---" >&2
    redact < "${tmpdir}/migrate.log" | tail -n 30 >&2 || true
  fi
  if [[ -f "${tmpdir}/api.log" ]]; then
    echo "--- api (redacted) ---" >&2
    redact < "${tmpdir}/api.log" | tail -n 30 >&2 || true
  fi
  if [[ -f "${tmpdir}/build.log" ]]; then
    echo "--- frontend build ---" >&2
    tail -n 30 "${tmpdir}/build.log" >&2 || true
  fi
  if [[ -f "${tmpdir}/proxy.log" ]]; then
    echo "--- cut proxy ---" >&2
    tail -n 20 "${tmpdir}/proxy.log" >&2 || true
  fi
  if [[ -n "${pid_build:-}" ]]; then kill_tree "${pid_build}"; fi
  docker rm -f "${container}" >/dev/null 2>&1 || true
  rm -rf "${tmpdir}"
  rm -f "${STATE}" "${ENTRIES}" "${PORT_FILE}" "${INDEX_FILE}" "${HERE}/.cut-port" "${HERE}/.cut-status"
  exit 1
}

docker run -d --name "${container}" --label investigators.disposable=true \
  -e POSTGRES_USER=investigators_e2e -e POSTGRES_PASSWORD="${pass}" \
  -e POSTGRES_DB=investigators_e2e_db \
  --tmpfs /var/lib/postgresql/data:rw -p 127.0.0.1::5432 "${PG_IMAGE}" >/dev/null \
  || fail "could not start disposable postgres"

ready=0
for _ in $(seq 1 60); do
  if docker exec "${container}" pg_isready -h 127.0.0.1 -U investigators_e2e -d investigators_e2e_db >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
[[ "${ready}" == 1 ]] || fail "disposable postgres did not become ready"
pg_port="$(docker port "${container}" 5432/tcp | head -n1 | awk -F: '{print $NF}')"

set +e
sed -e '/^\\password/d' -e '/^\\du/d' -e '/^\\l /d' -e '/^\\dn/d' -e '/^\\ddp/d' \
  -e '/^CREATE DATABASE/d' -e 's/investigators_db/investigators_e2e_db/g' \
  "${ROOT}/scripts/db/inv0301_bootstrap.sql" \
  | docker exec -i "${container}" psql -q -v ON_ERROR_STOP=1 -U investigators_e2e -d investigators_e2e_db \
  >"${tmpdir}/bootstrap.log" 2>&1
boot_code=$?
printf "ALTER ROLE investigators_migrator PASSWORD '%s';\nALTER ROLE investigators_user PASSWORD '%s';\nALTER DATABASE investigators_e2e_db OWNER TO investigators_migrator;\n" \
  "${migrator_pass}" "${runtime_pass}" \
  | docker exec -i "${container}" psql -q -v ON_ERROR_STOP=1 -U investigators_e2e -d investigators_e2e_db \
  >>"${tmpdir}/bootstrap.log" 2>&1
role_code=$?
set -e
[[ "${boot_code}" == 0 && "${role_code}" == 0 ]] || fail "bootstrap SQL did not apply"

(
  cd "${ROOT}/backend"
  unset INVESTIGATORS_ENV_FILE INVESTIGATORS_TEST_DATABASE_URL || true
  export APP_MODE=preview
  export INVESTIGATORS_MIGRATION_DATABASE_URL="postgresql://investigators_migrator:${migrator_pass}@127.0.0.1:${pg_port}/investigators_e2e_db"
  uv run alembic upgrade head
) >"${tmpdir}/migrate.log" 2>&1 || fail "alembic upgrade failed"

api_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
runtime_dsn="postgresql://investigators_user:${runtime_pass}@127.0.0.1:${pg_port}/investigators_e2e_db"

(
  cd "${ROOT}/backend"
  unset INVESTIGATORS_ENV_FILE INVESTIGATORS_TEST_DATABASE_URL INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS || true
  export APP_MODE=preview
  export DATABASE_URL="${runtime_dsn}"
  export PREVIEW_SIGNING_KEY="${preview_key}"
  export MODEL_PROVIDER=none
  export LOG_LEVEL=INFO
  exec uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port "${api_port}" --no-access-log
) >"${tmpdir}/api.log" 2>&1 &
pid_api=$!

(
  cd "${ROOT}/backend"
  unset INVESTIGATORS_ENV_FILE INVESTIGATORS_TEST_DATABASE_URL INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS || true
  export APP_MODE=preview
  export DATABASE_URL="${runtime_dsn}"
  export PREVIEW_SIGNING_KEY="${preview_key}"
  export MODEL_PROVIDER=none
  export LOG_LEVEL=INFO
  exec uv run python -m app.workers.main
) >"${tmpdir}/worker.log" 2>&1 &
pid_worker=$!

# Vite proxies straight at the API. The cut proxy sits in front of Vite so
# closing its sockets resets the browser's event stream. Vite itself does not
# pass an upstream reset through to a response that is already streaming.
printf '%s\n' "${api_port}" > "${PORT_FILE}"
wait "${pid_build}" || fail "frontend build failed"
pid_build=
(
  cd "${ROOT}/frontend"
  exec bash e2e/dev_server.sh
) >"${tmpdir}/vite.log" 2>&1 &
pid_vite=$!

rm -f "${HERE}/.cut-port"
python3 "${HERE}/cut_proxy.py" 4180 4179 "${HERE}/.cut-port" >"${tmpdir}/proxy.log" 2>&1 &
pid_proxy=$!
proxy_control=
for _ in $(seq 1 50); do
  if [[ -s "${HERE}/.cut-port" ]]; then
    proxy_control="$(tr -d '[:space:]' < "${HERE}/.cut-port")"
    break
  fi
  if ! kill -0 "${pid_proxy}" >/dev/null 2>&1; then
    fail "cut proxy exited"
  fi
  sleep 0.1
done
[[ "${proxy_control}" =~ ^[0-9]+$ ]] || fail "cut proxy did not listen"

umask 077
cat > "${STATE}" <<EOF
container=${container}
pid_api=${pid_api}
pid_worker=${pid_worker}
pid_vite=${pid_vite}
pid_proxy=${pid_proxy}
proxy_control=${proxy_control}
tmpdir=${tmpdir}
EOF

up=0
for _ in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:${api_port}/ready" >/dev/null 2>&1; then
    up=1
    break
  fi
  if ! kill -0 "${pid_api}" >/dev/null 2>&1; then
    fail "api process exited before ready"
  fi
  sleep 0.5
done
[[ "${up}" == 1 ]] || fail "api did not become ready"

(
  cd "${ROOT}/backend"
  unset INVESTIGATORS_ENV_FILE || true
  export PREVIEW_SIGNING_KEY="${preview_key}"
  export PYTHONPATH="${ROOT}/backend"
  uv run python "${HERE}/mint_preview.py" "${ENTRIES}"
) >>"${tmpdir}/migrate.log" 2>&1 || fail "could not mint preview entry paths"

ui=0
for _ in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:4180/investigators/" >/dev/null 2>&1; then
    ui=1
    break
  fi
  if ! kill -0 "${pid_vite}" >/dev/null 2>&1; then
    echo "--- vite (redacted) ---" >&2
    redact < "${tmpdir}/vite.log" | tail -n 30 >&2 || true
    fail "vite exited before it was ready"
  fi
  sleep 0.5
done
[[ "${ui}" == 1 ]] || fail "vite did not become ready"

echo "e2e stack ready"
