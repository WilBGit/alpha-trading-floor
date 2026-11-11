from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class MarketRegimeName(str, Enum):
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


class CatalystCategory(str, Enum):
    FED = "FED"
    ECONOMIC_DATA = "ECONOMIC_DATA"
    EARNINGS = "EARNINGS"
    GEOPOLITICAL = "GEOPOLITICAL"
    ENERGY = "ENERGY"
    SECTOR = "SECTOR"
    COMPANY = "COMPANY"
    REGULATION = "REGULATION"
    OTHER = "OTHER"


class OpportunityStatus(str, Enum):
    DETECTED = "DETECTED"
    GENERATING = "GENERATING"
    GENERATED = "GENERATED"
    REJECTED = "REJECTED"
    POSTED = "POSTED"
    EXPIRED = "EXPIRED"


class GeneratedPostStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    POSTING = "POSTING"
    POSTED = "POSTED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class SystemEventSeverity(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


MARKET_REGIME_TYPE = SAEnum(
    MarketRegimeName,
    name="market_regime_name",
    native_enum=False,
    validate_strings=True,
    create_constraint=True,
)

OPPORTUNITY_STATUS_TYPE = SAEnum(
    OpportunityStatus,
    name="opportunity_status",
    native_enum=False,
    validate_strings=True,
    create_constraint=True,
)

GENERATED_POST_STATUS_TYPE = SAEnum(
    GeneratedPostStatus,
    name="generated_post_status",
    native_enum=False,
    validate_strings=True,
    create_constraint=True,
)

GENERATED_POST_MARKET_REGIME_TYPE = SAEnum(
    MarketRegimeName,
    name="generated_post_market_regime",
    native_enum=False,
    validate_strings=True,
    create_constraint=True,
)

CATALYST_CATEGORY_TYPE = SAEnum(
    CatalystCategory,
    name="catalyst_category",
    native_enum=False,
    validate_strings=True,
    create_constraint=True,
)

SYSTEM_EVENT_SEVERITY_TYPE = SAEnum(
    SystemEventSeverity,
    name="system_event_severity",
    native_enum=False,
    validate_strings=True,
    create_constraint=True,
)


class MarketSnapshot(Base, TimestampMixin):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    spy_price: Mapped[float | None] = mapped_column(Numeric(18, 6))
    spy_change_percent: Mapped[float | None] = mapped_column(Numeric(18, 6))
    qqq_price: Mapped[float | None] = mapped_column(Numeric(18, 6))
    qqq_change_percent: Mapped[float | None] = mapped_column(Numeric(18, 6))
    dia_price: Mapped[float | None] = mapped_column(Numeric(18, 6))
    dia_change_percent: Mapped[float | None] = mapped_column(Numeric(18, 6))
    iwm_price: Mapped[float | None] = mapped_column(Numeric(18, 6))
    iwm_change_percent: Mapped[float | None] = mapped_column(Numeric(18, 6))
    vix_value: Mapped[float | None] = mapped_column(Numeric(18, 6))
    vix_change_percent: Mapped[float | None] = mapped_column(Numeric(18, 6))
    market_breadth: Mapped[float | None] = mapped_column(Numeric(18, 6))
    advancers: Mapped[int | None] = mapped_column(Integer)
    decliners: Mapped[int | None] = mapped_column(Integer)
    up_volume: Mapped[int | None] = mapped_column(Integer)
    down_volume: Mapped[int | None] = mapped_column(Integer)
    raw_data_json: Mapped[dict | None] = mapped_column(JSON)


class TickerSnapshot(Base, TimestampMixin):
    __tablename__ = "ticker_snapshots"
    __table_args__ = (
        Index("ix_ticker_snapshots_symbol", "symbol"),
        Index("ix_ticker_snapshots_snapshot_time", "snapshot_time"),
        Index("ix_ticker_snapshots_symbol_snapshot_time", "symbol", "snapshot_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price: Mapped[float | None] = mapped_column(Numeric(18, 6))
    change_percent: Mapped[float | None] = mapped_column(Numeric(18, 6))
    volume: Mapped[int | None] = mapped_column(Integer)
    relative_volume: Mapped[float | None] = mapped_column(Numeric(18, 6))
    day_high: Mapped[float | None] = mapped_column(Numeric(18, 6))
    day_low: Mapped[float | None] = mapped_column(Numeric(18, 6))
    open_price: Mapped[float | None] = mapped_column(Numeric(18, 6))
    previous_close: Mapped[float | None] = mapped_column(Numeric(18, 6))
    vwap: Mapped[float | None] = mapped_column(Numeric(18, 6))
    rsi: Mapped[float | None] = mapped_column(Numeric(18, 6))
    ema_9: Mapped[float | None] = mapped_column(Numeric(18, 6))
    ema_20: Mapped[float | None] = mapped_column(Numeric(18, 6))
    ema_50: Mapped[float | None] = mapped_column(Numeric(18, 6))
    distance_from_vwap: Mapped[float | None] = mapped_column(Numeric(18, 6))
    raw_data_json: Mapped[dict | None] = mapped_column(JSON)


class MarketRegime(Base, TimestampMixin):
    __tablename__ = "market_regimes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    regime: Mapped[MarketRegimeName] = mapped_column(MARKET_REGIME_TYPE, nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_data_json: Mapped[dict | None] = mapped_column(JSON)


class PriceActionEventRecord(Base, TimestampMixin):
    __tablename__ = "price_action_events"
    __table_args__ = (
        Index("ix_price_action_events_symbol", "symbol"),
        Index("ix_price_action_events_event_type", "event_type"),
        Index("ix_price_action_events_detected_at", "detected_at"),
        Index("ix_price_action_events_session_date", "session_date"),
        Index("ix_price_action_events_symbol_detected_at", "symbol", "detected_at"),
        UniqueConstraint("event_id", name="uq_price_action_events_event_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(192), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(32), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    importance: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_json: Mapped[list | None] = mapped_column(JSON)
    supporting_values_json: Mapped[dict | None] = mapped_column(JSON)
    session_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MarketObservationRecord(Base, TimestampMixin):
    __tablename__ = "market_observations"
    __table_args__ = (
        Index("ix_market_observations_observation_type", "observation_type"),
        Index("ix_market_observations_detected_at", "detected_at"),
        Index("ix_market_observations_session_date", "session_date"),
        Index("ix_market_observations_importance_score", "importance_score"),
        Index("ix_market_observations_observation_type_detected_at", "observation_type", "detected_at"),
        UniqueConstraint("observation_key", name="uq_market_observations_observation_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    observation_key: Mapped[str] = mapped_column(String(192), nullable=False)
    observation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    symbols_json: Mapped[list | None] = mapped_column(JSON)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    session_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    importance_score: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence_score: Mapped[int] = mapped_column(Integer, nullable=False)
    facts_json: Mapped[list | None] = mapped_column(JSON)
    interpretation_boundary: Mapped[str] = mapped_column(Text, nullable=False)
    source_events_json: Mapped[list | None] = mapped_column(JSON)


class MarketCatalyst(Base, TimestampMixin):
    __tablename__ = "market_catalysts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    headline: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1024))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    symbols_json: Mapped[list | None] = mapped_column(JSON)
    category: Mapped[CatalystCategory] = mapped_column(CATALYST_CATEGORY_TYPE, nullable=False)
    impact_score: Mapped[int] = mapped_column(Integer, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)


class ContentOpportunity(Base, TimestampMixin):
    __tablename__ = "content_opportunities"
    __table_args__ = (
        Index("ix_content_opportunities_status", "status"),
        Index("ix_content_opportunities_analysis_id", "analysis_id"),
        Index("ix_content_opportunities_content_pattern_key", "content_pattern_key"),
        Index("ix_content_opportunities_decision", "decision"),
        UniqueConstraint("opportunity_id", name="uq_content_opportunities_opportunity_id"),
        UniqueConstraint("opportunity_key", name="uq_content_opportunities_opportunity_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    opportunity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    symbols_json: Mapped[list | None] = mapped_column(JSON)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    observation: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_data_json: Mapped[dict | None] = mapped_column(JSON)
    importance_score: Mapped[int] = mapped_column(Integer, nullable=False)
    time_sensitivity_score: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[OpportunityStatus] = mapped_column(OPPORTUNITY_STATUS_TYPE, nullable=False)
    opportunity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    opportunity_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    analysis_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    primary_symbol: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_regime: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_regime_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_event_ids_json: Mapped[list | None] = mapped_column(JSON)
    source_observation_keys_json: Mapped[list | None] = mapped_column(JSON)
    source_divergence_types_json: Mapped[list | None] = mapped_column(JSON)
    facts_json: Mapped[dict | None] = mapped_column(JSON)
    interpretation_boundary: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    freshness_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    novelty_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_score: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    confirmation_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decision_reasons_json: Mapped[list | None] = mapped_column(JSON)
    cooldown_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content_pattern_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    material_change: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    generated_posts = relationship("GeneratedPost", back_populates="source_opportunity")


class GeneratedPost(Base, TimestampMixin):
    __tablename__ = "generated_posts"
    __table_args__ = (
        Index("ix_generated_posts_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    post_type: Mapped[str] = mapped_column(String(64), nullable=False)
    format_tag: Mapped[str | None] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    normalized_hash: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    symbols_json: Mapped[list | None] = mapped_column(JSON)
    market_regime: Mapped[str | None] = mapped_column(GENERATED_POST_MARKET_REGIME_TYPE)
    source_opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("content_opportunities.id"))
    ai_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    ai_model: Mapped[str] = mapped_column(String(128), nullable=False)
    fact_confidence_score: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_score: Mapped[int] = mapped_column(Integer, nullable=False)
    duplicate_score: Mapped[int] = mapped_column(Integer, nullable=False)
    safety_status: Mapped[str] = mapped_column(String(32), nullable=False)
    validation_json: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[GeneratedPostStatus] = mapped_column(GENERATED_POST_STATUS_TYPE, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(256))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    source_opportunity = relationship("ContentOpportunity", back_populates="generated_posts")
    published_record = relationship("PublishedPost", back_populates="generated_post", uselist=False)


class GeneratedContentDraft(Base, TimestampMixin):
    __tablename__ = "generated_content_drafts"
    __table_args__ = (
        Index("ix_generated_content_drafts_draft_request_id", "draft_request_id"),
        Index("ix_generated_content_drafts_analysis_id", "analysis_id"),
        Index("ix_generated_content_drafts_opportunity_id", "opportunity_id"),
        Index("ix_generated_content_drafts_opportunity_key", "opportunity_key"),
        Index("ix_generated_content_drafts_generated_at", "generated_at"),
        Index("ix_generated_content_drafts_final_decision", "final_decision"),
        UniqueConstraint("draft_id", name="uq_generated_content_drafts_draft_id"),
        UniqueConstraint("content_hash", name="uq_generated_content_drafts_content_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_id: Mapped[str] = mapped_column(String(128), nullable=False)
    opportunity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    opportunity_key: Mapped[str] = mapped_column(String(128), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    voice_profile: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    character_count: Mapped[int] = mapped_column(Integer, nullable=False)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    validation_failures_json: Mapped[list | None] = mapped_column(JSON)
    factual_claims_json: Mapped[list | None] = mapped_column(JSON)
    numeric_claims_json: Mapped[list | None] = mapped_column(JSON)
    symbol_claims_json: Mapped[list | None] = mapped_column(JSON)
    quality_score: Mapped[int] = mapped_column(Integer, nullable=False)
    final_decision: Mapped[str] = mapped_column(String(32), nullable=False)
    final_decision_reasons_json: Mapped[list | None] = mapped_column(JSON)


class PublicationRecord(Base, TimestampMixin):
    __tablename__ = "publication_records"
    __table_args__ = (
        Index("ix_publication_records_draft_id", "draft_id"),
        Index("ix_publication_records_opportunity_id", "opportunity_id"),
        Index("ix_publication_records_created_at", "created_at"),
        Index("ix_publication_records_state", "state"),
        UniqueConstraint("idempotency_key", name="uq_publication_records_idempotency_key"),
        UniqueConstraint("platform_post_id", name="uq_publication_records_platform_post_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    publication_request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    analysis_id: Mapped[str] = mapped_column(String(128), nullable=False)
    opportunity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    opportunity_key: Mapped[str] = mapped_column(String(128), nullable=False)
    draft_id: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    content_pattern_key: Mapped[str | None] = mapped_column(String(128))
    publish_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    approval_status: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_reasons_json: Mapped[list | None] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider: Mapped[str | None] = mapped_column(String(64))
    provider_request_id: Mapped[str | None] = mapped_column(String(128))
    platform_post_id: Mapped[str | None] = mapped_column(String(128))
    platform_response_status: Mapped[int | None] = mapped_column(Integer)
    retryable: Mapped[bool | None] = mapped_column(Boolean)
    error_category: Mapped[str | None] = mapped_column(String(64))
    sanitized_error: Mapped[str | None] = mapped_column(String(512))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PublicationReconciliation(Base, TimestampMixin):
    __tablename__ = "publication_reconciliations"
    __table_args__ = (
        UniqueConstraint("publication_request_id", name="uq_publication_reconciliations_request_id"),
        Index("ix_publication_reconciliations_state", "reconciliation_state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    publication_request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    original_publication_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reconciliation_state: Mapped[str] = mapped_column(String(48), nullable=False)
    platform_post_found: Mapped[bool] = mapped_column(Boolean, nullable=False)
    platform_post_id: Mapped[str | None] = mapped_column(String(128))
    matched_content_hash: Mapped[str | None] = mapped_column(String(128))
    matched_content_text: Mapped[str | None] = mapped_column(Text)
    matched_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    match_method: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reconciled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(128))
    error_category: Mapped[str | None] = mapped_column(String(64))
    sanitized_error: Mapped[str | None] = mapped_column(String(512))
    warnings_json: Mapped[list | None] = mapped_column(JSON)


class PublicationJob(Base, TimestampMixin):
    __tablename__ = "publication_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_publication_jobs_idempotency_key"),
        Index("ix_publication_jobs_status", "status"),
        Index("ix_publication_jobs_not_before", "not_before"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(512))
    analytics_window: Mapped[str | None] = mapped_column(String(16))


class ProductionCycle(Base, TimestampMixin):
    __tablename__ = "production_cycles"
    __table_args__ = (
        UniqueConstraint("cycle_id", name="uq_production_cycles_cycle_id"),
        Index("ix_production_cycles_session_date", "session_date"),
        Index("ix_production_cycles_started_at", "started_at"),
        Index("ix_production_cycles_outcome", "outcome"),
        Index("ix_production_cycles_publication_request_id", "publication_request_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cycle_id: Mapped[str] = mapped_column(String(64), nullable=False)
    runner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    analysis_id: Mapped[str | None] = mapped_column(String(128))
    session_date: Mapped[str] = mapped_column(String(16), nullable=False)
    operating_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    market_state: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    analysis_confidence: Mapped[int | None] = mapped_column(Integer)
    opportunity_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    draft_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approved_draft_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    publication_decision: Mapped[str | None] = mapped_column(String(64))
    publication_request_id: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(64))
    error_message_sanitized: Mapped[str | None] = mapped_column(String(512))


class PublicationHold(Base, TimestampMixin):
    __tablename__ = "publication_holds"
    __table_args__ = (UniqueConstraint("hold_name", name="uq_publication_holds_hold_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hold_name: Mapped[str] = mapped_column(String(64), nullable=False)
    hold_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(128))
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SystemLease(Base, TimestampMixin):
    __tablename__ = "system_leases"
    __table_args__ = (UniqueConstraint("lease_name", name="uq_system_leases_lease_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lease_name: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    host: Mapped[str] = mapped_column(String(256), nullable=False)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PostMetricsSnapshot(Base, TimestampMixin):
    __tablename__ = "post_metrics_snapshots"
    __table_args__ = (
        UniqueConstraint("capture_key", name="uq_post_metrics_snapshots_capture_key"),
        Index("ix_post_metrics_snapshots_platform_post_id", "platform_post_id"),
        Index("ix_post_metrics_snapshots_captured_at", "captured_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    capture_key: Mapped[str] = mapped_column(String(192), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    platform_post_id: Mapped[str] = mapped_column(String(128), nullable=False)
    publication_request_id: Mapped[str | None] = mapped_column(String(64))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    analytics_window: Mapped[str] = mapped_column(String(16), nullable=False)
    impressions: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    replies: Mapped[int | None] = mapped_column(Integer)
    reposts: Mapped[int | None] = mapped_column(Integer)
    bookmarks: Mapped[int | None] = mapped_column(Integer)
    quote_posts: Mapped[int | None] = mapped_column(Integer)
    profile_clicks: Mapped[int | None] = mapped_column(Integer)
    url_clicks: Mapped[int | None] = mapped_column(Integer)
    engagement_rate: Mapped[float | None] = mapped_column(Numeric(12, 6))
    source_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_payload_version: Mapped[str | None] = mapped_column(String(32))
    availability_flags_json: Mapped[dict | None] = mapped_column(JSON)


class PublishedPost(Base, TimestampMixin):
    __tablename__ = "published_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    generated_post_id: Mapped[int] = mapped_column(ForeignKey("generated_posts.id"), unique=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    platform_post_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    impressions: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    replies: Mapped[int | None] = mapped_column(Integer)
    reposts: Mapped[int | None] = mapped_column(Integer)
    bookmarks: Mapped[int | None] = mapped_column(Integer)
    engagement_rate: Mapped[float | None] = mapped_column(Numeric(18, 6))
    last_metrics_update: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    generated_post = relationship("GeneratedPost", back_populates="published_record")


class ContentPattern(Base, TimestampMixin):
    __tablename__ = "content_patterns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pattern_name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    voice: Mapped[str] = mapped_column(String(64), nullable=False)
    post_type: Mapped[str] = mapped_column(String(64), nullable=False)
    template_structure: Mapped[str] = mapped_column(Text, nullable=False)
    times_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    average_impressions: Mapped[float | None] = mapped_column(Numeric(18, 6))
    average_engagement_rate: Mapped[float | None] = mapped_column(Numeric(18, 6))
    average_replies: Mapped[float | None] = mapped_column(Numeric(18, 6))
    performance_score: Mapped[float | None] = mapped_column(Numeric(18, 6))


class SystemEvent(Base, TimestampMixin):
    __tablename__ = "system_events"
    __table_args__ = (
        Index("ix_system_events_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[SystemEventSeverity] = mapped_column(SYSTEM_EVENT_SEVERITY_TYPE, nullable=False)
    component: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)


PriceActionEvent = PriceActionEventRecord
MarketObservation = MarketObservationRecord
