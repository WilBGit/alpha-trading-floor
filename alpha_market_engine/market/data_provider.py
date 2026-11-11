from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from .models import (
    IntradayBar,
    MarketBreadth,
    MarketIndexQuote,
    MarketQuote,
    ProviderHealth,
    SectorPerformance,
)


class MarketDataError(RuntimeError):
    pass


class MarketDataAuthenticationError(MarketDataError):
    pass


class MarketDataRateLimitError(MarketDataError):
    pass


class MarketDataStaleError(MarketDataError):
    pass


class MarketDataValidationError(MarketDataError):
    pass


class MarketDataProvider(ABC):
    provider_name: str = "unknown"
    data_feed: str | None = None
    supports_batch_quotes: bool = True

    @abstractmethod
    async def get_quote(self, symbol: str) -> MarketQuote:
        raise NotImplementedError

    @abstractmethod
    async def get_quotes(self, symbols: list[str]) -> dict[str, MarketQuote]:
        raise NotImplementedError

    @abstractmethod
    async def get_intraday_bars(self, symbol: str, timeframe: str, limit: int | None = None) -> list[IntradayBar]:
        raise NotImplementedError

    @abstractmethod
    async def get_market_indices(self) -> dict[str, MarketQuote]:
        raise NotImplementedError

    @abstractmethod
    async def get_sector_performance(self) -> list[SectorPerformance] | None:
        raise NotImplementedError

    @abstractmethod
    async def get_market_breadth(self) -> MarketBreadth | None:
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> ProviderHealth:
        raise NotImplementedError


class FallbackQuoteProvider(MarketDataProvider):
    """Use the requested provider first and fill missing quotes from a certified fallback."""

    def __init__(self, primary: MarketDataProvider, fallback: MarketDataProvider) -> None:
        self.primary = primary
        self.fallback = fallback
        self.provider_name = primary.provider_name
        self.data_feed = primary.data_feed
        self.supports_batch_quotes = primary.supports_batch_quotes
        self.last_quote_failures: dict[str, str] = {}

    @staticmethod
    def _annotate(quote: MarketQuote, *, requested: str, active: str, fallback_used: bool, failure: str | None) -> MarketQuote:
        metadata = {
            **(quote.validation_metadata or {}),
            "requested_primary_provider": requested,
            "active_quote_provider": active,
            "fallback_used": fallback_used,
            "primary_failure_reason": failure,
        }
        return quote.model_copy(update={"validation_metadata": metadata})

    async def get_quote(self, symbol: str) -> MarketQuote:
        quotes = await self.get_quotes([symbol])
        quote = quotes.get(symbol.upper().strip())
        if quote is None:
            raise MarketDataValidationError(f"unavailable quote: {symbol}")
        return quote

    async def get_quotes(self, symbols: list[str]) -> dict[str, MarketQuote]:
        requested = [symbol.upper().strip() for symbol in symbols if symbol and symbol.strip()]
        primary_quotes: dict[str, MarketQuote] = {}
        primary_failure: str | None = None
        self.last_quote_failures = {}
        try:
            primary_quotes = await self.primary.get_quotes(requested)
        except MarketDataError as exc:
            primary_failure = f"{type(exc).__name__}: {exc}"
            if len(requested) > 1:
                for symbol in requested:
                    try:
                        primary_quotes[symbol] = await self.primary.get_quote(symbol)
                    except MarketDataError:
                        continue

        result = {
            symbol: self._annotate(
                quote,
                requested=self.primary.provider_name,
                active=self.primary.provider_name,
                fallback_used=False,
                failure=None,
            )
            for symbol, quote in primary_quotes.items()
        }
        missing = [symbol for symbol in requested if symbol not in result]
        for symbol in missing:
            try:
                quote = await self.fallback.get_quote(symbol)
            except MarketDataError:
                if primary_failure is None:
                    primary_failure = f"missing primary quote for {symbol}"
                self.last_quote_failures[symbol] = primary_failure or f"missing primary quote for {symbol}"
                continue
            result[symbol] = self._annotate(
                quote,
                requested=self.primary.provider_name,
                active=self.fallback.provider_name,
                fallback_used=True,
                failure=primary_failure or f"missing primary quote for {symbol}",
            )
        return result

    async def get_intraday_bars(self, symbol: str, timeframe: str, limit: int | None = None) -> list[IntradayBar]:
        return await self.primary.get_intraday_bars(symbol, timeframe, limit)

    async def get_market_indices(self) -> dict[str, MarketQuote]:
        return await self.get_quotes(["SPY", "QQQ", "DIA", "IWM"])

    async def get_sector_performance(self) -> list[SectorPerformance] | None:
        return await self.primary.get_sector_performance()

    async def get_market_breadth(self) -> MarketBreadth | None:
        return await self.primary.get_market_breadth()

    async def health_check(self) -> ProviderHealth:
        return await self.primary.health_check()


@dataclass(slots=True)
class CacheEntry:
    value: Any
    expires_at: datetime


class MarketDataCache(ABC):
    @abstractmethod
    def get(self, key: str) -> Any | None:
        raise NotImplementedError

    @abstractmethod
    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> ProviderHealth:
        raise NotImplementedError


class InMemoryMarketDataCache(MarketDataCache):
    def __init__(self) -> None:
        self._store: dict[str, CacheEntry] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if datetime.now(UTC) >= entry.expires_at:
            self._store.pop(key, None)
            return None
        return entry.value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._store[key] = CacheEntry(value=value, expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds))

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider="in-memory-cache", healthy=True, message="cache in memory", metadata={"entries": len(self._store)})


try:  # pragma: no cover - optional runtime dependency
    import redis
except Exception:  # pragma: no cover
    redis = None


class RedisMarketDataCache(MarketDataCache):
    def __init__(self, redis_url: str, namespace: str = "alpha:market:data") -> None:
        if redis is None:
            raise RuntimeError("redis package is not installed")
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)
        self._namespace = namespace

    def _key(self, key: str) -> str:
        return f"{self._namespace}:{key}"

    def get(self, key: str) -> Any | None:
        raw = self._client.get(self._key(key))
        if raw is None:
            return None
        import json

        return json.loads(raw)

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        import json

        self._client.setex(self._key(key), ttl_seconds, json.dumps(value, default=str))

    def delete(self, key: str) -> None:
        self._client.delete(self._key(key))

    def health_check(self) -> ProviderHealth:
        try:
            pong = self._client.ping()
            return ProviderHealth(provider="redis", healthy=bool(pong), message="redis reachable", metadata={"namespace": self._namespace})
        except Exception as exc:  # pragma: no cover - depends on external redis
            return ProviderHealth(provider="redis", healthy=False, message=str(exc), metadata={"namespace": self._namespace})


def build_market_cache_key(
    provider: str,
    data_type: str,
    symbol: str | None = None,
    timeframe: str | None = None,
    feed: str | None = None,
) -> str:
    parts = ["provider", provider, data_type]
    if symbol:
        parts.append(symbol)
    if timeframe:
        parts.append(timeframe)
    if feed:
        parts.append(feed)
    return ":".join(parts)


class StaticMarketDataProvider(MarketDataProvider):
    provider_name = "static"
    data_feed = "synthetic"

    def __init__(
        self,
        quotes: dict[str, MarketQuote],
        indices: dict[str, MarketQuote] | None = None,
        breadth: MarketBreadth | None = None,
        sector_performance: list[SectorPerformance] | dict[str, Decimal | float | int] | None = None,
        bars: dict[str, list[IntradayBar]] | None = None,
        health: ProviderHealth | None = None,
    ) -> None:
        self._quotes = quotes
        self._indices = indices or {symbol: quote for symbol, quote in quotes.items() if symbol in {"SPY", "QQQ", "DIA", "IWM", "VIX"}}
        self._breadth = breadth
        self._bars = bars or {}
        self._health = health or ProviderHealth(provider=self.provider_name, healthy=True, data_feed=self.data_feed)
        if isinstance(sector_performance, dict):
            now = datetime.now(UTC)
            self._sector_performance = [
                SectorPerformance(sector=sector, change_percent=Decimal(str(value)), provider=self.provider_name, quote_timestamp=now, data_feed=self.data_feed)
                for sector, value in sector_performance.items()
            ]
        else:
            self._sector_performance = list(sector_performance or [])

    async def get_quote(self, symbol: str) -> MarketQuote:
        if symbol not in self._quotes:
            raise MarketDataValidationError(f"missing symbol: {symbol}")
        return self._quotes[symbol]

    async def get_quotes(self, symbols: list[str]) -> dict[str, MarketQuote]:
        result: dict[str, MarketQuote] = {}
        missing: list[str] = []
        for symbol in symbols:
            quote = self._quotes.get(symbol)
            if quote is None:
                missing.append(symbol)
                continue
            result[symbol] = quote
        if not result:
            raise MarketDataValidationError(f"missing symbols: {', '.join(missing or symbols)}")
        return result

    async def get_intraday_bars(self, symbol: str, timeframe: str, limit: int | None = None) -> list[IntradayBar]:
        bars = list(self._bars.get(symbol, []))
        if limit is not None:
            return bars[-limit:]
        return bars

    async def get_market_indices(self) -> dict[str, MarketQuote]:
        return dict(self._indices)

    async def get_sector_performance(self) -> list[SectorPerformance] | None:
        return list(self._sector_performance)

    async def get_market_breadth(self) -> MarketBreadth | None:
        return self._breadth

    async def health_check(self) -> ProviderHealth:
        return self._health


async def maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return await value
    return value
