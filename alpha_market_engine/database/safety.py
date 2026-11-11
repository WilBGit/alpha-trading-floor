from __future__ import annotations

from sqlalchemy.engine import make_url


class NonTestDatabaseError(RuntimeError):
    pass


def extract_database_name(database_url: str) -> str:
    url = make_url(database_url)
    if not url.database:
        raise ValueError("DATABASE_URL does not include a database/schema name")
    return url.database


def is_test_database_name(database_name: str) -> bool:
    name = database_name.lower()
    return name.endswith("_test") or "test" in name


def assert_test_database_url(database_url: str) -> str:
    database_name = extract_database_name(database_url)
    if not is_test_database_name(database_name):
        raise NonTestDatabaseError(
            f"REFUSING TO RUN DATABASE INTEGRATION TESTS AGAINST NON-TEST DATABASE: {database_name}"
        )
    return database_name
