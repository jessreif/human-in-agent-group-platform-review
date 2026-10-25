"""INV-0106: Compose/container definitions keep the agreed boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_TEXT = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
COMPOSE: dict[str, Any] = yaml.safe_load(COMPOSE_TEXT)
# Explanatory comments may name forbidden ports; only configuration lines are checked.
COMPOSE_CONFIG = "\n".join(
    line.split(" #")[0] for line in COMPOSE_TEXT.splitlines() if not line.lstrip().startswith("#")
)
SERVICES: dict[str, Any] = COMPOSE["services"]
EXPECTED_PORTS = {"investigators-api": ["127.0.0.1:8519:8000"], "investigators-ui": ["127.0.0.1:8518:8080"]}
# INV-1001 model sidecar: the worker shares this container's netns so the approved
# provider URL is literal loopback; it owns the only sanctioned volume (model store).
APP_SERVICES = {"investigators-api", "investigators-worker", "investigators-ui"}
OLLAMA = "investigators-ollama"
# One-shot warm-loader (2026-10-03): joins the ollama netns for the same reason the
# worker does — the server binds loopback only inside that netns, so the API is
# otherwise unreachable. It publishes nothing and exits after pinning the model.
OLLAMA_NETNS_GUESTS = {"investigators-worker", "investigators-ollama-warmup"}


def test_service_and_container_names() -> None:
    assert COMPOSE["name"] == "investigators"
    assert set(SERVICES) == APP_SERVICES | {OLLAMA, "investigators-ollama-warmup"}
    for name, service in SERVICES.items():
        assert service["container_name"] == name


def test_only_loopback_8518_8519_are_published() -> None:
    for name, service in SERVICES.items():
        assert service.get("ports", []) == EXPECTED_PORTS.get(name, []), name
    for forbidden in ("8111", "8112", "0.0.0.0"):
        assert forbidden not in COMPOSE_CONFIG
    # The only permitted network_mode is a guest joining the ollama netns.
    for name in OLLAMA_NETNS_GUESTS:
        assert SERVICES[name].get("network_mode") == "service:investigators-ollama"
    for name, service in SERVICES.items():
        if name not in OLLAMA_NETNS_GUESTS:
            assert "network_mode" not in service, name


def test_dct_pattern_env_hosts_restart_health() -> None:
    for name in APP_SERVICES:
        service = SERVICES[name]
        assert service["restart"] == "unless-stopped", name
        assert service["healthcheck"]["test"][0] == "CMD", name
        assert service["read_only"] is True
        assert "ALL" in service["cap_drop"]
    for name in ("investigators-api", "investigators-worker"):
        assert SERVICES[name]["env_file"] == [".env"]
    # host.docker.internal mapping lives on the api directly; the worker inherits it
    # through the shared ollama netns (its own /etc/hosts is the sidecar's).
    assert SERVICES["investigators-api"]["extra_hosts"] == ["host.docker.internal:host-gateway"]
    assert "extra_hosts" not in SERVICES["investigators-worker"]
    assert "env_file" not in SERVICES["investigators-ui"]  # static UI gets no secrets
    assert "environment" not in SERVICES["investigators-ui"]
    # The model sidecar: no env_file (no secrets), its OLLAMA_* env is declared inline,
    # and it carries the host.docker.internal mapping the worker's netns relies on.
    ollama = SERVICES[OLLAMA]
    assert ollama["restart"] == "unless-stopped"
    assert "healthcheck" in ollama
    assert "env_file" not in ollama
    assert ollama["extra_hosts"] == ["host.docker.internal:host-gateway"]


def test_no_mounts_of_secrets_ingress_or_other_apps() -> None:
    # The API's mounts are the read-only docs root for /admin/docs (repo tree, .env
    # masked by a /dev/null shadow mount) plus the export output root ADMIN_EXPORT_DIR
    # writes to — a dedicated host dir outside the repo (read_only fs, so exports
    # cannot land on the container layer or under /srv/docs). Everything else stays
    # mount-free.
    assert SERVICES["investigators-api"]["volumes"] == [
        "./:/srv/docs:ro",
        "/dev/null:/srv/docs/.env:ro",
        "./local-data/exports:/srv/exports",
    ]
    for name in APP_SERVICES - {"investigators-api"}:
        assert "volumes" not in SERVICES[name], name
    # The sidecar's only mounts are the local model store and the clock.
    assert SERVICES[OLLAMA]["volumes"] == [
        "./local-data/ollama-models:/opt/ollama/models",
        "/etc/localtime:/etc/localtime:ro",
    ]
    assert "/etc/httpd" not in COMPOSE_CONFIG
    assert "study_admin_common" not in COMPOSE_CONFIG


def test_builds_use_declared_roots_and_pinned_bases() -> None:
    assert SERVICES["investigators-api"]["build"]["context"] == "./backend"
    assert SERVICES["investigators-ui"]["build"]["context"] == "./frontend"
    for service in SERVICES.values():
        for key, value in service.get("build", {}).get("args", {}).items():
            if key.endswith("_IMAGE"):
                assert "@sha256:" in value, key
    # Pull-based images are digest-pinned too.
    assert "@sha256:" in SERVICES[OLLAMA]["image"]


def test_dockerignore_excludes_env_and_dockerfiles_never_copy_it() -> None:
    for root in ("backend", "frontend"):
        ignore = (ROOT / root / ".dockerignore").read_text(encoding="utf-8").splitlines()
        assert ".env" in ignore and "**/.env" in ignore
        dockerfile = (ROOT / root / "Dockerfile").read_text(encoding="utf-8")
        assert ".env" not in dockerfile
        assert "USER" in dockerfile or "nginx-unprivileged" in dockerfile


def test_ui_nginx_serves_prefix_free_and_prefixed_paths() -> None:
    conf = (ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    assert "listen 8080;" in conf
    assert "rewrite ^/investigators/(.*)$ /$1 last;" in conf
    assert "try_files $uri /index.html;" in conf


def test_smoke_stack_derives_migration_head() -> None:
    """BA-02: the /ready smoke assertion must compare against the image's own
    expected_schema_head() — a hardcoded revision literal goes stale on every new
    migration and silently passes nothing."""
    script = (ROOT / "scripts" / "smoke_stack.sh").read_text(encoding="utf-8")
    assert "EXPECTED_HEAD" in script
    assert "expected_schema_head" in script
    import re

    assert not re.search(r'"current":"[a-z0-9_]+"', script), (
        "smoke_stack.sh must not hardcode a migration revision"
    )
