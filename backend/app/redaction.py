"""Log redaction (INV-0103 "no secret logging", INV-0105 "structured redacted logs").

Two layers:
1. Pattern redaction: DSN credentials and ``key=value`` / ``"key": "value"`` pairs whose key
   names look secret (password, secret, token, key, authorization, cookie, dsn).
2. Exact-value redaction: every secret loaded by :mod:`app.settings` is registered here and
   replaced wherever it appears, including inside exception text and tracebacks.
"""

from __future__ import annotations

import re
import threading

REDACTED = "***"
_MIN_SECRET_LENGTH = 4

_DSN_CREDENTIALS = re.compile(
    r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)(?P<user>[^:/@\s'\"]+):(?P<password>[^@\s'\"]+)@",
    re.IGNORECASE,
)
_SECRET_WORDS = "|".join(
    (
        "password",
        "passwd",
        "secret",
        "token",
        r"api[_-]?key",
        "authorization",
        "cookie",
        "dsn",
        "database_url",
    )
)
_SECRET_KEY = rf"[A-Za-z0-9_.-]*(?:{_SECRET_WORDS})[A-Za-z0-9_.-]*"
_KEY_VALUE = re.compile(
    rf"(?P<key>{_SECRET_KEY})(?P<sep>\s*[=:]\s*)(?P<quote>['\"]?)(?P<value>[^'\"\s,;}}&]+)",
    re.IGNORECASE,
)
_JSON_PAIR = re.compile(
    rf"(?P<key>\"{_SECRET_KEY}\")(?P<sep>\s*:\s*)\"(?P<value>(?:[^\"\\]|\\.)*)\"",
    re.IGNORECASE,
)

_lock = threading.Lock()
_registered: set[str] = set()


def register_secret(value: str | None) -> None:
    """Register an exact secret value for redaction. Short/empty values are ignored."""
    if value and len(value) >= _MIN_SECRET_LENGTH:
        with _lock:
            _registered.add(value)


def clear_registered_secrets() -> None:
    """Test helper."""
    with _lock:
        _registered.clear()


def redact(text: str) -> str:
    if not text:
        return text
    with _lock:
        secrets = sorted(_registered, key=len, reverse=True)
    for secret in secrets:
        if secret in text:
            text = text.replace(secret, REDACTED)
    text = _DSN_CREDENTIALS.sub(lambda m: f"{m['scheme']}{m['user']}:{REDACTED}@", text)
    text = _JSON_PAIR.sub(lambda m: f'{m["key"]}{m["sep"]}"{REDACTED}"', text)
    text = _KEY_VALUE.sub(lambda m: f"{m['key']}{m['sep']}{m['quote']}{REDACTED}", text)
    return text
