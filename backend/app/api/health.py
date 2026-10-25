"""Operational endpoints (INV-0105).

- ``GET /health``  liveness only: the process is up and serving.
- ``GET /ready``   readiness: database reachable and, once migrations exist, at the expected
  head. Responses never include driver error text.
- ``GET /version`` build/schema/version stamps.
- ``GET /metrics`` Prometheus text, served only to direct callers. Any request that arrives
  through the Apache proxy carries ``X-Forwarded-*`` headers and receives 404.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.db.engine import DatabaseProbe, expected_schema_head, probe_database

router = APIRouter()
_PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-server", "forwarded")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


async def _probe(request: Request) -> DatabaseProbe:
    state = request.app.state
    timeout = state.settings.READINESS_DB_TIMEOUT_SECONDS + 1
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(probe_database, state.engine), timeout=timeout
        )
    except TimeoutError:
        return DatabaseProbe(reachable=False, schema_revision="unknown")


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    state = request.app.state
    probe = await _probe(request)
    expected = expected_schema_head()
    checks: dict[str, Any] = {
        "database": "ok" if probe.reachable else "unavailable",
        "schema": {"current": probe.schema_revision, "expected_head": expected},
    }
    reasons = []
    if not probe.reachable:
        reasons.append("database_unavailable")
    elif expected is not None and probe.schema_revision != expected:
        reasons.append("schema_mismatch")
    for reason in reasons:
        state.metrics.readiness_failures.labels(reason).inc()
    body = {"status": "not_ready" if reasons else "ready", "checks": checks}
    return JSONResponse(body, status_code=503 if reasons else 200)


@router.get("/version")
async def version(request: Request) -> dict[str, Any]:
    state = request.app.state
    build = state.build
    return {
        "service": "investigators-api",
        "service_version": build.service_version,
        "source_sha256": build.source_sha256,
        "behavior_sha256": build.behavior_sha256,
        "build_time": build.build_time,
        "image": build.image_ref,
        "pinned_build": build.pinned,
        "schema_expected_head": expected_schema_head(),
        "app_mode": state.settings.APP_MODE.value,
    }


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    if any(h in request.headers for h in _PROXY_HEADERS):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    from app.services.assistant_metrics import render

    assistant = await asyncio.to_thread(render, request.app.state.engine)
    return Response(
        generate_latest(request.app.state.metrics.registry) + assistant,
        media_type=CONTENT_TYPE_LATEST,
    )
