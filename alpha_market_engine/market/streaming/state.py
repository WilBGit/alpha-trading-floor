from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from threading import RLock
from typing import Any

from ..models import IntradayBar


class MarketDataFeedKind(str, Enum):
    IEX = "IEX"
    SIP = "SIP"
    DELAYED_SIP = "DELAYED_SIP"
    OTHER = "OTHER"


class FeedScope(str, Enum):
    SINGLE_VENUE = "SINGLE_VENUE"
    CONSOLIDATED = "CONSOLIDATED"
    UNKNOWN = "UNKNOWN"


class DeliveryMode(str, Enum):
    REST = "REST"
    WEBSOCKET = "WEBSOCKET"


class FreshnessStatus(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class DelayStatus(str, Enum):
    REALTIME = "REALTIME"
    DELAYED = "DELAYED"
    UNKNOWN = "UNKNOWN"


class ConnectionStatus(str, Enum):
    CONNECTED = "CONNECTED"
    AUTHENTICATED = "AUTHENTICATED"
    SUBSCRIBED = "SUBSCRIBED"
    DISCONNECTED = "DISCONNECTED"


class StreamHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"


class VWAPMethod(str, Enum):
    PROVIDER_BAR_WEIGHTED = "PROVIDER_BAR_WEIGHTED"
    TYPICAL_PRICE_APPROXIMATION = "TYPICAL_PRICE_APPROXIMATION"
    UNAVAILABLE = "UNAVAILABLE"


class VWAPQuality(str, Enum):
    GOOD = "GOOD"
    DEGRADED = "DEGRADED"
    INVALID = "INVALID"


@dataclass(slots=True)
class MarketDataSourceMetadata:
    data_feed: MarketDataFeedKind
    feed_scope: FeedScope
    delivery_mode: DeliveryMode
    freshness_status: FreshnessStatus
    delay_status: DelayStatus
    source: str
    event_timestamp: datetime | None = None
    age_seconds: int | None = None
    request_feed: str | None = None


@dataclass(slots=True)
class VWAPCoverageReport:
    quality: VWAPQuality
    expected_bar_count: int
    actual_bar_count: int
    missing_minute_count: int
    coverage_percentage: Decimal
    first_bar_timestamp: datetime | None
    last_bar_timestamp: datetime | None
    max_gap_minutes: int
    contiguous_gap_minutes: int


@dataclass(slots=True)
class SessionVWAPResult:
    vwap: Decimal | None
    method: VWAPMethod
    quality: VWAPQuality
    coverage: VWAPCoverageReport
    bar_vwap_available: bool


@dataclass(slots=True)
class ChannelState:
    price: Decimal | None = None
    timestamp: datetime | None = None
    source: str | None = None
    feed: MarketDataFeedKind = MarketDataFeedKind.OTHER
    feed_scope: FeedScope = FeedScope.UNKNOWN
    delivery_mode: DeliveryMode = DeliveryMode.WEBSOCKET
    delay_status: DelayStatus = DelayStatus.UNKNOWN


@dataclass(slots=True)
class LiveSymbolState:
    symbol: str
    provider: str | None = None
    feed: str | None = None
    feed_kind: MarketDataFeedKind = MarketDataFeedKind.OTHER
    feed_scope: FeedScope = FeedScope.UNKNOWN
    delivery_mode: DeliveryMode = DeliveryMode.WEBSOCKET
    delay_status: DelayStatus = DelayStatus.UNKNOWN
    connection_status: ConnectionStatus = ConnectionStatus.DISCONNECTED
    latest_trade_price: Decimal | None = None
    latest_trade_timestamp: datetime | None = None
    latest_trade_source: str | None = None
    latest_bid: Decimal | None = None
    latest_ask: Decimal | None = None
    latest_quote_timestamp: datetime | None = None
    latest_quote_source: str | None = None
    latest_bar: IntradayBar | None = None
    latest_bar_timestamp: datetime | None = None
    latest_bar_source: str | None = None
    last_event_received_at: datetime | None = None
    last_error: str | None = None
    trade_event_count: int = 0
    quote_event_count: int = 0
    bar_event_count: int = 0
    rejected_event_count: int = 0

    def freshness(self, now: datetime | None = None, stale_after_seconds: int = 30) -> FreshnessStatus:
        now = now or datetime.now(UTC)
        timestamps = [ts for ts in (self.latest_trade_timestamp, self.latest_quote_timestamp, self.latest_bar_timestamp, self.last_event_received_at) if ts is not None]
        if not timestamps:
            return FreshnessStatus.UNKNOWN
        age_seconds = max(0, int((now - max(timestamps)).total_seconds()))
        return FreshnessStatus.FRESH if age_seconds <= stale_after_seconds else FreshnessStatus.STALE


@dataclass(slots=True)
class StreamHealth:
    status: StreamHealthStatus
    connected: bool
    authenticated: bool
    subscribed_symbols: tuple[str, ...]
    last_message_time: datetime | None
    last_trade_time: dict[str, datetime | None]
    last_quote_time: dict[str, datetime | None]
    last_bar_time: dict[str, datetime | None]
    reconnect_count: int
    last_error: str | None = None
    message: str | None = None


@dataclass(slots=True)
class _SymbolSnapshot:
    trade: ChannelState = field(default_factory=ChannelState)
    quote: ChannelState = field(default_factory=ChannelState)
    bar: ChannelState = field(default_factory=ChannelState)
    state: LiveSymbolState = field(default_factory=lambda: LiveSymbolState(symbol=""))


def _to_feed_kind(requested_feed: str | None) -> MarketDataFeedKind:
    normalized = (requested_feed or "").strip().lower()
    if normalized == "iex":
        return MarketDataFeedKind.IEX
    if normalized == "sip":
        return MarketDataFeedKind.SIP
    if normalized == "delayed_sip":
        return MarketDataFeedKind.DELAYED_SIP
    return MarketDataFeedKind.OTHER


def classify_market_data_source(
    requested_feed: str | None,
    *,
    delivery_mode: DeliveryMode,
    now: datetime | None = None,
    event_timestamp: datetime | None = None,
    explicit_delay_status: DelayStatus | None = None,
    source: str | None = None,
    freshness_threshold_seconds: int = 60,
) -> MarketDataSourceMetadata:
    now = now or datetime.now(UTC)
    feed_kind = _to_feed_kind(requested_feed)
    if feed_kind == MarketDataFeedKind.IEX:
        feed_scope = FeedScope.SINGLE_VENUE
    elif feed_kind in {MarketDataFeedKind.SIP, MarketDataFeedKind.DELAYED_SIP}:
        feed_scope = FeedScope.CONSOLIDATED
    else:
        feed_scope = FeedScope.UNKNOWN

    if explicit_delay_status is not None:
        delay_status = explicit_delay_status
    elif feed_kind == MarketDataFeedKind.IEX:
        delay_status = DelayStatus.REALTIME
    elif feed_kind == MarketDataFeedKind.DELAYED_SIP:
        delay_status = DelayStatus.DELAYED
    else:
        delay_status = DelayStatus.UNKNOWN

    if event_timestamp is None:
        freshness_status = FreshnessStatus.UNKNOWN
        age_seconds = None
    else:
        age_seconds = max(0, int((now - event_timestamp).total_seconds()))
        freshness_status = FreshnessStatus.FRESH if age_seconds <= freshness_threshold_seconds else FreshnessStatus.STALE

    return MarketDataSourceMetadata(
        data_feed=feed_kind,
        feed_scope=feed_scope,
        delivery_mode=delivery_mode,
        freshness_status=freshness_status,
        delay_status=delay_status,
        source=source or requested_feed or "unknown",
        event_timestamp=event_timestamp,
        age_seconds=age_seconds,
        request_feed=requested_feed,
    )


def assess_session_bar_coverage(
    bars: list[IntradayBar],
    *,
    session_open: datetime | None = None,
    session_close: datetime | None = None,
    interval_minutes: int = 1,
) -> VWAPCoverageReport:
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")

    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    if not ordered:
        return VWAPCoverageReport(
            quality=VWAPQuality.INVALID,
            expected_bar_count=0,
            actual_bar_count=0,
            missing_minute_count=0,
            coverage_percentage=Decimal("0"),
            first_bar_timestamp=None,
            last_bar_timestamp=None,
            max_gap_minutes=0,
            contiguous_gap_minutes=0,
        )

    first = ordered[0].timestamp
    last = ordered[-1].timestamp
    if session_open is None:
        session_open = first
    if session_close is None:
        session_close = last + timedelta(minutes=interval_minutes)

    expected_timestamps: list[datetime] = []
    cursor = session_open
    while cursor < session_close:
        expected_timestamps.append(cursor)
        cursor += timedelta(minutes=interval_minutes)

    observed = {bar.timestamp.replace(second=0, microsecond=0) for bar in ordered}
    expected = {ts.replace(second=0, microsecond=0) for ts in expected_timestamps}
    actual_count = len(observed & expected)
    expected_count = len(expected)
    missing_count = max(0, expected_count - actual_count)
    coverage = Decimal("0") if expected_count == 0 else (Decimal(actual_count) / Decimal(expected_count))

    max_gap = 0
    contiguous_gap = 0
    prev = None
    for ts in sorted(observed & expected):
        if prev is not None:
            gap = int((ts - prev).total_seconds() // (60 * interval_minutes)) - 1
            if gap > max_gap:
                max_gap = gap
        prev = ts
    if expected_timestamps:
        contiguous_gap = max_gap

    if coverage >= Decimal("0.95") and max_gap <= 3:
        quality = VWAPQuality.GOOD
    elif coverage >= Decimal("0.001"):
        quality = VWAPQuality.DEGRADED
    else:
        quality = VWAPQuality.INVALID

    return VWAPCoverageReport(
        quality=quality,
        expected_bar_count=expected_count,
        actual_bar_count=actual_count,
        missing_minute_count=missing_count,
        coverage_percentage=coverage * Decimal("100"),
        first_bar_timestamp=first,
        last_bar_timestamp=last,
        max_gap_minutes=max_gap,
        contiguous_gap_minutes=contiguous_gap,
    )


def calculate_session_vwap(
    bars: list[IntradayBar],
    *,
    session_open: datetime | None = None,
    session_close: datetime | None = None,
) -> SessionVWAPResult:
    coverage = assess_session_bar_coverage(bars, session_open=session_open, session_close=session_close)
    if coverage.quality == VWAPQuality.INVALID:
        return SessionVWAPResult(vwap=None, method=VWAPMethod.UNAVAILABLE, quality=VWAPQuality.INVALID, coverage=coverage, bar_vwap_available=any(bar.vwap is not None for bar in bars))

    usable_bars = [bar for bar in sorted(bars, key=lambda item: item.timestamp) if bar.volume > 0]
    if not usable_bars:
        return SessionVWAPResult(vwap=None, method=VWAPMethod.UNAVAILABLE, quality=VWAPQuality.INVALID, coverage=coverage, bar_vwap_available=False)

    provider_vwap_trustworthy = all(bar.vwap is not None for bar in usable_bars)
    numerator = Decimal("0")
    denominator = Decimal("0")
    if provider_vwap_trustworthy:
        for bar in usable_bars:
            numerator += Decimal(str(bar.vwap)) * Decimal(bar.volume)
            denominator += Decimal(bar.volume)
        method = VWAPMethod.PROVIDER_BAR_WEIGHTED
    else:
        for bar in usable_bars:
            typical_price = (Decimal(str(bar.high)) + Decimal(str(bar.low)) + Decimal(str(bar.close))) / Decimal("3")
            numerator += typical_price * Decimal(bar.volume)
            denominator += Decimal(bar.volume)
        method = VWAPMethod.TYPICAL_PRICE_APPROXIMATION

    if denominator <= 0:
        return SessionVWAPResult(vwap=None, method=VWAPMethod.UNAVAILABLE, quality=VWAPQuality.INVALID, coverage=coverage, bar_vwap_available=provider_vwap_trustworthy)

    quality = coverage.quality
    return SessionVWAPResult(vwap=numerator / denominator, method=method, quality=quality, coverage=coverage, bar_vwap_available=provider_vwap_trustworthy)


class LiveMarketStateStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._symbols: dict[str, LiveSymbolState] = {}
        self.connected = False
        self.authenticated = False
        self.subscribed_symbols: set[str] = set()
        self.last_message_time: datetime | None = None
        self.reconnect_count = 0
        self.last_error: str | None = None

    def _symbol(self, symbol: str) -> LiveSymbolState:
        with self._lock:
            state = self._symbols.get(symbol)
            if state is None:
                state = LiveSymbolState(symbol=symbol)
                self._symbols[symbol] = state
            return state

    def set_connection(self, *, connected: bool | None = None, authenticated: bool | None = None, subscribed_symbols: set[str] | None = None, last_message_time: datetime | None = None, reconnect_count: int | None = None, last_error: str | None = None) -> None:
        with self._lock:
            if connected is not None:
                self.connected = connected
            if authenticated is not None:
                self.authenticated = authenticated
            if subscribed_symbols is not None:
                self.subscribed_symbols = set(subscribed_symbols)
            if last_message_time is not None:
                self.last_message_time = last_message_time
            if reconnect_count is not None:
                self.reconnect_count = reconnect_count
            if last_error is not None:
                self.last_error = last_error

    def update_trade(
        self,
        symbol: str,
        *,
        price: Decimal,
        timestamp: datetime,
        provider: str,
        feed: str | None = None,
        feed_scope: FeedScope | None = None,
        delay_status: DelayStatus | None = None,
        delivery_mode: DeliveryMode = DeliveryMode.WEBSOCKET,
        source: str = "WEBSOCKET_TRADE",
    ) -> bool:
        with self._lock:
            state = self._symbol(symbol)
            if state.latest_trade_timestamp is not None and timestamp < state.latest_trade_timestamp:
                state.rejected_event_count += 1
                return False
            state.provider = provider
            state.feed = feed or state.feed
            state.feed_kind = _to_feed_kind(feed)
            state.feed_scope = feed_scope or state.feed_scope
            state.delay_status = delay_status or state.delay_status
            state.delivery_mode = delivery_mode
            state.latest_trade_price = Decimal(str(price))
            state.latest_trade_timestamp = timestamp
            state.latest_trade_source = source
            state.last_event_received_at = timestamp
            state.trade_event_count += 1
            state.connection_status = ConnectionStatus.CONNECTED
            self.last_message_time = timestamp
            return True

    def update_quote(
        self,
        symbol: str,
        *,
        bid: Decimal | None,
        ask: Decimal | None,
        timestamp: datetime,
        provider: str,
        feed: str | None = None,
        feed_scope: FeedScope | None = None,
        delay_status: DelayStatus | None = None,
        delivery_mode: DeliveryMode = DeliveryMode.WEBSOCKET,
        source: str = "WEBSOCKET_QUOTE",
    ) -> bool:
        with self._lock:
            state = self._symbol(symbol)
            if state.latest_quote_timestamp is not None and timestamp < state.latest_quote_timestamp:
                state.rejected_event_count += 1
                return False
            state.provider = provider
            state.feed = feed or state.feed
            state.feed_kind = _to_feed_kind(feed)
            state.feed_scope = feed_scope or state.feed_scope
            state.delay_status = delay_status or state.delay_status
            state.delivery_mode = delivery_mode
            state.latest_bid = Decimal(str(bid)) if bid is not None else None
            state.latest_ask = Decimal(str(ask)) if ask is not None else None
            state.latest_quote_timestamp = timestamp
            state.latest_quote_source = source
            state.last_event_received_at = timestamp
            state.quote_event_count += 1
            state.connection_status = ConnectionStatus.CONNECTED
            self.last_message_time = timestamp
            return True

    def update_bar(
        self,
        symbol: str | None = None,
        *,
        bar: IntradayBar,
        provider: str,
        feed: str | None = None,
        feed_scope: FeedScope | None = None,
        delay_status: DelayStatus | None = None,
        delivery_mode: DeliveryMode = DeliveryMode.WEBSOCKET,
        source: str = "WEBSOCKET_BAR",
    ) -> bool:
        symbol = symbol or bar.symbol
        with self._lock:
            state = self._symbol(symbol)
            if state.latest_bar_timestamp is not None and bar.timestamp < state.latest_bar_timestamp:
                state.rejected_event_count += 1
                return False
            state.provider = provider
            state.feed = feed or state.feed
            state.feed_kind = _to_feed_kind(feed)
            state.feed_scope = feed_scope or state.feed_scope
            state.delay_status = delay_status or state.delay_status
            state.delivery_mode = delivery_mode
            state.latest_bar = bar
            state.latest_bar_timestamp = bar.timestamp
            state.latest_bar_source = source
            state.last_event_received_at = bar.timestamp
            state.bar_event_count += 1
            state.connection_status = ConnectionStatus.CONNECTED
            self.last_message_time = bar.timestamp
            return True

    def get_symbol_state(self, symbol: str) -> LiveSymbolState:
        with self._lock:
            state = self._symbols.get(symbol)
            if state is None:
                state = LiveSymbolState(symbol=symbol)
                self._symbols[symbol] = state
            return replace(state)

    def get_states(self) -> dict[str, LiveSymbolState]:
        with self._lock:
            return {symbol: replace(state) for symbol, state in self._symbols.items()}

    def get_freshness(self, symbol: str | None = None, *, now: datetime | None = None, stale_after_seconds: int = 30) -> FreshnessStatus | dict[str, FreshnessStatus]:
        now = now or datetime.now(UTC)
        if symbol is not None:
            return self.get_symbol_state(symbol).freshness(now=now, stale_after_seconds=stale_after_seconds)
        return {sym: state.freshness(now=now, stale_after_seconds=stale_after_seconds) for sym, state in self.get_states().items()}

    def get_health(
        self,
        *,
        now: datetime | None = None,
        connected: bool | None = None,
        authenticated: bool | None = None,
        subscribed_symbols: set[str] | None = None,
        stale_after_seconds: int = 30,
        disconnected_after_seconds: int = 120,
    ) -> StreamHealth:
        now = now or datetime.now(UTC)
        with self._lock:
            connected = self.connected if connected is None else connected
            authenticated = self.authenticated if authenticated is None else authenticated
            subscribed = tuple(sorted(self.subscribed_symbols if subscribed_symbols is None else subscribed_symbols))
            last_message_time = self.last_message_time
            status = StreamHealthStatus.HEALTHY
            if not connected:
                status = StreamHealthStatus.DISCONNECTED
            else:
                if last_message_time is not None:
                    message_age = max(0, int((now - last_message_time).total_seconds()))
                    if message_age > disconnected_after_seconds:
                        status = StreamHealthStatus.STALE
                    elif message_age > stale_after_seconds:
                        status = StreamHealthStatus.STALE
                else:
                    status = StreamHealthStatus.STALE
                if not authenticated or not subscribed:
                    status = StreamHealthStatus.DEGRADED if status == StreamHealthStatus.HEALTHY else status
                if self.last_error and status == StreamHealthStatus.HEALTHY:
                    status = StreamHealthStatus.DEGRADED

            return StreamHealth(
                status=status,
                connected=connected,
                authenticated=authenticated,
                subscribed_symbols=subscribed,
                last_message_time=last_message_time,
                last_trade_time={symbol: state.latest_trade_timestamp for symbol, state in self._symbols.items()},
                last_quote_time={symbol: state.latest_quote_timestamp for symbol, state in self._symbols.items()},
                last_bar_time={symbol: state.latest_bar_timestamp for symbol, state in self._symbols.items()},
                reconnect_count=self.reconnect_count,
                last_error=self.last_error,
                message="stream healthy" if status == StreamHealthStatus.HEALTHY else None,
            )


__all__ = [
    "MarketDataFeedKind",
    "FeedScope",
    "DeliveryMode",
    "FreshnessStatus",
    "DelayStatus",
    "ConnectionStatus",
    "StreamHealthStatus",
    "VWAPMethod",
    "VWAPQuality",
    "MarketDataSourceMetadata",
    "VWAPCoverageReport",
    "SessionVWAPResult",
    "LiveSymbolState",
    "StreamHealth",
    "LiveMarketStateStore",
    "classify_market_data_source",
    "assess_session_bar_coverage",
    "calculate_session_vwap",
]
