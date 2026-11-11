from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from alpha_market_engine.market.data_provider import MarketQuote, StaticMarketDataProvider
from alpha_market_engine.market.snapshot import MarketDataService

NY = ZoneInfo("America/New_York")
FRIDAY_CLOSE = datetime(2026, 1, 2, 16, 0, tzinfo=NY)
FRIDAY_QUOTE = MarketQuote(
    symbol="SPY",
    price=Decimal("100"),
    previous_close=Decimal("99"),
    quote_timestamp=FRIDAY_CLOSE,
    provider="static",
    data_feed="synthetic",
)
OLDER_QUOTE = MarketQuote(
    symbol="SPY",
    price=Decimal("100"),
    previous_close=Decimal("99"),
    quote_timestamp=datetime(2026, 1, 1, 16, 0, tzinfo=NY),
    provider="static",
    data_feed="synthetic",
)


def make_service(now: datetime) -> MarketDataService:
    provider = StaticMarketDataProvider(quotes={"SPY": FRIDAY_QUOTE})
    return MarketDataService(provider, clock=lambda: now)


@pytest.mark.parametrize(
    "now, quote_timestamp, expected_reference_close",
    [
        (datetime(2026, 1, 3, 10, 0, tzinfo=NY), FRIDAY_CLOSE, datetime(2026, 1, 2, 16, 0, tzinfo=NY)),
        (datetime(2026, 1, 4, 10, 0, tzinfo=NY), FRIDAY_CLOSE, datetime(2026, 1, 2, 16, 0, tzinfo=NY)),
        (datetime(2026, 1, 5, 8, 15, tzinfo=NY), FRIDAY_CLOSE, datetime(2026, 1, 2, 16, 0, tzinfo=NY)),
        (datetime(2026, 1, 20, 8, 15, tzinfo=NY), datetime(2026, 1, 16, 16, 0, tzinfo=NY), datetime(2026, 1, 16, 16, 0, tzinfo=NY)),
        (datetime(2026, 2, 17, 8, 15, tzinfo=NY), datetime(2026, 2, 13, 16, 0, tzinfo=NY), datetime(2026, 2, 13, 16, 0, tzinfo=NY)),
    ],
)
def test_closed_and_premarket_freshness_uses_last_completed_xnys_session(now: datetime, quote_timestamp: datetime, expected_reference_close: datetime):
    service = make_service(now)
    quote = FRIDAY_QUOTE if quote_timestamp == FRIDAY_CLOSE else MarketQuote(
        symbol="SPY",
        price=Decimal("100"),
        previous_close=Decimal("99"),
        quote_timestamp=quote_timestamp,
        provider="static",
        data_feed="synthetic",
    )
    freshness = service._quote_is_fresh(quote, service.session_calculator.current_session(now))

    assert freshness.stale is False
    assert freshness.reference_timestamp == expected_reference_close
    assert freshness.reference_session == expected_reference_close.date().isoformat()


def test_older_session_quote_becomes_stale_once_a_new_session_completes():
    now = datetime(2026, 1, 5, 8, 15, tzinfo=NY)
    service = make_service(now)
    freshness = service._quote_is_fresh(OLDER_QUOTE, service.session_calculator.current_session(now))

    assert freshness.stale is True
    assert freshness.reference_timestamp == datetime(2026, 1, 2, 16, 0, tzinfo=NY)
