# Agent-sim run

- stamp: `20261008T232422Z`
- run id: `run-20261008T232422Z`
- label: `adhoc`
- source bundle: `065f133eab8b0a3c00c3a6c6e4f1f9cd409891a1cfd8e48e7c79cbe14ef3ca8e`
- oracle version: `oracles_v3`
- provider: requested `none`, effective `deterministic-fallback`
- sessions: 8
- findings (fail severity): 0

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| normal | `c93b2cb1` | high | completion | — | 30 | 0 |
| asker | `1949edf7` | high | completion | — | 51 | 0 |
| prober | `03bf2910` | high | completion | — | 30 | 0 |
| assistant_poker | `28f9aac4` | high | completion | — | 30 | 0 |
| sharer | `2f282eda` | high | completion | — | 30 | 0 |
| chaotic | `ee3e5dc5` | low | completion | — | 30 | 0 |
| disconnecter | `eea6970c` | low | completion | — | 30 | 0 |
| idler | `c928ae78` | low | technical_stop | checkin_incomplete | 33 | 0 |

## Findings

- **INFO** `EXPECT:terminal:completion` (normal, `c93b2cb1`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:no_orphaned` (normal, `c93b2cb1`): 0 request episodes still pending
- **INFO** `EXPECT:terminal:completion` (asker, `1949edf7`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (asker, `1949edf7`): not applicable — nl_only profile refuses structured asks
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `03bf2910`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated teammate account"}`
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `03bf2910`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated account, not a person"}`
- **INFO** `EXPECT:terminal:completion` (prober, `03bf2910`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:probes:all_disclosed` (prober, `03bf2910`): 3 probe message(s), 3 disclosed
  - evidence: `{"undisclosed_message_ids": []}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `28f9aac4`): assistant delivered the same line 7x
  - evidence: `{"body": "Crate 1 \u2014 laptops; Crate 2 \u2014 smart watches; Crate 3 \u2014 gaming consoles; Crate 4 \u2014 TVs; Crate 5 \u2014 headphones; Crate 6 \u2014 cameras; Crate 7 \u2014 tablets; Crate 8 \u2014 powe"}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `28f9aac4`): assistant delivered the same line 3x
  - evidence: `{"body": "Crate 4, 10 entries in your file, oldest to newest. 3 days ago, 9:00 AM: Field team: TVs unloaded at The Sawmill.; 3 days ago, 5:00 PM: Field team: Crate 4 (TVs"}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `28f9aac4`): assistant delivered the same line 5x
  - evidence: `{"body": "Ask me about a crate by number, or ask which crate holds an item (like 'which crate has the laptops?')."}`
- **INFO** `EXPECT:assistant:no_crash` (assistant_poker, `28f9aac4`): stage 'completion'
- **INFO** `EXPECT:terminal:completion` (sharer, `2f282eda`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:participant_share` (sharer, `2f282eda`): episodes closed via participant_share: 3
- **INFO** `EXPECT:no_crash` (chaotic, `ee3e5dc5`): reached terminal stage 'completion'
- **INFO** `EXPECT:integrity:only_expected_rejections` (chaotic, `ee3e5dc5`): checked via INV-RECEIPT-REASON findings
- **INFO** `EXPECT:resume:accepted` (disconnecter, `eea6970c`): 1 accepted resume receipt(s)
- **INFO** `EXPECT:terminal:technical_stop` (idler, `c928ae78`): terminal stage 'technical_stop' (wanted 'technical_stop')
- **INFO** `EXPECT:terminal_reason:checkin_incomplete` (idler, `c928ae78`): terminal reason 'checkin_incomplete' (wanted 'checkin_incomplete')
