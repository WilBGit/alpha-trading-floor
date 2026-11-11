from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from alpha_market_engine.market.models import IntradayBar, MarketBreadth, MarketQuote, SectorPerformance


@pytest.mark.parametrize(
    "price,expected_message",
    [(-1, "price must be greater than zero"), (Decimal("0"), "price must be greater than zero")],
)
def test_market_quote_rejects_non_positive_price(price, expected_message):
    with pytest.raises(ValueError, match=expected_message):
        MarketQuote(symbol="SPY", price=price, previous_close=Decimal("600"), quote_timestamp=datetime.now(UTC), provider="test")


@pytest.mark.parametrize(
    "price",
    [float("nan"), float("inf"), float("-inf")],
)
def test_market_quote_rejects_non_finite_price(price):
    with pytest.raises(ValueError):
        MarketQuote(symbol="SPY", price=price, previous_close=Decimal("600"), quote_timestamp=datetime.now(UTC), provider="test")


@pytest.mark.parametrize(
    "high,low",
    [(Decimal("620"), Decimal("621")), (Decimal("10"), Decimal("11"))],
)
def test_market_quote_rejects_invalid_high_low(high, low):
    with pytest.raises(ValueError, match="day_high must be greater than or equal to day_low"):
        MarketQuote(
            symbol="SPY",
            price=Decimal("620"),
            previous_close=Decimal("600"),
            day_high=high,
            day_low=low,
            quote_timestamp=datetime.now(UTC),
            provider="test",
        )


@pytest.mark.parametrize("timestamp", [datetime.now(), datetime.now(UTC).replace(tzinfo=None)])
def test_market_quote_rejects_naive_timestamp(timestamp):
    with pytest.raises(ValueError, match="quote_timestamp must be timezone-aware"):
        MarketQuote(symbol="SPY", price=Decimal("620"), previous_close=Decimal("600"), quote_timestamp=timestamp, provider="test")


def test_market_quote_calculates_change_percent():
    quote = MarketQuote(symbol="SPY", price=Decimal("630"), previous_close=Decimal("600"), quote_timestamp=datetime.now(UTC), provider="test")
    assert quote.change == Decimal("30")
    assert quote.change_percent == Decimal("5")


def test_market_quote_flags_change_percent_mismatch():
    quote = MarketQuote(
        symbol="SPY",
        price=Decimal("630"),
        previous_close=Decimal("600"),
        change_percent=Decimal("3.25"),
        quote_timestamp=datetime.now(UTC),
        provider="test",
    )
    assert quote.validation_metadata["change_percent_mismatch"] is True
    assert quote.validation_metadata["expected_change_percent"] == Decimal("5")


@pytest.mark.parametrize("volume", [-1, -10])
def test_intraday_bar_rejects_negative_volume(volume):
    with pytest.raises(ValueError, match="volume must be non-negative"):
        IntradayBar(
            symbol="SPY",
            timestamp=datetime.now(UTC),
            open=Decimal("620"),
            high=Decimal("621"),
            low=Decimal("619"),
            close=Decimal("620.5"),
            volume=volume,
            provider="test",
        )


def test_market_breadth_rejects_negative_counts():
    with pytest.raises(ValueError, match="advancers must be non-negative"):
        MarketBreadth(advancers=-1, decliners=1)


def test_sector_performance_requires_timezone_aware_timestamp():
    with pytest.raises(ValueError, match="quote_timestamp must be timezone-aware"):
        SectorPerformance(sector="Technology", change_percent=Decimal("1.2"), provider="test", quote_timestamp=datetime.now())
