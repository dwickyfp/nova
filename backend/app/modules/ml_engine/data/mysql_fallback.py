"""Bounded MySQL-protocol fallback for deployments without Arrow Flight."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pyarrow as pa

from app.common.identifiers import check_identifier
from app.core.config import settings
from app.core.database import db
from app.modules.ml_engine.data.arrow_flight import ArrowFlightDataSource
from app.modules.ml_engine.spec import MLSecurityContext


class MySQLBatchDataSource:
    def __init__(self, *, batch_size: int | None = None) -> None:
        self.batch_size = batch_size or settings.ML_MYSQL_BATCH_SIZE

    async def stream(self, sql: str, security: MLSecurityContext) -> AsyncIterator[pa.RecordBatch]:
        async with (
            db.user_conn(
                username=security.username,
                password=security.password,
                database=security.database,
            ) as conn,
            conn.cursor() as cursor,
        ):
            if security.role:
                await cursor.execute(f"SET ROLE {check_identifier(security.role, field='role')}")
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
    """Use Arrow first; fall back only when transport setup fails before data arrives."""

    def __init__(self) -> None:
        self.arrow = ArrowFlightDataSource()
        self.mysql = MySQLBatchDataSource()

    @property
    def queue_wait_seconds(self) -> float:
        return self.arrow.queue_wait_seconds

    async def stream(self, sql: str, security: MLSecurityContext) -> AsyncIterator[pa.RecordBatch]:
        if settings.ML_ARROW_ENABLED:
            yielded = False
            try:
                async for batch in self.arrow.stream(sql, security):
                    yielded = True
                    yield batch
                return
            except Exception:
                if yielded:
                    raise
        async for batch in self.mysql.stream(sql, security):
            yield batch
