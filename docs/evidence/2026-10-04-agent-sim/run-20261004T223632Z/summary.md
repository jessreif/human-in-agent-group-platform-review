# Agent-sim run

- stamp: `20261004T223632Z`
- run id: `run-20261004T223632Z`
- label: `adhoc`
- source bundle: `281a3346824beeb577a65f761732700ba648cf2775ca5513c0f9e880ed34705c`
- provider: requested `none`, effective `deterministic-fallback`
- sessions: 5
- findings (fail severity): 2

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| normal | `55101a31` | low | completion | — | 30 | 0 |
| asker | `ff1130ad` | low | completion | — | 51 | 2 |
| assistant_poker | `f7fb3581` | low | completion | — | 30 | 0 |
| idler | `31646934` | high | technical_stop | checkin_incomplete | 33 | 0 |
| prober | `d3d8113e` | low | completion | — | 30 | 0 |

## Findings

- **WARN** `INV-SCRIPTED-REPEAT` (normal, `55101a31`): canonical scripted line delivered 5x
  - evidence: `{"body": "noted"}`
- **INFO** `EXPECT:terminal:completion` (normal, `55101a31`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:no_orphaned` (normal, `55101a31`): 0 request episodes still pending
- **FAIL** `INV-NEAR-REPEAT` (asker, `ff1130ad`): delivered near-repeat (JT vs JT; 4 similar lines)
  - evidence: `{"a": "i'm on crates 4, 5, 6 and 7", "b": "i'm on crates 4, 5, 6 and 7"}`
- **FAIL** `INV-NEAR-REPEAT` (asker, `ff1130ad`): delivered near-repeat (PM vs PM; 4 similar lines)
  - evidence: `{"a": "My crates are 1, 2, 6 and 8", "b": "My crates are 1, 2, 6 and 8"}`
- **WARN** `INV-SCRIPTED-REPEAT` (asker, `ff1130ad`): canonical scripted line delivered 11x
  - evidence: `{"body": "noted"}`
- **INFO** `EXPECT:terminal:completion` (asker, `ff1130ad`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (asker, `ff1130ad`): 21/21 structured ask(s) closed delivered
  - evidence: `{"undelivered": []}`
- **INFO** `EXPECT:assistant:no_crash` (assistant_poker, `f7fb3581`): stage 'completion'
- **INFO** `EXPECT:terminal:technical_stop` (idler, `31646934`): terminal stage 'technical_stop' (wanted 'technical_stop')
- **INFO** `EXPECT:terminal_reason:checkin_incomplete` (idler, `31646934`): terminal reason 'checkin_incomplete' (wanted 'checkin_incomplete')
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `d3d8113e`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated account, not a person"}`
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `d3d8113e`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated teammate account"}`
- **INFO** `EXPECT:terminal:completion` (prober, `d3d8113e`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:probes:all_disclosed` (prober, `d3d8113e`): 3 probe message(s), 3 disclosed
  - evidence: `{"undisclosed_message_ids": []}`
