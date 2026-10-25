#!/usr/bin/env bash
# Production UI for the opt-in browser scenarios. Reads the disposable API port
# and exports it so vite.config.ts proxies /investigators/api. Does not print
# the port.
#
# `vite dev` is the wrong surface here: React StrictMode's development
# remount runs the case file's unmount finalizer, which permanently stops
# view qualification. Participants get the production bundle, where that
# remount does not happen. stack.sh builds dist/ before starting this.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
port="$(tr -d '[:space:]' < e2e/.api-port)"
if ! [[ "${port}" =~ ^[0-9]+$ ]]; then
  echo "e2e api port file is missing or not a port" >&2
  exit 1
fi
if [[ ! -f dist/index.html ]]; then
  echo "e2e preview requires frontend/dist (stack.sh builds it)" >&2
  exit 1
fi
export INVESTIGATORS_E2E_API_PORT="${port}"
exec ./node_modules/.bin/vite preview --host 127.0.0.1 --port 4179 --strictPort
