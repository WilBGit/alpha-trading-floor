from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from alpha_market_engine.market.data_provider import MarketBreadth, MarketQuote, StaticMarketDataProvider
from alpha_market_engine.market.models import IntradayBar
from alpha_market_engine.market.snapshot import MarketSnapshotService
from alpha_market_engine.market.streaming.alpaca_stream import bounded_exponential_backoff
from alpha_market_engine.market.streaming.state import (
    DelayStatus,
    DeliveryMode,
    FeedScope,
    FreshnessStatus,
    LiveMarketStateStore,
    MarketDataFeedKind,
    MarketDataSourceMetadata,
    StreamHealthStatus,
    VWAPMethod,
    VWAPQuality,
    assess_session_bar_coverage,
    calculate_session_vwap,
    classify_market_data_source,
)

NY = datetime.now(UTC).astimezone().tzinfo
NOW = datetime(2026, 7, 13, 14, 41, tzinfo=UTC)


def make_bar(symbol: str, timestamp: datetime, open_: str, high: str, low: str, close: str, volume: int, *, vwap: str | None = None) -> IntradayBar:
    return IntradayBar(
        symbol=symbol,
        timestamp=timestamp,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
        vwap=Decimal(vwap) if vwap is not None else None,
        provider="static",
        data_feed="synthetic",
    )


def make_quote(symbol: str, price: str, *, timestamp: datetime = NOW, previous_close: str = "100") -> MarketQuote:
    return MarketQuote(
        symbol=symbol,
        price=Decimal(price),
        previous_close=Decimal(previous_close),
        quote_timestamp=timestamp,
        provider="static",
        data_feed="synthetic",
    )


def test_feed_metadata_classification_separates_stale_from_delay():
    metadata = classify_market_data_source(
        requested_feed="iex",
        delivery_mode=DeliveryMode.REST,
        now=NOW,
        event_timestamp=NOW - timedelta(hours=2),
    )

    assert metadata.data_feed == MarketDataFeedKind.IEX
    assert metadata.feed_scope == FeedScope.SINGLE_VENUE
    assert metadata.delivery_mode == DeliveryMode.REST
    assert metadata.delay_status == DelayStatus.REALTIME
    assert metadata.freshness_status == FreshnessStatus.STALE


def test_delayed_sip_classification_is_explicit():
    metadata = classify_market_data_source(
        requested_feed="delayed_sip",
        delivery_mode=DeliveryMode.WEBSOCKET,
        now=NOW,
        event_timestamp=NOW,
    )

    assert metadata.data_feed == MarketDataFeedKind.DELAYED_SIP
    assert metadata.feed_scope == FeedScope.CONSOLIDATED
    assert metadata.delay_status == DelayStatus.DELAYED
    assert metadata.freshness_status == FreshnessStatus.FRESH


def test_live_state_store_updates_and_rejects_out_of_order_events():
    store = LiveMarketStateStore()
    t1 = NOW - timedelta(seconds=5)
    t0 = NOW - timedelta(seconds=30)

    store.update_trade("SPY", price=Decimal("500.10"), timestamp=t1, provider="alpaca", feed="iex")
    store.update_trade("SPY", price=Decimal("499.00"), timestamp=t0, provider="alpaca", feed="iex")
    store.update_quote("SPY", bid=Decimal("500.00"), ask=Decimal("500.20"), timestamp=t1, provider="alpaca", feed="iex")
    store.update_bar("SPY", bar=make_bar("SPY", t1, "500", "501", "499", "500.1", 100, vwap="500.0"), provider="alpaca", feed="iex")

    state = store.get_symbol_state("SPY")
    assert state.latest_trade_price == Decimal("500.10")
    assert state.latest_trade_timestamp == t1
    assert state.latest_bid == Decimal("500.00")
    assert state.latest_ask == Decimal("500.20")
    assert state.latest_bar is not None
    assert state.trade_event_count == 1
    assert state.rejected_event_count == 1


def test_stream_health_transitions_from_connected_to_stale_and_disconnected():
    store = LiveMarketStateStore()
    state_time = NOW - timedelta(seconds=45)
    store.update_trade("SPY", price=Decimal("500.10"), timestamp=state_time, provider="alpaca", feed="iex")

    healthy = store.get_health(now=NOW, connected=True, authenticated=True, subscribed_symbols={"SPY"}, stale_after_seconds=60, disconnected_after_seconds=120)
    stale = store.get_health(now=NOW, connected=True, authenticated=True, subscribed_symbols={"SPY"}, stale_after_seconds=30, disconnected_after_seconds=120)
    disconnected = store.get_health(now=NOW, connected=False, authenticated=False, subscribed_symbols=set(), stale_after_seconds=30, disconnected_after_seconds=120)

    assert healthy.status == StreamHealthStatus.HEALTHY
    assert stale.status == StreamHealthStatus.STALE
    assert disconnected.status == StreamHealthStatus.DISCONNECTED


def test_session_vwap_uses_provider_bar_weighted_formula_when_available():
    bars = [
        make_bar("SPY", NOW - timedelta(minutes=2), "100", "101", "99", "100.5", 100, vwap="100.0"),
        make_bar("SPY", NOW - timedelta(minutes=1), "101", "102", "100", "101.5", 300, vwap="101.0"),
    ]

    result = calculate_session_vwap(bars)

    assert result.vwap is not None
    assert result.method == VWAPMethod.PROVIDER_BAR_WEIGHTED
    assert result.quality == VWAPQuality.GOOD
    assert result.vwap == Decimal("100.75")


def test_session_vwap_falls_back_to_typical_price_when_provider_vwap_missing():
    bars = [
        make_bar("SPY", NOW - timedelta(minutes=2), "100", "101", "99", "100.5", 100),
        make_bar("SPY", NOW - timedelta(minutes=1), "101", "102", "100", "101.5", 300),
    ]

    result = calculate_session_vwap(bars)

    assert result.vwap is not None
    assert result.method == VWAPMethod.TYPICAL_PRICE_APPROXIMATION
    assert result.quality == VWAPQuality.GOOD


def test_vwap_quality_thresholds_cover_good_degraded_and_invalid():
    session_open = datetime(2026, 7, 13, 13, 30, tzinfo=UTC)
    session_close = datetime(2026, 7, 13, 13, 40, tzinfo=UTC)
    bars_good = [make_bar("SPY", session_open + timedelta(minutes=i), "100", "101", "99", "100", 100) for i in range(10)]
    bars_degraded = bars_good[:-1]
    bars_invalid = []

    assert assess_session_bar_coverage(bars_good, session_open=session_open, session_close=session_close).quality == VWAPQuality.GOOD
    assert assess_session_bar_coverage(bars_degraded, session_open=session_open, session_close=session_close).quality == VWAPQuality.DEGRADED
    assert assess_session_bar_coverage(bars_invalid, session_open=session_open, session_close=session_close).quality == VWAPQuality.INVALID


def test_bounded_exponential_backoff_stays_within_limits():
    assert bounded_exponential_backoff(0, jitter_ratio=0.0) == 1
    assert bounded_exponential_backoff(1, jitter_ratio=0.0) == 2
    assert bounded_exponential_backoff(2, jitter_ratio=0.0) == 4
    assert bounded_exponential_backoff(10, jitter_ratio=0.0) == 30


@pytest.mark.asyncio
async def test_snapshot_prefers_live_stream_state_then_rest_fallback():
    provider = StaticMarketDataProvider(
        quotes={
            "SPY": make_quote("SPY", "501.00"),
            "QQQ": make_quote("QQQ", "401.00"),
            "DIA": make_quote("DIA", "301.00"),
            "IWM": make_quote("IWM", "201.00"),
            "VIX": make_quote("VIX", "16.00"),
        },
        breadth=MarketBreadth(advancers=1, decliners=1, breadth_score=Decimal("0.0"), quote_timestamp=NOW),
        bars={
            "SPY": [make_bar("SPY", NOW - timedelta(minutes=1), "500", "501", "499", "500", 100)],
            "QQQ": [make_bar("QQQ", NOW - timedelta(minutes=1), "400", "401", "399", "400", 100)],
            "DIA": [make_bar("DIA", NOW - timedelta(minutes=1), "300", "301", "299", "300", 100)],
            "IWM": [make_bar("IWM", NOW - timedelta(minutes=1), "200", "201", "199", "200", 100)],
        },
        sector_performance=[],
    )
    store = LiveMarketStateStore()
    store.update_trade("SPY", price=Decimal("505.00"), timestamp=NOW - timedelta(seconds=2), provider="alpaca", feed="iex")
    store.update_bar("SPY", bar=make_bar("SPY", NOW - timedelta(minutes=1), "504", "506", "503", "505", 120, vwap="505.0"), provider="alpaca", feed="iex")
    snapshot = await MarketSnapshotService(provider, live_state_store=store, clock=lambda: NOW).create_snapshot()

    assert snapshot.spy is not None
    assert snapshot.spy.price == Decimal("505.00")
    assert snapshot.spy.price_source == "WEBSOCKET_TRADE"
    assert snapshot.spy.bar_source == "WEBSOCKET_BAR"
    assert snapshot.raw_data["sources"]["SPY"]["price_source"] == "WEBSOCKET_TRADE"


@pytest.mark.asyncio
async def test_snapshot_rest_fallback_when_live_state_is_stale():
    provider = StaticMarketDataProvider(
        quotes={
            "SPY": make_quote("SPY", "501.00"),
            "QQQ": make_quote("QQQ", "401.00"),
            "DIA": make_quote("DIA", "301.00"),
            "IWM": make_quote("IWM", "201.00"),
            "VIX": make_quote("VIX", "16.00"),
        },
        breadth=MarketBreadth(advancers=1, decliners=1, breadth_score=Decimal("0.0"), quote_timestamp=NOW),
        bars={
            "SPY": [make_bar("SPY", NOW - timedelta(minutes=1), "500", "501", "499", "500", 100)],
            "QQQ": [make_bar("QQQ", NOW - timedelta(minutes=1), "400", "401", "399", "400", 100)],
            "DIA": [make_bar("DIA", NOW - timedelta(minutes=1), "300", "301", "299", "300", 100)],
            "IWM": [make_bar("IWM", NOW - timedelta(minutes=1), "200", "201", "199", "200", 100)],
        },
        sector_performance=[],
    )
    store = LiveMarketStateStore()
    store.update_trade("SPY", price=Decimal("505.00"), timestamp=NOW - timedelta(minutes=10), provider="alpaca", feed="iex")
    snapshot = await MarketSnapshotService(provider, live_state_store=store, clock=lambda: NOW).create_snapshot()

    assert snapshot.spy is not None
    assert snapshot.spy.price == Decimal("501.00")
    assert snapshot.spy.price_source == "REST_QUOTE"
