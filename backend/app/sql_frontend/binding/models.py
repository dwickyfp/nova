from dataclasses import dataclass, field

from app.sql_frontend.binding.types import SqlType, UnknownType, parse_sql_type


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
    nullable: bool | None
    default: str | None = None
    generated_expression: str | None = None
    sql_type: SqlType = field(default_factory=UnknownType)
    auto_increment: bool | None = None
    key_column: bool | None = None
    hidden: bool | None = None
    partition_column: bool | None = None

    def __post_init__(self) -> None:
        if self.sql_type == UnknownType():
            object.__setattr__(self, "sql_type", parse_sql_type(self.data_type))


@dataclass(frozen=True, slots=True)
class BoundTable:
    name: TableName
    table_type: str
    columns: tuple[BoundColumn, ...] = ()
    key_type: str | None = None
    primary_key_columns: tuple[str, ...] = ()
    partition_sql: str | None = None
    partition_columns: tuple[str, ...] | None = None
    schema_version: str | None = None


@dataclass(frozen=True, slots=True)
class BoundOutputColumn:
    name: str
    sql_type: SqlType = field(default_factory=UnknownType)
    nullable: bool | None = None
    source_expression: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class BoundRelation:
    columns: tuple[BoundOutputColumn, ...]
