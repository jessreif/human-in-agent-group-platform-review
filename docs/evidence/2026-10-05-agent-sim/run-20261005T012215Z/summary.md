# Agent-sim run

- stamp: `20261005T012215Z`
- run id: `run-20261005T012215Z`
- label: `adhoc`
- source bundle: `668a0e918ff3ce30fd7d09b1d17aee96c4ff52e1c71163805fb52675b7b13a7d`
- oracle version: `oracles_v3`
- provider: requested `none`, effective `deterministic-fallback`
- sessions: 5
- findings (fail severity): 0

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| normal | `c08a74d9` | low | completion | — | 30 | 0 |
| asker | `a4d13e9b` | low | completion | — | 51 | 0 |
| prober | `299cff0d` | high | completion | — | 30 | 0 |
| disconnecter | `d20f62c9` | low | completion | — | 30 | 0 |
| idler | `6b9da623` | low | technical_stop | checkin_incomplete | 33 | 0 |

## Findings

- **INFO** `EXPECT:terminal:completion` (normal, `c08a74d9`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:no_orphaned` (normal, `c08a74d9`): 0 request episodes still pending
- **WARN** `INV-SCRIPTED-REPEAT` (asker, `a4d13e9b`): canonical scripted line delivered 3x
  - evidence: `{"body": "a request will pull whatever i have on it"}`
- **WARN** `INV-SCRIPTED-REPEAT` (asker, `a4d13e9b`): canonical scripted line delivered 9x
  - evidence: `{"body": "noted"}`
- **INFO** `EXPECT:terminal:completion` (asker, `a4d13e9b`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (asker, `a4d13e9b`): 28/28 structured ask(s) closed delivered
  - evidence: `{"undelivered": [], "dead_window_expired": []}`
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `299cff0d`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated teammate account"}`
- **INFO** `EXPECT:terminal:completion` (prober, `299cff0d`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:probes:all_disclosed` (prober, `299cff0d`): 3 probe message(s), 3 disclosed
  - evidence: `{"undisclosed_message_ids": []}`
- **INFO** `EXPECT:resume:accepted` (disconnecter, `d20f62c9`): 1 accepted resume receipt(s)
- **INFO** `EXPECT:terminal:technical_stop` (idler, `6b9da623`): terminal stage 'technical_stop' (wanted 'technical_stop')
- **INFO** `EXPECT:terminal_reason:checkin_incomplete` (idler, `6b9da623`): terminal reason 'checkin_incomplete' (wanted 'checkin_incomplete')
