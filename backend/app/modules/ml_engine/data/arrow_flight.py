"""StarRocks Arrow Flight SQL source using the official ADBC client."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import pyarrow as pa

from app.common.identifiers import check_identifier
from app.core.config import settings
from app.modules.ml_engine.spec import MLSecurityContext


class ArrowFlightDataSource:
    """Stream native Arrow batches while authenticating as the requesting user."""

    def __init__(
        self,
        *,
        batch_size: int | None = None,
        queue_depth: int | None = None,
    ) -> None:
        self.batch_size = batch_size or settings.ML_ARROW_BATCH_SIZE
        self.queue_depth = queue_depth or settings.ML_ARROW_QUEUE_DEPTH
        self.queue_wait_seconds = 0.0

    async def stream(self, sql: str, security: MLSecurityContext) -> AsyncIterator[pa.RecordBatch]:
        """Bridge one thread-affine ADBC stream into asyncio with backpressure.

        The producer thread owns connect, cursor creation, execution, reader
        iteration, and cleanup.  ``run_coroutine_threadsafe(queue.put(...))``
        intentionally blocks that thread while the bounded queue is full.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[_StreamItem] = asyncio.Queue(maxsize=self.queue_depth)
        stopped = threading.Event()
        producer = threading.Thread(
            target=self._produce,
            args=(loop, queue, stopped, sql, security),
            name=f"nova-ml-flight-{id(queue):x}",
            daemon=True,
        )
        self.queue_wait_seconds = 0.0
        producer.start()
        try:
            while True:
                item = await queue.get()
                if item.error is not None:
                    raise item.error
                if item.done:
                    break
                assert item.batch is not None
                yield item.batch
        finally:
            stopped.set()
            # ADBC cancellation support is driver/version dependent. The
            # producer checks ``stopped`` between batches and always closes its
            # reader/cursor/connection on the owning thread.
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.to_thread(producer.join), timeout=5)

    def _produce(
        self,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[_StreamItem],
        stopped: threading.Event,
        sql: str,
        security: MLSecurityContext,
    ) -> None:
        queue_wait_seconds = 0.0
        try:
            for batch in self._open_reader(sql, security):
                if stopped.is_set():
                    break
                bounded_batches = (
                    [batch]
                    if batch.num_rows <= self.batch_size
                    else pa.Table.from_batches([batch]).to_batches(max_chunksize=self.batch_size)
                )
                for bounded in bounded_batches:
                    wait_started = time.perf_counter()
                    accepted = self._put(loop, queue, _StreamItem(batch=bounded), stopped)
                    queue_wait_seconds += time.perf_counter() - wait_started
                    if stopped.is_set() or not accepted:
                        return
        except BaseException as exc:
            self._put(loop, queue, _StreamItem(error=exc), stopped)
        finally:
            self.queue_wait_seconds = queue_wait_seconds
            if not stopped.is_set():
                self._put(loop, queue, _StreamItem(done=True), stopped)

    @staticmethod
    def _put(
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[_StreamItem],
        item: _StreamItem,
        stopped: threading.Event,
    ) -> bool:
        if loop.is_closed():
            return False
        future = asyncio.run_coroutine_threadsafe(queue.put(item), loop)
        try:
            while not stopped.is_set():
                try:
                    future.result(timeout=0.1)
                    return True
                except concurrent.futures.TimeoutError:
                    continue
            future.cancel()
            return False
        except BaseException:
            future.cancel()
            return False

    def _open_reader(self, sql: str, security: MLSecurityContext) -> Iterator[pa.RecordBatch]:
        if settings.RANGER_ENABLED:
            raise PermissionError("Arrow Flight is not qualified for Ranger enforcement")
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
                statements.append(f"USE {check_identifier(security.database, field='database')}")
            if security.role:
                statements.append(f"SET ROLE {check_identifier(security.role, field='role')}")
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


@dataclass(frozen=True)
class _StreamItem:
    batch: pa.RecordBatch | None = None
    error: BaseException | None = None
    done: bool = False
