"""Shared durable-store fake: replicas receive separate caches over the same records."""

import time

from app.modules.ml_engine.ephemeral.repository import decode_entry, encode_entry


class MemoryEphemeralRepository:
    def __init__(self):
        self.records = {}

    async def put(self, entry):
        self.records[entry.run_id] = (
            encode_entry(entry),
            time.time() + entry.expires_at - time.monotonic(),
        )

    async def get(self, run_id, scope):
        row = self.records.get(run_id)
        if row is None or row[1] <= time.time():
            return None
        entry = decode_entry(*row)
        return entry if entry.scope_key == scope else None

    async def by_fingerprint(self, fingerprint, scope):
        for run_id in reversed(self.records):
            entry = await self.get(run_id, scope)
            if entry and entry.fingerprint == fingerprint:
                return entry
        return None

    async def expired(self):
        return [decode_entry(*row) for row in self.records.values() if row[1] <= time.time()]

    async def delete_expired(self, entry):
        self.records.pop(entry.run_id, None)

    async def remove(self, run_id, scope):
        row = self.records.get(run_id)
        if row and decode_entry(*row).scope_key == scope:
            self.records.pop(run_id)
