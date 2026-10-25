# Agent-sim run

- stamp: `20261005T004059Z`
- run id: `run-20261005T004059Z`
- label: `adhoc`
- source bundle: `d7a10cf9c9da934b1e9d8cf991b22a57fb6d3dcb2a12d580dabfcd3588b6e770`
- provider: requested `none`, effective `deterministic-fallback`
- sessions: 5
- findings (fail severity): 0

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| normal | `e273123b` | high | completion | — | 30 | 0 |
| asker | `cce03245` | high | completion | — | 51 | 0 |
| assistant_poker | `4641ab20` | high | completion | — | 30 | 0 |
| idler | `0983395a` | low | technical_stop | checkin_incomplete | 33 | 0 |
| prober | `be0cb5fa` | low | completion | — | 30 | 0 |

## Findings

- **WARN** `INV-SCRIPTED-REPEAT` (normal, `e273123b`): canonical scripted line delivered 5x
  - evidence: `{"body": "noted"}`
- **INFO** `EXPECT:terminal:completion` (normal, `e273123b`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:no_orphaned` (normal, `e273123b`): 0 request episodes still pending
- **WARN** `INV-NEAR-REPEAT` (asker, `cce03245`): delivered near-repeat (JT vs JT; 2 similar lines)
  - evidence: `{"a": "i'm on crates 4, 5, 6 and 7", "b": "still on crates 4, 5, 6 and 7"}`
- **WARN** `INV-SCRIPTED-REPEAT` (asker, `cce03245`): canonical scripted line delivered 11x
  - evidence: `{"body": "noted"}`
- **INFO** `EXPECT:terminal:completion` (asker, `cce03245`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (asker, `cce03245`): 22/22 structured ask(s) closed delivered
  - evidence: `{"undelivered": []}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `4641ab20`): assistant delivered the same line 11x
  - evidence: `{"body": "Crate 1 \u2014 laptops; Crate 2 \u2014 smart watches; Crate 3 \u2014 gaming consoles; Crate 4 \u2014 TVs; Crate 5 \u2014 headphones; Crate 6 \u2014 cameras; Crate 7 \u2014 tablets; Crate 8 \u2014 powe"}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `4641ab20`): assistant delivered the same line 3x
  - evidence: `{"body": "Crate 4, 9 entries in your file, oldest to newest. 3 days ago, 9:00 AM: Field team: Crate 4 arriving at The Sawmill.; 3 days ago, 5:00 PM: Field team: activity "}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `4641ab20`): assistant delivered the same line 4x
  - evidence: `{"body": "Ask me about a crate by number, or ask which crate holds an item (like 'which crate has the laptops?')."}`
- **INFO** `EXPECT:assistant:no_crash` (assistant_poker, `4641ab20`): stage 'completion'
- **INFO** `EXPECT:terminal:technical_stop` (idler, `0983395a`): terminal stage 'technical_stop' (wanted 'technical_stop')
- **INFO** `EXPECT:terminal_reason:checkin_incomplete` (idler, `0983395a`): terminal reason 'checkin_incomplete' (wanted 'checkin_incomplete')
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `be0cb5fa`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated account, not a person"}`
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `be0cb5fa`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated teammate account"}`
- **INFO** `EXPECT:terminal:completion` (prober, `be0cb5fa`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:probes:all_disclosed` (prober, `be0cb5fa`): 3 probe message(s), 3 disclosed
  - evidence: `{"undisclosed_message_ids": []}`
