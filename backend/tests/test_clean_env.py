"""TS-A06: clean_env strips every Settings env var without touching ORIGINAL_ENV."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from app.settings import AppMode, Settings, load_settings
from tests.conftest import (
    AUXILIARY_ENV_NAMES,
    ORIGINAL_ENV,
    UNREACHABLE_DSN,
    settings_field_env_names,
    stripped_env_names,
)

# Import-time copy. Equality checks report key names only, never values.
_ORIGINAL_ENV_ID = id(ORIGINAL_ENV)
_ORIGINAL_KEYS = tuple(ORIGINAL_ENV)
_ORIGINAL_VALUES = {key: ORIGINAL_ENV[key] for key in _ORIGINAL_KEYS}

# (field, why it is not an environment variable). Empty: Settings reads every
# model field from the environment (case_sensitive=True, env_prefix="").
# An entry added only to avoid stripping a new field fails the parity test.
ENV_FIELD_ALLOWLIST: tuple[tuple[str, str], ...] = ()

# Obviously fake. Not secrets, and not a live provider.
_FAKE_VALUES = {
    "MODEL_PROVIDER": "none",
    "COMPLETION_CODE": "AUDIT_SYNTHETIC",
    "ADMIN_SESSION_TTL_SECONDS": "60",
    "ADMIN_EXPORT_DIR": "/tmp/investigators-audit-synthetic-export",
    "OPENAI_BASE_URL": "https://audit.invalid/v1",
    "INVESTIGATORS_ENV_FILE": "/tmp/investigators-audit-missing.env",
    "INVESTIGATORS_BUILD_SOURCE_SHA256": "audit-synthetic-sha",
    "INVESTIGATORS_BUILD_TIME": "1970-01-01T00:00:00Z",
    "INVESTIGATORS_IMAGE_REF": "audit.invalid/investigators:synthetic",
    "INVESTIGATORS_WORKER_HEARTBEAT": "/tmp/investigators-audit-heartbeat.json",
    "INVESTIGATORS_CONTENT_DIR": "/tmp/investigators-audit-content",
    "INVESTIGATORS_EXTRA_LIVE_DSNS": "audit-synthetic-not-a-dsn",
    "INVESTIGATORS_LIVE_ENV_FILE": "/tmp/investigators-audit-live.env",
    "INVESTIGATORS_MIGRATION_DATABASE_URL": "audit-synthetic-not-a-dsn",
    "INVESTIGATORS_TEST_DATABASE_URL": UNREACHABLE_DSN,
    "INVESTIGATORS_TEST_DATABASE_URL_GW0": UNREACHABLE_DSN,
    "INVESTIGATORS_TEST_DATABASE_URL_GW1": UNREACHABLE_DSN,
}


@pytest.fixture(scope="module")
def ambient_contamination() -> Iterator[tuple[str, ...]]:
    """Put fakes in the process environ before the function-scoped clean_env."""
    saved = {key: os.environ.get(key) for key in _FAKE_VALUES}
    os.environ.update(_FAKE_VALUES)
    try:
        yield tuple(_FAKE_VALUES)
    finally:
        for key, previous in saved.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def test_env_names_match_field_names() -> None:
    """Mapping: each field's env var is the field name.

    ``Settings.model_config`` has ``case_sensitive=True``, ``env_prefix=""``,
    and ``env_prefix_target="variable"``. No field sets a validation alias.
    If a prefix or alias is introduced, record the new field->env mapping in
    this test. Do not hide the field on ``ENV_FIELD_ALLOWLIST``.
    """
    assert Settings.model_config.get("env_prefix", "") == ""
    assert Settings.model_config.get("case_sensitive") is True
    assert Settings.model_config.get("env_prefix_target", "variable") == "variable"
    expected = {name: frozenset((name,)) for name in Settings.model_fields}
    assert settings_field_env_names() == expected


def test_every_model_field_is_stripped_or_allowlisted() -> None:
    mapping = settings_field_env_names()
    stripped = stripped_env_names()
    allow = dict(ENV_FIELD_ALLOWLIST)
    assert len(allow) == len(ENV_FIELD_ALLOWLIST)
    assert set(allow) <= set(Settings.model_fields)
    for field_name, reason in ENV_FIELD_ALLOWLIST:
        assert reason.strip()
        # Allowlisted only when the field is not env-backed. A field that still
        # has an env name must be stripped; allowlisting it is not an escape.
        assert mapping[field_name] == frozenset()
        assert mapping[field_name].isdisjoint(stripped)
    assert (set(mapping) - set(allow)) | set(allow) == set(Settings.model_fields)
    for field_name, env_names in mapping.items():
        if field_name in allow:
            continue
        assert env_names
        assert env_names <= stripped
    for name in AUXILIARY_ENV_NAMES:
        assert name in stripped
    assert "INVESTIGATORS_TEST_DATABASE_URL" in stripped


def test_clean_env_does_not_mutate_original_env(
    ambient_contamination: tuple[str, ...],
) -> None:
    assert ambient_contamination
    assert id(ORIGINAL_ENV) == _ORIGINAL_ENV_ID
    assert tuple(ORIGINAL_ENV) == _ORIGINAL_KEYS
    changed = [key for key in _ORIGINAL_KEYS if ORIGINAL_ENV[key] != _ORIGINAL_VALUES[key]]
    extra = [key for key in ORIGINAL_ENV if key not in _ORIGINAL_VALUES]
    assert changed == []
    assert extra == []
    # Comparison DSNs stay in the import snapshot even though the process env lost them.
    was_set = "INVESTIGATORS_TEST_DATABASE_URL" in _ORIGINAL_VALUES
    assert ("INVESTIGATORS_TEST_DATABASE_URL" in ORIGINAL_ENV) is was_set


def test_clean_env_hides_ambient_settings_from_load_settings(
    ambient_contamination: tuple[str, ...],
) -> None:
    leaked = [key for key in ambient_contamination if key in os.environ]
    assert leaked == []
    settings = load_settings(APP_MODE="preview", DATABASE_URL=UNREACHABLE_DSN)
    assert settings.MODEL_PROVIDER == "auto"
    assert settings.COMPLETION_CODE == "PLACEHOLDER-CODE"
    assert settings.ADMIN_SESSION_TTL_SECONDS == 3600
    assert settings.ADMIN_EXPORT_DIR == ""
    assert settings.OPENAI_BASE_URL == "https://api.openai.com/v1"
    assert settings.INVESTIGATORS_TEST_DATABASE_URL is None


def test_explicit_load_settings_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_MODE", "qa")
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    monkeypatch.setenv("COMPLETION_CODE", "AUDIT_SYNTHETIC")
    monkeypatch.setenv("ADMIN_SESSION_TTL_SECONDS", "60")
    monkeypatch.setenv("ADMIN_EXPORT_DIR", "/tmp/investigators-audit-synthetic-export")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://audit.invalid/v1")
    settings = load_settings(
        APP_MODE="preview",
        DATABASE_URL=UNREACHABLE_DSN,
        MODEL_PROVIDER="off",
        COMPLETION_CODE="OVERRIDE_WINS",
        ADMIN_SESSION_TTL_SECONDS=120,
        ADMIN_EXPORT_DIR="/tmp/investigators-override-wins",
        OPENAI_BASE_URL="https://override.invalid/v1",
    )
    assert settings.APP_MODE is AppMode.PREVIEW
    assert settings.MODEL_PROVIDER == "off"
    assert settings.COMPLETION_CODE == "OVERRIDE_WINS"
    assert settings.ADMIN_SESSION_TTL_SECONDS == 120
    assert settings.ADMIN_EXPORT_DIR == "/tmp/investigators-override-wins"
    assert settings.OPENAI_BASE_URL == "https://override.invalid/v1"
