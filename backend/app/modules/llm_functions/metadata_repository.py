"""AI function usage metering in PostgreSQL.

The prepared replacement for the ``NOVA_SYSTEM.USAGE_LLM_FUNCTIONS`` writes in
``UsageMeter.flush`` and the reads in ``AIUsageService._function_tokens``,
switched in at the all-domain cutover. Usage is append-only: every flushed
total is a new record with its own identity and server time, never changed or
removed by a workload. Imported rows keep their legacy ``usage_id`` without any
inferred uniqueness. Reading usage across people is for administrators.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus

from app.core.metadata_db import MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext

#: One flushed total: (function, alias, model, status) and the summed counters.
UsageTotal = tuple[str, str | None, str | None, str, int, int, int, int, int]


class LLMUsageMetadataRepository:
    def __init__(
        self, connection: psycopg.AsyncConnection[dict[str, Any]], actor: SecurityContext,
    ) -> None:
        self.connection = connection
        self.actor = actor

    async def _bound(self) -> None:
        if self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Usage metadata requires an active metadata transaction")
        cursor = await self.connection.execute(
            "SELECT current_setting('nova.principal',true)=%s "
            "AND current_setting('nova.active_role',true)=%s "
            "AND current_setting('nova.security_context_version',true)=%s "
            "AND current_setting('nova.service_identity',true)=current_user AS valid",
            (self.actor.principal, self.actor.active_role,
             str(self.actor.security_context_version)),
        )
        row = await cursor.fetchone()
        if not row or row["valid"] is not True:
            raise MetadataPermissionDenied()

    async def record(self, totals: list[UsageTotal]) -> int:
        """Append the flushed totals in the caller's transaction; returns the rows written."""
        await self._bound()
        if not totals:
            return 0
        async with self.connection.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO nova_meta.usage_llm_functions (function_name,alias_name,model_name,"
                "status,request_count,prompt_tokens,completion_tokens,total_tokens,duration_ms) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                totals,
            )
        return len(totals)

    async def function_tokens(
        self, start: datetime, end: datetime, *, limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Usage between ``start`` and ``end``, newest first (administrators only)."""
        await self._bound()
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError("A positive usage limit is required")
        cursor = await self.connection.execute(
            "SELECT * FROM nova_meta.usage_llm_functions WHERE recorded_at>=%s "
            "AND recorded_at<%s ORDER BY recorded_at DESC,record_id LIMIT %s",
            (start, end, limit),
        )
        return list(await cursor.fetchall())
