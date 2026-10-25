"""PostgreSQL DSN parsing and identity comparison.

Identity (host group, port, database) is what the destructive-test guard compares. Loopback
aliases, including ``host.docker.internal`` (which reaches the host cluster from containers),
are collapsed into one host group so that two spellings of the same server never look distinct.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

LOCAL_HOST_GROUP = "local-host-cluster"
_LOCAL_ALIASES = frozenset(
    {
        "",
        "localhost",
        "127.0.0.1",
        "::1",
        "[::1]",
        "0.0.0.0",  # noqa: S104 - alias comparison only, nothing binds here
        "host.docker.internal",
        "host-gateway",
    }
)
DEFAULT_PORT = 5432


@dataclass(frozen=True)
class DatabaseIdentity:
    host_group: str
    port: int
    database: str

    def __str__(self) -> str:  # log-safe: no user or password
        return f"{self.host_group}:{self.port}/{self.database}"


@dataclass(frozen=True)
class ParsedDsn:
    scheme: str
    username: str | None
    password: str | None
    host: str
    port: int
    database: str

    @property
    def identity(self) -> DatabaseIdentity:
        host = self.host.lower()
        group = LOCAL_HOST_GROUP if host in _LOCAL_ALIASES else host
        return DatabaseIdentity(group, self.port, self.database)


def parse_dsn(dsn: str) -> ParsedDsn:
    parts = urlsplit(dsn.strip())
    if not parts.scheme.startswith("postgres"):
        raise ValueError("not a PostgreSQL DSN")
    database = unquote(parts.path.lstrip("/"))
    if not database:
        raise ValueError("DSN does not name a database")
    return ParsedDsn(
        scheme=parts.scheme,
        username=unquote(parts.username) if parts.username else None,
        password=unquote(parts.password) if parts.password else None,
        host=parts.hostname or "",
        port=parts.port or DEFAULT_PORT,
        database=database,
    )


def sqlalchemy_url(dsn: str) -> str:
    """Select the psycopg (v3) driver for plain ``postgresql://`` DSNs."""
    raw = dsn.strip()
    for prefix in ("postgresql://", "postgres://"):
        if raw.startswith(prefix):
            return "postgresql+psycopg://" + raw[len(prefix) :]
    return raw


def safe_label(dsn: str) -> str:
    """Log-safe description of a DSN."""
    try:
        return str(parse_dsn(dsn).identity)
    except ValueError:
        return "<unparseable DSN>"
