from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from hashlib import sha256

from app.core.exceptions import StarRocksError
from app.modules.query.repository import QueryRepository, QueryResult
from app.sql_frontend.binding.models import BoundColumn, BoundTable, TableName
from app.sql_frontend.binding.types import UnknownType
from app.sql_frontend.context import ExecutionContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import parse_statement


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _identifier(value: str) -> str:
    if not value or "\x00" in value:
        raise SemanticError("Invalid catalog identifier")
    return "`" + value.replace("`", "``") + "`"


def column_projection(columns: tuple[BoundColumn, ...], *, text_limit: int | None = None) -> str:
    if (
        not 1 <= len(columns) <= 512
        or len({column.name.casefold() for column in columns}) != len(columns)
        or text_limit is not None
        and (type(text_limit) is not int or not 1 <= text_limit <= 65536)
    ):
        raise SemanticError("Invalid column projection")
    parts = []
    for column in columns:
        name = _identifier(column.name)
        if text_limit is not None and column.sql_type.kind in {"CHAR", "VARCHAR"}:
            parts.append(f"LEFT({name},{text_limit}) AS {name}")
        else:
            parts.append(name)
    return ",".join(parts)


def source_projection(
    table: BoundTable,
    columns: tuple[str, ...],
    *,
    limit: int,
    offset: int = 0,
    text_limit: int | None = None,
    order_by: tuple[str, ...] = (),
) -> str:
    """Project only catalog-bound columns; names never become SQL fragments."""
    if (
        type(limit) is not int
        or not 0 <= limit <= 1000
        or type(offset) is not int
        or not 0 <= offset <= 10_000
        or not columns
        or len(columns) > 512
        or len(set(columns)) != len(columns)
        or not set(columns) <= {column.name for column in table.columns}
        or not set(order_by) <= set(columns)
        or len(order_by) != len(set(order_by))
        or table.name.database is None
    ):
        raise SemanticError("Invalid source projection")
    qualified = ".".join(
        _identifier(value)
        for value in (
            table.name.catalog,
            table.name.database,
            table.name.table,
        )
    )
    available = {column.name: column for column in table.columns}
    projection = column_projection(
        tuple(available[name] for name in columns), text_limit=text_limit
    )
    order = (
        " ORDER BY " + ",".join(_identifier(name) + " ASC" for name in order_by) if order_by else ""
    )
    return f"SELECT {projection} FROM {qualified}{order} LIMIT {limit}" + (
        f" OFFSET {offset}" if offset else ""
    )


def query_output_schema(result: QueryResult) -> tuple[BoundColumn, ...]:
    """Decode the pinned FE's schema result, refusing incomplete or ambiguous metadata."""
    if (
        result.error
        or result.columns != ["ordinal", "name", "physical_type", "nullable"]
        or not 1 <= len(result.rows) <= 512
    ):
        raise SemanticError("Query output schema is unavailable")
    columns = []
    seen: set[str] = set()
    size = 0
    for index, row in enumerate(result.rows, 1):
        if (
            len(row) != 4
            or any(type(value) is not str for value in row)
            or row[0] != str(index)
            or row[3] not in {"YES", "NO"}
        ):
            raise SemanticError("Invalid query output metadata")
        _, name, physical_type, nullable = row
        if (
            not name
            or len(name) > 256
            or "\x00" in name
            or name.casefold() in seen
            or not physical_type
            or len(physical_type) > 65535
        ):
            raise SemanticError("Query output names must be unique and bounded")
        try:
            size += sum(len(value.encode("utf-8")) for value in row)
        except UnicodeEncodeError:
            raise SemanticError("Invalid query output text") from None
        if size > 1_048_576:
            raise SemanticError("Query output metadata exceeds the byte limit")
        column = BoundColumn(name, physical_type, index, nullable == "YES")
        if isinstance(column.sql_type, UnknownType):
            raise SemanticError("Unsupported query output type")
        columns.append(column)
        seen.add(name.casefold())
    return tuple(columns)


class StarRocksCatalogProvider:
    def __init__(
        self, repository: QueryRepository, context: ExecutionContext, password: Callable[[], str]
    ) -> None:
        self.repository = repository
        self.context = context
        self.password = password

    async def _query(self, sql: str) -> QueryResult:
        context = self.context
        try:
            result = await self.repository.execute_as_user(
                sql=sql,
                username=context.username,
                password="" if context.connection is not None else self.password(),
                database=context.database,
                role=context.role,
                connected=context.connection,
            )
        except StarRocksError as error:
            if error.engine_unavailable:
                raise  # Not a refusal: the caller answers that the engine is unavailable.
            raise SemanticError("Catalog metadata is unavailable or not accessible") from None
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

    async def get_source_identity(self, name: TableName) -> tuple[str, str]:
        """Read the engine object ID under the caller's active role."""
        if name.catalog != "default_catalog" or name.schema != "default":
            raise SemanticError("This source does not provide a verified physical identity")
        result = await self._query(
            "SELECT c.TABLE_ID,t.TABLE_TYPE FROM information_schema.tables_config c "
            "INNER JOIN information_schema.tables t ON c.TABLE_SCHEMA=t.TABLE_SCHEMA "
            "AND c.TABLE_NAME=t.TABLE_NAME "
            f"WHERE c.TABLE_SCHEMA={_literal(self._database(name))} "
            f"AND c.TABLE_NAME={_literal(name.table)}"
        )
        if len(result.rows) != 1 or len(result.rows[0]) != 2:
            raise SemanticError("Source identity is unavailable or not accessible")
        identity, kind = result.rows[0]
        if type(identity) is not int or identity <= 0 or kind not in {"BASE TABLE", "VIEW"}:
            raise SemanticError("This source does not provide a verified physical identity")
        return str(identity), kind

    async def get_source_schema(self, name: TableName) -> BoundTable:
        """Capture catalog schema without sampling rows or rebinding a replaced object.

        This is an observation, not a DDL lock. Consumers must compare the saved
        identity and schema again before execution; a mismatch requires repair.
        """
        resolved = replace(name, database=self._database(name))
        before = await self.get_source_identity(resolved)
        columns = await self.get_columns(resolved)
        after = await self.get_source_identity(resolved)
        if before != after:
            raise SemanticError("Source changed while reading its schema")
        if not columns or any(column.nullable is None for column in columns):
            raise SemanticError("Source column metadata is unavailable or incomplete")
        if [column.ordinal for column in columns] != list(range(1, len(columns) + 1)):
            raise SemanticError("Source column order is unavailable or incomplete")
        if len({column.name.casefold() for column in columns}) != len(columns):
            raise SemanticError("Source columns are ambiguous")
        schema = {
            "catalog": resolved.catalog,
            "database": resolved.database,
            "schema": resolved.schema,
            "table": resolved.table,
            "physical_id": after[0],
            "kind": after[1],
            "columns": [
                (column.name, column.data_type, column.ordinal, column.nullable)
                for column in columns
            ],
        }
        encoded = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
        digest = sha256(encoded).hexdigest()
        return BoundTable(
            resolved, after[1], columns=columns, physical_id=after[0], schema_version=digest
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
                default = node.defaultDesc()
                attributes[node.identifier().getText().strip("`").casefold()] = (
                    any(
                        child.getText().upper() == "AUTO_INCREMENT" for child in node.children or []
                    ),
                    parsed.normalized_sql[
                        generated.expression().start.start : generated.expression().stop.stop + 1
                    ]
                    if (generated := node.generatedColumnDesc()) is not None
                    else None,
                    parsed.normalized_sql[
                        parsed.tokens.get(default.start.tokenIndex + 1).start : default.stop.stop
                        + 1
                    ].strip()
                    if default is not None and default.stop.tokenIndex > default.start.tokenIndex
                    else None,
                )
        columns = tuple(
            replace(
                column,
                auto_increment=attributes.get(column.name.casefold(), (None, None, None))[0],
                generated_expression=attributes.get(column.name.casefold(), (None, None, None))[1]
                or column.generated_expression,
                default_sql=attributes.get(column.name.casefold(), (None, None, None))[2],
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
