from __future__ import annotations

from alpha_market_engine.content.confidence import compute_fact_confidence
from alpha_market_engine.content.validator import validate_claims
from alpha_market_engine.market.data_provider import MarketBreadth, MarketIndexQuote, StaticMarketDataProvider
from alpha_market_engine.market.snapshot import MarketSnapshotEngine


def _snapshot():
    provider = StaticMarketDataProvider(
        quotes={
            "SPY": MarketIndexQuote(symbol="SPY", price=620.0, change_percent=0.25, relative_volume=1.0, vwap=619.5),
            "QQQ": MarketIndexQuote(symbol="QQQ", price=550.0, change_percent=-0.10, relative_volume=1.0, vwap=548.0),
            "DIA": MarketIndexQuote(symbol="DIA", price=430.0, change_percent=0.12, relative_volume=1.0, vwap=429.5),
            "IWM": MarketIndexQuote(symbol="IWM", price=225.0, change_percent=0.18, relative_volume=1.0, vwap=224.0),
            "VIX": MarketIndexQuote(symbol="VIX", price=18.5, change_percent=0.1),
        },
        breadth=MarketBreadth(advancers=1700, decliners=1500, breadth_score=0.08),
        sector_performance={"Technology": -0.2},
    )
    return MarketSnapshotEngine(provider).build_snapshot()


def test_incorrect_price_claim_rejection():
    payload = {"claims": [{"claim": "$QQQ is down 1.2%.", "type": "FACT"}]}
    results = validate_claims(payload, _snapshot())
    assert results[0].passed is False


def test_incorrect_percentage_claim_rejection():
    payload = {"claims": [{"claim": "$SPY is down 2.0%.", "type": "FACT"}]}
    results = validate_claims(payload, _snapshot())
    assert results[0].passed is False


def test_unsupported_catalyst_rejection():
    payload = {"claims": [{"claim": "$NVDA is falling because the Fed said so.", "type": "FACT"}]}
    results = validate_claims(payload, _snapshot(), catalysts=[])
    assert results[0].passed is True


def test_low_confidence_rejection():
    results = validate_claims({"claims": []}, _snapshot())
    assert compute_fact_confidence(results) == 0
