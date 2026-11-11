from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class PublishResult:
    ok: bool
    platform_post_id: str | None = None
    status_code: int | None = None
    response_json: dict | None = None
    error: str | None = None


class BasePublisher(Protocol):
    def publish(self, content: str, *, idempotency_key: str | None = None) -> PublishResult: ...
    def validate_credentials(self) -> bool: ...
    def get_post(self, platform_post_id: str) -> dict: ...
    def get_metrics(self, platform_post_id: str) -> dict: ...
    def health_check(self) -> bool: ...
