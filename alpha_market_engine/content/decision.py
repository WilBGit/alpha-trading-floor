from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class PublishContext:
    auto_post_enabled: bool
    fact_confidence_score: int
    quality_score: int
    duplicate_score: int
    safety_status: str
    daily_post_count: int
    max_daily_x_posts: int
    opportunity_status: str
    stale_market_data: bool = False
    market_data_stale_seconds: int | None = None
    cooldown_active: bool = False
    duplicate_publish_attempt: bool = False
    x_api_failure_count: int = 0
    fact_validation_failure_count: int = 0
    now: datetime | None = None
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PublishDecision:
    allowed: bool
    reasons: list[str]


def can_publish(ctx: PublishContext) -> PublishDecision:
    reasons = list(ctx.reasons)
    if not ctx.auto_post_enabled:
        reasons.append("AUTO_POST_DISABLED")
    if ctx.fact_confidence_score < 85:
        reasons.append("FACT_CONFIDENCE_TOO_LOW")
    if ctx.quality_score < 80:
        reasons.append("QUALITY_TOO_LOW")
    if ctx.duplicate_score >= 85:
        reasons.append("DUPLICATE_TOO_HIGH")
    if ctx.safety_status != "PASS":
        reasons.append("SAFETY_FAIL")
    if ctx.daily_post_count >= ctx.max_daily_x_posts:
        reasons.append("DAILY_LIMIT_REACHED")
    if ctx.opportunity_status not in {"DETECTED", "GENERATED", "APPROVED"}:
        reasons.append("OPPORTUNITY_NOT_ACTIVE")
    if ctx.stale_market_data:
        reasons.append("MARKET_DATA_STALE")
    if ctx.cooldown_active:
        reasons.append("EVENT_COOLDOWN_ACTIVE")
    if ctx.duplicate_publish_attempt:
        reasons.append("DUPLICATE_PUBLISH_PREVENTED")
    if ctx.x_api_failure_count >= 3:
        reasons.append("X_API_FAILSAFE_ACTIVE")
    if ctx.fact_validation_failure_count >= 5:
        reasons.append("FACT_VALIDATION_FAILSAFE_ACTIVE")
    return PublishDecision(allowed=not reasons, reasons=reasons)
