# Agent-sim run

- stamp: `20261004T193346Z`
- label: `batch3`
- source bundle: `aa9f56afa6a59fed1fddd6105246ad65c81245fecff850490c5a4bbab62498cb`
- sessions: 8
- findings (fail severity): 1

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| normal | `1d51aeb2` | low | completion | — | 30 | 0 |
| asker | `0f335649` | high | completion | — | 51 | 0 |
| prober | `fe3fd693` | low | completion | — | 30 | 0 |
| idler | `17781b4a` | high | technical_stop | checkin_incomplete | 33 | 0 |
| sharer | `853b595a` | high | completion | — | 30 | 0 |
| chaotic | `501fa530` | high | completion | — | 30 | 0 |
| assistant_poker | `c7e06ef1` | low | completion | — | 30 | 0 |
| disconnecter | `aa4c6e74` | high | completion | — | 30 | 1 |

## Findings

- **INFO** `EXPECT:terminal:completion` (normal, `1d51aeb2`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:no_orphaned` (normal, `1d51aeb2`): 0 request episodes still pending
- **WARN** `INV-SCRIPTED-REPEAT` (asker, `0f335649`): canonical scripted line delivered 12x
  - evidence: `{"body": "noted"}`
- **INFO** `EXPECT:terminal:completion` (asker, `0f335649`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (asker, `0f335649`): 47 delivered episode(s) vs 29 structured ask(s)
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `fe3fd693`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated account, not a person"}`
- **WARN** `INV-SCRIPTED-REPEAT` (prober, `fe3fd693`): canonical scripted line delivered 3x
  - evidence: `{"body": "i'm an automated teammate account"}`
- **INFO** `EXPECT:terminal:completion` (prober, `fe3fd693`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:probes:all_disclosed` (prober, `fe3fd693`): 3 probe message(s), 3 disclosure event(s)
- **WARN** `INV-EPISODE-BOOKKEEPING` (idler, `17781b4a`): 1 request episode(s) still pending at terminal stage technical_stop
- **INFO** `EXPECT:terminal:technical_stop` (idler, `17781b4a`): terminal stage 'technical_stop' (wanted 'technical_stop')
- **INFO** `EXPECT:terminal_reason:checkin_incomplete` (idler, `17781b4a`): terminal reason 'checkin_incomplete' (wanted 'checkin_incomplete')
- **WARN** `INV-SCRIPTED-REPEAT` (sharer, `853b595a`): canonical scripted line delivered 5x
  - evidence: `{"body": "got it"}`
- **WARN** `INV-SCRIPTED-REPEAT` (sharer, `853b595a`): canonical scripted line delivered 6x
  - evidence: `{"body": "makes sense"}`
- **INFO** `EXPECT:terminal:completion` (sharer, `853b595a`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:participant_share` (sharer, `853b595a`): episodes closed via participant_share: 3
- **WARN** `INV-SCRIPTED-REPEAT` (chaotic, `501fa530`): canonical scripted line delivered 5x
  - evidence: `{"body": "makes sense"}`
- **WARN** `INV-SCRIPTED-REPEAT` (chaotic, `501fa530`): canonical scripted line delivered 5x
  - evidence: `{"body": "noted"}`
- **INFO** `EXPECT:no_crash` (chaotic, `501fa530`): reached terminal stage 'completion'
- **INFO** `EXPECT:integrity:only_expected_rejections` (chaotic, `501fa530`): checked via INV-RECEIPT-REASON findings
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `c7e06ef1`): assistant delivered the same line 15x
  - evidence: `{"body": "Ask me about a specific crate by number or by contents."}`
- **INFO** `EXPECT:assistant:no_crash` (assistant_poker, `c7e06ef1`): stage 'completion'
- **FAIL** `INV-NEAR-REPEAT` (disconnecter, `aa4c6e74`): delivered near-repeat (PM vs JT; 2 similar lines)
  - evidence: `{"a": "I'm on crates 2, 3, 4 and 5", "b": "i'm on crates 4, 5, 6 and 8"}`
- **WARN** `INV-SCRIPTED-REPEAT` (disconnecter, `aa4c6e74`): canonical scripted line delivered 4x
  - evidence: `{"body": "got it"}`
- **WARN** `INV-SCRIPTED-REPEAT` (disconnecter, `aa4c6e74`): canonical scripted line delivered 4x
  - evidence: `{"body": "noted"}`
- **INFO** `EXPECT:resume:accepted` (disconnecter, `aa4c6e74`): 1 accepted resume receipt(s)
