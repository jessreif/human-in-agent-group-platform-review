"""INV-0103: `.env.example` and the typed settings agree; the template holds no secret."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.settings import ConfigurationError, Settings, load_settings

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / ".env.example"


def _example() -> dict[str, str]:
    values = {}
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


# Read by operator scripts, the worker, or the demo tooling, not by Settings.
_NON_SETTINGS_KEYS = frozenset(
    {
        "PROLIFIC_API_TOKEN",
        "PROLIFIC_COMPLETION_URL",
        "TEST_URL_CONNECT",
        "TEST_URL_PROLIFIC",
        "INVESTIGATORS_MIGRATION_DATABASE_URL",
        "INVESTIGATORS_BUILD_SOURCE_SHA256",
        "INVESTIGATORS_BEHAVIOR_SOURCE_SHA256",
        "INVESTIGATORS_BUILD_TIME",
        "INVESTIGATORS_IMAGE_REF",
        "INVESTIGATORS_WORKER_HEARTBEAT",
        "INVESTIGATORS_E2E",
        "PLAYWRIGHT_BROWSERS_PATH",
    }
)


def test_every_template_key_is_a_setting_and_required_ones_are_present() -> None:
    keys = set(_example())
    fields = set(Settings.model_fields)
    extra = keys - fields
    assert extra <= _NON_SETTINGS_KEYS, extra - _NON_SETTINGS_KEYS
    required = {name for name, field in Settings.model_fields.items() if field.is_required()}
    assert required <= keys


def test_template_contains_no_secret() -> None:
    values = _example()
    assert values["ADMIN_PASSWORD"] == ""
    assert "CHANGE_ME" in values["DATABASE_URL"]
    assert values["INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS"] == "0"


def test_template_loads_in_preview_and_is_rejected_in_live(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _example():
        monkeypatch.delenv(key, raising=False)
    settings = load_settings(env_file=EXAMPLE)
    assert settings.APP_MODE.value == "preview"
    assert settings.admin_enabled is False
    assert settings.database_placeholder is True
    assert settings.admin_emails == ("admin1@university.edu", "admin2@university.edu")
    monkeypatch.setenv("APP_MODE", "live")
    with pytest.raises(ConfigurationError, match="placeholder"):
        load_settings(env_file=EXAMPLE)
