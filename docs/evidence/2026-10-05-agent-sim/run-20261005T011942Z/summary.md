# Agent-sim run

- stamp: `20261005T011942Z`
- run id: `run-20261005T011942Z`
- label: `adhoc`
- source bundle: `44c72569428b8a73aca31b54d4d39614e8c4f802acde014c28e4ac0bdfa87bbd`
- oracle version: `oracles_v3`
- provider: requested `none`, effective `deterministic-fallback`
- sessions: 1
- findings (fail severity): 0

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| asker | `ccd015f7` | low | completion | — | 51 | 0 |

## Findings

- **WARN** `INV-SCRIPTED-REPEAT` (asker, `ccd015f7`): canonical scripted line delivered 13x
  - evidence: `{"body": "noted"}`
- **WARN** `INV-SCRIPTED-REPEAT` (asker, `ccd015f7`): canonical scripted line delivered 3x
  - evidence: `{"body": "got it"}`
- **INFO** `EXPECT:terminal:completion` (asker, `ccd015f7`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (asker, `ccd015f7`): 27/27 structured ask(s) closed delivered (2 expired inside the dead window)
  - evidence: `{"undelivered": [], "dead_window_expired": ["292f96d1-47cb-4a53-810f-169a64c379c2", "00a11468-bceb-4404-be31-5c3104cfa0c0"]}`
