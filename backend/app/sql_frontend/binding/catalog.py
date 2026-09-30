from dataclasses import replace
from typing import Protocol

from app.sql_frontend.binding.models import BoundColumn, BoundTable, TableName
from app.sql_frontend.errors import SemanticError


class CatalogProvider(Protocol):
    async def resolve_table(self, name: TableName) -> BoundTable | None: ...
    async def get_columns(self, name: TableName) -> tuple[BoundColumn, ...]: ...
    async def get_details(self, name: TableName) -> BoundTable: ...


class Binder:
    def __init__(self, catalog: CatalogProvider) -> None:
        self.catalog = catalog
        self._tables: dict[TableName, BoundTable] = {}
        self._columns: dict[TableName, tuple[BoundColumn, ...]] = {}
        self._details: dict[TableName, BoundTable] = {}

    async def resolve_table(self, name: TableName) -> BoundTable:
        if name not in self._tables:
            table = await self.catalog.resolve_table(name)
            if table is None:
                raise SemanticError("Table does not exist or is not accessible")
            self._tables[name] = table
        return self._tables[name]

    async def get_columns(self, name: TableName) -> tuple[BoundColumn, ...]:
        await self.resolve_table(name)
        if name not in self._columns:
            self._columns[name] = tuple(
                sorted(await self.catalog.get_columns(name), key=lambda column: column.ordinal)
            )
        return self._columns[name]

    async def get_details(self, name: TableName) -> BoundTable:
        table = await self.resolve_table(name)
        if name not in self._details:
            self._details[name] = replace(
                await self.catalog.get_details(name), table_type=table.table_type
            )
        return self._details[name]

    async def resolve_column(self, name: TableName, column_name: str) -> BoundColumn:
        for column in await self.get_columns(name):
            if column.name == column_name:
                return column
        raise SemanticError("Column does not exist")
