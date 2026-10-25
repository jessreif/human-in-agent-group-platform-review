#!/usr/bin/env bash
# Re-verify that 127.0.0.1:8518/8519 are free (or already held by our own containers) before
# `docker compose up` (INV-0106, spec §3.1). Read-only. Exit 1 if another process holds a port.
set -uo pipefail
status=0
for port in 8518 8519; do
  if ss -ltnH "sport = :${port}" | grep -q .; then
    owner="$(docker ps --format '{{.Names}} {{.Ports}}' | grep -E "127\.0\.0\.1:${port}->" | awk '{print $1}')"
    if [[ "${owner}" == investigators-* ]]; then
      echo "port ${port}: held by our container ${owner} (ok for restart)"
    else
      echo "port ${port}: IN USE by another process/container (${owner:-non-docker}); refusing" >&2
      status=1
    fi
  else
    echo "port ${port}: free"
  fi
done
for forbidden in 8111 8112; do
  # Comments may name the forbidden ports; only configuration lines count.
  if grep -v '^[[:space:]]*#' "$(dirname "$0")/../docker-compose.yml" | grep -q "${forbidden}"; then
    echo "docker-compose.yml mentions forbidden port ${forbidden}" >&2
    status=1
  fi
done
exit "${status}"
