from __future__ import annotations

from collections.abc import Callable

from app.modules.query.repository import QueryRepository, QueryResult
from app.sql_frontend.binding.models import BoundColumn, BoundTable, TableName
from app.sql_frontend.context import ExecutionContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import parse_statement


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _identifier(value: str) -> str:
    if not value or "\x00" in value:
        raise SemanticError("Invalid catalog identifier")
    return "`" + value.replace("`", "``") + "`"


class StarRocksCatalogProvider:
    def __init__(
        self, repository: QueryRepository, context: ExecutionContext, password: Callable[[], str]
    ) -> None:
        self.repository = repository
        self.context = context
        self.password = password

    async def _query(self, sql: str) -> QueryResult:
        context = self.context
        result = await self.repository.execute_as_user(
            sql=sql,
            username=context.username,
            password="" if context.connection is not None else self.password(),
            database=context.database,
            role=context.role,
            connected=context.connection,
        )
        if result.error:
            raise SemanticError("Catalog metadata is unavailable or not accessible")
        return result

    def _database(self, name: TableName) -> str:
        database = name.database or self.context.database
        if not database:
            raise SemanticError("A database is required to bind a table")
        return database

    def _information_schema(self, name: TableName) -> str:
        catalog = _identifier(name.catalog)
        return (
            "information_schema"
            if name.catalog == "default_catalog"
            else catalog + ".information_schema"
        )

    def _qualified(self, name: TableName) -> str:
        return ".".join(
            _identifier(value) for value in (name.catalog, self._database(name), name.table)
        )

    async def resolve_table(self, name: TableName) -> BoundTable | None:
        rows = await self._query(
            f"SELECT TABLE_TYPE FROM {self._information_schema(name)}.tables "
            f"WHERE TABLE_SCHEMA={_literal(self._database(name))} "
            f"AND TABLE_NAME={_literal(name.table)}"
        )
        return BoundTable(name, str(rows.rows[0][0])) if rows.rows else None

    async def get_columns(self, name: TableName) -> tuple[BoundColumn, ...]:
        result = await self._query(
            "SELECT COLUMN_NAME, COLUMN_TYPE, ORDINAL_POSITION, IS_NULLABLE, "
            f"COLUMN_DEFAULT, GENERATION_EXPRESSION FROM {self._information_schema(name)}.columns "
            f"WHERE TABLE_SCHEMA={_literal(self._database(name))} "
            f"AND TABLE_NAME={_literal(name.table)} ORDER BY ORDINAL_POSITION"
        )
        return tuple(
            BoundColumn(
                str(row[0]),
                str(row[1]),
                int(row[2]),
                str(row[3]).upper() == "YES",
                row[4],
                row[5] or None,
            )
            for row in result.rows
        )

    async def get_details(self, name: TableName) -> BoundTable:
        from app.modules.query.dialect.parser import _walk_nodes

        result = await self._query(f"SHOW CREATE TABLE {self._qualified(name)}")
        if not result.rows:
            raise SemanticError("Table does not exist or is not accessible")
        parsed = parse_statement(str(result.rows[0][1]))
        key_type = None
        primary: tuple[str, ...] = ()
        partition = None
        for node in _walk_nodes(parsed.statement_context):
            kind = type(node).__name__
            fragment = (
                parsed.normalized_sql[node.start.start : node.stop.stop + 1]
                if getattr(node, "start", None) and getattr(node, "stop", None)
                else ""
            )
            if kind == "KeyDescContext":
                key_type = node.start.text.upper()
                if key_type == "PRIMARY":
                    primary = tuple(
                        identifier.getText().strip("`")
                        for identifier in node.identifierList().identifier()
                    )
            if kind == "PartitionDescContext":
                partition = fragment
        return BoundTable(
            name,
            "BASE TABLE",
            key_type=key_type,
            primary_key_columns=primary,
            partition_sql=partition,
        )
