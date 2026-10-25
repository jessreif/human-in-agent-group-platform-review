# Evaluations

Commands were run on 2026-10-21 against this source tree. The interpreter was Python 3.12.12 from an existing local virtualenv. `uv run` was not the command. Nothing here called the model service, the production database, or the public site.

## Automation disclosure (no model)

Command:

```bash
cd backend && pytest tests/test_adversarial_classifiers.py -q --tb=short
```

Result, re-run the same day during verification: **5 passed in 0.02s.** The counts below are from that re-run. An earlier run the same day printed the same counts in 0.03s.

`is_automation_probe` on `AUTOMATION_CORPUS` (103 rows):

| | Predicted probe | Predicted not a probe |
|---|---|---|
| Labeled probe | tp = 85 | fn = 0 |
| Labeled not a probe | fp = 0 | tn = 18 |

Precision 1.00. Recall 1.00. Per-category agreement was 8/8 boundary, 6/6 capability_test, 11/11 challenge_response, 12/12 collective, 7/7 coordinated, 19/19 direct, 2/2 injection, 1/1 mockery, 9/9 model_specific, 11/11 provenance, 17/17 third_person.

There were no false negatives on this set. The 18 true negatives are the corpus rows marked as expected non-fires. They are the ordinary and near-miss questions included in the set. This is agreement with a researcher-written labeled set, not a holdout sample of new participant messages. **Verified** for that claim only.

`is_intel_ask` on its 55-row corpus in the same run: precision 1.00, recall 1.00, tp=38, fp=0, fn=0, tn=17.

## Demo stamp

```bash
python3 scripts/build_demo_mocks.py --check
```

Result: `OK: 1120 demo mocks current`. **Verified** for “the files match the script.” Not a fidelity check against the platform.

## Seed and fingerprint commands

| Command | Result on 2026-10-21 |
|---|---|
| `python3 scripts/source_bundle.py` | `79d15a5496921a467fb52feca00a64d3738ebd874a11f6704668a565ad314246` |
| `python3 scripts/source_bundle.py --behavior` | `ebc039574c720b91b7a3233c13dd1151c744a41138b42bec2defe24813911c26` |
| `python3 scripts/verify_fingerprints.py --write` | Wrote `docs/FINGERPRINTS.json` including the frozen-reference hash |
| `python3 scripts/check_demo_fidelity.py` | Printed the workload-remark and missing-automation-line gaps and `documented demo gaps only` |
| `python ../scripts/regenerate_task.py` from `backend/` | `aceb6cac85dabceb10d08a91e8860cbcd900949b607cb0e4bb25c186f03ccb9e` and a note that `world_id` is excluded |
| `python ../scripts/reproduce_allocation.py` from `backend/` | `high low` for block 0 of the `tp_ai_v7` manifest hash |
| `python ../scripts/build_materials_docs.py` from `backend/` | Wrote `docs/MATERIALS/` |
| `Table(` count in `schema.py` | 49 |

The Python 3.12 commands used an existing local virtualenv. `uv run` was not the command that produced these lines.

## Unit suite

From `backend/`, with that same virtualenv:

```bash
python -m pytest -m "not integration" -q --tb=line
```

Result: **2098 passed, 482 deselected in 37.04s.** **Verified** for that command. Deselected tests are the integration marker. This pass does not by itself promote every design claim to **Verified**. A claim stays **Implemented** unless the row in `docs/CLAIMS_TO_TESTS.md` names a test this run actually executed for that claim.

## Contract tests

From the repository root, with `PYTHONPATH=backend` and the same interpreter:

```bash
python -m pytest tests/contracts -q --tb=line
```

Result: **2 failed, 126 passed, 1 skipped in 4.28s.**

The skip is `tests/contracts/test_bundle_leakage.py`: `frontend/dist` is not built. The two failures are `tests/contracts/test_records.py`. The design spec on disk hashes to `fea004855976ece73df6fa9bbd4c5a83dd3248593cf8a181e030057ded000dad`. The pin still expects `b158a25801393395e2cfe825a68bd8d84a6a67294435d4144f5514ea470069e3`, which is the production file's hash. Host names in the copy in this repository were replaced before publication, so the pin and `docs/REQUIREMENT_TRACE.md` no longer match. `scripts/build_requirement_trace.py` was not re-run, so the record still shows that the spec text changed. On 2026-10-22 the stdlib-only contract tests were rerun after the documentation was reorganized, and the same two tests were the only failures.

## Not run

| Evaluation | Status | Command, when you have the dependency |
|---|---|---|
| Check detection rates on a fresh adversarial output set | **Requires model** for model outputs. Rule checks can run without a model; they were not given a new labeled set in this pass. | The existing checker tests live under `backend/tests/`. |
| Fallback rates by check and by condition | Not computed. Would need session exports or a harness run. | Do not invent rates from the anonymized exports without a script. |
| All simulated-participant strategies | Not run. Needs Postgres. | `scripts/run_agent_sim.sh` or `python -m harness.cli --provider none` |
| LLM coding rubric reliability | **Planned.** The coder path is dormant. No reliability statistic exists in this run. | — |
| Coverage, concurrent sessions, latency, GPU memory | Not measured. `tests/load/` has no test. | — |
| Integration tests | Not run. They need a throwaway database. | `scripts/test_integration.sh` |
| Frontend tests | Not run. `node_modules` is not included. | `cd frontend && npm test` |
| Contract tests | Run. See the section above. Two failures are the design-spec pin, not a new logic bug. | `python -m pytest tests/contracts` from the repository root |
