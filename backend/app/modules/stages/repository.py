"""Stage registration storage within an actor-bound PostgreSQL transaction."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.pq import TransactionStatus

from app.core.metadata_db import MetadataConflict


class StageRepository:
    def __init__(self, connection: psycopg.AsyncConnection[dict[str, Any]]) -> None:
        self.connection = connection

    def _transaction(self) -> None:
        if self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Stage storage requires an active metadata transaction")

    async def identities(
        self, *, identity: str | None = None, name: str | None = None,
        database: str | None = None, schema: str | None = None,
    ) -> list[dict[str, Any]]:
        self._transaction()
        cursor = await self.connection.execute(
            "SELECT * FROM nova_meta.discover_stages(%s,%s,%s,%s)",
            (identity, name, database, schema),
        )
        return list(await cursor.fetchall())

    async def get(self, identity: str) -> dict[str, Any] | None:
        self._transaction()
        cursor = await self.connection.execute(
            "SELECT * FROM nova_meta.config_stages WHERE id=%s AND NOT is_deleted", (identity,),
        )
        return await cursor.fetchone()

    async def in_scope(self) -> list[dict[str, Any]]:
        self._transaction()
        cursor = await self.connection.execute(
            "SELECT * FROM nova_meta.config_stages WHERE NOT is_deleted ORDER BY name,id"
        )
        return list(await cursor.fetchall())

    async def create(
        self, identity: str, data: dict[str, Any], *, namespace_kind: str,
    ) -> dict[str, Any]:
        self._transaction()
        cursor = await self.connection.execute(
            "INSERT INTO nova_meta.config_stages "
            "(id,name,database_name,schema_name,storage_connection,base_prefix,namespace_kind) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (identity, data["name"], data["database_name"], data["schema_name"],
             data["storage_connection"], data["base_prefix"], namespace_kind),
        )
        row = await cursor.fetchone()
        assert row is not None
        return row

    async def delete(self, identity: str, *, expected_revision: int) -> dict[str, Any]:
        self._transaction()
        if type(expected_revision) is not int or not 1 <= expected_revision < 2**63 - 1:
            raise ValueError("A positive stage revision is required")
        cursor = await self.connection.execute(
            "UPDATE nova_meta.config_stages SET is_deleted=true,"
            "metadata_revision=metadata_revision+1 "
            "WHERE id=%s AND metadata_revision=%s AND NOT is_deleted RETURNING *",
            (identity, expected_revision),
        )
        row = await cursor.fetchone()
        if row is None:
            raise MetadataConflict()
        return row
