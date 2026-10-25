"""Shared fixtures.

Unit tests never read the real service ``.env`` and never touch a real database: an autouse
fixture strips every Investigators setting from the environment, and the default DSN points at
a closed local port so readiness deterministically reports ``database_unavailable``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_settings import EnvSettingsSource

from app.main import create_app
from app.redaction import clear_registered_secrets
from app.settings import Settings, load_settings
from app.timing.clock import VirtualClock

# Snapshot before any fixture mutates the environment (used by integration fixtures).
# clean_env deletes keys from the process environ only; it must not mutate this dict.
ORIGINAL_ENV: dict[str, str] = dict(os.environ)

# os.environ readers that are not Settings fields. Field names are not copied here;
# settings_field_env_names() derives those at fixture setup.
AUXILIARY_ENV_NAMES: frozenset[str] = frozenset(
    {
        "INVESTIGATORS_ENV_FILE",
        "INVESTIGATORS_BUILD_SOURCE_SHA256",
        "INVESTIGATORS_BUILD_TIME",
        "INVESTIGATORS_IMAGE_REF",
        "INVESTIGATORS_WORKER_HEARTBEAT",
        "INVESTIGATORS_CONTENT_DIR",
        "INVESTIGATORS_EXTRA_LIVE_DSNS",
        "INVESTIGATORS_LIVE_ENV_FILE",
        "INVESTIGATORS_MIGRATION_DATABASE_URL",
    }
)
_WORKER_TEST_DSN_PREFIX = "INVESTIGATORS_TEST_DATABASE_URL_GW"
_TEST_DATABASE_URL = "INVESTIGATORS_TEST_DATABASE_URL"
UNREACHABLE_DSN = (
    "postgresql://unit_user:unit-test-password-123@127.0.0.1:1/investigators_unit_test"
)


def settings_field_env_names() -> dict[str, frozenset[str]]:
    """Env names Settings reads for each model field (prefix, alias, and case).

    ``EnvSettingsSource._extract_field_info`` is the resolver ``load_settings``
    uses. Current config is case-sensitive with an empty prefix and no aliases,
    so each env name equals the field name.
    """
    source = EnvSettingsSource(Settings)
    return {
        field_name: frozenset(
            env_name
            for _field_key, env_name, _is_complex in source._extract_field_info(field, field_name)
        )
        for field_name, field in Settings.model_fields.items()
    }


def stripped_env_names() -> frozenset[str]:
    """Process-environ names ``clean_env`` deletes, except ``*_GW*`` worker DSNs."""
    names: set[str] = set()
    for env_names in settings_field_env_names().values():
        names.update(env_names)
    names.update(AUXILIARY_ENV_NAMES)
    names.add(_TEST_DATABASE_URL)
    return frozenset(names)


def _environ_keys_to_strip() -> list[str]:
    """Keys to delete from ``os.environ``. Does not read or write ``ORIGINAL_ENV``."""
    wanted = stripped_env_names()
    case_sensitive = bool(Settings.model_config.get("case_sensitive", False))
    if case_sensitive:
        keys = set(wanted)
        keys.update(key for key in os.environ if key.startswith(_WORKER_TEST_DSN_PREFIX))
        return sorted(keys)
    folded = {name.casefold() for name in wanted}
    prefix = _WORKER_TEST_DSN_PREFIX.casefold()
    return sorted(
        key for key in os.environ if key.casefold() in folded or key.casefold().startswith(prefix)
    )


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Derived at setup from Settings.model_fields, not a hand-maintained field list.
    for key in _environ_keys_to_strip():
        monkeypatch.delenv(key, raising=False)
    clear_registered_secrets()
    yield
    clear_registered_secrets()


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    def _make(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "APP_MODE": "preview",
            "DATABASE_URL": UNREACHABLE_DSN,
            "READINESS_DB_TIMEOUT_SECONDS": 1,
        }
        values.update(overrides)
        return load_settings(**values)

    return _make


@pytest.fixture
def virtual_clock() -> VirtualClock:
    return VirtualClock()


@pytest.fixture
def app(make_settings: Callable[..., Settings], virtual_clock: VirtualClock) -> FastAPI:
    return create_app(make_settings(), clock=virtual_clock, configure_logs=False)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
