from __future__ import annotations

import pytest

from alpha_market_engine.database.safety import (
    NonTestDatabaseError,
    assert_test_database_url,
    extract_database_name,
    is_test_database_name,
)


@pytest.mark.parametrize(
    "database_name",
    ["alpha_market_test", "alpha_market_test_local", "this_is_a_test_db", "db_test_2026"],
)
def test_accepts_test_database_names(database_name: str) -> None:
    assert is_test_database_name(database_name)
    assert assert_test_database_url(f"mysql+pymysql://root@127.0.0.1:3306/{database_name}") == database_name


@pytest.mark.parametrize("database_name", ["alpha_market", "jarvis", "production", "alpha_trading_floor"])
def test_rejects_non_test_database_names(database_name: str) -> None:
    with pytest.raises(NonTestDatabaseError, match=f"REFUSING TO RUN DATABASE INTEGRATION TESTS AGAINST NON-TEST DATABASE: {database_name}"):
        assert_test_database_url(f"mysql+pymysql://root@127.0.0.1:3306/{database_name}")


def test_extract_database_name() -> None:
    assert extract_database_name("mysql+pymysql://root@127.0.0.1:3306/alpha_market_test?charset=utf8mb4") == "alpha_market_test"
