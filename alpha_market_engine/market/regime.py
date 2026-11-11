from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .brain_types import DivergenceAnalysis, DivergenceSignal, MarketRegime, MarketRegimeAnalysis, MarketStructure, MomentumAnalysis, MomentumRegime, clamp_score


@dataclass(slots=True)
class RegimeRules:
    confidence_threshold: int = 55
    broad_confirmation_threshold: Decimal = Decimal("0.15")
    rotation_divergence_threshold: Decimal = Decimal("0.25")
    flat_change_threshold: Decimal = Decimal("0.10")


class MarketRegimeAnalyzer:
    """Deterministic market regime scoring.

    Scoring model summary:
    - RISK_ON / RISK_OFF require broad directional confirmation across SPY, QQQ, DIA, and IWM.
    - ROTATION requires meaningful cross-index divergence plus mixed VWAP/structure evidence.
    - TREND_UP / TREND_DOWN use directional structures and momentum.
    - RANGE uses compressed changes and range structures.
    - MIXED is a fallback when evidence conflicts.
    - UNKNOWN is returned when confidence is below threshold.
    """

    def __init__(self, rules: RegimeRules | None = None) -> None:
        self.rules = rules or RegimeRules()

    def _pct(self, value: Any) -> Decimal:
        return Decimal(str(value or 0))

    def _normalize_inputs(
        self,
        *,
        index_data: dict[str, dict[str, Any]] | None = None,
        index_changes: dict[str, Decimal | float | int] | None = None,
        above_vwap: dict[str, bool] | None = None,
        structures: dict[str, Any] | None = None,
        momentum: dict[str, Any] | None = None,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, Any]]:
        if index_data is None:
            index_data = {}
            for symbol in {"SPY", "QQQ", "DIA", "IWM"}:
                index_data[symbol] = {
                    "change_percent": (index_changes or {}).get(symbol, Decimal("0")),
                    "above_vwap": bool((above_vwap or {}).get(symbol, False)),
                    "below_vwap": not bool((above_vwap or {}).get(symbol, False)),
                }
        structures = structures or {}
        momentum = momentum or {}
        return index_data, structures, momentum

    def detect(self, snapshot: Any) -> MarketRegimeAnalysis:
        index_data: dict[str, dict[str, Any]] = {}
        above_vwap: dict[str, bool] = {}
        structures: dict[str, Any] = {}
        momentum: dict[str, Any] = {}
        for symbol in ("SPY", "QQQ", "DIA", "IWM"):
            item = getattr(snapshot, symbol.lower(), None)
            if item is None:
                continue
            change_percent = self._pct(getattr(item, "change_percent", 0))
            session_vwap = getattr(item, "session_vwap", None) or getattr(item, "vwap", None)
            above_flag = getattr(item, "above_vwap", None)
            if above_flag is None and session_vwap is not None:
                try:
                    above_flag = getattr(item, "price", 0) >= session_vwap
                except TypeError:
                    above_flag = False
            above_vwap[symbol] = bool(above_flag)
            index_data[symbol] = {
                "change_percent": change_percent,
                "above_vwap": above_vwap[symbol],
                "below_vwap": not above_vwap[symbol],
                "price": getattr(item, "price", None),
                "vwap": session_vwap,
            }
            if change_percent > 0:
                structures[symbol] = MarketStructure.UPTREND
                momentum[symbol] = MomentumRegime.STEADY_UP
            elif change_percent < 0:
                structures[symbol] = MarketStructure.DOWNTREND
                momentum[symbol] = MomentumRegime.STEADY_DOWN
            else:
                structures[symbol] = MarketStructure.RANGE
                momentum[symbol] = MomentumRegime.NEUTRAL

        vix = getattr(snapshot, "vix", None)
        breadth = getattr(snapshot, "market_breadth", None)
        vix_price = self._pct(getattr(vix, "price", 0))
        if vix is not None and vix_price >= Decimal("25"):
            return MarketRegimeAnalysis(
                regime=MarketRegime.HIGH_VOLATILITY,
                confidence=95,
                scores={"HIGH_VOLATILITY": 100},
                evidence=["VIX is elevated and volatility is expanding."],
                warnings=[],
                supporting_data={"snapshot_time": getattr(snapshot, "snapshot_time", None), "market_breadth": getattr(breadth, "breadth_score", None)},
            )

        divergences: list[DivergenceSignal] = []
        spy_change = self._pct(index_data.get("SPY", {}).get("change_percent", 0))
        qqq_change = self._pct(index_data.get("QQQ", {}).get("change_percent", 0))
        if qqq_change <= spy_change - self.rules.rotation_divergence_threshold:
            divergences.append(DivergenceSignal.QQQ_UNDERPERFORMING_SPY)
        elif spy_change <= qqq_change - self.rules.rotation_divergence_threshold:
            divergences.append(DivergenceSignal.QQQ_OUTPERFORMING_SPY)

        if divergences and ((spy_change > 0 > qqq_change) or (spy_change < 0 < qqq_change)):
            return MarketRegimeAnalysis(
                regime=MarketRegime.ROTATION,
                confidence=90,
                scores={"ROTATION": 100},
                evidence=["Cross-index relative performance is materially divergent"],
                warnings=[],
                supporting_data={"index_data": index_data, "cross_validation_confidence": 95 if len(index_data) >= 4 and breadth is not None else 80},
            )

        cross_validation_confidence = 95 if len(index_data) >= 4 and breadth is not None else 80
        return self.analyze(
            index_data=index_data,
            above_vwap=above_vwap,
            structures=structures,
            momentum=momentum,
            divergences=divergences,
            cross_validation_confidence=cross_validation_confidence,
        )

    def analyze(
        self,
        *,
        index_data: dict[str, dict[str, Any]] | None = None,
        index_changes: dict[str, Decimal | float | int] | None = None,
        above_vwap: dict[str, bool] | None = None,
        structures: dict[str, Any] | None = None,
        momentum: dict[str, Any] | None = None,
        divergences: DivergenceAnalysis | list[DivergenceSignal] | None,
        cross_validation_confidence: int,
    ) -> MarketRegimeAnalysis:
        index_data, structures, momentum = self._normalize_inputs(
            index_data=index_data,
            index_changes=index_changes,
            above_vwap=above_vwap,
            structures=structures,
            momentum=momentum,
        )

        spy = index_data.get("SPY", {})
        qqq = index_data.get("QQQ", {})
        dia = index_data.get("DIA", {})
        iwm = index_data.get("IWM", {})

        spy_change = self._pct(spy.get("change_percent", 0))
        qqq_change = self._pct(qqq.get("change_percent", 0))
        dia_change = self._pct(dia.get("change_percent", 0))
        iwm_change = self._pct(iwm.get("change_percent", 0))

        above_vwap_count = sum(1 for item in [spy, qqq, dia, iwm] if item.get("above_vwap", False))
        below_vwap_count = sum(1 for item in [spy, qqq, dia, iwm] if item.get("below_vwap", False))
        positive_count = sum(1 for value in [spy_change, qqq_change, dia_change, iwm_change] if value > 0)
        negative_count = sum(1 for value in [spy_change, qqq_change, dia_change, iwm_change] if value < 0)

        def _structure_value(value: Any) -> MarketStructure:
            return value.structure if hasattr(value, "structure") else value

        def _momentum_value(value: Any) -> MomentumRegime:
            return value.regime if hasattr(value, "regime") else value

        structure_counts = {
            "UP": sum(1 for value in structures.values() if _structure_value(value) == MarketStructure.UPTREND),
            "DOWN": sum(1 for value in structures.values() if _structure_value(value) == MarketStructure.DOWNTREND),
            "RANGE": sum(1 for value in structures.values() if _structure_value(value) == MarketStructure.RANGE),
            "MIXED": sum(1 for value in structures.values() if _structure_value(value) == MarketStructure.MIXED),
            "INSUFFICIENT": sum(1 for value in structures.values() if _structure_value(value) == MarketStructure.INSUFFICIENT_DATA),
        }
        momentum_counts = {
            "UP": sum(1 for value in momentum.values() if _momentum_value(value) in {MomentumRegime.ACCELERATING_UP, MomentumRegime.STEADY_UP}),
            "DOWN": sum(1 for value in momentum.values() if _momentum_value(value) in {MomentumRegime.ACCELERATING_DOWN, MomentumRegime.STEADY_DOWN}),
            "EXHAUSTING_UP": sum(1 for value in momentum.values() if _momentum_value(value) == MomentumRegime.EXHAUSTING_UP),
            "EXHAUSTING_DOWN": sum(1 for value in momentum.values() if _momentum_value(value) == MomentumRegime.EXHAUSTING_DOWN),
            "NEUTRAL": sum(1 for value in momentum.values() if _momentum_value(value) == MomentumRegime.NEUTRAL),
            "INSUFFICIENT": sum(1 for value in momentum.values() if _momentum_value(value) == MomentumRegime.INSUFFICIENT_DATA),
        }

        required_symbols = ("SPY", "QQQ", "DIA", "IWM")
        missing_structure_count = sum(
            1
            for symbol in required_symbols
            if symbol not in structures or _structure_value(structures[symbol]) == MarketStructure.INSUFFICIENT_DATA
        )
        missing_momentum_count = sum(
            1
            for symbol in required_symbols
            if symbol not in momentum or _momentum_value(momentum[symbol]) == MomentumRegime.INSUFFICIENT_DATA
        )
        missing_vwap_count = sum(
            1
            for symbol in required_symbols
            if symbol in index_data
            and "above_vwap" in index_data[symbol]
            and "below_vwap" in index_data[symbol]
            and not index_data[symbol].get("above_vwap")
            and not index_data[symbol].get("below_vwap")
        )

        missing_evidence = {
            "structure": missing_structure_count,
            "momentum": missing_momentum_count,
            "vwap": missing_vwap_count,
        }

        if cross_validation_confidence < self.rules.confidence_threshold or missing_structure_count >= 3 or missing_momentum_count >= 3:
            unknown_scores = {
                "RISK_ON": 0, "RISK_OFF": 0, "ROTATION": 0, "TREND_UP": 0,
                "TREND_DOWN": 0, "RANGE": 0, "HIGH_VOLATILITY": 0,
                "LOW_VOLATILITY": 0, "MIXED": 0, "UNKNOWN": 100,
            }
            raw_confidence = clamp_score(min(cross_validation_confidence, 49))
            return MarketRegimeAnalysis(
                regime=MarketRegime.UNKNOWN,
                confidence=raw_confidence,
                scores=unknown_scores,
                evidence=["Data confidence is too low for a deterministic regime call."],
                warnings=["Low confidence or insufficient structure/momentum data"],
                supporting_data={
                    "cross_validation_confidence": cross_validation_confidence,
                    "analysis_data_confidence": cross_validation_confidence,
                    "missing_evidence": missing_evidence,
                    "confidence_penalties": {},
                    "raw_confidence": raw_confidence,
                    "winning_score": 100,
                    "second_highest_regime": "RISK_ON",
                    "second_highest_score": 0,
                    "score_gap": 100,
                    "data_confidence_cap_applied": raw_confidence < 100,
                },
            )

        scores = {
            "RISK_ON": 0,
            "RISK_OFF": 0,
            "ROTATION": 0,
            "TREND_UP": 0,
            "TREND_DOWN": 0,
            "RANGE": 0,
            "HIGH_VOLATILITY": 0,
            "LOW_VOLATILITY": 0,
            "MIXED": 0,
            "UNKNOWN": 0,
        }
        evidence: list[str] = []
        warnings: list[str] = []

        if positive_count >= 3 and above_vwap_count >= 3 and structure_counts["UP"] >= 2 and momentum_counts["UP"] >= 2:
            scores["RISK_ON"] += 80
            evidence.append("Broad positive confirmation across major indices")
        if negative_count >= 3 and below_vwap_count >= 3 and structure_counts["DOWN"] >= 2 and momentum_counts["DOWN"] >= 2:
            scores["RISK_OFF"] += 80
            evidence.append("Broad negative confirmation across major indices")

        divergence_signals = divergences.signals if isinstance(divergences, DivergenceAnalysis) else list(divergences or [])
        if divergence_signals:
            if any(signal in {DivergenceSignal.QQQ_UNDERPERFORMING_SPY, DivergenceSignal.QQQ_OUTPERFORMING_SPY, DivergenceSignal.IWM_UNDERPERFORMING_SPY, DivergenceSignal.IWM_OUTPERFORMING_SPY} for signal in divergence_signals):
                if len(divergence_signals) >= 2 or (positive_count > 0 and negative_count > 0):
                    scores["ROTATION"] += 80
                    evidence.append("Cross-index relative performance is materially divergent")
            if any(signal.name.endswith("UNDERPERFORMING_SPY") for signal in divergence_signals):
                scores["MIXED"] += 20

        if structure_counts["UP"] >= 2 and momentum_counts["UP"] >= 2 and positive_count >= 2:
            scores["TREND_UP"] += 70
        if structure_counts["DOWN"] >= 2 and momentum_counts["DOWN"] >= 2 and negative_count >= 2:
            scores["TREND_DOWN"] += 70
        if structure_counts["RANGE"] >= 2 and abs(spy_change) <= self.rules.flat_change_threshold and abs(qqq_change) <= self.rules.flat_change_threshold:
            scores["RANGE"] += 75
        if structure_counts["MIXED"] >= 2:
            scores["MIXED"] += 60
        if momentum_counts["EXHAUSTING_UP"] or momentum_counts["EXHAUSTING_DOWN"]:
            scores["HIGH_VOLATILITY"] += 20
        if above_vwap_count >= 3 and structure_counts["RANGE"] >= 2:
            scores["LOW_VOLATILITY"] += 15

        if positive_count >= 3 and above_vwap_count >= 3:
            scores["RISK_ON"] += 20
        if negative_count >= 3 and below_vwap_count >= 3:
            scores["RISK_OFF"] += 20

        if divergence_signals and any(signal.name.endswith("UNDERPERFORMING_SPY") for signal in divergence_signals):
            scores["MIXED"] += 10

        chosen_name = max(scores.items(), key=lambda item: item[1])[0]
        raw_confidence = clamp_score(scores[chosen_name])
        confidence_penalties: dict[str, int] = {}
        if missing_structure_count:
            confidence_penalties["missing_structure_evidence"] = min(20, missing_structure_count * 10)
        if missing_momentum_count:
            confidence_penalties["missing_momentum_evidence"] = min(20, missing_momentum_count * 10)
        if missing_vwap_count:
            confidence_penalties["missing_vwap_evidence"] = min(20, missing_vwap_count * 5)

        confidence = clamp_score(raw_confidence - sum(confidence_penalties.values()))
        if confidence > cross_validation_confidence:
            confidence_penalties["data_confidence_cap"] = confidence - cross_validation_confidence
            confidence = clamp_score(cross_validation_confidence)
        if confidence < self.rules.confidence_threshold:
            chosen_name = "MIXED"
            confidence = max(confidence, 50)

        if chosen_name == "RISK_ON":
            evidence.append("SPY, QQQ, DIA, and IWM are broadly positive and above VWAP")
        elif chosen_name == "RISK_OFF":
            evidence.append("SPY, QQQ, DIA, and IWM are broadly negative and below VWAP")
        elif chosen_name == "ROTATION":
            evidence.append("Relative performance is divergent across the major indices")
        elif chosen_name == "TREND_UP":
            evidence.append("Directional structures and momentum are aligned higher")
        elif chosen_name == "TREND_DOWN":
            evidence.append("Directional structures and momentum are aligned lower")
        elif chosen_name == "RANGE":
            evidence.append("Price action is compressed and range-bound")
        elif chosen_name == "MIXED":
            evidence.append("Evidence is conflicting across indices and internal structure")
        else:
            warnings.append("Regime could not be classified deterministically")

        if isinstance(divergences, DivergenceAnalysis) and divergences.evidence:
            evidence.extend(divergences.evidence[:3])
        if cross_validation_confidence < 80:
            warnings.append("Cross-validation confidence is degraded")
        for reason, penalty in confidence_penalties.items():
            warnings.append(f"Regime confidence penalty: {reason} (-{penalty})")

        ranked_scores = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        second_regime, second_score = ranked_scores[1] if len(ranked_scores) > 1 else (None, None)
        data_cap_applied = "data_confidence_cap" in confidence_penalties

        return MarketRegimeAnalysis(
            regime=MarketRegime[chosen_name],
            confidence=confidence,
            scores=scores,
            evidence=evidence,
            warnings=warnings,
            supporting_data={
                "index_data": index_data,
                "structures": {symbol: _structure_value(value).value for symbol, value in structures.items()},
                "momentum": {symbol: _momentum_value(value).value for symbol, value in momentum.items()},
                "cross_validation_confidence": cross_validation_confidence,
                "analysis_data_confidence": cross_validation_confidence,
                "missing_evidence": missing_evidence,
                "confidence_penalties": confidence_penalties,
                "raw_confidence": raw_confidence,
                "winning_score": scores[chosen_name],
                "second_highest_regime": second_regime,
                "second_highest_score": second_score,
                "score_gap": scores[chosen_name] - second_score if second_score is not None else None,
                "data_confidence_cap_applied": data_cap_applied,
            },
        )


MarketRegimeDetector = MarketRegimeAnalyzer
MarketRegimeDecision = MarketRegimeAnalysis
