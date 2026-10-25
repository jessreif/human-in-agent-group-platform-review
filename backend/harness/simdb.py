"""Disposable-PostgreSQL provisioning for simulation runs.

Mirrors ``scripts/test_backend.sh``: an ephemeral ``postgres:15-alpine`` container with
a tmpfs data directory and a random loopback port, or an operator-supplied DSN (which
must still pass ``assert_disposable``). Bootstrap applies the INV-0301 role/schema
layout, then Alembic migrates to head — the same surface the integration suite uses.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

from app.db.engine import make_engine
from app.db.guard import (
    DisposableDatabase,
    assert_disposable,
    collect_live_dsns,
    reset_disposable_schema,
)
from tests.integration.pg_bootstrap import RoleDsns, apply_bootstrap

BACKEND = Path(__file__).resolve().parents[1]
# The project env file is *always* a live-DSN reference for the destructive
# guard (BA-78 residual) — direct `python -m harness.cli` calls have no wrapper
# to export INVESTIGATORS_LIVE_ENV_FILE for them.
_PROJECT_ENV_FILE = BACKEND.parents[0] / ".env"
PG_IMAGE = os.environ.get(
    "INVESTIGATORS_TEST_PG_IMAGE",
    "postgres:15-alpine@sha256:f7d23353e1b15400d22ebe31189f4d314b87a4c129cc400c8c2d8d4ca127bf81",
)


@dataclass
class SimDatabase:
    """Role engines for one disposable database; ``container`` is the ephemeral docker
    container name when the harness spawned it (None for an operator-supplied DSN)."""

    dsns: RoleDsns
    superuser: Engine
    migrator: Engine
    runtime: Engine
    research: Engine
    compensation: Engine
    container: str | None

    def dispose(self) -> None:
        for engine in (self.migrator, self.runtime, self.research, self.compensation):
            engine.dispose()
        self.superuser.dispose()
        if self.container is not None:
            _dispose_container(self.container)


def _alembic_head(migrator_dsn: str) -> None:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    os.environ["INVESTIGATORS_MIGRATION_DATABASE_URL"] = migrator_dsn
    command.upgrade(config, "head")


def _spawn_container() -> tuple[str, str]:
    """Start the ephemeral Postgres; returns (container_name, superuser dsn)."""
    if shutil.which("docker") is None:
        raise RuntimeError("docker unavailable and no --dsn supplied")
    name = f"investigators-simdb-{uuid.uuid4().hex[:12]}"
    password = secrets.token_hex(16)
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--label",
            "investigators.disposable=true",
            "-e",
            "POSTGRES_USER=investigators_test",
            "-e",
            f"POSTGRES_PASSWORD={password}",
            "-e",
            "POSTGRES_DB=investigators_test_db",
            "--tmpfs",
            "/var/lib/postgresql/data:rw",
            "-p",
            "127.0.0.1::5432",
            PG_IMAGE,
        ],
        check=True,
        capture_output=True,
    )
    try:
        for _ in range(60):
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    name,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "investigators_test",
                    "-d",
                    "investigators_test_db",
                ],
                check=False,
                capture_output=True,
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"disposable postgres {name} never became ready")
        port_line = subprocess.run(
            ["docker", "port", name, "5432/tcp"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()[0]
        port = port_line.rsplit(":", 1)[1].strip()
        return (
            name,
            f"postgresql://investigators_test:{password}@127.0.0.1:{port}/investigators_test_db",
        )
    except BaseException:
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
        raise


def _dispose_container(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)


def provision(
    dsn: str | None = None,
    *,
    live_env_file: Path | None = None,
    environ: dict[str, str] | None = None,
) -> tuple[SimDatabase, DisposableDatabase]:
    """Provision a disposable database: bootstrap roles, migrate to head, return engines.

    ``dsn`` (operator-supplied ``--dsn`` or ``INVESTIGATORS_TEST_DATABASE_URL``) is
    used verbatim; without one an ephemeral container is spawned. Either way the
    destructive guard must pass before a single statement runs — and the guard is
    fed the *real* configuration, not manufactured inputs:

    * ``allow_destructive`` comes from ``INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS``
      (the wrapper exports ``1``; a bare ``python -m harness.cli`` without it is
      refused);
    * ``app_mode`` comes from ``APP_MODE`` — a ``live`` environment refuses;
    * live-DSN comparison comes from the environment plus the configured live env
      file (``live_env_file`` arg, else ``INVESTIGATORS_LIVE_ENV_FILE``). With no
      live reference at all the guard fails closed.

    BA-78/BA-86: a failure after spawning — guard refusal, bootstrap error,
    migration error — removes the spawned container; an operator-supplied ``--dsn``
    database is never removed.
    """
    env = dict(os.environ if environ is None else environ)
    container: str | None = None
    if dsn is None:
        dsn = env.get("INVESTIGATORS_TEST_DATABASE_URL")
    if dsn is None:
        container, dsn = _spawn_container()
    env_file = live_env_file or (
        Path(env["INVESTIGATORS_LIVE_ENV_FILE"]) if env.get("INVESTIGATORS_LIVE_ENV_FILE") else None
    )
    # BA-78 residual: the project `.env` is a configured live reference whether or
    # not the caller remembered to point at it — a bare `python -m harness.cli`
    # must compare against it too, not just whatever another env var supplied.
    env_files = []
    for candidate in (env_file, _PROJECT_ENV_FILE):
        if candidate and candidate.is_file() and candidate not in env_files:
            env_files.append(candidate)
    try:
        disposable = assert_disposable(
            dsn,
            allow_destructive=env.get("INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS"),
            live_dsns=collect_live_dsns(env, env_files=env_files),
            app_mode=env.get("APP_MODE", "preview"),
        )
        superuser = make_engine(disposable.dsn)
        try:
            reset_disposable_schema(superuser, disposable)
            dsns = apply_bootstrap(superuser, disposable.dsn, disposable.identity.database)
            _alembic_head(dsns.migrator)
        except BaseException:
            superuser.dispose()
            raise
    except BaseException:
        if container is not None:
            _dispose_container(container)
        raise
    return (
        SimDatabase(
            dsns=dsns,
            superuser=superuser,
            migrator=make_engine(dsns.migrator),
            runtime=make_engine(dsns.runtime),
            research=make_engine(dsns.research),
            compensation=make_engine(dsns.compensation),
            container=container,
        ),
        disposable,
    )
