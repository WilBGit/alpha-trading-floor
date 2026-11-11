from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

from alpha_market_engine.market.brain_types import EventLifecycle, MarketBrainResult, PriceActionEvent


class OpportunityType(str, Enum):
    VWAP_RECLAIM = "VWAP_RECLAIM"
    VWAP_LOSS = "VWAP_LOSS"
    VWAP_REJECTION = "VWAP_REJECTION"
    ABOVE_VWAP_CONFIRMATION = "ABOVE_VWAP_CONFIRMATION"
    BELOW_VWAP_CONFIRMATION = "BELOW_VWAP_CONFIRMATION"
    OR5_BREAKOUT_CONFIRMED = "OR5_BREAKOUT_CONFIRMED"
    OR5_BREAKDOWN_CONFIRMED = "OR5_BREAKDOWN_CONFIRMED"
    OR15_BREAKOUT_CONFIRMED = "OR15_BREAKOUT_CONFIRMED"
    OR15_BREAKDOWN_CONFIRMED = "OR15_BREAKDOWN_CONFIRMED"
    OR30_BREAKOUT_CONFIRMED = "OR30_BREAKOUT_CONFIRMED"
    OR30_BREAKDOWN_CONFIRMED = "OR30_BREAKDOWN_CONFIRMED"
    FAILED_OR_BREAKOUT = "FAILED_OR_BREAKOUT"
    FAILED_OR_BREAKDOWN = "FAILED_OR_BREAKDOWN"
    SESSION_HIGH_BREAK = "SESSION_HIGH_BREAK"
    SESSION_LOW_BREAK = "SESSION_LOW_BREAK"
    RANGE_EXPANSION = "RANGE_EXPANSION"
    RANGE_COMPRESSION = "RANGE_COMPRESSION"
    MOMENTUM_ACCELERATION = "MOMENTUM_ACCELERATION"
    MOMENTUM_EXHAUSTION = "MOMENTUM_EXHAUSTION"
    TREND_STRUCTURE = "TREND_STRUCTURE"
    QQQ_LEADERSHIP = "QQQ_LEADERSHIP"
    QQQ_WEAKNESS = "QQQ_WEAKNESS"
    IWM_LEADERSHIP = "IWM_LEADERSHIP"
    IWM_WEAKNESS = "IWM_WEAKNESS"
    TECH_LEADERSHIP = "TECH_LEADERSHIP"
    SMALL_CAP_PARTICIPATION = "SMALL_CAP_PARTICIPATION"
    INDEX_DISAGREEMENT = "INDEX_DISAGREEMENT"
    BROAD_INDEX_CONFIRMATION = "BROAD_INDEX_CONFIRMATION"
    MARKET_ROTATION = "MARKET_ROTATION"
    RISK_ON_REGIME = "RISK_ON_REGIME"
    RISK_OFF_REGIME = "RISK_OFF_REGIME"
    TREND_UP_REGIME = "TREND_UP_REGIME"
    TREND_DOWN_REGIME = "TREND_DOWN_REGIME"
    RANGE_REGIME = "RANGE_REGIME"
    HIGH_VOLATILITY_REGIME = "HIGH_VOLATILITY_REGIME"
    LOW_VOLATILITY_REGIME = "LOW_VOLATILITY_REGIME"
    MIXED_MARKET_STRUCTURE = "MIXED_MARKET_STRUCTURE"
    REGIME_CHANGE = "REGIME_CHANGE"


class ConfirmationState(str, Enum):
    UNCONFIRMED = "UNCONFIRMED"
    DEVELOPING = "DEVELOPING"
    CONFIRMED = "CONFIRMED"
    WEAKENING = "WEAKENING"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"


class ContentDecision(str, Enum):
    POST_CANDIDATE = "POST_CANDIDATE"
    WAIT_FOR_CONFIRMATION = "WAIT_FOR_CONFIRMATION"
    SKIP = "SKIP"
    SUPPRESS_DUPLICATE = "SUPPRESS_DUPLICATE"


class DecisionReason(str, Enum):
    HIGH_IMPORTANCE_CONFIRMED_EVENT = "HIGH_IMPORTANCE_CONFIRMED_EVENT"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    INSUFFICIENT_NOVELTY = "INSUFFICIENT_NOVELTY"
    COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"
    DUPLICATE_ACTIVE_OPPORTUNITY = "DUPLICATE_ACTIVE_OPPORTUNITY"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    REGIME_TOO_AMBIGUOUS = "REGIME_TOO_AMBIGUOUS"
    MATERIAL_CHANGE_DETECTED = "MATERIAL_CHANGE_DETECTED"
    FACT_QUALITY_DEGRADED = "FACT_QUALITY_DEGRADED"
    OPPORTUNITY_EXPIRED = "OPPORTUNITY_EXPIRED"
    BELOW_CONTENT_SCORE_THRESHOLD = "BELOW_CONTENT_SCORE_THRESHOLD"
    HIGHER_RANKED_CANDIDATE_SELECTED = "HIGHER_RANKED_CANDIDATE_SELECTED"
    CYCLE_SELECTION_LIMIT_REACHED = "CYCLE_SELECTION_LIMIT_REACHED"
    LOWER_RANKED_ELIGIBLE_OPPORTUNITY = "LOWER_RANKED_ELIGIBLE_OPPORTUNITY"


BASE_IMPORTANCE: dict[OpportunityType, int] = {
    OpportunityType.REGIME_CHANGE: 92,
    OpportunityType.VWAP_RECLAIM: 78,
    OpportunityType.VWAP_LOSS: 78,
    OpportunityType.VWAP_REJECTION: 74,
    OpportunityType.OR30_BREAKOUT_CONFIRMED: 84,
    OpportunityType.OR30_BREAKDOWN_CONFIRMED: 84,
    OpportunityType.OR15_BREAKOUT_CONFIRMED: 78,
    OpportunityType.OR15_BREAKDOWN_CONFIRMED: 78,
    OpportunityType.OR5_BREAKOUT_CONFIRMED: 68,
    OpportunityType.OR5_BREAKDOWN_CONFIRMED: 68,
    OpportunityType.FAILED_OR_BREAKOUT: 76,
    OpportunityType.FAILED_OR_BREAKDOWN: 76,
    OpportunityType.QQQ_LEADERSHIP: 76,
    OpportunityType.QQQ_WEAKNESS: 76,
    OpportunityType.IWM_LEADERSHIP: 72,
    OpportunityType.IWM_WEAKNESS: 72,
    OpportunityType.INDEX_DISAGREEMENT: 74,
    OpportunityType.MARKET_ROTATION: 76,
    OpportunityType.MIXED_MARKET_STRUCTURE: 48,
    OpportunityType.RANGE_EXPANSION: 62,
    OpportunityType.RANGE_COMPRESSION: 52,
}

for _type in OpportunityType:
    BASE_IMPORTANCE.setdefault(_type, 55)

COOLDOWN_MINUTES: dict[str, int] = {
    "OR5": 25,
    "OR15": 40,
    "OR30": 75,
    "VWAP": 30,
    "RELATIVE_PERFORMANCE": 45,
    "REGIME": 90,
    "STRUCTURE": 45,
    "DEFAULT": 30,
}


@dataclass(slots=True)
class ContentOpportunity:
    opportunity_id: str
    opportunity_key: str
    analysis_id: str
    opportunity_type: OpportunityType
    symbols: tuple[str, ...]
    primary_symbol: str | None
    session_date: str
    detected_at: datetime
    source_regime: str
    source_regime_confidence: int
    source_event_ids: tuple[str, ...] = ()
    source_observation_keys: tuple[str, ...] = ()
    source_divergence_types: tuple[str, ...] = ()
    facts: dict[str, Any] = field(default_factory=dict)
    interpretation_boundary: str = "Data supports the stated deterministic fact. It does not independently prove unstated causes or continuation."
    importance_score: int = 0
    confidence_score: int = 0
    freshness_score: int = 0
    novelty_score: int = 0
    content_score: Decimal = Decimal("0")
    confirmation_state: ConfirmationState = ConfirmationState.UNCONFIRMED
    decision: ContentDecision = ContentDecision.SKIP
    decision_reasons: tuple[DecisionReason, ...] = ()
    cooldown_key: str = ""
    content_pattern_key: str = ""
    expires_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None
    material_change: bool = False


@dataclass(slots=True)
class ContentOpportunityResult:
    analysis_id: str
    detected_opportunities: list[ContentOpportunity]
    ranked_opportunities: list[ContentOpportunity]
    selected_candidates: list[ContentOpportunity]
    decision_summary: dict[str, int]
    suppression_summary: dict[str, int]
    warnings: list[str] = field(default_factory=list)


def canonical_key(*, opportunity_type: OpportunityType, symbols: tuple[str, ...], session_date: str, source_ids: tuple[str, ...], content_pattern_key: str) -> str:
    payload = {"type": opportunity_type.value, "symbols": sorted(symbols), "session_date": session_date, "source_ids": sorted(source_ids), "pattern": content_pattern_key}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _score(value: int | Decimal) -> int:
    return max(0, min(100, int(Decimal(str(value)).quantize(Decimal("1")))))


def _family(opportunity_type: OpportunityType) -> str:
    value = opportunity_type.value
    if value.startswith("OR5"):
        return "OR5"
    if value.startswith("OR15"):
        return "OR15"
    if value.startswith("OR30"):
        return "OR30"
    if "VWAP" in value:
        return "VWAP"
    if opportunity_type in {OpportunityType.QQQ_LEADERSHIP, OpportunityType.QQQ_WEAKNESS, OpportunityType.IWM_LEADERSHIP, OpportunityType.IWM_WEAKNESS, OpportunityType.INDEX_DISAGREEMENT}:
        return "RELATIVE_PERFORMANCE"
    if value.endswith("REGIME") or opportunity_type in {OpportunityType.MARKET_ROTATION, OpportunityType.REGIME_CHANGE}:
        return "REGIME"
    if "STRUCTURE" in value:
        return "STRUCTURE"
    return "DEFAULT"


def _map_event(event_type: str) -> OpportunityType | None:
    direct = {
        "VWAP_RECLAIM": OpportunityType.VWAP_RECLAIM,
        "VWAP_LOSS": OpportunityType.VWAP_LOSS,
        "VWAP_REJECTION": OpportunityType.VWAP_REJECTION,
        "ABOVE_VWAP": OpportunityType.ABOVE_VWAP_CONFIRMATION,
        "BELOW_VWAP": OpportunityType.BELOW_VWAP_CONFIRMATION,
        "FAILED_OR_BREAKOUT": OpportunityType.FAILED_OR_BREAKOUT,
        "FAILED_OR_BREAKDOWN": OpportunityType.FAILED_OR_BREAKDOWN,
        "RANGE_EXPANSION": OpportunityType.RANGE_EXPANSION,
        "RANGE_COMPRESSION": OpportunityType.RANGE_COMPRESSION,
        "MOMENTUM_ACCELERATION": OpportunityType.MOMENTUM_ACCELERATION,
        "MOMENTUM_EXHAUSTION": OpportunityType.MOMENTUM_EXHAUSTION,
        "MIXED_STRUCTURE": OpportunityType.MIXED_MARKET_STRUCTURE,
        "NEW_SESSION_HIGH": OpportunityType.SESSION_HIGH_BREAK,
        "NEW_SESSION_LOW": OpportunityType.SESSION_LOW_BREAK,
        "HIGHER_HIGHS_HIGHER_LOWS": OpportunityType.TREND_STRUCTURE,
        "LOWER_HIGHS_LOWER_LOWS": OpportunityType.TREND_STRUCTURE,
    }
    if event_type in direct:
        return direct[event_type]
    for duration in ("5", "15", "30"):
        if event_type == f"OR{duration}_BREAKOUT":
            return OpportunityType[f"OR{duration}_BREAKOUT_CONFIRMED"]
        if event_type == f"OR{duration}_BREAKDOWN":
            return OpportunityType[f"OR{duration}_BREAKDOWN_CONFIRMED"]
    return None


def _boundary(opportunity_type: OpportunityType) -> str:
    if opportunity_type in {OpportunityType.QQQ_LEADERSHIP, OpportunityType.QQQ_WEAKNESS, OpportunityType.IWM_LEADERSHIP, OpportunityType.IWM_WEAKNESS, OpportunityType.INDEX_DISAGREEMENT, OpportunityType.MARKET_ROTATION}:
        return "Data supports deterministic relative performance across the captured indices. It does not independently prove institutional activity, sector-level capital rotation, or causation."
    if opportunity_type.value.startswith("OR") or opportunity_type in {OpportunityType.FAILED_OR_BREAKOUT, OpportunityType.FAILED_OR_BREAKDOWN}:
        return "Data supports the captured opening-range event. It does not independently prove sustained trend continuation or reversal."
    if opportunity_type in {OpportunityType.VWAP_RECLAIM, OpportunityType.VWAP_LOSS, OpportunityType.VWAP_REJECTION, OpportunityType.ABOVE_VWAP_CONFIRMATION, OpportunityType.BELOW_VWAP_CONFIRMATION}:
        return "Data supports the captured VWAP relationship. It does not independently prove future direction or trader intent."
    return "Data supports the stated deterministic fact. It does not independently prove unstated causes or continuation."


def _content_pattern(opportunity_type: OpportunityType, symbols: tuple[str, ...], facts: dict[str, Any]) -> str:
    if opportunity_type.value.startswith("OR"):
        return f"{symbols[0] if symbols else 'MARKET'}:OR:{opportunity_type.value.split('_')[0]}"
    if opportunity_type in {OpportunityType.QQQ_LEADERSHIP, OpportunityType.QQQ_WEAKNESS}:
        return "QQQ_SPY:RELATIVE_PERFORMANCE:QQQ"
    if opportunity_type in {OpportunityType.IWM_LEADERSHIP, OpportunityType.IWM_WEAKNESS}:
        return "IWM_SPY:RELATIVE_PERFORMANCE:IWM"
    if opportunity_type in {OpportunityType.INDEX_DISAGREEMENT, OpportunityType.MARKET_ROTATION}:
        return "MAJOR_INDICES:RELATIVE_PERFORMANCE"
    if "VWAP" in opportunity_type.value:
        return f"{symbols[0] if symbols else 'MARKET'}:VWAP:{opportunity_type.value}"
    if opportunity_type.value.endswith("REGIME") or opportunity_type in {OpportunityType.REGIME_CHANGE, OpportunityType.MARKET_ROTATION}:
        return "MARKET:REGIME:" + opportunity_type.value
    return f"{','.join(symbols)}:{opportunity_type.value}"


class ContentOpportunityEngine:
    """Deterministic Phase 5A evaluator. It never queries a market-data provider."""

    def __init__(self, *, repository: Any | None = None, max_post_candidates_per_cycle: int = 1, min_confidence: int = 60, post_score_threshold: int = 70) -> None:
        self.repository = repository
        self.max_post_candidates_per_cycle = max_post_candidates_per_cycle
        self.min_confidence = min_confidence
        self.post_score_threshold = post_score_threshold
        self._memory: dict[str, ContentOpportunity] = {}

    def _history(self, key: str) -> ContentOpportunity | None:
        if key in self._memory:
            return self._memory[key]
        if self.repository is not None:
            return self.repository.get_content_opportunity_by_key(key)
        return None

    def _freshness(self, detected_at: datetime, now: datetime, opportunity_type: OpportunityType) -> int:
        age_minutes = max(0, int((now - detected_at).total_seconds() // 60))
        decay = 1 if _family(opportunity_type) == "REGIME" else 2
        return _score(100 - age_minutes * decay)

    def _confidence(self, result: MarketBrainResult, source_confidence: int | None = None) -> int:
        values = [result.analysis_confidence.score]
        if source_confidence is not None:
            values.append(source_confidence)
        return _score(Decimal(sum(values)) / Decimal(len(values)))

    def _persist(self, opportunity: ContentOpportunity) -> ContentOpportunity:
        self._memory[opportunity.opportunity_key] = opportunity
        if self.repository is not None:
            self.repository.upsert_content_opportunity(opportunity)
        return opportunity

    def _make(self, *, result: MarketBrainResult, opportunity_type: OpportunityType, symbols: tuple[str, ...], detected_at: datetime, facts: dict[str, Any], source_event_ids: tuple[str, ...] = (), source_observation_keys: tuple[str, ...] = (), source_divergence_types: tuple[str, ...] = (), source_confidence: int | None = None, confirmation: ConfirmationState = ConfirmationState.CONFIRMED) -> ContentOpportunity:
        pattern = _content_pattern(opportunity_type, symbols, facts)
        source_ids = source_event_ids + source_observation_keys + source_divergence_types
        key = canonical_key(opportunity_type=opportunity_type, symbols=symbols, session_date=result.analysis_session_date, source_ids=source_ids, content_pattern_key=pattern)
        existing = self._history(key)
        relative_difference = Decimal(str(facts["relative_difference"])) if "relative_difference" in facts else None
        if existing is not None and existing.confirmation_state == ConfirmationState.RESOLVED and relative_difference is not None and relative_difference >= Decimal("0.25"):
            key = canonical_key(opportunity_type=opportunity_type, symbols=symbols, session_date=result.analysis_session_date, source_ids=source_ids + ("occurrence:2",), content_pattern_key=pattern)
            existing = self._history(key)
        now = result.analysis_started_at
        importance = BASE_IMPORTANCE[opportunity_type]
        confidence = self._confidence(result, source_confidence)
        freshness = self._freshness(detected_at, now, opportunity_type)
        novelty = 10 if existing is not None else 90
        material_change = False
        if existing is not None and "relative_difference" in facts and "relative_difference" in existing.facts:
            material_change = abs(Decimal(str(facts["relative_difference"])) - Decimal(str(existing.facts["relative_difference"]))) >= Decimal("0.50")
            if material_change:
                novelty = 75
        if relative_difference is not None and relative_difference < Decimal("0.25") and existing is not None:
            confirmation = ConfirmationState.RESOLVED
        content_score = (Decimal(importance) * Decimal("0.35") + Decimal(confidence) * Decimal("0.30") + Decimal(freshness) * Decimal("0.20") + Decimal(novelty) * Decimal("0.15")).quantize(Decimal("0.01"))
        reasons: list[DecisionReason] = []
        promotion = existing is not None and existing.confirmation_state == ConfirmationState.DEVELOPING and confirmation == ConfirmationState.CONFIRMED
        if promotion:
            novelty = 75
            content_score = (Decimal(importance) * Decimal("0.35") + Decimal(confidence) * Decimal("0.30") + Decimal(freshness) * Decimal("0.20") + Decimal(novelty) * Decimal("0.15")).quantize(Decimal("0.01"))
        if confirmation == ConfirmationState.RESOLVED:
            decision = ContentDecision.SKIP
            reasons.append(DecisionReason.OPPORTUNITY_EXPIRED)
        elif existing is not None and not material_change and not promotion:
            decision = ContentDecision.SUPPRESS_DUPLICATE
            reasons.extend([DecisionReason.DUPLICATE_ACTIVE_OPPORTUNITY, DecisionReason.INSUFFICIENT_NOVELTY])
        elif confirmation != ConfirmationState.CONFIRMED:
            decision = ContentDecision.WAIT_FOR_CONFIRMATION
            reasons.append(DecisionReason.AWAITING_CONFIRMATION)
        elif confidence < self.min_confidence:
            decision = ContentDecision.SKIP
            reasons.append(DecisionReason.LOW_CONFIDENCE)
        elif content_score < self.post_score_threshold:
            decision = ContentDecision.SKIP
            reasons.append(DecisionReason.BELOW_CONTENT_SCORE_THRESHOLD)
        else:
            decision = ContentDecision.POST_CANDIDATE
            reasons.append(DecisionReason.HIGH_IMPORTANCE_CONFIRMED_EVENT)
            if material_change:
                reasons.append(DecisionReason.MATERIAL_CHANGE_DETECTED)
        opportunity = ContentOpportunity(
            opportunity_id=existing.opportunity_id if existing else str(uuid.uuid4()), opportunity_key=key, analysis_id=result.analysis_id,
            opportunity_type=opportunity_type, symbols=tuple(sorted(symbols)), primary_symbol=symbols[0] if symbols else None,
            session_date=result.analysis_session_date, detected_at=detected_at, source_regime=result.regime.regime.value,
            source_regime_confidence=result.regime.confidence, source_event_ids=source_event_ids, source_observation_keys=source_observation_keys,
            source_divergence_types=source_divergence_types, facts=facts, interpretation_boundary=_boundary(opportunity_type),
            importance_score=importance, confidence_score=confidence, freshness_score=freshness, novelty_score=novelty,
            content_score=content_score, confirmation_state=confirmation, decision=decision, decision_reasons=tuple(reasons),
            cooldown_key=pattern, content_pattern_key=pattern, expires_at=detected_at + timedelta(minutes=COOLDOWN_MINUTES.get(_family(opportunity_type), 30)),
            created_at=existing.created_at if existing else now, updated_at=now, material_change=material_change,
        )
        return self._persist(opportunity)

    def evaluate(self, market_brain_result: MarketBrainResult) -> ContentOpportunityResult:
        result = market_brain_result
        detected: list[ContentOpportunity] = []
        for symbol, analysis in result.price_action.items():
            for event in analysis.events:
                mapped = _map_event(event.event_type.value)
                if mapped is None:
                    continue
                state = ConfirmationState.CONFIRMED if event.lifecycle == EventLifecycle.CONFIRMED else ConfirmationState.RESOLVED if event.lifecycle in {EventLifecycle.FAILED, EventLifecycle.RESOLVED, EventLifecycle.EXPIRED} else ConfirmationState.DEVELOPING
                detected.append(self._make(result=result, opportunity_type=mapped, symbols=(symbol,), detected_at=event.detected_at, facts={"evidence": list(event.evidence), **event.supporting_values}, source_event_ids=(event.event_id or f"{symbol}:{event.event_type.value}",), source_confidence=event.confidence, confirmation=state))
        for signal in result.divergences.signals:
            if signal == "QQQ_OUTPERFORMING_SPY":
                fact = result.divergences.relative_performance_facts.get("QQQ", {})
                detected.append(self._make(result=result, opportunity_type=OpportunityType.QQQ_LEADERSHIP, symbols=("QQQ", "SPY"), detected_at=result.analysis_started_at, facts=dict(fact), source_divergence_types=(signal,), source_confidence=result.divergences.confidence))
            elif signal == "QQQ_UNDERPERFORMING_SPY":
                fact = result.divergences.relative_performance_facts.get("QQQ", {})
                detected.append(self._make(result=result, opportunity_type=OpportunityType.QQQ_WEAKNESS, symbols=("QQQ", "SPY"), detected_at=result.analysis_started_at, facts=dict(fact), source_divergence_types=(signal,), source_confidence=result.divergences.confidence))
            elif signal == "IWM_OUTPERFORMING_SPY":
                fact = result.divergences.relative_performance_facts.get("IWM", {})
                detected.append(self._make(result=result, opportunity_type=OpportunityType.IWM_LEADERSHIP, symbols=("IWM", "SPY"), detected_at=result.analysis_started_at, facts=dict(fact), source_divergence_types=(signal,), source_confidence=result.divergences.confidence))
            elif signal == "IWM_UNDERPERFORMING_SPY":
                fact = result.divergences.relative_performance_facts.get("IWM", {})
                detected.append(self._make(result=result, opportunity_type=OpportunityType.IWM_WEAKNESS, symbols=("IWM", "SPY"), detected_at=result.analysis_started_at, facts=dict(fact), source_divergence_types=(signal,), source_confidence=result.divergences.confidence))
            elif signal == "INDEX_DISAGREEMENT":
                detected.append(self._make(result=result, opportunity_type=OpportunityType.INDEX_DISAGREEMENT, symbols=("SPY", "QQQ", "DIA", "IWM"), detected_at=result.analysis_started_at, facts=dict(result.divergences.index_spread_fact or {}), source_divergence_types=(signal,), source_confidence=result.divergences.confidence))
        regime_map = {"ROTATION": OpportunityType.MARKET_ROTATION, "RISK_ON": OpportunityType.RISK_ON_REGIME, "RISK_OFF": OpportunityType.RISK_OFF_REGIME, "TREND_UP": OpportunityType.TREND_UP_REGIME, "TREND_DOWN": OpportunityType.TREND_DOWN_REGIME, "RANGE": OpportunityType.RANGE_REGIME, "HIGH_VOLATILITY": OpportunityType.HIGH_VOLATILITY_REGIME, "LOW_VOLATILITY": OpportunityType.LOW_VOLATILITY_REGIME}
        if result.regime.regime.value in regime_map:
            regime_type = regime_map[result.regime.regime.value]
            detected.append(self._make(result=result, opportunity_type=regime_type, symbols=("SPY", "QQQ", "DIA", "IWM"), detected_at=result.analysis_started_at, facts={"scores": result.regime.scores, "winning_score": result.regime.supporting_data.get("winning_score")}, source_observation_keys=tuple(o.observation_key for o in result.observations if o.observation_key is not None), source_confidence=result.regime.confidence, confirmation=ConfirmationState.DEVELOPING if result.regime.confidence < 70 else ConfirmationState.CONFIRMED))
        if any(a.structure.value == "MIXED" for a in result.structures.values()):
            detected.append(self._make(result=result, opportunity_type=OpportunityType.MIXED_MARKET_STRUCTURE, symbols=tuple(sorted(result.structures)), detected_at=result.analysis_started_at, facts={"structures": {s: a.structure.value for s, a in result.structures.items()}}, source_observation_keys=tuple(o.observation_key for o in result.observations if o.observation_type == "MIXED_INDEX_STRUCTURE"), source_confidence=result.analysis_confidence.score, confirmation=ConfirmationState.CONFIRMED))
        ranked = sorted(detected, key=lambda x: (-int(x.decision == ContentDecision.POST_CANDIDATE), -x.content_score, -x.confidence_score, -x.importance_score, -x.freshness_score, x.opportunity_key))
        selected = [o for o in ranked if o.decision == ContentDecision.POST_CANDIDATE][: self.max_post_candidates_per_cycle]
        for opportunity in ranked:
            if opportunity.decision == ContentDecision.POST_CANDIDATE and opportunity not in selected:
                opportunity.decision = ContentDecision.SKIP
                opportunity.decision_reasons = (
                    DecisionReason.CYCLE_SELECTION_LIMIT_REACHED,
                    DecisionReason.LOWER_RANKED_ELIGIBLE_OPPORTUNITY,
                )
                self._persist(opportunity)
        summary = {decision.value: sum(1 for o in ranked if o.decision == decision) for decision in ContentDecision}
        suppression = {"duplicate": summary[ContentDecision.SUPPRESS_DUPLICATE.value], "cooldown": 0}
        return ContentOpportunityResult(analysis_id=result.analysis_id, detected_opportunities=detected, ranked_opportunities=ranked, selected_candidates=selected, decision_summary=summary, suppression_summary=suppression)
