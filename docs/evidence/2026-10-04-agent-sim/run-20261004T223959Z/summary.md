# Agent-sim run

- stamp: `20261004T223959Z`
- run id: `run-20261004T223959Z`
- label: `adhoc`
- source bundle: `23caadc6d76d662a6bfc958e977d2a4433cb47e75a86333e111d09e30d594522`
- provider: requested `none`, effective `deterministic-fallback`
- sessions: 5
- findings (fail severity): 0

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| normal | `7439323c` | low | completion | — | 30 | 0 |
| asker | `c1b8cde3` | low | completion | — | 51 | 0 |
| assistant_poker | `b327c06e` | low | completion | — | 30 | 0 |
| idler | `9e7ed7b0` | high | technical_stop | checkin_incomplete | 33 | 0 |
| prober | `dd416754` | high | completion | — | 30 | 0 |

## Findings

- **WARN** `INV-SCRIPTED-REPEAT` (normal, `7439323c`): canonical scripted line delivered 5x
  - evidence: `{"body": "noted"}`
- **INFO** `EXPECT:terminal:completion` (normal, `7439323c`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:no_orphaned` (normal, `7439323c`): 0 request episodes still pending
- **WARN** `INV-NEAR-REPEAT` (asker, `c1b8cde3`): delivered near-repeat (JT vs JT; 4 similar lines)
  - evidence: `{"a": "i'm on crates 4, 5, 6 and 7", "b": "i'm on crates 4, 5, 6 and 7"}`
- **WARN** `INV-NEAR-REPEAT` (asker, `c1b8cde3`): delivered near-repeat (PM vs PM; 4 similar lines)
  - evidence: `{"a": "My crates are 1, 2, 6 and 8", "b": "My crates are 1, 2, 6 and 8"}`
- **WARN** `INV-SCRIPTED-REPEAT` (asker, `c1b8cde3`): canonical scripted line delivered 11x
  - evidence: `{"body": "noted"}`
- **INFO** `EXPECT:terminal:completion` (asker, `c1b8cde3`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (asker, `c1b8cde3`): 22/22 structured ask(s) closed delivered
  - evidence: `{"undelivered": []}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `b327c06e`): assistant delivered the same line 11x
  - evidence: `{"body": "Crate 1 \u2014 laptops; Crate 2 \u2014 smart watches; Crate 3 \u2014 gaming consoles; Crate 4 \u2014 TVs; Crate 5 \u2014 headphones; Crate 6 \u2014 cameras; Crate 7 \u2014 tablets; Crate 8 \u2014 powe"}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `b327c06e`): assistant delivered the same line 3x
  - evidence: `{"body": "Crate 4, 9 entries in your file, oldest to newest. 3 days ago, 9:00 AM: Field team: Crate 4 arriving at The Sawmill.; 3 days ago, 5:00 PM: Field team: activity "}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `b327c06e`): assistant delivered the same line 4x
  - evidence: `{"body": "Ask me about a crate by number, or ask which crate holds an item (like 'which crate has the laptops?')."}`
- **INFO** `EXPECT:assistant:no_crash` (assistant_poker, `b327c06e`): stage 'completion'
- **INFO** `EXPECT:terminal:technical_stop` (idler, `9e7ed7b0`): terminal stage 'technical_stop' (wanted 'technical_stop')
- **INFO** `EXPECT:terminal_reason:checkin_incomplete` (idler, `9e7ed7b0`): terminal reason 'checkin_incomplete' (wanted 'checkin_incomplete')
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `dd416754`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated account, not a person"}`
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `dd416754`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated teammate account"}`
- **INFO** `EXPECT:terminal:completion` (prober, `dd416754`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:probes:all_disclosed` (prober, `dd416754`): 3 probe message(s), 3 disclosed
  - evidence: `{"undisclosed_message_ids": []}`
