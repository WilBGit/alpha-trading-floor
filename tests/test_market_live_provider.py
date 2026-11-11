from __future__ import annotations

import os
from decimal import Decimal

from dotenv import load_dotenv
import pytest

load_dotenv(override=False)

RUN_LIVE = os.getenv("RUN_LIVE_MARKET_TESTS", "false").lower() in {"1", "true", "yes"}

from alpha_market_engine.market.providers import AlpacaMarketDataProvider
from alpha_market_engine.market.snapshot import MarketSnapshotService


@pytest.mark.skipif(not RUN_LIVE, reason="Live market tests are disabled; set RUN_LIVE_MARKET_TESTS=true to enable")
@pytest.mark.asyncio
async def test_live_alpaca_market_snapshot():
    api_key = os.getenv("ALPACA_API_KEY")
    api_secret = os.getenv("ALPACA_API_SECRET")
    assert api_key is not None and api_secret is not None
    provider = AlpacaMarketDataProvider(api_key=api_key, api_secret=api_secret, data_feed=os.getenv("ALPACA_DATA_FEED", "iex"))
    try:
        snapshot = await MarketSnapshotService(provider).create_snapshot()
        assert snapshot.provider == "alpaca"
        assert snapshot.market_session is not None
        assert snapshot.snapshot_time is not None
    finally:
        await provider.close()
