"""Admin authentication, session tokens, login throttling and input hygiene (INV-1301).

Deployment truth (spec §3.1): since 2026-10-11 the Shibboleth ``<Location>`` blocks for
``/investigators/admin`` and ``/investigators/api/admin`` follow the public
``<Location "/investigators/">`` block, so Apache enforces SSO on the admin paths (the
2026-09-28 layout let the public block override them). The application remains an
independent second gate and deliberately never reads any forwarded identity header: the
ONLY signals are the ``inv_admin`` signed cookie and the ``inv_admin_csrf`` double-submit
token.

Role model (documented in docs/ADMIN_SURFACE.md): three roles, three secrets.
``ADMIN_PASSWORD`` is the researcher credential *and* the master gate — empty disables the
whole admin surface fail-closed. ``ADMIN_OPERATOR_PASSWORD`` and
``ADMIN_COMPENSATION_PASSWORD`` enable the operator and compensation roles independently.
A login POST claims ``{role, password}`` and is checked with ``hmac.compare_digest`` only
against that role's secret, so a compensation credential can never mint a researcher
session (and vice versa). The optional ``email`` field records researcher identity for
audit attribution; it must be on ``ADMIN_EMAILS`` and is never an authentication signal.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Literal, TypedDict
from urllib.parse import quote, unquote, urlsplit

from fastapi import Request

from app.settings import Settings

Role = Literal["researcher", "operator", "compensation"]
ROLES: tuple[Role, ...] = ("researcher", "operator", "compensation")

SESSION_COOKIE = "inv_admin"
CSRF_COOKIE = "inv_admin_csrf"
CSRF_HEADER = "x-csrf-token"
COOKIE_PATH = "/investigators/"
TOKEN_DOMAIN = b"investigators.admin-session/1."

# Headers Apache may strip/forward that must never be consulted (spec §3.1).
UNTRUSTED_IDENTITY_HEADERS = frozenset(
    {"x-remote-user", "eppn", "x-eppn", "x-shib-authenticated", "remote-user"}
)

MAX_TEXT_CHARS = 2000
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True)
class Principal:
    """One authenticated admin request."""

    role: Role
    actor: str  # audit identity: the allowlisted email or "admin:<role>"
    expires_at: int


# ----- signed session token -----------------------------------------------------------------


def _signing_key(settings: Settings) -> bytes:
    """Session-token signing key derived from the researcher secret; rotating
    ``ADMIN_PASSWORD`` invalidates every outstanding admin session."""
    return hashlib.sha256(
        TOKEN_DOMAIN + settings.ADMIN_PASSWORD.get_secret_value().encode("utf-8")
    ).digest()


def _sign(settings: Settings, payload: str) -> str:
    return hmac.new(_signing_key(settings), payload.encode("ascii"), hashlib.sha256).hexdigest()


def make_session_token(
    settings: Settings, *, role: Role, actor: str, now: float | None = None
) -> tuple[str, int]:
    """Return ``(token, expires_at_epoch)`` for a fresh admin session."""
    issued = int(now if now is not None else time.time())
    expires = issued + settings.ADMIN_SESSION_TTL_SECONDS
    # The actor may contain ':' (e.g. "admin:researcher") — quote it so the wire
    # format stays exactly six colon-separated fields.
    payload = f"v1:{role}:{quote(actor, safe='')}:{issued}:{expires}:{secrets.token_hex(8)}"
    return f"{payload}:{_sign(settings, payload)}", expires


def verify_session_token(
    settings: Settings, token: str | None, *, now: float | None = None
) -> Principal | None:
    """Resolve an admin cookie to its principal, or None. Fail closed on any defect."""
    if not token or not settings.admin_enabled:
        return None
    parts = token.split(":")
    if len(parts) != 7 or parts[0] != "v1":
        return None
    _v, role, actor_quoted, issued_s, expires_s, _nonce, sig = parts
    if role not in ROLES:
        return None
    actor = unquote(actor_quoted)
    payload = ":".join(parts[:-1])
    if not hmac.compare_digest(sig, _sign(settings, payload)):
        return None
    try:
        issued, expires = int(issued_s), int(expires_s)
    except ValueError:
        return None
    stamp = int(now if now is not None else time.time())
    if expires <= stamp or issued > stamp or expires - issued != settings.ADMIN_SESSION_TTL_SECONDS:
        return None
    if not settings.admin_role_enabled(role):
        return None  # role secret rotated out: existing sessions die
    return Principal(role=role, actor=actor, expires_at=expires)


def check_password(settings: Settings, *, role: str, password: str) -> bool:
    """Constant-time check of ``password`` against the claimed role's secret only.
    Unknown or disabled roles compare against a fixed dummy so the failure shape and
    timing do not reveal which roles exist."""
    secret = settings.admin_role_secret(role) if role in ROLES else ""
    expected = secret.encode("utf-8") if secret else b"disabled-role-dummy"
    return bool(secret) and hmac.compare_digest(password.encode("utf-8"), expected)


def csrf_token() -> str:
    return secrets.token_urlsafe(24)


class CookieSettings(TypedDict):
    """Keyword arguments for ``Response.set_cookie`` — typed so the ``**`` splat
    type-checks (audit L2)."""

    path: str
    secure: bool
    httponly: bool
    samesite: Literal["strict"]


def cookie_settings() -> CookieSettings:
    return {"path": COOKIE_PATH, "secure": True, "httponly": True, "samesite": "strict"}


def csrf_cookie_settings() -> CookieSettings:
    # readable by the admin SPA so it can echo the value in X-CSRF-Token
    return {"path": COOKIE_PATH, "secure": True, "httponly": False, "samesite": "strict"}


def check_csrf(request: Request) -> bool:
    """Double-submit: every mutating admin request echoes ``inv_admin_csrf`` in
    ``X-CSRF-Token`` (SameSite=Strict plus this covers CSRF without server state)."""
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header:
        return False
    return hmac.compare_digest(cookie, header)


def check_origin(request: Request) -> bool:
    """A browser POST must come from the deployment origin; absent Origin = non-browser
    tool and is allowed (mirrors the participant stream rule, spec §5.1)."""
    origin = request.headers.get("origin")
    if origin is None:
        return True
    host = request.headers.get("host") or request.url.hostname or ""
    return urlsplit(origin).netloc == host


def client_ip(request: Request) -> str:
    """Throttle key only — never logged or persisted (no raw IPs in logs, spec §11)."""
    return request.client.host if request.client else "unknown"


# ----- login throttle (in-memory; the API is single-process behind Apache) -------------------


class LoginThrottle:
    """Sliding-window limiter: at most ``max_attempts`` failures per ``window_s`` per
    (client, role). Keys and counts are held in memory only."""

    def __init__(self, *, max_attempts: int, window_s: int) -> None:
        self.max_attempts = max_attempts
        self.window_s = window_s
        self._attempts: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: tuple[str, str], now: float) -> list[float]:
        attempts = [t for t in self._attempts.get(key, []) if now - t < self.window_s]
        self._attempts[key] = attempts
        return attempts

    def allowed(self, key: tuple[str, str], now: float | None = None) -> bool:
        stamp = now if now is not None else time.time()
        with self._lock:
            return len(self._prune(key, stamp)) < self.max_attempts

    def record(self, key: tuple[str, str], now: float | None = None) -> None:
        stamp = now if now is not None else time.time()
        with self._lock:
            self._prune(key, stamp).append(stamp)

    def reset(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._attempts.pop(key, None)


# ----- input hygiene (INV-1309: bounded, sanitized operator text) ----------------------------


def sanitize_text(value: object, *, max_chars: int = MAX_TEXT_CHARS) -> str:
    """Bound and strip control characters from operator-supplied free text. HTML/JS is
    never interpreted downstream (the admin UI renders text nodes only), but audit
    payloads stay clean regardless."""
    if not isinstance(value, str):
        return ""
    cleaned = _CONTROL_CHARS.sub("", value.strip())
    return cleaned[:max_chars]
