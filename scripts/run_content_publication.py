from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from alpha_market_engine.content.drafting import CodexDraftingProvider, ContentDraftingService, GeneratedContentDraft, FakeDraftingProvider
from alpha_market_engine.content.opportunities import ContentDecision, ContentOpportunity, ConfirmationState, OpportunityType, ContentOpportunityEngine
from alpha_market_engine.content.publication import ContentPublicationService, FakePublishingProvider, PublishMode
from alpha_market_engine.database.models import ContentOpportunity as OpportunityRecord, GeneratedContentDraft as DraftRecord
from alpha_market_engine.database.repository import Repository
from alpha_market_engine.market.brain import MarketBrainService
from alpha_market_engine.market.providers import AlpacaMarketDataProvider, RobinhoodMarketDataProvider


def safe(value):
    if is_dataclass(value): return safe(asdict(value))
    if isinstance(value, Enum): return value.value
    if isinstance(value, Decimal): return str(value)
    if isinstance(value, datetime): return value.astimezone(UTC).isoformat()
    if isinstance(value, dict): return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [safe(v) for v in value]
    return value


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def opportunity_from_record(record):
    return ContentOpportunity(
        opportunity_id=record.opportunity_id, opportunity_key=record.opportunity_key, analysis_id=record.analysis_id,
        opportunity_type=OpportunityType(record.opportunity_type), symbols=tuple(record.symbols_json or []), primary_symbol=record.primary_symbol,
        session_date=record.detected_at.date().isoformat(), detected_at=aware(record.detected_at), source_regime=record.source_regime or "",
        source_regime_confidence=record.source_regime_confidence or 0, facts=record.facts_json or {}, importance_score=record.importance_score,
        confidence_score=record.confidence_score or 0, freshness_score=record.freshness_score or 0, novelty_score=record.novelty_score or 0,
        content_score=Decimal(str(record.content_score or 0)), confirmation_state=ConfirmationState(record.confirmation_state or "UNCONFIRMED"),
        decision=ContentDecision(record.decision or "SKIP"), source_event_ids=tuple(record.source_event_ids_json or []),
        source_observation_keys=tuple(record.source_observation_keys_json or []), source_divergence_types=tuple(record.source_divergence_types_json or []),
        interpretation_boundary=record.interpretation_boundary or "", content_pattern_key=record.content_pattern_key or "",
    )


def draft_from_record(record):
    return GeneratedContentDraft(
        draft_id=record.draft_id, draft_request_id=record.draft_request_id, analysis_id=record.analysis_id,
        opportunity_id=record.opportunity_id, opportunity_key=record.opportunity_key, platform=record.platform,
        content_text=record.content_text, content_hash=record.content_hash, provider=record.provider, model=record.model,
        prompt_version=record.prompt_version, voice_profile=record.voice_profile, draft_attempt=record.draft_attempt,
        generated_at=aware(record.generated_at), character_count=record.character_count, validation_status=record.validation_status,
        validation_failures=tuple(record.validation_failures_json or []), factual_claims=tuple(record.factual_claims_json or []),
        numeric_claims=tuple(record.numeric_claims_json or []), symbol_claims=tuple(record.symbol_claims_json or []), quality_score=record.quality_score,
        final_decision=record.final_decision, final_decision_reasons=tuple(record.final_decision_reasons_json or []),
    )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--certify", action="store_true")
    args = parser.parse_args()
    load_dotenv(override=False)
    database_url = os.environ.get("DATABASE_URL", "")
    if "alpha_market_dev" not in database_url:
        raise SystemExit("controlled certification requires DATABASE_URL targeting alpha_market_dev")
    session = sessionmaker(bind=create_engine(database_url, pool_pre_ping=True, future=True), autoflush=False, future=True)()
    repo = Repository(session)
    try:
        primary = RobinhoodMarketDataProvider()
        secondary = None
        if os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_API_SECRET"):
            secondary = AlpacaMarketDataProvider(api_key=os.environ["ALPACA_API_KEY"], api_secret=os.environ["ALPACA_API_SECRET"], base_url=os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets"), data_feed=os.getenv("ALPACA_DATA_FEED", "iex"))
        market_result = await MarketBrainService(primary, secondary=secondary, symbols=["SPY", "QQQ", "DIA", "IWM"], repository=repo).analyze_market()
        opportunities = ContentOpportunityEngine(repository=repo).evaluate(market_result)
        opportunity = opportunities.selected_candidates[0] if opportunities.selected_candidates else None
        draft = None
        if opportunity is None:
            persisted_draft = session.query(DraftRecord).filter_by(final_decision="APPROVE").order_by(DraftRecord.generated_at.desc()).first()
            if persisted_draft is None:
                raise SystemExit("NO_APPROVED_DRAFT_FOR_DRY_RUN")
            persisted_opportunity = session.query(OpportunityRecord).filter_by(opportunity_id=persisted_draft.opportunity_id).one()
            opportunity, draft = opportunity_from_record(persisted_opportunity), draft_from_record(persisted_draft)
        else:
            provider = FakeDraftingProvider() if not os.getenv("X_DRAFT_LIVE", "") else CodexDraftingProvider(model="gpt-5.6-sol")
            draft_result = ContentDraftingService(provider, repository=repo).draft(opportunity)
            if draft_result.approved_draft is None:
                raise SystemExit("SELECTED_OPPORTUNITY_DRAFT_NOT_APPROVED")
            draft = draft_result.approved_draft
        publisher = ContentPublicationService(provider=FakePublishingProvider(), repository=repo, auto_post_enabled=False)
        successful = repo.get_successful_publications("X")
        eligibility = publisher.evaluate(draft, opportunity, PublishMode.DRY_RUN, now=datetime.now(UTC), successful_posts=successful)
        execution = publisher.publish(draft, opportunity, PublishMode.DRY_RUN, now=datetime.now(UTC), successful_posts=successful)
        payload = {"analysis_id": market_result.analysis_id, "opportunity": opportunity, "draft": draft, "eligibility": eligibility, "execution": execution, "publish_mode": "DRY_RUN", "auto_post_enabled": False, "provider_invocation_count": 0, "published": False, "daily_successful_post_count": len(successful), "daily_hard_max": publisher.daily_hard_max}
        print(json.dumps(safe(payload), indent=2, sort_keys=True))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
