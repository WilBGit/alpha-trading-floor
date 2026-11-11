from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

_DECIMAL_ZERO = Decimal("0")
_CHANGE_PERCENT_TOLERANCE = Decimal("0.25")


class MarketSession(str, Enum):
    PREMARKET = "PREMARKET"
    REGULAR = "REGULAR"
    AFTER_HOURS = "AFTER_HOURS"
    CLOSED = "CLOSED"


class DataQualityStatus(str, Enum):
    GOOD = "GOOD"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    INVALID = "INVALID"


class MarketQuote(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", arbitrary_types_allowed=True)

    symbol: str
    price: Decimal
    previous_close: Decimal | None = None
    change: Decimal | None = None
    change_percent: Decimal | None = None
    open_price: Decimal | None = None
    day_high: Decimal | None = None
    day_low: Decimal | None = None
    relative_volume: Decimal | None = None
    volume: int | None = None
    quote_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    provider: str = "unknown"
    data_feed: str | None = None
    is_delayed: bool = False
    vwap: Decimal | None = None
    trade_count: int | None = None
    validation_metadata: dict[str, Any] = Field(default_factory=dict)

    @staticmethod
    def _to_decimal(value: Any) -> Decimal:
        if isinstance(value, Decimal):
            decimal_value = value
        elif isinstance(value, bool):
            raise ValueError("decimal value must not be boolean")
        elif isinstance(value, int):
            decimal_value = Decimal(value)
        elif isinstance(value, float):
            decimal_value = Decimal(str(value))
        elif isinstance(value, str):
            decimal_value = Decimal(value)
        else:
            decimal_value = Decimal(str(value))
        if not decimal_value.is_finite():
            raise ValueError("decimal value must be finite")
        return decimal_value

    @model_validator(mode="after")
    def validate_model(self) -> "MarketQuote":
        if self.quote_timestamp.tzinfo is None or self.quote_timestamp.utcoffset() is None:
            raise ValueError("quote_timestamp must be timezone-aware")

        price = self._to_decimal(self.price)
        if price <= _DECIMAL_ZERO:
            raise ValueError("price must be greater than zero")
        object.__setattr__(self, "price", price)

        for field_name in ("previous_close", "change", "change_percent", "open_price", "day_high", "day_low", "vwap", "relative_volume"):
            value = getattr(self, field_name)
            if value is None:
                continue
            decimal_value = self._to_decimal(value)
            if decimal_value.is_nan() or not decimal_value.is_finite():
                raise ValueError(f"{field_name} must be finite")
            if field_name in {"previous_close", "open_price", "day_high", "day_low", "vwap", "relative_volume"} and decimal_value <= _DECIMAL_ZERO:
                raise ValueError(f"{field_name} must be greater than zero")
            object.__setattr__(self, field_name, decimal_value)

        if self.day_high is not None and self.day_low is not None and self.day_high < self.day_low:
            raise ValueError("day_high must be greater than or equal to day_low")
        if self.open_price is not None and self.open_price <= _DECIMAL_ZERO:
            raise ValueError("open_price must be greater than zero")
        if self.trade_count is not None and self.trade_count < 0:
            raise ValueError("trade_count must be non-negative")
        if self.volume is not None and self.volume < 0:
            raise ValueError("volume must be non-negative")
        if self.previous_close is not None and self.previous_close <= _DECIMAL_ZERO:
            raise ValueError("previous_close must be greater than zero")

        if self.previous_close is not None and self.change is None:
            object.__setattr__(self, "change", self.price - self.previous_close)
        if self.previous_close is not None:
            expected_change_percent = ((self.price - self.previous_close) / self.previous_close) * Decimal("100")
            if self.change_percent is None:
                object.__setattr__(self, "change_percent", expected_change_percent)
            else:
                reported = self._to_decimal(self.change_percent)
                object.__setattr__(self, "change_percent", reported)
                if abs(reported - expected_change_percent) > _CHANGE_PERCENT_TOLERANCE:
                    object.__setattr__(
                        self,
                        "validation_metadata",
                        {
                            **self.validation_metadata,
                            "change_percent_mismatch": True,
                            "expected_change_percent": expected_change_percent,
                            "change_percent_expected": expected_change_percent,
                            "change_percent_reported": reported,
                            "change_percent_delta": reported - expected_change_percent,
                        },
                    )
        elif self.change_percent is not None:
            object.__setattr__(self, "change_percent", self._to_decimal(self.change_percent))

        return self


class IntradayBar(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", arbitrary_types_allowed=True)

    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    provider: str
    data_feed: str | None = None
    session: MarketSession | None = None
    vwap: Decimal | None = None
    trade_count: int | None = None
    validation_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_model(self) -> "IntradayBar":
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        open_price = MarketQuote._to_decimal(self.open)
        high_price = MarketQuote._to_decimal(self.high)
        low_price = MarketQuote._to_decimal(self.low)
        close_price = MarketQuote._to_decimal(self.close)
        if open_price <= _DECIMAL_ZERO:
            raise ValueError("open must be greater than zero")
        if high_price <= _DECIMAL_ZERO:
            raise ValueError("high must be greater than zero")
        if low_price <= _DECIMAL_ZERO:
            raise ValueError("low must be greater than zero")
        if close_price <= _DECIMAL_ZERO:
            raise ValueError("close must be greater than zero")
        if high_price < low_price:
            raise ValueError("high must be greater than or equal to low")
        if self.volume < 0:
            raise ValueError("volume must be non-negative")
        if self.trade_count is not None and self.trade_count < 0:
            raise ValueError("trade_count must be non-negative")
        if self.vwap is not None:
            vwap = MarketQuote._to_decimal(self.vwap)
            if vwap <= _DECIMAL_ZERO:
                raise ValueError("vwap must be greater than zero")
            object.__setattr__(self, "vwap", vwap)
        object.__setattr__(self, "open", open_price)
        object.__setattr__(self, "high", high_price)
        object.__setattr__(self, "low", low_price)
        object.__setattr__(self, "close", close_price)
        return self


class MarketBreadth(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", arbitrary_types_allowed=True)

    advancers: int | None = None
    decliners: int | None = None
    up_volume: int | None = None
    down_volume: int | None = None
    breadth_score: Decimal | None = None
    provider: str | None = None
    data_feed: str | None = None
    quote_timestamp: datetime | None = None
    validation_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_model(self) -> "MarketBreadth":
        for field_name in ("advancers", "decliners", "up_volume", "down_volume"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        if self.breadth_score is not None:
            object.__setattr__(self, "breadth_score", MarketQuote._to_decimal(self.breadth_score))
        if self.quote_timestamp is not None and (self.quote_timestamp.tzinfo is None or self.quote_timestamp.utcoffset() is None):
            raise ValueError("quote_timestamp must be timezone-aware")
        return self


class SectorPerformance(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", arbitrary_types_allowed=True)

    sector: str
    change_percent: Decimal
    provider: str
    quote_timestamp: datetime
    data_feed: str | None = None
    validation_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_model(self) -> "SectorPerformance":
        object.__setattr__(self, "change_percent", MarketQuote._to_decimal(self.change_percent))
        if self.quote_timestamp.tzinfo is None or self.quote_timestamp.utcoffset() is None:
            raise ValueError("quote_timestamp must be timezone-aware")
        return self


class ProviderHealth(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", arbitrary_types_allowed=True)

    provider: str
    healthy: bool
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message: str | None = None
    data_feed: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


MarketIndexSnapshot = MarketQuote
MarketIndexQuote = MarketQuote
