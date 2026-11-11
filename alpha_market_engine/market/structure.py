from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from statistics import mean
from typing import Any

from .brain_types import MarketStructure, StructureAnalysis, SwingPoint, decimal_or_none
from .models import IntradayBar


@dataclass(slots=True)
class StructureRules:
    pivot_window: int = 2
    minimum_swings: int = 2


class MarketStructureAnalyzer:
    def __init__(self, pivot_window: int = 2, rules: StructureRules | None = None) -> None:
        self.rules = rules or StructureRules(pivot_window=pivot_window)
        self.pivot_window = pivot_window

    def _swing_highs(self, symbol: str, bars: list[IntradayBar]) -> list[SwingPoint]:
        swings: list[SwingPoint] = []
        window = self.pivot_window
        for index in range(window, len(bars) - window):
            bar = bars[index]
            if all(bar.high > bars[left].high for left in range(index - window, index)) and all(bar.high >= bars[right].high for right in range(index + 1, index + window + 1)):
                swings.append(SwingPoint(timestamp=bar.timestamp, price=decimal_or_none(bar.high) or Decimal("0"), kind="HIGH", index=index))
        return swings

    def _swing_lows(self, symbol: str, bars: list[IntradayBar]) -> list[SwingPoint]:
        swings: list[SwingPoint] = []
        window = self.pivot_window
        for index in range(window, len(bars) - window):
            bar = bars[index]
            if all(bar.low < bars[left].low for left in range(index - window, index)) and all(bar.low <= bars[right].low for right in range(index + 1, index + window + 1)):
                swings.append(SwingPoint(timestamp=bar.timestamp, price=decimal_or_none(bar.low) or Decimal("0"), kind="LOW", index=index))
        return swings

    def analyze(self, symbol: str, bars: list[IntradayBar]) -> StructureAnalysis:
        ordered = sorted(bars, key=lambda item: item.timestamp)
        if len(ordered) < self.pivot_window * 2 + 1:
            return StructureAnalysis(symbol=symbol, structure=MarketStructure.INSUFFICIENT_DATA, confidence=0, reasoning="Not enough bars for pivot confirmation.")

        swing_highs = self._swing_highs(symbol, ordered)
        swing_lows = self._swing_lows(symbol, ordered)
        evidence: list[str] = []

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            quarter = max(1, len(ordered) // 4)
            first_slice = ordered[:quarter]
            last_slice = ordered[-quarter:]
            if len(ordered) < self.pivot_window * 2 + 1:
                return StructureAnalysis(
                    symbol=symbol,
                    structure=MarketStructure.INSUFFICIENT_DATA,
                    confidence=0,
                    reasoning="Not enough bars for pivot confirmation.",
                )
            swing_highs = [
                SwingPoint(timestamp=first_slice[-1].timestamp, price=decimal_or_none(max(bar.high for bar in first_slice)) or Decimal("0"), kind="HIGH", index=0),
                SwingPoint(timestamp=last_slice[-1].timestamp, price=decimal_or_none(max(bar.high for bar in last_slice)) or Decimal("0"), kind="HIGH", index=len(ordered) - 1),
            ]
            swing_lows = [
                SwingPoint(timestamp=first_slice[-1].timestamp, price=decimal_or_none(min(bar.low for bar in first_slice)) or Decimal("0"), kind="LOW", index=0),
                SwingPoint(timestamp=last_slice[-1].timestamp, price=decimal_or_none(min(bar.low for bar in last_slice)) or Decimal("0"), kind="LOW", index=len(ordered) - 1),
            ]
            evidence = ["Fallback swing points derived from early and late session ranges."]
        else:
            evidence = []

        last_highs = swing_highs[-2:]
        last_lows = swing_lows[-2:]
        higher_highs = last_highs[-1].price > last_highs[0].price
        higher_lows = last_lows[-1].price > last_lows[0].price
        lower_highs = last_highs[-1].price < last_highs[0].price
        lower_lows = last_lows[-1].price < last_lows[0].price

        net_change = ordered[-1].close - ordered[0].close
        positive_moves = sum(1 for index in range(1, len(ordered)) if ordered[index].close > ordered[index - 1].close)
        negative_moves = sum(1 for index in range(1, len(ordered)) if ordered[index].close < ordered[index - 1].close)
        total_move = max(ordered[-1].high, ordered[0].high) - min(ordered[-1].low, ordered[0].low)
        range_ratio = float(total_move / ordered[-1].close) if ordered[-1].close else 0.0

        if positive_moves > 0 and negative_moves > 0 and (range_ratio > 0.008 or abs(net_change) > ordered[0].close * Decimal("0.003")) and not (higher_highs and higher_lows) and not (lower_highs and lower_lows):
            return StructureAnalysis(symbol=symbol, structure=MarketStructure.MIXED, confidence=70, swing_highs=swing_highs, swing_lows=swing_lows, evidence=evidence + ["Directional swings are mixed."], reasoning="Trend components conflict.")

        if abs(net_change) <= ordered[0].close * Decimal("0.002") and range_ratio <= 0.02:
            return StructureAnalysis(
                symbol=symbol,
                structure=MarketStructure.RANGE,
                confidence=82,
                swing_highs=swing_highs,
                swing_lows=swing_lows,
                evidence=evidence + [f"Compact range between {min(bar.low for bar in ordered)} and {max(bar.high for bar in ordered)}"],
                reasoning="Price oscillates within a contained range.",
            )

        if higher_highs and higher_lows and net_change > 0:
            evidence = evidence + [
                f"Higher high sequence: {last_highs[0].price} -> {last_highs[-1].price}",
                f"Higher low sequence: {last_lows[0].price} -> {last_lows[-1].price}",
            ]
            return StructureAnalysis(symbol=symbol, structure=MarketStructure.UPTREND, confidence=90, swing_highs=swing_highs, swing_lows=swing_lows, evidence=evidence, reasoning="Confirmed higher highs and higher lows.")

        if lower_highs and lower_lows and net_change < 0:
            evidence = evidence + [
                f"Lower high sequence: {last_highs[0].price} -> {last_highs[-1].price}",
                f"Lower low sequence: {last_lows[0].price} -> {last_lows[-1].price}",
            ]
            return StructureAnalysis(symbol=symbol, structure=MarketStructure.DOWNTREND, confidence=90, swing_highs=swing_highs, swing_lows=swing_lows, evidence=evidence, reasoning="Confirmed lower highs and lower lows.")

        if positive_moves > 0 and negative_moves > 0:
            return StructureAnalysis(symbol=symbol, structure=MarketStructure.MIXED, confidence=70, swing_highs=swing_highs, swing_lows=swing_lows, evidence=evidence + ["Directional swings are mixed."], reasoning="Trend components conflict.")

        if net_change > 0:
            return StructureAnalysis(symbol=symbol, structure=MarketStructure.UPTREND, confidence=72, swing_highs=swing_highs, swing_lows=swing_lows, evidence=evidence + ["Net price change is positive with supportive swing structure."], reasoning="Price is trending upward.")
        if net_change < 0:
            return StructureAnalysis(symbol=symbol, structure=MarketStructure.DOWNTREND, confidence=72, swing_highs=swing_highs, swing_lows=swing_lows, evidence=evidence + ["Net price change is negative with supportive swing structure."], reasoning="Price is trending downward.")

        return StructureAnalysis(symbol=symbol, structure=MarketStructure.MIXED, confidence=60, swing_highs=swing_highs, swing_lows=swing_lows, evidence=evidence + ["Structure is not clean enough to classify as trend or range."], reasoning="Mixed structure with insufficient directional agreement.")
