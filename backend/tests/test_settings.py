from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from app.redaction import redact
from app.settings import AppMode, ConfigurationError, Settings, load_settings

SECRET_DSN = (
    "postgresql://investigators_user:S3cretPassw0rd@host.docker.internal:5432/investigators_db"
)


def test_loads_required_values(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(ADMIN_EMAILS="admin1@university.edu, ADMIN2@university.edu")
    assert settings.APP_MODE is AppMode.PREVIEW
    assert settings.admin_emails == ("admin1@university.edu", "admin2@university.edu")
    assert settings.INVESTIGATORS_TEST_DATABASE_URL is None
    assert settings.INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS is False


def test_missing_required_configuration_names_fields_without_values() -> None:
    with pytest.raises(ConfigurationError) as info:
        load_settings(ADMIN_PASSWORD="hunter2-very-secret")
    message = str(info.value)
    assert "APP_MODE [missing]" in message
    assert "DATABASE_URL [missing]" in message
    assert "hunter2-very-secret" not in message


def test_invalid_values_are_never_echoed() -> None:
    with pytest.raises(ConfigurationError) as info:
        load_settings(APP_MODE="production", DATABASE_URL="mysql://root:TopSecret99@db/x")
    message = str(info.value)
    assert "TopSecret99" not in message
    assert "production" not in message
    assert "APP_MODE" in message and "DATABASE_URL" in message
    # The message stays informative after passing through the log redactor.
    assert "missing" in redact(message) or "invalid" in redact(message)


def test_mode_must_be_explicit_and_is_case_insensitive(
    make_settings: Callable[..., Settings],
) -> None:
    assert make_settings(APP_MODE=" QA ").APP_MODE is AppMode.QA
    with pytest.raises(ConfigurationError):
        make_settings(APP_MODE="")


def test_empty_admin_password_disables_admin_without_blocking(
    make_settings: Callable[..., Settings],
) -> None:
    assert make_settings(ADMIN_PASSWORD="").admin_enabled is False
    assert make_settings(ADMIN_PASSWORD="   ").admin_enabled is False
    assert make_settings(ADMIN_PASSWORD="long-enough-password").admin_enabled is True


def test_live_mode_rejects_placeholder_and_destructive_opt_in() -> None:
    placeholder = (
        "postgresql://investigators_user:CHANGE_ME@host.docker.internal:5432/investigators_db"
    )
    with pytest.raises(ConfigurationError, match="placeholder"):
        load_settings(APP_MODE="live", DATABASE_URL=placeholder)
    with pytest.raises(ConfigurationError, match="destructive"):
        load_settings(
            APP_MODE="live", DATABASE_URL=SECRET_DSN, INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS="1"
        )
    assert load_settings(APP_MODE="preview", DATABASE_URL=placeholder).database_placeholder


def test_empty_test_dsn_is_treated_as_unset(make_settings: Callable[..., Settings]) -> None:
    assert make_settings(INVESTIGATORS_TEST_DATABASE_URL="").INVESTIGATORS_TEST_DATABASE_URL is None


def test_bad_admin_email_list_rejected(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ConfigurationError, match="ADMIN_EMAILS"):
        make_settings(ADMIN_EMAILS="admin1@university.edu, not-an-email")


def test_describe_is_secret_free(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(DATABASE_URL=SECRET_DSN, ADMIN_PASSWORD="admin-pass-value")
    text = repr(settings.describe()) + repr(settings)
    assert "S3cretPassw0rd" not in text
    assert "admin-pass-value" not in text


def test_loaded_secrets_are_registered_for_redaction(
    make_settings: Callable[..., Settings],
) -> None:
    make_settings(DATABASE_URL=SECRET_DSN, ADMIN_PASSWORD="admin-pass-value")
    assert "S3cretPassw0rd" not in redact("connect failed with password S3cretPassw0rd")
    assert "admin-pass-value" not in redact("oops admin-pass-value")


def test_env_file_loading_and_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / "service.env"
    env_file.write_text(
        f"APP_MODE=qa\nDATABASE_URL={SECRET_DSN}\nLOG_LEVEL=debug\nUNRELATED=1\n", encoding="utf-8"
    )
    monkeypatch.setenv("INVESTIGATORS_ENV_FILE", str(env_file))
    assert load_settings().APP_MODE is AppMode.QA
    monkeypatch.setenv("APP_MODE", "preview")
    settings = load_settings()
    assert settings.APP_MODE is AppMode.PREVIEW  # real environment wins over the file
    assert settings.LOG_LEVEL == "DEBUG"


def test_missing_env_file_fails_safely(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("INVESTIGATORS_ENV_FILE", str(tmp_path / "absent.env"))
    with pytest.raises(ConfigurationError, match="missing file"):
        load_settings()
