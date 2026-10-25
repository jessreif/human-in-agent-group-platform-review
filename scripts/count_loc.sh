#!/usr/bin/env bash
# count_loc.sh — count lines of code in the investigators tree.
#
# Counts real lines in source files, grouped by language, excluding
# generated/vendored trees (node_modules, dist, .venv, __pycache__,
# docs/evidence, docs/releases) and lockfiles. Prints a per-language table and
# a TOTAL line — the number appended to README.md.
#
# Usage: scripts/count_loc.sh            # table
#        scripts/count_loc.sh --total    # just the number (for README updates)
set -euo pipefail
cd "$(dirname "$0")/.."

total=0
count() { # $1 = label, rest = find -name expr
  local label="$1"; shift
  local n
  n=$(find . -type f \( "$@" \) \
        -not -path '*/node_modules/*' \
        -not -path './frontend/dist/*' \
        -not -path '*/.venv/*' \
        -not -path './docs/evidence/*' \
        -not -path './docs/releases/*' \
        -not -path '*/__pycache__/*' \
        -not -name '*.lock' \
        -not -name 'package-lock.json' \
        -not -name 'uv.lock' \
        -exec cat {} + | wc -l)
  printf '%-12s %8d\n' "$label" "$n"
  total=$((total + n))
}

count "Python"     -name '*.py'
count "TypeScript" -name '*.ts' -o -name '*.tsx'
count "JavaScript" -name '*.js' -o -name '*.mjs'
count "CSS/HTML"   -name '*.css' -o -name '*.html'
count "SQL"        -name '*.sql'
count "Shell"      -name '*.sh'
count "JSON"       -name '*.json'
count "Markdown"   -name '*.md'

printf '%-12s %8d\n' TOTAL "$total"
if [ "${1:-}" = "--total" ]; then echo "$total"; fi
