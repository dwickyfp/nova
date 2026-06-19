"""Query service — orchestrates the full SQL execution pipeline.

Pipeline:
1. Parse SQL (detect @stage references)
2. Translate @stage → FILES() (if stage references found)
3. Inject credentials (if FILES() calls present)
4. Execute against StarRocks
5. Return standardized result
"""

from __future__ import annotations

import re

from app.common.audit import write_audit_log
from app.common.sql_guard import guard_sql
from app.common.sql_guard import is_destructive_sql, is_unscoped_mutation
from app.core.config import settings
from app.core.config import get_storage_connection, load_nova_app_config
from app.core.database import db
from app.core.exceptions import ForbiddenSQLError
from app.core.security import decrypt_password
from app.modules.query.dialect.injector import get_credential_params
from app.modules.query.dialect.parser import CommandType, parse_sql
from app.modules.query.dialect.translator import (
    StorageConfig,
    translate_stage_query,
)
from app.modules.query.repository import QueryRepository, QueryResult


class QueryService:
    """Orchestrates SQL execution with @stage dialect support."""

    def __init__(self):
        self._repo = QueryRepository()

    async def execute(
        self,
        sql: str,
        username: str,
        encrypted_password: str,
        database: str | None = None,
        schema: str | None = None,
        role: str | None = None,
        max_rows: int | None = None,
        session_id: str | None = None,
        confirm_destructive: bool = False,
        file_id: str | None = None,
    ) -> QueryResult:
        """Execute SQL with full @stage dialect pipeline.

        Args:
            sql: The SQL statement to execute
            username: Authenticated username
            encrypted_password: Fernet-encrypted DB password from session
            database: Optional database context
            schema: Optional schema context

        Returns:
            QueryResult with columns, rows, metadata
        """
        # Normalize Nova's editor-friendly db.default.table notation to the
        # StarRocks-compatible db.table form before validation/execution.
        normalized_sql = self._normalize_default_schema_qualification(sql)

        # 1. Guard: block dangerous SQL
        guard_sql(normalized_sql)
        if is_destructive_sql(normalized_sql) and not confirm_destructive:
            raise ForbiddenSQLError(
                "Destructive SQL requires confirmation before execution."
            )

        # 2. Parse: detect @stage references
        parsed = parse_sql(normalized_sql)

        executed_sql = normalized_sql
        warnings = []

        # 3. Translate @stage → FILES() if needed
        if parsed.stage_refs:
            # Load stage configs from NOVA_SYSTEM
            stage_configs = await self._load_stage_configs(database, schema)

            try:
                executed_sql, warnings = translate_stage_query(
                    parsed, stage_configs
                )
            except ValueError as e:
                return QueryResult(
                    original_sql=sql,
                    executed_sql=normalized_sql,
                    warnings=[f"❌ {e}"],
                )

            # 4. Inject credentials into FILES() calls
            creds = get_credential_params("s3")
            if creds:
                cred_parts = [f"'{k}'='{v}'" for k, v in creds.items()]
                cred_str = ", ".join(cred_parts)
                # Inject into any FILES() call that doesn't have credentials
                if "aws.s3.access_key" not in executed_sql:
                    import re
                    def _inject(m):
                        content = m.group(1)
                        if "access_key" not in content:
                            content = f"{content}, {cred_str}"
                        return f"FILES({content})"
                    executed_sql = re.sub(r'FILES\(([^)]+)\)', _inject, executed_sql)

        # 5. Execute
        password = decrypt_password(encrypted_password)
        try:
            result = await self._repo.execute_as_user(
                sql=executed_sql,
                username=username,
                password=password,
                database=database,
                role=role,
                max_rows=max_rows,
            )
            result.original_sql = sql
            result.executed_sql = executed_sql
            result.warnings = warnings
            await write_audit_log(
                event_type="query",
                user_name=username,
                action="execute",
                object_type="sql",
                object_name=(database or "") if database else "workspace",
                status="SUCCESS",
                sql_text=sql,
                rewritten_sql=executed_sql,
                duration_ms=int(result.elapsed_ms),
                rows_affected=result.affected_rows or result.row_count,
                session_id=session_id,
                file_id=file_id,
                database_name=database,
                schema_name=schema,
            )
            return result
        except Exception as exc:
            await write_audit_log(
                event_type="query",
                user_name=username,
                action="execute",
                object_type="sql",
                object_name=(database or "") if database else "workspace",
                status="ERROR",
                sql_text=sql,
                rewritten_sql=executed_sql,
                error_message=str(exc),
                session_id=session_id,
                file_id=file_id,
                database_name=database,
                schema_name=schema,
            )
            raise

    async def get_history(
        self,
        *,
        username: str,
        file_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Retrieve query execution history from AUDIT_LOG."""
        conditions = ["event_type = 'query'", "user_name = %s"]
        params: list = [username]

        if file_id:
            conditions.append("file_id = %s")
            params.append(file_id)
        if status:
            conditions.append("status = %s")
            params.append(status.upper())

        where = " AND ".join(conditions)

        count_result = await db.execute_system(
            f"SELECT COUNT(*) FROM NOVA_SYSTEM.AUDIT_LOG WHERE {where}",
            params,
        )
        total = count_result["rows"][0][0] if count_result["rows"] else 0

        result = await db.execute_system(
            f"""
            SELECT query_id, event_time, sql_text, status, duration_ms,
                   rows_affected, error_message, file_id, database_name, schema_name
            FROM NOVA_SYSTEM.AUDIT_LOG
            WHERE {where}
            ORDER BY event_time DESC
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )

        items = []
        for row in result["rows"]:
            items.append({
                "query_id": row[0] or "",
                "event_time": str(row[1]) if row[1] else "",
                "sql_text": row[2] or "",
                "status": row[3] or "",
                "duration_ms": row[4],
                "rows_affected": row[5],
                "error_message": row[6],
                "file_id": row[7],
                "database_name": row[8],
                "schema_name": row[9],
            })

        return {"items": items, "total": total}

    async def explain(
        self,
        sql: str,
        username: str,
        encrypted_password: str,
        database: str | None = None,
        role: str | None = None,
    ) -> QueryResult:
        """Get EXPLAIN plan for a SQL statement.

        Translates @stage references first, then runs EXPLAIN.
        """
        normalized_sql = self._normalize_default_schema_qualification(sql)
        guard_sql(normalized_sql)

        parsed = parse_sql(normalized_sql)
        executed_sql = normalized_sql

        if parsed.stage_refs:
            stage_configs = await self._load_stage_configs(database, None)
            try:
                executed_sql, _ = translate_stage_query(parsed, stage_configs)
            except ValueError as e:
                return QueryResult(
                    original_sql=sql,
                    executed_sql=normalized_sql,
                    warnings=[f"❌ {e}"],
                )

        explain_sql = f"EXPLAIN {executed_sql}"
        password = decrypt_password(encrypted_password)

        return await self._repo.execute_as_user(
            sql=explain_sql,
            username=username,
            password=password,
            database=database,
            role=role,
        )

    async def get_context(
        self,
        username: str,
        encrypted_password: str,
    ) -> dict:
        password = decrypt_password(encrypted_password)
        databases = await self._list_user_databases(username, password)
        prefs = await db.execute_system(
            """
            SELECT pref_key, pref_value
            FROM NOVA_SYSTEM.CONFIG_USER_PREFERENCES
            WHERE user_name = %s AND pref_key IN
            ('workspace.last_database', 'workspace.last_schema', 'workspace.last_role')
            """,
            [username],
        )
        pref_map = {row[0]: row[1] for row in prefs["rows"]}
        roles = await self._get_roles(username, password)
        default_db = pref_map.get("workspace.last_database") or (databases[0] if databases else None)
        schemas = await self.list_schemas(database=default_db)
        return {
            "roles": roles,
            "databases": databases,
            "schemas": schemas,
            "defaults": {
                "database": default_db,
                "schema": pref_map.get("workspace.last_schema") or (schemas[0] if schemas else None),
                "role": pref_map.get("workspace.last_role") or (roles[0] if roles else None),
            },
        }

    async def get_completions(
        self,
        *,
        username: str,
        encrypted_password: str,
        kind: str,
        prefix: str = "",
        database: str | None = None,
        schema: str | None = None,
        role: str | None = None,
        table: str | None = None,
        stage: str | None = None,
    ) -> dict:
        password = decrypt_password(encrypted_password)
        if kind == "role":
            items = await self._get_roles(username, password)
            return {"items": self._filter_strings(items, prefix, "role")}
        if kind == "database":
            items = await self._list_user_databases(username, password, role)
            return {"items": self._filter_strings(items, prefix, "database")}
        if kind == "schema":
            items = await self.list_schemas(database)
            return {"items": self._filter_strings(items, prefix, "schema")}
        if kind == "column" and table and database:
            columns = await self._list_columns(username, password, database, table, role)
            return {"items": self._filter_strings(columns, prefix, "column")}
        if kind == "stage":
            stages = await self._list_stages(database, schema)
            return {"items": self._filter_strings(stages, prefix, "stage")}
        if kind == "stage_file" and stage:
            rows = await self._list_stage_files(stage, database, schema)
            return {"items": self._filter_strings(rows, prefix, "stage_file")}

        objects = await self._list_objects(username, password, database, role)
        return {"items": self._filter_strings(objects, prefix, "object")}

    async def list_schemas(self, database: str | None) -> list[str]:
        if not database:
            return ["default"]
        result = await db.execute_system(
            """
            SELECT DISTINCT schema_name
            FROM NOVA_SYSTEM.CONFIG_STAGES
            WHERE database_name = %s
            ORDER BY schema_name
            """,
            [database],
        )
        schemas = [row[0] for row in result["rows"] if row[0]]
        return schemas or ["default"]

    async def _load_stage_configs(
        self,
        database: str | None,
        schema: str | None,
    ) -> dict[str, StorageConfig]:
        """Load stage configurations from NOVA_SYSTEM.

        Returns a map of stage_name → StorageConfig.
        """
        try:
            sql = (
                "SELECT name, storage_connection, base_prefix "
                "FROM NOVA_SYSTEM.CONFIG_STAGES"
            )
            params: list[str] = []
            filters = []
            if database:
                filters.append("database_name = %s")
                params.append(database)
            if schema:
                filters.append("schema_name = %s")
                params.append(schema)
            if filters:
                sql += " WHERE " + " AND ".join(filters)
            result = await db.execute_system(sql, params or None)
            configs = {}
            for row in result["rows"]:
                name, storage_conn, base_prefix = row[0], row[1], row[2]
                conn = get_storage_connection(storage_conn)
                configs[name] = StorageConfig(
                    storage_type=conn.type,
                    endpoint=conn.endpoint,
                    bucket=conn.bucket,
                    base_prefix=base_prefix,
                    access_key=conn.access_key,
                    secret_key=conn.secret_key,
                    region=conn.region or "us-east-1",
                )
            return configs
        except Exception:
            return {}

    async def _list_user_databases(
        self,
        username: str,
        password: str,
        role: str | None = None,
    ) -> list[str]:
        result = await self._repo.execute_as_user(
            sql="SHOW DATABASES",
            username=username,
            password=password,
            role=role,
        )
        return [
            row[0] for row in result.rows
            if row and row[0] not in ("_statistics_", "information_schema", "sys")
        ]

    async def _get_roles(self, username: str, password: str) -> list[str]:
        from app.modules.auth.service import auth_service

        return await auth_service.get_user_roles(username, password)

    async def _list_objects(
        self,
        username: str,
        password: str,
        database: str | None,
        role: str | None,
    ) -> list[str]:
        if not database:
            return []
        tables = await self._repo.execute_as_user(
            sql=f"SHOW TABLES FROM `{database}`",
            username=username,
            password=password,
            role=role,
            database=database,
        )
        return [row[0] for row in tables.rows]

    async def _list_columns(
        self,
        username: str,
        password: str,
        database: str,
        table: str,
        role: str | None,
    ) -> list[str]:
        result = await self._repo.execute_as_user(
            sql=f"DESC `{database}`.`{table}`",
            username=username,
            password=password,
            role=role,
            database=database,
        )
        return [row[0] for row in result.rows]

    async def _list_stages(
        self,
        database: str | None,
        schema: str | None,
    ) -> list[str]:
        sql = "SELECT name FROM NOVA_SYSTEM.CONFIG_STAGES"
        params: list[str] = []
        clauses = []
        if database:
            clauses.append("database_name = %s")
            params.append(database)
        if schema:
            clauses.append("schema_name = %s")
            params.append(schema)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        result = await db.execute_system(sql, params or None)
        return [row[0] for row in result["rows"]]

    async def _list_stage_files(
        self,
        stage_name: str,
        database: str | None,
        schema: str | None,
    ) -> list[str]:
        from app.modules.stages.service import stage_service

        result = await db.execute_system(
            """
            SELECT id
            FROM NOVA_SYSTEM.CONFIG_STAGES
            WHERE name = %s
              AND (%s IS NULL OR database_name = %s)
              AND (%s IS NULL OR schema_name = %s)
            LIMIT 1
            """,
            [stage_name, database, database, schema, schema],
        )
        if not result["rows"]:
            return []
        files = await stage_service.list_files(result["rows"][0][0], prefix="")
        return [file["name"] for file in files]

    @staticmethod
    def _filter_strings(items: list[str], prefix: str, item_type: str) -> list[dict]:
        lowered = prefix.lower()
        filtered = [item for item in items if item.lower().startswith(lowered)]
        return [{"label": item, "type": item_type} for item in filtered[:50]]

    @staticmethod
    def _normalize_default_schema_qualification(sql: str) -> str:
        # Nova's workspace UI can expose database.default.table as a friendly,
        # future-proof path shape. StarRocks table references are database.table,
        # so collapse the middle `.default.` segment before execution.
        return re.sub(
            r"(?<!@)\b(`?[A-Za-z_][\w$]*`?)\.(`?default`?)\.(`?[A-Za-z_][\w$]*`?)\b",
            r"\1.\3",
            sql,
            flags=re.IGNORECASE,
        )


query_service = QueryService()
