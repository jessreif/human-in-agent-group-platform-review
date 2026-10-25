# Agent-sim run

- stamp: `20261011T193018Z`
- run id: `run-20261011T193018Z`
- label: `v9-preflight-ai`
- source bundle: `b5b9f485226e3fffbe5abaae69a08edf89c8d35bc8770a80578bff465d1fd080`
- oracle version: `oracles_v3`
- provider: requested `none`, effective `deterministic-fallback`
- sessions: 3
- findings (fail severity): 0

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| normal | `4d697eb1` | high | completion | — | 27 | 0 |
| asker | `f60113e0` | low | completion | — | 45 | 0 |
| idler | `9e72cf9d` | high | technical_stop | checkin_incomplete | 33 | 0 |

## Findings

- **INFO** `EXPECT:terminal:completion` (normal, `4d697eb1`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:no_orphaned` (normal, `4d697eb1`): 0 request episodes still pending
- **INFO** `EXPECT:terminal:completion` (asker, `f60113e0`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (asker, `f60113e0`): not applicable — nl_only profile refuses structured asks
- **INFO** `EXPECT:terminal:technical_stop` (idler, `9e72cf9d`): terminal stage 'technical_stop' (wanted 'technical_stop')
- **INFO** `EXPECT:terminal_reason:checkin_incomplete` (idler, `9e72cf9d`): terminal reason 'checkin_incomplete' (wanted 'checkin_incomplete')
