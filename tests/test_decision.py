from __future__ import annotations

from alpha_market_engine.content.decision import PublishContext, can_publish
from alpha_market_engine.market.calendar import is_nyse_holiday, is_trading_day
from datetime import datetime


def test_max_daily_post_enforcement():
    decision = can_publish(PublishContext(
        auto_post_enabled=True, fact_confidence_score=95, quality_score=95, duplicate_score=10,
        safety_status="PASS", daily_post_count=8, max_daily_x_posts=8, opportunity_status="APPROVED"
    ))
    assert decision.allowed is False
    assert "DAILY_LIMIT_REACHED" in decision.reasons


def test_auto_post_disabled_enforcement():
    decision = can_publish(PublishContext(
        auto_post_enabled=False, fact_confidence_score=95, quality_score=95, duplicate_score=10,
        safety_status="PASS", daily_post_count=0, max_daily_x_posts=8, opportunity_status="APPROVED"
    ))
    assert decision.allowed is False
    assert "AUTO_POST_DISABLED" in decision.reasons


def test_stale_market_data_rejection():
    decision = can_publish(PublishContext(
        auto_post_enabled=True, fact_confidence_score=95, quality_score=95, duplicate_score=10,
        safety_status="PASS", daily_post_count=0, max_daily_x_posts=8, opportunity_status="APPROVED", stale_market_data=True
    ))
    assert decision.allowed is False
    assert "MARKET_DATA_STALE" in decision.reasons


def test_market_holiday_detection():
    assert is_nyse_holiday(datetime(2026, 7, 4)) is True
    assert is_trading_day(datetime(2026, 7, 6)) is True
