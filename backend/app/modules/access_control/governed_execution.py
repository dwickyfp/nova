"""One fail-closed entry point for user-owned SQL execution."""

from __future__ import annotations

import asyncmy

from app.modules.query.repository import QueryRepository, QueryResult

from .security_context import SecurityContext


class GovernedExecutionService:
    def __init__(self, repository: QueryRepository | None = None) -> None:
        self._repository = repository or QueryRepository()

    async def execute_sql(
        self,
        security: SecurityContext,
        sql: str,
        *,
        password: str = "",
        connection: asyncmy.Connection | None = None,
        max_rows: int | None = None,
    ) -> QueryResult:
        return await self._repository.execute_as_user(
            sql=sql,
            username=security.principal,
            password=password,
            database=security.database,
            role=security.active_role,
            max_rows=max_rows,
            connected=connection,
        )


governed_execution_service = GovernedExecutionService()
