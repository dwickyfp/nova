"""Warm the ANTLR prediction caches before a process serves SQL.

The generated parser learns its DFA lazily: the first statement of each shape
pays tens of milliseconds of ATN simulation (45-140 ms measured for common
queries), and each parse worker thread keeps its own caches
(:class:`app.sql_frontend.parser._PredictionState`), so every thread pays it
again. Parsing a small corpus of common shapes at startup moves that cost out
of the first user requests.

The warm-up is bounded: it stops at :data:`WARMUP_BUDGET_SECONDS` however far
it got, never fails startup, and reports what it did so the cost is measured
in the process log. It runs while the process is not yet serving, which is
what makes warming the shared, unlocked caches on the calling thread safe.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass

from app.core.config import settings
from app.sql_frontend import parser as sql_parser

logger = logging.getLogger(__name__)

#: Upper bound on the whole warm-up, calling thread and workers together.
WARMUP_BUDGET_SECONDS = 2.0

#: Common statement shapes: worksheet reads, generated Dataviz/semantic SQL,
#: metadata commands, DML and the Nova stage forms. Only parsed, never run.
WARMUP_CORPUS: tuple[str, ...] = (
    "SELECT 1",
    "SELECT * FROM db.t WHERE id = 1 AND name LIKE 'a%' ORDER BY id LIMIT 100",
    "SELECT a.id, b.name FROM db.a AS a LEFT JOIN db.b AS b ON a.id = b.a_id "
    "WHERE a.created_at >= DATE '2026-01-01'",
    "SELECT region, COUNT(*) AS n, SUM(amount) AS total, AVG(amount) FROM sales "
    "GROUP BY region HAVING SUM(amount) > 100 ORDER BY total DESC",
    "WITH monthly AS (SELECT date_trunc('month', order_date) AS m, SUM(amount) AS s "
    "FROM orders GROUP BY 1) SELECT m, s, LAG(s) OVER (ORDER BY m) AS prev, "
    "ROW_NUMBER() OVER (PARTITION BY m ORDER BY s DESC) AS rn FROM monthly",
    "SELECT CASE WHEN amount > 100 THEN 'high' WHEN amount > 10 THEN 'mid' ELSE 'low' END "
    "AS band, CAST(amount AS DECIMAL(18, 2)), COALESCE(note, '') FROM t",
    "SELECT id FROM t WHERE id IN (1, 2, 3) UNION ALL SELECT id FROM u "
    "WHERE EXISTS (SELECT 1 FROM v WHERE v.id = u.id)",
    "SELECT * FROM @stage/folder/file.csv",
    "SHOW TABLES",
    "SHOW DATABASES",
    "DESCRIBE db.t",
    "EXPLAIN SELECT * FROM t WHERE id = 1",
    "INSERT INTO t (a, b) VALUES (1, 'x'), (2, 'y')",
    "UPDATE t SET a = a + 1 WHERE id = 1",
    "DELETE FROM t WHERE id = 1",
    "CREATE TABLE t (id BIGINT, name VARCHAR(64)) DUPLICATE KEY(id) "
    "DISTRIBUTED BY HASH(id) BUCKETS 1",
    "SET ROLE analyst",
    "USE db",
)


@dataclass(frozen=True, slots=True)
class WarmupReport:
    """What one warm-up did, for the startup log and tests."""

    statements: int
    worker_threads: int
    elapsed_ms: float
    complete: bool


def _warm_this_thread(deadline: float) -> tuple[int, bool]:
    parsed = 0
    for sql in WARMUP_CORPUS:
        if time.monotonic() >= deadline:
            return parsed, False
        try:
            sql_parser.parse_tree(sql)
        except Exception:  # noqa: BLE001 - a warm-up miss costs latency only
            logger.debug("SQL parser warm-up statement failed", exc_info=True)
        parsed += 1
    return parsed, True


def _warm_worker(barrier: threading.Barrier, deadline: float) -> tuple[int, bool]:
    # Hold every task until all have started, so each runs on its own thread
    # and every worker's private caches are warmed, not one thread's twice.
    try:
        barrier.wait(timeout=max(0.0, deadline - time.monotonic()))
    except threading.BrokenBarrierError:
        return 0, False
    return _warm_this_thread(deadline)


def warm_parser(budget_seconds: float = WARMUP_BUDGET_SECONDS) -> WarmupReport:
    """Parse :data:`WARMUP_CORPUS` here and on every parse worker thread.

    Call it before the process accepts SQL: the calling thread's parses train
    the shared caches that inline parses use.
    """
    started = time.monotonic()
    deadline = started + budget_seconds
    threads = settings.SQL_PARSE_THREADS
    futures: list[Future[tuple[int, bool]]] = []
    if settings.SQL_PARSE_OFFLOAD_MIN_CHARS:
        barrier = threading.Barrier(threads)
        pool = sql_parser._pool()
        futures = [pool.submit(_warm_worker, barrier, deadline) for _ in range(threads)]
    statements, complete = _warm_this_thread(deadline)
    warmed_workers = 0
    for future in futures:
        try:
            count, done = future.result(timeout=max(0.0, deadline - time.monotonic()) + 0.5)
        except Exception:  # noqa: BLE001 - a timeout or failure never fails startup
            count, done = 0, False
        statements += count
        warmed_workers += 1 if done else 0
        complete = complete and done
    report = WarmupReport(
        statements=statements,
        worker_threads=warmed_workers,
        elapsed_ms=round((time.monotonic() - started) * 1000, 1),
        complete=complete,
    )
    logger.info(
        "SQL parser warmed: %d statements on the serving thread and %d of %d parse "
        "worker threads in %.1f ms%s",
        statements, warmed_workers, len(futures), report.elapsed_ms,
        "" if complete else " (stopped at the time budget)",
    )
    return report


__all__ = ["WARMUP_BUDGET_SECONDS", "WARMUP_CORPUS", "WarmupReport", "warm_parser"]
