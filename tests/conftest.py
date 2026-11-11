from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from alpha_market_engine.config.settings import get_settings
from alpha_market_engine.database.safety import assert_test_database_url

ROOT = Path(__file__).resolve().parents[1]
TEST_DB_NAME = os.environ.get("TEST_DATABASE_NAME", "alpha_market_test")
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    f"mysql+pymysql://root@127.0.0.1:3306/{TEST_DB_NAME}?charset=utf8mb4",
)

_REQUIRED_ENV = {
    "DATABASE_URL": TEST_DATABASE_URL,
    "REDIS_URL": "redis://127.0.0.1:6379/0",
    "AI_PROVIDER": "local",
    "AI_MODEL": "echo",
    "AI_API_KEY": "test",
    "AI_BASE_URL": "http://127.0.0.1:11434/v1",
    "X_CLIENT_ID": "test-client-id",
    "X_CLIENT_SECRET": "test-client-secret",
    "X_ACCESS_TOKEN": "test-access-token",
    "X_ACCESS_TOKEN_SECRET": "test-access-token-secret",
    "X_BEARER_TOKEN": "test-bearer-token",
    "MARKET_DATA_PROVIDER": "test",
    "MARKET_DATA_API_KEY": "test-market-key",
    "MARKET_TIMEZONE": "America/Los_Angeles",
}


@pytest.fixture(scope="session")
def mysql_test_engine() -> Iterator[object]:
    subprocess.run(
        [
            "mysql",
            "-h127.0.0.1",
            "-uroot",
            "-e",
            f"DROP DATABASE IF EXISTS {TEST_DB_NAME}; CREATE DATABASE {TEST_DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    old_env = {key: os.environ.get(key) for key in _REQUIRED_ENV}
    os.environ.update(_REQUIRED_ENV)
    get_settings.cache_clear()
    assert_test_database_url(os.environ["DATABASE_URL"])

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(cfg, "head")

    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
    try:
        yield engine
    finally:
        engine.dispose()
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


@pytest.fixture()
def db_session(mysql_test_engine) -> Iterator[Session]:
    SessionLocal = sessionmaker(bind=mysql_test_engine, autoflush=False, autocommit=False, future=True)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        with mysql_test_engine.begin() as connection:
            connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            inspector = inspect(connection)
            for table_name in inspector.get_table_names():
                if table_name == "alembic_version":
                    continue
                connection.execute(text(f"TRUNCATE TABLE `{table_name}`"))
            connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))
