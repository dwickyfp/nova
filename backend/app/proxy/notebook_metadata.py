"""Catalog answers for the DuckDB MySQL scanner on a notebook connection.

A notebook's DuckDB attaches Nova with the ``mysql`` extension, which reads
the catalog with four fixed ``information_schema`` queries before it scans a
table or runs ``mysql_query``. Ranger governs ``information_schema`` column by
column, so a role that may read its own tables can still be refused the
catalog itself, and every Nova query in its notebook would fail.

Nova's rule for "which databases may this role see" is ``SHOW DATABASES``
(``catalog/access.py``), so the scanner's queries are answered with it:

* schemas: ``SHOW DATABASES`` through the governed path, Nova's hidden
  databases removed, exactly as a MySQL client sees them;
* columns: the scanner's own query first; when the engine refuses it,
  ``SHOW TABLES`` and ``SHOW FULL COLUMNS`` through the governed path, so the
  answer lists only what the role may describe;
* indexes and size: answered locally. StarRocks has no MySQL indexes, and the
  size only feeds DuckDB's ``database_size``.

Only these exact query shapes are recognised; anything else runs unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.proxy.session import unquote_literal

_LITERAL = r"(?P<{name}>'(?:[^'\\]|\\.|'')*')"

_SCHEMATA = re.compile(
    r"^select schema_name from information_schema\.schemata;?$", re.IGNORECASE
)
_COLUMN_LIST = (
    r"c\.column_name, c\.data_type, c\.column_type, c\.column_default, c\.is_nullable, "
    r"c\.numeric_precision, c\.numeric_scale, t\.table_comment, c\.column_comment "
    r"from information_schema\.columns c left join information_schema\.tables t "
    r"on t\.table_catalog = c\.table_catalog and t\.table_schema = c\.table_schema "
    r"and t\.table_name = c\.table_name "
)
_COLUMNS_OF_SCHEMA = re.compile(
    r"^select c\.table_name, " + _COLUMN_LIST
    + r"where c\.table_schema ?= ?" + _LITERAL.format(name="schema")
    + r" order by c\.table_name, c\.ordinal_position;?$",
    re.IGNORECASE,
)
_COLUMNS_OF_TABLE = re.compile(
    r"^select " + _COLUMN_LIST
    + r"where c\.table_schema ?= ?" + _LITERAL.format(name="schema")
    + r" and c\.table_name ?= ?" + _LITERAL.format(name="table")
    + r" order by c\.table_name, c\.ordinal_position;?$",
    re.IGNORECASE,
)
_INDEXES = re.compile(
    r"^select distinct table_name, index_name from information_schema\.statistics "
    r"where table_schema ?= ?" + _LITERAL.format(name="schema") + r";?$",
    re.IGNORECASE,
)
_SIZE = re.compile(
    r"^select sum\(data_length \+ index_length\) from information_schema\.tables "
    r"where table_schema ?= ?" + _LITERAL.format(name="schema") + r";?$",
    re.IGNORECASE,
)

#: Columns of the scanner's column query, in its order (without ``table_name``).
COLUMN_FIELDS = (
    "column_name", "data_type", "column_type", "column_default", "is_nullable",
    "numeric_precision", "numeric_scale", "table_comment", "column_comment",
)
#: Tables described one by one when the engine refuses ``information_schema``.
MAX_DESCRIBED_TABLES = 500


@dataclass(frozen=True)
class ScannerQuery:
    kind: str  # schemas | columns | indexes | size
    schema: str = ""
    table: str | None = None


def _value(match: re.Match[str], name: str) -> str | None:
    return unquote_literal(match.group(name))


def scanner_query(statement: str) -> ScannerQuery | None:
    """The scanner catalog query ``statement`` is, or ``None``."""
    text = " ".join(statement.split())
    if _SCHEMATA.match(text):
        return ScannerQuery("schemas")
    for pattern, kind in ((_COLUMNS_OF_TABLE, "columns"), (_COLUMNS_OF_SCHEMA, "columns"),
                          (_INDEXES, "indexes"), (_SIZE, "size")):
        match = pattern.match(text)
        if match is None:
            continue
        schema = _value(match, "schema")
        table = _value(match, "table") if "table" in match.groupdict() else None
        if schema is None or ("table" in match.groupdict() and table is None):
            return None
        return ScannerQuery(kind, schema=schema, table=table)
    return None


def quote_identifier(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


_PRECISION = re.compile(
    r"^\s*(?P<base>[a-z_0-9]+)\s*(?:\((?P<args>[\d,\s]+)\))?", re.IGNORECASE
)


def column_row(described: dict[str, object]) -> list[object]:
    """One scanner column row from a ``SHOW FULL COLUMNS`` row."""
    column_type = str(described.get("Type") or "").strip()
    match = _PRECISION.match(column_type)
    base = (match.group("base") if match else column_type).strip().lower()
    precision: int | None = None
    scale: int | None = None
    if match and match.group("args") and base in {"decimal", "numeric", "decimalv2",
                                                  "decimal32", "decimal64", "decimal128"}:
        numbers = [int(part) for part in match.group("args").split(",") if part.strip()]
        precision = numbers[0] if numbers else None
        scale = numbers[1] if len(numbers) > 1 else 0
        base = "decimal"
    nullable = str(described.get("Null") or "YES").upper()
    return [
        described.get("Field"),
        base,
        column_type.lower(),
        described.get("Default"),
        "NO" if nullable in {"NO", "FALSE"} else "YES",
        precision,
        scale,
        None,
        described.get("Comment") or None,
    ]


_NAME = r"(?:`(?:[^`]|``)+`|[A-Za-z_][\w$]*)"
_DELETE_ALL = re.compile(
    rf"^\s*DELETE\s+FROM\s+({_NAME}(?:\s*\.\s*{_NAME}){{0,2}})"
    r"(?:\s+WHERE\s+(?:TRUE|1\s*=\s*1))?\s*;?\s*$",
    re.IGNORECASE,
)


def delete_all_as_truncate(statement: str) -> str | None:
    """``TRUNCATE TABLE t`` for DuckDB's unconditional ``DELETE FROM t``, else None.

    DuckDB sends ``TRUNCATE t`` and ``DELETE FROM t [WHERE true]`` to MySQL as a
    ``DELETE`` without a usable condition, which the engine refuses ("Delete must
    specify where clause"). Removing every row is what ``TRUNCATE TABLE`` does;
    it still runs through the governed pipeline as the user.
    """
    match = _DELETE_ALL.match(statement)
    return f"TRUNCATE TABLE {match.group(1)}" if match else None

