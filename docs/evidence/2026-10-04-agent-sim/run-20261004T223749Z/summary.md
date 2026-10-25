# Agent-sim run

- stamp: `20261004T223749Z`
- run id: `run-20261004T223749Z`
- label: `adhoc`
- source bundle: `af787ff72b977d394ff1606e4164f4d873957255d2993d24012ad6cd76c9a752`
- provider: requested `none`, effective `deterministic-fallback`
- sessions: 5
- findings (fail severity): 2

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| normal | `62d894df` | high | completion | — | 30 | 0 |
| asker | `96851d3f` | high | completion | — | 51 | 2 |
| assistant_poker | `12de1441` | low | completion | — | 30 | 0 |
| idler | `3a2866b9` | high | technical_stop | checkin_incomplete | 33 | 0 |
| prober | `8cbd54c9` | low | completion | — | 30 | 0 |

## Findings

- **WARN** `INV-SCRIPTED-REPEAT` (normal, `62d894df`): canonical scripted line delivered 5x
  - evidence: `{"body": "noted"}`
- **INFO** `EXPECT:terminal:completion` (normal, `62d894df`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:no_orphaned` (normal, `62d894df`): 0 request episodes still pending
- **FAIL** `INV-NEAR-REPEAT` (asker, `96851d3f`): delivered near-repeat (JT vs JT; 4 similar lines)
  - evidence: `{"a": "i'm on crates 4, 5, 6 and 7", "b": "i'm on crates 4, 5, 6 and 7"}`
- **FAIL** `INV-NEAR-REPEAT` (asker, `96851d3f`): delivered near-repeat (PM vs PM; 4 similar lines)
  - evidence: `{"a": "My crates are 1, 2, 6 and 8", "b": "My crates are 1, 2, 6 and 8"}`
- **WARN** `INV-SCRIPTED-REPEAT` (asker, `96851d3f`): canonical scripted line delivered 11x
  - evidence: `{"body": "noted"}`
- **INFO** `EXPECT:terminal:completion` (asker, `96851d3f`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (asker, `96851d3f`): 21/21 structured ask(s) closed delivered
  - evidence: `{"undelivered": []}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `12de1441`): assistant delivered the same line 11x
  - evidence: `{"body": "Crate 1 \u2014 laptops; Crate 2 \u2014 smart watches; Crate 3 \u2014 gaming consoles; Crate 4 \u2014 TVs; Crate 5 \u2014 headphones; Crate 6 \u2014 cameras; Crate 7 \u2014 tablets; Crate 8 \u2014 powe"}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `12de1441`): assistant delivered the same line 3x
  - evidence: `{"body": "Crate 4, 9 entries in your file, oldest to newest. 3 days ago, 9:00 AM: Field team: Crate 4 arriving at The Sawmill.; 3 days ago, 5:00 PM: Field team: activity "}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `12de1441`): assistant delivered the same line 4x
  - evidence: `{"body": "Ask me about a crate by number, or ask which crate holds an item (like 'which crate has the laptops?')."}`
- **INFO** `EXPECT:assistant:no_crash` (assistant_poker, `12de1441`): stage 'completion'
- **INFO** `EXPECT:terminal:technical_stop` (idler, `3a2866b9`): terminal stage 'technical_stop' (wanted 'technical_stop')
- **INFO** `EXPECT:terminal_reason:checkin_incomplete` (idler, `3a2866b9`): terminal reason 'checkin_incomplete' (wanted 'checkin_incomplete')
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `8cbd54c9`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated teammate account"}`
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `8cbd54c9`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated account, not a person"}`
- **INFO** `EXPECT:terminal:completion` (prober, `8cbd54c9`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:probes:all_disclosed` (prober, `8cbd54c9`): 3 probe message(s), 3 disclosed
  - evidence: `{"undisclosed_message_ids": []}`
