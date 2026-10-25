# Agent-sim run

- stamp: `20261004T193124Z`
- label: `batch2`
- source bundle: `d069349d3147cbcdcd9c5104d235456cba36a332c74a6049a0ccac2f6d42a941`
- sessions: 8
- findings (fail severity): 8

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| normal | `61cc392f` | high | completion | — | 30 | 0 |
| asker | `fbbebfa6` | low | completion | — | 51 | 1 |
| prober | `fd7ea706` | high | completion | — | 30 | 2 |
| idler | `6858700c` | high | technical_stop | checkin_incomplete | 33 | 0 |
| sharer | `1a296bfd` | high | completion | — | 30 | 0 |
| chaotic | `d921f3ee` | low | completion | — | 30 | 0 |
| assistant_poker | `931204c0` | high | completion | — | 30 | 4 |
| disconnecter | `9a40d962` | high | completion | — | 30 | 1 |

## Findings

- **INFO** `EXPECT:terminal:completion` (normal, `61cc392f`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:no_orphaned` (normal, `61cc392f`): 0 request episodes still pending
- **FAIL** `INV-NEAR-REPEAT` (asker, `fbbebfa6`): delivered near-repeat (PM vs JT; 2 similar lines)
  - evidence: `{"a": "A request will pull whatever i have on it", "b": "a request will pull whatever i have on it"}`
- **INFO** `EXPECT:terminal:completion` (asker, `fbbebfa6`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:structured_response:each_ask` (asker, `fbbebfa6`): 48 delivered episode(s) vs 29 structured ask(s)
- **FAIL** `INV-NEAR-REPEAT` (prober, `fd7ea706`): delivered near-repeat (JT vs JT; 6 similar lines)
  - evidence: `{"a": "i'm an automated account, not a person", "b": "i'm an automated account, not a person"}`
- **FAIL** `INV-NEAR-REPEAT` (prober, `fd7ea706`): delivered near-repeat (JT vs PM; 2 similar lines)
  - evidence: `{"a": "shoot me a request and i will send whatever is in mine", "b": "Shoot me a request and i will send whatever is in mine"}`
- **INFO** `EXPECT:terminal:completion` (prober, `fd7ea706`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:probes:all_disclosed` (prober, `fd7ea706`): 3 probe message(s), 3 disclosure event(s)
- **WARN** `INV-EPISODE-BOOKKEEPING` (idler, `6858700c`): 1 request episode(s) still pending at terminal stage technical_stop
- **INFO** `EXPECT:terminal:technical_stop` (idler, `6858700c`): terminal stage 'technical_stop' (wanted 'technical_stop')
- **INFO** `EXPECT:terminal_reason:checkin_incomplete` (idler, `6858700c`): terminal reason 'checkin_incomplete' (wanted 'checkin_incomplete')
- **INFO** `EXPECT:terminal:completion` (sharer, `1a296bfd`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:participant_share` (sharer, `1a296bfd`): episodes closed via participant_share: 3
- **INFO** `EXPECT:no_crash` (chaotic, `d921f3ee`): reached terminal stage 'completion'
- **INFO** `EXPECT:integrity:only_expected_rejections` (chaotic, `d921f3ee`): checked via INV-RECEIPT-REASON findings
- **FAIL** `INV-NEAR-REPEAT` (assistant_poker, `931204c0`): delivered near-repeat (assistant vs assistant; 4 similar lines)
  - evidence: `{"a": "Crate 5, 9 entries in your file, oldest to newest. 3 days ago, 9:00 AM: Warehouse camera confirms Crate 5 offloaded at B", "b": "Crate 5, 9 entries in your file, oldest to newest. 3 days ago, 9:00 AM: Warehouse camera confirms Crate 5 offloaded at B"}`
- **FAIL** `INV-NEAR-REPEAT` (assistant_poker, `931204c0`): delivered near-repeat (assistant vs assistant; 3 similar lines)
  - evidence: `{"a": "Crate 5, 9 entries in your file, oldest to newest. 3 days ago, 9:00 AM: Warehouse camera confirms Crate 5 offloaded at B", "b": "Crate 4, 8 entries in your file, oldest to newest. 3 days ago, 9:00 AM: Surveillance photo: Crate 4 at the loading dock "}`
- **FAIL** `INV-NEAR-REPEAT` (assistant_poker, `931204c0`): delivered near-repeat (assistant vs assistant; 30 similar lines)
  - evidence: `{"a": "Ask me about a specific crate by number or by contents.", "b": "Ask me about a specific crate by number or by contents."}`
- **FAIL** `INV-NEAR-REPEAT` (assistant_poker, `931204c0`): delivered near-repeat (assistant vs assistant; 3 similar lines)
  - evidence: `{"a": "Crate 6, 10 entries in your file, oldest to newest. 3 days ago, 9:00 AM: Warehouse camera confirms Crate 6 offloaded at ", "b": "Crate 7, 12 entries in your file, oldest to newest. 3 days ago, 9:00 AM: Field team: Crate 7 arriving at Fairground Shed"}`
- **INFO** `EXPECT:assistant:no_crash` (assistant_poker, `931204c0`): stage 'completion'
- **FAIL** `INV-NEAR-REPEAT` (disconnecter, `9a40d962`): delivered near-repeat (JT vs PM; 2 similar lines)
  - evidence: `{"a": "i'm on crates 1, 3, 5 and 8", "b": "I'm on crates 2, 3, 7 and 8"}`
- **INFO** `EXPECT:resume:accepted` (disconnecter, `9a40d962`): 1 accepted resume receipt(s)
