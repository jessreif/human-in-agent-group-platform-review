# Agent-sim run

- stamp: `20261008T231607Z`
- run id: `run-20261008T231607Z`
- label: `adhoc`
- source bundle: `31934e3e8c8cb950c0674575a52e9740d9d4932fcd65cf111afd9deba6dca213`
- oracle version: `oracles_v3`
- provider: requested `stub`, effective `sim-stub`
- sessions: 8
- findings (fail severity): 0

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| normal | `a7c87a91` | low | completion | — | 30 | 0 |
| asker | `e3186d52` | low | completion | — | 51 | 0 |
| prober | `c2881efd` | low | completion | — | 30 | 0 |
| assistant_poker | `75e473e6` | low | completion | — | 30 | 0 |
| sharer | `48422488` | low | completion | — | 30 | 0 |
| chaotic | `757a6dbf` | high | completion | — | 30 | 0 |
| disconnecter | `6905413b` | low | completion | — | 30 | 0 |
| idler | `2f06be92` | high | technical_stop | checkin_incomplete | 33 | 0 |

## Findings

- **INFO** `EXPECT:terminal:completion` (normal, `a7c87a91`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:no_orphaned` (normal, `a7c87a91`): 0 request episodes still pending
- **INFO** `EXPECT:terminal:completion` (asker, `e3186d52`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (asker, `e3186d52`): not applicable — nl_only profile refuses structured asks
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `c2881efd`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated account, not a person"}`
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `c2881efd`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated teammate account"}`
- **INFO** `EXPECT:terminal:completion` (prober, `c2881efd`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:probes:all_disclosed` (prober, `c2881efd`): 3 probe message(s), 3 disclosed
  - evidence: `{"undisclosed_message_ids": []}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `75e473e6`): assistant delivered the same line 7x
  - evidence: `{"body": "Crate 1 \u2014 laptops; Crate 2 \u2014 smart watches; Crate 3 \u2014 gaming consoles; Crate 4 \u2014 TVs; Crate 5 \u2014 headphones; Crate 6 \u2014 cameras; Crate 7 \u2014 tablets; Crate 8 \u2014 powe"}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `75e473e6`): assistant delivered the same line 3x
  - evidence: `{"body": "i'll take a look now"}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `75e473e6`): assistant delivered the same line 3x
  - evidence: `{"body": "on it, checking my notes"}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `75e473e6`): assistant delivered the same line 3x
  - evidence: `{"body": "looking through what i have"}`
- **INFO** `EXPECT:assistant:no_crash` (assistant_poker, `75e473e6`): stage 'completion'
- **INFO** `EXPECT:terminal:completion` (sharer, `48422488`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:participant_share` (sharer, `48422488`): episodes closed via participant_share: 3
- **INFO** `EXPECT:no_crash` (chaotic, `757a6dbf`): reached terminal stage 'completion'
- **INFO** `EXPECT:integrity:only_expected_rejections` (chaotic, `757a6dbf`): checked via INV-RECEIPT-REASON findings
- **INFO** `EXPECT:resume:accepted` (disconnecter, `6905413b`): 1 accepted resume receipt(s)
- **INFO** `EXPECT:terminal:technical_stop` (idler, `2f06be92`): terminal stage 'technical_stop' (wanted 'technical_stop')
- **INFO** `EXPECT:terminal_reason:checkin_incomplete` (idler, `2f06be92`): terminal reason 'checkin_incomplete' (wanted 'checkin_incomplete')
