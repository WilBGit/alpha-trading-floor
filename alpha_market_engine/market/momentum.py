from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .brain_types import MomentumAnalysis, MomentumRegime, decimal_or_none
from .models import IntradayBar


@dataclass(slots=True)
class MomentumRules:
    minimum_bars: int = 5
    accelerating_return_threshold: Decimal = Decimal("0.35")
    steady_return_threshold: Decimal = Decimal("0.10")
    exhaustion_return_threshold: Decimal = Decimal("0.20")
    range_expansion_threshold: Decimal = Decimal("1.20")
    compression_threshold: Decimal = Decimal("0.85")


class MomentumAnalyzer:
    def __init__(self, rules: MomentumRules | None = None) -> None:
        self.rules = rules or MomentumRules()

    def analyze(self, symbol: str, bars: list[IntradayBar], *, session_vwap: Decimal | None = None) -> MomentumAnalysis:
        ordered = sorted(bars, key=lambda item: item.timestamp)
        if len(ordered) < self.rules.minimum_bars:
            return MomentumAnalysis(symbol=symbol, regime=MomentumRegime.INSUFFICIENT_DATA, confidence=0, evidence=["Not enough completed bars."], metrics={})

        closes = [Decimal(str(bar.close)) for bar in ordered]
        returns = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
        last_returns = returns[-3:] if len(returns) >= 3 else returns
        net_change = closes[-1] - closes[0]
        net_pct = (net_change / closes[0]) * Decimal("100") if closes[0] else Decimal("0")
        direction = 1 if net_change > 0 else -1 if net_change < 0 else 0
        consecutive_up = 0
        consecutive_down = 0
        for delta in reversed(returns):
            if delta > 0:
                if consecutive_down:
                    break
                consecutive_up += 1
            elif delta < 0:
                if consecutive_up:
                    break
                consecutive_down += 1
            else:
                break

        avg_recent_range = sum(Decimal(str(bar.high - bar.low)) for bar in ordered[-3:]) / Decimal("3")
        avg_early_range = sum(Decimal(str(bar.high - bar.low)) for bar in ordered[:3]) / Decimal("3")
        range_ratio = (avg_recent_range / avg_early_range) if avg_early_range else Decimal("1")
        distance_from_vwap = None if session_vwap is None else closes[-1] - session_vwap
        distance_pct = None if session_vwap is None or session_vwap == 0 else ((closes[-1] - session_vwap) / session_vwap) * Decimal("100")

        metrics = {
            "net_change": net_change,
            "net_pct": net_pct,
            "consecutive_up": consecutive_up,
            "consecutive_down": consecutive_down,
            "range_ratio": range_ratio,
            "distance_from_vwap": distance_from_vwap,
            "distance_pct": distance_pct,
        }
        evidence: list[str] = [f"Net change {net_pct:.2f}% over {len(ordered)} bars"]
        if distance_pct is not None:
            evidence.append(f"Latest close is {distance_pct:.2f}% from session VWAP")

        avg_prior_abs = sum(abs(value) for value in returns[:-1]) / Decimal(len(returns[:-1])) if len(returns) > 1 else abs(last_returns[-1])
        recent_abs = abs(last_returns[-1]) if last_returns else Decimal("0")
        accelerating_up = direction > 0 and len(last_returns) == 3 and last_returns[0] > 0 and last_returns[1] > last_returns[0] and last_returns[2] > last_returns[1] and range_ratio >= self.rules.range_expansion_threshold
        accelerating_down = direction < 0 and len(last_returns) == 3 and last_returns[0] < 0 and last_returns[1] < last_returns[0] and last_returns[2] < last_returns[1] and range_ratio >= self.rules.range_expansion_threshold
        exhausting_up = direction > 0 and (recent_abs < avg_prior_abs * Decimal("0.5") or range_ratio <= self.rules.compression_threshold) and (returns[-1] <= Decimal("0") or returns[-2] <= Decimal("0"))
        exhausting_down = direction < 0 and (recent_abs < avg_prior_abs * Decimal("0.5") or range_ratio <= self.rules.compression_threshold) and (returns[-1] >= Decimal("0") or returns[-2] >= Decimal("0"))
        steady_up = direction > 0 and net_pct >= self.rules.steady_return_threshold and not accelerating_up and not exhausting_up
        steady_down = direction < 0 and abs(net_pct) >= self.rules.steady_return_threshold and not accelerating_down and not exhausting_down

        if accelerating_up:
            return MomentumAnalysis(symbol=symbol, regime=MomentumRegime.ACCELERATING_UP, confidence=90, evidence=evidence + ["Recent gains are expanding."] , metrics=metrics)
        if accelerating_down:
            return MomentumAnalysis(symbol=symbol, regime=MomentumRegime.ACCELERATING_DOWN, confidence=90, evidence=evidence + ["Recent losses are expanding."], metrics=metrics)
        if exhausting_up:
            return MomentumAnalysis(symbol=symbol, regime=MomentumRegime.EXHAUSTING_UP, confidence=82, evidence=evidence + ["Up move is losing range expansion or decelerating."], metrics=metrics)
        if exhausting_down:
            return MomentumAnalysis(symbol=symbol, regime=MomentumRegime.EXHAUSTING_DOWN, confidence=82, evidence=evidence + ["Down move is losing range expansion or decelerating."], metrics=metrics)
        if steady_up:
            return MomentumAnalysis(symbol=symbol, regime=MomentumRegime.STEADY_UP, confidence=80, evidence=evidence + ["Price is advancing at a steady pace."], metrics=metrics)
        if steady_down:
            return MomentumAnalysis(symbol=symbol, regime=MomentumRegime.STEADY_DOWN, confidence=80, evidence=evidence + ["Price is declining at a steady pace."], metrics=metrics)
        if abs(net_pct) <= self.rules.steady_return_threshold and range_ratio <= self.rules.compression_threshold:
            return MomentumAnalysis(symbol=symbol, regime=MomentumRegime.NEUTRAL, confidence=75, evidence=evidence + ["Price action is balanced and compressed."], metrics=metrics)
        return MomentumAnalysis(symbol=symbol, regime=MomentumRegime.NEUTRAL, confidence=65, evidence=evidence + ["No sustained directional momentum."], metrics=metrics)
