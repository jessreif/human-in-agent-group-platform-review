"""DSN selection and database-name guards. No database is contacted."""

from __future__ import annotations

import pytest

from tests.integration.db_clone import (
    MissingWorkerDatabase,
    _ident,
    checked_drop_name,
    database_local_statements,
    role_passwords,
    select_test_dsn,
)

_SHARED = "postgresql://unit:shared-secret@127.0.0.1:1/investigators_test_db"
_GW0 = "postgresql://unit:worker-zero@127.0.0.1:2/investigators_test_db"
_GW1 = "postgresql://unit:worker-one@127.0.0.1:3/investigators_test_db"


def test_select_test_dsn_without_worker_uses_the_shared_url() -> None:
    assert select_test_dsn({"INVESTIGATORS_TEST_DATABASE_URL": _SHARED}) == _SHARED


def test_select_test_dsn_without_any_url_returns_none() -> None:
    assert select_test_dsn({}) is None


def test_select_test_dsn_worker_without_its_url_does_not_use_the_shared_one() -> None:
    env = {
        "PYTEST_XDIST_WORKER": "gw1",
        "INVESTIGATORS_TEST_DATABASE_URL": _SHARED,
        "INVESTIGATORS_TEST_DATABASE_URL_GW0": _GW0,
    }
    with pytest.raises(MissingWorkerDatabase, match="GW1"):
        select_test_dsn(env)


def test_select_test_dsn_worker_uses_only_its_own_url() -> None:
    env = {
        "PYTEST_XDIST_WORKER": "gw1",
        "INVESTIGATORS_TEST_DATABASE_URL": _SHARED,
        "INVESTIGATORS_TEST_DATABASE_URL_GW0": _GW0,
        "INVESTIGATORS_TEST_DATABASE_URL_GW1": _GW1,
    }
    assert select_test_dsn(env) == _GW1


def test_ident_rejects_live_name_and_injection() -> None:
    with pytest.raises(ValueError):
        _ident("investigators_db")
    with pytest.raises(ValueError):
        _ident('investigators_test_db"; DROP DATABASE investigators_test_db')
    assert _ident("investigators_test_bootstrap") == "investigators_test_bootstrap"


def test_ident_rejects_names_over_63_characters() -> None:
    with pytest.raises(ValueError):
        _ident("investigators_test_" + ("a" * 50))


def test_drop_refuses_the_maintenance_database() -> None:
    with pytest.raises(ValueError, match="maintenance"):
        checked_drop_name("investigators_test_db", "investigators_test_db")
    assert (
        checked_drop_name("investigators_test_bootstrap", "investigators_test_db")
        == "investigators_test_bootstrap"
    )


def test_role_passwords_follow_the_server_secret_and_stay_distinct() -> None:
    alpha = role_passwords("alpha")
    assert alpha == role_passwords("alpha")
    assert alpha != role_passwords("beta")
    assert len(set(alpha.values())) == len(alpha)


def test_database_local_statements_are_grants_not_schema_copies() -> None:
    statements = database_local_statements("investigators_test_c_gw0_1")
    joined = "\n".join(statements)
    assert "investigators_test_c_gw0_1" in joined
    assert "investigators_db" not in joined
    assert "CREATE SCHEMA" not in joined.upper()
    assert "GRANT CONNECT" in joined.upper()
