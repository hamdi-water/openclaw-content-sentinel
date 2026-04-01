"""
Phase 22: Performance Plane — Tiered Caching Engine.

==================================================

This module implements a multi-tier caching system for the Sentinel,
focusing on RAG embedding compute and retrieval results to minimize LLM usage
and maximize throughput.
"""

from __future__ import annotations

import collections
import hashlib
import json
import time
from collections.abc import Callable
from typing import Any

from .config import AppConfig
from .utils import dump_json, load_json


class TieredCache:
    """LRU-based memory cache with persistent filesystem fallback."""

    def __init__(
        self,
        config: AppConfig,
        namespace: str,
        capacity: int = 1000,
        ttl: int = 3600,
    ) -> None:
        self.config = config
        self.namespace = namespace
        self.capacity = capacity
        self.ttl = ttl
        self._memory_cache: dict[str, tuple[float, Any]] = collections.OrderedDict()
        self.cache_dir = config.data_dir / "cache" / namespace
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _hash_key(self, key: Any) -> str:
        """Deterministically hash any JSON-serializable key."""
        serialized = json.dumps(key, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(serialized.encode()).hexdigest()

    def get(self, key: Any) -> Any | None:
        """Get item from memory or disk cache."""
        h = self._hash_key(key)

        # 1. Memory Tier
        if h in self._memory_cache:
            expiry, value = self._memory_cache[h]
            if time.time() < expiry:
                # Move to end (LRU)
                self._memory_cache[h] = self._memory_cache.pop(h)
                return value
            else:
                del self._memory_cache[h]

        # 2. Disk Tier
        path = self.cache_dir / f"{h}.json"
        if path.exists():
            try:
                payload = load_json(path)
                if payload and time.time() < payload.get("expiry", 0):
                    value = payload.get("value")
                    # Backfill memory cache
                    self.set(key, value)
                    return value
                else:
                    path.unlink(missing_ok=True)
            except Exception:
                path.unlink(missing_ok=True)

        return None

    def set(self, key: Any, value: Any) -> None:
        """Set item in memory and disk cache."""
        h = self._hash_key(key)
        expiry = time.time() + self.ttl

        # Memory update
        if h in self._memory_cache:
            self._memory_cache.pop(h)
        self._memory_cache[h] = (expiry, value)

        # LRU eviction
        if len(self._memory_cache) > self.capacity:
            self._memory_cache.popitem(last=False)

        # Disk update
        path = self.cache_dir / f"{h}.json"
        dump_json(path, {"expiry": expiry, "value": value})


def cached_call[T](
    cache: TieredCache,
    key: Any,
    func: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
    """Wrap a function call with tiered caching."""
    cached = cache.get(key)
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    result = func(*args, **kwargs)
    cache.set(key, result)
    return result
