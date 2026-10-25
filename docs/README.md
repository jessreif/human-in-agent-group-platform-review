# Documentation

## Main documents

| Document | Contents |
|---|---|
| `PLATFORM_OVERVIEW.md` | Architecture, trust boundaries, execution model, and the platform's guarantees and their limits |
| `CODE_TOUR.md` | A path through the code that carries the main design guarantees |
| `DISCLOSURE.md` | What participants are told, what is withheld until the debrief, and where it is corrected |
| `MODEL_CARD.md` | The language model, how it is served, decoding settings, and what is reproducible |
| `CLAIMS_TO_TESTS.md` | Each platform guarantee and the test or evaluation behind it |
| `EVALUATIONS.md` | Test and evaluation commands that were run, and their results |
| `GLOSSARY.md` | Terms and internal codes used across the documentation |
| `development-history.md` | How the platform was built and revised before the pilots |

## Reference

| Document | Contents |
|---|---|
| `ARCHITECTURE.md` | Services and data flow |
| `DATA_DICTIONARY.md` | Database tables and export files |
| `CODEBOOK.md` | Definitions of every computed measure (generated from code) |
| `MATERIALS/` | Catalogs of profiles, message banks, checks, prompts, and instruments (generated from `content/`) |
| `TECHNICAL_APPENDIX.md` | Stage graph, command matrix, event types, tables, and profiles (generated for each build) |
| `TIMING_CONFORMANCE_MATRIX.md` | Timing settings by profile and how each is enforced (generated) |
| `TIMING_REFERENCE_TO_V2_DIFF.md` | Differences between the original reference profiles and their revised successors (generated) |
| `ADMIN_SURFACE.md` | The researcher admin interface and its access control |
| `HISTORICAL_IMPORT_CONTRACT.md` | Rules for importing data from earlier, pre-platform studies |
| `TOOLCHAIN.md` | Pinned versions of languages, libraries, and base images |
| `FINGERPRINTS.json` | Recorded source fingerprints, checked by `scripts/verify_fingerprints.py` |

## Development records

These were written while the platform was being built and use the project's internal shorthand. `GLOSSARY.md` explains the codes.

| Document | Contents |
|---|---|
| `DESIGN_SPEC.md` | Full design specification for the platform and study profiles |
| `agent/` | Design, decisions, observed behavior, and simulation harness for the agent teammates and assistant |
| `DECISIONS.md` | Register of research decisions D01 to D14 |
| `IMPLEMENTATION_PLAN.md` | Work packages and tasks |
| `IMPLEMENTATION_STATUS.md` | Task ledger and dated deployment notes |
| `REQUIREMENT_TRACE.md` | Mapping from design requirements to tasks and checks (generated) |
| `baseline/` | Checksums of the design inputs and the two frozen reference demos |
| `evidence/` | Summaries of simulation runs cited by `agent/` |
| `clickthrough/` | A generated click-through mock used by a documentation test |

## Status labels

Some documents label each claim by how it is supported:

| Label | Meaning |
|---|---|
| **Verified** | A test or evaluation passed in a run recorded in `EVALUATIONS.md` |
| **Implemented** | The behavior is in the code and a test file exists, but no recorded run confirms that claim |
| **Requires model** | Needs the local model service to observe |
| **Not in demo** | The playable demos do not do this |
| **Described only** | Stated in documentation and not confirmed by a test |
| **Planned** | Not built yet |
