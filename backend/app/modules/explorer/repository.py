"""Explorer repository — read-only StarRocks metadata queries (system pool)."""

import json
import logging

from app.core.database import db

log = logging.getLogger(__name__)

SYSTEM_DBS = frozenset({"_statistics_", "information_schema", "sys"})


class ExplorerRepository:
    """Query StarRocks metadata via the admin system pool."""

    # ── Catalogs ───────────────────────────────────────────────

    async def list_catalogs(self) -> list[dict]:
        """SHOW CATALOGS → Catalog, Type, Comment."""
        result = await db.execute_system("SHOW CATALOGS")
        catalogs = []
        for row in result["rows"]:
            catalogs.append({
                "name": row[0],
                "type": row[1] if len(row) > 1 else "Internal",
                "comment": row[2] if len(row) > 2 else None,
            })
        return catalogs

    async def list_databases_for_catalog(self) -> list[str]:
        """SHOW DATABASES filtered."""
        result = await db.execute_system("SHOW DATABASES")
        return [
            row[0]
            for row in result["rows"]
            if row[0] not in SYSTEM_DBS
        ]

    # ── Database objects ───────────────────────────────────────

    async def list_tables(self, database: str) -> list[dict]:
        """Tables via information_schema.TABLES_CONFIG (StarRocks-specific)."""
        sql = (
            "SELECT TABLE_NAME, TABLE_MODEL, PRIMARY_KEY, "
            "DISTRIBUTE_KEY, DISTRIBUTE_TYPE, DISTRIBUTE_BUCKET, "
            "SORT_KEY, PROPERTIES "
            "FROM information_schema.tables_config "
            "WHERE TABLE_SCHEMA = %s"
        )
        result = await db.execute_system(sql, [database])

        # Also get row counts and sizes from information_schema.tables
        stats_sql = (
            "SELECT TABLE_NAME, ENGINE, TABLE_ROWS, DATA_LENGTH, CREATE_TIME "
            "FROM information_schema.tables "
            "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'"
        )
        stats_result = await db.execute_system(stats_sql, [database])
        stats_map: dict[str, dict] = {}
        for row in stats_result["rows"]:
            stats_map[row[0]] = {
                "engine": row[1],
                "row_count": row[2],
                "data_size": row[3],
                "create_time": row[4],
            }

        tables = []
        for row in result["rows"]:
            name = row[0]
            props_raw = row[7]
            props = {}
            if props_raw:
                try:
                    props = json.loads(props_raw) if isinstance(props_raw, str) else props_raw
                except (json.JSONDecodeError, TypeError):
                    pass

            stat = stats_map.get(name, {})
            tables.append({
                "name": name,
                "table_model": row[1],
                "engine": stat.get("engine"),
                "row_count": stat.get("row_count"),
                "data_size": stat.get("data_size"),
                "create_time": stat.get("create_time"),
                "primary_key": row[2],
                "partition_key": None,  # not in tables_config
                "distribute_key": row[3],
                "distribute_type": row[4],
                "distribute_bucket": row[5],
                "sort_key": row[6],
                "properties": props,
            })
        return tables

    async def list_views(self, database: str) -> list[dict]:
        """Views via information_schema.VIEWS."""
        sql = (
            "SELECT TABLE_NAME, DEFINER, IS_UPDATABLE "
            "FROM information_schema.views "
            "WHERE TABLE_SCHEMA = %s"
        )
        result = await db.execute_system(sql, [database])
        return [
            {
                "name": row[0],
                "definer": row[1],
                "is_updatable": row[2],
            }
            for row in result["rows"]
        ]

    async def list_materialized_views(self, database: str) -> list[dict]:
        """MVs via information_schema.materialized_views."""
        sql = (
            "SELECT TABLE_NAME, REFRESH_TYPE, IS_ACTIVE, "
            "LAST_REFRESH_STATE, TABLE_ROWS, QUERY_REWRITE_STATUS "
            "FROM information_schema.materialized_views "
            "WHERE TABLE_SCHEMA = %s"
        )
        result = await db.execute_system(sql, [database])
        return [
            {
                "name": row[0],
                "refresh_type": row[1],
                "is_active": bool(row[2]) if row[2] is not None else None,
                "last_refresh_state": row[3],
                "table_rows": row[4],
                "query_rewrite_status": row[5],
            }
            for row in result["rows"]
        ]

    async def list_functions(self, database: str) -> list[dict]:
        """UDFs via information_schema.routines."""
        sql = (
            "SELECT ROUTINE_NAME, ROUTINE_TYPE, DEFINER, CREATED "
            "FROM information_schema.routines "
            "WHERE ROUTINE_SCHEMA = %s"
        )
        result = await db.execute_system(sql, [database])
        return [
            {
                "name": row[0],
                "routine_type": row[1],
                "definer": row[2],
                "created": row[3],
            }
            for row in result["rows"]
        ]

    async def list_pipes(self, database: str) -> list[dict]:
        """Pipes via information_schema.pipes."""
        sql = (
            "SELECT PIPE_NAME, STATE, TABLE_NAME, LOAD_STATUS, "
            "LAST_ERROR, CREATED_TIME "
            "FROM information_schema.pipes "
            "WHERE DATABASE_NAME = %s"
        )
        result = await db.execute_system(sql, [database])
        return [
            {
                "name": row[0],
                "state": row[1],
                "target_table": row[2],
                "load_status": row[3],
                "last_error": row[4],
                "created_time": row[5],
            }
            for row in result["rows"]
        ]

    async def list_stages(self, database: str) -> list[dict]:
        """Nova stages from CONFIG_STAGES table."""
        sql = (
            "SELECT name, storage_connection, base_prefix, created_at "
            "FROM NOVA_SYSTEM.CONFIG_STAGES "
            "WHERE database_name = %s"
        )
        try:
            result = await db.execute_system(sql, [database])
            return [
                {
                    "name": row[0],
                    "storage_connection": row[1],
                    "prefix": row[2],
                    "created_at": row[3],
                }
                for row in result["rows"]
            ]
        except Exception:
            log.debug("No stages found for database %s", database)
            return []

    # ── Detail views ───────────────────────────────────────────

    async def get_table_detail(self, database: str, table: str) -> dict | None:
        """Full table detail: columns, partitions, properties, DDL."""
        # Columns from information_schema
        cols_sql = (
            "SELECT COLUMN_NAME, ORDINAL_POSITION, DATA_TYPE, COLUMN_TYPE, "
            "IS_NULLABLE, COLUMN_KEY, COLUMN_DEFAULT, EXTRA, COLUMN_COMMENT, "
            "NUMERIC_PRECISION, NUMERIC_SCALE, CHARACTER_MAXIMUM_LENGTH "
            "FROM information_schema.columns "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
            "ORDER BY ORDINAL_POSITION"
        )
        cols_result = await db.execute_system(cols_sql, [database, table])
        if not cols_result["rows"]:
            return None

        columns = [
            {
                "name": r[0],
                "ordinal_position": r[1],
                "data_type": r[2],
                "column_type": r[3],
                "is_nullable": r[4],
                "column_key": r[5],
                "column_default": r[6],
                "extra": r[7],
                "column_comment": r[8],
                "numeric_precision": r[9],
                "numeric_scale": r[10],
                "character_maximum_length": r[11],
            }
            for r in cols_result["rows"]
        ]

        # Properties from tables_config
        props_sql = (
            "SELECT TABLE_MODEL, PRIMARY_KEY, PARTITION_KEY, "
            "DISTRIBUTE_KEY, DISTRIBUTE_TYPE, DISTRIBUTE_BUCKET, "
            "SORT_KEY, PROPERTIES "
            "FROM information_schema.tables_config "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s"
        )
        props_result = await db.execute_system(props_sql, [database, table])
        properties: dict = {}
        if props_result["rows"]:
            r = props_result["rows"][0]
            props_raw = r[7]
            props_dict: dict = {}
            if props_raw:
                try:
                    props_dict = json.loads(props_raw) if isinstance(props_raw, str) else (props_raw or {})
                except (json.JSONDecodeError, TypeError):
                    pass
            properties = {
                "table_model": r[0],
                "primary_key": r[1],
                "partition_key": r[2],
                "distribute_key": r[3],
                "distribute_type": r[4],
                "distribute_bucket": r[5],
                "sort_key": r[6],
                "properties": props_dict,
            }

        # DDL
        create_ddl = None
        try:
            ddl_result = await db.execute_system(
                f"SHOW CREATE TABLE `{database}`.`{table}`"
            )
            create_ddl = ddl_result["rows"][0][1] if ddl_result["rows"] else None
        except Exception:
            pass

        # Partitions via partitions_meta (StarRocks-specific)
        partitions: list[dict] = []
        try:
            part_sql = (
                "SELECT PARTITION_NAME, PARTITION_ID, PARTITION_KEY, "
                "PARTITION_VALUE, ROW_COUNT, DATA_SIZE, STORAGE_SIZE, "
                "BUCKETS, REPLICATION_NUM, VISIBLE_VERSION, DATA_VERSION "
                "FROM information_schema.partitions_meta "
                "WHERE DB_NAME = %s AND TABLE_NAME = %s "
                "ORDER BY PARTITION_ID"
            )
            part_result = await db.execute_system(part_sql, [database, table])
            partitions = [
                {
                    "name": r[0],
                    "partition_id": r[1],
                    "partition_key": r[2],
                    "partition_value": r[3],
                    "row_count": r[4],
                    "data_size": r[5],
                    "storage_size": r[6],
                    "buckets": r[7],
                    "replication_num": r[8],
                    "visible_version": r[9],
                    "data_version": r[10],
                }
                for r in part_result["rows"]
            ]
        except Exception as e:
            log.warning("Failed to fetch partitions for %s.%s: %s", database, table, e)

        # Stats
        row_count = None
        data_size = None
        try:
            stats_sql = (
                "SELECT TABLE_ROWS, DATA_LENGTH "
                "FROM information_schema.tables "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s"
            )
            stats_result = await db.execute_system(stats_sql, [database, table])
            if stats_result["rows"]:
                row_count = stats_result["rows"][0][0]
                data_size = stats_result["rows"][0][1]
        except Exception:
            pass

        return {
            "name": table,
            "database": database,
            "columns": columns,
            "properties": properties,
            "partitions": partitions,
            "row_count": row_count,
            "data_size": data_size,
            "create_ddl": create_ddl,
        }

    async def get_view_detail(self, database: str, view: str) -> dict | None:
        """View detail from information_schema.views + SHOW CREATE VIEW."""
        sql = (
            "SELECT TABLE_NAME, VIEW_DEFINITION, DEFINER, "
            "SECURITY_TYPE, IS_UPDATABLE "
            "FROM information_schema.views "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s"
        )
        result = await db.execute_system(sql, [database, view])
        if not result["rows"]:
            return None
        r = result["rows"][0]
        detail = {
            "name": r[0],
            "database": database,
            "definition": r[1],
            "definer": r[2],
            "security_type": r[3],
            "is_updatable": r[4],
        }

        # DDL
        try:
            ddl_result = await db.execute_system(
                f"SHOW CREATE VIEW `{database}`.`{view}`"
            )
            detail["create_ddl"] = ddl_result["rows"][0][1] if ddl_result["rows"] else None
        except Exception:
            detail["create_ddl"] = None

        return detail

    async def get_mv_detail(self, database: str, mv: str) -> dict | None:
        """MV detail from information_schema.materialized_views."""
        sql = (
            "SELECT TABLE_NAME, MATERIALIZED_VIEW_DEFINITION, REFRESH_TYPE, "
            "IS_ACTIVE, INACTIVE_REASON, TASK_NAME, "
            "LAST_REFRESH_STATE, LAST_REFRESH_ERROR_MESSAGE, "
            "LAST_REFRESH_START_TIME, LAST_REFRESH_FINISHED_TIME, "
            "LAST_REFRESH_DURATION, TABLE_ROWS, QUERY_REWRITE_STATUS, "
            "CREATOR "
            "FROM information_schema.materialized_views "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s"
        )
        result = await db.execute_system(sql, [database, mv])
        if not result["rows"]:
            return None
        r = result["rows"][0]
        return {
            "name": r[0],
            "database": database,
            "definition": r[1],
            "refresh_type": r[2],
            "is_active": bool(r[3]) if r[3] is not None else None,
            "inactive_reason": r[4],
            "task_name": r[5],
            "last_refresh_state": r[6],
            "last_refresh_error": r[7],
            "last_refresh_start_time": r[8],
            "last_refresh_finished_time": r[9],
            "last_refresh_duration": float(r[10]) if r[10] is not None else None,
            "table_rows": r[11],
            "query_rewrite_status": r[12],
            "creator": r[13],
        }

    async def get_function_detail(self, database: str, fn: str) -> dict | None:
        """Function detail from information_schema.routines."""
        sql = (
            "SELECT ROUTINE_NAME, ROUTINE_TYPE, ROUTINE_DEFINITION, "
            "DEFINER, CREATED, LAST_ALTERED, IS_DETERMINISTIC, "
            "SQL_DATA_ACCESS, ROUTINE_COMMENT "
            "FROM information_schema.routines "
            "WHERE ROUTINE_SCHEMA = %s AND ROUTINE_NAME = %s"
        )
        result = await db.execute_system(sql, [database, fn])
        if not result["rows"]:
            return None
        r = result["rows"][0]
        return {
            "name": r[0],
            "database": database,
            "routine_type": r[1],
            "definition": r[2],
            "definer": r[3],
            "created": r[4],
            "last_altered": r[5],
            "is_deterministic": r[6],
            "sql_data_access": r[7],
            "comment": r[8],
        }

    async def get_pipe_detail(self, database: str, pipe: str) -> dict | None:
        """Pipe detail from information_schema.pipes."""
        sql = (
            "SELECT PIPE_ID, PIPE_NAME, STATE, TABLE_NAME, "
            "PROPERTIES, LOAD_STATUS, LAST_ERROR, CREATED_TIME "
            "FROM information_schema.pipes "
            "WHERE DATABASE_NAME = %s AND PIPE_NAME = %s"
        )
        result = await db.execute_system(sql, [database, pipe])
        if not result["rows"]:
            return None
        r = result["rows"][0]
        props_raw = r[4]
        props = {}
        if props_raw:
            try:
                props = json.loads(props_raw) if isinstance(props_raw, str) else props_raw
            except (json.JSONDecodeError, TypeError):
                pass
        return {
            "name": r[1],
            "database": database,
            "pipe_id": r[0],
            "state": r[2],
            "target_table": r[3],
            "properties": props,
            "load_status": r[5],
            "last_error": r[6],
            "created_time": r[7],
        }


explorer_repo = ExplorerRepository()
