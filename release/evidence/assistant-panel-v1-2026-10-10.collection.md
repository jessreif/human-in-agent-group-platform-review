# assistant-panel-v1-2026-10-10 — collection record (sealed 2026-10-12, BA-108)

## What this bundle is

Acceptance evidence for `assistant_release_panel_v1`, collected 2026-10-10 on
build `1039f9cb…` with pinned model
`gemma4:26b@sha256:5571076f…d251` (20 rows: `tp_ai_v6` + `tp_scripted_v8` ×
high/low × 1 seed × 5 probes).

## The defect this seal repairs

`release/assistant-panel-v1.json` was edited in place after collection
(v6/v8 → v7/v9 profile pins), so the documented checker could no longer
validate the archived bundle. On 2026-10-12 the panel was restored to its
collection-time contents and sealed:

- `release/assistant-panel-v1.json` — restored immutable panel (never edit;
  successor panels get a new version).
- `assistant-panel-v1-2026-10-10.panel.json` (this directory) — the sealed
  copy the checker resolves first for this bundle.
- `release/assistant-panel-v2.json` — the AI-only successor panel
  (`tp_ai_v13`), matching the fielded 1×2 design. New collections run against
  v2.

The bundle predates the `panel_sha256` evidence field; the checker enforces
the pin for bundles that carry it (the collector now emits it).

## Collector restoration

The original driver `scripts/assistant_panel.py` was absent; it has been
restored as a service-path collector (same command handlers, scheduler,
validator, and durable-row harvest as production — driving over the real
`assistant_request`/`advance_due`/telemetry-receipt code paths against a
disposable PostgreSQL, not a mocked surface). Invocation:

```bash
cd backend && INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS=1 \
  uv run python ../scripts/assistant_panel.py collect \
    --panel ../release/assistant-panel-v2.json \
    --out ../release/evidence/assistant-panel-v2-<date>.json \
    --provider ollama --auto-adjudicate --regressions-ok
```

- `--provider ollama` requires the real local-adapter env
  (`MODEL_PROVIDER` + `LOCAL_MODEL_*`). `stub`/`none` exercise the pipeline
  but cannot produce a passing generated-route row — the claim checker
  honestly rejects them, which is the intended behavior (synthetic providers
  never stand in for a real-provider acceptance run).
- The collector never fabricates outcomes: failed generation, fallback
  delivery, or a missing render receipt is recorded as-is and fails the gate.

## Verify

```bash
uv run --project backend python scripts/check_assistant_release.py \
  release/evidence/assistant-panel-v1-2026-10-10.json
# resolves assistant-panel-v1-2026-10-10.panel.json → passed: true
```

Regression coverage for the seal lives in
`backend/tests/test_assistant_audit_remediation.py::test_sealed_v1_evidence_validates_only_against_its_own_panel`.
