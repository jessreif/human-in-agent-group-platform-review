"""Typed runtime settings (INV-0103).

Values come from the process environment. Under Docker Compose that environment is populated
from ``./.env`` via ``env_file`` (the same pattern as ``pcs``/``dct``).
For host-side tools, set ``INVESTIGATORS_ENV_FILE`` to an env file path; real environment
variables still take precedence over the file.

Rules enforced here:
- Missing or invalid required configuration fails startup with a message that names the
  offending fields but never echoes their values.
- An empty ``ADMIN_PASSWORD`` disables admin (fail closed) instead of blocking startup.
- ``APP_MODE`` must be stated explicitly: ``preview``, ``qa`` or ``live``.
- Secrets are ``SecretStr`` and are registered with the log redactor at load time.
"""

from __future__ import annotations

import os
import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.flags import FeatureFlags
from app.redaction import register_secret

ENV_FILE_VARIABLE = "INVESTIGATORS_ENV_FILE"
PLACEHOLDER_MARKER = "CHANGE_ME"
_EMAIL_RE = re.compile(r"^[^@\s,]+@[^@\s,]+\.[^@\s,]+$")
_POSTGRES_SCHEMES = ("postgresql://", "postgresql+psycopg://", "postgres://")


class AppMode(StrEnum):
    PREVIEW = "preview"
    QA = "qa"
    LIVE = "live"


class ConfigurationError(RuntimeError):
    """Raised when required configuration is missing or invalid. Message is value-free."""


class ConnectCompletion(BaseModel):
    """Server-configured project handoff, never supplied by a participant URL."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    code: str = Field(default="", max_length=128, pattern=r"^[A-Za-z0-9_-]*$")
    url: str = Field(default="", max_length=2048)

    @model_validator(mode="after")
    def _valid_handoff(self) -> ConnectCompletion:
        if not self.code and not self.url:
            raise ValueError("Connect needs a completion code or URL")
        if self.url:
            parsed = urlsplit(self.url)
            if (
                parsed.scheme != "https"
                or parsed.netloc != "connect.cloudresearch.com"
                or not re.fullmatch(r"/participant/project/[A-Za-z0-9_-]+/complete/?", parsed.path)
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("expected a Connect HTTPS project completion URL")
        return self


class Settings(BaseSettings):
    """Runtime settings. Field names match the keys in ``.env.example``."""

    model_config = SettingsConfigDict(
        env_file=None,
        extra="ignore",
        case_sensitive=True,
        frozen=True,
        env_ignore_empty=False,
    )

    APP_MODE: AppMode
    DATABASE_URL: SecretStr
    # Admin role secrets (INV-1301, spec §11). ADMIN_PASSWORD is the researcher credential
    # AND the master admin gate: when it is empty the whole admin surface is disabled
    # (fail closed) regardless of the other role secrets. The operator and compensation
    # secrets independently enable those roles; a login claims a role and is checked only
    # against that role's secret, so compensation credentials can never open research views.
    ADMIN_PASSWORD: SecretStr = SecretStr("")
    ADMIN_OPERATOR_PASSWORD: SecretStr = SecretStr("")
    ADMIN_COMPENSATION_PASSWORD: SecretStr = SecretStr("")
    ADMIN_EMAILS: str = ""
    # Signed admin-session lifetime and login throttle (in-memory, per client+role).
    ADMIN_SESSION_TTL_SECONDS: int = Field(default=3600, ge=60, le=86400)
    ADMIN_LOGIN_MAX_ATTEMPTS: int = Field(default=5, ge=1, le=100)
    ADMIN_LOGIN_WINDOW_SECONDS: int = Field(default=300, ge=10, le=86400)
    # Directory the admin export queue writes into (INV-1308). Empty = exports return the
    # manifest summary without touching the filesystem.
    ADMIN_EXPORT_DIR: str = ""
    # Read-only docs root served by ``/admin/docs`` (researcher only). Files are resolved
    # against a fixed allowlist — never user paths. Empty = docs tab lists nothing.
    ADMIN_DOCS_DIR: str = ""
    INVESTIGATORS_TEST_DATABASE_URL: SecretStr | None = None
    INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS: bool = False
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    READINESS_DB_TIMEOUT_SECONDS: float = Field(default=2.0, gt=0, le=30)
    # HMAC key for signed preview descriptors (INV-0209/0406). Empty disables previews
    # entirely (fail closed); live deployments never accept previews regardless.
    PREVIEW_SIGNING_KEY: SecretStr = SecretStr("")
    # JSON object keyed by batch ID. Exposed only after successful completion.
    CONNECT_COMPLETION: dict[str, ConnectCompletion] = Field(default_factory=dict, repr=False)
    # Shared finish-screen code for all platforms and preview/QA sessions.
    # Connect routing still uses its per-batch URL. Empty uses the shared default;
    # unique receipt identifiers remain internal, and saved handoffs never change.
    COMPLETION_CODE: str = Field(default="PLACEHOLDER-CODE", max_length=128, pattern=r"^[A-Za-z0-9_-]*$")
    # INV-1001 approved local-provider endpoint (spec §7.4). Unset/empty keeps the worker
    # deterministic-only (provider=None, deterministic fallback — same as an outage).
    # When set it must be a plain-http loopback URL (enforced again by the adapter at
    # construction), LOCAL_MODEL_ID must pin the served model, and LOCAL_MODEL_DIGEST
    # must pin the served weight digest (BA-40 fail-closed: no unverified generation).
    LOCAL_MODEL_BASE_URL: str | None = None
    LOCAL_MODEL_ID: str = ""
    LOCAL_MODEL_DIGEST: str = ""
    LOCAL_MODEL_MAX_CONCURRENCY: int = Field(default=4, ge=1, le=64)
    # Provider selection flag. ``auto`` (default) keeps the historical behavior — the
    # pinned loopback adapter when LOCAL_MODEL_BASE_URL is set, else deterministic-only.
    # ``openai``/``minimax`` select the remote adapters (preview/qa only — remote
    # providers are refused under APP_MODE=live); ``none``/``off`` forces
    # deterministic-only even when endpoints are configured.
    MODEL_PROVIDER: Literal["auto", "ollama", "local", "openai", "minimax", "none", "off"] = "auto"
    # Teammate-mechanism feature flag (owner direction 2026-10-11): the scripted-
    # teammate manifest family — ``teammates.social_generation: deterministic_only``
    # — is excluded from live-mode fielding (live batches, live batch groups, and
    # live-session consent allocation all refuse it) unless this is enabled.
    # Default off = the AI-teammate-only design; set 1 to reactivate the scripted
    # arm. Preview/QA mints and sessions are unaffected, so scripted profiles can
    # still be previewed and sim-tested at any time.
    SCRIPTED_TEAMMATES_ENABLED: bool = False
    # AI-assistant feature flag: when 0 the assistant is removed from the
    # experiment for every mode — no assistant pane or practice page, the
    # ``assistant_prompting`` channel closes server-side (``assistant_request``
    # commands are rejected), and the assistant-specific comprehension /
    # post-task items and open-ended clause drop out of what participants must
    # answer. Default on; the implementation stays in place — set 1 to
    # reactivate it.
    AI_ASSISTANT_ENABLED: bool = True
    # Crate-overlap condition feature flag (2026-10-15): 0 = manifests whose
    # ``task.crate_overlap`` is a non-legacy level (none/some/full) are excluded
    # from live-mode fielding — live batch/group mint, enrollment-open, and
    # live-session consent allocation all refuse them. Preview/QA mints and
    # sessions are unaffected, so the condition profiles stay previewable and
    # sim-testable. Set 1 to field the overlap experiment.
    CRATE_OVERLAP_ENABLED: bool = False
    # Case-file-size condition feature flag (2026-10-15): 0 = manifests with
    # ``task.participant_intel_budget`` set (the thin ~20-record case file)
    # are excluded from live-mode fielding on the same four surfaces.
    # Preview/QA unaffected. Set 1 to field the intel-budget experiment.
    CRATE_NUMBER_ENABLED: bool = False
    # Time-pressure manipulation feature flag (2026-10-15): 0 removes the
    # pressure manipulation in every mode — no checkin job is scheduled (the
    # pop-up never opens), pressure_remark lines and the "One minute left"
    # warning are never delivered, low-time styling is suppressed (the
    # countdown stays as a neutral timer; the deadline still applies), and the
    # check-in follow-up items drop out of post_task. Set 0 to run without
    # time pressure.
    TIME_PRESSURE_ENABLED: bool = True
    # AI Q&A feature flag (2026-10-16): 1 inserts an optional "AI Q&A
    # (Optional)" rules-Q&A page between the comprehension check and the
    # connecting screen — participants may ask free-text questions about the
    # task rules (local-model answers, deterministic fallback on failure,
    # exchanges stored in ``app.qa_exchange``). Continue is always active.
    # 0 = the training-to-connecting flow runs unchanged. Default off.
    AI_QA_ENABLED: bool = False
    # Bonus-payment feature flag (2026-10-16): 0 removes the $0.50 accuracy
    # bonus from the experiment in every mode — the consent stage serves the
    # ``consent.reference.nobonus`` artifact (bonus-eligibility sentence
    # removed), the background/gameplay/debrief bonus lines drop out of the
    # rendered copy, and entitlements compute under ``payment.s5_nobonus``
    # ($3.00 flat). Session records carry what was actually served/paid.
    # Default on — the bonus offer stays; set 0 to field the no-bonus posture.
    BONUS_PAYMENT_ENABLED: bool = True
    OPENAI_API_KEY: SecretStr = SecretStr("")
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL_ID: str = ""
    MINIMAX_API_KEY: SecretStr = SecretStr("")
    MINIMAX_BASE_URL: str = "https://api.minimax.io/anthropic"
    MINIMAX_MODEL_ID: str = ""

    @field_validator("LOCAL_MODEL_BASE_URL", mode="before")
    @classmethod
    def _empty_model_url_is_none(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value.strip() if isinstance(value, str) else value

    @field_validator("LOCAL_MODEL_DIGEST", mode="before")
    @classmethod
    def _digest_shape(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        digest = value.strip().lower().removeprefix("sha256:")
        if digest and not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("must be a sha256 hex digest (optionally 'sha256:'-prefixed)")
        return digest

    @field_validator("INVESTIGATORS_TEST_DATABASE_URL", mode="before")
    @classmethod
    def _empty_test_dsn_is_none(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def _upper_log_level(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("APP_MODE", mode="before")
    @classmethod
    def _lower_mode(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("DATABASE_URL", "INVESTIGATORS_TEST_DATABASE_URL")
    @classmethod
    def _postgres_only(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        raw = value.get_secret_value().strip()
        if not raw.startswith(_POSTGRES_SCHEMES):
            raise ValueError("must be a PostgreSQL DSN (postgresql://...)")
        return SecretStr(raw)

    @field_validator("ADMIN_EMAILS")
    @classmethod
    def _valid_emails(cls, value: str) -> str:
        for email in (e.strip() for e in value.split(",")):
            if email and not _EMAIL_RE.match(email):
                raise ValueError("must be a comma-separated list of e-mail addresses")
        return value

    @model_validator(mode="after")
    def _mode_rules(self) -> Settings:
        if self.APP_MODE is AppMode.LIVE:
            if PLACEHOLDER_MARKER in self.DATABASE_URL.get_secret_value():
                raise ValueError(
                    "DATABASE_URL still contains the template placeholder in live mode"
                )
            if self.INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS:
                raise ValueError("destructive database tests can never be enabled in live mode")
        if self.LOCAL_MODEL_BASE_URL is not None and not self.LOCAL_MODEL_ID.strip():
            raise ValueError("LOCAL_MODEL_ID is required when LOCAL_MODEL_BASE_URL is set")
        # BA-40: served-weight verification is unconditional — when the local
        # provider would actually be selected, a missing digest pin fails closed
        # rather than serving unverified generations.
        if (
            self.LOCAL_MODEL_BASE_URL is not None
            and self.MODEL_PROVIDER in ("auto", "ollama", "local")
            and not self.LOCAL_MODEL_DIGEST
        ):
            raise ValueError("LOCAL_MODEL_DIGEST is required when LOCAL_MODEL_BASE_URL is set")
        if self.MODEL_PROVIDER == "openai":
            if not self.OPENAI_API_KEY.get_secret_value().strip():
                raise ValueError("OPENAI_API_KEY is required when MODEL_PROVIDER=openai")
            if not self.OPENAI_MODEL_ID.strip():
                raise ValueError("OPENAI_MODEL_ID is required when MODEL_PROVIDER=openai")
        if self.MODEL_PROVIDER == "minimax":
            if not self.MINIMAX_API_KEY.get_secret_value().strip():
                raise ValueError("MINIMAX_API_KEY is required when MODEL_PROVIDER=minimax")
            if not self.MINIMAX_MODEL_ID.strip():
                raise ValueError("MINIMAX_MODEL_ID is required when MODEL_PROVIDER=minimax")
        return self

    # ----- derived, value-free views -------------------------------------------------------
    @property
    def admin_enabled(self) -> bool:
        """The admin surface exists only while the researcher secret is set (fail closed)."""
        return bool(self.ADMIN_PASSWORD.get_secret_value().strip())

    def admin_role_enabled(self, role: str) -> bool:
        """Whether ``role`` may log in. Unknown roles and a disabled admin both fail closed."""
        if not self.admin_enabled:
            return False
        return bool(self.admin_role_secret(role))

    def admin_role_secret(self, role: str) -> str:
        """The configured plaintext secret for ``role`` ('' if unset or unknown)."""
        field = {
            "researcher": self.ADMIN_PASSWORD,
            "operator": self.ADMIN_OPERATOR_PASSWORD,
            "compensation": self.ADMIN_COMPENSATION_PASSWORD,
        }.get(role)
        return "" if field is None else field.get_secret_value().strip()

    @property
    def admin_emails(self) -> tuple[str, ...]:
        return tuple(e.strip().lower() for e in self.ADMIN_EMAILS.split(",") if e.strip())

    @property
    def database_placeholder(self) -> bool:
        return PLACEHOLDER_MARKER in self.DATABASE_URL.get_secret_value()

    def flags(self) -> FeatureFlags:
        """The deployment feature flags as one typed value — pass this down,
        never the individual ``*_ENABLED`` settings (audit H4)."""
        return FeatureFlags(
            scripted_teammates=self.SCRIPTED_TEAMMATES_ENABLED,
            ai_assistant=self.AI_ASSISTANT_ENABLED,
            crate_overlap=self.CRATE_OVERLAP_ENABLED,
            crate_number=self.CRATE_NUMBER_ENABLED,
            time_pressure=self.TIME_PRESSURE_ENABLED,
            ai_qa=self.AI_QA_ENABLED,
            bonus_payment=self.BONUS_PAYMENT_ENABLED,
        )

    def feature_flags(self) -> dict[str, bool]:
        """The deployment feature-flag posture — the gates an analyst needs to
        know when interpreting an export. Recorded verbatim in ``session_opened``
        payloads at entry and stamped into every export bundle."""
        return self.flags().as_dict()

    def describe(self) -> dict[str, Any]:
        """A log-safe summary. Never includes secret values or DSN credentials."""
        return {
            "app_mode": self.APP_MODE.value,
            "admin_enabled": self.admin_enabled,
            "admin_roles_enabled": sorted(
                r for r in ("researcher", "operator", "compensation") if self.admin_role_enabled(r)
            ),
            "admin_email_count": len(self.admin_emails),
            "admin_export_dir_configured": bool(self.ADMIN_EXPORT_DIR.strip()),
            "database_configured": not self.database_placeholder,
            "test_database_configured": self.INVESTIGATORS_TEST_DATABASE_URL is not None,
            "destructive_db_tests_allowed": self.INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS,
            "preview_signing_configured": bool(self.PREVIEW_SIGNING_KEY.get_secret_value().strip()),
            "local_model_configured": self.LOCAL_MODEL_BASE_URL is not None,
            "local_model_max_concurrency": self.LOCAL_MODEL_MAX_CONCURRENCY,
            "model_provider": self.MODEL_PROVIDER,
            "scripted_teammates_enabled": self.SCRIPTED_TEAMMATES_ENABLED,
            "ai_assistant_enabled": self.AI_ASSISTANT_ENABLED,
            "crate_overlap_enabled": self.CRATE_OVERLAP_ENABLED,
            "crate_number_enabled": self.CRATE_NUMBER_ENABLED,
            "time_pressure_enabled": self.TIME_PRESSURE_ENABLED,
            "ai_qa_enabled": self.AI_QA_ENABLED,
            "bonus_payment_enabled": self.BONUS_PAYMENT_ENABLED,
            "remote_model_configured": self.MODEL_PROVIDER in ("openai", "minimax"),
            "log_level": self.LOG_LEVEL,
        }


def _register_secrets(settings: Settings) -> None:
    from app.db.dsn import parse_dsn

    register_secret(settings.ADMIN_PASSWORD.get_secret_value())
    register_secret(settings.ADMIN_OPERATOR_PASSWORD.get_secret_value())
    register_secret(settings.ADMIN_COMPENSATION_PASSWORD.get_secret_value())
    register_secret(settings.PREVIEW_SIGNING_KEY.get_secret_value())
    register_secret(settings.OPENAI_API_KEY.get_secret_value())
    register_secret(settings.MINIMAX_API_KEY.get_secret_value())
    for dsn in (settings.DATABASE_URL, settings.INVESTIGATORS_TEST_DATABASE_URL):
        if dsn is None:
            continue
        register_secret(dsn.get_secret_value())
        password = parse_dsn(dsn.get_secret_value()).password
        if password:
            register_secret(password)


def _format_validation_error(exc: ValidationError) -> str:
    problems = []
    for error in exc.errors(include_url=False, include_input=False, include_context=False):
        field = ".".join(str(part) for part in error.get("loc", ())) or "settings"
        kind = error.get("type", "invalid")
        detail = "missing" if kind == "missing" else f"invalid ({kind})"
        if kind == "value_error":
            # Our own validator messages are value-free by construction.
            detail = f"invalid: {error.get('msg', '').removeprefix('Value error, ')}"
        # "FIELD [detail]" rather than "FIELD: detail" so the log redactor's key=value rule
        # does not blank the explanation for secret-named fields such as DATABASE_URL.
        problems.append(f"{field} [{detail}]")
    return "Invalid Investigators configuration -> " + "; ".join(problems)


def load_settings(env_file: str | Path | None = None, **overrides: Any) -> Settings:
    """Load settings, raising :class:`ConfigurationError` with a value-free message on failure.

    ``env_file`` defaults to ``$INVESTIGATORS_ENV_FILE`` (unset: environment only).
    ``overrides`` are for tests and take precedence over the environment.
    """
    if env_file is None:
        env_file = os.environ.get(ENV_FILE_VARIABLE) or None
    if env_file is not None and not Path(env_file).is_file():
        raise ConfigurationError(f"{ENV_FILE_VARIABLE} points to a missing file")
    try:
        settings = Settings(_env_file=env_file, **overrides)
    except ValidationError as exc:
        raise ConfigurationError(_format_validation_error(exc)) from None
    _register_secrets(settings)
    return settings
