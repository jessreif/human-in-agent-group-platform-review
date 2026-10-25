"""Shared admin-API client for operator scripts (audit M2).

Replaces the login + ``Set-Cookie`` scraping that each smoke script, and many
ad hoc operations commands, re-implemented. Secrets are read from ``.env`` and
never printed. Cookies are passed explicitly because the admin cookies are
``Secure`` and path-scoped, so httpx drops them on the loopback http base URL.

    from _adminclient import AdminClient
    admin = AdminClient.login()            # researcher
    admin.post("/admin/batch-groups/study.live.v15/enrollment", {"open": False, "reason": "x"})
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
ADMIN_BASE = "http://127.0.0.1:8519"
_PASSWORD_KEYS = {
    "researcher": "ADMIN_PASSWORD",
    "operator": "ADMIN_OPERATOR_PASSWORD",
    "compensation": "ADMIN_COMPENSATION_PASSWORD",
}


def env_value(key: str, env_file: Path = ROOT / ".env") -> str:
    """One value from ``.env``. Raises ``SystemExit`` (never echoing the file) if absent."""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"{key} not found in {env_file.name}")


class AdminClient:
    def __init__(self, client: httpx.Client, cookies: dict[str, str]) -> None:
        self._client = client
        self._cookies = cookies

    @classmethod
    def login(cls, role: str = "researcher", *, base_url: str = ADMIN_BASE) -> AdminClient:
        client = httpx.Client(base_url=base_url, timeout=60.0)
        r = client.post(
            "/admin/login", json={"role": role, "password": env_value(_PASSWORD_KEYS[role])}
        )
        r.raise_for_status()
        cookies: dict[str, str] = {}
        for header in r.headers.get_list("set-cookie"):
            for name in ("inv_admin", "inv_admin_csrf"):
                m = re.match(rf"\s*{name}=([^;,]+)", header)
                if m:
                    cookies[name] = m.group(1)
        if "inv_admin" not in cookies:
            raise SystemExit(f"admin login as {role} returned no session cookie")
        return cls(client, cookies)

    @property
    def headers(self) -> dict[str, str]:
        return {
            "x-csrf-token": self._cookies.get("inv_admin_csrf", ""),
            "cookie": "; ".join(f"{k}={v}" for k, v in self._cookies.items()),
        }

    def get(self, path: str, **params: Any) -> httpx.Response:
        return self._client.get(path, params=params or None, headers=self.headers)

    def post(self, path: str, body: dict[str, Any] | None = None) -> httpx.Response:
        return self._client.post(path, json=body or {}, headers=self.headers)
