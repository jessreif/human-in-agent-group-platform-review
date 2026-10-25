#!/usr/bin/env bash
# Profile the unit suites without replacing scripts/check.sh.
# Writes under docs/evidence/test-suite-plan/<utc>/. Not called by the gate.
#
#   scripts/profile_tests.sh
#
# Exits non-zero when either unit suite does. Does not start PostgreSQL and
# does not print DSNs, passwords, or cookie values.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

UTC="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${ROOT}/docs/evidence/test-suite-plan/${UTC}"
mkdir -p "${OUT}"

SEED="${INVESTIGATORS_PROFILE_SEED:-137}"
CHECKSUM="$(python3 scripts/source_bundle.py)"
UV_VER="$(uv --version 2>/dev/null || echo "uv-unavailable")"
NODE_VER="$(node -v 2>/dev/null || echo "node-unavailable")"
NPM_VER="$(npm -v 2>/dev/null || echo "npm-unavailable")"

{
  echo "checksum ${CHECKSUM}"
  echo "tools ${UV_VER} | node ${NODE_VER} | npm ${NPM_VER}"
  echo "seed ${SEED}"
  echo "selection frontend unit + pytest -m \"not integration\""
  echo "out ${OUT}"
} | tee "${OUT}/NOTE.txt"

frontend_rc=0
backend_rc=0
frontend_wall=0
backend_wall=0

echo "=== frontend: typecheck lint vitest ==="
fe_start="$(date +%s)"
(
  cd frontend
  npm run -s typecheck
  npm run -s lint
  npx vitest run --reporter=json --outputFile="${OUT}/frontend.json" --sequence.seed="${SEED}"
) >"${OUT}/frontend.log" 2>&1 || frontend_rc=$?
fe_end="$(date +%s)"
frontend_wall=$((fe_end - fe_start))
echo "frontend_rc ${frontend_rc} wall_s ${frontend_wall}" | tee -a "${OUT}/NOTE.txt"

echo "=== backend: pytest -m not integration ==="
be_start="$(date +%s)"
(
  cd backend
  uv run pytest -q -m "not integration" --durations=20 \
    --junitxml="${OUT}/backend-unit.xml"
) >"${OUT}/backend-unit.log" 2>&1 || backend_rc=$?
be_end="$(date +%s)"
backend_wall=$((be_end - be_start))
echo "backend_rc ${backend_rc} wall_s ${backend_wall}" | tee -a "${OUT}/NOTE.txt"

python3 - "${OUT}" "${CHECKSUM}" "${SEED}" "${frontend_rc}" "${frontend_wall}" "${backend_rc}" "${backend_wall}" <<'PY'
import json, sys
from pathlib import Path
import xml.etree.ElementTree as ET

out, checksum, seed, fe_rc, fe_wall, be_rc, be_wall = sys.argv[1:]
outp = Path(out)
lines = [
    "# Profile note",
    "",
    f"- checksum: `{checksum}`",
    "- command: `scripts/profile_tests.sh`",
    '- selection: frontend typecheck, lint, vitest; `pytest -m "not integration"`',
    f"- seed: `{seed}` (vitest sequence seed)",
    f"- frontend exit {fe_rc}, wall {fe_wall} s",
    f"- backend exit {be_rc}, wall {be_wall} s",
    "",
]
fj = outp / "frontend.json"
if fj.exists():
    data = json.loads(fj.read_text())
    lines.append(
        "- frontend collected {n} tests: {p} passed, {f} failed, {s} pending".format(
            n=data.get("numTotalTests"),
            p=data.get("numPassedTests"),
            f=data.get("numFailedTests"),
            s=data.get("numPendingTests"),
        )
    )
bx = outp / "backend-unit.xml"
if bx.exists():
    root = ET.parse(bx).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is not None:
        lines.append(
            "- backend collected {n}: {fails} failed, {err} errors, {skip} skipped".format(
                n=suite.attrib.get("tests"),
                fails=suite.attrib.get("failures"),
                err=suite.attrib.get("errors"),
                skip=suite.attrib.get("skipped"),
            )
        )
        lines.append(f"- backend pytest time attribute: {suite.attrib.get('time')} s")
lines.append("")
lines.append("Phase totals are the two wall times above. pytest does not emit setup/call/teardown sums; `--durations=20` is in `backend-unit.log`.")
lines.append("No database URLs are recorded.")
(outp / "NOTE.md").write_text("\n".join(lines) + "\n")
PY

echo "note ${OUT}/NOTE.md"
if [[ "${frontend_rc}" != 0 || "${backend_rc}" != 0 ]]; then
  echo "profile failed frontend=${frontend_rc} backend=${backend_rc}" >&2
  exit 1
fi
exit 0
