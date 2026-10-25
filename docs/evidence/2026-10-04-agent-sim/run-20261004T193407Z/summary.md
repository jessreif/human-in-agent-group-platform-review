# Agent-sim run

- stamp: `20261004T193407Z`
- label: `nl-v2`
- source bundle: `aa9f56afa6a59fed1fddd6105246ad65c81245fecff850490c5a4bbab62498cb`
- sessions: 3
- findings (fail severity): 1

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| normal | `567aaf41` | low | completion | — | 30 | 0 |
| asker | `98ecb719` | high | completion | — | 51 | 1 |
| idler | `c692d055` | high | technical_stop | checkin_incomplete | 33 | 0 |

## Findings

- **INFO** `EXPECT:terminal:completion` (normal, `567aaf41`): terminal stage 'completion' (wanted 'completion')
- **INFO** `EXPECT:episodes:no_orphaned` (normal, `567aaf41`): 0 request episodes still pending
- **INFO** `EXPECT:terminal:completion` (asker, `98ecb719`): terminal stage 'completion' (wanted 'completion')
- **FAIL** `EXPECT:structured_response:each_ask` (asker, `98ecb719`): 9 delivered episode(s) vs 0 structured ask(s)
- **WARN** `INV-EPISODE-BOOKKEEPING` (idler, `c692d055`): 1 request episode(s) still pending at terminal stage technical_stop
- **INFO** `EXPECT:terminal:technical_stop` (idler, `c692d055`): terminal stage 'technical_stop' (wanted 'technical_stop')
- **INFO** `EXPECT:terminal_reason:checkin_incomplete` (idler, `c692d055`): terminal reason 'checkin_incomplete' (wanted 'checkin_incomplete')
