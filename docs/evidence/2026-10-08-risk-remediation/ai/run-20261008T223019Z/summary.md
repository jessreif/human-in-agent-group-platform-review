# Agent-sim run

- stamp: `20261008T223019Z`
- run id: `run-20261008T223019Z`
- label: `adhoc`
- source bundle: `a523152ef129690c66102e4a2ffa7604b24662983c1ba93cf62d75ec5eaa1321`
- oracle version: `oracles_v3`
- provider: requested `stub`, effective `sim-stub`
- sessions: 8
- findings (fail severity): 0

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| normal | `687046b4` | high | completion | — | 30 | 0 |
| asker | `094d6546` | high | completion | — | 51 | 0 |
| prober | `4f792235` | high | completion | — | 30 | 0 |
| assistant_poker | `96fd443a` | high | completion | — | 30 | 0 |
| sharer | `3078617e` | high | completion | — | 30 | 0 |
| chaotic | `ea82ccd1` | low | completion | — | 30 | 0 |
| disconnecter | `ad499ebb` | high | completion | — | 30 | 0 |
| idler | `06e3989e` | low | technical_stop | checkin_incomplete | 33 | 0 |

## Findings

- **INFO** `EXPECT:terminal:completion` (normal, `687046b4`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:no_orphaned` (normal, `687046b4`): 0 request episodes still pending
- **INFO** `EXPECT:terminal:completion` (asker, `094d6546`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (asker, `094d6546`): not applicable — nl_only profile refuses structured asks
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `4f792235`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated account, not a person"}`
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `4f792235`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated teammate account"}`
- **INFO** `EXPECT:terminal:completion` (prober, `4f792235`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:probes:all_disclosed` (prober, `4f792235`): 3 probe message(s), 3 disclosed
  - evidence: `{"undisclosed_message_ids": []}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `96fd443a`): assistant delivered the same line 7x
  - evidence: `{"body": "Crate 1 \u2014 laptops; Crate 2 \u2014 smart watches; Crate 3 \u2014 gaming consoles; Crate 4 \u2014 TVs; Crate 5 \u2014 headphones; Crate 6 \u2014 cameras; Crate 7 \u2014 tablets; Crate 8 \u2014 powe"}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `96fd443a`): assistant delivered the same line 3x
  - evidence: `{"body": "i'll take a look now"}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `96fd443a`): assistant delivered the same line 3x
  - evidence: `{"body": "on it, checking my notes"}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `96fd443a`): assistant delivered the same line 3x
  - evidence: `{"body": "looking through what i have"}`
- **INFO** `EXPECT:assistant:no_crash` (assistant_poker, `96fd443a`): stage 'completion'
- **INFO** `EXPECT:terminal:completion` (sharer, `3078617e`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:participant_share` (sharer, `3078617e`): episodes closed via participant_share: 3
- **INFO** `EXPECT:no_crash` (chaotic, `ea82ccd1`): reached terminal stage 'completion'
- **INFO** `EXPECT:integrity:only_expected_rejections` (chaotic, `ea82ccd1`): checked via INV-RECEIPT-REASON findings
- **INFO** `EXPECT:resume:accepted` (disconnecter, `ad499ebb`): 1 accepted resume receipt(s)
- **INFO** `EXPECT:terminal:technical_stop` (idler, `06e3989e`): terminal stage 'technical_stop' (wanted 'technical_stop')
- **INFO** `EXPECT:terminal_reason:checkin_incomplete` (idler, `06e3989e`): terminal reason 'checkin_incomplete' (wanted 'checkin_incomplete')
