from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace as N

from alpha_market_engine.content.observability import (
    ANALYTICS_WINDOWS,
    FakeMetricsProvider,
    FakeReconciliationProvider,
    JobType,
    MetricsIngestionService,
    ProviderMetricsResult,
    ProviderReconciliationResult,
    PublicationReconciliationService,
    ReconciliationState,
    engagement_rate,
    job_idempotency_key,
    metrics_capture_key,
    retry_job_allowed,
    sample_size_label,
    summarize_metrics,
)
from alpha_market_engine.content.publication import PublicationState

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)


def record(state=PublicationState.UNKNOWN_PLATFORM_STATE.value, age=60, text="$QQQ +1.11% vs $SPY +0.36%."):
    attempted = NOW - timedelta(seconds=age)
    return N(publication_request_id="req-1", idempotency_key="idem-1", platform="X", state=state, platform_post_id=None, content_hash="hash-1", content_text=text, attempted_at=attempted, requested_at=attempted, published_at=None)


def found_result(text="$QQQ +1.11% vs $SPY +0.36%."):
    return ProviderReconciliationResult(True, "fake", "r-1", "post-1", "hash-1", text, NOW, "exact_content_hash", 100)


def test_succeeded_needs_no_reconciliation():
    provider = FakeReconciliationProvider()
    result = PublicationReconciliationService(provider).reconcile(record(PublicationState.SUCCEEDED.value), now=NOW)
    assert result.reconciliation_state is ReconciliationState.NOT_REQUIRED
    assert provider.invocation_count == 0


def test_unknown_state_post_found_is_success_without_resend():
    provider = FakeReconciliationProvider([found_result()])
    result = PublicationReconciliationService(provider).reconcile(record(), now=NOW)
    assert result.reconciliation_state is ReconciliationState.PLATFORM_POST_FOUND
    assert result.platform_post_id == "post-1"
    assert not result.retry_allowed
    assert provider.invocation_count == 1


def test_unknown_state_immediate_not_found_is_ambiguous():
    provider = FakeReconciliationProvider([ProviderReconciliationResult(False, "fake", "r-2")])
    result = PublicationReconciliationService(provider).reconcile(record(age=10), now=NOW)
    assert result.reconciliation_state is ReconciliationState.PLATFORM_STATE_AMBIGUOUS
    assert not result.retry_allowed


def test_unknown_state_not_found_after_grace_allows_retry_evaluation():
    provider = FakeReconciliationProvider([ProviderReconciliationResult(False, "fake", "r-3")])
    result = PublicationReconciliationService(provider).reconcile(record(age=601), now=NOW)
    assert result.reconciliation_state is ReconciliationState.PLATFORM_POST_NOT_FOUND
    assert result.retry_allowed
    assert retry_job_allowed(PublicationState.UNKNOWN_PLATFORM_STATE.value, result.reconciliation_state.value, result.retry_allowed)


def test_similar_post_is_not_an_exact_reconciliation():
    provider = FakeReconciliationProvider([found_result("QQQ leadership remains strong")])
    result = PublicationReconciliationService(provider).reconcile(record(age=601), now=NOW)
    # Provider results are trusted only as deterministic exact-match results; a different text is not accepted.
    assert result.reconciliation_state is ReconciliationState.PLATFORM_POST_NOT_FOUND
    assert not result.platform_post_found


def test_stale_sending_enters_reconciliation():
    provider = FakeReconciliationProvider([found_result()])
    result = PublicationReconciliationService(provider).reconcile(record(PublicationState.SENDING.value, age=121), now=NOW)
    assert result.reconciliation_state is ReconciliationState.PLATFORM_POST_FOUND


def test_reconciliation_provider_failures_do_not_resend():
    provider = FakeReconciliationProvider([ProviderReconciliationResult(None, "fake", "r-4", retryable=True, error_category="TIMEOUT")])
    result = PublicationReconciliationService(provider).reconcile(record(age=601), now=NOW)
    assert result.reconciliation_state is ReconciliationState.RECONCILIATION_FAILED_RETRYABLE
    assert not result.retry_allowed


def test_reconciliation_job_identity_and_unknown_state_block():
    key1 = job_idempotency_key(JobType.RECONCILE_PUBLICATION, "req-1")
    key2 = job_idempotency_key(JobType.RECONCILE_PUBLICATION, "req-1")
    assert key1 == key2
    assert not retry_job_allowed(PublicationState.FAILED_RETRYABLE.value, "UNKNOWN_PLATFORM_STATE", True)


def test_metrics_complete_partial_zero_and_windows():
    metrics = ProviderMetricsResult(True, "fake", "m-1", impressions=100, likes=4, replies=2, reposts=3, bookmarks=1, availability_flags={"impressions": True, "bookmarks": True})
    assert engagement_rate(metrics) == Decimal("10.000000")
    provider = FakeMetricsProvider([metrics])
    snapshot = MetricsIngestionService(provider).ingest(platform_post_id="post-1", publication_request_id="req-1", analytics_window="15_MIN", captured_at=NOW)
    assert snapshot is not None
    assert snapshot.engagement_rate == Decimal("10.000000")
    assert snapshot.capture_key == metrics_capture_key("post-1", "15_MIN", NOW)
    assert ANALYTICS_WINDOWS == ("15_MIN", "1_HOUR", "4_HOUR", "24_HOUR")
    assert engagement_rate(ProviderMetricsResult(True, "fake", "m-2", impressions=0)) is None


def test_metrics_not_found_and_duplicate_key_are_safe():
    assert MetricsIngestionService(FakeMetricsProvider([ProviderMetricsResult(False, "fake", "m-3", error_category="NOT_FOUND")])).ingest(platform_post_id="missing", analytics_window="1_HOUR", captured_at=NOW) is None
    assert metrics_capture_key("post-1", "15_MIN", NOW) == metrics_capture_key("post-1", "15_MIN", NOW)


def test_performance_summary_sample_labels_and_window_separation():
    rows = [N(analytics_window="15_MIN", impressions=10, engagement_rate=Decimal("1.0")), N(analytics_window="24_HOUR", impressions=100, engagement_rate=Decimal("4.0"))]
    summary = summarize_metrics(rows, group_key="QQQ_LEADERSHIP", analytics_window="15_MIN")
    assert summary.post_count == 1
    assert summary.average_impressions == Decimal("10.000000")
    assert summary.sample_size_label == "INSUFFICIENT_SAMPLE"
    assert sample_size_label(5) == "EARLY_SIGNAL"
    assert sample_size_label(20) == "MODERATE_SAMPLE"
    assert sample_size_label(50) == "STRONGER_SAMPLE"
