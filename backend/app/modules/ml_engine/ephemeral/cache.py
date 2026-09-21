"""TTL cache for non-persistent runs and promotion-ready model bundles."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class EphemeralEntry:
    run_id: str
    fingerprint: str
    scope_key: str
    output: object
    artifact_payload: bytes
    artifact_sha256: str
    expires_at: float


class EphemeralRunCache:
    def __init__(self, ttl_seconds: float) -> None:
        self.ttl_seconds = ttl_seconds
        self._by_run: dict[str, EphemeralEntry] = {}
        self._by_fingerprint: dict[tuple[str, str], str] = {}

    def put(self, entry: EphemeralEntry) -> None:
        self.cleanup()
        self._by_run[entry.run_id] = entry
        self._by_fingerprint[(entry.scope_key, entry.fingerprint)] = entry.run_id

    def by_fingerprint(self, scope_key: str, fingerprint: str) -> EphemeralEntry | None:
        self.cleanup()
        run_id = self._by_fingerprint.get((scope_key, fingerprint))
        return self._by_run.get(run_id) if run_id else None

    def by_run(self, run_id: str, scope_key: str) -> EphemeralEntry | None:
        self.cleanup()
        entry = self._by_run.get(run_id)
        return entry if entry and entry.scope_key == scope_key else None

    def cleanup(self) -> int:
        now = time.monotonic()
        expired = [run_id for run_id, entry in self._by_run.items() if entry.expires_at <= now]
        for run_id in expired:
            entry = self._by_run.pop(run_id)
            self._by_fingerprint.pop((entry.scope_key, entry.fingerprint), None)
        return len(expired)
