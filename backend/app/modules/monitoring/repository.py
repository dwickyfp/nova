"""Monitoring repository — system-level queries for monitoring dashboards.

Engine views run through ``db.execute_system()`` (admin connection pool).
Audit history is read from the metadata audit log in the caller's own
transaction, so row-level security applies to it.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.common.metadata_audit import AuditReader, audit_filter
from app.core.database import db
from app.core.metadata_scope import metadata_transaction
from app.modules.access_control.security_context import SecurityContext

if TYPE_CHECKING:
    from app.modules.monitoring.alerts import AlertMetrics
    from app.modules.monitoring.readiness import AuditInput

logger = logging.getLogger(__name__)

#: Databases whose loads are bookkeeping, not data people load: the engine's
#: statistics collector and Nova's own control plane. They make up nearly all
#: of the engine's load history.
_INTERNAL_LOAD_DBS = ("_statistics_", "NOVA_SYSTEM")
_LOADS_CACHE_KEY = "nova:monitoring:loads:recent"
_LOADS_REFRESH_LOCK = "nova:monitoring:loads:refreshing"
#: An answer this recent is served as is; an older one is served while refreshed.
_LOADS_FRESH_SECONDS = 30
#: How long a stale answer may still be served before a visit waits again.
_LOADS_KEEP_SECONDS = 600
_LOADS_LOCK_SECONDS = 60
_BACKGROUND: set["asyncio.Task[Any]"] = set()
_STATS_CACHE_KEY = "nova:monitoring:loads:stats"
_STATS_REFRESH_LOCK = "nova:monitoring:loads:stats:refreshing"
#: The history page shows recent loads; older ones stay reachable by database.
_LOADS_SCAN_LIMIT = 5000
_LOAD_COLUMNS = (
    "LABEL, DB_NAME, TABLE_NAME, TYPE, STATE, PROGRESS, CREATE_TIME, LOAD_START_TIME, "
    "LOAD_COMMIT_TIME, LOAD_FINISH_TIME, ERROR_MSG, SINK_ROWS, SCAN_ROWS"
)


def _load_item(row: tuple) -> dict:
    return {
        "label": row[0] or "",
        "db_name": row[1] or "",
        "table_name": row[2] or "",
        "load_type": row[3] or "",
        "state": row[4] or "",
        "progress": row[5] or "",
        "create_time": str(row[6]) if row[6] else "",
        "load_start_time": str(row[7]) if row[7] else "",
        "load_commit_time": str(row[8]) if row[8] else "",
        "load_finish_time": str(row[9]) if row[9] else "",
        "error_msg": row[10],
        "sink_rows": row[11] or 0,
        "scan_rows": row[12] or 0,
    }


class MonitoringRepository:
    """System-level queries powering the six monitoring pages."""

    async def assistant_usage(
        self, actor: SecurityContext, start: datetime, end: datetime, *, limit: int,
    ) -> list[dict]:
        """Token counters only; conversation bodies never enter monitoring."""
        async with metadata_transaction(actor, read_only=True) as scope:
            cursor = await scope.connection.execute(
                "SELECT m.message_id,m.created_at,m.user_name,m.model_name,m.prompt_tokens,"
                "m.completion_tokens,m.total_tokens,t.workspace_file_id "
                "FROM nova_meta.config_assistant_messages m "
                "LEFT JOIN nova_meta.config_assistant_threads t "
                "ON t.thread_id=m.thread_id AND t.user_name=m.user_name "
                "WHERE m.role='assistant' AND (m.agent_id IS NULL "
                "OR m.agent_id NOT IN ('__smart__', '__auto__')) "
                "AND m.created_at>=%s AND m.created_at<%s "
                "ORDER BY m.created_at DESC,m.message_id DESC LIMIT %s",
                (start, end, limit),
            )
            return list(await cursor.fetchall())

    async def ai_query_usage(
        self, actor: SecurityContext, start: datetime, end: datetime,
        *, patterns: tuple[str, ...], limit: int,
    ) -> list[dict]:
        """Candidate AI calls from PG audit history, still subject to its RLS."""
        if not patterns:
            return []
        async with metadata_transaction(actor, read_only=True) as scope:
            cursor = await scope.connection.execute(
                "SELECT log_id,event_time,user_name,sql_text,status,duration_ms "
                "FROM nova_meta.audit_log WHERE event_type='query' "
                "AND event_time>=%s AND event_time<%s "
                f"AND ({' OR '.join(['UPPER(sql_text) LIKE %s'] * len(patterns))}) "
                "ORDER BY event_time DESC,log_id DESC LIMIT %s",
                (start, end, *patterns, limit),
            )
            return list(await cursor.fetchall())

    # ── Query History (audit events where event_type='query') ────────
    #
    # Audit reads run in the caller's metadata transaction. Row-level security
    # shows an administration role every event and anyone else their own.

    async def get_query_history(
        self,
        *,
        actor: SecurityContext,
        limit: int = 50,
        offset: int = 0,
        user_name: str | None = None,
        status: str | None = None,
        database_name: str | None = None,
        min_duration_ms: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        search: str | None = None,
    ) -> dict:
        """Paginated query history (event_type='query' only)."""
        selection = audit_filter(
            event_type="query",
            user_name=user_name or None,
            status=status.upper() if status else None,
            database_name=database_name or None,
            min_duration_ms=min_duration_ms,
            since=date_from,
            through=date_to,
            sql_contains=search or None,
        )
        async with metadata_transaction(actor, read_only=True) as scope:
            reader = AuditReader(scope.connection)
            total = await reader.count(selection)
            rows = await reader.events(
                selection,
                ("log_id", "query_id", "event_time", "user_name", "sql_text", "status",
                 "duration_ms", "rows_affected", "error_message", "database_name",
                 "schema_name", "session_id", "file_id"),
                limit=limit, offset=offset,
            )
        items = [
            {
                "log_id": row["log_id"],
                "query_id": row["query_id"] or "",
                "event_time": str(row["event_time"]) if row["event_time"] else "",
                "user_name": row["user_name"] or "",
                "sql_text": row["sql_text"] or "",
                "status": row["status"] or "",
                "duration_ms": row["duration_ms"],
                "rows_affected": row["rows_affected"],
                "error_message": row["error_message"],
                "database_name": row["database_name"],
                "schema_name": row["schema_name"],
                "session_id": row["session_id"],
                "file_id": row["file_id"],
            }
            for row in rows
        ]
        return {"items": items, "total": total}

    async def get_query_history_stats(
        self,
        *,
        actor: SecurityContext,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict:
        """Aggregate stats for the query history dashboard."""
        selection = audit_filter(event_type="query", since=date_from, through=date_to)
        async with metadata_transaction(actor, read_only=True) as scope:
            summary = await AuditReader(scope.connection).summary(selection)
        total = summary["total"] or 0
        error_count = summary["error_count"] or 0
        return {
            "total": total,
            "avg_duration_ms": round(float(summary["avg_duration_ms"] or 0), 2),
            "error_count": error_count,
            "success_count": summary["non_error_count"] or 0,
            "error_rate": round(error_count / total, 4) if total > 0 else 0.0,
        }

    # ── Audit Trail (ALL event types) ────────────────────────────────

    async def get_audit_trail(
        self,
        *,
        actor: SecurityContext,
        limit: int = 50,
        offset: int = 0,
        event_type: str | None = None,
        user_name: str | None = None,
        status: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict:
        """Paginated audit trail — ALL event types."""
        selection = audit_filter(
            event_type=event_type or None,
            user_name=user_name or None,
            status=status.upper() if status else None,
            since=date_from,
            through=date_to,
        )
        async with metadata_transaction(actor, read_only=True) as scope:
            reader = AuditReader(scope.connection)
            total = await reader.count(selection)
            rows = await reader.events(
                selection,
                ("log_id", "query_id", "event_type", "event_time", "user_name",
                 "object_type", "object_name", "action", "sql_text", "status",
                 "error_message", "duration_ms", "rows_affected", "session_id",
                 "database_name", "schema_name"),
                limit=limit, offset=offset,
            )
        items = [
            {
                "log_id": row["log_id"],
                "query_id": row["query_id"] or "",
                "event_type": row["event_type"] or "",
                "event_time": str(row["event_time"]) if row["event_time"] else "",
                "user_name": row["user_name"] or "",
                "object_type": row["object_type"],
                "object_name": row["object_name"],
                "action": row["action"] or "",
                "sql_text": row["sql_text"],
                "status": row["status"] or "",
                "error_message": row["error_message"],
                "duration_ms": row["duration_ms"],
                "rows_affected": row["rows_affected"],
                "session_id": row["session_id"],
                "database_name": row["database_name"],
                "schema_name": row["schema_name"],
            }
            for row in rows
        ]
        return {"items": items, "total": total}

    # ── Active Queries (processlist) ─────────────────────────────────

    async def get_active_queries(self) -> list[dict]:
        """Current running queries via SHOW PROCESSLIST."""
        result = await db.execute_system("SHOW PROCESSLIST")

        items = []
        for row in result["rows"]:
            # SHOW PROCESSLIST columns (15):
            # 0:ServerName, 1:Id, 2:User, 3:Host, 4:Db,
            # 5:Command, 6:ConnectionStartTime, 7:Time, 8:State, 9:Info,
            # 10:IsPending, 11:Warehouse, 12:CNGroup, 13:Catalog, 14:QueryId
            conn_id = row[1]
            user = row[2] or ""
            host = row[3] or ""
            db_name = row[4]
            command = row[5] or ""
            time_sec = row[7] if row[7] is not None else 0
            state = row[8] or ""
            info = row[9] if len(row) > 9 else None
            query_id = row[14] if len(row) > 14 else None

            # Skip idle / system connections with no query text
            if command in ("Sleep", "Daemon") and not info:
                continue

            items.append(
                {
                    "id": conn_id,
                    "user": user,
                    "host": host,
                    "db": db_name,
                    "command": command,
                    "time": time_sec,
                    "state": state,
                    "info": info,
                    "query_id": query_id,
                }
            )

        return items

    async def kill_query(self, connection_id: int) -> bool:
        """Kill a running query by connection ID."""
        try:
            await db.execute_system(f"KILL QUERY {int(connection_id)}")
            return True
        except Exception as exc:
            logger.warning("Failed to kill query %s: %s", connection_id, exc)
            return False

    # ── Tasks ────────────────────────────────────────────────────────

    async def get_task_runs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        task_name: str | None = None,
        state: str | None = None,
    ) -> dict:
        """Paginated task run history from information_schema.task_runs."""
        conditions: list[str] = []
        params: list = []

        if task_name:
            conditions.append("TASK_NAME = %s")
            params.append(task_name)
        if state:
            conditions.append("STATE = %s")
            params.append(state.upper())

        where = " AND ".join(conditions) if conditions else "1 = 1"

        count_result = await db.execute_system(
            f"SELECT COUNT(*) FROM information_schema.task_runs WHERE {where}",
            params,
        )
        total = count_result["rows"][0][0] if count_result["rows"] else 0

        result = await db.execute_system(
            f"""
            SELECT TASK_NAME, CREATE_TIME, FINISH_TIME, STATE,
                   ERROR_MESSAGE, `PROPERTIES`
            FROM information_schema.task_runs
            WHERE {where}
            ORDER BY CREATE_TIME DESC
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )

        items = []
        for row in result["rows"]:
            items.append(
                {
                    "task_name": row[0] or "",
                    "create_time": str(row[1]) if row[1] else "",
                    "finish_time": str(row[2]) if row[2] else "",
                    "state": row[3] or "",
                    "error_message": row[4],
                    "properties": row[5],
                }
            )

        return {"items": items, "total": total}

    async def get_tasks(self) -> list[dict]:
        """List defined async tasks from information_schema.tasks."""
        result = await db.execute_system(
            """
            SELECT TASK_NAME, CREATE_TIME, SCHEDULE, `DATABASE`,
                   DEFINITION, PROPERTIES
            FROM information_schema.tasks
            ORDER BY CREATE_TIME DESC
            """
        )

        items = []
        for row in result["rows"]:
            items.append(
                {
                    "task_name": row[0] or "",
                    "create_time": str(row[1]) if row[1] else "",
                    "schedule": row[2],
                    "database": row[3],
                    "definition": row[4],
                    "properties": row[5],
                }
            )

        return items

    # ── Query Cost ───────────────────────────────────────────────────

    async def get_query_cost_history(
        self,
        *,
        actor: SecurityContext,
        limit: int = 50,
        offset: int = 0,
        user_name: str | None = None,
        database_name: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict:
        """Paginated query cost history with duration/rows."""
        selection = audit_filter(
            event_type="query",
            user_name=user_name or None,
            database_name=database_name or None,
            since=date_from,
            through=date_to,
        )
        async with metadata_transaction(actor, read_only=True) as scope:
            reader = AuditReader(scope.connection)
            total = await reader.count(selection)
            rows = await reader.events(
                selection,
                ("log_id", "query_id", "event_time", "user_name", "database_name",
                 "sql_text", "duration_ms", "rows_affected", "status", "error_message"),
                limit=limit, offset=offset,
            )
        items = [
            {
                "log_id": row["log_id"] or "",
                "query_id": row["query_id"] or "",
                "event_time": str(row["event_time"]) if row["event_time"] else "",
                "user_name": row["user_name"] or "",
                "database_name": row["database_name"],
                "sql_text": row["sql_text"] or "",
                "duration_ms": row["duration_ms"] or 0,
                "rows_affected": row["rows_affected"] or 0,
                "status": row["status"] or "",
                "error_message": row["error_message"],
            }
            for row in rows
        ]
        return {"items": items, "total": total}

    async def get_cost_aggregation(
        self,
        *,
        actor: SecurityContext,
        group_by: str = "hour",
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict]:
        """Query events by UTC hour or day for chart data."""
        selection = audit_filter(event_type="query", since=date_from, through=date_to)
        async with metadata_transaction(actor, read_only=True) as scope:
            rows = await AuditReader(scope.connection).buckets(
                selection, "day" if group_by == "day" else "hour",
            )
        return [
            {
                "period": row["period"] or "",
                "query_count": row["query_count"] or 0,
                "avg_duration_ms": round(float(row["avg_duration_ms"] or 0), 2),
                "total_rows": row["total_rows"] or 0,
                "error_count": row["error_count"] or 0,
            }
            for row in rows
        ]

    async def get_fe_metrics_summary(self) -> dict:
        """Key metrics from information_schema.fe_metrics.

        StarRocks fe_metrics returns rows with columns like
        (METRIC_NAME, METRIC_VALUE). We parse the ones we care about
        and return a flat summary dict.
        """
        target_metrics = {
            "query_total",
            "query_success",
            "query_err",
            "slow_query",
            "connection_total",
            "query_latency_ms",
            "query_latency_99th_ms",
            "query_latency_95th_ms",
            "query_begin_failed",
            "query_internal_error",
        }

        try:
            result = await db.execute_system(
                "SELECT * FROM information_schema.fe_metrics"
            )
        except Exception as exc:
            logger.warning("Failed to query fe_metrics: %s", exc)
            return {}

        metrics: dict = {}
        for row in result["rows"]:
            # fe_metrics columns: FE_ID(0), NAME(1), LABELS(2), VALUE(3)
            name = str(row[1]).lower() if row[1] else ""
            value = row[3] if len(row) > 3 else None

            # Match by prefix so we capture gauge and counter variants
            for target in target_metrics:
                if target in name:
                    metrics[target] = self._parse_metric_value(value)
                    break

        return metrics

    # ── Data Loads ───────────────────────────────────────────────────

    async def get_data_loads(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        state: str | None = None,
        db_name: str | None = None,
        load_type: str | None = None,
    ) -> dict:
        """Paginated data load history from information_schema.loads.

        The engine keeps every load job, including the many small writes of
        Nova's own control plane and of its statistics collector, and it only
        narrows its scan for a single ``DB_NAME = ...`` predicate. So:

        * a database filter goes straight to the engine (fast, narrowed scan);
        * otherwise one scan reads the recent loads into user databases, which
          is cached briefly and filtered and paged here, instead of two full
          scans (count and page) per request.
        """
        if db_name:
            conditions = ["DB_NAME = %s"]
            params: list = [db_name]
            if state:
                conditions.append("STATE = %s")
                params.append(state.upper())
            if load_type:
                conditions.append("TYPE = %s")
                params.append(load_type)
            where = " AND ".join(conditions)
            count_result = await db.execute_system(
                f"SELECT COUNT(*) FROM information_schema.loads WHERE {where}",
                params,
            )
            total = count_result["rows"][0][0] if count_result["rows"] else 0
            result = await db.execute_system(
                f"SELECT {_LOAD_COLUMNS} FROM information_schema.loads WHERE {where} "
                "ORDER BY CREATE_TIME DESC LIMIT %s OFFSET %s",
                params + [limit, offset],
            )
            return {"items": [_load_item(row) for row in result["rows"]], "total": total}

        items = await self._recent_loads()
        if state:
            items = [item for item in items if item["state"] == state.upper()]
        if load_type:
            items = [item for item in items if item["load_type"] == load_type]
        return {"items": items[offset : offset + limit], "total": len(items)}

    async def _recent_loads(self) -> list[dict]:
        """The most recent loads into user databases, from one cached scan."""
        items = await self._cached_scan(
            _LOADS_CACHE_KEY, _LOADS_REFRESH_LOCK, self._scan_recent_loads,
        )
        return list(items)

    async def _cached_scan(
        self, key: str, lock: str, scan: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Stale-while-revalidate over one slow, caller-independent engine scan.

        An answer older than the fresh window is still returned at once while
        one background scan replaces it, so only the very first visit waits.
        Callers check access before calling; the cached value is the same
        system-pool answer for every permitted caller.
        """
        from app.core.redis import session_store

        client = session_store._redis
        if client is None:
            return await scan()
        try:
            cached = await client.get(key)
        except Exception:  # noqa: BLE001 - a cache fault never blocks the answer
            logger.debug("Monitoring cache read failed", exc_info=True)
            return await scan()
        if not cached:
            return await self._refresh(client, key, lock, scan)
        entry = json.loads(cached)
        if not isinstance(entry, dict) or "items" not in entry:
            return await self._refresh(client, key, lock, scan)
        if time.time() - float(entry.get("at", 0)) > _LOADS_FRESH_SECONDS:
            try:
                # One refresh at a time across workers.
                if await client.set(lock, "1", nx=True, ex=_LOADS_LOCK_SECONDS):
                    task = asyncio.create_task(self._refresh(client, key, lock, scan))
                    _BACKGROUND.add(task)
                    task.add_done_callback(_BACKGROUND.discard)
            except Exception:  # noqa: BLE001
                logger.debug("Monitoring refresh could not start", exc_info=True)
        return entry["items"]

    async def _refresh(
        self, client: Any, key: str, lock: str, scan: Callable[[], Awaitable[Any]],
    ) -> Any:
        items = await scan()
        try:
            await client.set(
                key, json.dumps({"at": time.time(), "items": items}), ex=_LOADS_KEEP_SECONDS,
            )
            await client.delete(lock)
        except Exception:  # noqa: BLE001
            logger.debug("Monitoring cache write failed", exc_info=True)
        return items

    async def _refresh_recent_loads(self, client: Any) -> list[dict]:
        return list(await self._refresh(
            client, _LOADS_CACHE_KEY, _LOADS_REFRESH_LOCK, self._scan_recent_loads,
        ))

    async def _scan_recent_loads(self) -> list[dict]:
        excluded = ", ".join(f"'{name}'" for name in _INTERNAL_LOAD_DBS)
        result = await db.execute_system(
            f"SELECT {_LOAD_COLUMNS} FROM information_schema.loads "
            f"WHERE DB_NAME NOT IN ({excluded}) "
            "ORDER BY CREATE_TIME DESC LIMIT %s",
            [_LOADS_SCAN_LIMIT],
        )
        return [_load_item(row) for row in result["rows"]]

    async def get_load_stats(self) -> dict:
        """Aggregate stats for the data loads dashboard.

        Counting every load job is a full engine scan (65 s measured with about
        220 databases), so it shares the loads list's stale-while-revalidate cache.
        """
        stats = await self._cached_scan(
            _STATS_CACHE_KEY, _STATS_REFRESH_LOCK, self._scan_load_stats,
        )
        return dict(stats)

    async def _scan_load_stats(self) -> dict:
        result = await db.execute_system(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN STATE = 'FINISHED' THEN 1 ELSE 0 END)  AS finished,
                SUM(CASE WHEN STATE = 'CANCELLED' THEN 1 ELSE 0 END) AS cancelled,
                SUM(CASE WHEN STATE = 'LOADING' THEN 1 ELSE 0 END)   AS loading,
                SUM(CASE WHEN STATE NOT IN ('FINISHED', 'CANCELLED', 'LOADING')
                    THEN 1 ELSE 0 END) AS other
            FROM information_schema.loads
            WHERE DB_NAME NOT IN ('_statistics_')
            """
        )

        if not result["rows"]:
            return {
                "total": 0,
                "finished": 0,
                "cancelled": 0,
                "loading": 0,
                "other": 0,
            }

        row = result["rows"][0]
        return {
            "total": row[0] or 0,
            "finished": row[1] or 0,
            "cancelled": row[2] or 0,
            "loading": row[3] or 0,
            "other": row[4] or 0,
        }

    # ── Alert metric collection ──────────────────────────────────────

    async def collect_alert_metrics(self) -> "AlertMetrics":
        """Build an :class:`AlertMetrics` snapshot from live engine state.

        Each probe is independent: one failing probe leaves its metric ``None``
        (unknown) instead of aborting the whole snapshot, and only a total loss
        of engine connectivity sets ``engine_reachable=False``. This keeps the
        alert engine honest — an unreadable counter is never reported as zero.
        """
        from app.modules.monitoring.alerts import AlertMetrics

        metrics = AlertMetrics()
        reached: list[bool] = []

        async def frontends() -> None:
            try:
                result = await db.execute_system("SHOW FRONTENDS")
                rows = self._rows_to_maps(result)
                reached.append(True)
                metrics.fe_total = len(rows)
                metrics.fe_alive = sum(1 for r in rows if self._truthy(r.get("Alive")))
                metrics.fe_leader_count = sum(
                    1
                    for r in rows
                    if str(r.get("Role", "")).upper() == "LEADER"
                    and self._truthy(r.get("Alive"))
                    and self._truthy(r.get("Join"))
                )
                metrics.fe_max_journal_lag = self._journal_lag(rows)
                metrics.fe_meta_log_count = self._max_int(rows, ("MetaLogCount", "metaLogCount"))
            except Exception as exc:
                logger.warning("Alert probe SHOW FRONTENDS failed: %s", exc)

        async def backends() -> None:
            try:
                result = await db.execute_system("SHOW BACKENDS")
                rows = self._rows_to_maps(result)
                reached.append(True)
                metrics.be_total = len(rows)
                live = [
                    r
                    for r in rows
                    if self._truthy(r.get("Alive"))
                    and not self._truthy(r.get("SystemDecommissioned"))
                    and not self._truthy(r.get("ClusterDecommissioned"))
                ]
                metrics.be_alive = len(live)
                metrics.be_disk_used_pct_max = self._max_float(
                    live, ("MaxDiskUsedPct", "UsedPct")
                )
            except Exception as exc:
                logger.warning("Alert probe SHOW BACKENDS failed: %s", exc)

        async def compaction_score() -> None:
            try:
                result = await db.execute_system(
                    "SELECT MAX(MAX_COMPACTION_SCORE) FROM information_schema.be_tablets"
                )
                if result["rows"]:
                    metrics.max_compaction_score = self._float_or_none(result["rows"][0][0])
            except Exception as exc:
                logger.debug("Alert probe compaction score failed: %s", exc)

        async def compaction_failures() -> None:
            try:
                result = await db.execute_system(
                    "SELECT SUM(C) FROM ("
                    " SELECT COUNT(*) AS C FROM information_schema.be_compactions"
                    " WHERE STATUS != 'SUCCESS'"
                    ") t"
                )
                if result["rows"]:
                    metrics.be_compaction_failures = self._int_or_none(result["rows"][0][0])
            except Exception as exc:
                logger.debug("Alert probe compaction failures failed: %s", exc)

        async def query_metrics() -> None:
            try:
                metrics.query_err_rate, metrics.query_latency_p95_ms = (
                    await self._fe_query_metrics()
                )
            except Exception as exc:
                logger.debug("Alert probe fe query metrics failed: %s", exc)

        async def heap() -> None:
            try:
                metrics.fe_heap_used_ratio = await self._fe_heap_ratio()
            except Exception as exc:
                logger.debug("Alert probe fe heap failed: %s", exc)

        # The probes are independent reads; each owns its fields and failure.
        await asyncio.gather(
            frontends(), backends(), compaction_score(), compaction_failures(),
            query_metrics(), heap(),
        )
        reachable = bool(reached)
        metrics.engine_reachable = reachable
        return metrics

    async def collect_audit_input(self) -> "AuditInput":
        """Gather the read-only observations the readiness audit evaluates.

        Membership and repositories come from the engine. The root
        empty-password probe runs on a *separate* connection as ``root`` so it
        cannot be conflated with the system-pool identity doing the rest of the
        reads; a failure to prove rejection is reported as unknown, never as a
        pass.
        """
        from app.modules.monitoring.readiness import AuditInput

        data = AuditInput()
        reachable = False

        try:
            fe = self._rows_to_maps(await db.execute_system("SHOW FRONTENDS"))
            data.fe_rows = fe
            reachable = True
            for r in fe:
                if r.get("ClusterId") is not None:
                    data.cluster_id = str(r.get("ClusterId"))
                    break
            for r in fe:
                if str(r.get("Role", "")).upper() == "LEADER":
                    data.version = str(r.get("Version")) if r.get("Version") else None
                    break
            if not data.version and fe:
                data.version = str(fe[0].get("Version")) if fe[0].get("Version") else None
        except Exception as exc:
            logger.warning("Readiness probe SHOW FRONTENDS failed: %s", exc)

        try:
            data.be_rows = self._rows_to_maps(await db.execute_system("SHOW BACKENDS"))
            reachable = True
        except Exception as exc:
            logger.warning("Readiness probe SHOW BACKENDS failed: %s", exc)

        try:
            data.repository_rows = self._rows_to_maps(
                await db.execute_system("SHOW REPOSITORIES")
            )
        except Exception as exc:
            logger.debug("Readiness probe SHOW REPOSITORIES failed: %s", exc)

        data.root_password_rejected = await self._probe_root_empty_password()
        data.engine_reachable = reachable
        return data

    async def _probe_root_empty_password(self) -> bool | None:
        """True if root is correctly rejected; False if it authenticates; None if unknown.

        Opens a fresh ``asyncmy`` connection as ``root`` with an empty password
        so a rejected login is observed as an error code, distinct from any
        pooled connection state. Uses the same driver as the rest of the app
        (no pymysql dependency at runtime).
        """
        import asyncmy

        from app.core.config import settings

        try:
            conn = await asyncmy.connect(
                host=settings.STARROCKS_HOST,
                port=settings.STARROCKS_FE_MYSQL_PORT,
                user="root",
                password="",
                connect_timeout=5,
            )
        except Exception as exc:
            code = exc.args[0] if getattr(exc, "args", None) else None
            if code in (1045, 1698):
                return True
            logger.debug("Could not probe root empty password: %s", exc)
            return None
        else:
            conn.close()
            return False  # authenticated with empty password — a real problem

    async def _fe_query_metrics(self) -> tuple[float | None, float | None]:
        """Derive error rate and P95 latency from ``information_schema.fe_metrics``."""
        err_rate: float | None = None
        p95: float | None = None
        try:
            result = await db.execute_system(
                """
                SELECT NAME, VALUE
                FROM information_schema.fe_metrics
                WHERE NAME IN (
                    'query_total', 'query_err', 'query_latency_95th_ms'
                )
                """
            )
            by_name: dict[str, float] = {}
            for row in result["rows"]:
                name = str(row[0] or "").lower()
                value = self._float_or_none(row[1])
                if value is not None:
                    by_name[name] = value
            total = by_name.get("query_total")
            err = by_name.get("query_err")
            if total is not None and err is not None and total > 0:
                err_rate = err / total
            p95 = by_name.get("query_latency_95th_ms")
        except Exception:
            return None, None
        return err_rate, p95

    async def _fe_heap_ratio(self) -> float | None:
        """Used/max FE heap ratio. Returns None unless both halves are known."""
        used: float | None = None
        cap: float | None = None
        try:
            result = await db.execute_system(
                """
                SELECT NAME, LABELS, VALUE
                FROM information_schema.fe_metrics
                WHERE NAME = 'jvm_heap_size_bytes'
                """
            )
            for row in result["rows"]:
                labels = str(row[1] or "").lower()
                value = self._float_or_none(row[2])
                if value is None:
                    continue
                if "'used'" in labels or '"used"' in labels or "=used" in labels:
                    used = value
                elif "'max'" in labels or '"max"' in labels or "=max" in labels:
                    cap = value
        except Exception:
            return None
        if used is None or cap is None or cap <= 0:
            return None
        return used / cap

    # ── Internal helpers ─────────────────────────────────────────────

    @staticmethod
    def _rows_to_maps(result: dict) -> list[dict]:
        columns = [str(c) for c in result.get("columns", [])]
        return [
            {columns[i]: value for i, value in enumerate(row) if i < len(columns)}
            for row in (result.get("rows") or [])
        ]

    @staticmethod
    def _truthy(value) -> bool:
        if value is None:
            return False
        return str(value).strip().lower() in {"true", "1", "yes", "alive", "ok"}

    @classmethod
    def _journal_lag(cls, rows: list[dict]) -> int | None:
        journals: list[int] = []
        for r in rows:
            if str(r.get("Role", "")).upper() not in {"LEADER", "FOLLOWER"}:
                continue
            value = cls._int_or_none(r.get("ReplayedJournalId"))
            if value is not None:
                journals.append(value)
        if len(journals) < 2:
            return None
        return max(journals) - min(journals)

    @classmethod
    def _max_int(cls, rows: list[dict], keys: tuple[str, ...]) -> int | None:
        values = []
        for r in rows:
            for key in keys:
                value = cls._int_or_none(r.get(key))
                if value is not None:
                    values.append(value)
                    break
        return max(values) if values else None

    @classmethod
    def _max_float(cls, rows: list[dict], keys: tuple[str, ...]) -> float | None:
        values = []
        for r in rows:
            for key in keys:
                value = cls._float_or_none(r.get(key))
                if value is not None:
                    values.append(value)
                    break
        return max(values) if values else None

    @staticmethod
    def _float_or_none(raw) -> float | None:
        if raw is None:
            return None
        try:
            return float(str(raw).strip().rstrip("%"))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _int_or_none(raw) -> int | None:
        if raw is None:
            return None
        try:
            return int(float(str(raw).strip()))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_metric_value(raw) -> int | float | str:
        """Best-effort parse of an fe_metrics value string to a number."""
        if raw is None:
            return 0
        s = str(raw).strip()
        if not s:
            return 0
        try:
            if "." in s:
                return float(s)
            return int(s)
        except (ValueError, TypeError):
            return s


monitoring_repo = MonitoringRepository()
