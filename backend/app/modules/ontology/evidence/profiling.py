"""Column profiles and value sketches, computed in StarRocks as the caller.

A profile is a handful of numbers per column: rows, distinct values (HLL via
``approx_count_distinct``), non-null values and value lengths. A sketch is the
bottom-k of a 32-bit hash of the column's distinct values (KMV, Beyer et al.
2007): enough to estimate how much of one column's values occur in another
without moving or keeping any value.

Profiles are read with the caller's own session, so Ranger row filters and
masks shape them exactly as they shape the caller's queries. Sketches are
derived from values, so they live only in memory while joins are discovered
and are never stored.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

#: Bottom-k size: a 4% standard error on Jaccard, exact for domains up to k values.
SKETCH_SIZE = 256

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_INTEGER = {"tinyint", "smallint", "int", "integer", "bigint", "largeint"}
_TEXT = {"char", "varchar", "string", "text"}


def quote(name: str) -> str:
    """A safe backquoted identifier; anything unusual is refused, never escaped."""
    if not _IDENTIFIER.match(name):
        raise ValueError(f"Unsupported identifier {name!r}")
    return f"`{name}`"


def type_family(datatype: str) -> str | None:
    """Columns that can hold keys: whole numbers or text. Others are never profiled for joins."""
    base = datatype.lower().split("(")[0].strip()
    if base in _INTEGER:
        return "integer"
    if base in _TEXT:
        return "text"
    return None


@dataclass(frozen=True)
class ColumnProfile:
    database: str
    table: str
    column: str
    datatype: str
    rows: int
    ndv: int
    non_null: int
    min_length: int | None = None
    max_length: int | None = None

    @property
    def key(self) -> str:
        return f"{self.database}.{self.table}.{self.column}"

    @property
    def family(self) -> str | None:
        return type_family(self.datatype)

    @property
    def uniqueness(self) -> float:
        """Distinct values per non-null value: 1.0 for a key."""
        return min(1.0, self.ndv / self.non_null) if self.non_null else 0.0

    @property
    def null_rate(self) -> float:
        return 1 - self.non_null / self.rows if self.rows else 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def columns_sql(database: str) -> str:
    """The caller-visible columns of every table in one database."""
    if not _IDENTIFIER.match(database):
        raise ValueError(f"Unsupported identifier {database!r}")
    return (
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        f"WHERE table_schema = '{database}' ORDER BY table_name, ordinal_position"
    )


def table_profile_sql(database: str, table: str, columns: list[str]) -> str:
    """One scan for every column's counts and length range."""
    parts = ["COUNT(*) AS n"]
    for index, column in enumerate(columns):
        name = quote(column)
        text = f"CAST({name} AS VARCHAR)"
        parts += [
            f"approx_count_distinct({name}) AS d{index}",
            f"COUNT({name}) AS c{index}",
            f"MIN(CHAR_LENGTH({text})) AS l{index}",
            f"MAX(CHAR_LENGTH({text})) AS h{index}",
        ]
    return f"SELECT {', '.join(parts)} FROM {quote(database)}.{quote(table)}"


def parse_table_profile(
    database: str, table: str, columns: list[tuple[str, str]], row: list[Any]
) -> list[ColumnProfile]:
    rows = int(row[0] or 0)
    profiles = []
    for index, (column, datatype) in enumerate(columns):
        base = 1 + index * 4
        ndv, non_null, low, high = row[base:base + 4]
        profiles.append(ColumnProfile(
            database=database, table=table, column=column, datatype=datatype, rows=rows,
            ndv=min(int(ndv or 0), int(non_null or 0)), non_null=int(non_null or 0),
            min_length=None if low is None else int(low),
            max_length=None if high is None else int(high),
        ))
    return profiles


def sketch_sql(profile: ColumnProfile, size: int = SKETCH_SIZE) -> str:
    """The ``size`` smallest hashes of the column's distinct values."""
    name = quote(profile.column)
    return (
        f"SELECT h FROM (SELECT DISTINCT murmur_hash3_32(CAST({name} AS VARCHAR)) AS h "
        f"FROM {quote(profile.database)}.{quote(profile.table)} WHERE {name} IS NOT NULL) s "
        f"ORDER BY h LIMIT {int(size)}"
    )


def uniqueness_sql(profile: ColumnProfile) -> str:
    """Exact non-null and distinct counts, to confirm a key the HLL estimate suggests."""
    name = quote(profile.column)
    return (f"SELECT COUNT({name}), COUNT(DISTINCT {name}) "
            f"FROM {quote(profile.database)}.{quote(profile.table)}")


def inclusion_sql(source: ColumnProfile, target: ColumnProfile) -> str:
    """Exact distinct source values and how many of them the target holds."""
    left, right = quote(source.column), quote(target.column)
    return (
        "SELECT COUNT(*), COUNT(t.v) FROM ("
        f"SELECT DISTINCT CAST({left} AS VARCHAR) AS v FROM "
        f"{quote(source.database)}.{quote(source.table)} WHERE {left} IS NOT NULL) s "
        "LEFT JOIN ("
        f"SELECT DISTINCT CAST({right} AS VARCHAR) AS v FROM "
        f"{quote(target.database)}.{quote(target.table)} WHERE {right} IS NOT NULL) t "
        "ON s.v = t.v"
    )


@dataclass(frozen=True)
class Sketch:
    """Bottom-k hashes of a column's distinct values; complete when the column has < k."""

    hashes: frozenset[int]
    ndv: int
    size: int = SKETCH_SIZE

    @property
    def complete(self) -> bool:
        return len(self.hashes) < self.size


def containment(a: Sketch, b: Sketch) -> float:
    """Estimated share of A's distinct values that also occur in B."""
    if not a.hashes or not b.hashes:
        return 0.0
    if a.complete and b.complete:
        return len(a.hashes & b.hashes) / len(a.hashes)
    k = min(a.size, b.size)
    union = sorted(a.hashes | b.hashes)[:k]
    if not union:
        return 0.0
    both = sum(1 for value in union if value in a.hashes and value in b.hashes)
    jaccard = both / len(union)
    ndv_a, ndv_b = max(a.ndv, len(a.hashes)), max(b.ndv, len(b.hashes))
    shared = jaccard * (ndv_a + ndv_b) / (1 + jaccard)
    return min(1.0, shared / ndv_a) if ndv_a else 0.0
