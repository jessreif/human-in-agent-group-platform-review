"""Executable generation identity, pinned at consent and copied into accepted jobs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services import engines, output_claims, prompts

FALLBACK_VERSION = "assistant_fallback_v2"
INFERENCE_POLICY: dict[str, Any] = {
    "temperature": 0.2,
    "max_tokens": 400,
    "reasoning_effort": "none_when_supported",
    "seed": "derived_session_job_v1",
}


def identity(build: str, model: str) -> dict[str, Any]:
    return {
        "build": build,
        "model": model,
        "builder": prompts.PROMPT_BUILDER_VERSION,
        "checker": output_claims.CHECKER_VERSION,
        "validator": engines.VALIDATOR_VERSION,
        "fallback": FALLBACK_VERSION,
        "inference": dict(INFERENCE_POLICY),
    }


def matches(
    expected: object,
    *,
    behavior: str,
    model: str,
    session_behavior: str | None,
    session_build: str,
) -> bool:
    """Compare immutable expected identity, never rewrite it to match the worker.

    ``behavior`` is this build's participant-facing digest
    (``BuildInfo.behavior_sha256``): an ops-only deploy cannot invalidate an
    in-flight session's pinned policy. The payload's ``build`` field is a pin
    token — for sessions consented under the scoped digest it is the behavior
    digest; for older rows it is the legacy whole-repo checksum — so it may
    equal either stored session digest, but the session's *behavior* digest
    must still equal this build's (a real participant-facing change refuses).
    """
    if not isinstance(expected, Mapping):
        return False
    pin = expected.get("build")
    pins = {p for p in (session_behavior, session_build) if p}
    return (
        isinstance(pin, str)
        and session_behavior == behavior
        and pin in pins
        and expected == identity(pin, model)
    )
