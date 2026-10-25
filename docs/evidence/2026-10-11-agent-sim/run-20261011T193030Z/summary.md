# Agent-sim run

- stamp: `20261011T193030Z`
- run id: `run-20261011T193030Z`
- label: `v9-preflight-scripted`
- source bundle: `b5b9f485226e3fffbe5abaae69a08edf89c8d35bc8770a80578bff465d1fd080`
- oracle version: `oracles_v3`
- provider: requested `none`, effective `deterministic-fallback`
- sessions: 3
- findings (fail severity): 0

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| normal | `5eb2f1ef` | low | completion | — | 27 | 0 |
| asker | `2e70eb34` | low | completion | — | 45 | 0 |
| idler | `18e548ba` | high | technical_stop | checkin_incomplete | 33 | 0 |

## Findings

- **INFO** `EXPECT:terminal:completion` (normal, `5eb2f1ef`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:no_orphaned` (normal, `5eb2f1ef`): 0 request episodes still pending
- **INFO** `EXPECT:terminal:completion` (asker, `2e70eb34`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (asker, `2e70eb34`): not applicable — nl_only profile refuses structured asks
- **INFO** `EXPECT:terminal:technical_stop` (idler, `18e548ba`): terminal stage 'technical_stop' (wanted 'technical_stop')
- **INFO** `EXPECT:terminal_reason:checkin_incomplete` (idler, `18e548ba`): terminal reason 'checkin_incomplete' (wanted 'checkin_incomplete')
