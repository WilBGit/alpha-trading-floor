from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from alpha_market_engine.content.publication import ContentPublicationService, FakePublishingProvider, ProviderPublicationResult, PublishMode
from alpha_market_engine.production import MarketSessionCalendar


def test_xnys_early_close_bound_is_calendar_driven():
    calendar = MarketSessionCalendar()
    session = calendar.session_for(datetime(2026, 11, 27, 19, 0, tzinfo=UTC))
    assert session is not None
    _, start, close = session
    assert close.hour < 20
    assert calendar.is_active(close - timedelta(seconds=1))
    assert not calendar.is_active(close + timedelta(seconds=1))




def _draft_and_opportunity():
    draft = SimpleNamespace(
        draft_id="shutdown-draft", draft_request_id="shutdown-draft-request", analysis_id="shutdown-analysis", opportunity_id="shutdown-opportunity", opportunity_key="QQQ_LEADERSHIP", platform="X", content_text="$QQQ is leading $SPY.", content_hash="shutdown-hash", provider="fake", model="fake", prompt_version="v1", voice_profile="default", draft_attempt=1, generated_at=datetime.now(UTC), character_count=24, validation_status="VALID", validation_failures=(), factual_claims=(), numeric_claims=(), symbol_claims=("QQQ", "SPY"), quality_score=100, final_decision="APPROVE", final_decision_reasons=()
    )
    opportunity = SimpleNamespace(opportunity_id="shutdown-opportunity", opportunity_key="QQQ_LEADERSHIP", opportunity_type="QQQ_LEADERSHIP", content_pattern_key="QQQ_LEADERSHIP", confirmation_state="CONFIRMED", decision="POST_CANDIDATE", status="APPROVED", detected_at=datetime.now(UTC), created_at=datetime.now(UTC), source_detected_at=datetime.now(UTC), source_observation_keys=(), symbols=("QQQ", "SPY"))
    return draft, opportunity


def test_publication_shutdown_success_persists_terminal_state_before_release():
    calls = []
    class Repo:
        def __init__(self): self.record = None
        def get_publication_by_idempotency_key(self, key): return self.record
        def create_publication_record(self, request, *, eligibility=None, state="PENDING"):
            self.record = SimpleNamespace(state=state, platform_post_id=None, platform_response_status=None, attempt_number=0, published_at=None, content_pattern_key=request.content_pattern_key, content_hash=request.content_hash); return self.record
        def update_publication_result(self, key, request, result, state, published_at=None):
            self.record.state = state.value; self.record.platform_post_id = result.platform_post_id; self.record.published_at = published_at; self.record.attempt_number += 1; return self.record
        def add_system_event_sync(self, **kwargs): pass
        def get_successful_publications(self, platform="X"): return []
    repo = Repo()
    provider = FakePublishingProvider([ProviderPublicationResult(True, "fake", "req-1", 201, "post-1", transmitted=True)])
    result = ContentPublicationService(provider, repository=repo, auto_post_enabled=True).publish(*_draft_and_opportunity(), mode=PublishMode.LIMITED_AUTO, approval_status="APPROVE", successful_posts=[])
    assert result.status.value == "SUCCEEDED"
    assert repo.record.state == "SUCCEEDED"
    assert provider.invocation_count == 1


def test_publication_shutdown_ambiguous_state_requires_reconciliation_no_second_call():
    class Repo:
        def __init__(self): self.record = None
        def get_publication_by_idempotency_key(self, key): return self.record
        def create_publication_record(self, request, *, eligibility=None, state="PENDING"):
            self.record = SimpleNamespace(state=state, platform_post_id=None, platform_response_status=None, attempt_number=0, published_at=None, content_pattern_key=request.content_pattern_key, content_hash=request.content_hash); return self.record
        def update_publication_result(self, key, request, result, state, published_at=None):
            self.record.state = state.value; self.record.attempt_number += 1; return self.record
        def add_system_event_sync(self, **kwargs): pass
        def get_successful_publications(self, platform="X"): return []
    repo = Repo()
    provider = FakePublishingProvider([ProviderPublicationResult(False, "fake", "req-unknown", None, None, transmitted=True, error_category="AMBIGUOUS_RESPONSE")])
    service = ContentPublicationService(provider, repository=repo, auto_post_enabled=True)
    result = service.publish(*_draft_and_opportunity(), mode=PublishMode.LIMITED_AUTO, approval_status="APPROVE", successful_posts=[])
    assert result.status.value == "UNKNOWN_PLATFORM_STATE"
    assert repo.record.state == "UNKNOWN_PLATFORM_STATE"
    assert provider.invocation_count == 1
