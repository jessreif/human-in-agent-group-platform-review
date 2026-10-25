# Agent-sim run

- stamp: `20261005T103408Z`
- run id: `run-20261005T103408Z`
- label: `channel-sweep`
- source bundle: `84ee7bce93127e3907bf71996d53deb3aa8c9ea297727d661d57dff018570597`
- oracle version: `oracles_v3`
- provider: requested `none`, effective `deterministic-fallback`
- sessions: 6
- findings (fail severity): 0

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| casefile_only | `73893fd5` | low | completion | — | 30 | 0 |
| chat_only | `ff0d3428` | low | completion | — | 30 | 0 |
| assistant_only | `d1eb798a` | low | completion | — | 30 | 0 |
| casefile_chat | `10e7c037` | low | completion | — | 30 | 0 |
| casefile_assistant | `4b5c519a` | high | completion | — | 30 | 0 |
| all_channels | `e376f07e` | low | completion | — | 30 | 0 |

## Findings

- **INFO** `EXPECT:terminal:completion` (casefile_only, `73893fd5`): terminal stage 'completion' (wanted 'completion')
- **WARN** `INV-SCRIPTED-REPEAT` (chat_only, `ff0d3428`): canonical scripted line delivered 18x
  - evidence: `{"body": "noted"}`
- **INFO** `EXPECT:terminal:completion` (chat_only, `ff0d3428`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (chat_only, `ff0d3428`): 7/7 structured ask(s) closed delivered
  - evidence: `{"undelivered": [], "dead_window_expired": []}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_only, `d1eb798a`): assistant delivered the same line 3x
  - evidence: `{"body": "Crate 1 \u2014 laptops; Crate 2 \u2014 smart watches; Crate 3 \u2014 gaming consoles; Crate 4 \u2014 TVs; Crate 5 \u2014 headphones; Crate 6 \u2014 cameras; Crate 7 \u2014 tablets; Crate 8 \u2014 powe"}`
- **INFO** `EXPECT:terminal:completion` (assistant_only, `d1eb798a`): terminal stage 'completion' (wanted 'completion')
- **WARN** `INV-SCRIPTED-REPEAT` (casefile_chat, `10e7c037`): canonical scripted line delivered 4x
  - evidence: `{"body": "got it"}`
- **INFO** `EXPECT:terminal:completion` (casefile_chat, `10e7c037`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (casefile_chat, `10e7c037`): 14/14 structured ask(s) closed delivered (1 expired inside the dead window)
  - evidence: `{"undelivered": [], "dead_window_expired": ["a21454ac-4957-4b8c-8496-4c5236872553"]}`
- **WARN** `INV-ASSISTANT-REPEAT` (casefile_assistant, `4b5c519a`): assistant delivered the same line 6x
  - evidence: `{"body": "Ask me about a crate by number, or ask which crate holds an item (like 'which crate has the laptops?')."}`
- **WARN** `INV-ASSISTANT-REPEAT` (casefile_assistant, `4b5c519a`): assistant delivered the same line 5x
  - evidence: `{"body": "Crate 1 \u2014 laptops; Crate 2 \u2014 smart watches; Crate 3 \u2014 gaming consoles; Crate 4 \u2014 TVs; Crate 5 \u2014 headphones; Crate 6 \u2014 cameras; Crate 7 \u2014 tablets; Crate 8 \u2014 powe"}`
- **INFO** `EXPECT:terminal:completion` (casefile_assistant, `4b5c519a`): terminal stage 'completion' (wanted 'completion')
- **WARN** `INV-ASSISTANT-REPEAT` (all_channels, `e376f07e`): assistant delivered the same line 4x
  - evidence: `{"body": "Ask me about a crate by number, or ask which crate holds an item (like 'which crate has the laptops?')."}`
- **INFO** `EXPECT:terminal:completion` (all_channels, `e376f07e`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (all_channels, `e376f07e`): 14/14 structured ask(s) closed delivered (1 expired inside the dead window)
  - evidence: `{"undelivered": [], "dead_window_expired": ["3cdb8470-2d30-4ab0-accd-05f5642db942"]}`
