from __future__ import annotations

from alpha_market_engine.market.data_provider import MarketBreadth, MarketIndexQuote, StaticMarketDataProvider
from alpha_market_engine.market.brain_types import MarketStructure, MomentumRegime
from alpha_market_engine.market.divergence import IndexDivergenceAnalyzer
from alpha_market_engine.market.regime import MarketRegimeDetector, MarketRegimeAnalyzer
from alpha_market_engine.market.snapshot import MarketSnapshotEngine


def test_regime_detector_flags_rotation_with_cross_market_divergence():
    provider = StaticMarketDataProvider(
        quotes={
            "SPY": MarketIndexQuote(symbol="SPY", price=620.0, change_percent=0.32, relative_volume=1.0, vwap=619.5),
            "QQQ": MarketIndexQuote(symbol="QQQ", price=546.0, change_percent=-0.52, relative_volume=1.0, vwap=548.0),
            "DIA": MarketIndexQuote(symbol="DIA", price=429.0, change_percent=0.10, relative_volume=0.8, vwap=428.9),
            "IWM": MarketIndexQuote(symbol="IWM", price=225.0, change_percent=0.08, relative_volume=0.9, vwap=224.8),
            "VIX": MarketIndexQuote(symbol="VIX", price=18.0, change_percent=-0.1),
        },
        breadth=MarketBreadth(advancers=1600, decliners=1500, breadth_score=0.04),
        sector_performance={"Energy": 1.0, "Technology": -0.4},
    )
    snapshot = MarketSnapshotEngine(provider).build_snapshot()
    decision = MarketRegimeDetector().detect(snapshot)

    assert decision.regime == "ROTATION"
    assert decision.confidence >= 80
    assert "rotation" in decision.reasoning.lower()


def test_regime_detector_flags_high_volatility_when_vix_spikes():
    provider = StaticMarketDataProvider(
        quotes={
            "SPY": MarketIndexQuote(symbol="SPY", price=615.0, change_percent=-0.2, relative_volume=0.9, vwap=616.0),
            "QQQ": MarketIndexQuote(symbol="QQQ", price=544.0, change_percent=-0.6, relative_volume=0.8, vwap=547.0),
            "DIA": MarketIndexQuote(symbol="DIA", price=427.0, change_percent=-0.3, relative_volume=0.8, vwap=428.0),
            "IWM": MarketIndexQuote(symbol="IWM", price=223.0, change_percent=-0.8, relative_volume=0.7, vwap=224.0),
            "VIX": MarketIndexQuote(symbol="VIX", price=28.0, change_percent=1.2),
        },
        breadth=MarketBreadth(advancers=900, decliners=2300, breadth_score=-0.36),
        sector_performance={"Technology": -1.2, "Energy": 0.4},
    )
    snapshot = MarketSnapshotEngine(provider).build_snapshot()
    decision = MarketRegimeDetector().detect(snapshot)

    assert decision.regime == "HIGH_VOLATILITY"
    assert decision.confidence >= 90


def test_rotation_confidence_is_capped_by_degraded_data_confidence():
    index_data = {
        "SPY": {"change_percent": 0.50, "above_vwap": True},
        "QQQ": {"change_percent": -0.50, "above_vwap": False},
        "DIA": {"change_percent": 0.10, "above_vwap": True},
        "IWM": {"change_percent": -0.20, "above_vwap": False},
    }
    structures = {
        "SPY": MarketStructure.UPTREND,
        "QQQ": MarketStructure.DOWNTREND,
        "DIA": MarketStructure.UPTREND,
        "IWM": MarketStructure.DOWNTREND,
    }
    momentum = {
        "SPY": MomentumRegime.STEADY_UP,
        "QQQ": MomentumRegime.STEADY_DOWN,
        "DIA": MomentumRegime.STEADY_UP,
        "IWM": MomentumRegime.STEADY_DOWN,
    }
    decision = MarketRegimeAnalyzer().analyze(
        index_data=index_data,
        structures=structures,
        momentum=momentum,
        divergences=IndexDivergenceAnalyzer().analyze(index_data),
        cross_validation_confidence=60,
    )

    assert decision.regime.value == "ROTATION"
    assert decision.scores["ROTATION"] == 80
    assert decision.confidence == 60
    assert "cross-validation confidence is degraded" in " ".join(decision.warnings).lower()


def test_zero_bar_fields_cannot_emit_high_confidence_rotation():
    index_data = {
        "SPY": {"change_percent": 0.50},
        "QQQ": {"change_percent": -0.50},
        "DIA": {"change_percent": 0.10},
        "IWM": {"change_percent": -0.20},
    }
    decision = MarketRegimeAnalyzer().analyze(
        index_data=index_data,
        structures={},
        momentum={},
        divergences=IndexDivergenceAnalyzer().analyze(index_data),
        cross_validation_confidence=80,
    )

    assert decision.regime.value == "UNKNOWN"
    assert decision.confidence < 80


def test_rotation_requires_divergence_signal():
    index_data = {
        "SPY": {"change_percent": 1.00, "above_vwap": True},
        "QQQ": {"change_percent": 1.05, "above_vwap": True},
        "DIA": {"change_percent": 1.02, "above_vwap": True},
        "IWM": {"change_percent": 0.98, "above_vwap": True},
    }
    structures = {symbol: MarketStructure.UPTREND for symbol in index_data}
    momentum = {symbol: MomentumRegime.STEADY_UP for symbol in index_data}
    decision = MarketRegimeAnalyzer().analyze(
        index_data=index_data,
        structures=structures,
        momentum=momentum,
        divergences=IndexDivergenceAnalyzer().analyze(index_data),
        cross_validation_confidence=95,
    )

    assert decision.regime.value != "ROTATION"
    assert decision.scores["ROTATION"] == 0
