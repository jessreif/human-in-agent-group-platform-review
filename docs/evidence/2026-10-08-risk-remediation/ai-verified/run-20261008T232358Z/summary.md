# Agent-sim run

- stamp: `20261008T232358Z`
- run id: `run-20261008T232358Z`
- label: `adhoc`
- source bundle: `065f133eab8b0a3c00c3a6c6e4f1f9cd409891a1cfd8e48e7c79cbe14ef3ca8e`
- oracle version: `oracles_v3`
- provider: requested `stub`, effective `sim-stub`
- sessions: 8
- findings (fail severity): 0

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| normal | `c69ad861` | high | completion | — | 30 | 0 |
| asker | `b0c45e6d` | low | completion | — | 51 | 0 |
| prober | `7808a9a6` | high | completion | — | 30 | 0 |
| assistant_poker | `02c80a3b` | low | completion | — | 30 | 0 |
| sharer | `f190ff4b` | low | completion | — | 30 | 0 |
| chaotic | `fdad8e50` | low | completion | — | 30 | 0 |
| disconnecter | `29ca1ae7` | high | completion | — | 30 | 0 |
| idler | `45e9b8e4` | low | technical_stop | checkin_incomplete | 33 | 0 |

## Findings

- **INFO** `EXPECT:terminal:completion` (normal, `c69ad861`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:no_orphaned` (normal, `c69ad861`): 0 request episodes still pending
- **INFO** `EXPECT:terminal:completion` (asker, `b0c45e6d`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (asker, `b0c45e6d`): not applicable — nl_only profile refuses structured asks
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `7808a9a6`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated teammate account"}`
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `7808a9a6`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated account, not a person"}`
- **INFO** `EXPECT:terminal:completion` (prober, `7808a9a6`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:probes:all_disclosed` (prober, `7808a9a6`): 3 probe message(s), 3 disclosed
  - evidence: `{"undisclosed_message_ids": []}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `02c80a3b`): assistant delivered the same line 7x
  - evidence: `{"body": "Crate 1 \u2014 laptops; Crate 2 \u2014 smart watches; Crate 3 \u2014 gaming consoles; Crate 4 \u2014 TVs; Crate 5 \u2014 headphones; Crate 6 \u2014 cameras; Crate 7 \u2014 tablets; Crate 8 \u2014 powe"}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `02c80a3b`): assistant delivered the same line 3x
  - evidence: `{"body": "i'll take a look now"}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `02c80a3b`): assistant delivered the same line 3x
  - evidence: `{"body": "on it, checking my notes"}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `02c80a3b`): assistant delivered the same line 3x
  - evidence: `{"body": "looking through what i have"}`
- **INFO** `EXPECT:assistant:no_crash` (assistant_poker, `02c80a3b`): stage 'completion'
- **INFO** `EXPECT:terminal:completion` (sharer, `f190ff4b`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:participant_share` (sharer, `f190ff4b`): episodes closed via participant_share: 3
- **INFO** `EXPECT:no_crash` (chaotic, `fdad8e50`): reached terminal stage 'completion'
- **INFO** `EXPECT:integrity:only_expected_rejections` (chaotic, `fdad8e50`): checked via INV-RECEIPT-REASON findings
- **INFO** `EXPECT:resume:accepted` (disconnecter, `29ca1ae7`): 1 accepted resume receipt(s)
- **INFO** `EXPECT:terminal:technical_stop` (idler, `45e9b8e4`): terminal stage 'technical_stop' (wanted 'technical_stop')
- **INFO** `EXPECT:terminal_reason:checkin_incomplete` (idler, `45e9b8e4`): terminal reason 'checkin_incomplete' (wanted 'checkin_incomplete')
