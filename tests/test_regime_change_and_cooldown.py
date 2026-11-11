from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alpha_market_engine.content.decision import PublishContext, can_publish
from alpha_market_engine.market.data_provider import MarketBreadth, MarketIndexQuote, StaticMarketDataProvider
from alpha_market_engine.market.regime import MarketRegimeDetector
from alpha_market_engine.market.snapshot import MarketSnapshotEngine


def test_regime_change_detection():
    prev_provider = StaticMarketDataProvider(
        quotes={
            "SPY": MarketIndexQuote(symbol="SPY", price=620.0, change_percent=0.3),
            "QQQ": MarketIndexQuote(symbol="QQQ", price=550.0, change_percent=0.25),
            "DIA": MarketIndexQuote(symbol="DIA", price=430.0, change_percent=0.1),
            "IWM": MarketIndexQuote(symbol="IWM", price=225.0, change_percent=0.2),
            "VIX": MarketIndexQuote(symbol="VIX", price=18.0, change_percent=0.1),
        },
        breadth=MarketBreadth(advancers=1700, decliners=1500, breadth_score=0.08),
    )
    current_provider = StaticMarketDataProvider(
        quotes={
            "SPY": MarketIndexQuote(symbol="SPY", price=620.0, change_percent=-0.5),
            "QQQ": MarketIndexQuote(symbol="QQQ", price=540.0, change_percent=-0.8),
            "DIA": MarketIndexQuote(symbol="DIA", price=429.0, change_percent=-0.4),
            "IWM": MarketIndexQuote(symbol="IWM", price=223.0, change_percent=-0.6),
            "VIX": MarketIndexQuote(symbol="VIX", price=27.0, change_percent=1.0),
        },
        breadth=MarketBreadth(advancers=900, decliners=2300, breadth_score=-0.3),
    )
    detector = MarketRegimeDetector()
    prev = detector.detect(MarketSnapshotEngine(prev_provider).build_snapshot())
    curr = detector.detect(MarketSnapshotEngine(current_provider).build_snapshot())
    assert prev.regime != curr.regime


def test_same_event_cooldown_protection():
    decision = can_publish(PublishContext(
        auto_post_enabled=True, fact_confidence_score=95, quality_score=95, duplicate_score=10,
        safety_status="PASS", daily_post_count=0, max_daily_x_posts=8, opportunity_status="APPROVED",
        cooldown_active=True, now=datetime.now(timezone.utc),
    ))
    assert decision.allowed is False
    assert "EVENT_COOLDOWN_ACTIVE" in decision.reasons
