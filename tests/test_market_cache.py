from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from alpha_market_engine.market.cache import InMemoryMarketDataCache
from alpha_market_engine.market.data_provider import (
    MarketDataAuthenticationError,
    MarketDataProvider,
    MarketDataRateLimitError,
    MarketDataValidationError,
    StaticMarketDataProvider,
)
from alpha_market_engine.market.models import MarketBreadth, MarketQuote, ProviderHealth, SectorPerformance
from alpha_market_engine.market.snapshot import MarketDataService


class CountingProvider(MarketDataProvider):
    provider_name = "counting"
    data_feed = "iex"

    def __init__(self, quote: MarketQuote):
        self.quote = quote
        self.quote_calls = 0
        self.quotes_calls = 0

    async def get_quote(self, symbol: str) -> MarketQuote:
        self.quote_calls += 1
        if symbol != self.quote.symbol:
            raise MarketDataValidationError(symbol)
        return self.quote

    async def get_quotes(self, symbols: list[str]) -> dict[str, MarketQuote]:
        self.quotes_calls += 1
        return {s: self.quote for s in symbols if s == self.quote.symbol}

    async def get_intraday_bars(self, symbol: str, timeframe: str, limit: int | None = None):
        return []

    async def get_market_indices(self):
        return {self.quote.symbol: self.quote}

    async def get_sector_performance(self):
        return None

    async def get_market_breadth(self):
        return None

    async def health_check(self):
        return ProviderHealth(provider=self.provider_name, healthy=True)


class ExpiringCache(InMemoryMarketDataCache):
    def __init__(self):
        super().__init__()
        self._expire_next_access: set[str] = set()

    def set(self, key: str, value, ttl_seconds: int) -> None:
        super().set(key, value, ttl_seconds)
        self._expire_next_access.add(key)

    def get(self, key: str):
        if key in self._expire_next_access:
            self._expire_next_access.remove(key)
            return None
        return super().get(key)


class AuthErrorProvider(MarketDataProvider):
    provider_name = "auth-error"
    data_feed = "iex"

    async def get_quote(self, symbol: str):
        raise MarketDataAuthenticationError("bad credentials")

    async def get_quotes(self, symbols: list[str]):
        raise MarketDataAuthenticationError("bad credentials")

    async def get_intraday_bars(self, symbol: str, timeframe: str, limit: int | None = None):
        raise MarketDataAuthenticationError("bad credentials")

    async def get_market_indices(self):
        raise MarketDataAuthenticationError("bad credentials")

    async def get_sector_performance(self):
        raise MarketDataAuthenticationError("bad credentials")

    async def get_market_breadth(self):
        raise MarketDataAuthenticationError("bad credentials")

    async def health_check(self):
        return ProviderHealth(provider=self.provider_name, healthy=False, message="bad credentials")


class RateLimitProvider(AuthErrorProvider):
    provider_name = "rate-limit"

    async def get_quote(self, symbol: str):
        raise MarketDataRateLimitError("rate limited")

    async def get_quotes(self, symbols: list[str]):
        raise MarketDataRateLimitError("rate limited")

    async def get_intraday_bars(self, symbol: str, timeframe: str, limit: int | None = None):
        raise MarketDataRateLimitError("rate limited")

    async def get_market_indices(self):
        raise MarketDataRateLimitError("rate limited")

    async def get_sector_performance(self):
        raise MarketDataRateLimitError("rate limited")

    async def get_market_breadth(self):
        raise MarketDataRateLimitError("rate limited")


@pytest.mark.asyncio
async def test_cache_hit_avoids_provider_request():
    quote = MarketQuote(symbol="SPY", price=Decimal("620"), previous_close=Decimal("610"), quote_timestamp=datetime.now(UTC), provider="counting")
    provider = CountingProvider(quote)
    service = MarketDataService(provider, cache=InMemoryMarketDataCache())

    first = await service.get_quote("SPY")
    second = await service.get_quote("SPY")

    assert first.price == second.price
    assert provider.quote_calls == 1


@pytest.mark.asyncio
async def test_cache_expiration_causes_provider_request():
    quote = MarketQuote(symbol="SPY", price=Decimal("620"), previous_close=Decimal("610"), quote_timestamp=datetime.now(UTC), provider="counting")
    service = MarketDataService(CountingProvider(quote), cache=ExpiringCache())

    await service.get_quote("SPY")
    await service.get_quote("SPY")

    assert service.provider.quote_calls == 2


@pytest.mark.asyncio
async def test_batch_quote_partial_result_handling():
    quote = MarketQuote(symbol="SPY", price=Decimal("620"), previous_close=Decimal("610"), quote_timestamp=datetime.now(UTC), provider="static")
    provider = StaticMarketDataProvider(quotes={"SPY": quote}, breadth=MarketBreadth(advancers=1, decliners=1), sector_performance=[SectorPerformance(sector="Technology", change_percent=Decimal("1.0"), provider="static", quote_timestamp=datetime.now(UTC))])
    service = MarketDataService(provider, cache=InMemoryMarketDataCache())

    quotes = await service.get_quotes(["SPY", "QQQ"])

    assert set(quotes) == {"SPY"}


@pytest.mark.asyncio
async def test_authentication_error_propagates():
    service = MarketDataService(AuthErrorProvider(), cache=InMemoryMarketDataCache())
    with pytest.raises(MarketDataAuthenticationError):
        await service.get_quote("SPY")


@pytest.mark.asyncio
async def test_rate_limit_error_propagates():
    service = MarketDataService(RateLimitProvider(), cache=InMemoryMarketDataCache())
    with pytest.raises(MarketDataRateLimitError):
        await service.get_quote("SPY")
