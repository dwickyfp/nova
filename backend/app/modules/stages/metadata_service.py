"""Governed Stage commands for the PostgreSQL cutover.

The caller owns the metadata UoW. Storage credentials and file effects remain
outside this registration transaction.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal
from uuid import uuid4

import psycopg
from psycopg.pq import TransactionStatus

from app.core.config import require_storage_connection
from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext
from app.modules.stages.access import StageAccessDenied, check_stage_access
from app.modules.stages.repository import StageRepository
from app.modules.stages.schemas import StageCreate
from app.modules.stages.service import StageRegistrationError, StageService

StageAction = Literal["read", "write", "delete"]


class StageMetadataService:
    def __init__(
        self, connection: psycopg.AsyncConnection[dict[str, Any]], actor: SecurityContext,
    ) -> None:
        self.connection = connection
        self.actor = actor
        self.repository = StageRepository(connection)

    async def _bound_actor(self) -> None:
        if self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Stage access requires an active metadata transaction")
        cursor = await self.connection.execute(
            "SELECT current_setting('nova.principal',true)=%s "
            "AND current_setting('nova.active_role',true)=%s "
            "AND current_setting('nova.security_context_version',true)=%s "
            "AND current_setting('nova.principal_type',true)=%s "
            "AND current_setting('nova.service_identity',true)=current_user AS valid",
            (self.actor.principal, self.actor.active_role,
             str(self.actor.security_context_version), self.actor.principal_type),
        )
        row = await cursor.fetchone()
        if not row or row["valid"] is not True:
            raise MetadataPermissionDenied()

    @asynccontextmanager
    async def _scope(self, identity: dict[str, Any], action: StageAction) -> AsyncIterator[None]:
        await self._bound_actor()
        if action not in {"read", "write", "delete"}:
            raise ValueError("Unknown stage action")
        cursor = await self.connection.execute(
            "SELECT coalesce(nullif(current_setting('nova.stage_database',true),''),"
            "nullif(current_setting('nova.stage_schema',true),''),"
            "nullif(current_setting('nova.stage_action',true),'')) AS active"
        )
        row = await cursor.fetchone()
        if row and row["active"] is not None:
            raise MetadataPermissionDenied()
        await check_stage_access(
            identity, action=action, username=self.actor.principal,
            active_role=self.actor.active_role,
        )
        await self._bound_actor()
        # A savepoint also clears the scope when a query fails or is cancelled.
        # It prevents a caught error from leaving the next command authorized.
        async with self.connection.transaction():
            await self.connection.execute(
                "SELECT set_config('nova.stage_database',%s,true),"
                "set_config('nova.stage_schema',%s,true),set_config('nova.stage_action',%s,true)",
                (identity["database_name"], identity["schema_name"], action),
            )
            yield
            await self.connection.execute(
                "SELECT set_config('nova.stage_database','',true),"
                "set_config('nova.stage_schema','',true),set_config('nova.stage_action','',true)"
            )

    async def identities(
        self, *, identity: str | None = None, name: str | None = None,
        database: str | None = None, schema: str | None = None,
    ) -> list[dict[str, Any]]:
        """Internal scope discovery; callers must authorize before exposing records."""
        await self._bound_actor()
        return await self.repository.identities(
            identity=identity, name=name, database=database, schema=schema,
        )

    async def list_stages(self) -> list[dict[str, Any]]:
        scopes = {
            (row["database_name"], row["schema_name"]) for row in await self.identities()
        }
        result: list[dict[str, Any]] = []
        for database, schema in sorted(scopes):
            try:
                async with self._scope({"database_name": database, "schema_name": schema}, "read"):
                    result.extend(await self.repository.in_scope())
            except StageAccessDenied:
                continue
        return sorted(result, key=lambda row: (row["name"], row["id"]))

    async def get_stage(
        self, identity: str, *, action: StageAction = "read",
    ) -> dict[str, Any] | None:
        rows = await self.identities(identity=identity)
        if not rows:
            return None
        async with self._scope(rows[0], action):
            return await self.repository.get(identity)

    async def create_stage(self, data: StageCreate) -> dict[str, Any]:
        values = StageCreate.model_validate(data.model_dump()).model_dump()
        async with self._scope(values, "delete"):
            prefix = values["base_prefix"]
            if prefix and self.actor.active_role != "ACCOUNTADMIN":
                raise StageAccessDenied("Custom stage folders require active ACCOUNTADMIN")
            if prefix:
                prefix = StageService._safe_relative_path(prefix.strip("/"), field="base prefix")
            try:
                require_storage_connection(values["storage_connection"])
            except ValueError:
                raise StageRegistrationError("Storage connection is not configured") from None
            identity = str(uuid4())
            values["base_prefix"] = prefix or f"nova/stages/{identity}"
            return await self.repository.create(
                identity, values, namespace_kind="MAPPED" if prefix else "MANAGED",
            )

    async def delete_stage(self, identity: str, *, expected_revision: int) -> dict[str, Any]:
        rows = await self.identities(identity=identity)
        if not rows:
            raise MetadataConflict()
        async with self._scope(rows[0], "delete"):
            return await self.repository.delete(identity, expected_revision=expected_revision)
