"""Immutable function definitions and their atomic registration jobs."""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID, uuid4, uuid5

import psycopg
from psycopg.pq import TransactionStatus

from app.common.durable_jobs import JobDefinition, JobRepository
from app.modules.llm_functions.repository import ModelAliasRepository
from app.modules.llm_functions.schemas import VALID_FUNCTION_TYPES

_JOB_NAMESPACE = UUID("320f4f05-8134-4559-a0ab-f4fb4b80d854")


class FunctionRegistrationRepository:
    def __init__(self, connection: psycopg.AsyncConnection[dict[str, Any]]) -> None:
        self._connection = connection
        self._require_transaction()

    def _require_transaction(self) -> None:
        if self._connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Function registration requires an active metadata transaction")

    async def definition(self, identity: UUID) -> dict[str, Any] | None:
        self._require_transaction()
        if not isinstance(identity, UUID):
            raise ValueError("Invalid function definition identity")
        cursor = await self._connection.execute(
            "SELECT * FROM nova_meta.ai_function_definitions WHERE id=%s",
            (identity,),
        )
        return await cursor.fetchone()

    async def head(self, function_type: str) -> dict[str, Any] | None:
        self._require_transaction()
        ModelAliasRepository._validate_type(function_type)
        cursor = await self._connection.execute(
            "SELECT * FROM nova_meta.ai_function_heads WHERE function_type=%s",
            (function_type,),
        )
        return await cursor.fetchone()

    async def reconcile_changed(self) -> list[dict[str, Any]]:
        """Persist intent only; a participant worker must verify the native UDF."""
        self._require_transaction()
        async with self._connection.transaction():
            await ModelAliasRepository(self._connection).lock_mutations()
            jobs = []
            for function_type in sorted(VALID_FUNCTION_TYPES):
                cursor = await self._connection.execute(
                    "WITH snapshot AS (SELECT nova_meta.ai_function_snapshot(%s) AS value) "
                    "SELECT s.value::text AS payload,s.value->>'mode' AS mode,h.version, "
                    "d.payload::jsonb IS NOT DISTINCT FROM s.value AS unchanged "
                    "FROM snapshot s LEFT JOIN nova_meta.ai_function_heads h ON h.function_type=%s "
                    "LEFT JOIN nova_meta.ai_function_definitions d ON d.id=h.definition_id",
                    (function_type, function_type),
                )
                current = await cursor.fetchone()
                assert current is not None
                if current["unchanged"] or (
                    current["version"] is None and current["mode"] == "unconfigured"
                ):
                    continue
                identity = uuid4()
                version = (current["version"] or 0) + 1
                payload = current["payload"]
                digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                await self._connection.execute(
                    "INSERT INTO nova_meta.ai_function_definitions "
                    "(id,function_type,version,payload,digest) VALUES (%s,%s,%s,%s,%s)",
                    (identity, function_type, version, payload, digest),
                )
                await self._connection.execute(
                    "INSERT INTO nova_meta.ai_function_heads "
                    "(function_type,definition_id,version,digest) "
                    "VALUES (%s,%s,%s,%s) ON CONFLICT (function_type) DO UPDATE "
                    "SET definition_id=EXCLUDED.definition_id,version=EXCLUDED.version,"
                    "digest=EXCLUDED.digest",
                    (function_type, identity, version, digest),
                )
                jobs.append(
                    await JobRepository(self._connection).enqueue(
                        JobDefinition(
                            queue="maintenance",
                            kind="ai.function.reconcile",
                            definition_ref=identity,
                            definition_version=version,
                            definition_digest=digest,
                            idempotency_key=uuid5(_JOB_NAMESPACE, str(identity)),
                            resource_key=hashlib.sha256(
                                ("nova.ai.function." + function_type).encode()
                            ).hexdigest(),
                            external_effect=True,
                        )
                    )
                )
            return jobs
