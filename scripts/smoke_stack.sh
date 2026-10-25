#!/usr/bin/env bash
# Boot the built images against DISPOSABLE resources only (WP01 exit criterion):
#   ephemeral PostgreSQL (tmpfs) + api + worker + ui on a private Docker network, published on
#   random loopback ports. Never binds 8518/8519, never reads the real .env, never touches the
#   host PostgreSQL cluster. Everything is removed on exit.
# Usage: scripts/smoke_stack.sh [--no-build]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
PG_IMAGE="postgres:15-alpine@sha256:f7d23353e1b15400d22ebe31189f4d314b87a4c129cc400c8c2d8d4ca127bf81"
[[ "${1:-}" == "--no-build" ]] || scripts/build_images.sh
EXPECTED_SHA="$(python3 scripts/source_bundle.py)"

SUFFIX="$$-${RANDOM}"
NET="investigators-smoke-${SUFFIX}"
PG="investigators-smoke-db-${SUFFIX}"
API="investigators-smoke-api-${SUFFIX}"
WORKER="investigators-smoke-worker-${SUFFIX}"
UI="investigators-smoke-ui-${SUFFIX}"
PASS="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
ADMIN_PASS="$(python3 -c 'import secrets; print(secrets.token_hex(12))')"
FAILURES=0

cleanup() {
  docker rm -f "${API}" "${WORKER}" "${UI}" "${PG}" >/dev/null 2>&1 || true
  docker network rm "${NET}" >/dev/null 2>&1 || true
}
trap cleanup EXIT
check() { # check <description> <command...>
  local description="$1"; shift
  if "$@"; then echo "PASS ${description}"; else echo "FAIL ${description}"; FAILURES=$((FAILURES + 1)); fi
}
HARDEN=(--read-only --tmpfs /tmp:size=16m --security-opt no-new-privileges:true --cap-drop ALL)

docker network create --label investigators.disposable=true "${NET}" >/dev/null
docker run -d --name "${PG}" --network "${NET}" --label investigators.disposable=true \
  -e POSTGRES_USER=investigators_smoke -e POSTGRES_PASSWORD="${PASS}" -e POSTGRES_DB=investigators_smoke_test \
  --tmpfs /var/lib/postgresql/data:rw "${PG_IMAGE}" >/dev/null
for _ in $(seq 1 60); do
  docker exec "${PG}" pg_isready -h 127.0.0.1 -U investigators_smoke -d investigators_smoke_test >/dev/null 2>&1 && break
  sleep 1
done

# INV-0301 layout from the operator script itself, then the image's own migrations as the
# migrator role; api/worker then run as the runtime role, exactly as in production.
MIGRATOR_PASS="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
RUNTIME_PASS="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
PSQL=(docker exec -i "${PG}" psql -q -v ON_ERROR_STOP=1 -U investigators_smoke -d investigators_smoke_test)
set +e
sed -e '/^\\password/d' -e '/^\\du/d' -e '/^\\l /d' -e '/^\\dn/d' -e '/^\\ddp/d' \
  -e '/^CREATE DATABASE/d' -e 's/investigators_db/investigators_smoke_test/g' \
  scripts/db/inv0301_bootstrap.sql | "${PSQL[@]}" >/dev/null 2>&1
BOOT_CODE=$?
printf "ALTER ROLE investigators_migrator PASSWORD '%s';\nALTER ROLE investigators_user PASSWORD '%s';\nALTER DATABASE investigators_smoke_test OWNER TO investigators_migrator;\n" \
  "${MIGRATOR_PASS}" "${RUNTIME_PASS}" | "${PSQL[@]}" >/dev/null 2>&1
ROLE_CODE=$?
MIGRATE_OUT="$(docker run --rm --network "${NET}" "${HARDEN[@]}" \
  -e "INVESTIGATORS_MIGRATION_DATABASE_URL=postgresql://investigators_migrator:${MIGRATOR_PASS}@${PG}:5432/investigators_smoke_test" \
  investigators-backend:local alembic upgrade head 2>&1)"
MIGRATE_CODE=$?
set -e
echo "--- database"
check "bootstrap SQL (scripts/db/inv0301_bootstrap.sql) applies" test "${BOOT_CODE}" -eq 0 -a "${ROLE_CODE}" -eq 0
check "image migrations apply as investigators_migrator" test "${MIGRATE_CODE}" -eq 0
check "migration output has no password" bash -c "! printf '%s' \"\$0\" | grep -q '${MIGRATOR_PASS}'" "${MIGRATE_OUT}"

DSN="postgresql://investigators_user:${RUNTIME_PASS}@${PG}:5432/investigators_smoke_test"
ENV_ARGS=(-e APP_MODE=preview -e "DATABASE_URL=${DSN}" -e "ADMIN_PASSWORD=${ADMIN_PASS}"
  -e ADMIN_EMAILS=admin1@university.edu,admin2@university.edu -e LOG_LEVEL=INFO)

docker run -d --name "${API}" --network "${NET}" "${HARDEN[@]}" "${ENV_ARGS[@]}" \
  -p 127.0.0.1::8000 investigators-backend:local >/dev/null
docker run -d --name "${WORKER}" --network "${NET}" "${HARDEN[@]}" "${ENV_ARGS[@]}" \
  investigators-backend:local python -m app.workers.main >/dev/null
docker run -d --name "${UI}" "${HARDEN[@]}" -p 127.0.0.1::8080 investigators-ui:local >/dev/null
API_PORT="$(docker port "${API}" 8000/tcp | head -n1 | awk -F: '{print $NF}')"
UI_PORT="$(docker port "${UI}" 8080/tcp | head -n1 | awk -F: '{print $NF}')"
A="http://127.0.0.1:${API_PORT}"
U="http://127.0.0.1:${UI_PORT}"
for _ in $(seq 1 30); do curl -sf "${A}/health" >/dev/null && break; sleep 1; done
for _ in $(seq 1 30); do curl -sf "${U}/healthz" >/dev/null && break; sleep 1; done
sleep 2

echo "--- api (${A})"
# The migration head is derived from the image itself, never hardcoded: a literal revision
# here goes stale the moment a new migration ships (audit BA-02).
EXPECTED_HEAD="$(docker run --rm "${HARDEN[@]}" investigators-backend:local \
  python -c 'from app.db.engine import expected_schema_head; print(expected_schema_head() or "")')"
check "image reports a single migration head" test -n "${EXPECTED_HEAD}"
check "api /health 200" bash -c "curl -sf '${A}/health' | grep -q '\"status\":\"ok\"'"
check "api /ready 200 at migration head (${EXPECTED_HEAD}) as runtime role" bash -c "curl -sf '${A}/ready' | grep -q '\"current\":\"${EXPECTED_HEAD}\"'"
check "api /version carries source sha ${EXPECTED_SHA:0:12}" bash -c "curl -sf '${A}/version' | grep -q '${EXPECTED_SHA}'"
check "api X-Request-ID header" bash -c "curl -si '${A}/health' | grep -qi '^x-request-id:'"
check "api /metrics direct 200" bash -c "curl -sf '${A}/metrics' | grep -q investigators_build_info"
check "api /metrics proxied 404" bash -c "[ \"\$(curl -s -o /dev/null -w '%{http_code}' -H 'X-Forwarded-For: 1.2.3.4' '${A}/metrics')\" = 404 ]"
check "api docs disabled" bash -c "[ \"\$(curl -s -o /dev/null -w '%{http_code}' '${A}/docs')\" = 404 ]"
check "api container healthcheck" docker exec "${API}" python -m app.healthcheck

echo "--- worker"
check "worker heartbeat healthcheck" docker exec "${WORKER}" python -m app.workers.healthcheck
check "worker sees database" bash -c "docker exec '${WORKER}' cat /tmp/investigators-worker-heartbeat.json | grep -q '\"database_reachable\": true'"

echo "--- ui (${U})"
INDEX="$(curl -sf "${U}/")"
ASSET="$(printf '%s' "${INDEX}" | grep -o '/investigators/assets/[^"]*\.js' | head -n1)"
check "ui index references /investigators/assets" test -n "${ASSET}"
check "ui asset via stripped path ${ASSET#/investigators}" curl -sf -o /dev/null "${U}${ASSET#/investigators}"
check "ui asset via prefixed path" curl -sf -o /dev/null "${U}${ASSET}"
check "ui SPA fallback" bash -c "curl -sf '${U}/some/deep/link' | grep -q 'id=\"root\"'"
check "ui version.json carries source sha" bash -c "curl -sf '${U}/version.json' | grep -q '${EXPECTED_SHA}'"
check "ui nosniff header" bash -c "curl -sI '${U}/' | grep -qi 'x-content-type-options: nosniff'"
TMP_DIST="$(mktemp -d)"
docker cp "${UI}:/usr/share/nginx/html/." "${TMP_DIST}/" >/dev/null
check "ui image bundle leakage scan" python3 scripts/check_bundle_leakage.py "${TMP_DIST}"
rm -rf "${TMP_DIST}"

echo "--- failure and secrecy behavior"
set +e
BAD_OUT="$(docker run --rm "${HARDEN[@]}" -e ADMIN_PASSWORD=should-not-appear investigators-backend:local 2>&1)"
BAD_CODE=$?
set -e
check "api exits 2 on missing configuration" test "${BAD_CODE}" -eq 2
check "config failure names fields" bash -c "printf '%s' \"\$0\" | grep -q 'APP_MODE \[missing\]'" "${BAD_OUT}"
check "config failure has no traceback" bash -c "! printf '%s' \"\$0\" | grep -q Traceback" "${BAD_OUT}"
check "config failure does not echo secrets" bash -c "! printf '%s' \"\$0\" | grep -q should-not-appear" "${BAD_OUT}"
LOGS="$(docker logs "${API}" 2>&1; docker logs "${WORKER}" 2>&1)"
check "no DB password in api/worker logs" bash -c "! printf '%s' \"\$0\" | grep -qE '${PASS}|${RUNTIME_PASS}|${MIGRATOR_PASS}'" "${LOGS}"
check "no admin password in api/worker logs" bash -c "! printf '%s' \"\$0\" | grep -q '${ADMIN_PASS}'" "${LOGS}"
check "logs are JSON lines" bash -c "printf '%s\n' \"\$0\" | head -n 3 | python3 -c 'import json,sys; [json.loads(l) for l in sys.stdin if l.strip()]'" "${LOGS}"
check "8518/8519 untouched by smoke run" bash -c "! docker ps --format '{{.Ports}}' | grep -E '127\.0\.0\.1:85(18|19)->' | grep -q ."

echo "smoke failures: ${FAILURES}"
exit "$(( FAILURES > 0 ? 1 : 0 ))"
