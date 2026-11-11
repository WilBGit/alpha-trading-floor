from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from .state import StreamHealth, LiveMarketStateStore


class MarketDataStream(ABC):
    def __init__(self, state_store: LiveMarketStateStore | None = None) -> None:
        self.state_store = state_store or LiveMarketStateStore()

    @abstractmethod
    async def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def subscribe(self, symbols: Iterable[str]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def unsubscribe(self, symbols: Iterable[str]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def run(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> StreamHealth:
        raise NotImplementedError
