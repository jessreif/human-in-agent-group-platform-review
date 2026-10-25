from __future__ import annotations

from pathlib import Path

import pytest

from app.db.dsn import LOCAL_HOST_GROUP, parse_dsn, safe_label, sqlalchemy_url
from app.db.guard import (
    UnsafeTestDatabaseError,
    assert_disposable,
    collect_live_dsns,
    read_env_file_value,
)

LIVE = "postgresql://investigators_user:pw@host.docker.internal:5432/investigators_db"
TEST = "postgresql://investigators_test:pw@127.0.0.1:5432/investigators_test_db"


def test_loopback_aliases_share_one_identity() -> None:
    a = parse_dsn("postgresql://u:p@localhost/investigators_db").identity
    b = parse_dsn("postgresql://x:y@host.docker.internal:5432/investigators_db").identity
    assert a == b
    assert a.host_group == LOCAL_HOST_GROUP


def test_safe_label_and_driver_selection() -> None:
    assert "pw" not in safe_label(LIVE)
    assert sqlalchemy_url(LIVE).startswith("postgresql+psycopg://")
    assert safe_label("not a dsn") == "<unparseable DSN>"


def test_accepts_distinct_opted_in_test_database() -> None:
    result = assert_disposable(TEST, allow_destructive="1", live_dsns=[LIVE])
    assert result.identity.database == "investigators_test_db"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"test_dsn": None, "allow_destructive": True, "live_dsns": [LIVE]}, "not set"),
        ({"test_dsn": TEST, "allow_destructive": "0", "live_dsns": [LIVE]}, "not enabled"),
        ({"test_dsn": TEST, "allow_destructive": None, "live_dsns": [LIVE]}, "not enabled"),
        (
            {"test_dsn": TEST, "allow_destructive": True, "live_dsns": [LIVE], "app_mode": "live"},
            "live mode",
        ),
        (
            {
                "test_dsn": "postgresql://u:p@127.0.0.1/investigators_scratch",
                "allow_destructive": True,
                "live_dsns": [LIVE],
            },
            "must contain 'test'",
        ),
        ({"test_dsn": TEST, "allow_destructive": True, "live_dsns": []}, "no live DSN"),
        (
            {
                "test_dsn": "postgresql://other:pw@localhost:5432/investigators_test_db",
                "allow_destructive": True,
                "live_dsns": [LIVE, "postgresql://a:b@127.0.0.1/investigators_test_db"],
            },
            "same as a live DSN",
        ),
    ],
)
def test_refuses_unsafe_configurations(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(UnsafeTestDatabaseError, match=message):
        assert_disposable(**kwargs)  # type: ignore[arg-type]


def test_error_messages_never_contain_passwords() -> None:
    same = "postgresql://u:VerySecretPw@127.0.0.1/investigators_test_db"
    with pytest.raises(UnsafeTestDatabaseError) as info:
        assert_disposable(same, allow_destructive=True, live_dsns=[same])
    assert "VerySecretPw" not in str(info.value)


def test_collects_live_dsns_from_env_extras_and_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nDATABASE_URL='postgresql://a:b@h/from_file'\n", encoding="utf-8"
    )
    found = collect_live_dsns(
        {"DATABASE_URL": LIVE, "INVESTIGATORS_EXTRA_LIVE_DSNS": "postgresql://c:d@h/x, "},
        env_files=[env_file, tmp_path / "missing.env"],
    )
    assert found == [LIVE, "postgresql://c:d@h/x", "postgresql://a:b@h/from_file"]
    assert read_env_file_value(env_file, "NOPE") is None
