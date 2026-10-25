"""Request IDs, request logging and basic failure metrics (INV-0105).

- ``X-Request-ID``: accepted from the client if well-formed, otherwise generated; echoed on
  every response and attached to every log line emitted while handling the request.
- Request log lines record method, *route template*, status and duration. Raw paths and query
  strings are never logged (they may carry platform identifiers; spec §11).
- Metrics use a per-application registry so tests stay isolated. ``/metrics`` is served only
  to direct (non-proxied) callers; see :mod:`app.api.health`.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.logging_config import request_id_var
from app.version import BuildInfo

logger = logging.getLogger("investigators.request")
REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class Metrics:
    def __init__(self, build: BuildInfo) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.requests = Counter(
            "investigators_http_requests_total",
            "HTTP requests by route template and status class",
            ["method", "route", "status_class"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "investigators_http_request_duration_seconds",
            "HTTP request duration by route template",
            ["route"],
            registry=self.registry,
        )
        self.unhandled = Counter(
            "investigators_unhandled_exceptions_total",
            "Unhandled exceptions by exception type",
            ["exception"],
            registry=self.registry,
        )
        self.readiness_failures = Counter(
            "investigators_readiness_failures_total",
            "Readiness probe failures by reason",
            ["reason"],
            registry=self.registry,
        )
        self.persistence_unavailable = Counter(
            "investigators_persistence_unavailable_total",
            "Requests answered 503 because the database was unavailable",
            registry=self.registry,
        )
        info = Gauge(
            "investigators_build_info",
            "Build stamp (value is always 1)",
            ["service_version", "source_sha256"],
            registry=self.registry,
        )
        info.labels(build.service_version, build.source_sha256).set(1)


def _route_template(request: Request) -> str:
    """Route *template* (bounded label cardinality); read after the router has matched."""
    path = getattr(request.scope.get("route"), "path", None)
    return path if isinstance(path, str) else "unmatched"


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, metrics: Metrics) -> None:
        super().__init__(app)
        self._metrics = metrics

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        supplied = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = supplied if _VALID_REQUEST_ID.match(supplied) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        request.state.request_id = request_id
        started = time.perf_counter()
        status = 500
        try:
            try:
                response = await call_next(request)
            except Exception as exc:
                # Last-resort guard: never leak exception text to the client.
                self._metrics.unhandled.labels(type(exc).__name__).inc()
                logger.exception("unhandled exception", extra={"route": _route_template(request)})
                response = JSONResponse(
                    {"error": "internal_error", "request_id": request_id}, status_code=500
                )
            status = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            elapsed = time.perf_counter() - started
            route = _route_template(request)
            self._metrics.requests.labels(request.method, route, f"{status // 100}xx").inc()
            self._metrics.latency.labels(route).observe(elapsed)
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "route": route,
                    "status": status,
                    "duration_ms": round(elapsed * 1000, 1),
                },
            )
            request_id_var.reset(token)
