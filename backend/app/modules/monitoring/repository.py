"""Monitoring repository — system-level queries for monitoring dashboards.

All queries run through ``db.execute_system()`` (admin connection pool)
so they work regardless of the authenticated user's RBAC privileges.
"""

import logging
from typing import TYPE_CHECKING

from app.core.database import db

if TYPE_CHECKING:
    from app.modules.monitoring.alerts import AlertMetrics
    from app.modules.monitoring.readiness import AuditInput

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
            SELECT log_id, query_id, event_time, user_name, database_name,
                   sql_text, COALESCE(duration_ms, 0) AS duration_ms,
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
                    "log_id": row[0] or "",
                    "query_id": row[1] or "",
                    "event_time": str(row[2]) if row[2] else "",
                    "user_name": row[3] or "",
                    "database_name": row[4],
                    "sql_text": row[5] or "",
                    "duration_ms": row[6] or 0,
                    "rows_affected": row[7] or 0,
                    "status": row[8] or "",
                    "error_message": row[9],
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
                    "period": row[0] or "",
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
        reachable = False

        # FE membership -------------------------------------------------
        try:
            result = await db.execute_system("SHOW FRONTENDS")
            rows = self._rows_to_maps(result)
            reachable = True
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

        # BE membership / storage --------------------------------------
        try:
            result = await db.execute_system("SHOW BACKENDS")
            rows = self._rows_to_maps(result)
            reachable = True
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

        # Compaction score (FE) ----------------------------------------
        try:
            result = await db.execute_system(
                "SELECT MAX(MAX_COMPACTION_SCORE) FROM information_schema.be_tablets"
            )
            if result["rows"]:
                metrics.max_compaction_score = self._float_or_none(result["rows"][0][0])
        except Exception as exc:
            logger.debug("Alert probe compaction score failed: %s", exc)

        # BE compaction failures ---------------------------------------
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

        # FE metric ratios ---------------------------------------------
        try:
            metrics.query_err_rate, metrics.query_latency_p95_ms = (
                await self._fe_query_metrics()
            )
        except Exception as exc:
            logger.debug("Alert probe fe query metrics failed: %s", exc)

        try:
            metrics.fe_heap_used_ratio = await self._fe_heap_ratio()
        except Exception as exc:
            logger.debug("Alert probe fe heap failed: %s", exc)

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
