#!/usr/bin/env bash
# All automated checks for the Investigators source tree (INV-0107). There is no CI server on
# this host; this script IS the gate. Exit status is non-zero if any step fails.
#
#   scripts/check.sh                 # source checks: backend, frontend, leakage, contracts, config
#   scripts/check.sh --with-images   # + build images and run scripts/smoke_stack.sh
#   scripts/check.sh --log DIR       # also tee the full output to DIR/check-<utc>.log
#
# Deterministic: dependencies come only from backend/uv.lock and frontend/package-lock.json,
# tests use a virtual clock, provider doubles and an ephemeral database.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
WITH_IMAGES=0
LOG_DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-images) WITH_IMAGES=1 ;;
    --log) LOG_DIR="$2"; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
if [[ -n "${LOG_DIR}" ]]; then
  mkdir -p "${LOG_DIR}"
  LOG_FILE="${LOG_DIR}/check-$(date -u +%Y%m%dT%H%M%SZ).log"
  exec > >(tee "${LOG_FILE}") 2>&1
fi

declare -a RESULTS=()
step() { # step <name> <command...>
  local name="$1"; shift
  echo; echo "=== ${name} ==="
  if "$@"; then RESULTS+=("PASS  ${name}"); else RESULTS+=("FAIL  ${name}"); fi
}

echo "investigators check run $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "source_sha256 $(python3 scripts/source_bundle.py)"
echo "tools: $(uv --version) | node $(node -v) | npm $(npm -v) | $(docker --version)"

step "pinned inputs (docs/baseline/SOURCE_CHECKSUMS.sha256)" \
  sha256sum --quiet -c docs/baseline/SOURCE_CHECKSUMS.sha256
step "backend: format, lint, types, unit + disposable-PostgreSQL integration tests" \
  scripts/test_backend.sh
step "content: manifests bind, publication immutable, arm/profile diffs, generated docs current" \
  bash -c "cd backend && uv run --frozen python -m app.domain.manifests.cli check"
step "frontend: npm ci" bash -c "cd frontend && npm ci --no-audit --no-fund --silent"
step "frontend: typecheck" bash -c "cd frontend && npm run -s typecheck"
step "frontend: lint" bash -c "cd frontend && npm run -s lint"
step "frontend: unit tests" bash -c "cd frontend && npm test --silent"
step "frontend: chromium smoke" bash -c "cd frontend && npm run -s test:e2e"
step "frontend: production build" bash -c "cd frontend && npm run -s build"
step "participant bundle leakage scan" python3 scripts/check_bundle_leakage.py frontend/dist
step "repository contracts (compose, env, trace, ledger, fixtures, leakage rules)" \
  bash -c "uv run --project backend --frozen pytest -q -p no:cacheprovider tests/contracts"
step "per-build docs current (click-through mock + technical appendix; plan §17.2 FR-2/FR-3)" \
  bash -c "python3 scripts/build_clickthrough.py --check && python3 scripts/build_demo_mocks.py --check && uv run --project backend --frozen python scripts/build_tech_appendix.py --check"
step "compose file validates" docker compose config --quiet
step "port preflight (8518/8519 free or ours; no 8111/8112)" scripts/preflight_ports.sh
if [[ "${WITH_IMAGES}" == 1 ]]; then
  step "image build + disposable smoke stack" scripts/smoke_stack.sh
fi

echo; echo "=== SUMMARY ==="
failed=0
for line in "${RESULTS[@]}"; do
  echo "${line}"
  [[ "${line}" == FAIL* ]] && failed=1
done
[[ -n "${LOG_DIR}" ]] && echo "log: ${LOG_FILE}"
exit "${failed}"
