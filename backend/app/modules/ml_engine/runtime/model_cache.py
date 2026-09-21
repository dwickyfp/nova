"""Async single-flight, LRU/TTL/size-bounded model cache."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass
class CacheEntry:
    value: dict
    size_bytes: int
    expires_at: float


class ModelCache:
    def __init__(self, *, max_models: int, max_bytes: int, ttl_seconds: float) -> None:
        self.max_models = max_models
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict[tuple[str, int], CacheEntry] = OrderedDict()
        self._locks: dict[tuple[str, int], asyncio.Lock] = {}
        self.hits = 0
        self.misses = 0

    async def get_or_load(
        self,
        key: tuple[str, int],
        loader: Callable[[], Awaitable[tuple[dict, int]]],
    ) -> dict:
        cached = self._get(key)
        if cached is not None:
            self.hits += 1
            return cached
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._get(key)
            if cached is not None:
                self.hits += 1
                return cached
            self.misses += 1
            try:
                value, size = await loader()
            except Exception:
                self._entries.pop(key, None)
                raise
            self._entries[key] = CacheEntry(value, size, time.monotonic() + self.ttl_seconds)
            self._entries.move_to_end(key)
            self._evict()
            return value

    def invalidate(self, key: tuple[str, int] | None = None) -> None:
        if key is None:
            self._entries.clear()
        else:
            self._entries.pop(key, None)

    def metrics(self) -> dict[str, int]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "models": len(self._entries),
            "bytes": sum(item.size_bytes for item in self._entries.values()),
        }

    def _get(self, key: tuple[str, int]) -> dict | None:
        item = self._entries.get(key)
        if item is None:
            return None
        if item.expires_at <= time.monotonic():
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return item.value

    def _evict(self) -> None:
        while self._entries and (
            len(self._entries) > self.max_models
            or sum(item.size_bytes for item in self._entries.values()) > self.max_bytes
        ):
            self._entries.popitem(last=False)
