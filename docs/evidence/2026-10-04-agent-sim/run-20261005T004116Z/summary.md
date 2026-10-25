# Agent-sim run

- stamp: `20261005T004116Z`
- run id: `run-20261005T004116Z`
- label: `adhoc`
- source bundle: `d7a10cf9c9da934b1e9d8cf991b22a57fb6d3dcb2a12d580dabfcd3588b6e770`
- provider: requested `stub`, effective `sim-stub`
- sessions: 1
- findings (fail severity): 0

| persona | session | arm | terminal | reason | steps | fails |
|---|---|---|---|---|---|---|
| assistant_poker | `feb08c7b` | high | completion | — | 30 | 0 |

## Findings

- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `feb08c7b`): assistant delivered the same line 3x
  - evidence: `{"body": "on it, checking my notes"}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `feb08c7b`): assistant delivered the same line 7x
  - evidence: `{"body": "Crate 1 \u2014 laptops; Crate 2 \u2014 smart watches; Crate 3 \u2014 gaming consoles; Crate 4 \u2014 TVs; Crate 5 \u2014 headphones; Crate 6 \u2014 cameras; Crate 7 \u2014 tablets; Crate 8 \u2014 powe"}`
- **WARN** `INV-ASSISTANT-REPEAT` (assistant_poker, `feb08c7b`): assistant delivered the same line 3x
  - evidence: `{"body": "looking through what i have"}`
- **INFO** `EXPECT:assistant:no_crash` (assistant_poker, `feb08c7b`): stage 'completion'
