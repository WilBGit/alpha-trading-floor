from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import httpx

from alpha_market_engine.config.settings import get_settings

from .base import PublishResult


@dataclass(slots=True)
class XPublisher:
    client: httpx.Client | None = None
    endpoint: str = "https://api.x.com/2/tweets"
    _published_idempotency_keys: set[str] = field(default_factory=set)

    def __post_init__(self):
        if self.client is None:
            self.client = httpx.Client(timeout=20)

    def validate_credentials(self) -> bool:
        settings = get_settings()
        required = [settings.x_bearer_token, settings.x_client_id, settings.x_client_secret]
        return all(bool(item and str(item).strip()) for item in required)

    def validate_x_post_length(self, content: str, max_length: int = 280) -> bool:
        return len(content) <= max_length

    def publish(self, content: str, *, idempotency_key: str | None = None) -> PublishResult:
        if not self.validate_credentials():
            return PublishResult(ok=False, error="missing credentials")
        if not self.validate_x_post_length(content):
            return PublishResult(ok=False, error="post too long")
        key = idempotency_key or hashlib.sha256(content.encode("utf-8")).hexdigest()
        if key in self._published_idempotency_keys:
            return PublishResult(ok=False, error="duplicate publish prevented")
        settings = get_settings()
        headers = {
            "Authorization": f"Bearer {settings.x_bearer_token}",
            "Content-Type": "application/json",
        }
        payload = {"text": content}
        try:
            response = self.client.post(self.endpoint, headers=headers, json=payload)
            if response.status_code >= 400:
                return PublishResult(ok=False, status_code=response.status_code, response_json=self._safe_json(response), error=response.text)
            data = self._safe_json(response)
            platform_post_id = self._extract_post_id(data)
            if platform_post_id:
                self._published_idempotency_keys.add(key)
            return PublishResult(ok=True, platform_post_id=platform_post_id, status_code=response.status_code, response_json=data)
        except httpx.HTTPError as exc:
            return PublishResult(ok=False, error=str(exc))

    def get_post(self, platform_post_id: str) -> dict:
        return {"id": platform_post_id}

    def get_metrics(self, platform_post_id: str) -> dict:
        return {}

    def health_check(self) -> bool:
        return self.validate_credentials()

    @staticmethod
    def _safe_json(response: httpx.Response) -> dict:
        try:
            return response.json()
        except Exception:
            return {}

    @staticmethod
    def _extract_post_id(data: dict) -> str | None:
        if isinstance(data, dict):
            if isinstance(data.get("data"), dict) and data["data"].get("id"):
                return str(data["data"]["id"])
            if data.get("id"):
                return str(data["id"])
        return None
