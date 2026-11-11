from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from statistics import median
from typing import Any, Protocol

import httpx

from alpha_market_engine.content.publication import PublicationState
from alpha_market_engine.auth.x_oauth import XOAuthError


class ReconciliationState(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    PLATFORM_POST_FOUND = "PLATFORM_POST_FOUND"
    PLATFORM_POST_NOT_FOUND = "PLATFORM_POST_NOT_FOUND"
    PLATFORM_STATE_AMBIGUOUS = "PLATFORM_STATE_AMBIGUOUS"
    RECONCILIATION_UNAVAILABLE = "RECONCILIATION_UNAVAILABLE"
    RECONCILIATION_FAILED_RETRYABLE = "RECONCILIATION_FAILED_RETRYABLE"
    RECONCILIATION_FAILED_FINAL = "RECONCILIATION_FAILED_FINAL"


@dataclass(frozen=True, slots=True)
class ProviderReconciliationResult:
    found: bool | None
    provider: str
    provider_request_id: str
    platform_post_id: str | None = None
    matched_content_hash: str | None = None
    matched_content_text: str | None = None
    matched_created_at: datetime | None = None
    match_method: str | None = None
    confidence: int = 0
    retryable: bool = False
    error_category: str | None = None
    sanitized_error: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PublicationReconciliationResult:
    publication_request_id: str
    idempotency_key: str
    platform: str
    original_publication_state: str
    reconciliation_state: ReconciliationState
    platform_post_found: bool
    platform_post_id: str | None
    matched_content_hash: str | None
    matched_content_text: str | None
    matched_created_at: datetime | None
    match_method: str | None
    confidence: int
    retry_allowed: bool
    reconciled_at: datetime
    provider: str
    provider_request_id: str | None
    error_category: str | None
    sanitized_error: str | None
    warnings: tuple[str, ...]


class PublicationReconciliationProvider(Protocol):
    def reconcile(self, publication_record: Any, *, window_start: datetime, window_end: datetime) -> ProviderReconciliationResult: ...


class FakeReconciliationProvider:
    def __init__(self, results: list[ProviderReconciliationResult] | None = None) -> None:
        self.results = list(results or [])
        self.invocation_count = 0

    def queue(self, result: ProviderReconciliationResult) -> None:
        self.results.append(result)

    def reconcile(self, publication_record: Any, *, window_start: datetime, window_end: datetime) -> ProviderReconciliationResult:
        self.invocation_count += 1
        if self.results:
            return self.results.pop(0)
        return ProviderReconciliationResult(None, "fake", f"fake-reconcile-{self.invocation_count}", warnings=("NO_FAKE_RESULT",))


class XReconciliationProvider:
    """Official X v2 adapter; requires OAuth2 user-context access and never scrapes."""
    BASE_URL = "https://api.x.com/2"

    def __init__(self, access_token: str | None = None, *, token_provider: Any | None = None, expected_username: str | None = None, timeout: float = 10.0):
        self.access_token = access_token or ""
        self.token_provider = token_provider
        self.expected_username = expected_username or os.getenv("X_EXPECTED_USERNAME", "")
        self.timeout = timeout
        self._verified_user_id: str | None = None

    @property
    def capability(self) -> str:
        return "CONFIGURED_READY" if self.access_token.strip() and self.access_token.strip().lower() not in {"disabled", "none", "unset"} else "NOT_CONFIGURED"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    def _access_token_for_request(self) -> str:
        if self.token_provider is None:
            if not self.access_token.strip() or self.access_token.strip().lower() in {"disabled", "none", "unset"}:
                raise XOAuthError("CONFIGURATION_ERROR", "X reconciliation token is not configured")
            return self.access_token
        token = self.token_provider.get_valid_access_token({"tweet.read", "users.read"})
        token_set = self.token_provider.store.load()
        if token_set is None or token_set.identity_verified_at is None:
            raise XOAuthError("AUTHENTICATED_ACCOUNT_MISMATCH", "Authenticated X account identity is not verified")
        if self.expected_username and (token_set.authorized_username or "").casefold() != self.expected_username.lstrip("@").casefold():
            raise XOAuthError("AUTHENTICATED_ACCOUNT_MISMATCH", "Authenticated X username does not match the expected account")
        self._verified_user_id = token_set.authorized_user_id
        return token

    def _result_from_post(self, post: dict[str, Any], publication_record: Any) -> ProviderReconciliationResult:
        data = post.get("data") or {}
        text = data.get("text")
        created = data.get("created_at")
        created_at = datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None
        content_hash = hashlib.sha256(text.encode()).hexdigest() if text is not None else None
        exact = content_hash == publication_record.content_hash or (text is not None and " ".join(text.split()) == " ".join(publication_record.content_text.split()))
        return ProviderReconciliationResult(exact, "x-api", data.get("id", "x-post-lookup"), data.get("id") if exact else None, content_hash if exact else None, text if exact else None, created_at if exact else None, "exact_content_hash" if content_hash == publication_record.content_hash else "exact_normalized_text" if exact else None, 100 if exact else 0, False, None, None, ())

    def reconcile(self, publication_record: Any, *, window_start: datetime, window_end: datetime) -> ProviderReconciliationResult:
        try:
            self.access_token = self._access_token_for_request()
        except XOAuthError as exc:
            return ProviderReconciliationResult(None, "x-api", "not-called", error_category=exc.category, sanitized_error=str(exc))
        if self.capability == "NOT_CONFIGURED":
            return ProviderReconciliationResult(None, "x-api", "not-called", error_category="CONFIGURATION", sanitized_error="X reconciliation credentials are not configured", warnings=("X_RECONCILIATION_NOT_CONFIGURED",))
        try:
            with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
                if publication_record.platform_post_id:
                    response = client.get(f"{self.BASE_URL}/tweets/{publication_record.platform_post_id}", params={"tweet.fields": "created_at"})
                    if response.status_code == 404: return ProviderReconciliationResult(False, "x-api", "post-lookup", error_category="POST_NOT_FOUND")
                    response.raise_for_status()
                    return self._result_from_post(response.json(), publication_record)
                user_id = self._verified_user_id
                if not user_id:
                    me = client.get(f"{self.BASE_URL}/users/me")
                    me.raise_for_status(); user_id = me.json().get("data", {}).get("id")
                if not user_id: return ProviderReconciliationResult(None, "x-api", "user-lookup", error_category="MALFORMED_RESPONSE", sanitized_error="Authenticated user identity was not returned")
                response = client.get(f"{self.BASE_URL}/users/{user_id}/tweets", params={"start_time": _aware(window_start).isoformat().replace("+00:00", "Z"), "end_time": _aware(window_end).isoformat().replace("+00:00", "Z"), "max_results": 100, "tweet.fields": "created_at"})
                response.raise_for_status()
                for post in response.json().get("data", []):
                    result = self._result_from_post({"data": post}, publication_record)
                    if result.found: return result
                return ProviderReconciliationResult(False, "x-api", "timeline-lookup", error_category="POST_NOT_FOUND")
        except httpx.TimeoutException:
            return ProviderReconciliationResult(None, "x-api", "timeout", retryable=True, error_category="TIMEOUT", sanitized_error="X reconciliation request timed out")
        except httpx.HTTPStatusError as exc:
            retryable = exc.response.status_code == 429 or exc.response.status_code >= 500
            return ProviderReconciliationResult(None, "x-api", "http-error", retryable=retryable, error_category="HTTP_RETRYABLE" if retryable else "HTTP_FINAL", sanitized_error=f"X reconciliation returned HTTP {exc.response.status_code}")
        except httpx.HTTPError:
            return ProviderReconciliationResult(None, "x-api", "transport-error", retryable=True, error_category="TRANSPORT", sanitized_error="X reconciliation transport failure")


RECONCILIATION_GRACE_SECONDS = 5 * 60
STALE_SENDING_SECONDS = 120
SEARCH_BEFORE_SECONDS = 120
SEARCH_AFTER_SECONDS = 600


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class PublicationReconciliationService:
    def __init__(self, provider: PublicationReconciliationProvider, *, repository: Any | None = None, grace_seconds: int = RECONCILIATION_GRACE_SECONDS, stale_sending_seconds: int = STALE_SENDING_SECONDS, safety: Any | None = None):
        self.provider = provider
        self.repository = repository
        self.grace_seconds = grace_seconds
        self.stale_sending_seconds = stale_sending_seconds
        self.safety = safety

    def reconcile(self, publication_record: Any, *, now: datetime | None = None, force: bool = False) -> PublicationReconciliationResult:
        now = now or datetime.now(UTC)
        original = str(publication_record.state)
        if original == PublicationState.SUCCEEDED.value:
            return PublicationReconciliationResult(publication_record.publication_request_id, publication_record.idempotency_key, publication_record.platform, original, ReconciliationState.NOT_REQUIRED, True, publication_record.platform_post_id, publication_record.content_hash, publication_record.content_text, publication_record.published_at, "persisted_platform_id", 100, False, now, "local", None, None, None, ())
        existing = self.repository.get_reconciliation(publication_record.publication_request_id) if self.repository is not None else None
        if existing is not None and existing.reconciliation_state == ReconciliationState.PLATFORM_POST_FOUND.value and not force:
            return self._from_record(existing)
        attempted_at = _aware(publication_record.attempted_at or publication_record.requested_at)
        age = max(0, int((now - attempted_at).total_seconds()))
        if original == PublicationState.UNKNOWN_PLATFORM_STATE or (original == PublicationState.SENDING.value and age >= self.stale_sending_seconds):
            window_start = attempted_at - timedelta(seconds=SEARCH_BEFORE_SECONDS)
            window_end = attempted_at + timedelta(seconds=SEARCH_AFTER_SECONDS)
            result = self.provider.reconcile(publication_record, window_start=window_start, window_end=window_end)
            if self.safety is not None and result.error_category and any(token in result.error_category.upper() for token in ("AUTH", "REFRESH", "IDENTITY", "SCOPE")):
                self.safety.auth_failure(result.error_category)
            if result.found and result.matched_content_hash and result.matched_content_hash != publication_record.content_hash:
                result = replace(result, found=False, platform_post_id=None, warnings=tuple(result.warnings) + ("MATCH_CONTENT_HASH_MISMATCH",))
            elif result.found and result.matched_content_text and " ".join(result.matched_content_text.split()) != " ".join(publication_record.content_text.split()):
                result = replace(result, found=False, platform_post_id=None, warnings=tuple(result.warnings) + ("MATCH_CONTENT_TEXT_MISMATCH",))
            if result.found is True:
                state = ReconciliationState.PLATFORM_POST_FOUND; retry_allowed = False
            elif result.found is False and age < self.grace_seconds:
                state = ReconciliationState.PLATFORM_STATE_AMBIGUOUS; retry_allowed = False
            elif result.found is False:
                state = ReconciliationState.PLATFORM_POST_NOT_FOUND; retry_allowed = True
            elif result.error_category == "CONFIGURATION":
                state = ReconciliationState.RECONCILIATION_UNAVAILABLE; retry_allowed = False
            elif result.retryable:
                state = ReconciliationState.RECONCILIATION_FAILED_RETRYABLE; retry_allowed = False
            else:
                state = ReconciliationState.RECONCILIATION_FAILED_FINAL; retry_allowed = False
            reconciled = PublicationReconciliationResult(publication_record.publication_request_id, publication_record.idempotency_key, publication_record.platform, original, state, result.found is True, result.platform_post_id, result.matched_content_hash, result.matched_content_text, result.matched_created_at, result.match_method, result.confidence, retry_allowed, now, result.provider, result.provider_request_id, result.error_category, result.sanitized_error, result.warnings)
            if self.repository is not None:
                self.repository.save_reconciliation(reconciled)
                if state == ReconciliationState.PLATFORM_POST_FOUND:
                    self.repository.mark_publication_reconciled_success(publication_record.idempotency_key, result.platform_post_id, result.matched_created_at or now)
                self.repository.add_system_event_sync(event_type="PUBLICATION_RECONCILED_SUCCEEDED" if state == ReconciliationState.PLATFORM_POST_FOUND else "PUBLICATION_RECONCILED_NOT_FOUND" if state == ReconciliationState.PLATFORM_POST_NOT_FOUND else "PUBLICATION_RECONCILIATION_AMBIGUOUS" if state == ReconciliationState.PLATFORM_STATE_AMBIGUOUS else "PUBLICATION_RECONCILIATION_FAILED", message=f"publication reconciliation {state.value.lower()}", metadata={"publication_request_id": publication_record.publication_request_id, "retry_allowed": retry_allowed})
            return reconciled
        return PublicationReconciliationResult(publication_record.publication_request_id, publication_record.idempotency_key, publication_record.platform, original, ReconciliationState.NOT_REQUIRED, False, None, None, None, None, None, 0, False, now, "local", None, None, None, ("PUBLICATION_STATE_DOES_NOT_REQUIRE_RECONCILIATION",))

    def _from_record(self, record: Any) -> PublicationReconciliationResult:
        return PublicationReconciliationResult(record.publication_request_id, record.idempotency_key, record.platform, record.original_publication_state, ReconciliationState(record.reconciliation_state), record.platform_post_found, record.platform_post_id, record.matched_content_hash, record.matched_content_text, record.matched_created_at, record.match_method, record.confidence, record.retry_allowed, record.reconciled_at, record.provider, record.provider_request_id, record.error_category, record.sanitized_error, tuple(record.warnings_json or []))


class JobType(str, Enum):
    RECONCILE_PUBLICATION = "RECONCILE_PUBLICATION"
    RETRY_PUBLICATION = "RETRY_PUBLICATION"
    INGEST_POST_METRICS = "INGEST_POST_METRICS"


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"


@dataclass(frozen=True, slots=True)
class PublicationJobSpec:
    job_type: JobType
    entity_id: str
    idempotency_key: str
    not_before: datetime
    attempt_number: int = 0
    max_attempts: int = 3
    status: JobStatus = JobStatus.PENDING
    last_error: str | None = None
    analytics_window: str | None = None


def job_idempotency_key(job_type: JobType, entity_id: str) -> str:
    return hashlib.sha256(f"{job_type.value}:{entity_id}".encode()).hexdigest()


def retry_job_allowed(publication_state: str, reconciliation_state: str | None, retry_allowed: bool) -> bool:
    return (publication_state == PublicationState.FAILED_RETRYABLE.value or (reconciliation_state == ReconciliationState.PLATFORM_POST_NOT_FOUND.value and retry_allowed)) and reconciliation_state not in {ReconciliationState.PLATFORM_STATE_AMBIGUOUS.value, "UNKNOWN_PLATFORM_STATE"}


@dataclass(frozen=True, slots=True)
class ProviderMetricsResult:
    found: bool
    provider: str
    provider_request_id: str
    impressions: int | None = None
    likes: int | None = None
    replies: int | None = None
    reposts: int | None = None
    bookmarks: int | None = None
    quote_posts: int | None = None
    profile_clicks: int | None = None
    url_clicks: int | None = None
    retryable: bool = False
    error_category: str | None = None
    sanitized_error: str | None = None
    availability_flags: dict[str, bool] | None = None


class PostMetricsProvider(Protocol):
    def get_metrics(self, platform_post_id: str) -> ProviderMetricsResult: ...


class XMetricsProvider:
    BASE_URL = "https://api.x.com/2"

    def __init__(self, access_token: str | None = None, *, token_provider: Any | None = None, client: httpx.Client | None = None, timeout: float = 10.0):
        self.access_token = access_token or ""
        self.token_provider = token_provider
        self.client = client or httpx.Client(timeout=timeout)
        self.timeout = timeout

    @property
    def capability(self) -> str:
        return "CONFIGURED_READY" if self.access_token.strip() and self.access_token.strip().lower() not in {"disabled", "none", "unset"} else "NOT_CONFIGURED"

    def get_metrics(self, platform_post_id: str) -> ProviderMetricsResult:
        if self.token_provider is not None:
            try:
                self.access_token = self.token_provider.get_valid_access_token({"tweet.read"})
            except XOAuthError as exc:
                return ProviderMetricsResult(False, "x-api", "not-called", error_category=exc.category, sanitized_error=str(exc))
        if self.capability == "NOT_CONFIGURED":
            return ProviderMetricsResult(False, "x-api", "not-called", error_category="CONFIGURATION", sanitized_error="X metrics credentials are not configured")
        try:
            response = self.client.get(f"{self.BASE_URL}/tweets/{platform_post_id}", headers={"Authorization": f"Bearer {self.access_token}"}, params={"tweet.fields": "public_metrics,non_public_metrics"})
            if response.status_code == 404: return ProviderMetricsResult(False, "x-api", "metrics-lookup", error_category="POST_NOT_FOUND")
            response.raise_for_status()
            data = response.json().get("data", {})
            public = data.get("public_metrics", {})
            private = data.get("non_public_metrics", {})
            return ProviderMetricsResult(True, "x-api", "metrics-lookup", public.get("impression_count"), public.get("like_count"), public.get("reply_count"), public.get("retweet_count"), private.get("bookmark_count"), public.get("quote_count"), private.get("user_profile_clicks"), private.get("url_link_clicks"), availability_flags={key: key in public or key in private for key in ("impression_count", "like_count", "reply_count", "retweet_count", "bookmark_count", "quote_count", "user_profile_clicks", "url_link_clicks")})
        except httpx.TimeoutException:
            return ProviderMetricsResult(False, "x-api", "timeout", retryable=True, error_category="TIMEOUT", sanitized_error="X metrics request timed out")
        except httpx.HTTPStatusError as exc:
            retryable = exc.response.status_code == 429 or exc.response.status_code >= 500
            return ProviderMetricsResult(False, "x-api", "http-error", retryable=retryable, error_category="HTTP_RETRYABLE" if retryable else "HTTP_FINAL", sanitized_error=f"X metrics returned HTTP {exc.response.status_code}")
        except httpx.HTTPError:
            return ProviderMetricsResult(False, "x-api", "transport-error", retryable=True, error_category="TRANSPORT", sanitized_error="X metrics transport failure")


@dataclass(frozen=True, slots=True)
class PostMetricsSnapshot:
    capture_key: str
    platform: str
    platform_post_id: str
    publication_request_id: str | None
    captured_at: datetime
    analytics_window: str
    impressions: int | None
    likes: int | None
    replies: int | None
    reposts: int | None
    bookmarks: int | None
    quote_posts: int | None
    profile_clicks: int | None
    url_clicks: int | None
    engagement_rate: Decimal | None
    source_provider: str
    provider_payload_version: str | None
    availability_flags: dict[str, bool]


class MetricsIngestionService:
    def __init__(self, provider: PostMetricsProvider, *, repository: Any | None = None, safety: Any | None = None):
        self.provider = provider
        self.repository = repository
        self.safety = safety
        self.last_result: ProviderMetricsResult | None = None
    def ingest(self, *, platform_post_id: str, platform: str = "X", publication_request_id: str | None = None, analytics_window: str, captured_at: datetime | None = None) -> PostMetricsSnapshot | None:
        captured_at = captured_at or datetime.now(UTC)
        result = self.provider.get_metrics(platform_post_id)
        self.last_result = result
        if self.safety is not None and result.error_category and any(token in result.error_category.upper() for token in ("AUTH", "REFRESH", "IDENTITY", "SCOPE")):
            self.safety.auth_failure(result.error_category)
        if not result.found:
            if self.repository is not None:
                self.repository.add_system_event_sync(event_type="POST_METRICS_FAILED", message="post metrics lookup failed", metadata={"platform_post_id": platform_post_id, "error_category": result.error_category})
            return None
        snapshot = PostMetricsSnapshot(metrics_capture_key(platform_post_id, analytics_window, captured_at), platform, platform_post_id, publication_request_id, captured_at, analytics_window, result.impressions, result.likes, result.replies, result.reposts, result.bookmarks, result.quote_posts, result.profile_clicks, result.url_clicks, engagement_rate(result), result.provider, "1", result.availability_flags or {})
        if self.repository is not None:
            self.repository.save_metrics_snapshot(snapshot)
            self.repository.add_system_event_sync(event_type="POST_METRICS_PARTIAL" if any(not available for available in snapshot.availability_flags.values()) else "POST_METRICS_INGESTED", message="post metrics snapshot ingested", metadata={"platform_post_id": platform_post_id, "analytics_window": analytics_window, "capture_key": snapshot.capture_key})
        return snapshot


class FakeMetricsProvider:
    def __init__(self, results: list[ProviderMetricsResult] | None = None) -> None:
        self.results = list(results or [])
        self.invocation_count = 0

    def queue(self, result: ProviderMetricsResult) -> None:
        self.results.append(result)

    def get_metrics(self, platform_post_id: str) -> ProviderMetricsResult:
        self.invocation_count += 1
        return self.results.pop(0) if self.results else ProviderMetricsResult(False, "fake", f"fake-metrics-{self.invocation_count}", error_category="NOT_FOUND")


def engagement_rate(metrics: ProviderMetricsResult) -> Decimal | None:
    if metrics.impressions is None or metrics.impressions <= 0:
        return None
    total = sum(value or 0 for value in (metrics.likes, metrics.replies, metrics.reposts, metrics.bookmarks))
    return (Decimal(total) / Decimal(metrics.impressions) * Decimal(100)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


ANALYTICS_WINDOWS = ("15_MIN", "1_HOUR", "4_HOUR", "24_HOUR")


def metrics_capture_key(platform_post_id: str, window: str, captured_at: datetime) -> str:
    bucket = captured_at.astimezone(UTC).replace(second=0, microsecond=0).isoformat()
    return hashlib.sha256(f"{platform_post_id}:{window}:{bucket}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PerformanceSummary:
    group_key: str
    analytics_window: str
    post_count: int
    average_impressions: Decimal | None
    median_impressions: Decimal | None
    average_engagement_rate: Decimal | None
    median_engagement_rate: Decimal | None
    sample_size_label: str


def sample_size_label(count: int) -> str:
    if count < 5: return "INSUFFICIENT_SAMPLE"
    if count < 20: return "EARLY_SIGNAL"
    if count < 50: return "MODERATE_SAMPLE"
    return "STRONGER_SAMPLE"


def summarize_metrics(rows: list[Any], *, group_key: str, analytics_window: str) -> PerformanceSummary:
    selected = [row for row in rows if getattr(row, "analytics_window", None) == analytics_window]
    impressions = [Decimal(str(row.impressions)) for row in selected if row.impressions is not None]
    rates = [Decimal(str(row.engagement_rate)) for row in selected if row.engagement_rate is not None]
    return PerformanceSummary(group_key, analytics_window, len(selected), (sum(impressions) / len(impressions)).quantize(Decimal("0.000001")) if impressions else None, median(impressions).quantize(Decimal("0.000001")) if impressions else None, (sum(rates) / len(rates)).quantize(Decimal("0.000001")) if rates else None, median(rates).quantize(Decimal("0.000001")) if rates else None, sample_size_label(len(selected)))
