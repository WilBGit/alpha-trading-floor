from __future__ import annotations

import asyncio
import os

from typing import cast

import pytest
from dotenv import load_dotenv

from alpha_market_engine.market.streaming import AlpacaMarketDataStream, LiveMarketStateStore


pytestmark = pytest.mark.asyncio


@pytest.mark.skipif(os.getenv("RUN_LIVE_MARKET_STREAM_TESTS", "false").lower() != "true", reason="live market stream validation is opt-in")
async def test_live_alpaca_market_stream_connection_and_subscription():
    load_dotenv(override=False)
    api_key = os.getenv("ALPACA_API_KEY")
    api_secret = os.getenv("ALPACA_API_SECRET")
    feed = os.getenv("ALPACA_DATA_FEED", "iex")
    if not api_key or not api_secret:
        pytest.skip("Alpaca credentials are not configured")

    store = LiveMarketStateStore()
    stream = AlpacaMarketDataStream(cast(str, api_key), cast(str, api_secret), feed=feed, symbols=["SPY", "QQQ", "DIA", "IWM"], state_store=store)
    try:
        await stream.connect()
        await stream.subscribe(["SPY", "QQQ", "DIA", "IWM"])
        await asyncio.sleep(5)
        health = stream.health_check()
        assert health.connected is True
        assert health.authenticated is True
        assert {"SPY", "QQQ", "DIA", "IWM"}.issubset(set(health.subscribed_symbols))
    finally:
        await stream.stop()
