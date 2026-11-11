from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from alpha_market_engine.content.drafting import ContentDraftingService
from alpha_market_engine.content.opportunities import ContentOpportunityEngine
from alpha_market_engine.content.publication import ContentPublicationService, ProviderPublicationResult, PublishMode
from alpha_market_engine.database.base import Base
from alpha_market_engine.database.repository import Repository
from alpha_market_engine.production import GlobalPublicationHold, HoldState, ProductionSafetyService
from alpha_market_engine.market.brain_types import DivergenceSignal


def market_result():
    return SimpleNamespace(analysis_id="r3-analysis", analysis_started_at=datetime.now(UTC), analysis_session_date="2026-07-14", analysis_confidence=SimpleNamespace(score=85), price_action={"SPY": SimpleNamespace(events=[])}, divergences=SimpleNamespace(signals=[DivergenceSignal.QQQ_OUTPERFORMING_SPY], confidence=85, relative_performance_facts={"QQQ": {"relative_difference": "0.75"}}, index_spread_fact=None), regime=SimpleNamespace(regime=SimpleNamespace(value="ROTATION"), confidence=85, supporting_data={}, scores={"ROTATION": 85}), observations=[], structures={}, momentum={})


class RaisingDraftProvider:
    def draft(self, package, reasons):
        raise RuntimeError("draft-provider-failure")


class FinalFailProvider:
    def __init__(self):
        self.calls = 0

    def publish(self, request):
        self.calls += 1
        return ProviderPublicationResult(False, "fake", f"fail-{self.calls}", 500, None, error_category="PROVIDER_FINAL", sanitized_error="provider failure")


def _repo():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    return Repository(session), session, engine


def _opportunity(repo):
    result = ContentOpportunityEngine(repository=repo).evaluate(market_result())
    return result.selected_candidates[0]


def test_actual_drafting_failure_path_persists_three_failure_breaker():
    repo, session, engine = _repo()
    safety = ProductionSafetyService(repo)
    service = ContentDraftingService(RaisingDraftProvider(), repository=repo, safety=safety)
    opportunity = _opportunity(repo)
    for _ in range(3):
        try:
            service.draft(opportunity)
        except RuntimeError:
            pass
    assert repo.get_global_hold().hold_state == HoldState.SYSTEM_HEALTH_HOLD.value
    session.close(); engine.dispose()


def test_actual_publication_final_failure_path_persists_three_failure_breaker_and_blocks_future_call():
    repo, session, engine = _repo()
    safety = ProductionSafetyService(repo)
    provider = FinalFailProvider()
    service = ContentPublicationService(provider, repository=repo, auto_post_enabled=True, safety=safety)
    draft = SimpleNamespace(draft_id="r3-draft", draft_request_id="r3-request", analysis_id="r3-analysis", opportunity_id="r3-opportunity", opportunity_key="QQQ_LEADERSHIP", platform="X", content_text="QQQ leads SPY on relative strength.", content_hash="r3-hash", generated_at=datetime.now(UTC), final_decision="APPROVE", validation_status="VALID", quality_score=100)
    opportunity = SimpleNamespace(opportunity_id="r3-opportunity", opportunity_key="QQQ_LEADERSHIP", opportunity_type="QQQ_LEADERSHIP", content_pattern_key="QQQ_LEADERSHIP", confirmation_state="CONFIRMED", decision="POST_CANDIDATE", status="APPROVED", detected_at=datetime.now(UTC))
    for _ in range(3):
        service.publish(draft, opportunity, mode=PublishMode.LIMITED_AUTO, approval_status="APPROVE")
    assert repo.get_global_hold().hold_state == HoldState.SYSTEM_HEALTH_HOLD.value
    service.publish(draft, opportunity, mode=PublishMode.LIMITED_AUTO, approval_status="APPROVE")
    assert provider.calls == 3
    session.close(); engine.dispose()
