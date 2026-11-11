from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace as N

import pytest

from alpha_market_engine.content.drafting import GeneratedContentDraft
from alpha_market_engine.content.publication import (
    ContentPublicationService, FakePublishingProvider, ProviderPublicationResult, PublishMode,
    PublicationDecision, PublicationState, XApiPublishingProvider,
)

NOW = datetime(2026, 7, 14, 16, 0, tzinfo=UTC)


def draft(final="APPROVE", status="VALID", text="$QQQ +1.11% vs $SPY +0.36%. Clear relative strength from $QQQ.", generated_at=None):
    generated_at = generated_at or NOW - timedelta(minutes=1)
    return GeneratedContentDraft("draft-1", "request-1", "analysis-1", "opp-1", "key-1", "X", text, "hash-1", "fake", "fake", "phase5b-v1", "ALPHA_TRADING_FLOOR_TRADER_V1", 1, generated_at, len(text), status, (), ("QQQ_LEADERSHIP",), ("1.11%", "0.36%"), ("QQQ", "SPY"), 100, final, ())


def opportunity(confirm="CONFIRMED", kind="QQQ_LEADERSHIP", decision="POST_CANDIDATE", detected_at=None):
    return N(opportunity_id="opp-1", opportunity_key="key-1", opportunity_type=N(value=kind), detected_at=detected_at or NOW - timedelta(minutes=2), confirmation_state=confirm, decision=decision, content_pattern_key="QQQ_SPY:RELATIVE_PERFORMANCE:QQQ", status="ACTIVE")


def service(provider=None, **kwargs):
    return ContentPublicationService(provider or FakePublishingProvider(), **kwargs)


def test_1_approved_dry_run_is_eligible_and_does_not_invoke_provider():
    provider = FakePublishingProvider(); result = service(provider).publish(draft(), opportunity(), PublishMode.DRY_RUN, now=NOW)
    assert result.status == PublicationState.PENDING and provider.invocation_count == 0


def test_2_auto_disabled():
    result = service(auto_post_enabled=False).evaluate(draft(), opportunity(), PublishMode.AUTO, now=NOW)
    assert result.decision == PublicationDecision.BLOCKED_AUTO_POST_DISABLED


def test_3_draft_not_approved():
    result = service().evaluate(draft(final="REJECT"), opportunity(), now=NOW)
    assert result.decision == PublicationDecision.BLOCKED_DRAFT_NOT_APPROVED


def test_4_manual_pending():
    result = service().evaluate(draft(), opportunity(), PublishMode.MANUAL_APPROVAL, now=NOW, approval_status="PENDING")
    assert result.decision == PublicationDecision.BLOCKED_HUMAN_APPROVAL_REQUIRED


def test_5_manual_approved_executes_fake():
    provider = FakePublishingProvider(); result = service(provider).publish(draft(), opportunity(), PublishMode.MANUAL_APPROVAL, now=NOW, approval_status="APPROVED")
    assert result.status == PublicationState.SUCCEEDED and provider.invocation_count == 1


def test_6_resolved_opportunity():
    result = service().evaluate(draft(), opportunity("RESOLVED"), now=NOW)
    assert result.decision == PublicationDecision.BLOCKED_OPPORTUNITY_RESOLVED


def test_7_expired_opportunity():
    result = service().evaluate(draft(), opportunity("EXPIRED"), now=NOW)
    assert result.decision == PublicationDecision.BLOCKED_OPPORTUNITY_EXPIRED


def test_8_stale_or5_uses_source_age():
    old = opportunity(detected_at=NOW - timedelta(minutes=20), kind="OR5_BREAKOUT_CONFIRMED")
    result = service().evaluate(draft(generated_at=NOW - timedelta(seconds=10)), old, now=NOW)
    assert result.decision == PublicationDecision.BLOCKED_CONTENT_STALE


def test_9_fresh_relative_performance():
    assert service().evaluate(draft(), opportunity(), now=NOW).decision == PublicationDecision.DRY_RUN_ELIGIBLE


def post(age=60, pattern="OTHER", content_hash="other"):
    return N(published_at=NOW - timedelta(seconds=age), content_pattern_key=pattern, content_hash=content_hash)


def test_10_minimum_spacing():
    result = service().evaluate(draft(), opportunity(), now=NOW, successful_posts=[post(10)])
    assert result.decision == PublicationDecision.BLOCKED_MINIMUM_POST_SPACING


def test_11_daily_hard_max():
    posts = [post(3600 + i, f"p{i}", f"h{i}") for i in range(10)]
    result = service(daily_hard_max=10).evaluate(draft(), opportunity(), now=NOW, successful_posts=posts)
    assert result.decision == PublicationDecision.BLOCKED_DAILY_HARD_MAX


def test_12_soft_target_does_not_generate_filler():
    result = service().evaluate(draft(), opportunity(confirm="DEVELOPING"), now=NOW, successful_posts=[post(3600, f"p{i}", f"h{i}") for i in range(2)])
    assert result.decision == PublicationDecision.BLOCKED_OPPORTUNITY_NOT_CONFIRMED


def test_13_idempotency_key_is_stable():
    s = service(); a = s._request(draft(), opportunity(), PublishMode.DRY_RUN, NOW); b = s._request(draft(), opportunity(), PublishMode.DRY_RUN, NOW)
    assert a.idempotency_key == b.idempotency_key


def test_14_duplicate_content_hash():
    result = service().evaluate(draft(), opportunity(), now=NOW, successful_posts=[post(3600, "other", "hash-1")])
    assert result.decision == PublicationDecision.BLOCKED_DUPLICATE_CONTENT


def test_15_pattern_cooldown():
    result = service().evaluate(draft(), opportunity(), now=NOW, successful_posts=[post(1200, "QQQ_SPY:RELATIVE_PERFORMANCE:QQQ", "other")])
    assert result.decision == PublicationDecision.BLOCKED_COOLDOWN


def test_16_material_change_is_trusted_only_when_new_candidate_is_supplied():
    old = opportunity(); new = opportunity(); new.opportunity_id = "opp-2"; new.opportunity_key = "key-2"; new.content_pattern_key = "QQQ_SPY:RELATIVE_PERFORMANCE:QQQ:occurrence-2"
    result = service().evaluate(draft(), new, now=NOW, successful_posts=[post(3600, old.content_pattern_key, "old")])
    assert result.decision == PublicationDecision.DRY_RUN_ELIGIBLE


def test_17_successful_publication():
    result = service(FakePublishingProvider()).publish(draft(), opportunity(), PublishMode.MANUAL_APPROVAL, now=NOW, approval_status="APPROVED")
    assert result.status == PublicationState.SUCCEEDED and result.platform_post_id


def test_18_retryable_failure_is_bounded_state():
    provider = FakePublishingProvider([ProviderPublicationResult(False, "fake", "r1", 503, retryable=True, error_category="HTTP_RETRYABLE", sanitized_error="temporary")])
    result = service(provider).publish(draft(), opportunity(), PublishMode.MANUAL_APPROVAL, now=NOW, approval_status="APPROVED")
    assert result.status == PublicationState.FAILED_RETRYABLE and result.retryable


def test_19_retry_limit_policy_is_external_and_bounded():
    provider = FakePublishingProvider([ProviderPublicationResult(False, "fake", "r1", 503, retryable=True)] * 3)
    service_instance = service(provider)
    results = [service_instance.publish(draft(), opportunity(), PublishMode.MANUAL_APPROVAL, now=NOW, approval_status="APPROVED") for _ in range(3)]
    assert all(r.status == PublicationState.FAILED_RETRYABLE for r in results)
    assert provider.invocation_count == 3


def test_20_permanent_auth_failure():
    provider = FakePublishingProvider([ProviderPublicationResult(False, "fake", "r1", 401, error_category="HTTP_PERMANENT")])
    result = service(provider).publish(draft(), opportunity(), PublishMode.MANUAL_APPROVAL, now=NOW, approval_status="APPROVED")
    assert result.status == PublicationState.FAILED_FINAL


def test_21_ambiguous_timeout_never_auto_resends():
    provider = FakePublishingProvider([ProviderPublicationResult(False, "fake", "r1", error_category="TIMEOUT_AFTER_TRANSMISSION", transmitted=True)])
    result = service(provider).publish(draft(), opportunity(), PublishMode.MANUAL_APPROVAL, now=NOW, approval_status="APPROVED")
    assert result.status == PublicationState.UNKNOWN_PLATFORM_STATE


def test_22_dry_run_never_invokes_real_provider():
    provider = FakePublishingProvider(); result = service(provider).publish(draft(), opportunity(), PublishMode.DRY_RUN, now=NOW)
    assert result.status == PublicationState.PENDING and provider.invocation_count == 0


def test_23_kill_switch_is_checked_before_send():
    provider = FakePublishingProvider(); instance = service(provider, auto_post_enabled=True)
    instance.auto_post_enabled = False
    result = instance.publish(draft(), opportunity(), PublishMode.AUTO, now=NOW)
    assert result.status == PublicationState.BLOCKED and provider.invocation_count == 0


def test_24_fake_provider_success_has_platform_id():
    result = service(FakePublishingProvider()).publish(draft(), opportunity(), PublishMode.MANUAL_APPROVAL, now=NOW, approval_status="APPROVED")
    assert result.platform_post_id == "fake-post-1"


def test_25_x_adapter_reports_missing_configuration_without_call():
    provider = XApiPublishingProvider(access_token="")
    assert not provider.configured
    result = provider.publish(service()._request(draft(), opportunity(), PublishMode.MANUAL_APPROVAL, NOW, "APPROVED"))
    assert result.error_category == "CONFIGURATION"


def test_26_publication_does_not_resolve_opportunity():
    opp = opportunity(); result = service(FakePublishingProvider()).publish(draft(), opp, PublishMode.MANUAL_APPROVAL, now=NOW, approval_status="APPROVED")
    assert result.status == PublicationState.SUCCEEDED and opp.confirmation_state == "CONFIRMED"


def test_27_provider_invocation_zero_for_dry_run_with_configured_fake():
    provider = FakePublishingProvider(); service(provider).publish(draft(), opportunity(), PublishMode.DRY_RUN, now=NOW)
    assert provider.invocation_count == 0
