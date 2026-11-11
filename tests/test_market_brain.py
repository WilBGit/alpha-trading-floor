from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_script_spec = importlib.util.spec_from_file_location("run_market_brain", Path(__file__).parents[1] / "scripts" / "run_market_brain.py")
assert _script_spec is not None and _script_spec.loader is not None
_script_module = importlib.util.module_from_spec(_script_spec)
_script_spec.loader.exec_module(_script_module)
_certification_payload = _script_module._certification_payload
from alpha_market_engine.market.data_provider import FallbackQuoteProvider, StaticMarketDataProvider
from alpha_market_engine.market.models import IntradayBar, MarketQuote
from alpha_market_engine.market.cross_validation import (
    CrossValidationStatus,
    CrossValidationTolerances,
    MarketDataCrossValidator,
)
from alpha_market_engine.market.confidence import AnalysisConfidence, MarketConfidenceCalculator
from alpha_market_engine.market.divergence import DivergenceSignal, IndexDivergenceAnalyzer
from alpha_market_engine.market.observations import ObservationType, MarketObservationEngine
from alpha_market_engine.market.price_action import EventLifecycle, OpeningRangeLevels, PriceActionAnalyzer
from alpha_market_engine.market.regime import MarketRegime, MarketRegimeAnalyzer
from alpha_market_engine.market.structure import MarketStructure, MarketStructureAnalyzer
from alpha_market_engine.market.momentum import MomentumAnalyzer, MomentumRegime
from alpha_market_engine.market.brain import MarketBrainService
from alpha_market_engine.market.source_policy import PHASE4_SOURCE_POLICY


BASE_TIME = datetime(2026, 7, 13, 13, 30, tzinfo=UTC)


def quote(symbol: str, price: str, previous_close: str, *, day_high: str | None = None, day_low: str | None = None, vwap: str | None = None) -> MarketQuote:
    price_dec = Decimal(price)
    prev_dec = Decimal(previous_close)
    return MarketQuote(
        symbol=symbol,
        price=price_dec,
        previous_close=prev_dec,
        change=price_dec - prev_dec,
        change_percent=((price_dec - prev_dec) / prev_dec) * Decimal("100"),
        day_high=Decimal(day_high) if day_high is not None else None,
        day_low=Decimal(day_low) if day_low is not None else None,
        vwap=Decimal(vwap) if vwap is not None else None,
        volume=1_000_000,
        quote_timestamp=BASE_TIME,
        provider="primary",
        validation_metadata={"venue_last_trade_time": BASE_TIME.isoformat().replace("+00:00", "Z")},
    )



def bar(symbol: str, minute: int, open_: str, high: str, low: str, close: str, volume: int, *, vwap: str | None = None) -> IntradayBar:
    timestamp = BASE_TIME + timedelta(minutes=minute)
    return IntradayBar(
        symbol=symbol,
        timestamp=timestamp,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
        provider="primary",
        vwap=Decimal(vwap) if vwap is not None else None,
    )



def rising_bars(symbol: str, closes: list[str], *, start: int = 0, base: str = "100.0") -> list[IntradayBar]:
    bars: list[IntradayBar] = []
    prev = Decimal(base)
    for i, close in enumerate(closes, start=start):
        close_dec = Decimal(close)
        high = max(prev, close_dec) + Decimal("0.20")
        low = min(prev, close_dec) - Decimal("0.15")
        bars.append(bar(symbol, i, str(prev), str(high), str(low), str(close_dec), 1000 + i * 10))
        prev = close_dec
    return bars



def flat_bars(symbol: str, closes: list[str], *, start: int = 0) -> list[IntradayBar]:
    bars: list[IntradayBar] = []
    prev = Decimal(closes[0])
    for i, close in enumerate(closes, start=start):
        close_dec = Decimal(close)
        high = max(prev, close_dec) + Decimal("0.08")
        low = min(prev, close_dec) - Decimal("0.08")
        bars.append(bar(symbol, i, str(prev), str(high), str(low), str(close_dec), 900))
        prev = close_dec
    return bars



def provider_pair(primary_quotes: dict[str, MarketQuote], secondary_quotes: dict[str, MarketQuote], *, primary_bars: dict[str, list[IntradayBar]] | None = None, secondary_bars: dict[str, list[IntradayBar]] | None = None):
    primary = StaticMarketDataProvider(quotes=primary_quotes, bars=primary_bars or {})
    secondary = StaticMarketDataProvider(quotes=secondary_quotes, bars=secondary_bars or {})
    return primary, secondary



@pytest.mark.asyncio
async def test_cross_validation_exact_match():
    primary, secondary = provider_pair({"SPY": quote("SPY", "100.00", "99.00")}, {"SPY": quote("SPY", "100.00", "99.00")})
    validator = MarketDataCrossValidator(primary, secondary, tolerances=CrossValidationTolerances(price_percent=Decimal("0.10")))

    result = await validator.compare_quotes("SPY", "SPY")

    assert result.status == CrossValidationStatus.MATCH
    assert result.absolute_difference == Decimal("0")
    assert result.percentage_difference == Decimal("0")



@pytest.mark.asyncio
async def test_cross_validation_within_tolerance():
    primary, secondary = provider_pair({"SPY": quote("SPY", "100.00", "99.00")}, {"SPY": quote("SPY", "100.05", "99.00")})
    validator = MarketDataCrossValidator(primary, secondary, tolerances=CrossValidationTolerances(price_percent=Decimal("0.10")))

    result = await validator.compare_quotes("SPY", "SPY")

    assert result.status == CrossValidationStatus.WITHIN_TOLERANCE
    assert result.percentage_difference is not None and result.percentage_difference <= Decimal("0.10")



@pytest.mark.asyncio
async def test_cross_validation_divergent():
    primary, secondary = provider_pair({"SPY": quote("SPY", "100.00", "99.00")}, {"SPY": quote("SPY", "101.00", "99.00")})
    validator = MarketDataCrossValidator(primary, secondary, tolerances=CrossValidationTolerances(price_percent=Decimal("0.10")))

    result = await validator.compare_quotes("SPY", "SPY")

    assert result.status == CrossValidationStatus.DIVERGENT
    assert result.percentage_difference is not None and result.percentage_difference > Decimal("0.10")



@pytest.mark.asyncio
async def test_cross_validation_secondary_unavailable():
    primary = StaticMarketDataProvider(quotes={"SPY": quote("SPY", "100.00", "99.00")})
    validator = MarketDataCrossValidator(primary, None)

    result = await validator.compare_quotes("SPY", "SPY")

    assert result.status == CrossValidationStatus.SECONDARY_UNAVAILABLE



def test_analysis_confidence_no_penalties():
    confidence = MarketConfidenceCalculator().calculate(
        core_field_status={"SPY": True, "QQQ": True, "DIA": True, "IWM": True},
        cross_validation_statuses=[CrossValidationStatus.MATCH, CrossValidationStatus.WITHIN_TOLERANCE],
        session_bar_coverage={"SPY": Decimal("100"), "QQQ": Decimal("100")},
        vwap_quality={"SPY": "GOOD", "QQQ": "GOOD"},
    )

    assert confidence.score == 100
    assert confidence.penalties == []



def test_analysis_confidence_applies_divergence_and_missing_data_penalties():
    confidence = MarketConfidenceCalculator().calculate(
        core_field_status={"SPY": True, "QQQ": False, "DIA": True, "IWM": True},
        cross_validation_statuses=[CrossValidationStatus.DIVERGENT],
        session_bar_coverage={"SPY": Decimal("97.4"), "QQQ": Decimal("84.0")},
        vwap_quality={"SPY": "DEGRADED", "QQQ": "GOOD"},
    )

    assert confidence.score < 100
    assert any("diverg" in penalty.reason.lower() for penalty in confidence.penalties)
    assert any("missing" in penalty.reason.lower() for penalty in confidence.penalties)
    assert confidence.warnings



def test_analysis_confidence_is_bounded():
    confidence = MarketConfidenceCalculator().calculate(
        core_field_status={"SPY": False, "QQQ": False, "DIA": False, "IWM": False},
        cross_validation_statuses=[CrossValidationStatus.DIVERGENT] * 10,
        session_bar_coverage={"SPY": Decimal("0"), "QQQ": Decimal("0")},
        vwap_quality={"SPY": "INVALID", "QQQ": "INVALID"},
    )

    assert 0 <= confidence.score <= 100



def test_vwap_reclaim_requires_confirmation_bars():
    bars = flat_bars("SPY", ["100.0"] * 18) + rising_bars("SPY", ["99.8", "100.2"], start=18, base="100.0")
    bars[-1] = bar("SPY", 19, "100.10", "100.25", "99.95", "100.2", 1190)
    analyzer = PriceActionAnalyzer(confirm_bars=3, reclaim_noise_tolerance=Decimal("0.10"))
    ranges = OpeningRangeLevels(or5_high=Decimal("100.0"), or5_low=Decimal("99.0"), or15_high=Decimal("100.0"), or15_low=Decimal("99.0"), or30_high=Decimal("100.0"), or30_low=Decimal("99.0"))

    events = analyzer.analyze(symbol="SPY", bars=bars, current_price=Decimal("100.2"), previous_close=Decimal("99.5"), session_vwap=Decimal("99.9"), ranges=ranges)

    assert not any(event.event_type == "VWAP_RECLAIM" for event in events)



def test_vwap_reclaim_confirms_after_multiple_closes_above_vwap():
    bars = flat_bars("SPY", ["99.8"] * 18) + rising_bars("SPY", ["100.05", "100.15", "100.20"], start=18, base="99.8")
    analyzer = PriceActionAnalyzer(confirm_bars=3, reclaim_noise_tolerance=Decimal("0.05"))
    ranges = OpeningRangeLevels(or5_high=Decimal("100.0"), or5_low=Decimal("99.0"), or15_high=Decimal("100.0"), or15_low=Decimal("99.0"), or30_high=Decimal("100.0"), or30_low=Decimal("99.0"))

    events = analyzer.analyze(symbol="SPY", bars=bars, current_price=Decimal("100.20"), previous_close=Decimal("99.5"), session_vwap=Decimal("99.9"), ranges=ranges)

    reclaim = next(event for event in events if event.event_type == "VWAP_RECLAIM")
    assert reclaim.lifecycle == EventLifecycle.CONFIRMED
    assert any("above session vwap" in fact.lower() for fact in reclaim.evidence)



def test_vwap_rejection_and_loss_detected():
    analyzer = PriceActionAnalyzer(confirm_bars=2, reclaim_noise_tolerance=Decimal("0.05"))
    ranges = OpeningRangeLevels(or5_high=Decimal("100.0"), or5_low=Decimal("99.0"), or15_high=Decimal("100.0"), or15_low=Decimal("99.0"), or30_high=Decimal("100.0"), or30_low=Decimal("99.0"))
    rejection_bars = rising_bars("SPY", ["99.55", "99.62", "99.68", "99.71", "99.73", "99.71", "99.69"], base="99.50")
    loss_bars = rising_bars("SPY", ["100.1", "100.2", "100.15", "100.05", "99.70"], base="99.9")

    rejection_events = analyzer.analyze(symbol="SPY", bars=rejection_bars, current_price=Decimal("99.45"), previous_close=Decimal("99.5"), session_vwap=Decimal("99.75"), ranges=ranges)
    loss_events = analyzer.analyze(symbol="SPY", bars=loss_bars, current_price=Decimal("99.70"), previous_close=Decimal("99.5"), session_vwap=Decimal("100.0"), ranges=ranges)

    assert any(event.event_type == "VWAP_REJECTION" for event in rejection_events)
    assert any(event.event_type == "VWAP_LOSS" for event in loss_events)



def test_opening_range_breakout_and_failed_breakout_are_deduped():
    bars = rising_bars("SPY", ["100.05", "100.10", "100.12", "100.20", "100.30", "100.18", "100.15"], base="99.8")
    analyzer = PriceActionAnalyzer(confirm_bars=2, reclaim_noise_tolerance=Decimal("0.05"))
    ranges = OpeningRangeLevels(or5_high=Decimal("100.10"), or5_low=Decimal("99.0"), or15_high=Decimal("100.10"), or15_low=Decimal("99.0"), or30_high=Decimal("100.10"), or30_low=Decimal("99.0"))

    first = analyzer.analyze(symbol="SPY", bars=bars, current_price=Decimal("100.15"), previous_close=Decimal("99.5"), session_vwap=Decimal("100.0"), ranges=ranges)
    second = analyzer.analyze(symbol="SPY", bars=bars, current_price=Decimal("100.15"), previous_close=Decimal("99.5"), session_vwap=Decimal("100.0"), ranges=ranges, previous_events=first)

    assert sum(1 for event in first if event.event_type == "OR5_BREAKOUT") == 1
    assert second == []



def test_structure_classification():
    uptrend_bars = rising_bars("QQQ", ["100.0", "100.8", "100.4", "101.4", "101.0", "102.0", "101.6", "102.8", "102.4"])
    downtrend_bars = rising_bars("QQQ", ["102.8", "102.0", "102.4", "101.4", "101.8", "100.8", "101.0", "100.0", "100.2"], base="103.0")
    analyzer = MarketStructureAnalyzer(pivot_window=2)

    up = analyzer.analyze("QQQ", uptrend_bars)
    down = analyzer.analyze("QQQ", downtrend_bars)

    assert up.structure == MarketStructure.UPTREND
    assert down.structure == MarketStructure.DOWNTREND



def test_structure_range_mixed_and_insufficient():
    range_bars = flat_bars("SPY", ["100.0", "100.1", "99.9", "100.0", "99.95", "100.05", "100.0", "99.97", "100.03"])
    mixed_bars = flat_bars("SPY", ["100.0", "101.0", "100.2", "101.1", "100.1", "101.2", "100.0", "101.3", "100.2"])
    analyzer = MarketStructureAnalyzer(pivot_window=2)

    assert analyzer.analyze("SPY", range_bars).structure == MarketStructure.RANGE
    assert analyzer.analyze("SPY", mixed_bars).structure == MarketStructure.MIXED
    assert analyzer.analyze("SPY", range_bars[:2]).structure == MarketStructure.INSUFFICIENT_DATA



def test_momentum_classifications():
    analyzer = MomentumAnalyzer()
    accelerating_up = rising_bars("SPY", ["100.0", "100.3", "100.7", "101.2", "101.9", "102.7", "103.6"])
    accelerating_down = rising_bars("SPY", ["103.6", "103.0", "102.5", "101.9", "101.2", "100.4", "99.5"], base="104.0")
    exhausting_up = rising_bars("SPY", ["100.0", "100.5", "100.9", "101.1", "101.2", "101.18", "101.15"])
    exhausting_down = rising_bars("SPY", ["101.5", "101.0", "100.6", "100.4", "100.3", "100.32", "100.35"], base="101.6")
    neutral = flat_bars("SPY", ["100.0", "100.02", "99.99", "100.01", "100.00", "100.02", "100.00"])

    assert analyzer.analyze("SPY", accelerating_up, session_vwap=Decimal("100.2")).regime == MomentumRegime.ACCELERATING_UP
    assert analyzer.analyze("SPY", accelerating_down, session_vwap=Decimal("103.0")).regime == MomentumRegime.ACCELERATING_DOWN
    assert analyzer.analyze("SPY", exhausting_up, session_vwap=Decimal("100.5")).regime == MomentumRegime.EXHAUSTING_UP
    assert analyzer.analyze("SPY", exhausting_down, session_vwap=Decimal("101.0")).regime == MomentumRegime.EXHAUSTING_DOWN
    assert analyzer.analyze("SPY", neutral, session_vwap=Decimal("100.0")).regime == MomentumRegime.NEUTRAL



def test_index_divergence_signals():
    analyzer = IndexDivergenceAnalyzer(relative_performance_threshold=Decimal("0.25"))
    analysis = analyzer.analyze(
        {
            "SPY": {"change_percent": Decimal("-0.25"), "above_vwap": True, "structure": MarketStructure.RANGE, "momentum": MomentumRegime.NEUTRAL},
            "QQQ": {"change_percent": Decimal("-0.82"), "above_vwap": False, "structure": MarketStructure.DOWNTREND, "momentum": MomentumRegime.ACCELERATING_DOWN},
            "DIA": {"change_percent": Decimal("-0.30"), "above_vwap": True, "structure": MarketStructure.MIXED, "momentum": MomentumRegime.NEUTRAL},
            "IWM": {"change_percent": Decimal("0.05"), "above_vwap": False, "structure": MarketStructure.RANGE, "momentum": MomentumRegime.NEUTRAL},
        }
    )

    assert DivergenceSignal.QQQ_UNDERPERFORMING_SPY in analysis.signals
    assert DivergenceSignal.INDEX_DISAGREEMENT in analysis.signals
    assert analysis.evidence



def test_regime_classification():
    analyzer = MarketRegimeAnalyzer()
    risk_on = analyzer.analyze(
        index_changes={"SPY": Decimal("0.40"), "QQQ": Decimal("0.55"), "DIA": Decimal("0.22"), "IWM": Decimal("0.34")},
        above_vwap={"SPY": True, "QQQ": True, "DIA": True, "IWM": True},
        structures={"SPY": MarketStructure.UPTREND, "QQQ": MarketStructure.UPTREND, "DIA": MarketStructure.UPTREND, "IWM": MarketStructure.UPTREND},
        momentum={"SPY": MomentumRegime.ACCELERATING_UP, "QQQ": MomentumRegime.ACCELERATING_UP, "DIA": MomentumRegime.STEADY_UP, "IWM": MomentumRegime.STEADY_UP},
        divergences=[DivergenceSignal.BROAD_INDEX_CONFIRMATION],
        cross_validation_confidence=92,
    )
    risk_off = analyzer.analyze(
        index_changes={"SPY": Decimal("-0.40"), "QQQ": Decimal("-0.55"), "DIA": Decimal("-0.22"), "IWM": Decimal("-0.34")},
        above_vwap={"SPY": False, "QQQ": False, "DIA": False, "IWM": False},
        structures={"SPY": MarketStructure.DOWNTREND, "QQQ": MarketStructure.DOWNTREND, "DIA": MarketStructure.DOWNTREND, "IWM": MarketStructure.DOWNTREND},
        momentum={"SPY": MomentumRegime.ACCELERATING_DOWN, "QQQ": MomentumRegime.ACCELERATING_DOWN, "DIA": MomentumRegime.STEADY_DOWN, "IWM": MomentumRegime.STEADY_DOWN},
        divergences=[],
        cross_validation_confidence=94,
    )
    rotation = analyzer.analyze(
        index_changes={"SPY": Decimal("0.30"), "QQQ": Decimal("-0.35"), "DIA": Decimal("0.15"), "IWM": Decimal("0.08")},
        above_vwap={"SPY": True, "QQQ": False, "DIA": True, "IWM": True},
        structures={"SPY": MarketStructure.RANGE, "QQQ": MarketStructure.DOWNTREND, "DIA": MarketStructure.RANGE, "IWM": MarketStructure.MIXED},
        momentum={"SPY": MomentumRegime.STEADY_UP, "QQQ": MomentumRegime.ACCELERATING_DOWN, "DIA": MomentumRegime.NEUTRAL, "IWM": MomentumRegime.NEUTRAL},
        divergences=[DivergenceSignal.QQQ_UNDERPERFORMING_SPY],
        cross_validation_confidence=90,
    )
    unknown = analyzer.analyze(
        index_changes={"SPY": Decimal("0.01"), "QQQ": Decimal("0.01"), "DIA": Decimal("0.01"), "IWM": Decimal("0.01")},
        above_vwap={"SPY": True, "QQQ": True, "DIA": True, "IWM": True},
        structures={"SPY": MarketStructure.INSUFFICIENT_DATA, "QQQ": MarketStructure.INSUFFICIENT_DATA, "DIA": MarketStructure.INSUFFICIENT_DATA, "IWM": MarketStructure.INSUFFICIENT_DATA},
        momentum={"SPY": MomentumRegime.NEUTRAL, "QQQ": MomentumRegime.NEUTRAL, "DIA": MomentumRegime.NEUTRAL, "IWM": MomentumRegime.NEUTRAL},
        divergences=[],
        cross_validation_confidence=45,
    )

    assert risk_on.regime == MarketRegime.RISK_ON
    assert risk_off.regime == MarketRegime.RISK_OFF
    assert rotation.regime == MarketRegime.ROTATION
    assert analyzer.analyze(
        index_changes={"SPY": Decimal("0.01"), "QQQ": Decimal("0.01"), "DIA": Decimal("0.01"), "IWM": Decimal("0.01")},
        above_vwap={"SPY": True, "QQQ": True, "DIA": True, "IWM": True},
        structures={"SPY": MarketStructure.RANGE, "QQQ": MarketStructure.RANGE, "DIA": MarketStructure.RANGE, "IWM": MarketStructure.RANGE},
        momentum={"SPY": MomentumRegime.NEUTRAL, "QQQ": MomentumRegime.NEUTRAL, "DIA": MomentumRegime.NEUTRAL, "IWM": MomentumRegime.NEUTRAL},
        divergences=[],
        cross_validation_confidence=45,
    ).regime == MarketRegime.UNKNOWN
    assert unknown.confidence < 60



def test_observations_include_boundary_and_scoring():
    observation = MarketObservationEngine().build([
        {"signal": DivergenceSignal.QQQ_UNDERPERFORMING_SPY, "confidence": 94, "importance": 82, "symbols": ["QQQ", "SPY"], "facts": ["QQQ is down 0.82%", "SPY is down 0.25%", "QQQ is below session VWAP", "SPY is above session VWAP"]},
    ])

    assert observation[0].observation_type == ObservationType.QQQ_WEAKER_THAN_SPY
    assert observation[0].confidence_score == 94
    assert observation[0].importance_score == 82
    assert "does not independently prove sector rotation" in observation[0].interpretation_boundary.lower()



@pytest.mark.asyncio
async def test_market_brain_service_end_to_end():
    primary = StaticMarketDataProvider(
        quotes={
            "SPY": quote("SPY", "100.00", "99.50", day_high="100.50", day_low="99.10", vwap="99.80"),
            "QQQ": quote("QQQ", "99.20", "99.60", day_high="100.10", day_low="98.90", vwap="99.05"),
            "DIA": quote("DIA", "100.15", "99.90", day_high="100.25", day_low="99.80", vwap="99.95"),
            "IWM": quote("IWM", "100.05", "99.95", day_high="100.30", day_low="99.70", vwap="99.90"),
        },
        bars={
            "SPY": rising_bars("SPY", ["99.70", "99.80", "99.95", "100.05", "100.12", "100.20", "100.28", "100.35", "100.42", "100.50"]),
            "QQQ": rising_bars("QQQ", ["99.60", "99.55", "99.40", "99.30", "99.25", "99.20", "99.18", "99.15", "99.12", "99.10"]),
            "DIA": rising_bars("DIA", ["99.90", "99.98", "100.02", "100.05", "100.08", "100.10", "100.12", "100.14", "100.16", "100.18"]),
            "IWM": rising_bars("IWM", ["99.88", "99.92", "99.95", "100.00", "100.02", "100.05", "100.06", "100.08", "100.10", "100.12"]),
        },
    )
    secondary = StaticMarketDataProvider(
        quotes={
            "SPY": quote("SPY", "100.02", "99.50", day_high="100.52", day_low="99.10", vwap="99.82"),
            "QQQ": quote("QQQ", "99.18", "99.60", day_high="100.05", day_low="98.90", vwap="99.03"),
            "DIA": quote("DIA", "100.16", "99.90", day_high="100.28", day_low="99.80", vwap="99.96"),
            "IWM": quote("IWM", "100.04", "99.95", day_high="100.31", day_low="99.70", vwap="99.91"),
        },
        bars={
            "SPY": rising_bars("SPY", ["99.70", "99.81", "99.96", "100.07", "100.15", "100.22", "100.30", "100.38", "100.44", "100.52"]),
            "QQQ": rising_bars("QQQ", ["99.60", "99.56", "99.43", "99.32", "99.26", "99.18", "99.15", "99.12", "99.10", "99.08"]),
            "DIA": rising_bars("DIA", ["99.90", "99.99", "100.03", "100.06", "100.09", "100.11", "100.13", "100.15", "100.17", "100.19"]),
            "IWM": rising_bars("IWM", ["99.88", "99.93", "99.96", "100.01", "100.03", "100.06", "100.07", "100.09", "100.11", "100.13"]),
        },
    )
    service = MarketBrainService(primary_provider=primary, secondary_provider=secondary)

    result = await service.analyze_market()

    assert result.snapshot.spy is not None
    assert result.cross_validation
    assert result.analysis_confidence.score <= 100
    assert result.price_action
    assert result.structures
    assert result.momentum
    assert result.divergences
    assert result.regime
    assert result.observations


@pytest.mark.asyncio
async def test_market_brain_falls_back_when_primary_bars_fail():
    class FlakyProvider(StaticMarketDataProvider):
        def __init__(self, *, fail_bars_for: set[str], **kwargs):
            super().__init__(**kwargs)
            self.fail_bars_for = fail_bars_for
            self._bar_calls: dict[str, int] = {}

        async def get_intraday_bars(self, symbol: str, timeframe: str, limit: int | None = None):
            self._bar_calls[symbol] = self._bar_calls.get(symbol, 0) + 1
            if symbol in self.fail_bars_for and self._bar_calls[symbol] > 1:
                raise RuntimeError(f"primary bars unavailable for {symbol}")
            return await super().get_intraday_bars(symbol, timeframe, limit)

    primary = FlakyProvider(
        fail_bars_for={"SPY"},
        quotes={
            "SPY": quote("SPY", "100.00", "99.00", day_high="100.20", day_low="99.50", vwap="99.80"),
            "QQQ": quote("QQQ", "99.20", "99.60", day_high="100.10", day_low="98.90", vwap="99.05"),
            "DIA": quote("DIA", "100.15", "99.90", day_high="100.25", day_low="99.80", vwap="99.95"),
            "IWM": quote("IWM", "100.05", "99.95", day_high="100.30", day_low="99.70", vwap="99.90"),
        },
        bars={
            "SPY": rising_bars("SPY", ["99.70", "99.80", "99.90", "100.00", "100.10"]),
            "QQQ": rising_bars("QQQ", ["99.60", "99.55", "99.40", "99.30", "99.25"]),
            "DIA": rising_bars("DIA", ["99.90", "99.98", "100.02", "100.05", "100.08"]),
            "IWM": rising_bars("IWM", ["99.88", "99.92", "99.95", "100.00", "100.02"]),
        },
    )
    secondary = StaticMarketDataProvider(
        quotes={
            "SPY": quote("SPY", "100.02", "99.00", day_high="100.25", day_low="99.50", vwap="99.82"),
            "QQQ": quote("QQQ", "99.18", "99.60", day_high="100.05", day_low="98.90", vwap="99.03"),
            "DIA": quote("DIA", "100.16", "99.90", day_high="100.28", day_low="99.80", vwap="99.96"),
            "IWM": quote("IWM", "100.04", "99.95", day_high="100.31", day_low="99.70", vwap="99.91"),
        },
        bars={
            "SPY": rising_bars("SPY", ["99.70", "99.81", "99.92", "100.03", "100.14"]),
            "QQQ": rising_bars("QQQ", ["99.60", "99.56", "99.43", "99.32", "99.26"]),
            "DIA": rising_bars("DIA", ["99.90", "99.99", "100.03", "100.06", "100.09"]),
            "IWM": rising_bars("IWM", ["99.88", "99.93", "99.96", "100.01", "100.03"]),
        },
    )
    service = MarketBrainService(primary_provider=primary, secondary_provider=secondary)

    result = await service.analyze_market()

    assert result.price_action["SPY"].session_vwap is not None
    assert result.cross_validation["SPY.latest_completed_1m_bar_close"].status == CrossValidationStatus.WITHIN_TOLERANCE


@pytest.mark.asyncio
async def test_market_brain_safely_handles_unavailable_change_percent():
    missing_percent_quotes = {
        "DIA": MarketQuote(
            symbol="DIA",
            price=Decimal("100"),
            previous_close=Decimal("99"),
            change=Decimal("1"),
            change_percent=Decimal("1.0101010101"),
            volume=1000,
            quote_timestamp=BASE_TIME,
            provider="primary",
        ),
        "IWM": MarketQuote(symbol="IWM", price=Decimal("100"), volume=1000, quote_timestamp=BASE_TIME, provider="primary"),
    }
    primary_quotes = {
        "SPY": quote("SPY", "100.00", "99.00"),
        "QQQ": quote("QQQ", "101.00", "99.00"),
        **missing_percent_quotes,
    }
    bars = {symbol: rising_bars(symbol, ["100.1", "100.2", "100.3", "100.4", "100.5"]) for symbol in primary_quotes}
    primary, secondary = provider_pair(primary_quotes, primary_quotes, primary_bars=bars, secondary_bars=bars)

    result = await MarketBrainService(primary, secondary=secondary, symbols=list(primary_quotes)).analyze_market()

    assert result.analysis_confidence.score <= 100
    assert all("IWM" not in signal.value for signal in result.divergences.signals)
    assert DivergenceSignal.INDEX_DISAGREEMENT not in result.divergences.signals
    assert result.analysis_id
    assert result.analysis_input.analysis_id == result.analysis_id
    assert result.analysis_input.symbols[0].current_price == Decimal("100.00")


def test_relative_performance_fact_has_one_decimal_source_and_display():
    analysis = IndexDivergenceAnalyzer().analyze(
        {
            "QQQ": {"change_percent": Decimal("1.247014359176103633349256751"), "above_vwap": True, "structure": MarketStructure.RANGE},
            "SPY": {"change_percent": Decimal("0.4017779676174966963439539757"), "above_vwap": True, "structure": MarketStructure.RANGE},
            "DIA": {"change_percent": Decimal("0.10"), "above_vwap": True, "structure": MarketStructure.RANGE},
            "IWM": {"change_percent": Decimal("0.20"), "above_vwap": True, "structure": MarketStructure.RANGE},
        }
    )
    fact = analysis.relative_performance_facts["QQQ"]
    assert fact["signed_difference"] == Decimal("0.8452363915586069370053027753")
    assert fact["display_difference"] == "0.85"
    assert "0.85" in next(item for item in analysis.evidence if "QQQ outperforms" in item)
    assert analysis.index_spread_fact is not None
    assert analysis.index_spread_fact["display_difference"] == "1.15"


def test_opening_range_event_references_range_fact_metadata():
    analyzer = PriceActionAnalyzer(confirm_bars=3)
    ranges = OpeningRangeLevels(
        or5_high=Decimal("100.00"),
        or5_low=Decimal("99.00"),
        metadata={"OR5": {"expected_bar_count": 5, "actual_bar_count": 5, "coverage_status": "COMPLETE", "end_boundary": "EXCLUSIVE"}},
    )
    bars = rising_bars("SPY", ["99.50", "99.60", "99.70", "99.80", "99.90", "100.10", "100.20", "100.30"])
    events = analyzer.analyze(symbol="SPY", bars=bars, current_price=Decimal("100.30"), previous_close=Decimal("99.00"), session_vwap=None, ranges=ranges)
    event = next(item for item in events if item.event_type.value == "OR5_BREAKOUT")
    assert event.supporting_values["range_high"] == "100.00"
    assert event.supporting_values["opening_range"]["expected_bar_count"] == 5
    assert event.supporting_values["opening_range"]["coverage_status"] == "COMPLETE"


@pytest.mark.asyncio
async def test_cross_validation_missing_primary_quote_is_not_comparable():
    primary = StaticMarketDataProvider(quotes={}, bars={"SPY": rising_bars("SPY", ["100.1", "100.2", "100.3"])})
    secondary = StaticMarketDataProvider(quotes={"SPY": quote("SPY", "100.00", "99.00")}, bars={"SPY": rising_bars("SPY", ["100.1", "100.2", "100.3"])})
    validator = MarketDataCrossValidator(primary, secondary)

    result = await validator.validate_symbol("SPY")

    assert result["current_price"].status == CrossValidationStatus.NOT_COMPARABLE
    assert result["current_price"].primary_value is None


def test_unknown_regime_has_complete_certification_provenance():
    analyzer = MarketRegimeAnalyzer()
    structures = {
        "SPY": MarketStructure.INSUFFICIENT_DATA,
        "QQQ": MarketStructure.INSUFFICIENT_DATA,
        "DIA": MarketStructure.INSUFFICIENT_DATA,
        "IWM": MarketStructure.RANGE,
    }
    momentum = {symbol: MomentumRegime.NEUTRAL for symbol in structures}
    regime = analyzer.analyze(
        index_data={symbol: {"change_percent": Decimal("0.10"), "above_vwap": True, "below_vwap": False} for symbol in structures},
        structures=structures,
        momentum=momentum,
        divergences=[],
        cross_validation_confidence=90,
    )
    assert regime.regime == MarketRegime.UNKNOWN
    support = regime.supporting_data
    assert set(regime.scores) == {"RISK_ON", "RISK_OFF", "ROTATION", "TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOLATILITY", "LOW_VOLATILITY", "MIXED", "UNKNOWN"}
    assert {"winning_score", "second_highest_regime", "second_highest_score", "score_gap", "raw_confidence", "analysis_data_confidence", "missing_evidence", "confidence_penalties", "data_confidence_cap_applied"} <= set(support)
    fake_result = SimpleNamespace(
        analysis_id="unknown-test",
        analysis_started_at=datetime.now(UTC),
        source_captured_at=datetime.now(UTC),
        analysis_session_date="2026-07-14",
        analysis_confidence=SimpleNamespace(),
        analysis_input=SimpleNamespace(),
        cross_validation={},
        price_action={},
        structures={},
        momentum={},
        divergences=SimpleNamespace(),
        regime=regime,
        observations=[],
    )
    payload = _certification_payload(fake_result, persistence={"status": "DISABLED", "reason": "test"})
    serialized = payload["regime"]
    required = {"all_regime_scores", "winning_regime", "winning_score", "second_highest_regime", "second_highest_score", "score_gap", "raw_regime_confidence", "analysis_data_confidence", "final_regime_confidence", "missing_structure_count", "missing_momentum_count", "missing_vwap_count", "confidence_penalties", "data_confidence_cap_applied", "evidence", "warnings"}
    assert required <= set(serialized)


@pytest.mark.asyncio
async def test_phase4_source_policy_uses_robinhood_quotes_and_alpaca_bars():
    symbols = ("SPY", "QQQ", "DIA", "IWM")
    primary = StaticMarketDataProvider(
        quotes={symbol: quote(symbol, "100.00", "99.00", vwap="99.80") for symbol in symbols},
        bars={symbol: rising_bars(symbol, ["99.70", "99.80", "99.90", "100.00", "100.10"]) for symbol in symbols},
    )
    primary.provider_name = "robinhood"
    secondary = StaticMarketDataProvider(
        quotes={symbol: quote(symbol, "100.01", "99.00", vwap="99.81") for symbol in symbols},
        bars={symbol: rising_bars(symbol, ["99.70", "99.80", "99.90", "100.00", "100.10"]) for symbol in symbols},
    )
    secondary.provider_name = "alpaca"

    result = await MarketBrainService(primary_provider=primary, secondary_provider=secondary).analyze_market()
    inputs = {item.symbol: item for item in result.analysis_input.symbols}

    assert PHASE4_SOURCE_POLICY.quote_preferred == "robinhood"
    assert PHASE4_SOURCE_POLICY.bar_preferred == "alpaca"
    assert inputs["SPY"].requested_primary_provider == "robinhood"
    assert inputs["SPY"].active_quote_provider == "robinhood"
    assert inputs["SPY"].requested_bar_provider == "alpaca"
    assert inputs["SPY"].active_bar_provider == "alpaca"
    assert inputs["SPY"].bar_fallback_used is False
    assert inputs["SPY"].previous_close_provider == "robinhood"
    assert result.analysis_input.provider_metadata["data_type_source_policy"]["bar_preferred"] == "alpaca"


@pytest.mark.asyncio
async def test_market_brain_uses_alpaca_quote_fallback_for_one_missing_primary_symbol():
    primary_quotes = {symbol: quote(symbol, "100.00", "99.00") for symbol in ("QQQ", "DIA", "IWM")}
    secondary_quotes = {symbol: quote(symbol, "100.00", "99.00") for symbol in ("SPY", "QQQ", "DIA", "IWM")}
    bars = {symbol: rising_bars(symbol, ["100", "100.1", "100.2", "100.3", "100.4"]) for symbol in ("SPY", "QQQ", "DIA", "IWM")}
    primary, secondary = provider_pair(primary_quotes, secondary_quotes, primary_bars=bars, secondary_bars=bars)
    primary.provider_name = "robinhood"
    secondary.provider_name = "alpaca"
    result = await MarketBrainService(primary, secondary=secondary).analyze_market()
    captured = {item.symbol: item for item in result.analysis_input.symbols}
    assert captured["SPY"].active_quote_provider == "alpaca"
    assert captured["SPY"].fallback_used is True
    assert captured["SPY"].requested_primary_provider == "robinhood"
    assert captured["SPY"].primary_failure_reason
    assert result.analysis_id


@pytest.mark.asyncio
async def test_market_brain_keeps_one_symbol_unavailable_without_crashing():
    primary_quotes = {symbol: quote(symbol, "100.00", "99.00") for symbol in ("QQQ", "DIA", "IWM")}
    secondary_quotes = {symbol: quote(symbol, "100.00", "99.00") for symbol in ("QQQ", "DIA", "IWM")}
    bars = {symbol: rising_bars(symbol, ["100", "100.1", "100.2", "100.3", "100.4"]) for symbol in ("QQQ", "DIA", "IWM")}
    primary, secondary = provider_pair(primary_quotes, secondary_quotes, primary_bars=bars, secondary_bars=bars)
    primary.provider_name = "robinhood"
    secondary.provider_name = "alpaca"
    result = await MarketBrainService(primary, secondary=secondary).analyze_market()
    captured = {item.symbol: item for item in result.analysis_input.symbols}
    assert captured["SPY"].current_price is None
    assert captured["SPY"].active_quote_provider == "UNAVAILABLE"
    assert captured["SPY"].fallback_used is False
    assert result.regime.regime in {MarketRegime.UNKNOWN, MarketRegime.MIXED}


@pytest.mark.asyncio
async def test_market_brain_all_core_quotes_unavailable_returns_degraded_result():
    primary = StaticMarketDataProvider(quotes={})
    secondary = StaticMarketDataProvider(quotes={})
    primary.provider_name = "robinhood"
    secondary.provider_name = "alpaca"
    result = await MarketBrainService(primary, secondary=secondary).analyze_market()
    assert result.analysis_id
    assert all(item.current_price is None for item in result.analysis_input.symbols)
    assert result.regime.regime == MarketRegime.UNKNOWN
