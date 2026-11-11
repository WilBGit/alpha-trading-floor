# pyright: reportMissingImports=false
from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest

from alpha_market_engine.market.data_provider import MarketDataAuthenticationError, MarketDataError, MarketDataValidationError
from alpha_market_engine.market.models import IntradayBar, MarketSession
from alpha_market_engine.market.providers.robinhood import (
    OpeningRangeResult,
    RobinhoodMarketDataProvider,
    RobinhoodToolNotAllowedError,
    _extract_json_value,
    calculate_opening_range,
    calculate_percentage_change,
)
from alpha_market_engine.market.streaming.state import VWAPMethod, VWAPQuality, assess_session_bar_coverage, calculate_session_vwap


FIXED_NOW = datetime(2026, 7, 13, 20, 0, tzinfo=UTC)


class FakeRobinhoodToolClient:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
        self.calls.append((tool_name, arguments))
        if tool_name not in self.responses:
            raise AssertionError(f"unexpected tool call: {tool_name}")
        return self.responses[tool_name]


def make_provider(responses: dict[str, object], *, now: datetime = FIXED_NOW) -> RobinhoodMarketDataProvider:
    return RobinhoodMarketDataProvider(tool_client=FakeRobinhoodToolClient(responses), clock=lambda: now)


def quote_payload(*, include_trade: bool = True, include_close: bool = True, include_times: bool = True) -> dict[str, object]:
    quote: dict[str, object] = {
        "symbol": "SPY",
        "bid_price": "752.450000",
        "ask_price": "752.480000",
        "previous_close": "748.100000",
        "adjusted_previous_close": "748.100000",
        "volume": 123456,
        "state": "regular",
        "has_traded": True,
    }
    if include_trade:
        quote["last_trade_price"] = "752.470000"
    if include_times:
        quote["venue_last_trade_time"] = "2026-07-13T19:59:57Z"
        quote["venue_bid_time"] = "2026-07-13T19:59:58Z"
        quote["venue_ask_time"] = "2026-07-13T19:59:58Z"
    close = {"price": "748.100000", "date": "2026-07-12"} if include_close else {}
    return {"data": {"results": [{"quote": quote, "close": close}]}}


def bar_payload(rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "data": {
            "results": [
                {
                    "symbol": "SPY",
                    "interval": "minute",
                    "bounds": "regular",
                    "bars": rows,
                }
            ]
        }
    }


def test_robinhood_extract_json_value_handles_pretty_cli_output() -> None:
    text = (
        "Warning: Unknown toolsets: moa\n"
        "\n┌─ Reasoning ──────────────────────────────────────────────────────────────────┐\n"
        "The tool returned the result.\n"
        "{\n"
        "  \"data\": {\n"
        "    \"results\": [\n"
        "      {\n"
        "        \"symbol\": \"SPY\",\n"
        "        \"bars\": [{\"begins_at\": \"2026-07-13T13:30:00Z\"}]\n"
        "      }\n"
        "    ]\n"
        "  },\n"
        "  \"guide\": \"ignore me\"\n"
        "}\n"
        "More commentary after the JSON should be ignored.\n"
    )

    parsed = _extract_json_value(text)
    assert parsed["data"]["results"][0]["symbol"] == "SPY"
    assert parsed["data"]["results"][0]["bars"][0]["begins_at"] == "2026-07-13T13:30:00Z"


@pytest.mark.asyncio
async def test_robinhood_allowlist_rejects_write_tools() -> None:
    provider = make_provider({})
    with pytest.raises(RobinhoodToolNotAllowedError):
        await provider.call_tool("mcp__robinhood_trading__add_option_to_watchlist", {"symbol": "SPY"})


@pytest.mark.asyncio
async def test_robinhood_quote_normalization_and_metadata() -> None:
    provider = make_provider({"mcp__robinhood_trading__get_equity_quotes": quote_payload()})
    quote = await provider.get_quote("SPY")

    assert quote.symbol == "SPY"
    assert quote.provider == "robinhood"
    assert quote.data_feed == "agentic"
    assert quote.price == Decimal("752.470000")
    assert quote.previous_close == Decimal("748.100000")
    assert quote.quote_timestamp == datetime(2026, 7, 13, 19, 59, 57, tzinfo=UTC)
    assert quote.change == Decimal("4.370000")
    assert quote.change_percent == calculate_percentage_change(Decimal("752.470000"), Decimal("748.100000"))
    assert quote.validation_metadata["source_tool"] == "mcp__robinhood_trading__get_equity_quotes"
    assert quote.validation_metadata["current_price_source"] == "last_trade_price"
    assert quote.validation_metadata["previous_close_source"] == "quote.previous_close"


@pytest.mark.asyncio
async def test_robinhood_missing_timestamp_falls_back_to_request_time() -> None:
    provider = make_provider({"mcp__robinhood_trading__get_equity_quotes": quote_payload(include_times=False)})
    quote = await provider.get_quote("SPY")

    assert quote.quote_timestamp == FIXED_NOW


@pytest.mark.asyncio
async def test_robinhood_price_falls_back_to_midpoint_when_last_trade_missing() -> None:
    provider = make_provider({"mcp__robinhood_trading__get_equity_quotes": quote_payload(include_trade=False)})
    quote = await provider.get_quote("SPY")

    assert quote.price == Decimal("752.465")
    assert quote.validation_metadata["current_price_source"] == "bid_ask_midpoint"


@pytest.mark.asyncio
async def test_robinhood_invalid_price_is_rejected() -> None:
    payload = cast(dict[str, Any], quote_payload())
    payload["data"]["results"][0]["quote"]["last_trade_price"] = "0"
    provider = make_provider({"mcp__robinhood_trading__get_equity_quotes": payload})

    with pytest.raises(MarketDataValidationError):
        await provider.get_quote("SPY")


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [[], {}])
async def test_robinhood_empty_quote_result_is_not_normalized_as_quote(payload: object) -> None:
    provider = make_provider({"mcp__robinhood_trading__get_equity_quotes": payload})
    with pytest.raises(MarketDataValidationError, match="empty quote result"):
        await provider.get_quote("SPY")


@pytest.mark.asyncio
async def test_robinhood_error_envelopes_are_classified_before_normalization() -> None:
    provider = make_provider({"mcp__robinhood_trading__get_equity_quotes": {"error": "temporary tool failure"}})
    with pytest.raises(MarketDataError, match="error envelope"):
        await provider.get_quote("SPY")

    provider = make_provider({"mcp__robinhood_trading__get_equity_quotes": {"error": "authentication required"}})
    with pytest.raises(MarketDataAuthenticationError):
        await provider.get_quote("SPY")


@pytest.mark.asyncio
async def test_robinhood_wrong_symbol_is_not_accepted_for_requested_symbol() -> None:
    payload = cast(dict[str, Any], quote_payload())
    payload["data"]["results"][0]["quote"]["symbol"] = "QQQ"
    provider = make_provider({"mcp__robinhood_trading__get_equity_quotes": payload})
    with pytest.raises(MarketDataValidationError, match="empty quote result"):
        await provider.get_quote("SPY")


@pytest.mark.asyncio
async def test_robinhood_batch_keeps_valid_symbols_when_one_has_no_price() -> None:
    first = cast(dict[str, Any], quote_payload())
    second = cast(dict[str, Any], quote_payload())
    second["data"]["results"][0]["quote"]["symbol"] = "QQQ"
    second["data"]["results"][0]["quote"]["last_trade_price"] = "0"
    payload = {"data": {"results": [first["data"]["results"][0], second["data"]["results"][0]]}}
    provider = make_provider({"mcp__robinhood_trading__get_equity_quotes": payload})
    quotes = await provider.get_quotes(["SPY", "QQQ"])
    assert set(quotes) == {"SPY"}


@pytest.mark.asyncio
async def test_robinhood_batch_preserves_valid_symbols_when_one_candidate_has_no_price() -> None:
    spy = cast(dict[str, Any], quote_payload())
    spy["data"]["results"][0]["quote"]["symbol"] = "SPY"
    qqq = cast(dict[str, Any], quote_payload())
    qqq["data"]["results"][0]["quote"]["symbol"] = "QQQ"
    dia = cast(dict[str, Any], quote_payload())
    dia["data"]["results"][0]["quote"]["symbol"] = "DIA"
    iwm = cast(dict[str, Any], quote_payload())
    iwm["data"]["results"][0]["quote"]["symbol"] = "IWM"
    iwm["data"]["results"][0]["quote"]["last_trade_price"] = "0"
    payload = {"data": {"results": spy["data"]["results"] + qqq["data"]["results"] + dia["data"]["results"] + iwm["data"]["results"]}}
    provider = make_provider({"mcp__robinhood_trading__get_equity_quotes": payload})

    quotes = await provider.get_quotes(["SPY", "QQQ", "DIA", "IWM"])

    assert set(quotes) == {"SPY", "QQQ", "DIA"}


@pytest.mark.asyncio
async def test_robinhood_previous_close_normalization_uses_close_payload() -> None:
    payload = cast(dict[str, Any], quote_payload())
    payload["data"]["results"][0]["quote"].pop("previous_close", None)
    payload["data"]["results"][0]["quote"].pop("adjusted_previous_close", None)
    provider = make_provider({"mcp__robinhood_trading__get_equity_quotes": payload})

    quote = await provider.get_quote("SPY")
    assert quote.previous_close == Decimal("748.100000")
    assert quote.validation_metadata["previous_close_source"] == "close.price"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_value", "classification"),
    [
        (Decimal("0.5"), None),
        (1, None),
        (0.5, None),
        ("0.5", None),
        (" 0.5 ", None),
        (None, "MISSING"),
        ("", "EMPTY_STRING"),
        ("   ", "WHITESPACE_STRING"),
        ("unknown", "PLACEHOLDER_TEXT"),
        ("N/A", "PLACEHOLDER_TEXT"),
        ("–", "PLACEHOLDER_TEXT"),
        ("null", "PLACEHOLDER_TEXT"),
        (True, "BOOLEAN"),
        (False, "BOOLEAN"),
        ([], "LIST"),
        ({}, "DICT"),
        ("1.2.3", "NON_NUMERIC"),
        ("0.5%", "NON_NUMERIC"),
        (math.nan, "NAN"),
        (math.inf, "INFINITY"),
        (-math.inf, "INFINITY"),
    ],
)
async def test_robinhood_change_percent_numeric_safety(raw_value: object, classification: str | None) -> None:
    payload = cast(dict[str, Any], quote_payload())
    payload["data"]["results"][0]["quote"]["change_percent"] = raw_value
    quote = await make_provider({"mcp__robinhood_trading__get_equity_quotes": payload}).get_quote("SPY")

    assert quote.change_percent == calculate_percentage_change(Decimal("752.470000"), Decimal("748.100000"))
    assert quote.validation_metadata["change_percent_source"] == "CALCULATED"
    if classification == "MISSING":
        assert "change_percent" in quote.validation_metadata["missing_fields"]
    elif classification is not None:
        assert quote.validation_metadata["numeric_parse_errors"]["change_percent"] == classification
    else:
        assert "change_percent" not in quote.validation_metadata.get("numeric_parse_errors", {})


@pytest.mark.asyncio
async def test_robinhood_malformed_last_trade_uses_valid_fallback_price() -> None:
    payload = cast(dict[str, Any], quote_payload())
    quote_data = payload["data"]["results"][0]["quote"]
    quote_data["last_trade_price"] = "not-a-price"
    quote_data["mark_price"] = "752.460000"

    quote = await make_provider({"mcp__robinhood_trading__get_equity_quotes": payload}).get_quote("SPY")

    assert quote.price == Decimal("752.460000")
    assert quote.validation_metadata["current_price_source"] == "mark_price"
    assert quote.validation_metadata["numeric_parse_errors"]["last_trade_price"] == "NON_NUMERIC"


@pytest.mark.asyncio
async def test_robinhood_rejects_quote_without_any_valid_current_price() -> None:
    payload = cast(dict[str, Any], quote_payload())
    quote_data = payload["data"]["results"][0]["quote"]
    for field in ("last_trade_price", "last_non_reg_trade_price", "last_price", "mark_price", "bid_price", "ask_price"):
        quote_data[field] = "invalid"

    with pytest.raises(MarketDataValidationError):
        await make_provider({"mcp__robinhood_trading__get_equity_quotes": payload}).get_quote("SPY")


@pytest.mark.asyncio
async def test_robinhood_malformed_previous_close_falls_back_to_close_payload() -> None:
    payload = cast(dict[str, Any], quote_payload())
    quote_data = payload["data"]["results"][0]["quote"]
    quote_data["previous_close"] = "N/A"
    quote_data["adjusted_previous_close"] = "invalid"

    quote = await make_provider({"mcp__robinhood_trading__get_equity_quotes": payload}).get_quote("SPY")

    assert quote.previous_close == Decimal("748.100000")
    assert quote.validation_metadata["previous_close_source"] == "close.price"
    assert quote.validation_metadata["numeric_parse_errors"]["previous_close"] == "PLACEHOLDER_TEXT"


@pytest.mark.asyncio
async def test_robinhood_unavailable_previous_close_suppresses_change_percent() -> None:
    payload = cast(dict[str, Any], quote_payload(include_close=False))
    quote_data = payload["data"]["results"][0]["quote"]
    quote_data["previous_close"] = "N/A"
    quote_data["adjusted_previous_close"] = "invalid"
    quote_data["change_percent"] = "unknown"

    quote = await make_provider({"mcp__robinhood_trading__get_equity_quotes": payload}).get_quote("SPY")

    assert quote.price == Decimal("752.470000")
    assert quote.previous_close is None
    assert quote.change is None
    assert quote.change_percent is None
    assert quote.validation_metadata["change_percent_source"] == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_robinhood_malformed_optional_quote_fields_are_suppressed() -> None:
    payload = cast(dict[str, Any], quote_payload())
    quote_data = payload["data"]["results"][0]["quote"]
    quote_data.update(
        {
            "bid_price": "unknown",
            "ask_price": [],
            "open_price": "N/A",
            "high_price": {},
            "low_price": "0.5%",
            "volume": "not-a-volume",
            "trade_count": True,
        }
    )

    quote = await make_provider({"mcp__robinhood_trading__get_equity_quotes": payload}).get_quote("SPY")

    assert quote.price == Decimal("752.470000")
    assert quote.open_price is None
    assert quote.day_high is None
    assert quote.day_low is None
    assert quote.volume is None
    assert quote.trade_count is None
    assert {"bid_price", "ask_price", "open_price", "day_high", "day_low", "volume", "trade_count"}.issubset(
        quote.validation_metadata["invalid_fields"]
    )


@pytest.mark.asyncio
async def test_robinhood_bar_numeric_invalidity_does_not_leak_decimal_errors() -> None:
    provider = make_provider(
        {
            "mcp__robinhood_trading__get_equity_historicals": bar_payload(
                [{
                    "begins_at": "2026-07-13T13:30:00Z",
                    "open_price": "N/A",
                    "close_price": "100.0",
                    "high_price": "100.5",
                    "low_price": "99.5",
                    "volume": "bad",
                    "session": "reg",
                }]
            )
        }
    )

    with pytest.raises(MarketDataValidationError):
        await provider.get_intraday_bars("SPY", "1m")


@pytest.mark.asyncio
async def test_robinhood_bar_valid_zero_volume_is_preserved() -> None:
    provider = make_provider(
        {
            "mcp__robinhood_trading__get_equity_historicals": bar_payload(
                [{
                    "begins_at": "2026-07-13T13:30:00Z",
                    "open_price": "100.0",
                    "close_price": "100.0",
                    "high_price": "100.5",
                    "low_price": "99.5",
                    "volume": 0,
                    "session": "reg",
                }]
            )
        }
    )

    bars = await provider.get_intraday_bars("SPY", "1m")
    assert bars[0].volume == 0


@pytest.mark.asyncio
async def test_robinhood_bar_normalization_and_ordering() -> None:
    provider = make_provider(
        {
            "mcp__robinhood_trading__get_equity_historicals": bar_payload(
                [
                    {
                        "begins_at": "2026-07-13T13:30:00Z",
                        "open_price": "752.470000",
                        "close_price": "753.170000",
                        "high_price": "753.200000",
                        "low_price": "752.400000",
                        "volume": 114204,
                        "session": "reg",
                        "vwap": "752.900000",
                    },
                    {
                        "begins_at": "2026-07-13T13:31:00Z",
                        "open_price": "753.200000",
                        "close_price": "753.570000",
                        "high_price": "753.650000",
                        "low_price": "753.016200",
                        "volume": 115762,
                        "session": "reg",
                        "vwap": "753.330000",
                    },
                ]
            )
        }
    )

    bars = await provider.get_intraday_bars("SPY", "1m")
    assert [bar.timestamp for bar in bars] == [
        datetime(2026, 7, 13, 13, 30, tzinfo=UTC),
        datetime(2026, 7, 13, 13, 31, tzinfo=UTC),
    ]
    assert bars[0].provider == "robinhood"
    assert bars[0].session == MarketSession.REGULAR
    assert bars[0].vwap == Decimal("752.900000")


@pytest.mark.asyncio
async def test_robinhood_bar_normalization_uses_most_recent_session_during_premarket() -> None:
    fixed_now = datetime(2026, 7, 14, 7, 40, tzinfo=UTC)
    provider = RobinhoodMarketDataProvider(tool_client=FakeRobinhoodToolClient({"mcp__robinhood_trading__get_equity_historicals": bar_payload([
        {
            "begins_at": "2026-07-13T13:30:00Z",
            "open_price": "752.470000",
            "close_price": "753.170000",
            "high_price": "753.200000",
            "low_price": "752.400000",
            "volume": 114204,
            "session": "reg",
            "vwap": "752.900000",
        }
    ])}), clock=lambda: fixed_now)

    bars = await provider.get_intraday_bars("SPY", "1m")
    assert len(bars) == 1
    assert bars[0].timestamp == datetime(2026, 7, 13, 13, 30, tzinfo=UTC)
    tool_client = cast(FakeRobinhoodToolClient, provider._tool_client)
    assert len(tool_client.calls) == 1
    for _, arguments in tool_client.calls:
        assert str(arguments["start_time"]).startswith("2026-07-13T")
        assert str(arguments["end_time"]).startswith("2026-07-13T")


@pytest.mark.asyncio
async def test_robinhood_duplicate_and_out_of_order_bars_are_rejected() -> None:
    provider = make_provider(
        {
            "mcp__robinhood_trading__get_equity_historicals": bar_payload(
                [
                    {
                        "begins_at": "2026-07-13T13:31:00Z",
                        "open_price": "753.200000",
                        "close_price": "753.570000",
                        "high_price": "753.650000",
                        "low_price": "753.016200",
                        "volume": 115762,
                        "session": "reg",
                    },
                    {
                        "begins_at": "2026-07-13T13:30:00Z",
                        "open_price": "752.470000",
                        "close_price": "753.170000",
                        "high_price": "753.200000",
                        "low_price": "752.400000",
                        "volume": 114204,
                        "session": "reg",
                    },
                ]
            )
        }
    )

    with pytest.raises(MarketDataValidationError):
        await provider.get_intraday_bars("SPY", "1m")


def test_robinhood_bar_completeness_framework() -> None:
    bars = [
        _bar("2026-07-13T13:30:00Z", "752.47", "753.20", "752.40", "753.17", 114204),
        _bar("2026-07-13T13:31:00Z", "753.20", "753.65", "753.02", "753.57", 115762),
        _bar("2026-07-13T13:33:00Z", "753.50", "753.58", "753.18", "753.27", 36350),
        _bar("2026-07-13T13:34:00Z", "753.27", "753.62", "753.22", "753.43", 29403),
    ]
    coverage = assess_session_bar_coverage(
        bars,
        session_open=datetime(2026, 7, 13, 13, 30, tzinfo=UTC),
        session_close=datetime(2026, 7, 13, 13, 35, tzinfo=UTC),
    )
    assert coverage.expected_bar_count == 5
    assert coverage.actual_bar_count == 4
    assert coverage.missing_minute_count == 1
    assert coverage.quality.name == VWAPQuality.DEGRADED.name


def test_robinhood_session_vwap_calculation() -> None:
    bars = [
        _bar("2026-07-13T13:30:00Z", "752.47", "753.20", "752.40", "753.17", 100, vwap="752.95"),
        _bar("2026-07-13T13:31:00Z", "753.20", "753.65", "753.02", "753.57", 200, vwap="753.32"),
    ]
    result = calculate_session_vwap(
        bars,
        session_open=datetime(2026, 7, 13, 13, 30, tzinfo=UTC),
        session_close=datetime(2026, 7, 13, 13, 32, tzinfo=UTC),
    )
    assert result.quality == VWAPQuality.GOOD
    assert result.method == VWAPMethod.PROVIDER_BAR_WEIGHTED
    assert result.vwap is not None

    fallback = calculate_session_vwap(
        [_bar("2026-07-13T13:30:00Z", "752.47", "753.20", "752.40", "753.17", 100)],
        session_open=datetime(2026, 7, 13, 13, 30, tzinfo=UTC),
        session_close=datetime(2026, 7, 13, 13, 31, tzinfo=UTC),
    )
    assert fallback.method == VWAPMethod.TYPICAL_PRICE_APPROXIMATION


def test_robinhood_opening_range_calculation() -> None:
    bars = [
        _bar("2026-07-13T13:30:00Z", "752.47", "753.20", "752.40", "753.17", 100),
        _bar("2026-07-13T13:31:00Z", "753.20", "753.65", "753.02", "753.57", 200),
        _bar("2026-07-13T13:32:00Z", "753.56", "753.91", "753.46", "753.52", 300),
        _bar("2026-07-13T13:33:00Z", "753.50", "753.58", "753.18", "753.27", 400),
        _bar("2026-07-13T13:34:00Z", "753.27", "753.62", "753.22", "753.43", 500),
    ]
    result = calculate_opening_range(
        bars,
        session_open=datetime(2026, 7, 13, 13, 30, tzinfo=UTC),
        window_minutes=5,
    )
    assert isinstance(result, OpeningRangeResult)
    assert result.bar_count == 5
    assert result.high == Decimal("753.91")
    assert result.low == Decimal("752.40")
    assert result.coverage.quality == VWAPQuality.GOOD


@pytest.mark.asyncio
async def test_robinhood_health_check_reports_live_quote() -> None:
    provider = make_provider({"mcp__robinhood_trading__get_equity_quotes": quote_payload()})
    health = await provider.health_check()

    assert health.provider == "robinhood"
    assert health.healthy is True
    assert health.data_feed == "agentic"
    assert health.metadata["sample_symbol"] == "SPY"


@pytest.mark.asyncio
async def test_robinhood_historical_fetch_caches_and_coalesces_requests() -> None:
    class GateHistoricalToolClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []
            self.release = asyncio.Event()
            self.active = 0
            self.max_active = 0

        async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
            self.calls.append((tool_name, arguments))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await self.release.wait()
                return bar_payload([
                    {
                        "begins_at": "2026-07-13T13:30:00Z",
                        "open_price": "752.470000",
                        "close_price": "753.170000",
                        "high_price": "753.200000",
                        "low_price": "752.400000",
                        "volume": 114204,
                        "session": "reg",
                    }
                ])
            finally:
                self.active -= 1

    tool_client = GateHistoricalToolClient()
    provider = RobinhoodMarketDataProvider(tool_client=tool_client, clock=lambda: FIXED_NOW)

    first = asyncio.create_task(provider.get_intraday_bars("SPY", "1m"))
    second = asyncio.create_task(provider.get_intraday_bars("SPY", "1m"))
    while len(tool_client.calls) == 0:
        await asyncio.sleep(0)
    assert len(tool_client.calls) == 1
    tool_client.release.set()
    bars_a = await first
    bars_b = await second

    assert bars_a[0].timestamp == bars_b[0].timestamp == datetime(2026, 7, 13, 13, 30, tzinfo=UTC)
    assert len(tool_client.calls) == 1
    assert any(event["cache_hit"] for event in provider.last_historical_fetch_telemetry)


@pytest.mark.asyncio
async def test_robinhood_historical_fetch_is_session_specific() -> None:
    now = {"value": datetime(2026, 7, 13, 20, 0, tzinfo=UTC)}

    class StaticHistoricalToolClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
            self.calls.append((tool_name, arguments))
            return bar_payload([
                {
                    "begins_at": "2026-07-13T13:30:00Z",
                    "open_price": "752.470000",
                    "close_price": "753.170000",
                    "high_price": "753.200000",
                    "low_price": "752.400000",
                    "volume": 114204,
                    "session": "reg",
                }
            ])

    tool_client = StaticHistoricalToolClient()
    provider = RobinhoodMarketDataProvider(tool_client=tool_client, clock=lambda: now["value"])

    await provider.get_intraday_bars("SPY", "1m")
    now["value"] = datetime(2026, 7, 14, 20, 0, tzinfo=UTC)
    await provider.get_intraday_bars("SPY", "1m")

    assert len(tool_client.calls) == 2
    first_args = tool_client.calls[0][1]
    second_args = tool_client.calls[1][1]
    assert first_args["start_time"] != second_args["start_time"]


@pytest.mark.asyncio
async def test_robinhood_historical_fetch_retries_retryable_timeouts() -> None:
    class RetryableHistoricalToolClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []
            self.attempt = 0

        async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
            self.calls.append((tool_name, arguments))
            self.attempt += 1
            if self.attempt == 1:
                raise MarketDataError("robinhood tool call timed out: mcp__robinhood_trading__get_equity_historicals")
            return bar_payload([
                {
                    "begins_at": "2026-07-13T13:30:00Z",
                    "open_price": "752.470000",
                    "close_price": "753.170000",
                    "high_price": "753.200000",
                    "low_price": "752.400000",
                    "volume": 114204,
                    "session": "reg",
                }
            ])

    provider = RobinhoodMarketDataProvider(tool_client=RetryableHistoricalToolClient(), clock=lambda: FIXED_NOW, historical_max_retries=1)
    bars = await provider.get_intraday_bars("SPY", "1m")

    assert len(bars) == 1
    assert len(cast(RetryableHistoricalToolClient, provider._tool_client).calls) == 2
    assert provider.last_historical_fetch_telemetry[-1]["success"] is True


@pytest.mark.asyncio
async def test_robinhood_historical_fetch_does_not_retry_validation_errors() -> None:
    class ValidationErrorHistoricalToolClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
            self.calls.append((tool_name, arguments))
            raise MarketDataValidationError("unsupported interval")

    provider = RobinhoodMarketDataProvider(tool_client=ValidationErrorHistoricalToolClient(), clock=lambda: FIXED_NOW, historical_max_retries=2)

    with pytest.raises(MarketDataValidationError):
        await provider.get_intraday_bars("SPY", "1m")
    assert len(cast(ValidationErrorHistoricalToolClient, provider._tool_client).calls) == 1


@pytest.mark.asyncio
async def test_robinhood_historical_fetch_respects_max_concurrency() -> None:
    class ConcurrencyTrackingHistoricalToolClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []
            self.release = asyncio.Event()
            self.active = 0
            self.max_active = 0

        async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
            self.calls.append((tool_name, arguments))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await self.release.wait()
                symbol_list = cast(list[str], arguments.get("symbols", ["SPY"]))
                symbol = symbol_list[0]
                return {
                    "data": {
                        "results": [
                            {
                                "symbol": symbol,
                                "interval": "minute",
                                "bounds": "regular",
                                "bars": [
                                    {
                                        "begins_at": "2026-07-13T13:30:00Z",
                                        "open_price": "752.470000",
                                        "close_price": "753.170000",
                                        "high_price": "753.200000",
                                        "low_price": "752.400000",
                                        "volume": 114204,
                                        "session": "reg",
                                    }
                                ],
                            }
                        ]
                    }
                }
            finally:
                self.active -= 1

    tool_client = ConcurrencyTrackingHistoricalToolClient()
    provider = RobinhoodMarketDataProvider(tool_client=tool_client, clock=lambda: FIXED_NOW, historical_max_concurrency=2)

    tasks = [
        asyncio.create_task(provider.get_intraday_bars(symbol, "1m"))
        for symbol in ("SPY", "QQQ", "DIA")
    ]
    while len(tool_client.calls) < 2:
        await asyncio.sleep(0)
    assert tool_client.max_active == 2
    tool_client.release.set()
    await asyncio.gather(*tasks)
    assert tool_client.max_active == 2


@pytest.mark.asyncio
async def test_robinhood_historical_fetch_times_out_by_symbol_budget() -> None:
    class SlowHistoricalToolClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
            self.calls.append((tool_name, arguments))
            await asyncio.sleep(0.1)
            return bar_payload([
                {
                    "begins_at": "2026-07-13T13:30:00Z",
                    "open_price": "752.470000",
                    "close_price": "753.170000",
                    "high_price": "753.200000",
                    "low_price": "752.400000",
                    "volume": 114204,
                    "session": "reg",
                }
            ])

    provider = RobinhoodMarketDataProvider(tool_client=SlowHistoricalToolClient(), clock=lambda: FIXED_NOW, historical_total_timeout_seconds=0.01, historical_max_retries=0)

    with pytest.raises(MarketDataError):
        await provider.get_intraday_bars("SPY", "1m")
    assert len(cast(SlowHistoricalToolClient, provider._tool_client).calls) == 1
    assert provider.last_historical_fetch_telemetry[-1]["timeout"] is True


@pytest.mark.asyncio
async def test_robinhood_historical_fetch_stops_at_retry_limit() -> None:
    class AlwaysTimeoutHistoricalToolClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
            self.calls.append((tool_name, arguments))
            raise MarketDataError("robinhood tool call timed out: mcp__robinhood_trading__get_equity_historicals")

    provider = RobinhoodMarketDataProvider(tool_client=AlwaysTimeoutHistoricalToolClient(), clock=lambda: FIXED_NOW, historical_max_retries=1)

    with pytest.raises(MarketDataError):
        await provider.get_intraday_bars("SPY", "1m")
    assert len(cast(AlwaysTimeoutHistoricalToolClient, provider._tool_client).calls) == 2


def _bar(
    timestamp: str,
    open_price: str,
    high_price: str,
    low_price: str,
    close_price: str,
    volume: int,
    *,
    vwap: str | None = None,
) -> IntradayBar:
    payload: dict[str, Any] = {
        "symbol": "SPY",
        "timestamp": datetime.fromisoformat(timestamp.replace("Z", "+00:00")),
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "volume": volume,
        "provider": "robinhood",
        "data_feed": "agentic",
    }
    if vwap is not None:
        payload["vwap"] = vwap
    return IntradayBar(**payload)
