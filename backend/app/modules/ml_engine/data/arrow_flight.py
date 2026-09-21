"""StarRocks Arrow Flight SQL source using the official ADBC client."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator

import pyarrow as pa

from app.common.identifiers import check_identifier
from app.core.config import settings
from app.modules.ml_engine.spec import MLSecurityContext


class ArrowFlightDataSource:
    """Stream native Arrow batches while authenticating as the requesting user."""

    def __init__(self, *, batch_size: int | None = None) -> None:
        self.batch_size = batch_size or settings.ML_ARROW_BATCH_SIZE

    async def stream(self, sql: str, security: MLSecurityContext) -> AsyncIterator[pa.RecordBatch]:
        iterator = await asyncio.to_thread(self._open_reader, sql, security)
        while True:
            batch = await asyncio.to_thread(next, iterator, None)
            if batch is None:
                break
            if batch.num_rows <= self.batch_size:
                yield batch
                continue
            table = pa.Table.from_batches([batch])
            for bounded in table.to_batches(max_chunksize=self.batch_size):
                yield bounded

    def _open_reader(self, sql: str, security: MLSecurityContext) -> Iterator[pa.RecordBatch]:
        import adbc_driver_flightsql.dbapi as flight_sql
        import adbc_driver_manager

        kwargs = {
            adbc_driver_manager.DatabaseOptions.USERNAME.value: security.username,
            adbc_driver_manager.DatabaseOptions.PASSWORD.value: security.password,
        }
        conn = flight_sql.connect(
            uri=(f"grpc://{settings.STARROCKS_HOST}:{settings.STARROCKS_ARROW_FLIGHT_PORT}"),
            db_kwargs=kwargs,
        )
        cursor = conn.cursor()
        try:
            statements: list[str] = []
            if security.database:
                statements.append(
                    f"USE {check_identifier(security.database, field='database')}"
                )
            if security.role:
                statements.append(
                    f"SET ROLE {check_identifier(security.role, field='role')}"
                )
            for statement in statements:
                cursor.execute(statement)
                # StarRocks returns a one-row StatusResult for session
                # statements. Consume it before reusing the ADBC cursor.
                cursor.fetch_arrow_table()
            cursor.execute(sql)
            reader = cursor.fetch_record_batch()
        except Exception:
            cursor.close()
            conn.close()
            raise

        def batches() -> Iterator[pa.RecordBatch]:
            try:
                yield from reader
            finally:
                cursor.close()
                conn.close()

        return batches()
