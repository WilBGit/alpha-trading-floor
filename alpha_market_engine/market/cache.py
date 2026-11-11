from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any

from .data_provider import MarketDataCache, ProviderHealth


@dataclass(slots=True)
class CacheRecord:
    value: Any
    expires_at: datetime


class InMemoryMarketDataCache(MarketDataCache):
    def __init__(self) -> None:
        self._store: dict[str, CacheRecord] = {}

    def get(self, key: str) -> Any | None:
        record = self._store.get(key)
        if record is None:
            return None
        if datetime.now(timezone.utc) >= record.expires_at:
            self._store.pop(key, None)
            return None
        return record.value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._store[key] = CacheRecord(value=value, expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds))

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            provider="in-memory-cache",
            healthy=True,
            message="cache in memory",
            metadata={"entries": len(self._store)},
        )


try:  # pragma: no cover - optional runtime dependency
    import redis
except Exception:  # pragma: no cover
    redis = None


class RedisMarketDataCache(MarketDataCache):
    def __init__(self, redis_url: str, namespace: str = "alpha:market:data") -> None:
        if redis is None:
            raise RuntimeError("redis package is not installed")
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)
        self._namespace = namespace

    def _key(self, key: str) -> str:
        return f"{self._namespace}:{key}"

    def get(self, key: str) -> Any | None:
        raw = self._client.get(self._key(key))
        if raw is None:
            return None
        import json

        return json.loads(raw)

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        import json

        self._client.setex(self._key(key), ttl_seconds, json.dumps(value, default=str))

    def delete(self, key: str) -> None:
        self._client.delete(self._key(key))

    def health_check(self) -> ProviderHealth:
        try:
            pong = self._client.ping()
            return ProviderHealth(provider="redis", healthy=bool(pong), message="redis reachable", metadata={"namespace": self._namespace})
        except Exception as exc:  # pragma: no cover - external dependency
            return ProviderHealth(provider="redis", healthy=False, message=str(exc), metadata={"namespace": self._namespace})


__all__ = ["InMemoryMarketDataCache", "RedisMarketDataCache"]
