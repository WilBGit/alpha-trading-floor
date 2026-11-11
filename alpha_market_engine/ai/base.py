from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class AIResponse:
    content: str
    model: str
    provider: str
    raw: dict | None = None


class AIProvider(Protocol):
    def generate(self, prompt: str, *, system: str | None = None, temperature: float = 0.2) -> AIResponse: ...
    def generate_json(self, prompt: str, *, system: str | None = None, temperature: float = 0.2) -> dict: ...
    def health_check(self) -> bool: ...
