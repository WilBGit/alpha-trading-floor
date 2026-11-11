from __future__ import annotations

import asyncio
import inspect
import json
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Protocol, cast

from pydantic import ValidationError as PydanticValidationError

from ..cache import InMemoryMarketDataCache
from ..data_provider import (
    MarketDataAuthenticationError,
    MarketDataCache,
    MarketDataError,
    MarketDataProvider,
    MarketDataValidationError,
    MarketBreadth,
    MarketIndexQuote,
    MarketQuote,
    ProviderHealth,
    SectorPerformance,
    build_market_cache_key,
)
from ..models import IntradayBar
from ..session import MarketSession, MarketSessionCalculator
from ..streaming.state import VWAPCoverageReport, VWAPMethod, VWAPQuality, assess_session_bar_coverage, calculate_session_vwap

ROBINHOOD_ALLOWED_MARKET_TOOLS: frozenset[str] = frozenset(
    {
        "mcp__robinhood_trading__get_equity_quotes",
        "mcp__robinhood_trading__get_equity_historicals",
        "mcp__robinhood_trading__get_equity_fundamentals",
        "mcp__robinhood_trading__get_equity_technical_indicators",
        "mcp__robinhood_trading__get_index_quotes",
        "mcp__robinhood_trading__get_option_historicals",
        "mcp__robinhood_trading__get_earnings_calendar",
        "mcp__robinhood_trading__get_earnings_results",
        "mcp__robinhood_trading__get_equity_tradability",
        "mcp__robinhood_trading__get_indexes",
        "mcp__robinhood_trading__search",
    }
)

ROBINHOOD_REJECTED_TOOL_PREFIXES: tuple[str, ...] = (
    "mcp__robinhood_trading__add_",
    "mcp__robinhood_trading__buy_",
    "mcp__robinhood_trading__cancel_",
    "mcp__robinhood_trading__create_",
    "mcp__robinhood_trading__deposit_",
    "mcp__robinhood_trading__modify_",
    "mcp__robinhood_trading__move_",
    "mcp__robinhood_trading__place_",
    "mcp__robinhood_trading__sell_",
    "mcp__robinhood_trading__submit_",
    "mcp__robinhood_trading__trade_",
    "mcp__robinhood_trading__transfer_",
    "mcp__robinhood_trading__update_",
    "mcp__robinhood_trading__withdraw_",
)


class RobinhoodToolNotAllowedError(MarketDataValidationError):
    pass


class RobinhoodToolResponseError(MarketDataError):
    pass


class RobinhoodToolClient(Protocol):
    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any: ...


@dataclass(slots=True)
class OpeningRangeResult:
    window_minutes: int
    high: Decimal | None
    low: Decimal | None
    range_size: Decimal | None
    range_percent: Decimal | None
    bar_count: int
    coverage: VWAPCoverageReport
    session_open: datetime
    session_close: datetime


Clock = Callable[[], datetime]


class HermesChatRobinhoodToolClient:
    """Thin CLI bridge to the already-authorized Hermes Robinhood MCP runtime."""

    def __init__(self, *, hermes_command: str = "hermes", timeout_seconds: int = 600) -> None:
        self.hermes_command = hermes_command
        self.timeout_seconds = timeout_seconds

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        tool = tool_name.split("__", 2)[-1]
        prompt = (
            "Using the robinhood-trading MCP server, call "
            f"{tool} with arguments {json.dumps(arguments, separators=(',', ':'), sort_keys=True)}. "
            "Return only the raw JSON result from the tool. Do not include commentary."
        )
        proc = await asyncio.create_subprocess_exec(
            self.hermes_command,
            "chat",
            "-q",
            prompt,
            "-Q",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError as exc:  # pragma: no cover - depends on external CLI
            proc.kill()
            raise MarketDataError(f"robinhood tool call timed out: {tool_name}") from exc

        if proc.returncode != 0:
            raise MarketDataError(
                f"robinhood tool call failed for {tool_name}: {stderr.decode(errors='replace').strip() or stdout.decode(errors='replace').strip()}"
            )

        text = stdout.decode(errors="replace").strip()
        return _extract_json_value(text)


class RobinhoodMarketDataProvider(MarketDataProvider):
    provider_name = "robinhood"
    data_feed = "agentic"
    supports_batch_quotes = True

    def __init__(
        self,
        tool_client: RobinhoodToolClient | None = None,
        *,
        clock: Clock | None = None,
        hermes_command: str = "hermes",
        timeout_seconds: int = 600,
        session_calculator: MarketSessionCalculator | None = None,
        historical_cache: MarketDataCache | None = None,
        historical_max_concurrency: int = 2,
        historical_total_timeout_seconds: int = 900,
        historical_max_retries: int = 2,
    ) -> None:
        self._tool_client = tool_client or HermesChatRobinhoodToolClient(
            hermes_command=hermes_command,
            timeout_seconds=timeout_seconds,
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._session_calculator = session_calculator or MarketSessionCalculator()
        self._timeout_seconds = timeout_seconds
        self._historical_per_call_timeout_seconds = timeout_seconds
        self._historical_cache = historical_cache or InMemoryMarketDataCache()
        self._historical_max_concurrency = max(1, historical_max_concurrency)
        if historical_total_timeout_seconds <= 0:
            raise ValueError("historical_total_timeout_seconds must be positive")
        self._historical_total_timeout_seconds = float(historical_total_timeout_seconds)
        self._historical_max_retries = max(0, historical_max_retries)
        self._historical_semaphore = asyncio.Semaphore(self._historical_max_concurrency)
        self._historical_inflight: dict[str, asyncio.Task[list[dict[str, Any]]]] = {}
        self._historical_inflight_lock = asyncio.Lock()
        self.last_historical_fetch_telemetry: list[dict[str, Any]] = []

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    def _historical_cache_key(self, *, symbol: str, interval: str, session_label: Any, bounds: str) -> str:
        session_text = str(getattr(session_label, "date", lambda: session_label)()) if hasattr(session_label, "date") else str(session_label)
        return build_market_cache_key(self.provider_name, "historical", symbol=symbol, timeframe=f"{interval}:{session_text}:{bounds}", feed=self.data_feed)

    def _record_historical_event(self, event: dict[str, Any]) -> None:
        self.last_historical_fetch_telemetry.append(event)

    def _is_retryable_historical_error(self, exc: Exception) -> bool:
        if isinstance(exc, MarketDataValidationError):
            return False
        if isinstance(exc, MarketDataError):
            message = str(exc).lower()
            return "timed out" in message or "transport" in message or "failed" in message or "temporary" in message
        return False

    def _historical_backoff_seconds(self, attempt: int) -> float:
        return min(8.0, 0.5 * (2 ** attempt))

    def _historical_window_policy(self, interval: str) -> int:
        # Full-session fetches are the only shape the CLI needs for Phase 4.
        # Avoid fan-out: one logical request per symbol/session/interval.
        return 24 * 60

    def _is_allowed_tool(self, tool_name: str) -> bool:
        if tool_name in ROBINHOOD_ALLOWED_MARKET_TOOLS:
            return True
        if any(tool_name.startswith(prefix) for prefix in ROBINHOOD_REJECTED_TOOL_PREFIXES):
            return False
        return False

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if not self._is_allowed_tool(tool_name):
            raise RobinhoodToolNotAllowedError(f"tool not allowlisted: {tool_name}")
        result = self._tool_client.call_tool(tool_name, arguments)
        if inspect.isawaitable(result):
            return await result
        return result

    async def get_quote(self, symbol: str) -> MarketQuote:
        quotes = await self.get_quotes([symbol])
        quote = quotes.get(symbol)
        if quote is None:
            raise MarketDataValidationError(f"missing symbol: {symbol}")
        return quote

    async def get_quotes(self, symbols: list[str]) -> dict[str, MarketQuote]:
        normalized = _normalize_symbols(symbols)
        if not normalized:
            raise MarketDataValidationError("symbols must not be empty")

        payload = await self.call_tool(
            "mcp__robinhood_trading__get_equity_quotes",
            {"symbols": normalized},
        )
        request_received_at = self._now()
        return _normalize_quote_payload(payload, request_received_at=request_received_at, provider=self.provider_name, data_feed=self.data_feed or "agentic", requested_symbols=normalized)

    async def get_intraday_bars(self, symbol: str, timeframe: str, limit: int | None = None) -> list[IntradayBar]:
        symbol = symbol.upper().strip()
        if not symbol:
            raise MarketDataValidationError("symbol must not be empty")
        interval = _normalize_interval(timeframe)
        now = self._now()
        session = self._session_calculator.current_session(now)
        if session == MarketSession.REGULAR:
            session_label = self._session_calculator.calendar.date_to_session(now.astimezone(self._session_calculator.timezone).date(), direction="none")
        else:
            session_label = self._session_calculator.most_recent_completed_session(now)
        session_open = self._session_calculator.calendar.session_open(session_label).to_pydatetime().astimezone(UTC)
        session_close = self._session_calculator.calendar.session_close(session_label).to_pydatetime().astimezone(UTC)
        end_time = min(now, session_close)
        cache_key = self._historical_cache_key(symbol=symbol, interval=interval, session_label=session_label, bounds="regular")

        cached_rows = self._historical_cache.get(cache_key)
        if isinstance(cached_rows, list) and (not cached_rows or isinstance(cached_rows[0], dict)):
            self._record_historical_event(
                {
                    "symbol": symbol,
                    "session": str(session_label),
                    "interval": interval,
                    "window_id": 0,
                    "request_start": session_open.isoformat(),
                    "request_end": end_time.isoformat(),
                    "elapsed_ms": 0,
                    "success": True,
                    "timeout": False,
                    "retry_number": 0,
                    "bars_returned": len(cached_rows),
                    "cache_hit": True,
                    "cache_coalesced": False,
                }
            )
            bars = _normalize_bar_payload(cached_rows, provider=self.provider_name, data_feed=self.data_feed or "agentic", symbol=symbol)
            return bars[-limit:] if limit is not None else bars

        async with self._historical_inflight_lock:
            task = self._historical_inflight.get(cache_key)
            if task is None:
                task = asyncio.create_task(
                    asyncio.wait_for(
                        self._fetch_historical_bars(
                            symbol=symbol,
                            start_time=session_open,
                            end_time=end_time,
                            interval=interval,
                            bounds="regular",
                        ),
                        timeout=self._historical_total_timeout_seconds,
                    )
                )
                self._historical_inflight[cache_key] = task
            else:
                self._record_historical_event(
                    {
                        "symbol": symbol,
                        "session": str(session_label),
                        "interval": interval,
                        "window_id": 0,
                        "request_start": session_open.isoformat(),
                        "request_end": end_time.isoformat(),
                        "elapsed_ms": 0,
                        "success": True,
                        "timeout": False,
                        "retry_number": 0,
                        "bars_returned": None,
                        "cache_hit": True,
                        "cache_coalesced": True,
                    }
                )

        request_started = self._now()
        try:
            raw_rows = await task
        except asyncio.TimeoutError as exc:
            async with self._historical_inflight_lock:
                if self._historical_inflight.get(cache_key) is task:
                    self._historical_inflight.pop(cache_key, None)
            request_ended = self._now()
            self._record_historical_event(
                {
                    "symbol": symbol,
                    "session": str(session_label),
                    "interval": interval,
                    "window_id": 0,
                    "request_start": request_started.isoformat(),
                    "request_end": request_ended.isoformat(),
                    "elapsed_ms": int((request_ended - request_started).total_seconds() * 1000),
                    "success": False,
                    "timeout": True,
                    "retry_number": 0,
                    "bars_returned": 0,
                    "cache_hit": False,
                    "cache_coalesced": False,
                }
            )
            raise MarketDataError(f"robinhood historical fetch timed out for {symbol}") from exc
        finally:
            async with self._historical_inflight_lock:
                if self._historical_inflight.get(cache_key) is task:
                    self._historical_inflight.pop(cache_key, None)

        self._historical_cache.set(cache_key, raw_rows, 60)
        bars = _normalize_bar_payload(raw_rows, provider=self.provider_name, data_feed=self.data_feed or "agentic", symbol=symbol)
        return bars[-limit:] if limit is not None else bars

    async def get_market_indices(self) -> dict[str, MarketQuote]:
        quotes = await self.get_quotes(["SPY", "QQQ", "DIA", "IWM"])
        return quotes

    async def get_sector_performance(self) -> list[SectorPerformance] | None:
        return None

    async def get_market_breadth(self) -> MarketBreadth | None:
        return None

    async def health_check(self) -> ProviderHealth:
        checked_at = self._now()
        try:
            quote = await self.get_quote("SPY")
            return ProviderHealth(
                provider=self.provider_name,
                healthy=True,
                checked_at=checked_at,
                message="robinhood agentic market data available",
                data_feed=self.data_feed,
                metadata={
                    "sample_symbol": quote.symbol,
                    "sample_quote_timestamp": quote.quote_timestamp.isoformat(),
                    "tool_client": type(self._tool_client).__name__,
                    "allowed_tools": len(ROBINHOOD_ALLOWED_MARKET_TOOLS),
                },
            )
        except Exception as exc:  # pragma: no cover - depends on live tool availability
            return ProviderHealth(
                provider=self.provider_name,
                healthy=False,
                checked_at=checked_at,
                message=str(exc),
                data_feed=self.data_feed,
                metadata={"tool_client": type(self._tool_client).__name__},
            )

    async def _fetch_historical_bars(
        self,
        *,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        interval: str,
        bounds: str,
    ) -> Any:
        attempt = 0
        while True:
            request_start = self._now()
            try:
                async with self._historical_semaphore:
                    payload = await self.call_tool(
                        "mcp__robinhood_trading__get_equity_historicals",
                        {
                            "symbols": [symbol],
                            "start_time": start_time.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                            "end_time": end_time.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                            "interval": interval,
                            "bounds": bounds,
                            "adjustment_type": "split",
                        },
                    )
                request_end = self._now()
                payload = _unwrap_jsonish(payload)
                rows = _extract_historical_bar_rows(payload, symbol=symbol)
                self._record_historical_event(
                    {
                        "symbol": symbol,
                        "session": str(start_time.date()),
                        "interval": interval,
                        "window_id": 0,
                        "request_start": request_start.isoformat(),
                        "request_end": request_end.isoformat(),
                        "elapsed_ms": int((request_end - request_start).total_seconds() * 1000),
                        "success": True,
                        "timeout": False,
                        "retry_number": attempt,
                        "bars_returned": len(rows),
                        "cache_hit": False,
                        "cache_coalesced": False,
                    }
                )
                return rows
            except Exception as exc:
                request_end = self._now()
                timeout = "timed out" in str(exc).lower()
                self._record_historical_event(
                    {
                        "symbol": symbol,
                        "session": str(start_time.date()),
                        "interval": interval,
                        "window_id": 0,
                        "request_start": request_start.isoformat(),
                        "request_end": request_end.isoformat(),
                        "elapsed_ms": int((request_end - request_start).total_seconds() * 1000),
                        "success": False,
                        "timeout": timeout,
                        "retry_number": attempt,
                        "bars_returned": 0,
                        "cache_hit": False,
                        "cache_coalesced": False,
                    }
                )
                if not self._is_retryable_historical_error(exc) or attempt >= self._historical_max_retries:
                    raise
                await asyncio.sleep(self._historical_backoff_seconds(attempt))
                attempt += 1


def calculate_percentage_change(current_price: Decimal | str | float | int, previous_close: Decimal | str | float | int) -> Decimal:
    try:
        current = MarketQuote._to_decimal(current_price)
        previous = MarketQuote._to_decimal(previous_close)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MarketDataValidationError("percentage change inputs must be finite numeric values") from exc
    if previous <= 0:
        raise MarketDataValidationError("previous_close must be greater than zero")
    result = ((current - previous) / previous) * Decimal("100")
    if not result.is_finite():
        raise MarketDataValidationError("percentage change must be finite")
    return result


def calculate_opening_range(bars: list[IntradayBar], *, session_open: datetime, window_minutes: int) -> OpeningRangeResult:
    if window_minutes <= 0:
        raise ValueError("window_minutes must be positive")
    session_close = session_open + timedelta(minutes=window_minutes)
    coverage = assess_session_bar_coverage(bars, session_open=session_open, session_close=session_close)
    window_bars = [bar for bar in bars if session_open <= bar.timestamp < session_close]
    if not window_bars:
        return OpeningRangeResult(
            window_minutes=window_minutes,
            high=None,
            low=None,
            range_size=None,
            range_percent=None,
            bar_count=0,
            coverage=coverage,
            session_open=session_open,
            session_close=session_close,
        )
    high = max(Decimal(str(bar.high)) for bar in window_bars)
    low = min(Decimal(str(bar.low)) for bar in window_bars)
    range_size = high - low
    range_percent = None if low <= 0 else (range_size / low) * Decimal("100")
    return OpeningRangeResult(
        window_minutes=window_minutes,
        high=high,
        low=low,
        range_size=range_size,
        range_percent=range_percent,
        bar_count=len(window_bars),
        coverage=coverage,
        session_open=session_open,
        session_close=session_close,
    )


def _normalize_symbols(symbols: list[str]) -> list[str]:
    normalized = []
    seen: set[str] = set()
    for symbol in symbols:
        cleaned = symbol.upper().strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def _normalize_interval(timeframe: str) -> str:
    key = timeframe.strip().lower()
    mapping = {
        "1m": "minute",
        "1min": "minute",
        "1minute": "minute",
        "minute": "minute",
        "5m": "5minute",
        "5min": "5minute",
        "5minute": "5minute",
        "15m": "15minute",
        "15min": "15minute",
        "15minute": "15minute",
        "30m": "30minute",
        "30min": "30minute",
        "30minute": "30minute",
        "1h": "hour",
        "hour": "hour",
        "day": "day",
        "1d": "day",
    }
    if key not in mapping:
        raise MarketDataValidationError(f"unsupported timeframe: {timeframe}")
    return mapping[key]


def _interval_minutes(interval: str) -> int:
    mapping = {
        "minute": 1,
        "5minute": 5,
        "15minute": 15,
        "30minute": 30,
        "hour": 60,
        "day": 390,
    }
    return mapping.get(interval, 1)


def _historical_chunk_minutes(interval: str) -> int:
    if interval == "minute":
        # A 1-minute session fetch becomes much faster when split into two
        # larger windows, but the Robinhood bridge starts returning empty
        # results once a single window grows too wide. 225 minutes stays
        # below that cliff while keeping the session to two requests.
        return 225
    if interval == "5minute":
        return 6 * 60
    if interval == "15minute":
        return 24 * 60
    if interval == "30minute":
        return 48 * 60
    return 7 * 24 * 60


def _time_windows(start: datetime, end: datetime, chunk: timedelta) -> list[tuple[datetime, datetime]]:
    if end <= start:
        return []
    windows: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        window_end = min(end, cursor + chunk)
        windows.append((cursor, window_end))
        cursor = window_end
    return windows


def _normalize_quote_payload(
    payload: Any,
    *,
    request_received_at: datetime,
    provider: str,
    data_feed: str,
    requested_symbols: list[str] | None = None,
) -> dict[str, MarketQuote]:
    payload = _unwrap_jsonish(payload)
    if isinstance(payload, dict):
        error_text = payload.get("error") or payload.get("errors")
        if error_text:
            text = str(error_text).lower()
            if any(marker in text for marker in ("auth", "unauthor", "forbidden", "token", "session")):
                raise MarketDataAuthenticationError("robinhood quote response indicates authentication/session failure")
            raise RobinhoodToolResponseError("robinhood quote tool returned an error envelope")
    results = _extract_results(payload)
    quotes: dict[str, MarketQuote] = {}
    invalid_symbols: list[str] = []
    for item in results:
        quote_data = item.get("quote") if isinstance(item, dict) else None
        if not isinstance(quote_data, dict):
            quote_data = item if isinstance(item, dict) else {}
        close_data = cast(dict[str, Any], item.get("close")) if isinstance(item, dict) and isinstance(item.get("close"), dict) else {}
        symbol = str(quote_data.get("symbol") or item.get("symbol") or "").upper().strip()
        if not symbol or (requested_symbols is not None and symbol not in requested_symbols):
            continue

        numeric_parse_errors: dict[str, str] = {}
        missing_fields: list[str] = []
        _parse_optional_decimal(quote_data.get("bid_price"), "bid_price", numeric_parse_errors=numeric_parse_errors, record_missing=False)
        _parse_optional_decimal(quote_data.get("ask_price"), "ask_price", numeric_parse_errors=numeric_parse_errors, record_missing=False)
        quote_timestamp = _select_quote_timestamp(quote_data, request_received_at=request_received_at)
        try:
            price, price_source = _select_quote_price(quote_data, numeric_parse_errors=numeric_parse_errors)
        except MarketDataValidationError:
            invalid_symbols.append(symbol)
            continue
        previous_close, previous_close_source = _select_previous_close(
            quote_data,
            close_data,
            numeric_parse_errors=numeric_parse_errors,
        )
        if previous_close is not None:
            change_percent = calculate_percentage_change(price, previous_close)
            change = price - previous_close
            change_percent_source = "CALCULATED"
        else:
            change_percent = None
            change = None
            change_percent_source = "UNAVAILABLE"
            missing_fields.append("previous_close")

        _parse_optional_decimal(
            _first_present_value(quote_data, ("change_percent", "percent_change", "change_pct")),
            "change_percent",
            numeric_parse_errors=numeric_parse_errors,
            missing_fields=missing_fields,
        )
        optional_decimals = {
            "open_price": _select_optional_decimal(quote_data, ("open_price", "open"), "open_price", numeric_parse_errors),
            "day_high": _select_optional_decimal(quote_data, ("high_price", "day_high", "high"), "day_high", numeric_parse_errors),
            "day_low": _select_optional_decimal(quote_data, ("low_price", "day_low", "low"), "day_low", numeric_parse_errors),
            "relative_volume": _select_optional_decimal(quote_data, ("relative_volume",), "relative_volume", numeric_parse_errors),
            "vwap": _select_optional_decimal(quote_data, ("vwap",), "vwap", numeric_parse_errors),
        }
        volume = _select_optional_int(quote_data, ("volume", "trade_volume", "volume_24h"), "volume", numeric_parse_errors)
        trade_count = _select_optional_int(quote_data, ("trade_count", "trades_count"), "trade_count", numeric_parse_errors)
        metadata = {
            "source_tool": "mcp__robinhood_trading__get_equity_quotes",
            "current_price_source": price_source,
            "previous_close_source": previous_close_source,
            "change_percent_source": change_percent_source,
            "request_received_at": request_received_at.isoformat(),
            "quote_state": quote_data.get("state"),
            "has_traded": quote_data.get("has_traded"),
            "venue_last_trade_time": quote_data.get("venue_last_trade_time"),
            "venue_last_non_reg_trade_time": quote_data.get("venue_last_non_reg_trade_time"),
            "venue_bid_time": quote_data.get("venue_bid_time"),
            "venue_ask_time": quote_data.get("venue_ask_time"),
            "bid_price_available": _parse_optional_decimal(quote_data.get("bid_price"), "bid_price", record_missing=False) is not None,
            "ask_price_available": _parse_optional_decimal(quote_data.get("ask_price"), "ask_price", record_missing=False) is not None,
            "previous_close": previous_close,
            "close_date": close_data.get("date"),
            "source_fields": sorted(quote_data.keys()),
            "invalid_fields": sorted(numeric_parse_errors),
            "numeric_parse_errors": numeric_parse_errors,
            "missing_fields": sorted(set(missing_fields)),
        }
        try:
            quote = MarketQuote(
                symbol=symbol,
                price=price,
                previous_close=previous_close,
                change=change,
                change_percent=change_percent,
                open_price=optional_decimals["open_price"],
                day_high=optional_decimals["day_high"],
                day_low=optional_decimals["day_low"],
                relative_volume=optional_decimals["relative_volume"],
                volume=volume,
                quote_timestamp=quote_timestamp,
                provider=provider,
                data_feed=data_feed,
                is_delayed=bool(quote_data.get("is_delayed", False)),
                vwap=optional_decimals["vwap"],
                trade_count=trade_count,
                validation_metadata=metadata,
            )
        except PydanticValidationError as exc:
            raise MarketDataValidationError(str(exc)) from exc
        quotes[symbol] = quote
    if not quotes:
        if invalid_symbols:
            raise MarketDataValidationError(f"valid response without usable price: {','.join(sorted(set(invalid_symbols)))}")
        raise MarketDataValidationError("empty quote result from robinhood")
    return quotes


def _normalize_bar_payload(payload: Any, *, provider: str, data_feed: str, symbol: str | None = None) -> list[IntradayBar]:
    payload = _unwrap_jsonish(payload)
    if isinstance(payload, list) and symbol is not None:
        raw_rows = [row for row in payload if isinstance(row, dict)]
        timestamps: list[datetime] = []
        bars: list[IntradayBar] = []
        for row in raw_rows:
            timestamp = _parse_datetime(row.get("begins_at") or row.get("timestamp") or row.get("time"))
            timestamps.append(timestamp)
            bars.append(_build_intraday_bar(symbol, row, timestamp=timestamp, provider=provider, data_feed=data_feed))
        if timestamps:
            if timestamps != sorted(timestamps):
                raise MarketDataValidationError(f"out-of-order bars returned for {symbol}")
            if len(set(timestamps)) != len(timestamps):
                raise MarketDataValidationError(f"duplicate bar timestamps returned for {symbol}")
        if bars:
            return bars
    results = _extract_results(payload)
    bars: list[IntradayBar] = []
    for item in results:
        symbol = str(item.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        raw_rows = cast(list[dict[str, Any]], item.get("bars")) if isinstance(item.get("bars"), list) else []
        normalized_rows = []
        timestamps: list[datetime] = []
        for row in raw_rows:
            timestamp = _parse_datetime(row.get("begins_at") or row.get("timestamp") or row.get("time"))
            timestamps.append(timestamp)
            normalized_rows.append(_build_intraday_bar(symbol, row, timestamp=timestamp, provider=provider, data_feed=data_feed, interval=item.get("interval"), bounds=item.get("bounds")))
        if timestamps:
            if timestamps != sorted(timestamps):
                raise MarketDataValidationError(f"out-of-order bars returned for {symbol}")
            if len(set(timestamps)) != len(timestamps):
                raise MarketDataValidationError(f"duplicate bar timestamps returned for {symbol}")
        bars.extend(normalized_rows)
    if not bars:
        raise MarketDataValidationError("no bars returned by robinhood")
    return bars


def _build_intraday_bar(
    symbol: str,
    row: dict[str, Any],
    *,
    timestamp: datetime,
    provider: str,
    data_feed: str,
    interval: Any | None = None,
    bounds: Any | None = None,
) -> IntradayBar:
    return IntradayBar(
        symbol=symbol,
        timestamp=timestamp,
        open=_to_decimal_or_error(row.get("open_price") or row.get("open"), "open"),
        high=_to_decimal_or_error(row.get("high_price") or row.get("high"), "high"),
        low=_to_decimal_or_error(row.get("low_price") or row.get("low"), "low"),
        close=_to_decimal_or_error(row.get("close_price") or row.get("close"), "close"),
        volume=_to_int_or_error(row.get("volume") or row.get("trade_volume") or 0, "volume"),
        provider=provider,
        data_feed=data_feed,
        session=_parse_market_session(row.get("session")),
        vwap=_to_decimal_or_none(row.get("vwap")),
        trade_count=_to_int_or_none(row.get("trade_count") or row.get("trades_count")),
        validation_metadata={
            "source_tool": "mcp__robinhood_trading__get_equity_historicals",
            "source_fields": sorted(row.keys()),
            "raw_session": row.get("session"),
            "interval": interval,
            "bounds": bounds,
        },
    )


def _extract_results(payload: Any) -> list[dict[str, Any]]:
    payload = _unwrap_jsonish(payload)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("results"), list):
        return [item for item in payload["data"]["results"] if isinstance(item, dict)]
    if isinstance(payload.get("results"), list):
        return [item for item in payload["results"] if isinstance(item, dict)]
    if isinstance(payload.get("quotes"), list):
        return [item for item in payload["quotes"] if isinstance(item, dict)]
    if isinstance(payload.get("result"), str):
        return _extract_results(_unwrap_jsonish(payload["result"]))
    if isinstance(payload.get("quote"), dict):
        return [payload]
    return [payload] if payload else []


def _extract_historical_bar_rows(payload: Any, *, symbol: str) -> list[dict[str, Any]]:
    payload = _unwrap_jsonish(payload)
    if isinstance(payload, dict):
        results = payload.get("data", {}).get("results") if isinstance(payload.get("data"), dict) else None
        if isinstance(results, list):
            for item in results:
                if isinstance(item, dict) and str(item.get("symbol") or "").upper().strip() == symbol:
                    bars = item.get("bars")
                    if isinstance(bars, list):
                        return [row for row in bars if isinstance(row, dict)]
        if isinstance(payload.get("result"), str):
            return _extract_historical_bar_rows(_unwrap_jsonish(payload["result"]), symbol=symbol)
    return []


def _unwrap_jsonish(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and stripped[0] in "[{":
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return value
        return value
    if isinstance(value, dict):
        if isinstance(value.get("result"), str):
            inner = _unwrap_jsonish(value["result"])
            if inner is not value["result"]:
                return inner
    return value


def _extract_json_value(text: str) -> Any:
    def _brace_match(start: int) -> str | None:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            ch = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        return text[start : index + 1]
        return None

    markers = [text.rfind('{\n  "data"'), text.rfind('{"data"'), text.rfind('{\n{'), text.find('{')]
    for marker in markers:
        if marker == -1:
            continue
        candidate = _brace_match(marker)
        if candidate is None:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    candidates = [idx for idx, ch in enumerate(text) if ch in '[{']
    for idx in reversed(candidates):
        candidate = _brace_match(idx)
        if candidate is None:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise MarketDataError(f'unable to parse JSON from Hermes output: {text[-500:]}')


def _first_present_value(mapping: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for alias in aliases:
        if alias in mapping:
            return mapping[alias]
    return None


def _classify_numeric_failure(value: Any) -> str:
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, list):
        return "LIST"
    if isinstance(value, dict):
        return "DICT"
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return "WHITESPACE_STRING" if value else "EMPTY_STRING"
        if stripped.lower() in {"unknown", "n/a", "na", "null", "none", "–", "—"}:
            return "PLACEHOLDER_TEXT"
    try:
        decimal_value = Decimal(str(value).strip() if isinstance(value, str) else str(value))
    except (InvalidOperation, TypeError, ValueError):
        return "NON_NUMERIC"
    if decimal_value.is_nan():
        return "NAN"
    if not decimal_value.is_finite():
        return "INFINITY"
    return "INVALID"


def _parse_optional_decimal(
    value: Any,
    field_name: str,
    *,
    numeric_parse_errors: dict[str, str] | None = None,
    missing_fields: list[str] | None = None,
    record_missing: bool = True,
) -> Decimal | None:
    if value is None:
        if record_missing and missing_fields is not None:
            missing_fields.append(field_name)
        return None
    if isinstance(value, str) and not value.strip():
        reason = _classify_numeric_failure(value)
        if numeric_parse_errors is not None:
            numeric_parse_errors[field_name] = reason
        return None
    try:
        if isinstance(value, bool):
            raise ValueError("boolean is not numeric")
        if isinstance(value, Decimal):
            decimal_value = value
        elif isinstance(value, int):
            decimal_value = Decimal(value)
        elif isinstance(value, float):
            decimal_value = Decimal(str(value))
        elif isinstance(value, str):
            decimal_value = Decimal(value.strip())
        else:
            raise TypeError("unsupported numeric type")
        if not decimal_value.is_finite():
            raise ValueError("numeric value must be finite")
        return decimal_value
    except (InvalidOperation, TypeError, ValueError):
        if numeric_parse_errors is not None:
            numeric_parse_errors[field_name] = _classify_numeric_failure(value)
        return None


def _select_optional_decimal(
    mapping: dict[str, Any],
    aliases: tuple[str, ...],
    field_name: str,
    numeric_parse_errors: dict[str, str],
) -> Decimal | None:
    for alias in aliases:
        if alias not in mapping:
            continue
        parsed = _parse_optional_decimal(mapping[alias], field_name, numeric_parse_errors=numeric_parse_errors, record_missing=False)
        if parsed is not None:
            return parsed
    return None


def _select_optional_int(
    mapping: dict[str, Any],
    aliases: tuple[str, ...],
    field_name: str,
    numeric_parse_errors: dict[str, str],
) -> int | None:
    for alias in aliases:
        if alias not in mapping:
            continue
        parsed = _parse_optional_decimal(mapping[alias], field_name, numeric_parse_errors=numeric_parse_errors, record_missing=False)
        if parsed is None:
            continue
        if parsed != parsed.to_integral_value():
            numeric_parse_errors[field_name] = "NON_INTEGER"
            continue
        return int(parsed)
    return None


def _to_decimal_or_none(value: Any) -> Decimal | None:
    return _parse_optional_decimal(value, "numeric_value", record_missing=False)


def _to_decimal_or_error(value: Any, field_name: str) -> Decimal:
    decimal_value = _to_decimal_or_none(value)
    if decimal_value is None:
        raise MarketDataValidationError(f"missing or invalid {field_name}")
    return decimal_value


def _to_int_or_none(value: Any) -> int | None:
    errors: dict[str, str] = {}
    if value is None:
        return None
    parsed = _parse_optional_decimal(value, "integer_value", numeric_parse_errors=errors, record_missing=False)
    if parsed is None or parsed != parsed.to_integral_value():
        return None
    return int(parsed)


def _to_int_or_error(value: Any, field_name: str) -> int:
    integer_value = _to_int_or_none(value)
    if integer_value is None:
        raise MarketDataValidationError(f"missing or invalid {field_name}")
    return integer_value


def _select_quote_timestamp(quote_data: dict[str, Any], *, request_received_at: datetime) -> datetime:
    for key in (
        "venue_last_trade_time",
        "last_trade_time",
        "venue_last_non_reg_trade_time",
        "last_non_reg_trade_time",
        "venue_bid_time",
        "venue_ask_time",
        "updated_at",
        "timestamp",
    ):
        timestamp = quote_data.get(key)
        if timestamp:
            return _parse_datetime(timestamp)
    return request_received_at


def _select_quote_price(quote_data: dict[str, Any], *, numeric_parse_errors: dict[str, str] | None = None) -> tuple[Decimal, str]:
    errors = numeric_parse_errors if numeric_parse_errors is not None else {}
    for key, source in (
        ("last_trade_price", "last_trade_price"),
        ("last_non_reg_trade_price", "last_non_reg_trade_price"),
        ("last_price", "last_price"),
        ("mark_price", "mark_price"),
    ):
        if key not in quote_data:
            continue
        value = _parse_optional_decimal(quote_data[key], key, numeric_parse_errors=errors, record_missing=False)
        if value is not None and value > 0:
            return value, source
        if value is not None:
            errors[key] = "NON_POSITIVE"
            raise MarketDataValidationError(f"invalid non-positive {key}")
    bid = _parse_optional_decimal(quote_data.get("bid_price"), "bid_price", numeric_parse_errors=errors, record_missing=False)
    ask = _parse_optional_decimal(quote_data.get("ask_price"), "ask_price", numeric_parse_errors=errors, record_missing=False)
    if bid is not None and bid > 0 and ask is not None and ask > 0:
        return (bid + ask) / Decimal("2"), "bid_ask_midpoint"
    if ask is not None and ask > 0:
        return ask, "ask_price"
    if bid is not None and bid > 0:
        return bid, "bid_price"
    raise MarketDataValidationError("quote payload did not contain a usable price")


def _select_previous_close(
    quote_data: dict[str, Any],
    close_data: dict[str, Any],
    *,
    numeric_parse_errors: dict[str, str] | None = None,
) -> tuple[Decimal | None, str | None]:
    errors = numeric_parse_errors if numeric_parse_errors is not None else {}
    for key, source in (
        ("previous_close", "quote.previous_close"),
        ("adjusted_previous_close", "quote.adjusted_previous_close"),
        ("close_price", "quote.close_price"),
    ):
        if key not in quote_data:
            continue
        value = _parse_optional_decimal(quote_data[key], key, numeric_parse_errors=errors, record_missing=False)
        if value is not None and value > 0:
            return value, source
        if value is not None:
            errors[key] = "NON_POSITIVE"
    for key, source in (("price", "close.price"), ("previous_close", "close.previous_close")):
        if key not in close_data:
            continue
        value = _parse_optional_decimal(close_data[key], source, numeric_parse_errors=errors, record_missing=False)
        if value is not None and value > 0:
            return value, source
    return None, None


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None or dt.utcoffset() is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_market_session(value: Any) -> MarketSession | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"reg", "regular", "rth"}:
        return MarketSession.REGULAR
    if text in {"pre", "premarket"}:
        return MarketSession.PREMARKET
    if text in {"post", "after_hours", "after-hours"}:
        return MarketSession.AFTER_HOURS
    if text in {"closed", "close"}:
        return MarketSession.CLOSED
    return None
