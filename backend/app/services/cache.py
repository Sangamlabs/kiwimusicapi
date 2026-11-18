import time
from typing import Any

class MemoryCache:
    """Fast in-memory TTL cache with lazy eviction."""

    def __init__(self, default_ttl: int = 3600):
        self._store: dict[str, tuple[float, Any]] = {}
        self.default_ttl = default_ttl

    def get(self, key: str) -> Any | None:
        if key not in self._store:
            return None
        expires_at, val = self._store[key]
        if time.time() > expires_at:
            del self._store[key]
            return None
        return val

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        expiry = time.time() + (ttl or self.default_ttl)
        self._store[key] = (expiry, value)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

cache = MemoryCache(default_ttl=3600)

