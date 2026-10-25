# Agent-sim run

- stamp: `20261012T203054Z`
- run id: `run-20261012T203054Z`
- label: `adhoc`
- source bundle: `0dee18e00a54b36f95f5751be604dc505b977992b9fac98b560e81185179f727`
- oracle version: `oracles_v3`
- provider: requested `none`, effective `deterministic-fallback`
- sessions: 4
- findings (fail severity): 0

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| normal | `87650c6a` | low | completion | — | 27 | 0 |
| asker | `1755e35d` | low | completion | — | 45 | 0 |
| sharer | `583cc843` | low | completion | — | 27 | 0 |
| idler | `7f2ec320` | low | technical_stop | checkin_incomplete | 33 | 0 |

## Findings

- **INFO** `EXPECT:terminal:completion` (normal, `87650c6a`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:no_orphaned` (normal, `87650c6a`): 0 request episodes still pending
- **WARN** `INV-SCRIPTED-REPEAT` (asker, `1755e35d`): canonical scripted line delivered 4x
  - evidence: `{"body": "noted"}`
- **INFO** `EXPECT:terminal:completion` (asker, `1755e35d`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (asker, `1755e35d`): not applicable — nl_only profile refuses structured asks
- **INFO** `EXPECT:terminal:completion` (sharer, `583cc843`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:participant_share` (sharer, `583cc843`): episodes closed via participant_share: 5
- **INFO** `EXPECT:terminal:technical_stop` (idler, `7f2ec320`): terminal stage 'technical_stop' (wanted 'technical_stop')
- **INFO** `EXPECT:terminal_reason:checkin_incomplete` (idler, `7f2ec320`): terminal reason 'checkin_incomplete' (wanted 'checkin_incomplete')
