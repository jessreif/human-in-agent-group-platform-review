# Simulation run summaries

Each folder here holds the summary of one run of the simulation harness (`backend/harness/`), in which software participants completed whole sessions against the real platform. A summary records the source fingerprint the run used, the profile, the participant strategies, and every rule check that passed or failed.

These summaries are kept because the agent documents in `docs/agent/` cite them, and a contract test (`tests/contracts/test_doc_consistency.py`) checks that those citations match the recorded results. The full replays and logs from each run are not included. To produce a new run, see `examples/README.md`.
