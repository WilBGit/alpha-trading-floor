from __future__ import annotations

import pytest
from pydantic import ValidationError

from alpha_market_engine.config.settings import Settings


BASE_ENV = {
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
    "DRY_RUN": "true",
    "AUTO_POST_ENABLED": "false",
    "MIN_FACT_CONFIDENCE": "85",
    "MIN_POST_QUALITY": "80",
    "MAX_DAILY_X_POSTS": "8",
}


def _apply_base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)


def test_missing_database_url_fails(monkeypatch: pytest.MonkeyPatch, tmp_path):
    _apply_base_env(monkeypatch)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert "DATABASE_URL" in str(exc_info.value)


def test_invalid_min_fact_confidence_fails(monkeypatch: pytest.MonkeyPatch):
    _apply_base_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://root@127.0.0.1:3306/alpha_market_test")
    monkeypatch.setenv("MIN_FACT_CONFIDENCE", "101")
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert "MIN_FACT_CONFIDENCE" in str(exc_info.value)


def test_invalid_min_post_quality_fails(monkeypatch: pytest.MonkeyPatch):
    _apply_base_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://root@127.0.0.1:3306/alpha_market_test")
    monkeypatch.setenv("MIN_POST_QUALITY", "-1")
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert "MIN_POST_QUALITY" in str(exc_info.value)


def test_invalid_max_daily_posts_fails(monkeypatch: pytest.MonkeyPatch):
    _apply_base_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://root@127.0.0.1:3306/alpha_market_test")
    monkeypatch.setenv("MAX_DAILY_X_POSTS", "0")
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert "MAX_DAILY_X_POSTS" in str(exc_info.value)


def test_invalid_market_timezone_fails(monkeypatch: pytest.MonkeyPatch):
    _apply_base_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://root@127.0.0.1:3306/alpha_market_test")
    monkeypatch.setenv("MARKET_TIMEZONE", "Mars/Phobos")
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert "invalid market timezone" in str(exc_info.value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("false", False), ("1", True), ("0", False)],
)
def test_auto_post_enabled_parsing(monkeypatch: pytest.MonkeyPatch, value: str, expected: bool):
    _apply_base_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://root@127.0.0.1:3306/alpha_market_test")
    monkeypatch.setenv("AUTO_POST_ENABLED", value)
    assert Settings().auto_post_enabled is expected
