from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from typing import Any

from .brain_types import (
    DivergenceAnalysis,
    DivergenceSignal,
    EventLifecycle,
    MarketObservation,
    MarketRegime,
    MarketRegimeAnalysis,
    MomentumAnalysis,
    MomentumRegime,
    ObservationType,
    PriceActionAnalysis,
    PriceActionEvent,
    PriceActionEventType,
)


@dataclass(slots=True)
class ObservationRules:
    minimum_confidence: int = 60
    relative_gap_threshold: Decimal = Decimal("0.25")


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


class MarketObservationEngine:
    def __init__(self, rules: ObservationRules | None = None) -> None:
        self.rules = rules or ObservationRules()

    def _boundary_for(self, observation_type: ObservationType) -> str:
        boundaries = {
            ObservationType.QQQ_WEAKER_THAN_SPY: "Data supports relative weakness. It does not independently prove sector rotation.",
            ObservationType.SPY_HOLDING_ABOVE_VWAP: "Data supports SPY strength above VWAP. It does not independently prove a sustained trend.",
            ObservationType.SMALL_CAPS_NOT_CONFIRMING: "Data supports small-cap lag. It does not independently prove a rotation regime.",
            ObservationType.BROAD_MARKET_STRENGTH: "Data supports broad strength. It does not independently prove durable trend continuation.",
            ObservationType.BROAD_MARKET_WEAKNESS: "Data supports broad weakness. It does not independently prove a macro regime shift.",
            ObservationType.MIXED_INDEX_STRUCTURE: "Data supports mixed structure. It does not independently prove direction.",
            ObservationType.VWAP_RECLAIM_CONFIRMED: "Data supports a confirmed VWAP reclaim. It does not independently prove a lasting breakout.",
            ObservationType.OPENING_RANGE_BREAKOUT_CONFIRMED: "Data supports a confirmed opening-range breakout. It does not independently prove follow-through.",
            ObservationType.OPENING_RANGE_BREAKOUT_FAILED: "Data supports a failed opening-range breakout. It does not independently prove deeper trend reversal.",
            ObservationType.MARKET_STRUCTURE_IMPROVING: "Data supports improving structure. It does not independently prove a trend change.",
            ObservationType.MARKET_STRUCTURE_WEAKENING: "Data supports weakening structure. It does not independently prove a trend change.",
            ObservationType.MOMENTUM_ACCELERATING: "Data supports stronger momentum. It does not independently prove order flow.",
            ObservationType.MOMENTUM_FADING: "Data supports fading momentum. It does not independently prove capitulation.",
        }
        return boundaries[observation_type]

    def _json_safe(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, datetime):
            return value.astimezone(UTC).isoformat()
        if isinstance(value, EventLifecycle):
            return value.value
        if isinstance(value, dict):
            return {str(key): self._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._json_safe(item) for item in value]
        return value

    def _observation_key(self, signature: dict[str, Any]) -> str:
        payload = json.dumps(self._json_safe(signature), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _gap_bucket(self, gap_percent: Decimal) -> int:
        threshold = self.rules.relative_gap_threshold
        if threshold <= 0:
            return 0
        return int((abs(gap_percent) / threshold).to_integral_value(rounding=ROUND_FLOOR))

    def _divergence_signature(self, item: dict[str, Any]) -> dict[str, Any]:
        signal = item.get("signal")
        supporting_data = item.get("supporting_data") or {}
        symbols = list(item.get("symbols") or [])
        if isinstance(signal, DivergenceSignal):
            signal_value = signal.value
        else:
            signal_value = str(signal)

        if signal == DivergenceSignal.QQQ_UNDERPERFORMING_SPY:
            qqq_change = _optional_decimal(supporting_data.get("QQQ", {}).get("change_percent"))
            spy_change = _optional_decimal(supporting_data.get("SPY", {}).get("change_percent"))
            gap = qqq_change - spy_change if qqq_change is not None and spy_change is not None else None
            return {
                "kind": "divergence",
                "signal": signal_value,
                "symbols": ["QQQ", "SPY"],
                "gap_bucket": self._gap_bucket(gap) if gap is not None else None,
                "vwap_alignment": {
                    "QQQ": bool(supporting_data.get("QQQ", {}).get("above_vwap", False)),
                    "SPY": bool(supporting_data.get("SPY", {}).get("above_vwap", False)),
                },
                "structure": {
                    "QQQ": supporting_data.get("QQQ", {}).get("structure"),
                    "SPY": supporting_data.get("SPY", {}).get("structure"),
                },
                "momentum": {
                    "QQQ": supporting_data.get("QQQ", {}).get("momentum"),
                    "SPY": supporting_data.get("SPY", {}).get("momentum"),
                },
                "source_events": list(item.get("source_events", [])),
            }
        if signal == DivergenceSignal.IWM_UNDERPERFORMING_SPY:
            iwm_change = _optional_decimal(supporting_data.get("IWM", {}).get("change_percent"))
            spy_change = _optional_decimal(supporting_data.get("SPY", {}).get("change_percent"))
            gap = iwm_change - spy_change if iwm_change is not None and spy_change is not None else None
            return {
                "kind": "divergence",
                "signal": signal_value,
                "symbols": ["IWM", "SPY"],
                "gap_bucket": self._gap_bucket(gap) if gap is not None else None,
                "vwap_alignment": {
                    "IWM": bool(supporting_data.get("IWM", {}).get("above_vwap", False)),
                    "SPY": bool(supporting_data.get("SPY", {}).get("above_vwap", False)),
                },
                "structure": {
                    "IWM": supporting_data.get("IWM", {}).get("structure"),
                    "SPY": supporting_data.get("SPY", {}).get("structure"),
                },
                "momentum": {
                    "IWM": supporting_data.get("IWM", {}).get("momentum"),
                    "SPY": supporting_data.get("SPY", {}).get("momentum"),
                },
            }
        if signal in {DivergenceSignal.BROAD_INDEX_CONFIRMATION, DivergenceSignal.INDEX_DISAGREEMENT}:
            changes = {symbol: _optional_decimal(values.get("change_percent")) for symbol, values in supporting_data.items()}
            valid_changes = [value for value in changes.values() if value is not None]
            spread = max(valid_changes) - min(valid_changes) if len(valid_changes) == len(changes) and valid_changes else None
            return {
                "kind": "divergence",
                "signal": signal_value,
                "symbols": symbols or sorted(supporting_data.keys()),
                "spread_bucket": self._gap_bucket(spread) if spread is not None else None,
                "vwap_alignment": {symbol: bool(values.get("above_vwap", False)) for symbol, values in supporting_data.items()},
                "structure": {symbol: values.get("structure") for symbol, values in supporting_data.items()},
                "momentum": {symbol: values.get("momentum") for symbol, values in supporting_data.items()},
                "source_events": list(item.get("source_events", [])),
            }
        return {
            "kind": "divergence",
            "signal": signal_value,
            "symbols": symbols or sorted(supporting_data.keys()),
            "facts": list(item.get("facts", [])),
        }

    def _observation_key_for_event(self, observation_type: ObservationType, event: PriceActionEvent) -> str:
        return self._observation_key(
            {
                "kind": "event",
                "observation_type": observation_type.value,
                "event_id": event.event_id or event.event_type.value,
                "symbol": event.symbol,
                "session_date": event.session_date or event.detected_at,
            }
        )

    def _observation_key_for_regime(self, observation_type: ObservationType, item: MarketRegimeAnalysis) -> str:
        return self._observation_key(
            {
                "kind": "regime",
                "observation_type": observation_type.value,
                "regime": item.regime.value,
                "symbols": list(item.supporting_data.get("symbols", ["SPY", "QQQ", "DIA", "IWM"])),
                "source_events": list(item.supporting_data.get("source_events", [])),
            }
        )

    def build(
        self,
        items: list[
            dict[str, Any]
            | PriceActionEvent
            | DivergenceAnalysis
            | PriceActionAnalysis
            | MomentumAnalysis
            | MarketRegimeAnalysis
        ],
    ) -> list[MarketObservation]:
        observations: list[MarketObservation] = []
        for item in items:
            if isinstance(item, PriceActionEvent):
                if item.event_type == PriceActionEventType.VWAP_RECLAIM and item.lifecycle == EventLifecycle.CONFIRMED:
                    obs_type = ObservationType.VWAP_RECLAIM_CONFIRMED
                elif item.event_type in {
                    PriceActionEventType.OR5_BREAKOUT,
                    PriceActionEventType.OR15_BREAKOUT,
                    PriceActionEventType.OR30_BREAKOUT,
                } and item.lifecycle == EventLifecycle.CONFIRMED:
                    obs_type = ObservationType.OPENING_RANGE_BREAKOUT_CONFIRMED
                elif item.event_type in {
                    PriceActionEventType.FAILED_OR_BREAKOUT,
                    PriceActionEventType.FAILED_OR_BREAKDOWN,
                }:
                    obs_type = ObservationType.OPENING_RANGE_BREAKOUT_FAILED
                else:
                    continue

                observations.append(
                    MarketObservation(
                        observation_type=obs_type,
                        symbols=[item.symbol],
                        detected_at=item.detected_at,
                        importance_score=item.importance,
                        confidence_score=item.confidence,
                        facts=list(item.evidence),
                        interpretation_boundary=self._boundary_for(obs_type),
                        source_events=[item.event_id or item.event_type.value],
                        observation_key=self._observation_key_for_event(obs_type, item),
                        session_date=item.session_date or item.detected_at.astimezone(UTC),
                    )
                )
                continue

            if isinstance(item, dict):
                signal = item.get("signal")
                if signal == DivergenceSignal.QQQ_UNDERPERFORMING_SPY:
                    obs_type = ObservationType.QQQ_WEAKER_THAN_SPY
                    symbols = list(item.get("symbols", ["QQQ", "SPY"]))
                    importance = int(item.get("importance", 80))
                    confidence = int(item.get("confidence", 90))
                elif signal == DivergenceSignal.IWM_UNDERPERFORMING_SPY:
                    obs_type = ObservationType.SMALL_CAPS_NOT_CONFIRMING
                    symbols = list(item.get("symbols", ["IWM", "SPY"]))
                    importance = int(item.get("importance", 76))
                    confidence = int(item.get("confidence", 85))
                elif signal == DivergenceSignal.BROAD_INDEX_CONFIRMATION:
                    obs_type = ObservationType.BROAD_MARKET_STRENGTH
                    symbols = list(item.get("symbols", ["SPY", "QQQ", "DIA", "IWM"]))
                    importance = int(item.get("importance", 88))
                    confidence = int(item.get("confidence", 88))
                elif signal == DivergenceSignal.INDEX_DISAGREEMENT:
                    obs_type = ObservationType.MIXED_INDEX_STRUCTURE
                    symbols = list(item.get("symbols", ["SPY", "QQQ", "DIA", "IWM"]))
                    importance = int(item.get("importance", 78))
                    confidence = int(item.get("confidence", 82))
                else:
                    continue

                detected_at = item.get("detected_at", datetime.now(UTC))
                observations.append(
                    MarketObservation(
                        observation_type=obs_type,
                        symbols=symbols,
                        detected_at=detected_at,
                        importance_score=importance,
                        confidence_score=confidence,
                        facts=list(item.get("facts", [])),
                        interpretation_boundary=self._boundary_for(obs_type),
                        source_events=list(item.get("source_events", [])) or [signal.value if hasattr(signal, "value") else str(signal)],
                        observation_key=self._observation_key(self._divergence_signature(item)),
                        session_date=detected_at.astimezone(UTC),
                    )
                )
                continue

            if isinstance(item, MarketRegimeAnalysis):
                if item.regime == MarketRegime.RISK_ON:
                    obs_type = ObservationType.BROAD_MARKET_STRENGTH
                elif item.regime == MarketRegime.RISK_OFF:
                    obs_type = ObservationType.BROAD_MARKET_WEAKNESS
                elif item.regime == MarketRegime.ROTATION:
                    obs_type = ObservationType.MIXED_INDEX_STRUCTURE
                elif item.regime == MarketRegime.TREND_UP:
                    obs_type = ObservationType.MARKET_STRUCTURE_IMPROVING
                elif item.regime == MarketRegime.TREND_DOWN:
                    obs_type = ObservationType.MARKET_STRUCTURE_WEAKENING
                elif item.regime == MarketRegime.RANGE:
                    obs_type = ObservationType.MIXED_INDEX_STRUCTURE
                else:
                    continue
                observations.append(
                    MarketObservation(
                        observation_type=obs_type,
                        symbols=list(item.supporting_data.get("symbols", ["SPY", "QQQ", "DIA", "IWM"])),
                        detected_at=item.detected_at,
                        importance_score=int(item.scores.get(item.regime.value, item.confidence)),
                        confidence_score=item.confidence,
                        facts=list(item.evidence),
                        interpretation_boundary=self._boundary_for(obs_type),
                        source_events=list(item.supporting_data.get("source_events", [])),
                        observation_key=self._observation_key_for_regime(obs_type, item),
                        session_date=item.detected_at.astimezone(UTC),
                    )
                )
                continue

            if isinstance(item, PriceActionAnalysis):
                for event in item.events:
                    if event.event_type == PriceActionEventType.ABOVE_VWAP:
                        obs_type = ObservationType.SPY_HOLDING_ABOVE_VWAP if event.symbol == "SPY" else ObservationType.BROAD_MARKET_STRENGTH
                    elif event.event_type in {PriceActionEventType.RANGE_EXPANSION, PriceActionEventType.MOMENTUM_ACCELERATION}:
                        obs_type = ObservationType.MOMENTUM_ACCELERATING
                    elif event.event_type in {PriceActionEventType.RANGE_COMPRESSION, PriceActionEventType.MOMENTUM_EXHAUSTION}:
                        obs_type = ObservationType.MOMENTUM_FADING
                    else:
                        continue
                    observations.append(
                        MarketObservation(
                            observation_type=obs_type,
                            symbols=[event.symbol],
                            detected_at=event.detected_at,
                            importance_score=event.importance,
                            confidence_score=event.confidence,
                            facts=list(event.evidence),
                            interpretation_boundary=self._boundary_for(obs_type),
                            source_events=[event.event_id or event.event_type.value],
                            observation_key=self._observation_key_for_event(obs_type, event),
                            session_date=event.session_date or event.detected_at.astimezone(UTC),
                        )
                    )
                continue

            if isinstance(item, MomentumAnalysis):
                if item.regime == MomentumRegime.ACCELERATING_UP:
                    obs_type = ObservationType.MOMENTUM_ACCELERATING
                elif item.regime == MomentumRegime.EXHAUSTING_UP:
                    obs_type = ObservationType.MOMENTUM_FADING
                elif item.regime == MomentumRegime.STEADY_UP:
                    obs_type = ObservationType.BROAD_MARKET_STRENGTH
                elif item.regime == MomentumRegime.STEADY_DOWN:
                    obs_type = ObservationType.BROAD_MARKET_WEAKNESS
                elif item.regime == MomentumRegime.EXHAUSTING_DOWN:
                    obs_type = ObservationType.MOMENTUM_FADING
                elif item.regime == MomentumRegime.ACCELERATING_DOWN:
                    obs_type = ObservationType.MARKET_STRUCTURE_WEAKENING
                else:
                    continue
                observations.append(
                    MarketObservation(
                        observation_type=obs_type,
                        symbols=[item.symbol],
                        detected_at=item.detected_at,
                        importance_score=item.importance,
                        confidence_score=item.confidence,
                        facts=list(item.evidence),
                        interpretation_boundary=self._boundary_for(obs_type),
                        source_events=[item.regime.value],
                        observation_key=self._observation_key(
                            {
                                "kind": "momentum",
                                "observation_type": obs_type.value,
                                "symbol": item.symbol,
                                "regime": item.regime.value,
                                "session_date": item.detected_at,
                            }
                        ),
                        session_date=item.detected_at.astimezone(UTC),
                    )
                )
                continue

        return observations
