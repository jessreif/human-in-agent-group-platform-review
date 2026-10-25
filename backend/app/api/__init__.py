"""HTTP API: participant, telemetry and protected admin endpoints (WP04+).

WP01 provides only operational endpoints (health, readiness, version, metrics).
Routes are declared without the external ``/investigators/api`` prefix; Apache strips it once
and the application sets ``root_path`` (spec §3.1).
"""
