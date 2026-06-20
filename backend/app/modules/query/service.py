"""Query service — orchestrates the full SQL execution pipeline.

Pipeline:
1. Parse SQL (detect @stage references)
2. Translate @stage → FILES() (if stage references found)
3. Inject credentials (if FILES() calls present)
4. Execute against StarRocks
5. Return standardized result
"""

from __future__ import annotations

import os
import re

from app.common.audit import write_audit_log
from app.common.sql_guard import guard_sql
from app.common.sql_guard import is_destructive_sql, is_unscoped_mutation, split_sql_statements
from app.core.config import settings
from app.core.config import get_storage_connection, load_nova_app_config, to_docker_endpoint
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
        self._minio_creds: tuple[str, str] | None = None

    def _load_minio_root_creds(self) -> tuple[str, str]:
        """Load MinIO root credentials from docker .env file."""
        if self._minio_creds:
            return self._minio_creds
        from pathlib import Path
        env_path = Path(__file__).resolve().parents[4] / "docker" / ".env"
        user, pw = "minioadmin", "minioadmin"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("MINIO_ROOT_USER="):
                    user = line.split("=", 1)[1].strip()
                elif line.startswith("MINIO_ROOT_PASSWORD="):
                    pw = line.split("=", 1)[1].strip()
        self._minio_creds = (user, pw)
        return self._minio_creds

    def _minio_root_user(self) -> str:
        return self._load_minio_root_creds()[0]

    def _minio_root_password(self) -> str:
        return self._load_minio_root_creds()[1]

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
        csv_column_names: list[str] | None = None

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

            # 3b. CSV auto-detect: read file header to detect delimiter & columns
            csv_params, csv_column_names = await self._detect_csv_params(parsed, stage_configs)
            if csv_params:
                # Inject CSV params into FILES() calls
                import re
                def _inject_csv(m):
                    content = m.group(1)
                    csv_parts = [f"'{k}'='{v}'" for k, v in csv_params.items()]
                    content = f"{content}, {', '.join(csv_parts)}"
                    return f"FILES({content})"
                executed_sql = re.sub(r'FILES\(([^)]+)\)', _inject_csv, executed_sql)

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

            # Rename $1, $2 columns with CSV header names if detected
            if csv_column_names and result.columns:
                for i, col_name in enumerate(csv_column_names):
                    if i < len(result.columns):
                        result.columns[i] = col_name
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

    async def execute_statements(
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
    ) -> list[QueryResult]:
        """Split SQL into statements and execute each sequentially.

        Stops on first error — returns results collected so far plus an error result.
        """
        statements = split_sql_statements(sql)
        if not statements:
            return [QueryResult(original_sql=sql, warnings=["Empty SQL"])]

        results: list[QueryResult] = []
        for stmt_sql in statements:
            try:
                result = await self.execute(
                    sql=stmt_sql,
                    username=username,
                    encrypted_password=encrypted_password,
                    database=database,
                    schema=schema,
                    role=role,
                    max_rows=max_rows,
                    session_id=session_id,
                    confirm_destructive=confirm_destructive,
                    file_id=file_id,
                )
                results.append(result)
            except Exception as exc:
                # Return error result for this statement and stop
                error_result = QueryResult(
                    original_sql=stmt_sql,
                    executed_sql=stmt_sql,
                    warnings=[str(exc)],
                )
                results.append(error_result)
                break
        return results

    async def get_history(
        self,
        *,
        username: str,
        file_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        database_name: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        min_duration_ms: int | None = None,
    ) -> dict:
        """Retrieve query execution history from AUDIT_LOG."""
        where, params = self._build_history_filters(
            username=username,
            file_id=file_id,
            status=status,
            search=search,
            database_name=database_name,
            date_from=date_from,
            date_to=date_to,
            min_duration_ms=min_duration_ms,
        )

        count_result = await db.execute_system(
            f"SELECT COUNT(*) FROM NOVA_SYSTEM.AUDIT_LOG WHERE {where}",
            params,
        )
        total = count_result["rows"][0][0] if count_result["rows"] else 0

        result = await db.execute_system(
            f"""
            SELECT log_id, query_id, event_time, user_name, object_name, action,
                   sql_text, status, duration_ms, rows_affected, error_message,
                   file_id, database_name, schema_name, session_id
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
                "log_id": str(row[0]) if row[0] is not None else "",
                "query_id": row[1] or "",
                "event_time": str(row[2]) if row[2] else "",
                "user_name": row[3] or "",
                "object_name": row[4] or "",
                "action": row[5] or "",
                "sql_text": row[6] or "",
                "status": row[7] or "",
                "duration_ms": row[8],
                "rows_affected": row[9],
                "error_message": row[10],
                "file_id": row[11],
                "database_name": row[12],
                "schema_name": row[13],
                "session_id": row[14],
            })

        return {"items": items, "total": total}

    async def get_history_stats(
        self,
        *,
        username: str,
        file_id: str | None = None,
        status: str | None = None,
        search: str | None = None,
        database_name: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        min_duration_ms: int | None = None,
    ) -> dict:
        """Return aggregate statistics for query execution history."""
        where, params = self._build_history_filters(
            username=username,
            file_id=file_id,
            status=status,
            search=search,
            database_name=database_name,
            date_from=date_from,
            date_to=date_to,
            min_duration_ms=min_duration_ms,
        )

        result = await db.execute_system(
            f"""
            SELECT COUNT(*) AS total,
                   AVG(duration_ms) AS avg_duration_ms,
                   SUM(CASE WHEN status = 'ERROR' THEN 1 ELSE 0 END) AS error_count,
                   SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) AS success_count
            FROM NOVA_SYSTEM.AUDIT_LOG
            WHERE {where}
            """,
            params,
        )

        row = result["rows"][0] if result["rows"] else (0, None, 0, 0)
        total = row[0] or 0
        avg_duration_ms = float(row[1]) if row[1] is not None else None
        error_count = row[2] or 0
        success_count = row[3] or 0
        error_rate = (error_count / total) if total > 0 else 0.0

        return {
            "total": total,
            "avg_duration_ms": avg_duration_ms,
            "error_count": error_count,
            "success_count": success_count,
            "error_rate": error_rate,
        }

    @staticmethod
    def _build_history_filters(
        *,
        username: str,
        file_id: str | None = None,
        status: str | None = None,
        search: str | None = None,
        database_name: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        min_duration_ms: int | None = None,
    ) -> tuple[str, list]:
        """Build shared WHERE clause and params for history queries."""
        conditions = ["event_type = 'query'", "user_name = %s"]
        params: list = [username]

        if file_id:
            conditions.append("file_id = %s")
            params.append(file_id)
        if status:
            conditions.append("status = %s")
            params.append(status.upper())
        if search:
            conditions.append("sql_text LIKE %s")
            params.append(f"%{search}%")
        if database_name:
            conditions.append("database_name = %s")
            params.append(database_name)
        if date_from:
            conditions.append("event_time >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("event_time <= %s")
            params.append(date_to)
        if min_duration_ms is not None:
            conditions.append("duration_ms >= %s")
            params.append(min_duration_ms)

        return " AND ".join(conditions), params

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
        folder: str | None = None,
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
            rows = await self._list_stage_files(stage, database, schema, folder=folder)
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
        First tries to filter by database/schema context.
        Falls back to loading ALL stages if none match (cross-database access).
        """
        try:
            # Try with database/schema filter first
            configs = await self._load_stage_configs_filtered(database, schema)
            if configs:
                return configs
            # Fallback: load all stages (cross-database access)
            return await self._load_stage_configs_filtered(None, None)
        except Exception:
            return {}

    async def _load_stage_configs_filtered(
        self,
        database: str | None,
        schema: str | None,
    ) -> dict[str, StorageConfig]:
        """Load stages with optional database/schema filter."""
        sql = (
            "SELECT name, database_name, schema_name, storage_connection, base_prefix "
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
            name, db_name, schema_name, storage_conn, base_prefix = row[0], row[1], row[2], row[3], row[4]
            conn = get_storage_connection(storage_conn)
            # Fallback base_prefix: {database_name}/{schema_name}/{stage_name}
            resolved_prefix = (base_prefix or "").strip("/")
            if not resolved_prefix:
                resolved_prefix = f"{db_name}/{schema_name}/{name}"
            configs[name] = StorageConfig(
                storage_type=conn.type,
                endpoint=to_docker_endpoint(conn.endpoint),
                bucket=conn.bucket,
                base_prefix=resolved_prefix,
                # Use root credentials for StarRocks FILES() access
                # (service account creds have AWS SDK v2 compatibility issues with MinIO)
                access_key=self._minio_root_user(),
                secret_key=self._minio_root_password(),
                region=conn.region or "us-east-1",
            )
        return configs

    async def _detect_csv_params(
        self,
        parsed,
        stage_configs: dict,
    ) -> tuple[dict[str, str], list[str] | None]:
        """Pre-read CSV file from MinIO to detect delimiter and header.

        Returns (params_dict, column_names_or_None).
        params_dict: FILES() params like {"csv.column_separator": ",", "csv.skip_header": "1"}
        column_names: list of header column names if detected, else None
        """
        if not parsed.stage_refs:
            return {}, None

        ref = parsed.stage_refs[0]
        # Detect format from file extension
        ext = ""
        if ref.file_name and "." in ref.file_name:
            ext = ref.file_name.rsplit(".", 1)[-1].lower()
        if ext not in ("csv", "tsv"):
            return {}, None

        config = stage_configs.get(ref.stage_name)
        if not config:
            return {}, None

        try:
            import boto3
            from botocore.config import Config as BotoConfig

            # Build S3 key
            parts = ref.path_parts + [ref.file_name] if ref.file_name else ref.path_parts
            s3_key = "/".join([config.base_prefix] + parts)

            s3 = boto3.client(
                "s3",
                endpoint_url=settings.S3_ENDPOINT,  # host-side endpoint for boto3
                aws_access_key_id=config.access_key,
                aws_secret_access_key=config.secret_key,
                config=BotoConfig(signature_version="s3v4"),
                region_name=config.region or "us-east-1",
            )

            # Read first 8KB of the file
            resp = s3.get_object(Bucket=config.bucket, Key=s3_key, Range="bytes=0-8191")
            raw = resp["Body"].read().decode("utf-8", errors="replace")
            lines = raw.split("\n")
            if len(lines) < 2:
                return {}, None

            first_line = lines[0].strip()

            # Detect delimiter by counting occurrences in first line
            candidates = [
                (",", first_line.count(",")),
                (";", first_line.count(";")),
                ("\t", first_line.count("\t")),
                ("|", first_line.count("|")),
            ]
            # Pick the delimiter with highest count (must be > 0)
            best_delim, best_count = max(candidates, key=lambda x: x[1])
            if best_count == 0:
                best_delim = ","

            # Detect enclosure
            enclose = ""
            if first_line.startswith('"') and first_line.endswith('"'):
                enclose = '"'

            # Detect if first line is a header:
            # Headers typically contain text, not numbers
            second_line = lines[1].strip() if len(lines) > 1 else ""
            first_fields = first_line.split(best_delim)
            second_fields = second_line.split(best_delim)

            is_header = False
            column_names = None
            if first_fields and second_fields and len(first_fields) == len(second_fields):
                # Check if first row looks like text (header) and second like data
                text_count = sum(1 for f in first_fields if not f.strip().replace("-", "").replace(".", "").isdigit())
                is_header = text_count > len(first_fields) / 2
                if is_header:
                    # Extract clean column names from header
                    column_names = [f.strip().strip('"').strip("'") for f in first_fields]

            params: dict[str, str] = {
                "csv.column_separator": best_delim,
                "csv.trim_space": "true",
            }
            if enclose:
                params["csv.enclose"] = enclose
                params["csv.escape"] = "\\\\"
            if is_header:
                params["csv.skip_header"] = "1"

            return params, column_names

        except Exception:
            # Silently fall back to defaults if detection fails
            return {}, None

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
        folder: str | None = None,
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
        s3_prefix = folder.replace('.', '/') if folder else ""
        files = await stage_service.list_files(result["rows"][0][0], prefix=s3_prefix)
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
