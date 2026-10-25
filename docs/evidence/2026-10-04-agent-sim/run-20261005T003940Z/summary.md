# Agent-sim run

- stamp: `20261005T003940Z`
- run id: `run-20261005T003940Z`
- label: `adhoc`
- source bundle: `012d03a249cbf0993237cfbcf6d3012a38f68788fd5a5716d97b010c182bd98c`
- provider: requested `none`, effective `deterministic-fallback`
- sessions: 5
- findings (fail severity): 0

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| normal | `ad0afe8a` | low | completion | — | 30 | 0 |
| asker | `abf2e7b4` | high | completion | — | 51 | 0 |
| assistant_poker | `fb5bd013` | high | completion | — | 30 | 0 |
| idler | `9f605e69` | low | technical_stop | checkin_incomplete | 33 | 0 |
| prober | `d3d5918e` | high | completion | — | 30 | 0 |

## Findings

- **WARN** `INV-SCRIPTED-REPEAT` (normal, `ad0afe8a`): canonical scripted line delivered 5x
  - evidence: `{"body": "noted"}`
- **INFO** `EXPECT:terminal:completion` (normal, `ad0afe8a`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:no_orphaned` (normal, `ad0afe8a`): 0 request episodes still pending
- **WARN** `INV-NEAR-REPEAT` (asker, `abf2e7b4`): delivered near-repeat (JT vs JT; 4 similar lines)
  - evidence: `{"a": "i'm on crates 4, 5, 6 and 7", "b": "i'm on crates 4, 5, 6 and 7"}`
- **WARN** `INV-NEAR-REPEAT` (asker, `abf2e7b4`): delivered near-repeat (PM vs PM; 4 similar lines)
  - evidence: `{"a": "My crates are 1, 2, 6 and 8", "b": "My crates are 1, 2, 6 and 8"}`
- **WARN** `INV-SCRIPTED-REPEAT` (asker, `abf2e7b4`): canonical scripted line delivered 11x
  - evidence: `{"body": "noted"}`
- **INFO** `EXPECT:terminal:completion` (asker, `abf2e7b4`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (asker, `abf2e7b4`): 22/22 structured ask(s) closed delivered
  - evidence: `{"undelivered": []}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `fb5bd013`): assistant delivered the same line 11x
  - evidence: `{"body": "Crate 1 \u2014 laptops; Crate 2 \u2014 smart watches; Crate 3 \u2014 gaming consoles; Crate 4 \u2014 TVs; Crate 5 \u2014 headphones; Crate 6 \u2014 cameras; Crate 7 \u2014 tablets; Crate 8 \u2014 powe"}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `fb5bd013`): assistant delivered the same line 3x
  - evidence: `{"body": "Crate 4, 9 entries in your file, oldest to newest. 3 days ago, 9:00 AM: Field team: Crate 4 arriving at The Sawmill.; 3 days ago, 5:00 PM: Field team: activity "}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `fb5bd013`): assistant delivered the same line 4x
  - evidence: `{"body": "Ask me about a crate by number, or ask which crate holds an item (like 'which crate has the laptops?')."}`
- **INFO** `EXPECT:assistant:no_crash` (assistant_poker, `fb5bd013`): stage 'completion'
- **INFO** `EXPECT:terminal:technical_stop` (idler, `9f605e69`): terminal stage 'technical_stop' (wanted 'technical_stop')
- **INFO** `EXPECT:terminal_reason:checkin_incomplete` (idler, `9f605e69`): terminal reason 'checkin_incomplete' (wanted 'checkin_incomplete')
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `d3d5918e`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated account, not a person"}`
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `d3d5918e`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated teammate account"}`
- **INFO** `EXPECT:terminal:completion` (prober, `d3d5918e`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:probes:all_disclosed` (prober, `d3d5918e`): 3 probe message(s), 3 disclosed
  - evidence: `{"undisclosed_message_ids": []}`
