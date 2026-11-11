from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .brain_types import DivergenceAnalysis, DivergenceSignal, MarketStructure, MomentumRegime, format_decimal


@dataclass(slots=True)
class DivergenceRules:
    relative_performance_threshold: Decimal = Decimal("0.25")


class IndexDivergenceAnalyzer:
    def __init__(self, relative_performance_threshold: Decimal = Decimal("0.25"), rules: DivergenceRules | None = None) -> None:
        self.rules = rules or DivergenceRules(relative_performance_threshold=relative_performance_threshold)

    def analyze(self, inputs: dict[str, dict[str, Any]]) -> DivergenceAnalysis:
        def optional_change(symbol: str) -> Decimal | None:
            value = inputs.get(symbol, {}).get("change_percent")
            if value is None:
                return None
            try:
                change = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                return None
            return change if change.is_finite() else None

        spy_change = optional_change("SPY")
        qqq = inputs.get("QQQ", {})
        iwm = inputs.get("IWM", {})

        signals: list[DivergenceSignal] = []
        evidence: list[str] = []
        supporting_data: dict[str, Any] = {symbol: dict(values) for symbol, values in inputs.items()}

        def rel(symbol: str) -> Decimal | None:
            change = optional_change(symbol)
            if change is None or spy_change is None:
                return None
            return change - spy_change

        qqq_rel = rel("QQQ")
        iwm_rel = rel("IWM")
        relative_performance_facts: dict[str, dict[str, Any]] = {}
        index_spread_fact: dict[str, Any] | None = None
        for symbol, relative in (("QQQ", qqq_rel), ("IWM", iwm_rel)):
            change = optional_change(symbol)
            if change is not None and spy_change is not None and relative is not None:
                relative_performance_facts[symbol] = {
                    "index_a": symbol,
                    "index_a_change_percent": change,
                    "index_b": "SPY",
                    "index_b_change_percent": spy_change,
                    "signed_difference": relative,
                    "absolute_difference": abs(relative),
                    "threshold": self.rules.relative_performance_threshold,
                    "display_difference": format_decimal(abs(relative)),
                    "rounding": "ROUND_HALF_UP",
                }

        if qqq_rel is not None and qqq_rel <= -self.rules.relative_performance_threshold:
            signals.append(DivergenceSignal.QQQ_UNDERPERFORMING_SPY)
            evidence.append(f"QQQ underperforms SPY by {format_decimal(abs(qqq_rel))} percentage points")
        if qqq_rel is not None and qqq_rel >= self.rules.relative_performance_threshold:
            signals.append(DivergenceSignal.QQQ_OUTPERFORMING_SPY)
            evidence.append(f"QQQ outperforms SPY by {format_decimal(qqq_rel)} percentage points")
        if iwm_rel is not None and iwm_rel <= -self.rules.relative_performance_threshold:
            signals.append(DivergenceSignal.IWM_UNDERPERFORMING_SPY)
            evidence.append(f"IWM underperforms SPY by {format_decimal(abs(iwm_rel))} percentage points")
        if iwm_rel is not None and iwm_rel >= self.rules.relative_performance_threshold:
            signals.append(DivergenceSignal.IWM_OUTPERFORMING_SPY)
            evidence.append(f"IWM outperforms SPY by {format_decimal(iwm_rel)} percentage points")

        complete_changes = {symbol: optional_change(symbol) for symbol in inputs}
        if complete_changes and all(change is not None for change in complete_changes.values()):
            typed_changes = {symbol: change for symbol, change in complete_changes.items() if change is not None}
            top_index = max(typed_changes.items(), key=lambda item: item[1])
            if top_index[0] == "DIA" and spy_change is not None and top_index[1] > spy_change:
                signals.append(DivergenceSignal.DIA_LEADING)
                evidence.append(f"DIA is leading with {format_decimal(top_index[1])}% change")

        if qqq_rel is not None and qqq_rel >= self.rules.relative_performance_threshold and qqq.get("above_vwap", False) and qqq.get("structure") in {MarketStructure.UPTREND, MarketStructure.RANGE}:
            signals.append(DivergenceSignal.TECH_LEADERSHIP)
            evidence.append("QQQ leadership is confirmed by relative performance and VWAP positioning")

        if iwm_rel is not None and iwm_rel >= self.rules.relative_performance_threshold and iwm.get("above_vwap", False):
            signals.append(DivergenceSignal.SMALL_CAP_PARTICIPATION)
            evidence.append("IWM is participating with positive relative performance above VWAP")

        if complete_changes and all(change is not None for change in complete_changes.values()):
            typed_changes = [change for change in complete_changes.values() if change is not None]
            maximum = max(typed_changes)
            minimum = min(typed_changes)
            spread = maximum - minimum
            max_symbol = max(complete_changes, key=lambda symbol: complete_changes[symbol] or Decimal("-Infinity"))
            min_symbol = min(complete_changes, key=lambda symbol: complete_changes[symbol] or Decimal("Infinity"))
            index_spread_fact = {
                "maximum_change_percent": maximum,
                "maximum_symbol": max_symbol,
                "minimum_change_percent": minimum,
                "minimum_symbol": min_symbol,
                "signed_difference": spread,
                "absolute_difference": abs(spread),
                "threshold": self.rules.relative_performance_threshold * Decimal("2"),
                "display_difference": format_decimal(spread),
                "rounding": "ROUND_HALF_UP",
            }
            vwaps = {symbol: bool(values.get("above_vwap", False)) for symbol, values in inputs.items()}
            if spread >= self.rules.relative_performance_threshold * Decimal("2"):
                signals.append(DivergenceSignal.INDEX_DISAGREEMENT)
                evidence.append(f"Index spread is {format_decimal(spread)} percentage points")
            if all(change > 0 for change in typed_changes) and all(vwaps.values()):
                signals.append(DivergenceSignal.BROAD_INDEX_CONFIRMATION)
                evidence.append("All major indices are positive and above VWAP")

        confidence = min(100, 70 + 5 * len(signals))
        if not signals:
            confidence = 50
        return DivergenceAnalysis(
            signals=signals,
            confidence=confidence,
            evidence=evidence,
            supporting_data=supporting_data,
            relative_performance_facts=relative_performance_facts,
            index_spread_fact=index_spread_fact,
        )
