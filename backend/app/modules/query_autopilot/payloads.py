from __future__ import annotations

import asyncio
from datetime import datetime

from app.core.security import decrypt_password, encrypt_password
from app.modules.ml_engine.artifacts.store import ArtifactStore, ObjectArtifactStore
from app.modules.query_autopilot.models import Enrollment, Scope
from app.sql_frontend.autopilot import map_snapshot
from app.sql_frontend.fingerprint import fingerprint

MAX_PAYLOAD_BYTES = 262144


class PayloadStore:
    def __init__(self, store: ArtifactStore | None = None) -> None:
        self.store = store or ObjectArtifactStore()

    def reference(self, identifier: str) -> str:
        return self.store.uri_for_key(f"query-autopilot/{identifier}")

    async def put(self, identifier: str, payload: str) -> str:
        if len(payload.encode()) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload_budget_exceeded")
        encrypted = encrypt_password(payload).encode()
        return await asyncio.to_thread(self.store.put, f"query-autopilot/{identifier}", encrypted)

    async def get(self, reference: str, *, expires_at: datetime, now: datetime) -> str:
        if expires_at <= now:
            raise ValueError("payload_expired")
        payload = await asyncio.to_thread(self.store.get, reference)
        if len(payload) > MAX_PAYLOAD_BYTES * 2:
            raise ValueError("payload_budget_exceeded")
        return decrypt_password(payload.decode())

    async def delete(self, reference: str) -> None:
        await asyncio.to_thread(self.store.delete, reference)


def eligible_sample(sql: str, scope: Scope, enrollment: Enrollment) -> bool:
    if not enrollment.enabled or not enrollment.replay_opt_in or scope != enrollment.scope:
        return False
    if not fingerprint(sql).replay_eligible:
        return False
    try:
        map_snapshot(sql, enrollment.table_mapping, scope.database, enrollment.sandbox_database)
        return True
    except ValueError:
        return False
