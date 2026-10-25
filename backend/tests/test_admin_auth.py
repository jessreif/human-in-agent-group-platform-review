"""INV-1301/1309: application-enforced admin authentication — unit coverage (no DB).

Covers the fail-closed gate, signed expiring session cookie, per-role secrets,
constant-time credential checks, login throttling, CSRF/origin enforcement and the
core adversarial requirement: forwarded identity headers (``X-Remote-User``, ``Eppn``,
``X-Shib-*``) are never consulted — Apache strips them on public ingress and the app
must not trust them even if they arrive.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http.cookies import Morsel, SimpleCookie
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import adminauth
from app.settings import Settings
from app.timing.clock import VirtualClock

PASSWORD = "test-admin-passphrase-1234"
OPERATOR_PW = "test-operator-passphrase-5678"
COMPENSATION_PW = "test-compensation-passphrase-90ab"


def _client(make_settings: Callable[..., Settings], **overrides: Any) -> TestClient:
    values: dict[str, Any] = {"ADMIN_PASSWORD": PASSWORD}
    values.update(overrides)
    app = create_app(make_settings(**values), clock=VirtualClock(), configure_logs=False)
    return TestClient(app, raise_server_exceptions=False)


def _login(
    client: TestClient,
    role: str = "researcher",
    password: str = PASSWORD,
    email: str | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """POST /admin/login. The deployment serves the API under /investigators/api/ so the
    scoped cookie (path=/investigators/) is sent; TestClient talks to the internal
    /admin/... paths directly, so copy the issued cookies onto path "/" for the jar."""
    body: dict[str, Any] = {"role": role, "password": password}
    if email is not None:
        body["email"] = email
    resp = client.post("/admin/login", json=body, headers=headers or {})
    for name in (adminauth.SESSION_COOKIE, adminauth.CSRF_COOKIE):
        value = resp.cookies.get(name)
        if value:
            client.cookies.set(name, value, path="/")
    return resp


def _csrf(client: TestClient) -> str:
    # The jar holds the deployed-path cookie plus the "/" test copy; ask for ours.
    token = client.cookies.get(adminauth.CSRF_COOKIE, path="/")
    assert token
    return token


def test_disabled_fails_closed(make_settings: Callable[..., Settings]) -> None:
    """Empty ADMIN_PASSWORD disables the whole admin surface (INV-1301)."""
    app = create_app(make_settings(), clock=VirtualClock(), configure_logs=False)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/admin/login", json={"role": "researcher", "password": PASSWORD})
    assert resp.status_code == 403
    assert resp.json()["error"] == "admin_disabled"
    assert client.get("/admin/whoami").status_code == 401
    assert client.get("/admin/sessions").status_code == 401


def test_login_round_trip_and_actor(make_settings: Callable[..., Settings]) -> None:
    client = _client(make_settings, ADMIN_EMAILS="pi@university.edu")
    resp = _login(client, email="pi@university.edu")
    assert resp.status_code == 200
    assert resp.json()["role"] == "researcher"
    assert resp.json()["actor"] == "pi@university.edu"
    assert client.cookies.get(adminauth.SESSION_COOKIE, path="/")
    me = client.get("/admin/whoami")
    assert me.status_code == 200
    assert me.json()["role"] == "researcher"
    assert me.json()["actor"] == "pi@university.edu"


def test_email_must_be_allowlisted(make_settings: Callable[..., Settings]) -> None:
    client = _client(make_settings, ADMIN_EMAILS="pi@university.edu")
    resp = _login(client, email="stranger@evil.example")
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_credentials"


def test_wrong_password_rejected(make_settings: Callable[..., Settings]) -> None:
    client = _client(make_settings)
    resp = _login(client, password="nope")
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_credentials"


def test_role_secrets_are_independent(make_settings: Callable[..., Settings]) -> None:
    roles = {
        "ADMIN_OPERATOR_PASSWORD": OPERATOR_PW,
        "ADMIN_COMPENSATION_PASSWORD": COMPENSATION_PW,
    }
    client = _client(make_settings, **roles)
    # Each role's own secret mints that role's session.
    assert _login(client, "researcher", PASSWORD).status_code == 200
    client2 = _client(make_settings, **roles)
    assert _login(client2, "operator", OPERATOR_PW).status_code == 200
    assert client2.get("/admin/whoami").json()["role"] == "operator"
    client3 = _client(make_settings, **roles)
    assert _login(client3, "compensation", COMPENSATION_PW).status_code == 200
    assert client3.get("/admin/whoami").json()["role"] == "compensation"
    # Cross-role reuse fails: the researcher secret cannot claim operator.
    assert _login(client, "operator", PASSWORD).status_code == 401
    assert _login(client, "compensation", COMPENSATION_PW + "x").status_code == 401
    # Unknown role names fail closed.
    assert _login(client, "superuser", PASSWORD).status_code == 401


def test_disabled_role_fails_closed(make_settings: Callable[..., Settings]) -> None:
    """An unset operator/compensation secret means the role cannot log in at all."""
    client = _client(make_settings)
    assert _login(client, "operator", "anything").status_code == 401
    assert _login(client, "compensation", "anything").status_code == 401


def test_login_rate_limit(make_settings: Callable[..., Settings]) -> None:
    client = _client(make_settings, ADMIN_LOGIN_MAX_ATTEMPTS=3)
    for _ in range(3):
        assert _login(client, password="bad").status_code == 401
    resp = _login(client)
    assert resp.status_code == 429
    assert resp.json()["error"] == "login_rate_limited"


def test_mutations_require_csrf(make_settings: Callable[..., Settings]) -> None:
    client = _client(make_settings)
    assert _login(client).status_code == 200
    assert client.post("/admin/logout").status_code == 403
    assert client.post("/admin/logout", headers={"X-CSRF-Token": "forged"}).status_code == 403
    assert client.post("/admin/logout", headers={"X-CSRF-Token": _csrf(client)}).status_code == 200


def test_mutations_check_origin(make_settings: Callable[..., Settings]) -> None:
    client = _client(make_settings)
    assert _login(client).status_code == 200
    resp = client.post(
        "/admin/logout",
        headers={
            "X-CSRF-Token": _csrf(client),
            "Origin": "https://evil.example.com",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "bad_origin"


def test_forged_identity_headers_do_not_authenticate(
    make_settings: Callable[..., Settings],
) -> None:
    """Apache strips X-Remote-User/Eppn/X-Shib-* on public ingress; even if headers
    arrive they must not create or elevate an admin session or set the actor."""
    client = _client(make_settings, ADMIN_OPERATOR_PASSWORD=OPERATOR_PW, ADMIN_EMAILS="pi@university.edu")
    forged = {
        "X-Remote-User": "attacker@university.edu",
        "X-EPPN": "attacker@university.edu",
        "Eppn": "attacker@university.edu",
        "X-Shib-Authenticated": "true",
    }
    assert client.get("/admin/whoami", headers=forged).status_code == 401
    assert client.get("/admin/sessions", headers=forged).status_code == 401
    resp = _login(client, headers=forged)
    assert resp.status_code == 200
    me = client.get("/admin/whoami", headers=forged).json()
    assert me["role"] == "researcher"  # headers cannot elevate to operator/compensation
    assert me["actor"] != "attacker@university.edu"


def test_tampered_cookie_rejected(make_settings: Callable[..., Settings]) -> None:
    client = _client(make_settings)
    assert _login(client).status_code == 200
    cookie = client.cookies.get(adminauth.SESSION_COOKIE, path="/")
    payload, _, _sig = cookie.rpartition(":")
    client.cookies.set(adminauth.SESSION_COOKIE, payload + ":" + "0" * 64, path="/")
    assert client.get("/admin/whoami").status_code == 401
    # Editing the signed role claim also invalidates the signature.
    client.cookies.set(
        adminauth.SESSION_COOKIE,
        cookie.replace("researcher", "compensation"),
        path="/",
    )
    assert client.get("/admin/whoami").status_code == 401


def test_expired_cookie_rejected(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(ADMIN_PASSWORD=PASSWORD, ADMIN_SESSION_TTL_SECONDS=60)
    app = create_app(settings, clock=VirtualClock(), configure_logs=False)
    client = TestClient(app, raise_server_exceptions=False)
    assert _login(client).status_code == 200
    cookie = client.cookies.get(adminauth.SESSION_COOKIE, path="/")
    principal = adminauth.verify_session_token(settings, cookie)
    assert principal is not None
    assert adminauth.verify_session_token(settings, cookie, now=principal.expires_at + 1) is None


def _cookie_deletion_expired(morsel: Morsel[str]) -> bool:
    """A browser drops the cookie on Max-Age=0 or an Expires in the past."""
    if morsel["max-age"] == "0":
        return True
    raw = morsel["expires"]
    if not raw:
        return False
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed < datetime.now(UTC)


def _deployment_prefixed(app: Any) -> Any:
    """Serve the internal app under /investigators so cookie Path= matches the request.

    TestClient otherwise talks to /admin/... and would not send or drop a cookie
    scoped to /investigators/. A deletion on ``/`` or ``/wrong-cookie-path/`` does
    not match the jar entry this client holds.
    """

    async def asgi(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            path = scope.get("path") or ""
            prefix = "/investigators"
            if path == prefix or path.startswith(prefix + "/"):
                scope = dict(scope)
                rest = path[len(prefix) :] or "/"
                scope["path"] = rest
                scope["raw_path"] = rest.encode("ascii")
        await app(scope, receive, send)

    return asgi


def test_logout_clears_session(make_settings: Callable[..., Settings]) -> None:
    assert adminauth.SESSION_COOKIE == "inv_admin"
    assert adminauth.CSRF_COOKIE == "inv_admin_csrf"
    assert adminauth.COOKIE_PATH == "/investigators/"
    app = create_app(
        make_settings(ADMIN_PASSWORD=PASSWORD), clock=VirtualClock(), configure_logs=False
    )
    client = TestClient(
        _deployment_prefixed(app),
        base_url="https://testserver",
        raise_server_exceptions=False,
    )
    resp = client.post(
        "/investigators/admin/login",
        json={"role": "researcher", "password": PASSWORD},
    )
    assert resp.status_code == 200
    held = {
        (cookie.name, cookie.path)
        for cookie in client.cookies.jar
        if cookie.name in (adminauth.SESSION_COOKIE, adminauth.CSRF_COOKIE)
    }
    assert held == {
        (adminauth.SESSION_COOKIE, adminauth.COOKIE_PATH),
        (adminauth.CSRF_COOKIE, adminauth.COOKIE_PATH),
    }
    token = client.cookies.get(adminauth.CSRF_COOKIE, path=adminauth.COOKIE_PATH)
    assert token
    assert client.get("/investigators/admin/whoami").status_code == 200
    resp = client.post("/investigators/admin/logout", headers={"X-CSRF-Token": token})
    assert resp.status_code == 200
    # headers.get concatenates Set-Cookie; each deletion is its own header.
    raw_set_cookie = resp.headers.get_list("set-cookie")
    assert len(raw_set_cookie) == 2
    deletions: dict[str, Morsel[str]] = {}
    for raw in raw_set_cookie:
        parsed = SimpleCookie()
        parsed.load(raw)
        assert len(parsed) == 1
        morsel = next(iter(parsed.values()))
        deletions[morsel.key] = morsel
    assert set(deletions) == {adminauth.SESSION_COOKIE, adminauth.CSRF_COOKIE}
    for morsel in deletions.values():
        assert morsel["path"] == adminauth.COOKIE_PATH
        assert _cookie_deletion_expired(morsel)
    assert client.get("/investigators/admin/whoami").status_code == 401


def test_sanitize_text_bounds_and_strips_controls() -> None:
    assert adminauth.sanitize_text("  hi  ") == "hi"
    assert len(adminauth.sanitize_text("x" * 5000, max_chars=100)) == 100
    assert adminauth.sanitize_text("a\x00b\x07c") == "abc"
    assert adminauth.sanitize_text("") == ""
    assert adminauth.sanitize_text(None) == ""
    assert adminauth.sanitize_text(123) == ""


@pytest.mark.parametrize("path", ["/admin/sessions", "/admin/monitor", "/admin/redaction/queue"])
def test_unauthenticated_read_denied(make_settings: Callable[..., Settings], path: str) -> None:
    client = _client(make_settings)
    assert client.get(path).status_code == 401


def test_role_denials_without_db(make_settings: Callable[..., Settings]) -> None:
    """Role gating happens before any DB touch: compensation cannot reach research
    views and researcher cannot reach the compensation queue."""
    settings = dict(
        ADMIN_OPERATOR_PASSWORD=OPERATOR_PW,
        ADMIN_COMPENSATION_PASSWORD=COMPENSATION_PW,
    )
    comp = _client(make_settings, **settings)
    assert _login(comp, "compensation", COMPENSATION_PW).status_code == 200
    assert comp.get("/admin/sessions").status_code == 403
    assert comp.get("/admin/redaction/queue").status_code == 403
    assert comp.get("/admin/monitor").status_code == 403
    res = _client(make_settings, **settings)
    assert _login(res, "researcher", PASSWORD).status_code == 200
    assert res.get("/admin/compensation/queue").status_code == 403
    op = _client(make_settings, **settings)
    assert _login(op, "operator", OPERATOR_PW).status_code == 200
    assert op.get("/admin/compensation/queue").status_code == 403
    assert op.get("/admin/redaction/queue").status_code == 403
