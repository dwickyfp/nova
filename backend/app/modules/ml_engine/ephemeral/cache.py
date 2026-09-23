"""Bounded TTL/LRU metadata cache for object-backed ephemeral ML runs."""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EphemeralEntry:
    run_id: str
    fingerprint: str
    scope_key: str
    output: Any
    artifact_sha256: str
    expires_at: float
    task: str = "regression"
    artifact_uri: str = ""
    artifact_size: int = 0
    # Compatibility for old callers. Production entries never retain this.
    artifact_payload: bytes | None = None
    spec_snapshot: dict[str, Any] = field(default_factory=dict)
    artifact_kind: str = "model"

    @property
    def memory_bytes(self) -> int:
        return (
            len(self.run_id)
            + len(self.fingerprint)
            + len(self.scope_key)
            + len(self.task)
            + len(self.artifact_uri)
            + (len(self.artifact_payload) if self.artifact_payload else 0)
            + len(json.dumps(self.spec_snapshot, default=str).encode())
            + len(json.dumps(getattr(self.output, "metrics", {}), default=str).encode())
            + len(json.dumps(getattr(self.output, "results", []), default=str).encode())
        )


class EphemeralRunCache:
    def __init__(
        self,
        ttl_seconds: float,
        *,
        max_entries: int = 32,
        max_memory_bytes: int = 64 * 1024 * 1024,
        on_evict: Callable[[EphemeralEntry], None] | None = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.max_memory_bytes = max_memory_bytes
        self.on_evict = on_evict
        self._by_run: OrderedDict[str, EphemeralEntry] = OrderedDict()
        self._by_fingerprint: dict[tuple[str, str], str] = {}
        self._memory_bytes = 0
        self._evictions = 0
        self._expired = 0
        self._cleanup_errors = 0

    def put(self, entry: EphemeralEntry) -> None:
        self.cleanup_expired()
        previous = self._by_run.get(entry.run_id)
        if previous is not None:
            self._remove(previous, cleanup_artifact=False)
        self._by_run[entry.run_id] = entry
        self._by_fingerprint[(entry.scope_key, entry.fingerprint)] = entry.run_id
        self._memory_bytes += entry.memory_bytes
        while len(self._by_run) > self.max_entries or self._memory_bytes > self.max_memory_bytes:
            _, oldest = self._by_run.popitem(last=False)
            self._evictions += 1
            self._remove(oldest, already_removed=True)

    def by_fingerprint(self, scope_key: str, fingerprint: str) -> EphemeralEntry | None:
        self.cleanup_expired()
        run_id = self._by_fingerprint.get((scope_key, fingerprint))
        return self._touch(run_id) if run_id else None

    def by_run(self, run_id: str, scope_key: str) -> EphemeralEntry | None:
        self.cleanup_expired()
        entry = self._touch(run_id)
        return entry if entry and entry.scope_key == scope_key else None

    def cleanup_expired(self) -> int:
        now = time.monotonic()
        expired = [entry for entry in self._by_run.values() if entry.expires_at <= now]
        for entry in expired:
            self._remove(entry)
        self._expired += len(expired)
        return len(expired)

    def cleanup(self) -> int:
        return self.cleanup_expired()

    @property
    def memory_bytes(self) -> int:
        return self._memory_bytes

    def metrics(self) -> dict[str, int]:
        return {
            "entries": len(self._by_run),
            "memory_bytes": self._memory_bytes,
            "evictions": self._evictions,
            "expired": self._expired,
            "cleanup_errors": self._cleanup_errors,
        }

    def _touch(self, run_id: str | None) -> EphemeralEntry | None:
        if run_id is None:
            return None
        entry = self._by_run.get(run_id)
        if entry is not None:
            self._by_run.move_to_end(run_id)
        return entry

    def _remove(
        self,
        entry: EphemeralEntry,
        *,
        already_removed: bool = False,
        cleanup_artifact: bool = True,
    ) -> None:
        if not already_removed:
            self._by_run.pop(entry.run_id, None)
        key = (entry.scope_key, entry.fingerprint)
        if self._by_fingerprint.get(key) == entry.run_id:
            self._by_fingerprint.pop(key, None)
        self._memory_bytes = max(0, self._memory_bytes - entry.memory_bytes)
        if cleanup_artifact and self.on_evict is not None:
            try:
                self.on_evict(entry)
            except Exception:
                self._cleanup_errors += 1
