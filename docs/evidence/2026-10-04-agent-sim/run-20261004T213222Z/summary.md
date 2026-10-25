# Agent-sim run

- stamp: `20261004T213222Z`
- run id: `run-20261004T213222Z`
- label: `ba78-88-remediation`
- source bundle: `7274389b1b388ea6089245b20574647c00838b610a619e3b75b0b429785d5c29`
- provider: requested `none`, effective `deterministic-fallback`
- sessions: 8
- findings (fail severity): 2

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| normal | `594d43cc` | low | completion | — | 30 | 1 |
| asker | `4fe72de8` | low | completion | — | 51 | 1 |
| prober | `03db59d8` | low | completion | — | 30 | 0 |
| idler | `467764f7` | low | technical_stop | checkin_incomplete | 33 | 0 |
| sharer | `62c792b5` | high | completion | — | 30 | 0 |
| chaotic | `8c350819` | high | completion | — | 30 | 0 |
| disconnecter | `1e052b1c` | high | completion | — | 30 | 0 |
| assistant_poker | `7f95a926` | low | completion | — | 30 | 0 |

## Findings

- **FAIL** `INV-NEAR-REPEAT` (normal, `594d43cc`): delivered near-repeat (JT vs PM; 2 similar lines)
  - evidence: `{"a": "i'm on crates 1, 2, 7 and 8", "b": "I'm on crates 2, 3, 5 and 8"}`
- **INFO** `EXPECT:terminal:completion` (normal, `594d43cc`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:no_orphaned` (normal, `594d43cc`): 0 request episodes still pending
- **WARN** `INV-SCRIPTED-REPEAT` (asker, `4fe72de8`): canonical scripted line delivered 4x
  - evidence: `{"body": "got it"}`
- **WARN** `INV-SCRIPTED-REPEAT` (asker, `4fe72de8`): canonical scripted line delivered 4x
  - evidence: `{"body": "makes sense"}`
- **WARN** `INV-SCRIPTED-REPEAT` (asker, `4fe72de8`): canonical scripted line delivered 11x
  - evidence: `{"body": "noted"}`
- **WARN** `INV-SCRIPTED-REPEAT` (asker, `4fe72de8`): canonical scripted line delivered 6x
  - evidence: `{"body": "send me a request for it and i will pass along what i have"}`
- **INFO** `EXPECT:terminal:completion` (asker, `4fe72de8`): terminal stage 'completion' (wanted 'completion')
- **FAIL** `EXPECT:structured_response:each_ask` (asker, `4fe72de8`): 21/22 structured ask(s) closed delivered
  - evidence: `{"undelivered": ["6d36f68f-5b3f-4e05-bf39-102122e27550"]}`
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `03db59d8`): canonical scripted line delivered 6x
  - evidence: `{"body": "i'm an automated teammate account"}`
- **INFO** `EXPECT:terminal:completion` (prober, `03db59d8`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:probes:all_disclosed` (prober, `03db59d8`): 3 probe message(s), 3 disclosed
  - evidence: `{"undisclosed_message_ids": []}`
- **WARN** `INV-EPISODE-BOOKKEEPING` (idler, `467764f7`): 1 request episode(s) still pending at terminal stage technical_stop
- **INFO** `EXPECT:terminal:technical_stop` (idler, `467764f7`): terminal stage 'technical_stop' (wanted 'technical_stop')
- **INFO** `EXPECT:terminal_reason:checkin_incomplete` (idler, `467764f7`): terminal reason 'checkin_incomplete' (wanted 'checkin_incomplete')
- **WARN** `INV-SCRIPTED-REPEAT` (sharer, `62c792b5`): canonical scripted line delivered 4x
  - evidence: `{"body": "got it"}`
- **WARN** `INV-SCRIPTED-REPEAT` (sharer, `62c792b5`): canonical scripted line delivered 4x
  - evidence: `{"body": "noted"}`
- **INFO** `EXPECT:terminal:completion` (sharer, `62c792b5`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:participant_share` (sharer, `62c792b5`): episodes closed via participant_share: 3
- **INFO** `EXPECT:no_crash` (chaotic, `8c350819`): reached terminal stage 'completion'
- **INFO** `EXPECT:integrity:only_expected_rejections` (chaotic, `8c350819`): checked via INV-RECEIPT-REASON findings
- **INFO** `EXPECT:resume:accepted` (disconnecter, `1e052b1c`): 1 accepted resume receipt(s)
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `7f95a926`): assistant delivered the same line 17x
  - evidence: `{"body": "Ask me about a specific crate by number or by contents."}`
- **INFO** `EXPECT:assistant:no_crash` (assistant_poker, `7f95a926`): stage 'completion'
