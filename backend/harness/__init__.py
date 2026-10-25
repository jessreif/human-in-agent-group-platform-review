"""Agent-participant simulation harness.

A scripted participant-bot that drives real sessions end-to-end — enrollment, stage
spine, chat, structured asks, assistant prompts, shares, check-in, instruments —
against a disposable PostgreSQL database. In-process command dispatch reuses the same
handlers the participant API calls (``execute_command`` + ``lifecycle.handler_for``),
so coverage is of the real command path, not a mock.

Two generation modes: ``provider=None`` (deterministic fallback — fast mechanism runs)
or a real/stub ``TextProvider`` (exercises generation + validation; the pinned Ollama
adapter is reachable only inside the worker's network namespace, so live-model runs
execute via ``docker exec investigators-worker``).

Outputs per-run evidence under ``docs/evidence/<date>-agent-sim/`` — transcripts plus an
anomaly report from the invariant oracle layer, feedable to the behaviors register.
"""
