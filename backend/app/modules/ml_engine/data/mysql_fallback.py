"""Bounded MySQL-protocol data source for governed ML reads."""

from __future__ import annotations

from collections.abc import AsyncIterator

import asyncmy.cursors
import pyarrow as pa

from app.common.identifiers import check_identifier
from app.core.config import settings
from app.core.database import db
from app.modules.ml_engine.spec import MLSecurityContext


class MySQLBatchDataSource:
    transport_used = "mysql_fallback"

    def __init__(self, *, batch_size: int | None = None) -> None:
        self.batch_size = batch_size or settings.ML_MYSQL_BATCH_SIZE

    async def stream(self, sql: str, security: MLSecurityContext) -> AsyncIterator[pa.RecordBatch]:
        security.validate()
        async with (
            db.user_conn(
                username=security.username,
                password=security.password,
                database=security.database,
            ) as conn,
            conn.cursor(asyncmy.cursors.SSCursor) as cursor,
        ):
            if security.role:
                await cursor.execute(f"SET ROLE {check_identifier(security.role, field='role')}")
                from app.modules.access_control.role_activation import _active_role_names

                await cursor.execute("SELECT CURRENT_ROLE()")
                row = await cursor.fetchone()
                # SSCursor.fetchone() leaves the EOF packet unread before the next command.
                await cursor.fetchall()
                if not row or _active_role_names(str(row[0])) != {security.role}:
                    raise PermissionError("ML connection did not confirm the active role")
            await cursor.execute(sql)
            columns = [item[0] for item in cursor.description or ()]
            while True:
                rows = await cursor.fetchmany(self.batch_size)
                if not rows:
                    break
                arrays = [pa.array(values) for values in zip(*rows, strict=False)]
                yield pa.RecordBatch.from_arrays(arrays, names=columns)


class ExistingConnectionBatchDataSource:
    """Stream from an already-authenticated proxy session without new credentials."""

    transport_used = "mysql_fallback"

    def __init__(self, connection, *, batch_size: int | None = None) -> None:
        self.connection = connection
        self.batch_size = batch_size or settings.ML_MYSQL_BATCH_SIZE

    async def stream(self, sql: str, security: MLSecurityContext) -> AsyncIterator[pa.RecordBatch]:
        del security
        async with self.connection.cursor() as cursor:
            await cursor.execute(sql)
            columns = [item[0] for item in cursor.description or ()]
            while True:
                rows = await cursor.fetchmany(self.batch_size)
                if not rows:
                    break
                arrays = [pa.array(values) for values in zip(*rows, strict=False)]
                yield pa.RecordBatch.from_arrays(arrays, names=columns)


class PreferredDataSource:
    """Stream over the caller's MySQL session, where Ranger enforcement applies.

    Arrow Flight is not qualified for Ranger enforcement, so it is never used
    for governed data.
    """

    def __init__(self) -> None:
        self.mysql = MySQLBatchDataSource()
        self.transport_used = "mysql_fallback"

    async def stream(self, sql: str, security: MLSecurityContext) -> AsyncIterator[pa.RecordBatch]:
        async for batch in self.mysql.stream(sql, security):
            yield batch
