# Agent-sim run

- stamp: `20261004T213722Z`
- run id: `run-20261004T213722Z`
- label: `ba78-88-remediation`
- source bundle: `7c059d17babd20769a1fa96ff6269ac57c8b73890b2a3e369beaaac19fb47103`
- provider: requested `none`, effective `deterministic-fallback`
- sessions: 8
- findings (fail severity): 1

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| normal | `f5c83467` | high | completion | — | 30 | 1 |
| asker | `d722a580` | high | completion | — | 51 | 0 |
| prober | `b55a0749` | low | completion | — | 30 | 0 |
| idler | `7d201384` | high | technical_stop | checkin_incomplete | 33 | 0 |
| sharer | `7945daca` | low | completion | — | 30 | 0 |
| chaotic | `5c68bf5d` | high | completion | — | 30 | 0 |
| disconnecter | `a8988705` | high | completion | — | 30 | 0 |
| assistant_poker | `56149da0` | high | completion | — | 30 | 0 |

## Findings

- **FAIL** `INV-NEAR-REPEAT` (normal, `f5c83467`): delivered near-repeat (JT vs PM; 2 similar lines)
  - evidence: `{"a": "i'm on crates 1, 2, 7 and 8", "b": "I'm on crates 2, 3, 5 and 8"}`
- **INFO** `EXPECT:terminal:completion` (normal, `f5c83467`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:no_orphaned` (normal, `f5c83467`): 0 request episodes still pending
- **WARN** `INV-SCRIPTED-REPEAT` (asker, `d722a580`): canonical scripted line delivered 11x
  - evidence: `{"body": "noted"}`
- **WARN** `INV-SCRIPTED-REPEAT` (asker, `d722a580`): canonical scripted line delivered 6x
  - evidence: `{"body": "send me a request for it and i will pass along what i have"}`
- **WARN** `INV-SCRIPTED-REPEAT` (asker, `d722a580`): canonical scripted line delivered 4x
  - evidence: `{"body": "makes sense"}`
- **WARN** `INV-SCRIPTED-REPEAT` (asker, `d722a580`): canonical scripted line delivered 4x
  - evidence: `{"body": "got it"}`
- **INFO** `EXPECT:terminal:completion` (asker, `d722a580`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (asker, `d722a580`): 19/19 structured ask(s) closed delivered
  - evidence: `{"undelivered": []}`
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `b55a0749`): canonical scripted line delivered 6x
  - evidence: `{"body": "i'm an automated teammate account"}`
- **INFO** `EXPECT:terminal:completion` (prober, `b55a0749`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:probes:all_disclosed` (prober, `b55a0749`): 3 probe message(s), 3 disclosed
  - evidence: `{"undisclosed_message_ids": []}`
- **WARN** `INV-EPISODE-BOOKKEEPING` (idler, `7d201384`): 1 request episode(s) still pending at terminal stage technical_stop
- **INFO** `EXPECT:terminal:technical_stop` (idler, `7d201384`): terminal stage 'technical_stop' (wanted 'technical_stop')
- **INFO** `EXPECT:terminal_reason:checkin_incomplete` (idler, `7d201384`): terminal reason 'checkin_incomplete' (wanted 'checkin_incomplete')
- **WARN** `INV-SCRIPTED-REPEAT` (sharer, `7945daca`): canonical scripted line delivered 4x
  - evidence: `{"body": "noted"}`
- **WARN** `INV-SCRIPTED-REPEAT` (sharer, `7945daca`): canonical scripted line delivered 4x
  - evidence: `{"body": "got it"}`
- **INFO** `EXPECT:terminal:completion` (sharer, `7945daca`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:participant_share` (sharer, `7945daca`): episodes closed via participant_share: 3
- **INFO** `EXPECT:no_crash` (chaotic, `5c68bf5d`): reached terminal stage 'completion'
- **INFO** `EXPECT:integrity:only_expected_rejections` (chaotic, `5c68bf5d`): checked via INV-RECEIPT-REASON findings
- **INFO** `EXPECT:resume:accepted` (disconnecter, `a8988705`): 1 accepted resume receipt(s)
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `56149da0`): assistant delivered the same line 17x
  - evidence: `{"body": "Ask me about a specific crate by number or by contents."}`
- **INFO** `EXPECT:assistant:no_crash` (assistant_poker, `56149da0`): stage 'completion'
