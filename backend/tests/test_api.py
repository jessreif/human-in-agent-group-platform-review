from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db.engine import expected_schema_head
from app.main import EXTERNAL_ROOT_PATH
from tests.conftest import UNREACHABLE_DSN


def test_root_path_matches_ingress(app: FastAPI) -> None:
    assert app.root_path == EXTERNAL_ROOT_PATH == "/investigators/api"


def test_health_is_liveness_only(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(response.headers["X-Request-ID"]) == 32


def test_well_formed_request_id_is_echoed_and_bad_one_replaced(client: TestClient) -> None:
    good = client.get("/health", headers={"X-Request-ID": "trace-1234abcd"})
    assert good.headers["X-Request-ID"] == "trace-1234abcd"
    bad = client.get("/health", headers={"X-Request-ID": "<script>"})
    assert bad.headers["X-Request-ID"] != "<script>"


def test_ready_reports_unavailable_database_without_details(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] == "unavailable"
    assert body["checks"]["schema"] == {
        "current": "unknown",
        "expected_head": expected_schema_head(),
    }
    text = response.text
    assert "unit-test-password-123" not in text
    assert "127.0.0.1" not in text
    assert UNREACHABLE_DSN not in text


def test_version_stamps(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    body = client.get("/version").json()
    assert body["service"] == "investigators-api"
    assert body["source_sha256"] == "unset"
    assert body["pinned_build"] is False
    assert body["schema_expected_head"] == expected_schema_head()
    assert body["app_mode"] == "preview"


def test_live_app_refuses_unpinned_build(
    make_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live must fail closed at boot: bare `docker compose build` leaves
    INVESTIGATORS_BUILD_SOURCE_SHA256 unset, and arm allocation (consent)
    requires the pinned checksum — refuse to serve rather than 500 mid-session."""
    monkeypatch.delenv("INVESTIGATORS_BUILD_SOURCE_SHA256", raising=False)
    from app.main import create_app
    from app.settings import ConfigurationError

    with pytest.raises(ConfigurationError, match="INVESTIGATORS_BUILD_SOURCE_SHA256"):
        create_app(make_settings(APP_MODE="live"), configure_logs=False)


def test_metrics_direct_only(client: TestClient) -> None:
    client.get("/health")
    client.get("/ready")
    direct = client.get("/metrics")
    assert direct.status_code == 200
    assert (
        'investigators_http_requests_total{method="GET",route="/health",status_class="2xx"}'
        in direct.text
    )
    assert 'investigators_readiness_failures_total{reason="database_unavailable"}' in direct.text
    assert "investigators_build_info" in direct.text
    proxied = client.get("/metrics", headers={"X-Forwarded-For": "152.3.1.1"})
    assert proxied.status_code == 404


def test_docs_and_openapi_are_disabled(client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_unhandled_errors_are_generic(app: FastAPI) -> None:
    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret detail postgresql://u:leak-me@h/db")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/boom")
        metrics = test_client.get("/metrics").text
    assert response.status_code == 500
    assert response.json()["error"] == "internal_error"
    assert "leak-me" not in response.text and "secret detail" not in response.text
    assert 'investigators_unhandled_exceptions_total{exception="RuntimeError"} 1.0' in metrics


def test_persistence_failures_map_to_503(app: FastAPI) -> None:
    @app.get("/db-down")
    async def db_down() -> None:
        raise OperationalError("SELECT 1", {}, Exception("password=leak-me"))

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/db-down")
    assert response.status_code == 503
    assert response.json()["error"] == "persistence_unavailable"
    assert "leak-me" not in response.text


def test_persistence_unavailable_maps_to_503(app: FastAPI) -> None:
    from app.db.tx import PersistenceUnavailableError

    @app.get("/not-saved")
    async def not_saved() -> None:
        raise PersistenceUnavailableError("database unavailable; nothing was saved")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/not-saved")
    assert response.status_code == 503
    assert response.json()["error"] == "persistence_unavailable"
