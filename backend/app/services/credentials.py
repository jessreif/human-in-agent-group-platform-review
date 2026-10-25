"""Opaque session credentials and the one-writer lease (INV-0402, INV-0407; spec §5.1, §8.2).

- The bearer token is 256 bits of ``secrets`` entropy; only its SHA-256 is persisted
  (``identity.session_credential``). Knowing a panel ID or a session UUID recovers nothing —
  the token is the only secret, and it lives only in the participant's HttpOnly cookie.
- Cookies are ``Secure``/``HttpOnly``, ``SameSite=Strict``, path-scoped to ``/investigators/``.
- CSRF: a second, non-HttpOnly cookie carries a random token; every mutating request must
  echo it in ``X-CSRF-Token`` (double-submit; SameSite=Strict plus this check covers CSRF
  without per-request server state). ``Origin`` on mutating/event-stream requests must be the
  deployment origin or absent (non-browser tools).
- The writer lease is one active writer per session, stored on ``app.session``. A second tab
  is read-only until it takes over explicitly or the incumbent's lease expires.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Literal, TypedDict

from sqlalchemy import func, select, update
from sqlalchemy.engine import Connection

from app.db import schema as s
from app.db.tx import db_now

TOKEN_BYTES = 32
SESSION_COOKIE = "inv_session"
CSRF_COOKIE = "inv_csrf"
CSRF_HEADER = "x-csrf-token"
COOKIE_PATH = "/investigators/"
WRITER_LEASE = timedelta(seconds=45)

CredentialKind = Literal["primary", "recovery"]


class CredentialError(ValueError):
    pass


def mint_token() -> str:
    return f"inv_{secrets.token_urlsafe(TOKEN_BYTES)}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def issue(
    conn: Connection,
    session_id: uuid.UUID,
    *,
    kind: CredentialKind = "primary",
) -> tuple[uuid.UUID, str]:
    """Insert the credential hash and return ``(credential_id, raw token)``."""
    credential_id = uuid.uuid4()
    token = mint_token()
    conn.execute(
        s.session_credential.insert().values(
            credential_id=credential_id,
            session_id=session_id,
            credential_sha256=hash_token(token),
            kind=kind,
        )
    )
    return credential_id, token


def is_tombstoned(conn: Connection, session_id: uuid.UUID) -> bool:
    """Whether the session carries a ``deletion_tombstone`` (INV-1307/BA-39)."""
    return bool(
        conn.execute(
            select(func.count())
            .select_from(s.admin_audit)
            .where(
                s.admin_audit.c.session_id == session_id,
                s.admin_audit.c.action == "deletion_tombstone",
            )
        ).scalar_one()
    )


def authenticate(conn: Connection, token: str) -> uuid.UUID | None:
    """Resolve a presented bearer token to its session, or None if unknown/revoked.

    BA-39: a tombstoned session is deleted by contract — its credential can never
    re-authenticate it. Cleanup cannot DELETE ``identity.session_credential`` rows under
    the runtime role, so this tombstone check is the revocation."""
    if not token.startswith("inv_"):
        return None
    row = conn.execute(
        select(s.session_credential.c.session_id).where(
            s.session_credential.c.credential_sha256 == hash_token(token),
            s.session_credential.c.revoked_at.is_(None),
        )
    ).first()
    if row is None or is_tombstoned(conn, row.session_id):
        return None
    return uuid.UUID(str(row.session_id))


def revoke(
    conn: Connection, credential_id: uuid.UUID, *, reason: str, now: datetime | None = None
) -> None:
    conn.execute(
        update(s.session_credential)
        .where(s.session_credential.c.credential_id == credential_id)
        .values(revoked_at=now or db_now(conn), revoked_reason=reason)
    )


def supersede(conn: Connection, session_id: uuid.UUID, *, reason: str) -> tuple[uuid.UUID, str]:
    """Recovery/re-entry: revoke every live credential for the session and issue a new
    ``recovery`` credential linked to the most recent one it replaced (INV-0407)."""
    rows = conn.execute(
        select(s.session_credential.c.credential_id).where(
            s.session_credential.c.session_id == session_id,
            s.session_credential.c.revoked_at.is_(None),
        )
    ).all()
    now = db_now(conn)
    credential_id, token = issue(conn, session_id, kind="recovery")
    for row in rows:
        conn.execute(
            update(s.session_credential)
            .where(s.session_credential.c.credential_id == row.credential_id)
            .values(revoked_at=now, revoked_reason=reason, superseded_by=credential_id)
        )
    return credential_id, token


def mint_writer() -> uuid.UUID:
    return uuid.uuid4()


def acquire_writer(conn: Connection, session_id: uuid.UUID, *, now: datetime) -> uuid.UUID:
    """Take the writer lease if free or expired; returns the writer id. Raises
    :class:`CredentialError` when another live writer holds it (second tab is read-only)."""
    row = (
        conn.execute(
            select(s.session.c.writer_id, s.session.c.writer_lease_until)
            .where(s.session.c.session_id == session_id)
            .with_for_update()
        )
        .mappings()
        .one()
    )
    holder, until = row["writer_id"], row["writer_lease_until"]
    if holder is not None and until is not None and until > now:
        raise CredentialError("writer_lease_held")
    writer = mint_writer()
    conn.execute(
        update(s.session)
        .where(s.session.c.session_id == session_id)
        .values(writer_id=writer, writer_lease_until=now + WRITER_LEASE)
    )
    return writer


def renew_writer(
    conn: Connection, session_id: uuid.UUID, writer: uuid.UUID, *, now: datetime
) -> bool:
    """Extend the lease iff the caller holds it. Commands by non-writers are rejected."""
    result = conn.execute(
        update(s.session)
        .where(
            s.session.c.session_id == session_id,
            s.session.c.writer_id == writer,
            s.session.c.writer_lease_until > now,
        )
        .values(writer_lease_until=now + WRITER_LEASE)
    )
    return result.rowcount == 1


def force_takeover(conn: Connection, session_id: uuid.UUID, *, now: datetime) -> uuid.UUID:
    """Explicit authenticated takeover: replace the incumbent lease holder (INV-0407)."""
    writer = mint_writer()
    conn.execute(
        update(s.session)
        .where(s.session.c.session_id == session_id)
        .values(writer_id=writer, writer_lease_until=now + WRITER_LEASE)
    )
    return writer


class CookieSettings(TypedDict, total=False):
    path: str
    secure: bool
    httponly: bool
    samesite: Literal["lax", "strict", "none"]


def csrf_token() -> str:
    return secrets.token_urlsafe(24)


def cookie_settings() -> CookieSettings:
    return {"path": COOKIE_PATH, "secure": True, "httponly": True, "samesite": "strict"}


def csrf_cookie_settings() -> CookieSettings:
    # readable by the participant app so it can echo the value in X-CSRF-Token
    return {"path": COOKIE_PATH, "secure": True, "httponly": False, "samesite": "strict"}
