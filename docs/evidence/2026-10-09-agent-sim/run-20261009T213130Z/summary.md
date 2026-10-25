# Agent-sim run

- stamp: `20261009T213130Z`
- run id: `run-20261009T213130Z`
- label: `v5-verify`
- source bundle: `9226e2a4db0fcb873686dce27e766c671a7ff19046ccaca5b1e20e4b62350da7`
- oracle version: `oracles_v3`
- provider: requested `none`, effective `deterministic-fallback`
- sessions: 2
- findings (fail severity): 0

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| normal | `9ca8d346` | low | completion | — | 26 | 0 |
| asker | `aebf5296` | high | completion | — | 44 | 0 |

## Findings

- **INFO** `EXPECT:terminal:completion` (normal, `9ca8d346`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:no_orphaned` (normal, `9ca8d346`): 0 request episodes still pending
- **WARN** `INV-SCRIPTED-REPEAT` (asker, `aebf5296`): canonical scripted line delivered 4x
  - evidence: `{"body": "makes sense"}`
- **WARN** `INV-SCRIPTED-REPEAT` (asker, `aebf5296`): canonical scripted line delivered 4x
  - evidence: `{"body": "noted"}`
- **INFO** `EXPECT:terminal:completion` (asker, `aebf5296`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (asker, `aebf5296`): not applicable — nl_only profile refuses structured asks
