from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Protocol

import httpx

from alpha_market_engine.content.drafting import GeneratedContentDraft
from alpha_market_engine.auth.x_oauth import XOAuthError


class PublishMode(str, Enum):
    DRY_RUN = "DRY_RUN"
    MANUAL_APPROVAL = "MANUAL_APPROVAL"
    AUTO = "AUTO"
    LIMITED_AUTO = "LIMITED_AUTO"


class PublicationDecision(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    DRY_RUN_ELIGIBLE = "DRY_RUN_ELIGIBLE"
    BLOCKED_AUTO_POST_DISABLED = "BLOCKED_AUTO_POST_DISABLED"
    BLOCKED_DRAFT_NOT_APPROVED = "BLOCKED_DRAFT_NOT_APPROVED"
    BLOCKED_HUMAN_APPROVAL_REQUIRED = "BLOCKED_HUMAN_APPROVAL_REQUIRED"
    BLOCKED_OPPORTUNITY_RESOLVED = "BLOCKED_OPPORTUNITY_RESOLVED"
    BLOCKED_OPPORTUNITY_EXPIRED = "BLOCKED_OPPORTUNITY_EXPIRED"
    BLOCKED_OPPORTUNITY_NOT_CONFIRMED = "BLOCKED_OPPORTUNITY_NOT_CONFIRMED"
    BLOCKED_CONTENT_STALE = "BLOCKED_CONTENT_STALE"
    BLOCKED_COOLDOWN = "BLOCKED_COOLDOWN"
    BLOCKED_MINIMUM_POST_SPACING = "BLOCKED_MINIMUM_POST_SPACING"
    BLOCKED_DAILY_HARD_MAX = "BLOCKED_DAILY_HARD_MAX"
    BLOCKED_ALREADY_PUBLISHED = "BLOCKED_ALREADY_PUBLISHED"
    BLOCKED_DUPLICATE_CONTENT = "BLOCKED_DUPLICATE_CONTENT"
    BLOCKED_MISSING_PLATFORM_CONFIG = "BLOCKED_MISSING_PLATFORM_CONFIG"
    BLOCKED_PLATFORM_UNAVAILABLE = "BLOCKED_PLATFORM_UNAVAILABLE"


class PublicationState(str, Enum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    UNKNOWN_PLATFORM_STATE = "UNKNOWN_PLATFORM_STATE"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class PublicationRequest:
    publication_request_id: str
    idempotency_key: str
    analysis_id: str
    opportunity_id: str
    opportunity_key: str
    draft_id: str
    draft_request_id: str
    platform: str
    content_text: str
    content_hash: str
    approved_at: datetime
    requested_at: datetime
    source_detected_at: datetime
    source_confirmation_state: str
    source_decision: str
    content_pattern_key: str
    publish_mode: PublishMode
    auto_post_enabled: bool
    approval_status: str = "NOT_REQUIRED"


@dataclass(frozen=True, slots=True)
class PublicationEligibilityResult:
    eligible: bool
    decision: PublicationDecision
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    evaluated_at: datetime
    draft_status: str
    opportunity_status: str
    opportunity_confirmation_state: str
    opportunity_age_seconds: int
    draft_age_seconds: int
    content_freshness_status: str
    cooldown_status: str
    daily_post_count: int
    daily_hard_max: int
    recent_post_age_seconds: int | None
    minimum_post_spacing_seconds: int
    duplicate_publication_status: str


@dataclass(frozen=True, slots=True)
class ProviderPublicationResult:
    ok: bool
    provider: str
    request_id: str
    status_code: int | None = None
    platform_post_id: str | None = None
    retryable: bool = False
    error_category: str | None = None
    sanitized_error: str | None = None
    latency_ms: int = 0
    transmitted: bool = False


@dataclass(frozen=True, slots=True)
class PublicationResult:
    publication_request_id: str
    idempotency_key: str
    platform: str
    status: PublicationState
    platform_post_id: str | None
    platform_response_status: int | None
    attempt_number: int
    published_at: datetime | None
    error_category: str | None
    error_code: str | None
    sanitized_error: str | None
    retryable: bool
    content_hash: str


class ContentPublishingProvider(Protocol):
    def publish(self, request: PublicationRequest) -> ProviderPublicationResult: ...


class FakePublishingProvider:
    def __init__(self, responses: list[ProviderPublicationResult] | None = None) -> None:
        self.responses = list(responses or [])
        self.invocation_count = 0

    def queue(self, result: ProviderPublicationResult) -> None:
        self.responses.append(result)

    def publish(self, request: PublicationRequest) -> ProviderPublicationResult:
        self.invocation_count += 1
        if self.responses:
            return self.responses.pop(0)
        return ProviderPublicationResult(True, "fake", f"fake-request-{self.invocation_count}", 201, f"fake-post-{self.invocation_count}", transmitted=True)


class XApiPublishingProvider:
    endpoint = "https://api.x.com/2/tweets"

    def __init__(self, *, access_token: str | None = None, token_provider: Any | None = None, expected_username: str | None = None, client: httpx.Client | None = None) -> None:
        self.access_token = access_token or os.getenv("X_ACCESS_TOKEN", "")
        self.token_provider = token_provider
        self.expected_username = expected_username or os.getenv("X_EXPECTED_USERNAME", "")
        self.client = client or httpx.Client(timeout=20)

    @property
    def configured(self) -> bool:
        token = self.access_token.strip().lower()
        return bool(token and token not in {"disabled", "unset", "none"})

    def publish(self, request: PublicationRequest) -> ProviderPublicationResult:
        try:
            access_token = self._access_token_for_request()
        except XOAuthError as exc:
            return ProviderPublicationResult(False, "x-api", str(uuid.uuid4()), error_category=exc.category, sanitized_error=str(exc))
        started = time.monotonic()
        transmitted = False
        request_id = str(uuid.uuid4())
        try:
            transmitted = True
            response = self.client.post(self.endpoint, headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}, json={"text": request.content_text})
            try:
                data = response.json()
            except ValueError:
                data = {}
            if response.status_code in {429, 500, 502, 503, 504}:
                return ProviderPublicationResult(False, "x-api", request_id, response.status_code, retryable=True, error_category="HTTP_RETRYABLE", sanitized_error=f"X API returned HTTP {response.status_code}", latency_ms=int((time.monotonic()-started)*1000), transmitted=transmitted)
            if response.status_code >= 400:
                return ProviderPublicationResult(False, "x-api", request_id, response.status_code, error_category="HTTP_PERMANENT", sanitized_error=f"X API returned HTTP {response.status_code}", latency_ms=int((time.monotonic()-started)*1000), transmitted=transmitted)
            post_id = str(data.get("data", {}).get("id")) if data.get("data", {}).get("id") else None
            if not post_id:
                return ProviderPublicationResult(False, "x-api", request_id, response.status_code, error_category="AMBIGUOUS_RESPONSE", sanitized_error="X accepted a response without a platform post ID", latency_ms=int((time.monotonic()-started)*1000), transmitted=transmitted)
            return ProviderPublicationResult(True, "x-api", request_id, response.status_code, post_id, latency_ms=int((time.monotonic()-started)*1000), transmitted=transmitted)
        except httpx.TimeoutException:
            return ProviderPublicationResult(False, "x-api", request_id, error_category="TIMEOUT_AFTER_TRANSMISSION" if transmitted else "TIMEOUT_BEFORE_TRANSMISSION", sanitized_error="X API request timed out", transmitted=transmitted)
        except httpx.HTTPError:
            return ProviderPublicationResult(False, "x-api", request_id, error_category="CONNECTION_FAILURE", sanitized_error="X API connection failed", retryable=not transmitted, transmitted=transmitted)


    def _access_token_for_request(self) -> str:
        if self.token_provider is None:
            if not self.configured:
                raise XOAuthError("CONFIGURATION", "X access token is not configured")
            return self.access_token
        token = self.token_provider.get_valid_access_token({"tweet.write"})
        token_set = self.token_provider.store.load()
        if token_set is None or token_set.identity_verified_at is None:
            raise XOAuthError("AUTHENTICATED_ACCOUNT_MISMATCH", "Authenticated X account identity is not verified")
        if self.expected_username and (token_set.authorized_username or "").casefold() != self.expected_username.lstrip("@").casefold():
            raise XOAuthError("AUTHENTICATED_ACCOUNT_MISMATCH", "Authenticated X username does not match the expected account")
        return token


def publication_idempotency_key(request: PublicationRequest) -> str:
    canonical = json.dumps({"platform": request.platform, "draft_id": request.draft_id, "content_hash": request.content_hash}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


FRESHNESS_SECONDS = {
    "OR5": 15 * 60, "OR15": 25 * 60, "OR30": 40 * 60, "VWAP": 25 * 60,
    "RELATIVE_PERFORMANCE": 40 * 60, "REGIME_CHANGE": 45 * 60, "REGIME": 90 * 60,
}
MINIMUM_POST_SPACING_SECONDS = 12 * 60
DAILY_HARD_MAX = 10


def freshness_limit(opportunity: Any) -> int:
    text = str(getattr(getattr(opportunity, "opportunity_type", ""), "value", getattr(opportunity, "opportunity_type", "")))
    if "OR5" in text: return FRESHNESS_SECONDS["OR5"]
    if "OR15" in text: return FRESHNESS_SECONDS["OR15"]
    if "OR30" in text: return FRESHNESS_SECONDS["OR30"]
    if "VWAP" in text: return FRESHNESS_SECONDS["VWAP"]
    if "REGIME_CHANGE" in text: return FRESHNESS_SECONDS["REGIME_CHANGE"]
    if "REGIME" in text or "ROTATION" in text: return FRESHNESS_SECONDS["REGIME"]
    return FRESHNESS_SECONDS["RELATIVE_PERFORMANCE"]


class ContentPublicationService:
    def __init__(self, provider: ContentPublishingProvider, *, repository: Any | None = None, auto_post_enabled: bool = False, daily_hard_max: int = DAILY_HARD_MAX, minimum_spacing_seconds: int = MINIMUM_POST_SPACING_SECONDS, safety: Any | None = None, before_provider_hook: Any | None = None, after_provider_success_hook: Any | None = None) -> None:
        self.provider = provider
        self.repository = repository
        self.auto_post_enabled = auto_post_enabled
        self.daily_hard_max = daily_hard_max
        self.minimum_spacing_seconds = minimum_spacing_seconds
        self.safety = safety
        self.before_provider_hook = before_provider_hook
        self.after_provider_success_hook = after_provider_success_hook

    def _request(self, draft: GeneratedContentDraft, opportunity: Any, mode: PublishMode, now: datetime, approval_status: str = "NOT_REQUIRED") -> PublicationRequest:
        request = PublicationRequest(str(uuid.uuid4()), "", draft.analysis_id, draft.opportunity_id, draft.opportunity_key, draft.draft_id, draft.draft_request_id, draft.platform, draft.content_text, draft.content_hash, draft.generated_at, now, opportunity.detected_at, str(getattr(opportunity.confirmation_state, "value", opportunity.confirmation_state)), str(getattr(opportunity.decision, "value", opportunity.decision)), opportunity.content_pattern_key, mode, self.auto_post_enabled, approval_status)
        return replace(request, idempotency_key=publication_idempotency_key(request))

    def evaluate(self, draft: GeneratedContentDraft, opportunity: Any, mode: PublishMode = PublishMode.DRY_RUN, *, now: datetime | None = None, approval_status: str = "NOT_REQUIRED", successful_posts: list[Any] | None = None) -> PublicationEligibilityResult:
        now = now or datetime.now(UTC)
        successful_posts = successful_posts or []
        age = max(0, int((now - opportunity.detected_at).total_seconds()))
        draft_age = max(0, int((now - draft.generated_at).total_seconds()))
        reasons: list[str] = []
        warnings: list[str] = []
        confirmation = str(getattr(opportunity.confirmation_state, "value", opportunity.confirmation_state))
        opp_status = str(getattr(opportunity, "status", "ACTIVE"))
        if draft.final_decision != "APPROVE" or draft.validation_status not in {"VALID", "APPROVED"} or not draft.content_hash or draft.quality_score < 70:
            reasons.append(PublicationDecision.BLOCKED_DRAFT_NOT_APPROVED.value)
        if mode == PublishMode.MANUAL_APPROVAL and approval_status != "APPROVED":
            reasons.append(PublicationDecision.BLOCKED_HUMAN_APPROVAL_REQUIRED.value)
        if mode in {PublishMode.AUTO, PublishMode.LIMITED_AUTO} and not self.auto_post_enabled:
            reasons.append(PublicationDecision.BLOCKED_AUTO_POST_DISABLED.value)
        if confirmation == "RESOLVED": reasons.append(PublicationDecision.BLOCKED_OPPORTUNITY_RESOLVED.value)
        if confirmation in {"EXPIRED"} or opp_status.upper() == "EXPIRED": reasons.append(PublicationDecision.BLOCKED_OPPORTUNITY_EXPIRED.value)
        if confirmation not in {"CONFIRMED"} and "REGIME" not in str(getattr(opportunity.opportunity_type, "value", opportunity.opportunity_type)):
            reasons.append(PublicationDecision.BLOCKED_OPPORTUNITY_NOT_CONFIRMED.value)
        limit = freshness_limit(opportunity)
        freshness = "FRESH" if age <= limit else "STALE"
        if freshness == "STALE": reasons.append(PublicationDecision.BLOCKED_CONTENT_STALE.value)
        recent_age = None
        if successful_posts:
            latest = max(successful_posts, key=lambda item: item.published_at)
            recent_age = max(0, int((now - latest.published_at).total_seconds()))
            if recent_age < self.minimum_spacing_seconds: reasons.append(PublicationDecision.BLOCKED_MINIMUM_POST_SPACING.value)
            if any(getattr(item, "content_pattern_key", None) == opportunity.content_pattern_key for item in successful_posts if (now - item.published_at).total_seconds() < 45 * 60):
                reasons.append(PublicationDecision.BLOCKED_COOLDOWN.value)
            if any(getattr(item, "content_hash", None) == draft.content_hash for item in successful_posts): reasons.append(PublicationDecision.BLOCKED_DUPLICATE_CONTENT.value)
        daily_count = sum(1 for item in successful_posts if item.published_at.date() == now.date())
        if daily_count >= self.daily_hard_max: reasons.append(PublicationDecision.BLOCKED_DAILY_HARD_MAX.value)
        if mode == PublishMode.DRY_RUN and not reasons:
            decision = PublicationDecision.DRY_RUN_ELIGIBLE
        elif not reasons:
            decision = PublicationDecision.ELIGIBLE
        else:
            decision = PublicationDecision(reasons[0])
        return PublicationEligibilityResult(not reasons, decision, tuple(reasons), tuple(warnings), now, draft.validation_status, opp_status, confirmation, age, draft_age, freshness, "BLOCKED" if any("COOLDOWN" in r for r in reasons) else "CLEAR", daily_count, self.daily_hard_max, recent_age, self.minimum_spacing_seconds, "DUPLICATE" if any("DUPLICATE" in r for r in reasons) else "CLEAR")

    def _emit(self, event_type: str, request: PublicationRequest, metadata: dict[str, Any] | None = None) -> None:
        if self.repository is not None and hasattr(self.repository, "add_system_event_sync"):
            self.repository.add_system_event_sync(event_type=event_type, message=f"publication {event_type.lower()}", metadata={"publication_request_id": request.publication_request_id, "idempotency_key": request.idempotency_key, **(metadata or {})})

    def publish(self, draft: GeneratedContentDraft, opportunity: Any, mode: PublishMode = PublishMode.DRY_RUN, *, now: datetime | None = None, approval_status: str = "NOT_REQUIRED", successful_posts: list[Any] | None = None, idempotency_key: str | None = None) -> PublicationResult:
        now = now or datetime.now(UTC)
        request = self._request(draft, opportunity, mode, now, approval_status)
        if idempotency_key is not None:
            request = replace(request, idempotency_key=idempotency_key)
        if self.repository is not None:
            try:
                existing = self.repository.get_publication_by_idempotency_key(request.idempotency_key)
            except Exception as exc:
                if self.safety is not None:
                    self.safety.mark_database_unavailable(type(exc).__name__)
                raise
            if existing is not None and existing.state == PublicationState.SUCCEEDED.value:
                return PublicationResult(request.publication_request_id, request.idempotency_key, request.platform, PublicationState.SUCCEEDED, existing.platform_post_id, existing.platform_response_status, existing.attempt_number, existing.published_at, None, None, None, False, request.content_hash)
        if self.safety is not None and mode != PublishMode.DRY_RUN:
            hold = self.safety.hold.get()
            if hold.state.value != "CLEAR" or not self.safety.database_available:
                return PublicationResult(request.publication_request_id, request.idempotency_key, request.platform, PublicationState.BLOCKED, None, None, 0, None, "SAFETY_HOLD", hold.state.value, "publication safety hold active", False, request.content_hash)
        eligibility = self.evaluate(draft, opportunity, mode, now=now, approval_status=approval_status, successful_posts=successful_posts)
        if not eligibility.eligible:
            self._emit("PUBLICATION_BLOCKED", request, {"decision": eligibility.decision.value, "reasons": list(eligibility.reasons)})
            if self.repository is not None:
                if hasattr(self.repository, "update_publication_decision"):
                    self.repository.update_publication_decision(request.idempotency_key, eligibility=eligibility, state=PublicationState.BLOCKED.value)
                else:
                    self.repository.create_publication_record(request, eligibility=eligibility, state=PublicationState.BLOCKED.value)
            return PublicationResult(request.publication_request_id, request.idempotency_key, request.platform, PublicationState.BLOCKED, None, None, 0, None, "ELIGIBILITY", eligibility.decision.value, ";".join(eligibility.reasons), False, request.content_hash)
        if mode == PublishMode.DRY_RUN:
            self._emit("PUBLICATION_DRY_RUN_ELIGIBLE", request)
            if self.repository is not None: self.repository.create_publication_record(request, eligibility=eligibility, state=PublicationState.PENDING.value)
            return PublicationResult(request.publication_request_id, request.idempotency_key, request.platform, PublicationState.PENDING, None, None, 0, None, None, None, None, False, request.content_hash)
        if mode in {PublishMode.AUTO, PublishMode.LIMITED_AUTO} and not self.auto_post_enabled:
            return PublicationResult(request.publication_request_id, request.idempotency_key, request.platform, PublicationState.BLOCKED, None, None, 0, None, "KILL_SWITCH", "AUTO_POST_DISABLED", "AUTO_POST_ENABLED is false", False, request.content_hash)
        if self.repository is not None: self.repository.create_publication_record(request, eligibility=eligibility, state=PublicationState.SENDING.value)
        self._emit("PUBLICATION_ATTEMPT_STARTED", request)
        if self.before_provider_hook is not None:
            self.before_provider_hook(request)
        try:
            result = self.provider.publish(request)
        except Exception as exc:
            category = str(getattr(exc, "category", type(exc).__name__))
            if self.safety is not None and any(token in category.upper() for token in ("AUTH", "REFRESH", "IDENTITY", "SCOPE")):
                self.safety.auth_failure(category)
            elif self.safety is not None:
                self.safety.record_x_provider_failure(category)
            raise
        if result.ok:
            state = PublicationState.SUCCEEDED; published_at = now
            if self.after_provider_success_hook is not None:
                self.after_provider_success_hook(request, result)
        elif result.error_category in {"TIMEOUT_AFTER_TRANSMISSION", "AMBIGUOUS_RESPONSE"} or result.transmitted:
            state = PublicationState.UNKNOWN_PLATFORM_STATE; published_at = None
        elif result.retryable:
            state = PublicationState.FAILED_RETRYABLE; published_at = None
        else:
            state = PublicationState.FAILED_FINAL; published_at = None
            if self.safety is not None:
                self.safety.record_x_provider_failure(result.error_category or "FAILED_FINAL")
        if self.repository is not None: self.repository.update_publication_result(request.idempotency_key, request, result, state, published_at)
        self._emit("PUBLICATION_SUCCEEDED" if state == PublicationState.SUCCEEDED else "PUBLICATION_UNKNOWN_PLATFORM_STATE" if state == PublicationState.UNKNOWN_PLATFORM_STATE else "PUBLICATION_FAILED_RETRYABLE" if state == PublicationState.FAILED_RETRYABLE else "PUBLICATION_FAILED_FINAL", request, {"platform_post_id": result.platform_post_id, "error_category": result.error_category})
        return PublicationResult(request.publication_request_id, request.idempotency_key, request.platform, state, result.platform_post_id, result.status_code, 1, published_at, result.error_category, None, result.sanitized_error, result.retryable, request.content_hash)
