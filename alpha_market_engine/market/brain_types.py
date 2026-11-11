from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any, Literal


class CrossValidationStatus(str, Enum):
    MATCH = "MATCH"
    WITHIN_TOLERANCE = "WITHIN_TOLERANCE"
    DIVERGENT = "DIVERGENT"
    NOT_COMPARABLE = "NOT_COMPARABLE"
    SECONDARY_UNAVAILABLE = "SECONDARY_UNAVAILABLE"


@dataclass(slots=True)
class CrossValidationTolerances:
    price_percent: Decimal = Decimal("0.10")
    bar_close_percent: Decimal = Decimal("0.10")
    session_high_low_percent: Decimal = Decimal("0.10")
    session_vwap_percent: Decimal = Decimal("0.10")


@dataclass(slots=True)
class CrossValidationResult:
    symbol: str
    field: str
    primary_provider: str
    secondary_provider: str | None
    primary_value: Decimal | None
    secondary_value: Decimal | None
    absolute_difference: Decimal | None
    percentage_difference: Decimal | None
    status: CrossValidationStatus
    reason: str


@dataclass(slots=True)
class ConfidencePenalty:
    reason: str
    penalty: int


@dataclass(slots=True)
class AnalysisConfidence:
    score: int
    penalties: list[ConfidencePenalty] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data_sources: list[str] = field(default_factory=list)


class EventLifecycle(str, Enum):
    DETECTED = "DETECTED"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"

    def is_terminal(self) -> bool:
        return self in {EventLifecycle.FAILED, EventLifecycle.RESOLVED, EventLifecycle.EXPIRED}


class PriceActionEventType(str, Enum):
    ABOVE_VWAP = "ABOVE_VWAP"
    BELOW_VWAP = "BELOW_VWAP"
    VWAP_RECLAIM = "VWAP_RECLAIM"
    VWAP_REJECTION = "VWAP_REJECTION"
    VWAP_LOSS = "VWAP_LOSS"
    ABOVE_PREVIOUS_CLOSE = "ABOVE_PREVIOUS_CLOSE"
    BELOW_PREVIOUS_CLOSE = "BELOW_PREVIOUS_CLOSE"
    BREAK_PREVIOUS_DAY_HIGH = "BREAK_PREVIOUS_DAY_HIGH"
    BREAK_PREVIOUS_DAY_LOW = "BREAK_PREVIOUS_DAY_LOW"
    OR5_BREAKOUT = "OR5_BREAKOUT"
    OR5_BREAKDOWN = "OR5_BREAKDOWN"
    OR15_BREAKOUT = "OR15_BREAKOUT"
    OR15_BREAKDOWN = "OR15_BREAKDOWN"
    OR30_BREAKOUT = "OR30_BREAKOUT"
    OR30_BREAKDOWN = "OR30_BREAKDOWN"
    FAILED_OR_BREAKOUT = "FAILED_OR_BREAKOUT"
    FAILED_OR_BREAKDOWN = "FAILED_OR_BREAKDOWN"
    SESSION_HIGH_TEST = "SESSION_HIGH_TEST"
    SESSION_LOW_TEST = "SESSION_LOW_TEST"
    NEW_SESSION_HIGH = "NEW_SESSION_HIGH"
    NEW_SESSION_LOW = "NEW_SESSION_LOW"
    RANGE_COMPRESSION = "RANGE_COMPRESSION"
    RANGE_EXPANSION = "RANGE_EXPANSION"
    MOMENTUM_ACCELERATION = "MOMENTUM_ACCELERATION"
    MOMENTUM_EXHAUSTION = "MOMENTUM_EXHAUSTION"
    HIGHER_HIGHS_HIGHER_LOWS = "HIGHER_HIGHS_HIGHER_LOWS"
    LOWER_HIGHS_LOWER_LOWS = "LOWER_HIGHS_LOWER_LOWS"
    MIXED_STRUCTURE = "MIXED_STRUCTURE"


@dataclass(slots=True)
class OpeningRangeLevels:
    or5_high: Decimal | None = None
    or5_low: Decimal | None = None
    or15_high: Decimal | None = None
    or15_low: Decimal | None = None
    or30_high: Decimal | None = None
    or30_low: Decimal | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PriceActionEvent:
    event_type: PriceActionEventType
    symbol: str
    detected_at: datetime
    confidence: int
    importance: int
    evidence: list[str]
    supporting_values: dict[str, Any]
    lifecycle: EventLifecycle = EventLifecycle.DETECTED
    event_id: str | None = None
    session_date: datetime | None = None


@dataclass(slots=True)
class PriceActionAnalysis:
    symbol: str
    detected_at: datetime
    current_price: Decimal
    previous_close: Decimal | None
    session_vwap: Decimal | None
    opening_ranges: OpeningRangeLevels
    events: list[PriceActionEvent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class MarketStructure(str, Enum):
    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    RANGE = "RANGE"
    MIXED = "MIXED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(slots=True)
class SwingPoint:
    timestamp: datetime
    price: Decimal
    kind: Literal["HIGH", "LOW"]
    index: int


@dataclass(slots=True)
class StructureAnalysis:
    symbol: str
    structure: MarketStructure
    confidence: int
    swing_highs: list[SwingPoint] = field(default_factory=list)
    swing_lows: list[SwingPoint] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    reasoning: str = ""


class MomentumRegime(str, Enum):
    ACCELERATING_UP = "ACCELERATING_UP"
    ACCELERATING_DOWN = "ACCELERATING_DOWN"
    STEADY_UP = "STEADY_UP"
    STEADY_DOWN = "STEADY_DOWN"
    EXHAUSTING_UP = "EXHAUSTING_UP"
    EXHAUSTING_DOWN = "EXHAUSTING_DOWN"
    NEUTRAL = "NEUTRAL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(slots=True)
class MomentumAnalysis:
    symbol: str
    regime: MomentumRegime
    confidence: int
    detected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    importance: int = 0
    evidence: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


class DivergenceSignal(str, Enum):
    QQQ_UNDERPERFORMING_SPY = "QQQ_UNDERPERFORMING_SPY"
    QQQ_OUTPERFORMING_SPY = "QQQ_OUTPERFORMING_SPY"
    IWM_UNDERPERFORMING_SPY = "IWM_UNDERPERFORMING_SPY"
    IWM_OUTPERFORMING_SPY = "IWM_OUTPERFORMING_SPY"
    DIA_LEADING = "DIA_LEADING"
    TECH_LEADERSHIP = "TECH_LEADERSHIP"
    SMALL_CAP_PARTICIPATION = "SMALL_CAP_PARTICIPATION"
    INDEX_DISAGREEMENT = "INDEX_DISAGREEMENT"
    BROAD_INDEX_CONFIRMATION = "BROAD_INDEX_CONFIRMATION"


@dataclass(slots=True)
class DivergenceAnalysis:
    signals: list[DivergenceSignal] = field(default_factory=list)
    confidence: int = 0
    evidence: list[str] = field(default_factory=list)
    supporting_data: dict[str, Any] = field(default_factory=dict)
    relative_performance_facts: dict[str, dict[str, Any]] = field(default_factory=dict)
    index_spread_fact: dict[str, Any] | None = None


class MarketRegime(str, Enum):
    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    ROTATION = "ROTATION"
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class MarketRegimeAnalysis:
    regime: MarketRegime
    confidence: int
    scores: dict[str, int]
    detected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    supporting_data: dict[str, Any] = field(default_factory=dict)

    @property
    def reasoning(self) -> str:
        parts = self.evidence or self.warnings or []
        if parts:
            return f"{self.regime.value}: {' '.join(parts)}"
        return self.regime.value


class ObservationType(str, Enum):
    SPY_HOLDING_ABOVE_VWAP = "SPY_HOLDING_ABOVE_VWAP"
    QQQ_WEAKER_THAN_SPY = "QQQ_WEAKER_THAN_SPY"
    SMALL_CAPS_NOT_CONFIRMING = "SMALL_CAPS_NOT_CONFIRMING"
    BROAD_MARKET_STRENGTH = "BROAD_MARKET_STRENGTH"
    BROAD_MARKET_WEAKNESS = "BROAD_MARKET_WEAKNESS"
    MIXED_INDEX_STRUCTURE = "MIXED_INDEX_STRUCTURE"
    VWAP_RECLAIM_CONFIRMED = "VWAP_RECLAIM_CONFIRMED"
    OPENING_RANGE_BREAKOUT_CONFIRMED = "OPENING_RANGE_BREAKOUT_CONFIRMED"
    OPENING_RANGE_BREAKOUT_FAILED = "OPENING_RANGE_BREAKOUT_FAILED"
    MARKET_STRUCTURE_IMPROVING = "MARKET_STRUCTURE_IMPROVING"
    MARKET_STRUCTURE_WEAKENING = "MARKET_STRUCTURE_WEAKENING"
    MOMENTUM_ACCELERATING = "MOMENTUM_ACCELERATING"
    MOMENTUM_FADING = "MOMENTUM_FADING"


@dataclass(slots=True)
class MarketObservation:
    observation_type: ObservationType
    symbols: list[str]
    detected_at: datetime
    importance_score: int
    confidence_score: int
    facts: list[str]
    interpretation_boundary: str
    source_events: list[str] = field(default_factory=list)
    observation_key: str | None = None
    session_date: datetime | None = None


@dataclass(frozen=True, slots=True)
class MarketAnalysisSymbolInput:
    symbol: str
    current_price: Decimal | None
    current_price_source: str
    current_price_timestamp: datetime | None
    previous_close: Decimal | None
    previous_close_source: str
    change_percent: Decimal | None
    change_percent_source: str
    session_vwap: Decimal | None
    vwap_method: str
    vwap_quality: str
    above_vwap: bool | None
    opening_ranges: OpeningRangeLevels
    bar_status: str
    bar_source: str
    analysis_session_date: str
    requested_primary_provider: str = "UNKNOWN"
    active_quote_provider: str = "UNKNOWN"
    fallback_used: bool = False
    primary_failure_reason: str | None = None
    requested_bar_provider: str = "UNKNOWN"
    active_bar_provider: str = "UNKNOWN"
    bar_fallback_used: bool = False
    bar_primary_failure_reason: str | None = None
    previous_close_provider: str = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class MarketAnalysisInput:
    analysis_id: str
    analysis_started_at: datetime
    source_captured_at: datetime
    analysis_session_date: str
    primary_provider: str
    secondary_provider: str | None
    symbols: tuple[MarketAnalysisSymbolInput, ...]
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    cross_validation_metadata: dict[str, Any] = field(default_factory=dict)
    data_capabilities: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MarketBrainResult:
    analysis_id: str
    analysis_started_at: datetime
    source_captured_at: datetime
    analysis_session_date: str
    analysis_input: MarketAnalysisInput
    snapshot: Any
    cross_validation: dict[str, CrossValidationResult]
    analysis_confidence: AnalysisConfidence
    price_action: dict[str, PriceActionAnalysis]
    structures: dict[str, StructureAnalysis]
    momentum: dict[str, MomentumAnalysis]
    divergences: DivergenceAnalysis
    regime: MarketRegimeAnalysis
    observations: list[MarketObservation]


def format_decimal(value: Decimal, places: int = 2) -> str:
    quantum = Decimal("1").scaleb(-places)
    return format(value.quantize(quantum, rounding=ROUND_HALF_UP), f".{places}f")


def clamp_score(score: int) -> int:
    return max(0, min(100, score))


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def pct_difference(primary: Decimal | None, secondary: Decimal | None) -> Decimal | None:
    if primary is None or secondary is None or primary == 0:
        return None
    return (abs(primary - secondary) / abs(primary)) * Decimal("100")
