from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from alpha_market_engine.market.models import MarketSession
from alpha_market_engine.market.session import MarketSessionCalculator


NY = ZoneInfo("America/New_York")
CALC = MarketSessionCalculator()


def test_regular_session_detection():
    assert CALC.current_session(datetime(2026, 1, 5, 10, 0, tzinfo=NY)) == MarketSession.REGULAR


def test_premarket_detection():
    assert CALC.current_session(datetime(2026, 1, 5, 8, 15, tzinfo=NY)) == MarketSession.PREMARKET


def test_after_hours_detection():
    assert CALC.current_session(datetime(2026, 1, 5, 17, 30, tzinfo=NY)) == MarketSession.AFTER_HOURS


def test_weekend_detection():
    assert CALC.current_session(datetime(2026, 1, 3, 10, 0, tzinfo=NY)) == MarketSession.CLOSED


def test_holiday_detection():
    assert CALC.current_session(datetime(2026, 12, 25, 10, 0, tzinfo=NY)) == MarketSession.CLOSED


def test_early_close_detection():
    assert CALC.current_session(datetime(2026, 11, 27, 14, 30, tzinfo=NY)) == MarketSession.CLOSED
