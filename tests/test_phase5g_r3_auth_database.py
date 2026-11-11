from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from alpha_market_engine.content.publication import ContentPublicationService, ProviderPublicationResult, PublishMode
from alpha_market_engine.database.base import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from alpha_market_engine.database.repository import Repository
from alpha_market_engine.production import HoldState, ProductionSafetyService


def _repo():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    return Repository(session), session, engine


class AuthFailureProvider:
    def __init__(self, category):
        self.category = category
        self.calls = 0
    def publish(self, request):
        self.calls += 1
        error = RuntimeError("safe-auth-error")
        error.category = self.category
        raise error


class CountingProvider:
    def __init__(self): self.calls = 0
    def publish(self, request):
        self.calls += 1
        return ProviderPublicationResult(True, "fake", "ok", 201, "post", transmitted=True)


def _fixture():
    draft = SimpleNamespace(draft_id="auth-draft", draft_request_id="auth-request", analysis_id="auth-analysis", opportunity_id="auth-opportunity", opportunity_key="QQQ_LEADERSHIP", platform="X", content_text="QQQ leads SPY on relative strength.", content_hash="auth-hash", generated_at=datetime.now(UTC), final_decision="APPROVE", validation_status="VALID", quality_score=100)
    opportunity = SimpleNamespace(opportunity_id="auth-opportunity", opportunity_key="QQQ_LEADERSHIP", opportunity_type="QQQ_LEADERSHIP", content_pattern_key="QQQ_LEADERSHIP", confirmation_state="CONFIRMED", decision="POST_CANDIDATE", status="APPROVED", detected_at=datetime.now(UTC))
    return draft, opportunity


@pytest.mark.parametrize("category", ["AUTH_FAILURE", "OAUTH_REFRESH_FAILURE", "IDENTITY_MISMATCH", "REQUIRED_SCOPE_FAILURE"])
def test_actual_auth_failure_categories_activate_auth_hold_and_block_next_request(category):
    repo, session, engine = _repo()
    safety = ProductionSafetyService(repo)
    provider = AuthFailureProvider(category)
    service = ContentPublicationService(provider, repository=repo, auto_post_enabled=True, safety=safety)
    draft, opportunity = _fixture()
    with pytest.raises(RuntimeError):
        service.publish(draft, opportunity, mode=PublishMode.LIMITED_AUTO, approval_status="APPROVE")
    assert repo.get_global_hold().hold_state == HoldState.AUTH_HOLD.value
    service.publish(draft, opportunity, mode=PublishMode.LIMITED_AUTO, approval_status="APPROVE")
    assert provider.calls == 1
    session.close(); engine.dispose()


def test_database_failure_is_fail_closed_without_provider_call_then_records_recovery_hold():
    repo, session, engine = _repo()
    safety = ProductionSafetyService(repo)
    provider = CountingProvider()
    class FailingRepository:
        def get_publication_by_idempotency_key(self, key): raise RuntimeError("database unavailable")
    service = ContentPublicationService(provider, repository=FailingRepository(), auto_post_enabled=True, safety=safety)
    draft, opportunity = _fixture()
    with pytest.raises(RuntimeError):
        service.publish(draft, opportunity, mode=PublishMode.LIMITED_AUTO, approval_status="APPROVE")
    assert safety.database_available is False
    assert provider.calls == 0
    safety.repository = repo
    recovered = safety.mark_database_available()
    assert recovered.state == HoldState.DATABASE_HOLD
    session.close(); engine.dispose()
