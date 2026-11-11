from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Generic, TypeVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .base import Base
from .models import ContentOpportunity as ContentOpportunityRecord, GeneratedContentDraft as GeneratedContentDraftRecord, PublicationRecord, PublicationReconciliation, PublicationJob, ProductionCycle, PublicationHold, SystemLease, PostMetricsSnapshot, MarketObservation, MarketRegime, MarketSnapshot, PriceActionEvent, SystemEvent, SystemEventSeverity, OpportunityStatus
from ..market.brain_types import EventLifecycle

ModelT = TypeVar("ModelT", bound=Base)


class Repository(Generic[ModelT]):
    def __init__(self, session: Session):
        self.session = session

    def add(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        return obj

    def commit(self) -> None:
        self.session.commit()

    def refresh(self, obj: ModelT) -> ModelT:
        self.session.refresh(obj)
        if isinstance(obj, PriceActionEvent):
            return self._attach_utc(obj, ("detected_at", "session_date", "resolved_at", "created_at", "updated_at"))
        if isinstance(obj, MarketObservation):
            return self._attach_utc(obj, ("detected_at", "session_date", "created_at", "updated_at"))
        if isinstance(obj, MarketRegime):
            return self._attach_utc(obj, ("detected_at", "created_at", "updated_at"))
        if isinstance(obj, MarketSnapshot):
            return self._attach_utc(obj, ("snapshot_time", "created_at", "updated_at"))
        if isinstance(obj, SystemEvent):
            return self._attach_utc(obj, ("created_at",))
        if isinstance(obj, ContentOpportunityRecord):
            return self._attach_utc(obj, ("detected_at", "expires_at", "created_at", "updated_at"))
        if isinstance(obj, GeneratedContentDraftRecord):
            return self._attach_utc(obj, ("generated_at", "created_at", "updated_at"))
        if isinstance(obj, PublicationRecord):
            return self._attach_utc(obj, ("requested_at", "attempted_at", "published_at", "source_detected_at", "created_at", "updated_at"))
        return obj

    def get(self, model: type[ModelT], object_id: int) -> ModelT | None:
        return self.session.get(model, object_id)

    def list_all(self, model: type[ModelT]) -> list[ModelT]:
        return list(self.session.query(model).all())

    def _json_safe(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, datetime):
            return self._ensure_utc(value).isoformat()
        if isinstance(value, Enum):
            return value.value
        if is_dataclass(value) and not isinstance(value, type):
            return self._json_safe(asdict(value))
        if isinstance(value, dict):
            return {str(key): self._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._json_safe(item) for item in value]
        return value

    def _ensure_utc(self, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone-aware datetime required")
        return value.astimezone(UTC)

    def _utc_or_now(self, value: datetime | None = None) -> datetime:
        return self._ensure_utc(value) if value is not None else datetime.now(UTC)

    def _attach_utc(self, record: Any, fields: tuple[str, ...]) -> Any:
        for field in fields:
            value = getattr(record, field, None)
            if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
                setattr(record, field, value.replace(tzinfo=UTC))
        return record

    def _normalize_price_action_lifecycle(self, lifecycle: str | EventLifecycle) -> str:
        if isinstance(lifecycle, EventLifecycle):
            return lifecycle.value
        try:
            return EventLifecycle(lifecycle).value
        except ValueError as exc:
            raise ValueError(f"invalid price action lifecycle: {lifecycle}") from exc

    def _validate_score(self, name: str, value: int) -> int:
        if not 0 <= int(value) <= 100:
            raise ValueError(f"{name} must be between 0 and 100")
        return int(value)

    def _allowed_price_action_transitions(self) -> dict[str, set[str]]:
        return {
            EventLifecycle.DETECTED.value: {EventLifecycle.CONFIRMED.value, EventLifecycle.FAILED.value, EventLifecycle.EXPIRED.value},
            EventLifecycle.CONFIRMED.value: {EventLifecycle.RESOLVED.value, EventLifecycle.FAILED.value, EventLifecycle.EXPIRED.value},
            EventLifecycle.FAILED.value: set(),
            EventLifecycle.RESOLVED.value: set(),
            EventLifecycle.EXPIRED.value: set(),
        }

    def _assert_price_action_transition(self, current: str, next_lifecycle: str) -> None:
        allowed = self._allowed_price_action_transitions().get(current, set())
        if next_lifecycle not in allowed:
            raise ValueError(f"invalid price action lifecycle transition: {current} -> {next_lifecycle}")

    def _ensure_price_action_session_date(self, detected_at: datetime, session_date: datetime | None) -> datetime:
        return self._utc_or_now(session_date or detected_at)

    def _ensure_observation_session_date(self, detected_at: datetime, session_date: datetime | None) -> datetime:
        return self._utc_or_now(session_date or detected_at)

    def _duplicate_lookup(self, model: type[ModelT], **filters: Any) -> ModelT | None:
        return self.session.query(model).filter_by(**filters).one_or_none()

    async def save_market_snapshot(self, snapshot: Any) -> MarketSnapshot:
        record = MarketSnapshot(
            snapshot_time=self._utc_or_now(snapshot.snapshot_time),
            spy_price=snapshot.spy.price,
            spy_change_percent=snapshot.spy.change_percent,
            qqq_price=snapshot.qqq.price,
            qqq_change_percent=snapshot.qqq.change_percent,
            dia_price=snapshot.dia.price,
            dia_change_percent=snapshot.dia.change_percent,
            iwm_price=snapshot.iwm.price,
            iwm_change_percent=snapshot.iwm.change_percent,
            vix_value=snapshot.vix.price if snapshot.vix else None,
            vix_change_percent=snapshot.vix.change_percent if snapshot.vix else None,
            market_breadth=snapshot.market_breadth.breadth_score if snapshot.market_breadth and snapshot.market_breadth.breadth_score is not None else None,
            advancers=snapshot.market_breadth.advancers if snapshot.market_breadth else None,
            decliners=snapshot.market_breadth.decliners if snapshot.market_breadth else None,
            up_volume=snapshot.market_breadth.up_volume if snapshot.market_breadth else None,
            down_volume=snapshot.market_breadth.down_volume if snapshot.market_breadth else None,
            raw_data_json=self._json_safe(snapshot.raw_data),
        )
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return self._attach_utc(record, ("snapshot_time",))

    async def save_price_action_event(
        self,
        *,
        event_id: str,
        symbol: str,
        event_type: str,
        lifecycle: str | EventLifecycle,
        detected_at,
        confidence: int,
        importance: int,
        evidence: list[Any] | None = None,
        supporting_values: dict[str, Any] | None = None,
        session_date=None,
        resolved_at=None,
    ) -> PriceActionEvent:
        lifecycle_value = self._normalize_price_action_lifecycle(lifecycle)
        confidence_value = self._validate_score("confidence", confidence)
        importance_value = self._validate_score("importance", importance)
        detected_utc = self._ensure_utc(detected_at)
        session_utc = self._ensure_price_action_session_date(detected_at, session_date)
        resolved_utc = self._utc_or_now(resolved_at) if resolved_at is not None else None
        existing = self.get_price_action_event_by_event_id(event_id)
        if existing is not None:
            return existing
        record = PriceActionEvent(
            event_id=event_id,
            symbol=symbol,
            event_type=event_type,
            lifecycle=lifecycle_value,
            detected_at=detected_utc,
            confidence=confidence_value,
            importance=importance_value,
            evidence_json=self._json_safe(evidence or []),
            supporting_values_json=self._json_safe(supporting_values or {}),
            session_date=session_utc,
            resolved_at=resolved_utc,
        )
        self.session.add(record)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.get_price_action_event_by_event_id(event_id)
            if existing is None:
                raise
            return existing
        self.session.refresh(record)
        return self._attach_utc(record, ("detected_at", "session_date", "resolved_at", "created_at", "updated_at"))

    def get_price_action_event_by_event_id(self, event_id: str) -> PriceActionEvent | None:
        record = self.session.query(PriceActionEvent).filter_by(event_id=event_id).one_or_none()
        return self._attach_utc(record, ("detected_at", "session_date", "resolved_at", "created_at", "updated_at")) if record is not None else None

    def get_recent_price_action_events(
        self,
        limit: int = 10,
        *,
        symbol: str | None = None,
        event_type: str | None = None,
    ) -> list[PriceActionEvent]:
        query = self.session.query(PriceActionEvent)
        if symbol is not None:
            query = query.filter(PriceActionEvent.symbol == symbol)
        if event_type is not None:
            query = query.filter(PriceActionEvent.event_type == event_type)
        records = query.order_by(PriceActionEvent.detected_at.desc(), PriceActionEvent.id.desc()).limit(limit).all()
        return [self._attach_utc(record, ("detected_at", "session_date", "resolved_at", "created_at", "updated_at")) for record in records]

    def get_active_price_action_event(self, symbol: str, event_type: str) -> PriceActionEvent | None:
        terminal = {EventLifecycle.FAILED.value, EventLifecycle.RESOLVED.value, EventLifecycle.EXPIRED.value}
        record = (
            self.session.query(PriceActionEvent)
            .filter(
                PriceActionEvent.symbol == symbol,
                PriceActionEvent.event_type == event_type,
                ~PriceActionEvent.lifecycle.in_(terminal),
            )
            .order_by(PriceActionEvent.detected_at.desc(), PriceActionEvent.id.desc())
            .first()
        )
        return self._attach_utc(record, ("detected_at", "session_date", "resolved_at", "created_at", "updated_at")) if record is not None else None

    def update_price_action_event_lifecycle(
        self,
        event_id: str,
        lifecycle: str | EventLifecycle,
        *,
        resolved_at: datetime | None = None,
    ) -> PriceActionEvent:
        record = self.get_price_action_event_by_event_id(event_id)
        if record is None:
            raise ValueError(f"price action event not found: {event_id}")
        next_lifecycle = self._normalize_price_action_lifecycle(lifecycle)
        self._assert_price_action_transition(record.lifecycle, next_lifecycle)
        record.lifecycle = next_lifecycle
        if next_lifecycle in {EventLifecycle.RESOLVED.value, EventLifecycle.FAILED.value, EventLifecycle.EXPIRED.value}:
            record.resolved_at = self._utc_or_now(resolved_at)
        self.session.commit()
        self.session.refresh(record)
        return self._attach_utc(record, ("detected_at", "session_date", "resolved_at", "created_at", "updated_at"))

    def resolve_price_action_event(self, event_id: str, *, resolved_at: datetime | None = None) -> PriceActionEvent:
        return self.update_price_action_event_lifecycle(event_id, EventLifecycle.RESOLVED, resolved_at=resolved_at)

    async def save_market_observation(
        self,
        *,
        observation_key: str,
        observation_type: str,
        symbols: list[str] | None,
        detected_at,
        importance_score: int,
        confidence_score: int,
        facts: list[Any] | None,
        interpretation_boundary: str,
        source_events: list[str] | None = None,
        session_date=None,
    ) -> MarketObservation:
        importance_value = self._validate_score("importance_score", importance_score)
        confidence_value = self._validate_score("confidence_score", confidence_score)
        detected_utc = self._ensure_utc(detected_at)
        session_utc = self._ensure_observation_session_date(detected_at, session_date)
        existing = self.get_market_observation_by_key(observation_key)
        if existing is not None:
            existing.observation_type = observation_type
            existing.symbols_json = self._json_safe(symbols or [])
            existing.detected_at = detected_utc
            existing.session_date = session_utc
            existing.importance_score = importance_value
            existing.confidence_score = confidence_value
            existing.facts_json = self._json_safe(facts or [])
            existing.interpretation_boundary = interpretation_boundary
            existing.source_events_json = self._json_safe(source_events or [])
            self.session.commit()
            self.session.refresh(existing)
            return self._attach_utc(existing, ("detected_at", "session_date", "created_at", "updated_at"))
        record = MarketObservation(
            observation_key=observation_key,
            observation_type=observation_type,
            symbols_json=self._json_safe(symbols or []),
            detected_at=detected_utc,
            session_date=session_utc,
            importance_score=importance_value,
            confidence_score=confidence_value,
            facts_json=self._json_safe(facts or []),
            interpretation_boundary=interpretation_boundary,
            source_events_json=self._json_safe(source_events or []),
        )
        self.session.add(record)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.get_market_observation_by_key(observation_key)
            if existing is None:
                raise
            return existing
        self.session.refresh(record)
        return self._attach_utc(record, ("detected_at", "session_date", "created_at", "updated_at"))

    def get_market_observation_by_key(self, observation_key: str) -> MarketObservation | None:
        record = self.session.query(MarketObservation).filter_by(observation_key=observation_key).one_or_none()
        return self._attach_utc(record, ("detected_at", "session_date", "created_at", "updated_at")) if record is not None else None

    def get_recent_market_observations(self, limit: int = 10) -> list[MarketObservation]:
        records = self.session.query(MarketObservation).order_by(MarketObservation.detected_at.desc(), MarketObservation.id.desc()).limit(limit).all()
        return [self._attach_utc(record, ("detected_at", "session_date", "created_at", "updated_at")) for record in records]

    def get_market_observations_by_type(self, observation_type: str, limit: int = 10) -> list[MarketObservation]:
        records = (
            self.session.query(MarketObservation)
            .filter(MarketObservation.observation_type == observation_type)
            .order_by(MarketObservation.detected_at.desc(), MarketObservation.id.desc())
            .limit(limit)
            .all()
        )
        return [self._attach_utc(record, ("detected_at", "session_date", "created_at", "updated_at")) for record in records]

    def get_high_importance_market_observations(self, minimum_importance: int = 80, limit: int = 10) -> list[MarketObservation]:
        records = (
            self.session.query(MarketObservation)
            .filter(MarketObservation.importance_score >= minimum_importance)
            .order_by(MarketObservation.importance_score.desc(), MarketObservation.detected_at.desc(), MarketObservation.id.desc())
            .limit(limit)
            .all()
        )
        return [self._attach_utc(record, ("detected_at", "session_date", "created_at", "updated_at")) for record in records]

    def get_content_opportunity_by_key(self, opportunity_key: str):
        record = self.session.query(ContentOpportunityRecord).filter_by(opportunity_key=opportunity_key).one_or_none()
        return self._attach_utc(record, ("detected_at", "expires_at", "created_at", "updated_at")) if record is not None else None

    def get_content_opportunity_by_id(self, opportunity_id: str):
        record = self.session.query(ContentOpportunityRecord).filter_by(opportunity_id=opportunity_id).one_or_none()
        return self._attach_utc(record, ("detected_at", "expires_at", "created_at", "updated_at")) if record is not None else None

    def get_active_content_opportunity(self, opportunity_key: str):
        record = self.get_content_opportunity_by_key(opportunity_key)
        if record is None or record.decision in {"SKIP", "SUPPRESS_DUPLICATE"}:
            return None
        return record

    def get_recent_content_opportunities(self, limit: int = 50):
        records = self.session.query(ContentOpportunityRecord).order_by(ContentOpportunityRecord.detected_at.desc(), ContentOpportunityRecord.id.desc()).limit(limit).all()
        return [self._attach_utc(record, ("detected_at", "expires_at", "created_at", "updated_at")) for record in records]

    def get_content_opportunities_by_pattern(self, content_pattern_key: str, limit: int = 50):
        records = self.session.query(ContentOpportunityRecord).filter_by(content_pattern_key=content_pattern_key).order_by(ContentOpportunityRecord.detected_at.desc(), ContentOpportunityRecord.id.desc()).limit(limit).all()
        return [self._attach_utc(record, ("detected_at", "expires_at", "created_at", "updated_at")) for record in records]

    def get_recent_selected_content_opportunities(self, limit: int = 50):
        records = self.session.query(ContentOpportunityRecord).filter_by(decision="POST_CANDIDATE").order_by(ContentOpportunityRecord.detected_at.desc(), ContentOpportunityRecord.id.desc()).limit(limit).all()
        return [self._attach_utc(record, ("detected_at", "expires_at", "created_at", "updated_at")) for record in records]

    def upsert_content_opportunity(self, opportunity):
        existing = self.get_content_opportunity_by_key(opportunity.opportunity_key)
        values = {
            "detected_at": self._ensure_utc(opportunity.detected_at),
            "opportunity_type": opportunity.opportunity_type.value,
            "symbols_json": self._json_safe(list(opportunity.symbols)),
            "title": opportunity.opportunity_type.value,
            "observation": opportunity.interpretation_boundary,
            "supporting_data_json": self._json_safe(opportunity.facts),
            "importance_score": self._validate_score("importance_score", opportunity.importance_score),
            "time_sensitivity_score": self._validate_score("time_sensitivity_score", opportunity.freshness_score),
            "status": OpportunityStatus.DETECTED,
            "opportunity_id": opportunity.opportunity_id,
            "opportunity_key": opportunity.opportunity_key,
            "analysis_id": opportunity.analysis_id,
            "primary_symbol": opportunity.primary_symbol,
            "source_regime": opportunity.source_regime,
            "source_regime_confidence": opportunity.source_regime_confidence,
            "source_event_ids_json": self._json_safe(list(opportunity.source_event_ids)),
            "source_observation_keys_json": self._json_safe(list(opportunity.source_observation_keys)),
            "source_divergence_types_json": self._json_safe(list(opportunity.source_divergence_types)),
            "facts_json": self._json_safe(opportunity.facts),
            "interpretation_boundary": opportunity.interpretation_boundary,
            "confidence_score": opportunity.confidence_score,
            "freshness_score": opportunity.freshness_score,
            "novelty_score": opportunity.novelty_score,
            "content_score": opportunity.content_score,
            "confirmation_state": opportunity.confirmation_state.value,
            "decision": opportunity.decision.value,
            "decision_reasons_json": self._json_safe([reason.value for reason in opportunity.decision_reasons]),
            "cooldown_key": opportunity.cooldown_key,
            "content_pattern_key": opportunity.content_pattern_key,
            "expires_at": self._utc_or_now(opportunity.expires_at) if opportunity.expires_at else None,
            "material_change": opportunity.material_change,
        }
        if existing is None:
            record = ContentOpportunityRecord(**values)
            self.session.add(record)
        else:
            if existing.decision == "POST_CANDIDATE" and values["decision"] == "SUPPRESS_DUPLICATE":
                values["decision"] = existing.decision
                values["decision_reasons_json"] = existing.decision_reasons_json or values["decision_reasons_json"]
            for key, value in values.items():
                setattr(existing, key, value)
            record = existing
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            record = self.get_content_opportunity_by_key(opportunity.opportunity_key)
            if record is None:
                raise
            return record
        self.session.refresh(record)
        return self._attach_utc(record, ("detected_at", "expires_at", "created_at", "updated_at"))

    def update_content_opportunity_decision(self, opportunity_key: str, decision: str, reasons: list[str] | None = None):
        record = self.get_content_opportunity_by_key(opportunity_key)
        if record is None:
            raise ValueError(f"content opportunity not found: {opportunity_key}")
        record.decision = decision
        record.decision_reasons_json = reasons or []
        self.session.commit()
        self.session.refresh(record)
        return self._attach_utc(record, ("detected_at", "expires_at", "created_at", "updated_at"))

    def update_content_opportunity_confirmation(self, opportunity_key: str, confirmation_state: str):
        record = self.get_content_opportunity_by_key(opportunity_key)
        if record is None:
            raise ValueError(f"content opportunity not found: {opportunity_key}")
        record.confirmation_state = confirmation_state
        self.session.commit()
        self.session.refresh(record)
        return self._attach_utc(record, ("detected_at", "expires_at", "created_at", "updated_at"))

    def resolve_content_opportunity(self, opportunity_key: str):
        return self.update_content_opportunity_confirmation(opportunity_key, "RESOLVED")

    def expire_content_opportunity(self, opportunity_key: str):
        return self.update_content_opportunity_confirmation(opportunity_key, "EXPIRED")

    def create_generated_content_draft(self, draft):
        existing = self.get_generated_content_draft_by_hash(draft.content_hash)
        if existing is not None:
            return existing
        record = GeneratedContentDraftRecord(
            draft_id=draft.draft_id, draft_request_id=draft.draft_request_id, analysis_id=draft.analysis_id,
            opportunity_id=draft.opportunity_id, opportunity_key=draft.opportunity_key, platform=draft.platform,
            content_text=draft.content_text, content_hash=draft.content_hash, provider=draft.provider, model=draft.model,
            prompt_version=draft.prompt_version, voice_profile=draft.voice_profile, draft_attempt=draft.draft_attempt,
            generated_at=self._ensure_utc(draft.generated_at), character_count=draft.character_count,
            validation_status=draft.validation_status, validation_failures_json=self._json_safe(list(draft.validation_failures)),
            factual_claims_json=self._json_safe(list(draft.factual_claims)), numeric_claims_json=self._json_safe(list(draft.numeric_claims)),
            symbol_claims_json=self._json_safe(list(draft.symbol_claims)), quality_score=self._validate_score("quality_score", draft.quality_score),
            final_decision=draft.final_decision, final_decision_reasons_json=self._json_safe(list(draft.final_decision_reasons)),
        )
        self.session.add(record)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.get_generated_content_draft_by_hash(draft.content_hash)
            if existing is None:
                raise
            return existing
        self.session.refresh(record)
        return self._attach_utc(record, ("generated_at", "created_at", "updated_at"))

    def get_generated_content_draft_by_id(self, draft_id: str):
        record = self.session.query(GeneratedContentDraftRecord).filter_by(draft_id=draft_id).one_or_none()
        return self._attach_utc(record, ("generated_at", "created_at", "updated_at")) if record is not None else None

    def get_generated_content_draft_by_hash(self, content_hash: str):
        record = self.session.query(GeneratedContentDraftRecord).filter_by(content_hash=content_hash).one_or_none()
        return self._attach_utc(record, ("generated_at", "created_at", "updated_at")) if record is not None else None

    def get_recent_generated_drafts(self, limit: int = 50):
        records = self.session.query(GeneratedContentDraftRecord).order_by(GeneratedContentDraftRecord.generated_at.desc(), GeneratedContentDraftRecord.id.desc()).limit(limit).all()
        return [self._attach_utc(record, ("generated_at", "created_at", "updated_at")) for record in records]

    def get_recent_approved_drafts(self, limit: int = 50):
        records = self.session.query(GeneratedContentDraftRecord).filter_by(final_decision="APPROVE").order_by(GeneratedContentDraftRecord.generated_at.desc(), GeneratedContentDraftRecord.id.desc()).limit(limit).all()
        return [self._attach_utc(record, ("generated_at", "created_at", "updated_at")) for record in records]

    def get_generated_drafts_by_opportunity(self, opportunity_id: str, limit: int = 50):
        records = self.session.query(GeneratedContentDraftRecord).filter_by(opportunity_id=opportunity_id).order_by(GeneratedContentDraftRecord.generated_at.desc(), GeneratedContentDraftRecord.id.desc()).limit(limit).all()
        return [self._attach_utc(record, ("generated_at", "created_at", "updated_at")) for record in records]

    def create_publication_record(self, request, *, eligibility=None, state="PENDING"):
        existing = self.get_publication_by_idempotency_key(request.idempotency_key)
        if existing is not None:
            return existing
        record = PublicationRecord(
            publication_request_id=request.publication_request_id, idempotency_key=request.idempotency_key,
            analysis_id=request.analysis_id, opportunity_id=request.opportunity_id, opportunity_key=request.opportunity_key,
            draft_id=request.draft_id, draft_request_id=request.draft_request_id, platform=request.platform,
            content_text=request.content_text, content_hash=request.content_hash, content_pattern_key=request.content_pattern_key,
            publish_mode=request.publish_mode.value, approval_status=request.approval_status,
            decision=eligibility.decision.value if eligibility is not None else "PENDING",
            decision_reasons_json=self._json_safe(list(eligibility.reasons)) if eligibility is not None else [], state=state,
            attempt_number=0, requested_at=self._ensure_utc(request.requested_at), source_detected_at=self._ensure_utc(request.source_detected_at),
        )
        self.session.add(record)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.get_publication_by_idempotency_key(request.idempotency_key)
            if existing is None:
                raise
            return existing
        self.session.refresh(record)
        return self._attach_utc(record, ("requested_at", "source_detected_at", "created_at", "updated_at"))

    def get_publication_by_request_id(self, request_id: str):
        record = self.session.query(PublicationRecord).filter_by(publication_request_id=request_id).one_or_none()
        return self._attach_utc(record, ("requested_at", "attempted_at", "published_at", "source_detected_at", "created_at", "updated_at")) if record is not None else None

    def get_publication_by_platform_post_id(self, platform_post_id: str):
        record = self.session.query(PublicationRecord).filter_by(platform_post_id=platform_post_id).order_by(PublicationRecord.created_at.desc()).first()
        return self._attach_utc(record, ("requested_at", "attempted_at", "published_at", "source_detected_at", "created_at", "updated_at")) if record is not None else None

    def get_publication_by_idempotency_key(self, key: str):
        record = self.session.query(PublicationRecord).filter_by(idempotency_key=key).one_or_none()
        return self._attach_utc(record, ("requested_at", "attempted_at", "published_at", "source_detected_at", "created_at", "updated_at")) if record is not None else None

    def get_successful_publications(self, platform: str = "X"):
        records = self.session.query(PublicationRecord).filter_by(platform=platform, state="SUCCEEDED").all()
        return [self._attach_utc(record, ("requested_at", "attempted_at", "published_at", "source_detected_at", "created_at", "updated_at")) for record in records]

    def update_publication_result(self, key: str, request, provider_result, state, published_at=None):
        record = self.get_publication_by_idempotency_key(key)
        if record is None:
            record = self.create_publication_record(request, state=state)
        record.state = state.value if hasattr(state, "value") else state
        record.attempt_number = int(record.attempt_number or 0) + 1
        record.provider = provider_result.provider
        record.provider_request_id = provider_result.request_id
        record.platform_response_status = provider_result.status_code
        record.platform_post_id = provider_result.platform_post_id
        record.retryable = provider_result.retryable
        record.error_category = provider_result.error_category
        record.sanitized_error = provider_result.sanitized_error
        record.attempted_at = datetime.now(UTC)
        record.published_at = published_at
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            return self.get_publication_by_idempotency_key(key)
        self.session.refresh(record)
        return self._attach_utc(record, ("requested_at", "attempted_at", "published_at", "source_detected_at", "created_at", "updated_at"))

    def update_publication_decision(self, key: str, *, eligibility, state: str = "BLOCKED"):
        record = self.get_publication_by_idempotency_key(key)
        if record is None:
            return None
        record.state = state
        record.decision = eligibility.decision.value
        record.decision_reasons_json = self._json_safe(list(eligibility.reasons))
        self.session.commit()
        self.session.refresh(record)
        return self._attach_utc(record, ("requested_at", "attempted_at", "published_at", "source_detected_at", "created_at", "updated_at"))

    def get_publication_records_by_opportunity(self, opportunity_id: str):
        records = self.session.query(PublicationRecord).filter_by(opportunity_id=opportunity_id).all()
        return [self._attach_utc(record, ("requested_at", "attempted_at", "published_at", "source_detected_at", "created_at", "updated_at")) for record in records]

    def get_reconciliation(self, publication_request_id: str):
        record = self.session.query(PublicationReconciliation).filter_by(publication_request_id=publication_request_id).one_or_none()
        return self._attach_utc(record, ("matched_created_at", "reconciled_at", "created_at", "updated_at")) if record is not None else None

    def save_reconciliation(self, result):
        record = self.get_reconciliation(result.publication_request_id)
        values = dict(publication_request_id=result.publication_request_id, idempotency_key=result.idempotency_key, platform=result.platform, original_publication_state=result.original_publication_state, reconciliation_state=result.reconciliation_state.value, platform_post_found=result.platform_post_found, platform_post_id=result.platform_post_id, matched_content_hash=result.matched_content_hash, matched_content_text=result.matched_content_text, matched_created_at=self._ensure_utc(result.matched_created_at) if result.matched_created_at else None, match_method=result.match_method, confidence=result.confidence, retry_allowed=result.retry_allowed, reconciled_at=self._ensure_utc(result.reconciled_at), provider=result.provider, provider_request_id=result.provider_request_id, error_category=result.error_category, sanitized_error=result.sanitized_error, warnings_json=list(result.warnings))
        if record is None:
            record = PublicationReconciliation(**values); self.session.add(record)
        else:
            for key, value in values.items(): setattr(record, key, value)
        self.session.commit(); self.session.refresh(record)
        return self._attach_utc(record, ("matched_created_at", "reconciled_at", "created_at", "updated_at"))

    def mark_publication_reconciled_success(self, idempotency_key: str, platform_post_id: str | None, published_at):
        record = self.get_publication_by_idempotency_key(idempotency_key)
        if record is None: return None
        record.state = "SUCCEEDED"; record.platform_post_id = platform_post_id; record.published_at = self._ensure_utc(published_at); record.retryable = False
        self.session.commit(); self.session.refresh(record)
        return self._attach_utc(record, ("requested_at", "attempted_at", "published_at", "source_detected_at", "created_at", "updated_at"))

    def schedule_publication_job(self, spec):
        existing = self.session.query(PublicationJob).filter_by(idempotency_key=spec.idempotency_key).one_or_none()
        if existing is not None: return existing
        record = PublicationJob(job_type=spec.job_type.value, entity_id=spec.entity_id, idempotency_key=spec.idempotency_key, not_before=self._ensure_utc(spec.not_before), attempt_number=spec.attempt_number, max_attempts=spec.max_attempts, status=spec.status.value, last_error=spec.last_error, analytics_window=getattr(spec, "analytics_window", None))
        self.session.add(record); self.session.commit(); self.session.refresh(record); return self._attach_utc(record, ("not_before", "created_at", "updated_at"))

    def claim_due_publication_job(self, now, *, stale_after_seconds: int = 300):
        from datetime import timedelta
        from sqlalchemy import or_
        current = self._ensure_utc(now)
        stale_before = current - timedelta(seconds=stale_after_seconds)
        job = (self.session.query(PublicationJob)
               .filter(or_(PublicationJob.status.in_(("PENDING", "FAILED_RETRYABLE")), (PublicationJob.status == "RUNNING") & (PublicationJob.updated_at < stale_before)))
               .filter(PublicationJob.not_before <= current)
               .order_by(PublicationJob.not_before.asc(), PublicationJob.id.asc())
               .with_for_update(skip_locked=True).first())
        if job is None:
            self.session.rollback()
            return None
        job.status = "RUNNING"
        job.attempt_number = int(job.attempt_number or 0) + 1
        job.updated_at = current
        self.session.commit(); self.session.refresh(job)
        return self._attach_utc(job, ("not_before", "created_at", "updated_at"))

    def complete_publication_job(self, job_id: int, *, success: bool, retryable: bool = False, error: str | None = None, not_before=None):
        job = self.session.get(PublicationJob, job_id)
        if job is None:
            return None
        if success:
            job.status = "SUCCEEDED"; job.last_error = None
        elif retryable and int(job.attempt_number or 0) < int(job.max_attempts or 0):
            job.status = "FAILED_RETRYABLE"; job.last_error = error
            if not_before is not None: job.not_before = self._ensure_utc(not_before)
        else:
            job.status = "FAILED_FINAL"; job.last_error = error
        self.session.commit(); self.session.refresh(job)
        return self._attach_utc(job, ("not_before", "created_at", "updated_at"))

    def recover_stale_publication_jobs(self, now, stale_after_seconds: int = 300):
        from datetime import timedelta
        current = self._ensure_utc(now); cutoff = current - timedelta(seconds=stale_after_seconds)
        rows = self.session.query(PublicationJob).filter(PublicationJob.status == "RUNNING", PublicationJob.updated_at < cutoff).with_for_update(skip_locked=True).all()
        recovered = []
        for job in rows:
            if job.job_type in {"RECONCILE_PUBLICATION", "INGEST_POST_METRICS"}:
                job.status = "FAILED_RETRYABLE" if int(job.attempt_number or 0) < int(job.max_attempts or 0) else "FAILED_FINAL"
                job.not_before = current + timedelta(seconds=60)
            elif job.job_type == "RETRY_PUBLICATION":
                publication = self.get_publication_by_idempotency_key(job.entity_id)
                if publication is None:
                    publication = self.get_publication_by_request_id(job.entity_id)
                state = getattr(publication, "state", None)
                if state == "SUCCEEDED": job.status = "SUCCEEDED"
                elif state in {"UNKNOWN_PLATFORM_STATE", "SENDING"}: job.status = "FAILED_FINAL"
                else: job.status = "FAILED_RETRYABLE" if int(job.attempt_number or 0) < int(job.max_attempts or 0) else "FAILED_FINAL"
                job.not_before = current + timedelta(seconds=60)
            job.updated_at = current; recovered.append(job.id)
        self.session.commit()
        return recovered

    def save_production_cycle(self, cycle, *, operating_mode: str):
        values = dict(cycle_id=cycle.cycle_id, runner_id=cycle.runner_id, analysis_id=cycle.analysis_id, session_date=cycle.session_date, operating_mode=operating_mode, market_state=cycle.market_state, started_at=self._ensure_utc(cycle.started_at), completed_at=self._ensure_utc(cycle.completed_at) if cycle.completed_at else None, analysis_confidence=cycle.analysis_confidence, opportunity_count=cycle.opportunity_count, selected_candidate_count=cycle.selected_candidate_count, draft_count=cycle.draft_count, approved_draft_count=cycle.approved_draft_count, publication_decision=cycle.publication_decision, publication_request_id=cycle.publication_request_id, outcome=cycle.outcome, error_category=cycle.error_category, error_message_sanitized=getattr(cycle, "error_message_sanitized", None))
        record = self.session.query(ProductionCycle).filter_by(cycle_id=cycle.cycle_id).one_or_none()
        if record is None:
            record = ProductionCycle(**values); self.session.add(record)
        else:
            for key, value in values.items(): setattr(record, key, value)
        self.session.commit(); self.session.refresh(record)
        return self._attach_utc(record, ("started_at", "completed_at", "created_at", "updated_at"))

    def get_production_cycles(self, session_date: str | None = None):
        query = self.session.query(ProductionCycle)
        if session_date is not None: query = query.filter_by(session_date=session_date)
        return [self._attach_utc(row, ("started_at", "completed_at", "created_at", "updated_at")) for row in query.order_by(ProductionCycle.started_at.asc()).all()]

    def get_global_hold(self):
        return self.session.query(PublicationHold).filter_by(hold_name="GLOBAL_PUBLICATION_HOLD").one_or_none()

    def upsert_global_hold(self, *, state: str, reason: str, source_reference: str | None = None, cleared_at=None):
        record = self.get_global_hold(); now = datetime.now(UTC)
        if record is None:
            record = PublicationHold(hold_name="GLOBAL_PUBLICATION_HOLD", hold_state=state, reason=reason, source_reference=source_reference, cleared_at=cleared_at); self.session.add(record)
        else:
            record.hold_state = state; record.reason = reason; record.source_reference = source_reference; record.cleared_at = cleared_at
        self.session.commit(); self.session.refresh(record); return record

    def acquire_system_lease(self, *, lease_name: str, owner_id: str, host: str, pid: int, now, ttl_seconds: int = 120) -> bool:
        from datetime import timedelta
        current = self._ensure_utc(now); record = self.session.query(SystemLease).filter_by(lease_name=lease_name).with_for_update().one_or_none()
        if record is not None:
            expires = record.expires_at if record.expires_at.tzinfo else record.expires_at.replace(tzinfo=UTC)
            if expires > current and record.owner_id != owner_id:
                self.session.rollback(); return False
        if record is None:
            record = SystemLease(lease_name=lease_name, owner_id=owner_id, host=host, pid=pid, acquired_at=current, heartbeat_at=current, expires_at=current + timedelta(seconds=ttl_seconds)); self.session.add(record)
        else:
            record.owner_id = owner_id; record.host = host; record.pid = pid; record.acquired_at = current; record.heartbeat_at = current; record.expires_at = current + timedelta(seconds=ttl_seconds)
        self.session.commit(); return True

    def heartbeat_system_lease(self, *, lease_name: str, owner_id: str, now, ttl_seconds: int = 120) -> bool:
        from datetime import timedelta
        record = self.session.query(SystemLease).filter_by(lease_name=lease_name, owner_id=owner_id).one_or_none()
        if record is None: return False
        current = self._ensure_utc(now); record.heartbeat_at = current; record.expires_at = current + timedelta(seconds=ttl_seconds); self.session.commit(); return True

    def release_system_lease(self, *, lease_name: str, owner_id: str) -> bool:
        record = self.session.query(SystemLease).filter_by(lease_name=lease_name, owner_id=owner_id).one_or_none()
        if record is None: return False
        self.session.delete(record); self.session.commit(); return True

    def ensure_metrics_jobs(self, publication, *, now=None):
        from datetime import timedelta
        from alpha_market_engine.content.observability import JobStatus, JobType, PublicationJobSpec, job_idempotency_key
        current = self._utc_or_now(now)
        windows = (("15_MIN", 15), ("1_HOUR", 60), ("4_HOUR", 240), ("24_HOUR", 1440))
        jobs = []
        if not publication.platform_post_id:
            return jobs
        for window, minutes in windows:
            entity_id = f"{publication.platform_post_id}:{window}"
            spec = PublicationJobSpec(JobType.INGEST_POST_METRICS, entity_id, job_idempotency_key(JobType.INGEST_POST_METRICS, entity_id), current + timedelta(minutes=minutes), max_attempts=3, status=JobStatus.PENDING, analytics_window=window)
            jobs.append(self.schedule_publication_job(spec))
        return jobs

    def get_session_summary(self, session_date: str):
        from sqlalchemy import func
        cycles = self.session.query(ProductionCycle).filter(ProductionCycle.session_date == session_date).all()
        start = datetime.fromisoformat(session_date).replace(tzinfo=UTC); end = start + timedelta(days=1)
        publications = self.session.query(PublicationRecord).filter(PublicationRecord.created_at >= start, PublicationRecord.created_at < end).all()
        events = self.session.query(SystemEvent).filter(SystemEvent.created_at >= start, SystemEvent.created_at < end).all()
        jobs = self.session.query(PublicationJob).all()
        snapshots = self.session.query(PostMetricsSnapshot).filter(PostMetricsSnapshot.captured_at >= start, PostMetricsSnapshot.captured_at < end).count()
        confidences = [c.analysis_confidence for c in cycles if c.analysis_confidence is not None]
        hold = self.get_global_hold()
        return {"session_date": session_date, "cycles_completed": len(cycles), "healthy_no_op_cycles": sum(c.outcome in {"HEALTHY_NO_CANDIDATE", "HEALTHY_NO_OPPORTUNITY"} for c in cycles), "degraded_cycles": sum(c.outcome == "DEGRADED" for c in cycles), "failed_cycles": sum(str(c.outcome).startswith("FAILED") for c in cycles), "opportunities_detected": sum(c.opportunity_count or 0 for c in cycles), "post_candidates_selected": sum(c.selected_candidate_count or 0 for c in cycles), "drafts_generated": sum(c.draft_count or 0 for c in cycles), "drafts_approved": sum(c.approved_draft_count or 0 for c in cycles), "posts_published": sum(p.state == "SUCCEEDED" for p in publications), "posts_blocked": sum(p.state == "BLOCKED" for p in publications), "duplicate_suppressions": sum(e.event_type == "PUBLICATION_BLOCKED" and "DUPLICATE" in str(e.metadata_json) for e in events), "daily_hard_max": 10, "average_analysis_confidence": (sum(confidences) / len(confidences)) if confidences else None, "active_hold": hold.hold_state if hold else "CLEAR", "unresolved_unknown_platform_states": self.session.query(PublicationRecord).filter_by(state="UNKNOWN_PLATFORM_STATE").count(), "pending_jobs": sum(j.status in {"PENDING", "RUNNING", "FAILED_RETRYABLE"} for j in jobs), "failed_jobs": sum(j.status == "FAILED_FINAL" for j in jobs), "metrics_snapshots": snapshots}

    def list_publication_jobs(self, limit: int = 100):
        return self.session.query(PublicationJob).order_by(PublicationJob.not_before.asc(), PublicationJob.id.asc()).limit(limit).all()

    def get_publication_records(self, limit: int = 20):
        return self.session.query(PublicationRecord).order_by(PublicationRecord.created_at.desc()).limit(limit).all()

    def maybe_emit_no_op_event(self, *, session_date: str, now: datetime, selected_candidate_count: int) -> bool:
        if selected_candidate_count:
            return False
        previous = self.session.query(ProductionCycle).filter(ProductionCycle.session_date == session_date, ProductionCycle.completed_at < now).order_by(ProductionCycle.completed_at.desc()).first()
        if previous is not None and previous.selected_candidate_count == 0:
            recent = self.session.query(SystemEvent).filter(SystemEvent.event_type == "NO_FRESH_OPPORTUNITY", SystemEvent.created_at >= now - timedelta(minutes=60)).first()
            if recent is not None: return False
        self.add_system_event_sync(event_type="NO_FRESH_OPPORTUNITY", message="no fresh opportunity in production cycle", metadata={"session_date": session_date})
        return True

    def get_system_events(self, event_type: str | None = None, *, since=None):
        query = self.session.query(SystemEvent)
        if event_type: query = query.filter(SystemEvent.event_type == event_type)
        if since: query = query.filter(SystemEvent.created_at >= self._ensure_utc(since))
        return [self._attach_utc(row, ("created_at",)) for row in query.order_by(SystemEvent.created_at.asc()).all()]

    def save_metrics_snapshot(self, snapshot):
        existing = self.session.query(PostMetricsSnapshot).filter_by(capture_key=snapshot.capture_key).one_or_none()
        if existing is not None: return existing
        availability_flags = getattr(snapshot, "availability_flags", getattr(snapshot, "availability_flags_json", {}))
        record = PostMetricsSnapshot(capture_key=snapshot.capture_key, platform=snapshot.platform, platform_post_id=snapshot.platform_post_id, publication_request_id=snapshot.publication_request_id, captured_at=self._ensure_utc(snapshot.captured_at), analytics_window=snapshot.analytics_window, impressions=snapshot.impressions, likes=snapshot.likes, replies=snapshot.replies, reposts=snapshot.reposts, bookmarks=snapshot.bookmarks, quote_posts=snapshot.quote_posts, profile_clicks=snapshot.profile_clicks, url_clicks=snapshot.url_clicks, engagement_rate=snapshot.engagement_rate, source_provider=snapshot.source_provider, provider_payload_version=snapshot.provider_payload_version, availability_flags_json=availability_flags)
        self.session.add(record); self.session.commit(); self.session.refresh(record); return self._attach_utc(record, ("captured_at", "created_at", "updated_at"))

    def get_metrics_snapshots(self, platform_post_id: str):
        rows = self.session.query(PostMetricsSnapshot).filter_by(platform_post_id=platform_post_id).order_by(PostMetricsSnapshot.captured_at.asc()).all()
        return [self._attach_utc(row, ("captured_at", "created_at", "updated_at")) for row in rows]

    def add_system_event_sync(self, *, event_type: str, severity: str = "INFO", component: str = "content_publication", message: str, metadata: dict[str, Any] | None = None):
        record = SystemEvent(event_type=event_type, severity=severity, component=component, message=message, metadata_json=self._json_safe(metadata or {}))
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return self._attach_utc(record, ("created_at",))

    async def save_market_regime(
        self,
        *,
        detected_at,
        regime: str,
        confidence: int,
        reasoning: str,
        supporting_data: dict[str, Any] | None = None,
    ) -> MarketRegime:
        record = MarketRegime(
            detected_at=self._ensure_utc(detected_at),
            regime=regime,
            confidence=self._validate_score("confidence", confidence),
            reasoning=reasoning,
            supporting_data_json=self._json_safe(supporting_data or {}),
        )
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return self._attach_utc(record, ("detected_at", "created_at", "updated_at"))

    async def add_system_event(
        self,
        *,
        event_type: str,
        severity: str,
        component: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> SystemEvent:
        record = SystemEvent(
            event_type=event_type,
            severity=severity,
            component=component,
            message=message,
            metadata_json=self._json_safe(metadata or {}),
        )
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return self._attach_utc(record, ("created_at",))
