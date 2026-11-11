from __future__ import annotations

from dataclasses import dataclass

from alpha_market_engine.config.settings import get_settings

from .base import AIResponse


@dataclass(slots=True)
class LocalEchoProvider:
    provider: str
    model: str

    def generate(self, prompt: str, *, system: str | None = None, temperature: float = 0.2) -> AIResponse:
        return AIResponse(content=prompt, model=self.model, provider=self.provider, raw={"echo": True})

    def generate_json(self, prompt: str, *, system: str | None = None, temperature: float = 0.2) -> dict:
        return {"echo": prompt, "model": self.model, "provider": self.provider}

    def health_check(self) -> bool:
        return True


class HttpAIProvider:
    def __init__(self, provider: str, model: str, api_key: str, base_url: str):
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def generate(self, prompt: str, *, system: str | None = None, temperature: float = 0.2) -> AIResponse:
        raise NotImplementedError("live AI provider wiring is pending provider-specific adapters")

    def generate_json(self, prompt: str, *, system: str | None = None, temperature: float = 0.2) -> dict:
        raise NotImplementedError("live AI provider wiring is pending provider-specific adapters")

    def health_check(self) -> bool:
        return False


def get_ai_provider():
    settings = get_settings()
    provider = settings.ai_provider.lower()
    if provider in {"local", "echo"}:
        return LocalEchoProvider(provider=provider, model=settings.ai_model)
    return HttpAIProvider(provider=provider, model=settings.ai_model, api_key=settings.ai_api_key, base_url=settings.ai_base_url)
