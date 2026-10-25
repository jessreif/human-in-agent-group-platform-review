"""Single backend application factory (INV-0101).

Run with ``uvicorn app.main:create_app --factory``. Nothing is configured at import time, so
tests can build isolated apps with explicit settings, clocks and engines.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from app.api.admin import AdminProblem, admin_problem_handler
from app.api.admin import router as admin_router
from app.api.health import router as health_router
from app.api.participant import router as participant_router
from app.db.engine import make_engine
from app.db.tx import PersistenceUnavailableError
from app.domain.manifests.store import ContentStore
from app.logging_config import configure_logging
from app.observability import Metrics, RequestContextMiddleware
from app.settings import AppMode, ConfigurationError, Settings, load_settings
from app.timing.clock import Clock, SystemClock, ensure_clock_allowed
from app.version import build_info

EXTERNAL_ROOT_PATH = "/investigators/api"
logger = logging.getLogger("investigators.api")


def create_app(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    engine: Engine | None = None,
    configure_logs: bool = True,
) -> FastAPI:
    settings = settings or load_settings()
    clock = clock or SystemClock()
    ensure_clock_allowed(settings.APP_MODE.value, clock)
    if configure_logs:
        configure_logging("investigators-api", settings.LOG_LEVEL)
    build = build_info()
    # Fail closed at boot, not at a participant's consent click: live arm
    # allocation requires the pinned source-bundle checksum (compat.py), so an
    # unstamped image must never serve a live deployment. Build via
    # scripts/build_images.sh — bare `docker compose build` leaves this unset.
    if settings.APP_MODE is AppMode.LIVE and not build.pinned:
        raise ConfigurationError(
            "APP_MODE=live requires INVESTIGATORS_BUILD_SOURCE_SHA256 pinned by "
            "scripts/build_images.sh (got 'unset')"
        )
    metrics = Metrics(build)
    engine = engine or make_engine(
        settings.DATABASE_URL.get_secret_value(),
        connect_timeout_seconds=settings.READINESS_DB_TIMEOUT_SECONDS,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "startup",
            extra={"settings": settings.describe(), "source_sha256": build.source_sha256},
        )
        if not settings.admin_enabled:
            logger.warning("admin disabled: ADMIN_PASSWORD is empty (fail closed)")
        try:
            yield
        finally:
            engine.dispose()
            logger.info("shutdown")

    app = FastAPI(
        title="Investigators API",
        version=build.service_version,
        root_path=EXTERNAL_ROOT_PATH,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.clock = clock
    app.state.engine = engine
    app.state.metrics = metrics
    app.state.build = build
    app.state.content_store = ContentStore.load_default()

    async def persistence_unavailable(request: Request, exc: Exception) -> JSONResponse:
        metrics.persistence_unavailable.inc()
        logger.error("persistence unavailable", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            {"error": "persistence_unavailable", "request_id": request.state.request_id},
            status_code=503,
        )

    # Never report Saved/Completed when the commit failed (spec §8.2/§8.3).
    app.add_exception_handler(OperationalError, persistence_unavailable)
    app.add_exception_handler(PersistenceUnavailableError, persistence_unavailable)
    # Admin guards raise a ready problem response (audit M3).
    app.add_exception_handler(AdminProblem, admin_problem_handler)

    app.add_middleware(RequestContextMiddleware, metrics=metrics)
    app.include_router(health_router)
    app.include_router(participant_router)
    # WP13: app-enforced admin surface, behind Apache's Shibboleth gate on admin paths; the
    # router itself fails closed when ADMIN_PASSWORD is empty.
    app.include_router(admin_router)
    return app
