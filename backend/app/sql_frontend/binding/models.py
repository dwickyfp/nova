from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TableName:
    table: str
    database: str | None = None
    schema: str = "default"
    catalog: str = "default_catalog"


@dataclass(frozen=True, slots=True)
class BoundColumn:
    name: str
    data_type: str
    ordinal: int
    nullable: bool
    default: str | None = None
    generated_expression: str | None = None


@dataclass(frozen=True, slots=True)
class BoundTable:
    name: TableName
    table_type: str
    columns: tuple[BoundColumn, ...] = ()
    key_type: str | None = None
    primary_key_columns: tuple[str, ...] = ()
    partition_sql: str | None = None
