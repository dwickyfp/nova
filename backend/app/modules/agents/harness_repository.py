"""Per-thread admission over Redis and collaboration-run lookups.

Admission is coordination only: Redis serializes changes to one owner's thread
while a Mission or turn is being admitted. The authority for runs is the PG run
journal's claim generation (``run_journal.py``) and the Mission revision.
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from typing import Any

from app.core.redis import session_store
from app.modules.agents.run_coordination import coordination_lock

ADMISSION_LOCK_SECONDS = 600
ADMISSION_LOCK_RENEW_SECONDS = 30


class AdmissionUnavailable(RuntimeError):
    pass


class HarnessRepository:
    @asynccontextmanager
    async def admission_lock(self, thread_id: str, owner_name: str):
        """Serialize changes to one owner's thread (its Missions and runs)."""
        client = session_store._redis
        if client is None:
            raise AdmissionUnavailable("Thread admission is temporarily unavailable")
        scope = hashlib.sha256(f"{owner_name}\0{thread_id}".encode()).hexdigest()
        body_failed = False
        try:
            async with coordination_lock(
                client, f"nova:auto:event:lock:admission:{scope}",
                ttl_seconds=ADMISSION_LOCK_SECONDS, renew_seconds=ADMISSION_LOCK_RENEW_SECONDS,
            ) as assert_owned:
                async def assert_admitted() -> None:
                    try:
                        await assert_owned()
                    except Exception as exc:
                        raise AdmissionUnavailable("Thread admission lock was lost") from exc

                try:
                    yield assert_admitted
                except BaseException:
                    body_failed = True
                    raise
        except Exception as exc:
            if body_failed:
                raise
            raise AdmissionUnavailable("Thread admission lock is unavailable") from exc

    async def ensure_schema(self) -> None:
        """PostgreSQL migrations own the run tables; nothing is created at startup."""

    async def get(self, run_id: str) -> dict[str, Any] | None:
        """A participant run of a collaboration tree, or None for any other run.

        Collaboration trees belonged to the retired Smart/Auto runtime. Their
        participant columns were not carried into the PostgreSQL run journal
        (see ``PG_RETIRED_AGENT_HISTORY.md``), and no current turn creates one,
        so every run answers ``None`` and callers refuse the participant.
        """
        del run_id
        return None


harness_repository = HarnessRepository()
