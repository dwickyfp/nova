"""Data movement for migrations (11-C) — pure SQL builders + copy plan.

The move is storage-agnostic and uses the **stage** abstraction Nova already has
(``@stage`` → FILES()):

1. **Export** runs on the *source* cluster: ``INSERT INTO FILES(...)`` writes the
   table as Parquet to a shared stage path.
2. **Import** runs on the *target*: ``INSERT INTO target.t SELECT ... FROM
   FILES(...)`` reads that path back.
3. **Verify** compares a row count and an order-independent digest on both sides.

Both clusters must reach the same object storage. That is the one operator
precondition; Nova does not invent a path between two isolated clusters.

This module is **pure**: it builds and describes SQL, executes nothing. The
service runs the statements through the query pipeline (target) and the source
connection (export). Keeping the SQL construction pure means the chunking,
column-listing and digest logic are unit-testable without an engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.modules.migration.chunk_planner import ChunkStrategy, TableChunk

#: A bare identifier, used to validate column names read from the source.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

#: A date/datetime literal the planner may emit. Bounds come from engine
#: statistics, but they are validated anyway: a bound is interpolated into SQL,
#: so only a recognisable literal is allowed through.
_DATE_LITERAL = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}:\d{2}(\.\d+)?)?$")

#: Numeric types StarRocks can SUM for the digest. A column outside this set is
#: skipped by the digest rather than cast (a cast could change the value or fail
#: on a string). The row count still covers every table.
_NUMERIC_TYPE_PREFIXES: tuple[str, ...] = (
    "tinyint",
    "smallint",
    "int",
    "integer",
    "bigint",
    "largeint",
    "float",
    "double",
    "decimal",
    "numeric",
)


class DataMovementError(ValueError):
    """A data-movement request that cannot be built safely."""


def quote(identifier: str) -> str:
    """Backtick-quote a validated identifier."""
    if not _IDENTIFIER.match(identifier or ""):
        raise DataMovementError(f"Invalid identifier: {identifier!r}")
    return f"`{identifier}`"


def is_numeric_type(data_type: str) -> bool:
    """Whether a StarRocks column type can feed the SUM digest."""
    normalized = (data_type or "").strip().lower()
    return normalized.startswith(_NUMERIC_TYPE_PREFIXES)


def _sql_literal(value: object) -> str:
    """Render a chunk bound as a safe SQL literal.

    Numbers pass through; a date/datetime-shaped string is quoted; anything else
    is refused, because the bound is interpolated into a predicate and a value
    that is not a known literal shape is a bug, not a query to guess at.
    """
    if isinstance(value, bool):
        raise DataMovementError("Boolean chunk bound is not supported")
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    if _DATE_LITERAL.match(text):
        return f"'{text}'"
    raise DataMovementError(f"Unsafe chunk bound: {value!r}")


def chunk_predicate(chunk: TableChunk, key_column: str) -> str:
    """The ``WHERE`` predicate selecting one chunk's rows.

    Only ``KEY_RANGE`` produces a predicate, because it is the only strategy
    whose bound is a column expression; ``PARTITION`` selection is done by the
    engine at read time and ``LIMIT``/``SINGLE`` carry no filter. The key column
    is validated through :func:`quote`, so a chunk cannot inject an identifier.

    Bounds are half-open: ``lower`` inclusive, ``upper`` exclusive, matching
    :class:`~app.modules.migration.chunk_planner.TableChunk`. A ``None`` bound is
    unbounded on that side.
    """
    if chunk.strategy is not ChunkStrategy.KEY_RANGE:
        return ""
    column = quote(key_column)
    parts: list[str] = []
    if chunk.lower is not None:
        operator = ">=" if chunk.lower_inclusive else ">"
        parts.append(f"{column} {operator} {_sql_literal(chunk.lower)}")
    if chunk.upper is not None:
        operator = "<=" if chunk.upper_inclusive else "<"
        parts.append(f"{column} {operator} {_sql_literal(chunk.upper)}")
    return " AND ".join(parts)


@dataclass(frozen=True)
class CopyColumn:
    """One column selected for a table copy."""

    name: str
    data_type: str


@dataclass(frozen=True)
class TableCopyPlan:
    """The export / import / verify SQL for one table."""

    database: str
    table: str
    stage_path: str
    columns: tuple[CopyColumn, ...]
    export_sql: str
    import_sql: str
    count_sql_target: str
    count_sql_source: str
    digest_sql_target: str | None
    digest_sql_source: str | None

    @property
    def copyable_columns(self) -> tuple[str, ...]:
        return tuple(col.name for col in self.columns)


def _column_list_sql(columns: tuple[CopyColumn, ...]) -> str:
    return ", ".join(quote(col.name) for col in columns)


def _digest_expression(columns: tuple[CopyColumn, ...]) -> str | None:
    """A deterministic, order-independent digest over numeric columns.

    ``SUM(CAST(col AS DOUBLE))`` per numeric column, COALESCEd to 0 so a nullable
    column does not null the whole digest. Returns ``None`` when no column is
    numeric — the count is then the only check, which the caller reports.
    """
    parts = [
        f"COALESCE(SUM(CAST({quote(col.name)} AS DOUBLE)), 0)"
        for col in columns
        if is_numeric_type(col.data_type)
    ]
    if not parts:
        return None
    return " + ".join(parts)


def build_table_copy(
    *,
    source_database: str,
    target_database: str,
    table: str,
    columns: tuple[CopyColumn, ...],
    stage_path: str,
    files_credential_sql: str = "",
) -> TableCopyPlan:
    """Build the export/import/verify SQL for one table.

    ``stage_path`` is the object-storage prefix the pass writes to and reads
    from (shared by both clusters). ``files_credential_sql`` is the
    already-composed ``'key'='value', ...`` credential fragment from
    ``get_credential_params`` — passed in so this module performs no I/O and
    never touches a credential itself.
    """
    if not columns:
        raise DataMovementError(f"Table '{table}' has no copyable columns")
    for col in columns:
        quote(col.name)  # validates every name before it reaches SQL

    src = f"{quote(source_database)}.{quote(table)}"
    dst = f"{quote(target_database)}.{quote(table)}"
    dir_path = stage_path.rstrip("/") + "/"
    # StarRocks FILES() does not expand a bare directory; the read needs a glob.
    # The export writes multiple part files into the directory, so import reads
    # them all with ``/*.parquet``.
    read_path = dir_path + "*.parquet"
    creds = (", " + files_credential_sql) if files_credential_sql else ""
    cols = _column_list_sql(columns)

    export_sql = (
        f"INSERT INTO FILES('path'='{dir_path}','format'='parquet','compression'='snappy'"
        f"{creds})\nSELECT {cols} FROM {src}"
    )
    import_sql = (
        f"INSERT INTO {dst}\n"
        f"SELECT {cols} FROM FILES('path'='{read_path}','format'='parquet'{creds})"
    )
    count_sql_target = f"SELECT COUNT(*) FROM {dst}"
    count_sql_source = f"SELECT COUNT(*) FROM {src}"

    digest_expr = _digest_expression(columns)
    digest_sql_target = f"SELECT {digest_expr} FROM {dst}" if digest_expr else None
    digest_sql_source = f"SELECT {digest_expr} FROM {src}" if digest_expr else None

    return TableCopyPlan(
        database=source_database,
        table=table,
        stage_path=dir_path,
        columns=columns,
        export_sql=export_sql,
        import_sql=import_sql,
        count_sql_target=count_sql_target,
        count_sql_source=count_sql_source,
        digest_sql_target=digest_sql_target,
        digest_sql_source=digest_sql_source,
    )


def stage_path_for(stage_root: str, migration_id: str, database: str, table: str) -> str:
    """A deterministic, collision-free stage prefix for one table's copy.

    The migration id scopes the run so two concurrent migrations of the same
    table do not overwrite each other's files.
    """
    for part in (migration_id, database, table):
        if not part or not re.fullmatch(r"[A-Za-z0-9_\-]+", part):
            raise DataMovementError(f"Unsafe stage path segment: {part!r}")
    return f"{stage_root.rstrip('/')}/{migration_id}/{database}/{table}"


@dataclass(frozen=True)
class CopyResult:
    """The outcome of one table's copy."""

    table: str
    rows_exported: int
    rows_imported: int
    verified: bool
    digest_match: bool | None
    note: str = ""
    errors: tuple[str, ...] = field(default_factory=tuple)
