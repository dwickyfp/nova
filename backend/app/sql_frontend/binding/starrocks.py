from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

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
                {"YES": True, "NO": False}.get(str(row[3]).upper()),
                row[4],
                row[5] or None,
            )
            for row in result.rows
        )

    async def get_details(self, name: TableName) -> BoundTable:
        from app.sql_frontend.antlr_utils import walk_nodes as _walk_nodes

        result = await self._query(f"SHOW CREATE TABLE {self._qualified(name)}")
        if not result.rows:
            raise SemanticError("Table does not exist or is not accessible")
        parsed = parse_statement(str(result.rows[0][1]))
        key_type = None
        primary: tuple[str, ...] = ()
        partition = None
        key_columns = None
        partition_columns: tuple[str, ...] | None = ()
        attributes = {}
        for node in _walk_nodes(parsed.statement_context):
            kind = type(node).__name__
            fragment = (
                parsed.normalized_sql[node.start.start : node.stop.stop + 1]
                if getattr(node, "start", None) and getattr(node, "stop", None)
                else ""
            )
            if kind == "KeyDescContext":
                key_type = node.start.text.upper()
                key_columns = tuple(
                    item.getText().strip("`") for item in node.identifierList().identifier()
                )
                if key_type == "PRIMARY":
                    primary = tuple(
                        identifier.getText().strip("`")
                        for identifier in node.identifierList().identifier()
                    )
            if kind == "PartitionDescContext":
                partition = fragment
                identifiers = [
                    child
                    for child in _walk_nodes(node)
                    if type(child).__name__ == "IdentifierListContext"
                ]
                partition_columns = (
                    tuple(
                        item.getText().strip("`")
                        for child in identifiers
                        for item in child.identifier()
                    )
                    or None
                )
            if kind == "ColumnDescContext":
                attributes[node.identifier().getText().strip("`").casefold()] = (
                    any(
                        child.getText().upper() == "AUTO_INCREMENT" for child in node.children or []
                    ),
                    node.generatedColumnDesc().getText() if node.generatedColumnDesc() else None,
                )
        columns = tuple(
            replace(
                column,
                auto_increment=attributes.get(column.name.casefold(), (None, None))[0],
                generated_expression=attributes.get(column.name.casefold(), (None, None))[1]
                or column.generated_expression,
                key_column=column.name.casefold() in {key.casefold() for key in key_columns}
                if key_columns is not None
                else None,
                partition_column=column.name.casefold()
                in {key.casefold() for key in partition_columns}
                if partition_columns is not None
                else None,
            )
            for column in await self.get_columns(name)
        )
        return BoundTable(
            name,
            "BASE TABLE",
            columns=columns,
            key_type=key_type,
            primary_key_columns=primary,
            partition_sql=partition,
            partition_columns=partition_columns,
        )
