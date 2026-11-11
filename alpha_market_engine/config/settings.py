from __future__ import annotations

from functools import lru_cache
from zoneinfo import available_timezones

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = Field(..., alias="DATABASE_URL")
    redis_url: str = Field(..., alias="REDIS_URL")

    ai_provider: str = Field(..., alias="AI_PROVIDER")
    ai_model: str = Field(..., alias="AI_MODEL")
    ai_api_key: str = Field(..., alias="AI_API_KEY")
    ai_base_url: str = Field(..., alias="AI_BASE_URL")

    x_client_id: str = Field(..., alias="X_CLIENT_ID")
    x_client_secret: str = Field(..., alias="X_CLIENT_SECRET")
    x_access_token: str = Field(..., alias="X_ACCESS_TOKEN")
    x_access_token_secret: str = Field(..., alias="X_ACCESS_TOKEN_SECRET")
    x_bearer_token: str = Field(..., alias="X_BEARER_TOKEN")
    x_oauth_redirect_uri: str = Field("http://127.0.0.1:8765/oauth/callback", alias="X_OAUTH_REDIRECT_URI")
    x_oauth_scopes: str = Field("tweet.read tweet.write users.read offline.access", alias="X_OAUTH_SCOPES")
    x_expected_username: str = Field("", alias="X_EXPECTED_USERNAME")

    market_data_provider: str = Field(..., alias="MARKET_DATA_PROVIDER")
    market_data_api_key: str = Field(..., alias="MARKET_DATA_API_KEY")

    stocktwits_enabled: bool = Field(True, alias="STOCKTWITS_ENABLED")
    auto_post_enabled: bool = Field(False, alias="AUTO_POST_ENABLED")
    dry_run: bool = Field(True, alias="DRY_RUN")

    min_fact_confidence: int = Field(85, alias="MIN_FACT_CONFIDENCE", ge=0, le=100)
    min_post_quality: int = Field(80, alias="MIN_POST_QUALITY", ge=0, le=100)
    max_daily_x_posts: int = Field(8, alias="MAX_DAILY_X_POSTS", ge=1, le=50)

    market_timezone: str = Field("America/Los_Angeles", alias="MARKET_TIMEZONE")

    @field_validator(
        "database_url",
        "redis_url",
        "ai_provider",
        "ai_model",
        "ai_api_key",
        "ai_base_url",
        "x_client_id",
        "x_client_secret",
        "x_access_token",
        "x_access_token_secret",
        "x_bearer_token",
        "market_data_provider",
        "market_data_api_key",
        "market_timezone",
        mode="before",
    )
    @classmethod
    def ensure_non_empty(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("required environment variable is missing or blank")
        return text

    @field_validator("market_timezone", mode="before")
    @classmethod
    def ensure_valid_timezone(cls, value: object) -> str:
        text = cls.ensure_non_empty(value)
        if text not in available_timezones():
            raise ValueError(f"invalid market timezone: {text}")
        return text


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
