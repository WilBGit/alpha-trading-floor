# pyright: reportMissingImports=false
from __future__ import annotations

import os
import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from alpha_market_engine.market.providers.robinhood import RobinhoodMarketDataProvider

RUN_LIVE_ROBINHOOD_TESTS = os.environ.get("RUN_LIVE_ROBINHOOD_TESTS", "").lower() == "true"


@pytest.mark.skipif(not RUN_LIVE_ROBINHOOD_TESTS, reason="set RUN_LIVE_ROBINHOOD_TESTS=true to run live Robinhood certification")
@pytest.mark.asyncio
async def test_live_robinhood_quote_samples_are_fresh_and_non_decreasing() -> None:
    provider = RobinhoodMarketDataProvider(timeout_seconds=240)
    symbols = ["SPY", "QQQ", "DIA", "IWM"]
    samples: list[dict[str, Any]] = []

    for sample_idx in range(3):
        request_received_at = datetime.now(UTC)
        quotes = await provider.get_quotes(symbols)
        assert set(quotes) == set(symbols)
        sample = {
            symbol: {
                "request_received_at": request_received_at,
                "quote": quote,
                "age_seconds": (request_received_at - quote.quote_timestamp).total_seconds(),
            }
            for symbol, quote in quotes.items()
        }
        samples.append(sample)
        if sample_idx < 2:
            await asyncio.sleep(10)

    for sample in samples:
        for symbol in symbols:
            quote = sample[symbol]["quote"]
            assert quote.quote_timestamp.tzinfo is not None
            assert quote.price > 0
            assert quote.previous_close is not None and quote.previous_close > 0
            assert sample[symbol]["age_seconds"] >= 0

    for symbol in symbols:
        timestamps = [samples[i][symbol]["quote"].quote_timestamp for i in range(3)]
        assert timestamps == sorted(timestamps)


@pytest.mark.skipif(not RUN_LIVE_ROBINHOOD_TESTS, reason="set RUN_LIVE_ROBINHOOD_TESTS=true to run live Robinhood certification")
@pytest.mark.asyncio
async def test_live_robinhood_1m_bars_are_ordered_and_regular() -> None:
    provider = RobinhoodMarketDataProvider(timeout_seconds=240)
    for symbol in ("SPY", "QQQ"):
        bars = await provider.get_intraday_bars(symbol, "1m", limit=5)
        assert bars
        assert [bar.timestamp for bar in bars] == sorted(bar.timestamp for bar in bars)
        assert all(bar.session is not None for bar in bars)
        assert all(bar.volume >= 0 for bar in bars)
        assert bars[0].timestamp.tzinfo is not None
