"""Query service — orchestrates the full SQL execution pipeline.

Pipeline:
1. Parse SQL (detect @stage references)
2. Translate @stage → FILES() (if stage references found)
3. Inject credentials (if FILES() calls present)
4. Execute against StarRocks
5. Return standardized result
"""

from app.common.sql_guard import guard_sql
from app.core.config import settings
from app.core.database import db
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
        # 1. Guard: block dangerous SQL
        guard_sql(sql)

        # 2. Parse: detect @stage references
        parsed = parse_sql(sql)

        executed_sql = sql
        warnings = []

        # 3. Translate @stage → FILES() if needed
        if parsed.stage_refs:
            # Load stage configs from NOVA_SYSTEM
            stage_configs = await self._load_stage_configs()

            try:
                executed_sql, warnings = translate_stage_query(
                    parsed, stage_configs
                )
            except ValueError as e:
                return QueryResult(
                    original_sql=sql,
                    executed_sql=sql,
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
        result = await self._repo.execute_as_user(
            sql=executed_sql,
            username=username,
            password=password,
            database=database,
        )

        result.original_sql = sql
        result.executed_sql = executed_sql
        result.warnings = warnings

        return result

    async def explain(
        self,
        sql: str,
        username: str,
        encrypted_password: str,
        database: str | None = None,
    ) -> QueryResult:
        """Get EXPLAIN plan for a SQL statement.

        Translates @stage references first, then runs EXPLAIN.
        """
        guard_sql(sql)

        parsed = parse_sql(sql)
        executed_sql = sql

        if parsed.stage_refs:
            stage_configs = await self._load_stage_configs()
            try:
                executed_sql, _ = translate_stage_query(parsed, stage_configs)
            except ValueError as e:
                return QueryResult(
                    original_sql=sql,
                    executed_sql=sql,
                    warnings=[f"❌ {e}"],
                )

        explain_sql = f"EXPLAIN {executed_sql}"
        password = decrypt_password(encrypted_password)

        return await self._repo.execute_as_user(
            sql=explain_sql,
            username=username,
            password=password,
            database=database,
        )

    async def _load_stage_configs(self) -> dict[str, StorageConfig]:
        """Load stage configurations from NOVA_SYSTEM.

        Returns a map of stage_name → StorageConfig.
        """
        try:
            result = await db.execute_system(
                "SELECT name, storage_connection, base_prefix "
                "FROM NOVA_SYSTEM.CONFIG_STAGES"
            )
            configs = {}
            for row in result["rows"]:
                name, storage_conn, base_prefix = row[0], row[1], row[2]
                # Load storage connection details from config
                # For now, use the global S3 settings
                configs[name] = StorageConfig(
                    storage_type="s3",
                    endpoint=settings.S3_ENDPOINT,
                    bucket="nova-stages",
                    base_prefix=base_prefix,
                    access_key=settings.S3_ACCESS_KEY,
                    secret_key=settings.S3_SECRET_KEY,
                )
            return configs
        except Exception:
            return {}


query_service = QueryService()
