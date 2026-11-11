from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from alpha_market_engine.content.drafting import ContentDraftingService
from alpha_market_engine.content.opportunities import ContentOpportunityEngine
from alpha_market_engine.content.publication import ContentPublicationService, ProviderPublicationResult, PublishMode
from alpha_market_engine.database.base import Base
from alpha_market_engine.database.repository import Repository
from alpha_market_engine.market.brain_types import DivergenceSignal
from alpha_market_engine.production import HoldState, ProductionSafetyService


def _assert_test_db(engine):
    name = engine.url.database
    assert name == "alpha_market_test", f"destructive certification refused database={name!r}"


def _clean(engine):
    _assert_test_db(engine)
    with engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in ("system_events", "publication_holds", "generated_content_drafts", "publication_records"):
            connection.execute(text(f"TRUNCATE TABLE `{table}`"))
        connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))


def _market_result():
    return SimpleNamespace(analysis_id="r4-analysis", analysis_started_at=datetime.now(UTC), analysis_session_date="2026-07-14", analysis_confidence=SimpleNamespace(score=85), price_action={"SPY": SimpleNamespace(events=[])}, divergences=SimpleNamespace(signals=[DivergenceSignal.QQQ_OUTPERFORMING_SPY], confidence=85, relative_performance_facts={"QQQ": {"relative_difference": "0.75"}}, index_spread_fact=None), regime=SimpleNamespace(regime=SimpleNamespace(value="ROTATION"), confidence=85, supporting_data={}, scores={"ROTATION": 85}), observations=[], structures={}, momentum={})


class RaisingDraftProvider:
    def draft(self, package, reasons):
        raise RuntimeError("r4-drafting-failure")


class FinalFailProvider:
    def __init__(self): self.calls = 0
    def publish(self, request):
        self.calls += 1
        return ProviderPublicationResult(False, "fake", f"r4-failure-{self.calls}", 500, None, error_category="PROVIDER_FINAL", sanitized_error="provider failure")


def _draft(hash_value: str):
    return SimpleNamespace(draft_id=f"r4-draft-{hash_value}", draft_request_id=f"r4-request-{hash_value}", analysis_id=f"r4-analysis-{hash_value}", opportunity_id=f"r4-opportunity-{hash_value}", opportunity_key="QQQ_LEADERSHIP", platform="X", content_text="QQQ leads SPY on relative strength.", content_hash=hash_value, generated_at=datetime.now(UTC), final_decision="APPROVE", validation_status="VALID", quality_score=100)


def _opportunity(hash_value: str):
    return SimpleNamespace(opportunity_id=f"r4-opportunity-{hash_value}", opportunity_key="QQQ_LEADERSHIP", opportunity_type="QQQ_LEADERSHIP", content_pattern_key="QQQ_LEADERSHIP", confirmation_state="CONFIRMED", decision="POST_CANDIDATE", status="APPROVED", detected_at=datetime.now(UTC))


def test_drafting_breaker_restart_durability(mysql_test_engine):
    _clean(mysql_test_engine)
    factory = sessionmaker(bind=mysql_test_engine, future=True)
    opportunity = ContentOpportunityEngine().evaluate(_market_result()).selected_candidates[0]
    for expected in (1, 2, 3):
        session = factory()
        repo = Repository(session)
        safety = ProductionSafetyService(repo)
        service = ContentDraftingService(RaisingDraftProvider(), repository=repo, safety=safety)
        with pytest.raises(RuntimeError): service.draft(opportunity)
        events = repo.get_system_events("DRAFTING_PROVIDER_FINAL_FAILURE")
        assert len(events) == expected
        assert (repo.get_global_hold() is not None and repo.get_global_hold().hold_state == HoldState.SYSTEM_HEALTH_HOLD.value) is (expected == 3)
        session.close()
    session = factory(); assert Repository(session).get_global_hold().hold_state == HoldState.SYSTEM_HEALTH_HOLD.value; session.close(); mysql_test_engine.dispose()




class SuccessProvider:
    def __init__(self): self.calls = 0
    def publish(self, request):
        self.calls += 1
        return ProviderPublicationResult(True, "fake", f"r4-post-{self.calls}", 200, None, error_category=None, sanitized_error=None)


def test_hold_clear_restores_future_publication_eligibility(mysql_test_engine):
    _clean(mysql_test_engine)
    session = sessionmaker(bind=mysql_test_engine, future=True)(); repo = Repository(session); safety = ProductionSafetyService(repo); provider = SuccessProvider()
    service = ContentPublicationService(provider, repository=repo, auto_post_enabled=True, safety=safety)
    safety.manual_hold("r4 test hold")
    blocked = service.publish(_draft("r4-blocked"), _opportunity("blocked"), mode=PublishMode.LIMITED_AUTO, approval_status="APPROVE")
    assert provider.calls == 0 and blocked.status.value == "BLOCKED"
    safety.clear_operator(oauth={}, unresolved_unknown_count=0, healthy=True)
    allowed = service.publish(_draft("r4-allowed"), _opportunity("allowed"), mode=PublishMode.LIMITED_AUTO, approval_status="APPROVE")
    assert provider.calls == 1 and allowed.status.value == "SUCCEEDED"
    session.close(); mysql_test_engine.dispose()


def test_x_provider_breaker_restart_durability(mysql_test_engine):
    _clean(mysql_test_engine)
    factory = sessionmaker(bind=mysql_test_engine, future=True)
    for index in (1, 2, 3):
        session = factory(); repo = Repository(session); safety = ProductionSafetyService(repo); provider = FinalFailProvider()
        service = ContentPublicationService(provider, repository=repo, auto_post_enabled=True, safety=safety)
        service.publish(_draft(f"r4-hash-{index}"), _opportunity(str(index)), mode=PublishMode.LIMITED_AUTO, approval_status="APPROVE")
        assert len(repo.get_system_events("X_PROVIDER_FINAL_FAILURE")) == index
        assert (repo.get_global_hold() is not None and repo.get_global_hold().hold_state == HoldState.SYSTEM_HEALTH_HOLD.value) is (index == 3)
        session.close()
    session = factory(); repo = Repository(session); provider = FinalFailProvider(); service = ContentPublicationService(provider, repository=repo, auto_post_enabled=True, safety=ProductionSafetyService(repo))
    service.publish(_draft("r4-fourth"), _opportunity("fourth"), mode=PublishMode.LIMITED_AUTO, approval_status="APPROVE")
    assert provider.calls == 0
    session.close(); mysql_test_engine.dispose()
