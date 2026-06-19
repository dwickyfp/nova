"""Monitoring repository — system-level queries for monitoring dashboards.

All queries run through ``db.execute_system()`` (admin connection pool)
so they work regardless of the authenticated user's RBAC privileges.
"""

import logging
from datetime import datetime

from app.core.database import db

logger = logging.getLogger(__name__)


class MonitoringRepository:
    """System-level queries powering the six monitoring pages."""

    # ── Query History (AUDIT_LOG where event_type='query') ───────────

    async def get_query_history(
        self,
        *,
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
        """Paginated query history from AUDIT_LOG (event_type='query' only)."""
        conditions = ["event_type = 'query'"]
        params: list = []

        if user_name:
            conditions.append("user_name = %s")
            params.append(user_name)
        if status:
            conditions.append("status = %s")
            params.append(status.upper())
        if database_name:
            conditions.append("database_name = %s")
            params.append(database_name)
        if min_duration_ms is not None:
            conditions.append("duration_ms >= %s")
            params.append(min_duration_ms)
        if date_from:
            conditions.append("event_time >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("event_time <= %s")
            params.append(date_to)
        if search:
            conditions.append("sql_text LIKE %s")
            params.append(f"%{search}%")

        where = " AND ".join(conditions)

        count_result = await db.execute_system(
            f"SELECT COUNT(*) FROM NOVA_SYSTEM.AUDIT_LOG WHERE {where}",
            params,
        )
        total = count_result["rows"][0][0] if count_result["rows"] else 0

        result = await db.execute_system(
            f"""
            SELECT log_id, query_id, event_time, user_name, sql_text, status,
                   duration_ms, rows_affected, error_message, database_name,
                   schema_name, session_id, file_id
            FROM NOVA_SYSTEM.AUDIT_LOG
            WHERE {where}
            ORDER BY event_time DESC
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )

        items = []
        for row in result["rows"]:
            items.append(
                {
                    "log_id": row[0],
                    "query_id": row[1] or "",
                    "event_time": str(row[2]) if row[2] else "",
                    "user_name": row[3] or "",
                    "sql_text": row[4] or "",
                    "status": row[5] or "",
                    "duration_ms": row[6],
                    "rows_affected": row[7],
                    "error_message": row[8],
                    "database_name": row[9],
                    "schema_name": row[10],
                    "session_id": row[11],
                    "file_id": row[12],
                }
            )

        return {"items": items, "total": total}

    async def get_query_history_stats(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict:
        """Aggregate stats for the query history dashboard."""
        conditions = ["event_type = 'query'"]
        params: list = []

        if date_from:
            conditions.append("event_time >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("event_time <= %s")
            params.append(date_to)

        where = " AND ".join(conditions)

        result = await db.execute_system(
            f"""
            SELECT
                COUNT(*)                                          AS total,
                COALESCE(AVG(COALESCE(duration_ms, 0)), 0)       AS avg_duration_ms,
                SUM(CASE WHEN status = 'ERROR' THEN 1 ELSE 0 END) AS error_count,
                SUM(CASE WHEN status != 'ERROR' THEN 1 ELSE 0 END) AS success_count
            FROM NOVA_SYSTEM.AUDIT_LOG
            WHERE {where}
            """,
            params,
        )

        if not result["rows"]:
            return {
                "total": 0,
                "avg_duration_ms": 0.0,
                "error_count": 0,
                "success_count": 0,
                "error_rate": 0.0,
            }

        row = result["rows"][0]
        total = row[0] or 0
        avg_duration_ms = round(float(row[1] or 0), 2)
        error_count = row[2] or 0
        success_count = row[3] or 0
        error_rate = round(error_count / total, 4) if total > 0 else 0.0

        return {
            "total": total,
            "avg_duration_ms": avg_duration_ms,
            "error_count": error_count,
            "success_count": success_count,
            "error_rate": error_rate,
        }

    # ── Audit Trail (ALL event types) ────────────────────────────────

    async def get_audit_trail(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        event_type: str | None = None,
        user_name: str | None = None,
        status: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict:
        """Paginated audit trail — ALL event types from AUDIT_LOG."""
        conditions: list[str] = []
        params: list = []

        if event_type:
            conditions.append("event_type = %s")
            params.append(event_type)
        if user_name:
            conditions.append("user_name = %s")
            params.append(user_name)
        if status:
            conditions.append("status = %s")
            params.append(status.upper())
        if date_from:
            conditions.append("event_time >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("event_time <= %s")
            params.append(date_to)

        where = " AND ".join(conditions) if conditions else "1 = 1"

        count_result = await db.execute_system(
            f"SELECT COUNT(*) FROM NOVA_SYSTEM.AUDIT_LOG WHERE {where}",
            params,
        )
        total = count_result["rows"][0][0] if count_result["rows"] else 0

        result = await db.execute_system(
            f"""
            SELECT log_id, query_id, event_type, event_time, user_name,
                   object_type, object_name, action, sql_text, status,
                   error_message, duration_ms, rows_affected, session_id,
                   database_name, schema_name
            FROM NOVA_SYSTEM.AUDIT_LOG
            WHERE {where}
            ORDER BY event_time DESC
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )

        items = []
        for row in result["rows"]:
            items.append(
                {
                    "log_id": row[0],
                    "query_id": row[1] or "",
                    "event_type": row[2] or "",
                    "event_time": str(row[3]) if row[3] else "",
                    "user_name": row[4] or "",
                    "object_type": row[5],
                    "object_name": row[6],
                    "action": row[7] or "",
                    "sql_text": row[8],
                    "status": row[9] or "",
                    "error_message": row[10],
                    "duration_ms": row[11],
                    "rows_affected": row[12],
                    "session_id": row[13],
                    "database_name": row[14],
                    "schema_name": row[15],
                }
            )

        return {"items": items, "total": total}

    # ── Active Queries (processlist) ─────────────────────────────────

    async def get_active_queries(self) -> list[dict]:
        """Current running queries via SHOW PROCESSLIST."""
        result = await db.execute_system("SHOW PROCESSLIST")

        items = []
        for row in result["rows"]:
            # SHOW PROCESSLIST columns: Id, User, Host, Db, Command, Time, State, Info
            conn_id = row[0]
            user = row[1] or ""
            host = row[2] or ""
            db_name = row[3]
            command = row[4] or ""
            time_sec = row[5] or 0
            state = row[6] or ""
            info = row[7] if len(row) > 7 else None

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
        limit: int = 50,
        offset: int = 0,
        user_name: str | None = None,
        database_name: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict:
        """Paginated query cost history from AUDIT_LOG with duration/rows."""
        conditions = ["event_type = 'query'"]
        params: list = []

        if user_name:
            conditions.append("user_name = %s")
            params.append(user_name)
        if database_name:
            conditions.append("database_name = %s")
            params.append(database_name)
        if date_from:
            conditions.append("event_time >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("event_time <= %s")
            params.append(date_to)

        where = " AND ".join(conditions)

        count_result = await db.execute_system(
            f"SELECT COUNT(*) FROM NOVA_SYSTEM.AUDIT_LOG WHERE {where}",
            params,
        )
        total = count_result["rows"][0][0] if count_result["rows"] else 0

        result = await db.execute_system(
            f"""
            SELECT query_id, event_time, user_name, database_name,
                   COALESCE(duration_ms, 0)   AS duration_ms,
                   COALESCE(rows_affected, 0) AS rows_affected,
                   status, error_message
            FROM NOVA_SYSTEM.AUDIT_LOG
            WHERE {where}
            ORDER BY event_time DESC
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )

        items = []
        for row in result["rows"]:
            items.append(
                {
                    "query_id": row[0] or "",
                    "event_time": str(row[1]) if row[1] else "",
                    "user_name": row[2] or "",
                    "database_name": row[3],
                    "duration_ms": row[4] or 0,
                    "rows_affected": row[5] or 0,
                    "status": row[6] or "",
                    "error_message": row[7],
                }
            )

        return {"items": items, "total": total}

    async def get_cost_aggregation(
        self,
        *,
        group_by: str = "hour",
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict]:
        """Aggregate AUDIT_LOG by hour/day for chart data."""
        if group_by == "day":
            time_bucket = "DATE_FORMAT(event_time, '%%Y-%%m-%%d')"
        else:
            time_bucket = "DATE_FORMAT(event_time, '%%Y-%%m-%%d %%H:00:00')"

        conditions = ["event_type = 'query'"]
        params: list = []

        if date_from:
            conditions.append("event_time >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("event_time <= %s")
            params.append(date_to)

        where = " AND ".join(conditions)

        result = await db.execute_system(
            f"""
            SELECT
                {time_bucket}                   AS bucket,
                COUNT(*)                         AS query_count,
                COALESCE(AVG(COALESCE(duration_ms, 0)), 0) AS avg_duration_ms,
                COALESCE(SUM(COALESCE(rows_affected, 0)), 0) AS total_rows,
                SUM(CASE WHEN status = 'ERROR' THEN 1 ELSE 0 END) AS error_count
            FROM NOVA_SYSTEM.AUDIT_LOG
            WHERE {where}
            GROUP BY bucket
            ORDER BY bucket ASC
            """,
            params,
        )

        items = []
        for row in result["rows"]:
            items.append(
                {
                    "bucket": row[0] or "",
                    "query_count": row[1] or 0,
                    "avg_duration_ms": round(float(row[2] or 0), 2),
                    "total_rows": row[3] or 0,
                    "error_count": row[4] or 0,
                }
            )

        return items

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
            # fe_metrics typically has (name, value) columns but
            # exact column count/position may vary across StarRocks versions.
            name = str(row[0]).lower() if row[0] else ""
            value = row[1] if len(row) > 1 else None

            # Match by prefix so we capture gauge and counter variants
            for target in target_metrics:
                if target in name:
                    metrics[name] = self._parse_metric_value(value)
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

        Filters out ``_statistics_`` database by default to exclude
        internal StarRocks maintenance loads.
        """
        conditions = ["DB_NAME NOT IN ('_statistics_')"]
        params: list = []

        if state:
            conditions.append("STATE = %s")
            params.append(state.upper())
        if db_name:
            conditions.append("DB_NAME = %s")
            params.append(db_name)
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
            f"""
            SELECT LABEL, DB_NAME, TABLE_NAME, TYPE, STATE,
                   PROGRESS, CREATE_TIME, LOAD_START_TIME,
                   LOAD_COMMIT_TIME, LOAD_FINISH_TIME,
                   ERROR_MSG, SINK_ROWS, SCAN_ROWS
            FROM information_schema.loads
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
            )

        return {"items": items, "total": total}

    async def get_load_stats(self) -> dict:
        """Aggregate stats for the data loads dashboard."""
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

    # ── Internal helpers ─────────────────────────────────────────────

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
