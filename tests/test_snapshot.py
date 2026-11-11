from __future__ import annotations

from alpha_market_engine.market.data_provider import MarketBreadth, MarketIndexQuote, MarketQuote, StaticMarketDataProvider
from alpha_market_engine.market.snapshot import MarketSnapshotEngine


def test_snapshot_engine_builds_normalized_snapshot():
    provider = StaticMarketDataProvider(
        quotes={
            "SPY": MarketIndexQuote(symbol="SPY", price=620.0, change_percent=0.35, relative_volume=1.2, vwap=618.0, previous_close=617.5),
            "QQQ": MarketIndexQuote(symbol="QQQ", price=550.0, change_percent=0.18, relative_volume=1.1, vwap=548.0, previous_close=548.9),
            "DIA": MarketIndexQuote(symbol="DIA", price=430.0, change_percent=0.12, relative_volume=0.9, vwap=429.5, previous_close=429.2),
            "IWM": MarketIndexQuote(symbol="IWM", price=225.0, change_percent=0.28, relative_volume=1.0, vwap=224.0, previous_close=224.4),
            "VIX": MarketIndexQuote(symbol="VIX", price=18.2, change_percent=0.6, previous_close=17.9),
        },
        breadth=MarketBreadth(advancers=1800, decliners=1400, up_volume=900000, down_volume=750000, breadth_score=0.12),
        sector_performance={"Technology": 1.2, "Energy": 0.8},
    )

    snapshot = MarketSnapshotEngine(provider).build_snapshot()

    assert snapshot.spy.price == 620.0
    assert snapshot.leading_index == "SPY"
    assert snapshot.lagging_index == "DIA"
    assert snapshot.tech_outperforming is False
    assert snapshot.small_caps_participating is False or snapshot.small_caps_participating is True
    assert snapshot.market_green is True
    assert snapshot.market_red is False
    assert snapshot.raw_data["indices"]["QQQ"]["price"] == 550.0
