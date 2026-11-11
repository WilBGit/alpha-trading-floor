from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

try:  # pragma: no cover - optional structured logging dependency
    import structlog
except Exception:  # pragma: no cover
    structlog = None

from pydantic import BaseModel, ConfigDict, Field

from .cache import InMemoryMarketDataCache
from .data_provider import (
    MarketDataAuthenticationError,
    MarketDataCache,
    MarketDataError,
    MarketDataProvider,
    MarketDataRateLimitError,
    MarketDataStaleError,
    MarketDataValidationError,
    build_market_cache_key,
)
from .models import (
    DataQualityStatus,
    IntradayBar,
    MarketBreadth,
    MarketIndexQuote,
    MarketQuote,
    MarketSession,
    ProviderHealth,
    SectorPerformance,
)
from .session import MarketSessionCalculator
from .streaming.state import (
    DelayStatus,
    DeliveryMode,
    FeedScope,
    FreshnessStatus,
    LiveMarketStateStore,
    MarketDataSourceMetadata,
    VWAPMethod,
    VWAPQuality,
    calculate_session_vwap,
)


if structlog is not None:  # pragma: no cover - logging backend
    log: Any = structlog.get_logger(__name__)
else:  # pragma: no cover - fallback
    import logging

    class _FallbackLogger:
        def __init__(self) -> None:
            self._logger = logging.getLogger(__name__)

        def info(self, event: str, **fields: Any) -> None:
            self._logger.info("%s %s", event, fields)

        def warning(self, event: str, **fields: Any) -> None:
            self._logger.warning("%s %s", event, fields)

        def error(self, event: str, **fields: Any) -> None:
            self._logger.error("%s %s", event, fields)

        def debug(self, event: str, **fields: Any) -> None:
            self._logger.debug("%s %s", event, fields)

    log = _FallbackLogger()


QUOTE_TTL_SECONDS = 20
BAR_TTL_SECONDS = 45
SECTOR_TTL_SECONDS = 180
BREADTH_TTL_SECONDS = 90


@dataclass(slots=True)
class QuoteFreshness:
    symbol: str
    age_seconds: int
    stale: bool
    reference_timestamp: datetime | None = None
    reference_session: str | None = None


class MarketDataService:
    def __init__(
        self,
        provider: MarketDataProvider,
        cache: MarketDataCache | None = None,
        session_calculator: MarketSessionCalculator | None = None,
        clock=None,
    ) -> None:
        self.provider = provider
        self.cache = cache or InMemoryMarketDataCache()
        self.session_calculator = session_calculator or MarketSessionCalculator()
        self._clock = clock or (lambda: datetime.now(UTC))
        self.last_telemetry: dict[str, Any] = {"events": []}

    def _now(self) -> datetime:
        return self._clock()

    def _freshness_tolerance_seconds(self, session: MarketSession) -> int:
        return self.session_calculator.freshness_tolerance_seconds(session)

    def _quote_is_fresh(self, quote: MarketQuote, session: MarketSession) -> QuoteFreshness:
        now = self._now()
        age_seconds = max(0, int((now - quote.quote_timestamp).total_seconds()))

        if session == MarketSession.REGULAR:
            stale = age_seconds > self._freshness_tolerance_seconds(session)
            return QuoteFreshness(symbol=quote.symbol, age_seconds=age_seconds, stale=stale)

        reference_close = self.session_calculator.most_recent_completed_session_close(now)
        quote_date = quote.quote_timestamp.astimezone(self.session_calculator.timezone).date()
        stale = quote_date < reference_close.date()
        return QuoteFreshness(
            symbol=quote.symbol,
            age_seconds=age_seconds,
            stale=stale,
            reference_timestamp=reference_close,
            reference_session=reference_close.date().isoformat(),
        )

    async def _cached(self, key: str, ttl_seconds: int, fetcher) -> tuple[Any, bool]:
        cached = self.cache.get(key)
        event: dict[str, Any] = {"cache_key": key, "ttl_seconds": ttl_seconds}
        if cached is not None:
            event.update({"cache_hit": True, "provider_request": False})
            self.last_telemetry.setdefault("events", []).append(event)
            log.info("market_cache_hit", key=key)
            return cached, True
        event.update({"cache_hit": False, "provider_request": True})
        self.last_telemetry.setdefault("events", []).append(event)
        log.info("market_cache_miss", key=key)
        value = await fetcher()
        self.cache.set(key, value, ttl_seconds)
        return value, False

    async def get_quote(self, symbol: str, *, session: MarketSession | None = None) -> MarketQuote:
        session = session or self.session_calculator.current_session(self._now())
        key = build_market_cache_key(self.provider.provider_name, "quote", symbol=symbol, feed=self.provider.data_feed)

        async def fetch() -> MarketQuote:
            started = self._now()
            quote = await self.provider.get_quote(symbol)
            latency_ms = int((self._now() - started).total_seconds() * 1000)
            freshness = self._quote_is_fresh(quote, session)
            quote.validation_metadata = {
                **quote.validation_metadata,
                "freshness": asdict(freshness),
                "provider_latency_ms": latency_ms,
            }
            if freshness.stale:
                quote.validation_metadata = {**quote.validation_metadata, "stale": True}
            return quote

        quote, _ = await self._cached(key, QUOTE_TTL_SECONDS, fetch)
        freshness = self._quote_is_fresh(quote, session)
        if freshness.stale:
            log.warning("market_quote_stale", symbol=symbol, age_seconds=freshness.age_seconds, session=session.value)
        return quote

    async def get_quotes(self, symbols: list[str], *, session: MarketSession | None = None) -> dict[str, MarketQuote]:
        session = session or self.session_calculator.current_session(self._now())
        symbols = list(dict.fromkeys(symbols))
        if not symbols:
            return {}
        key = build_market_cache_key(self.provider.provider_name, "quotes", symbol="-".join(sorted(symbols)), feed=self.provider.data_feed)

        async def fetch() -> dict[str, MarketQuote]:
            started = self._now()
            quotes = await self.provider.get_quotes(symbols)
            latency_ms = int((self._now() - started).total_seconds() * 1000)
            result: dict[str, MarketQuote] = {}
            for symbol, quote in quotes.items():
                freshness = self._quote_is_fresh(quote, session)
                quote.validation_metadata = {
                    **quote.validation_metadata,
                    "freshness": asdict(freshness),
                    "provider_latency_ms": latency_ms,
                }
                if freshness.stale:
                    quote.validation_metadata = {**quote.validation_metadata, "stale": True}
                result[symbol] = quote
            return result

        quotes, _ = await self._cached(key, QUOTE_TTL_SECONDS, fetch)
        for symbol, quote in quotes.items():
            freshness = self._quote_is_fresh(quote, session)
            if freshness.stale:
                log.warning("market_quote_stale", symbol=symbol, age_seconds=freshness.age_seconds, session=session.value)
        return quotes

    async def get_intraday_bars(self, symbol: str, timeframe: str, limit: int | None = None) -> list[IntradayBar]:
        key = build_market_cache_key(self.provider.provider_name, "bars", symbol=symbol, timeframe=f"{timeframe}:{limit if limit is not None else 'none'}", feed=self.provider.data_feed)

        async def fetch() -> list[IntradayBar]:
            started = self._now()
            bars = await self.provider.get_intraday_bars(symbol, timeframe, limit)
            latency_ms = int((self._now() - started).total_seconds() * 1000)
            for bar in bars:
                bar.validation_metadata = {"provider_latency_ms": latency_ms} if hasattr(bar, "validation_metadata") else {}
            return bars

        bars, _ = await self._cached(key, BAR_TTL_SECONDS, fetch)
        return bars

    async def get_market_indices(self) -> dict[str, MarketQuote]:
        key = build_market_cache_key(self.provider.provider_name, "market_indices", feed=self.provider.data_feed)

        async def fetch() -> dict[str, MarketQuote]:
            started = self._now()
            quotes = await self.provider.get_market_indices()
            latency_ms = int((self._now() - started).total_seconds() * 1000)
            result: dict[str, MarketQuote] = {}
            for symbol, quote in quotes.items():
                freshness = self._quote_is_fresh(quote, self.session_calculator.current_session(self._now()))
                quote.validation_metadata = {
                    **quote.validation_metadata,
                    "freshness": asdict(freshness),
                    "provider_latency_ms": latency_ms,
                }
                if freshness.stale:
                    quote.validation_metadata = {**quote.validation_metadata, "stale": True}
                result[symbol] = quote
            return result

        quotes, _ = await self._cached(key, QUOTE_TTL_SECONDS, fetch)
        return quotes

    async def get_sector_performance(self) -> list[SectorPerformance] | None:
        key = build_market_cache_key(self.provider.provider_name, "sector_performance", feed=self.provider.data_feed)

        async def fetch() -> list[SectorPerformance] | None:
            started = self._now()
            sectors = await self.provider.get_sector_performance()
            latency_ms = int((self._now() - started).total_seconds() * 1000)
            if sectors is None:
                return None
            result: list[SectorPerformance] = []
            for sector in sectors:
                result.append(sector.model_copy(update={"validation_metadata": {"provider_latency_ms": latency_ms}}) if hasattr(sector, "validation_metadata") else sector)
            return result

        sectors, _ = await self._cached(key, SECTOR_TTL_SECONDS, fetch)
        return sectors

    async def get_market_breadth(self) -> MarketBreadth | None:
        key = build_market_cache_key(self.provider.provider_name, "breadth", feed=self.provider.data_feed)

        async def fetch() -> MarketBreadth | None:
            started = self._now()
            breadth = await self.provider.get_market_breadth()
            latency_ms = int((self._now() - started).total_seconds() * 1000)
            if breadth is None:
                return None
            return breadth.model_copy(update={"validation_metadata": {"provider_latency_ms": latency_ms}})

        breadth, _ = await self._cached(key, BREADTH_TTL_SECONDS, fetch)
        return breadth

    async def health_check(self) -> ProviderHealth:
        return await self.provider.health_check()


class SnapshotSymbol(MarketQuote):
    above_vwap: bool | None = None
    session_vwap: Decimal | None = None
    bar_vwap: Decimal | None = None
    distance_from_vwap: Decimal | None = None
    bars_count: int | None = None
    price_source: str | None = None
    bar_source: str | None = None
    vwap_method: str | None = None
    vwap_quality: str | None = None
    data_feed_kind: str | None = None
    feed_scope: str | None = None
    delivery_mode: str | None = None
    delay_status: str | None = None
    freshness_status: str | None = None


class NormalizedMarketSnapshot(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="ignore")

    snapshot_time: datetime
    market_session: MarketSession
    spy: SnapshotSymbol | None
    qqq: SnapshotSymbol | None
    dia: SnapshotSymbol | None
    iwm: SnapshotSymbol | None
    vix: SnapshotSymbol | None = None
    market_breadth: MarketBreadth | None = None
    sector_performance: list[SectorPerformance] = Field(default_factory=list)
    provider: str
    data_feed: str | None = None
    oldest_data_timestamp: datetime | None = None
    newest_data_timestamp: datetime | None = None
    data_age_seconds: int | None = None
    data_quality_status: DataQualityStatus = DataQualityStatus.INVALID
    missing_fields: list[str] = Field(default_factory=list)
    leading_index: str = "UNKNOWN"
    lagging_index: str = "UNKNOWN"
    tech_outperforming: bool = False
    small_caps_participating: bool = False
    volatility_increasing: bool = False
    broad_market: bool = False
    volume_confirming: bool = False
    market_green: bool = False
    market_red: bool = False
    character_changed: bool | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)


class MarketSnapshotService:
    core_symbols = ("SPY", "QQQ", "DIA", "IWM")

    def __init__(
        self,
        provider: MarketDataProvider,
        bar_provider: MarketDataProvider | None = None,
        cache: MarketDataCache | None = None,
        session_calculator: MarketSessionCalculator | None = None,
        repository: Any | None = None,
        store_stale_diagnostics: bool = False,
        clock=None,
        live_state_store: LiveMarketStateStore | None = None,
    ) -> None:
        self.provider = provider
        self.bar_provider = bar_provider or provider
        self.data = MarketDataService(provider, cache=cache, session_calculator=session_calculator, clock=clock)
        self.bar_data = MarketDataService(self.bar_provider, cache=cache, session_calculator=self.data.session_calculator, clock=clock)
        self.session_calculator = self.data.session_calculator
        self.repository = repository
        self.store_stale_diagnostics = store_stale_diagnostics
        self.live_state_store = live_state_store
        self.last_source_quotes: dict[str, MarketQuote] = {}
        self.last_source_bars: dict[str, list[IntradayBar]] = {}
        self.last_source_raw_bars: dict[str, list[IntradayBar]] = {}

    @staticmethod
    def _quote_raw(quote: MarketQuote | None) -> dict[str, Any] | None:
        if quote is None:
            return None
        return {
            "symbol": quote.symbol,
            "price": float(quote.price),
            "previous_close": float(quote.previous_close) if quote.previous_close is not None else None,
            "change": float(quote.change) if quote.change is not None else None,
            "change_percent": float(quote.change_percent) if quote.change_percent is not None else None,
            "open_price": float(quote.open_price) if quote.open_price is not None else None,
            "day_high": float(quote.day_high) if quote.day_high is not None else None,
            "day_low": float(quote.day_low) if quote.day_low is not None else None,
            "relative_volume": float(quote.relative_volume) if quote.relative_volume is not None else None,
            "volume": quote.volume,
            "quote_timestamp": quote.quote_timestamp.isoformat(),
            "provider": quote.provider,
            "data_feed": quote.data_feed,
            "is_delayed": quote.is_delayed,
            "vwap": float(quote.vwap) if quote.vwap is not None else None,
            "trade_count": quote.trade_count,
        }

    def _bars_for_session(self, bars: list[IntradayBar], session_label) -> list[IntradayBar]:
        session_open = self.session_calculator.calendar.session_open(session_label).to_pydatetime().astimezone(self.session_calculator.timezone)
        session_close = self.session_calculator.calendar.session_close(session_label).to_pydatetime().astimezone(self.session_calculator.timezone)
        filtered: list[IntradayBar] = []
        for bar in bars:
            timestamp = bar.timestamp.astimezone(self.session_calculator.timezone)
            if session_open <= timestamp <= session_close:
                filtered.append(bar)
        return filtered

    @staticmethod
    def _to_snapshot_symbol(
        quote: MarketQuote,
        bars: list[IntradayBar],
        session_vwap: Decimal | None,
        *,
        price_source: str,
        bar_source: str,
        vwap_method: str,
        vwap_quality: str,
        bar_vwap: Decimal | None,
        data_feed_kind: str | None = None,
        feed_scope: str | None = None,
        delivery_mode: str | None = None,
        delay_status: str | None = None,
        freshness_status: str | None = None,
    ) -> SnapshotSymbol:
        above_vwap: bool | None = None
        distance_from_vwap: Decimal | None = None
        if session_vwap is not None and session_vwap > 0:
            above_vwap = quote.price >= session_vwap
            distance_from_vwap = ((quote.price - session_vwap) / session_vwap) * Decimal("100")
        return SnapshotSymbol(
            **quote.model_dump(),
            above_vwap=above_vwap,
            session_vwap=session_vwap,
            bar_vwap=bar_vwap,
            distance_from_vwap=distance_from_vwap,
            bars_count=len(bars) if bars else None,
            price_source=price_source,
            bar_source=bar_source,
            vwap_method=vwap_method,
            vwap_quality=vwap_quality,
            data_feed_kind=data_feed_kind,
            feed_scope=feed_scope,
            delivery_mode=delivery_mode,
            delay_status=delay_status,
            freshness_status=freshness_status,
        )

    @staticmethod
    def _session_vwap_from_bars(bars: list[IntradayBar]) -> Decimal | None:
        total_volume = sum(bar.volume for bar in bars if bar.volume > 0)
        if total_volume <= 0:
            return None
        numerator = Decimal("0")
        for bar in bars:
            if bar.volume <= 0:
                continue
            typical_price = (bar.high + bar.low + bar.close) / Decimal("3")
            numerator += typical_price * Decimal(bar.volume)
        return numerator / Decimal(total_volume)

    @staticmethod
    def _data_quality_from_flags(stale_symbols: list[str], invalid_symbols: list[str], missing_fields: list[str], core_ready: bool) -> DataQualityStatus:
        if invalid_symbols or not core_ready:
            return DataQualityStatus.INVALID
        if stale_symbols:
            return DataQualityStatus.STALE
        if missing_fields:
            return DataQualityStatus.DEGRADED
        return DataQualityStatus.GOOD

    @staticmethod
    def _index_order(quotes: dict[str, SnapshotSymbol]) -> tuple[str, str]:
        ordered = sorted(((symbol, quote.change_percent or Decimal("0")) for symbol, quote in quotes.items()), key=lambda item: item[1], reverse=True)
        if not ordered:
            return "UNKNOWN", "UNKNOWN"
        return ordered[0][0], ordered[-1][0]

    @staticmethod
    def _compute_character_changed(current: dict[str, SnapshotSymbol], previous_snapshot: NormalizedMarketSnapshot | None) -> bool | None:
        if previous_snapshot is None:
            return None
        previous = {
            "SPY": previous_snapshot.spy,
            "QQQ": previous_snapshot.qqq,
            "DIA": previous_snapshot.dia,
            "IWM": previous_snapshot.iwm,
        }
        deltas = []
        for symbol, quote in current.items():
            prior = previous.get(symbol)
            if prior is None:
                continue
            deltas.append(abs((quote.change_percent or Decimal("0")) - (prior.change_percent or Decimal("0"))))
        return any(delta >= Decimal("0.50") for delta in deltas)

    async def create_snapshot(self, previous_snapshot: NormalizedMarketSnapshot | None = None) -> NormalizedMarketSnapshot:
        session = self.session_calculator.current_session(self.data._now())
        now = self.data._now()
        current_ny = self.session_calculator._to_ny_datetime(now)
        session_label = (
            self.session_calculator.calendar.date_to_session(current_ny.date(), direction="none")
            if session == MarketSession.REGULAR
            else self.session_calculator.most_recent_completed_session(now)
        )

        try:
            quotes = await self.data.get_market_indices()
            breadth = await self.data.get_market_breadth()
            sector_performance = await self.data.get_sector_performance()
            bars_tasks = [self.bar_data.get_intraday_bars(symbol, "1Min", limit=390) for symbol in self.core_symbols]
            bars_result = await asyncio.gather(*bars_tasks, return_exceptions=True)
        except (MarketDataAuthenticationError, MarketDataRateLimitError, MarketDataValidationError, MarketDataError):
            raise

        self.last_source_raw_bars = {
            symbol: [] if isinstance(bar_list, BaseException) else list(bar_list)
            for symbol, bar_list in zip(self.core_symbols, bars_result)
        }

        core_quotes: dict[str, SnapshotSymbol] = {}
        quote_timestamps: dict[str, str] = {}
        quote_ages: dict[str, int] = {}
        stale_symbols: list[str] = []
        invalid_symbols: list[str] = []
        missing_fields: list[str] = []
        bars_by_symbol: dict[str, list[IntradayBar]] = {}
        latest_bar_timestamps: dict[str, str] = {}

        for symbol, bar_list in zip(self.core_symbols, bars_result):
            if isinstance(bar_list, Exception):
                bar_list = []
            session_bars = self._bars_for_session(bar_list, session_label)
            bars_by_symbol[symbol] = session_bars
            if session_bars:
                latest_bar_timestamps[symbol] = session_bars[-1].timestamp.isoformat()

        self.last_source_quotes = dict(quotes)
        self.last_source_bars = {symbol: list(values) for symbol, values in bars_by_symbol.items()}

        session_open = self.session_calculator.calendar.session_open(session_label).to_pydatetime().astimezone(self.session_calculator.timezone)
        session_close = self.session_calculator.calendar.session_close(session_label).to_pydatetime().astimezone(self.session_calculator.timezone)
        coverage_close = session_close if session != MarketSession.REGULAR else min(session_close, current_ny.replace(second=0, microsecond=0))

        for symbol in self.core_symbols:
            quote = quotes.get(symbol)
            if quote is None:
                invalid_symbols.append(symbol)
                missing_fields.append(f"{symbol.lower()}_quote")
                continue
            freshness = self.data._quote_is_fresh(quote, session)
            quote_timestamps[symbol] = quote.quote_timestamp.isoformat()
            quote_ages[symbol] = freshness.age_seconds
            if freshness.stale:
                stale_symbols.append(symbol)

            source_quote = quote
            price_source = "REST_QUOTE"
            bar_source = "REST_BAR" if self.bar_provider is self.provider else f"{self.bar_provider.provider_name.upper()}_BAR"
            bar_vwap = bars_by_symbol.get(symbol, [])[-1].vwap if bars_by_symbol.get(symbol, []) else None
            freshness_status = "STALE" if freshness.stale else "FRESH"
            data_feed_kind = quote.data_feed
            feed_scope = None
            delivery_mode = "REST"
            delay_status = "DELAYED" if quote.is_delayed else "REALTIME"
            if self.live_state_store is not None and session != MarketSession.CLOSED:
                live_state = self.live_state_store.get_symbol_state(symbol)
                if live_state.freshness(now=now, stale_after_seconds=30) == FreshnessStatus.FRESH:
                    live_trade = live_state.latest_trade_price
                    live_bar = live_state.latest_bar
                    if live_trade is not None and live_state.latest_trade_timestamp is not None:
                        source_data = quote.model_dump()
                        source_data.update(
                            {
                                "price": live_trade,
                                "quote_timestamp": live_state.latest_trade_timestamp,
                                "provider": live_state.provider or quote.provider,
                                "data_feed": live_state.feed or quote.data_feed,
                                "is_delayed": live_state.delay_status == DelayStatus.DELAYED,
                                "validation_metadata": {
                                    **quote.validation_metadata,
                                    "price_source": "WEBSOCKET_TRADE",
                                    "feed_scope": live_state.feed_scope.value,
                                    "delivery_mode": live_state.delivery_mode.value,
                                    "delay_status": live_state.delay_status.value,
                                },
                            }
                        )
                        source_quote = MarketQuote(**source_data)
                        price_source = "WEBSOCKET_TRADE"
                        bar_source = "WEBSOCKET_BAR" if live_bar is not None else "REST_BAR"
                        if live_bar is not None:
                            bar_vwap = live_bar.vwap
                    elif live_bar is not None:
                        source_data = quote.model_dump()
                        source_data.update(
                            {
                                "price": live_bar.close,
                                "quote_timestamp": live_bar.timestamp,
                                "provider": live_state.provider or quote.provider,
                                "data_feed": live_state.feed or quote.data_feed,
                                "is_delayed": live_state.delay_status == DelayStatus.DELAYED,
                                "validation_metadata": {
                                    **quote.validation_metadata,
                                    "price_source": "WEBSOCKET_BAR",
                                    "feed_scope": live_state.feed_scope.value,
                                    "delivery_mode": live_state.delivery_mode.value,
                                    "delay_status": live_state.delay_status.value,
                                },
                            }
                        )
                        source_quote = MarketQuote(**source_data)
                        price_source = "WEBSOCKET_BAR"
                        bar_source = "WEBSOCKET_BAR"
                        bar_vwap = live_bar.vwap
                    feed_scope = live_state.feed_scope.value
                    delivery_mode = live_state.delivery_mode.value
                    delay_status = live_state.delay_status.value
                    data_feed_kind = live_state.feed_kind.value
                    freshness_status = live_state.freshness(now=now, stale_after_seconds=30).value

            session_vwap_result = calculate_session_vwap(bars_by_symbol.get(symbol, []), session_open=session_open, session_close=coverage_close)
            core_quotes[symbol] = self._to_snapshot_symbol(
                source_quote,
                bars_by_symbol.get(symbol, []),
                session_vwap_result.vwap,
                price_source=price_source,
                bar_source=bar_source,
                vwap_method=session_vwap_result.method.value,
                vwap_quality=session_vwap_result.quality.value,
                bar_vwap=bar_vwap,
                data_feed_kind=data_feed_kind,
                feed_scope=feed_scope,
                delivery_mode=delivery_mode,
                delay_status=delay_status,
                freshness_status=freshness_status,
            )
            if core_quotes[symbol].above_vwap is None:
                missing_fields.append(f"{symbol.lower()}_above_vwap")

        vix_quote = quotes.get("VIX")
        vix = None
        if vix_quote is not None:
            vix = self._to_snapshot_symbol(
                vix_quote,
                [],
                None,
                price_source="REST_QUOTE",
                bar_source="REST_BAR",
                vwap_method=VWAPMethod.UNAVAILABLE.value,
                vwap_quality=VWAPQuality.INVALID.value,
                bar_vwap=None,
                data_feed_kind=vix_quote.data_feed,
                feed_scope=None,
                delivery_mode=DeliveryMode.REST.value,
                delay_status=DelayStatus.DELAYED.value if vix_quote.is_delayed else DelayStatus.REALTIME.value,
                freshness_status="STALE" if self.data._quote_is_fresh(vix_quote, session).stale else "FRESH",
            )
            quote_timestamps["VIX"] = vix_quote.quote_timestamp.isoformat()
            quote_ages["VIX"] = max(0, int((now - vix_quote.quote_timestamp).total_seconds()))
        else:
            missing_fields.append("vix")

        if breadth is None:
            missing_fields.append("market_breadth")
        if sector_performance is None:
            missing_fields.append("sector_performance")
            sector_performance = []

        oldest_timestamp = None
        newest_timestamp = None
        timestamps: list[datetime] = [quote.quote_timestamp for quote in quotes.values()]
        if breadth and breadth.quote_timestamp:
            timestamps.append(breadth.quote_timestamp)
        if vix_quote is not None:
            timestamps.append(vix_quote.quote_timestamp)
        for symbol, session_bars in bars_by_symbol.items():
            if session_bars:
                timestamps.extend([bar.timestamp for bar in session_bars])
        if timestamps:
            oldest_timestamp = min(timestamps)
            newest_timestamp = max(timestamps)

        data_age_seconds = None
        if oldest_timestamp is not None:
            data_age_seconds = max(0, int((now - oldest_timestamp).total_seconds()))

        market_session = session
        snapshot_quality = self._data_quality_from_flags(stale_symbols, invalid_symbols, missing_fields, core_ready=len(core_quotes) == 4)
        leading_index, lagging_index = self._index_order(core_quotes)
        spy = core_quotes.get("SPY")
        qqq = core_quotes.get("QQQ")
        dia = core_quotes.get("DIA")
        iwm = core_quotes.get("IWM")

        spy_change = spy.change_percent if spy and spy.change_percent is not None else Decimal("0")
        qqq_change = qqq.change_percent if qqq and qqq.change_percent is not None else Decimal("0")
        dia_change = dia.change_percent if dia and dia.change_percent is not None else Decimal("0")
        iwm_change = iwm.change_percent if iwm and iwm.change_percent is not None else Decimal("0")

        snapshot = NormalizedMarketSnapshot(
            snapshot_time=now,
            market_session=market_session,
            spy=spy,
            qqq=qqq,
            dia=dia,
            iwm=iwm,
            vix=vix,
            market_breadth=breadth,
            sector_performance=sector_performance,
            provider=self.provider.provider_name,
            data_feed=self.provider.data_feed,
            oldest_data_timestamp=oldest_timestamp,
            newest_data_timestamp=newest_timestamp,
            data_age_seconds=data_age_seconds,
            data_quality_status=snapshot_quality,
            missing_fields=sorted(set(missing_fields)),
            leading_index=leading_index,
            lagging_index=lagging_index,
            tech_outperforming=(qqq_change >= spy_change),
            small_caps_participating=(iwm_change >= spy_change - Decimal("0.10")),
            volatility_increasing=(vix.change_percent or Decimal("0")) > Decimal("0.50") if vix is not None and vix.change_percent is not None else False,
            broad_market=breadth.breadth_score >= Decimal("0") if breadth and breadth.breadth_score is not None else bool((breadth.advancers or 0) >= (breadth.decliners or 0)) if breadth else False,
            volume_confirming=any((quote.volume or 0) > 0 for quote in core_quotes.values()),
            market_green=spy_change > Decimal("0"),
            market_red=spy_change < Decimal("0"),
            character_changed=self._compute_character_changed(core_quotes, previous_snapshot),
            raw_data={
                "provider": self.provider.provider_name,
                "data_feed": self.provider.data_feed,
                "market_session": market_session.value,
                "data_quality_status": snapshot_quality.value,
                "missing_fields": sorted(set(missing_fields)),
                "sector_performance": [sector.model_dump(mode="json") for sector in sector_performance],
                "indices": {
                    "SPY": self._quote_raw(spy),
                    "QQQ": self._quote_raw(qqq),
                    "DIA": self._quote_raw(dia),
                    "IWM": self._quote_raw(iwm),
                    "VIX": self._quote_raw(vix),
                },
                "sources": {
                    symbol: {
                        "price_source": quote.price_source,
                        "bar_source": quote.bar_source,
                        "vwap_method": quote.vwap_method,
                        "vwap_quality": quote.vwap_quality,
                        "data_feed_kind": quote.data_feed_kind,
                        "feed_scope": quote.feed_scope,
                        "delivery_mode": quote.delivery_mode,
                        "delay_status": quote.delay_status,
                        "freshness_status": quote.freshness_status,
                    }
                    for symbol, quote in core_quotes.items()
                },
                "quote_timestamps": quote_timestamps,
                "quote_ages_seconds": quote_ages,
                "latest_bar_timestamps": latest_bar_timestamps,
                "stale_symbols": stale_symbols,
                "invalid_symbols": invalid_symbols,
                "bars_count": {symbol: len(bars_by_symbol.get(symbol, [])) for symbol in self.core_symbols},
                "telemetry": getattr(self.data, "last_telemetry", {}),
            },
        )

        if self.repository is not None:
            if snapshot.data_quality_status == DataQualityStatus.INVALID:
                await self.repository.add_system_event(
                    event_type="market_snapshot_invalid",
                    severity="ERROR",
                    component="market_snapshot_service",
                    message="Refusing to persist invalid market snapshot",
                    metadata={"missing_fields": snapshot.missing_fields, "provider": snapshot.provider},
                )
                return snapshot
            if snapshot.data_quality_status == DataQualityStatus.STALE:
                await self.repository.add_system_event(
                    event_type="market_snapshot_stale",
                    severity="WARNING",
                    component="market_snapshot_service",
                    message="Stale market snapshot detected",
                    metadata={"missing_fields": snapshot.missing_fields, "provider": snapshot.provider},
                )
                if not self.store_stale_diagnostics:
                    return snapshot
            stored = await self.repository.save_market_snapshot(snapshot)
            snapshot.raw_data = {**snapshot.raw_data, "stored_snapshot_id": stored.id, "stored": True}
        return snapshot


class MarketSnapshotEngine:
    def __init__(self, provider: MarketDataProvider, bar_provider: MarketDataProvider | None = None, cache: MarketDataCache | None = None, session_calculator: MarketSessionCalculator | None = None, repository: Any | None = None, clock=None) -> None:
        self.service = MarketSnapshotService(provider, bar_provider=bar_provider, cache=cache, session_calculator=session_calculator, repository=repository, clock=clock)

    def build_snapshot(self, previous_snapshot: NormalizedMarketSnapshot | None = None) -> NormalizedMarketSnapshot:
        return asyncio.run(self.service.create_snapshot(previous_snapshot=previous_snapshot))
