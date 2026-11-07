"""Column equalities between two named tables in one statement, from the central parse tree.

``FROM a JOIN b ON a.x = b.y`` and the implicit ``FROM a, b WHERE a.x = b.y``
both say that people join ``a.x`` with ``b.y``. Join discovery counts these in
query history as evidence next to the data itself. Only comparisons whose two
sides are qualified columns of two different named tables count; a side that
names a subquery, a CTE or an expression does not.

This is analysis only: it never binds names against the engine, executes, or
reads metadata. A statement that does not parse has no joins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.sql_frontend.antlr_utils import walk_nodes
from app.sql_frontend.binding.relations import identifier, table_name
from app.sql_frontend.errors import SQLFrontendError
from app.sql_frontend.parser import parse_statement


@dataclass(frozen=True, slots=True)
class JoinPair:
    """``left_table.left_column = right_table.right_column``, tables as ``database.table``."""

    left_table: str
    left_column: str
    right_table: str
    right_column: str

    def ordered(self) -> tuple[str, str]:
        """Both sides as ``database.table.column``, sorted so either order counts once."""
        left = f"{self.left_table}.{self.left_column}"
        right = f"{self.right_table}.{self.right_column}"
        return (left, right) if left <= right else (right, left)


def _column(node: Any) -> tuple[str, str] | None:
    """``(qualifier, column)`` of a bare ``q.column`` side, or None for anything else."""
    current = node
    while True:
        names = type(current).__name__
        if names == "DereferenceContext":
            return identifier(current.base), identifier(current.fieldName)
        children = list(current.getChildren()) if hasattr(current, "getChildren") else []
        if len(children) != 1:
            return None
        current = children[0]


def join_pairs(sql: str, *, database: str | None = None) -> list[JoinPair]:
    """Every equality of two qualified columns from two different named tables."""
    try:
        parsed = parse_statement(sql)
    except SQLFrontendError:
        return []
    nodes = list(walk_nodes(parsed.statement_context))
    tables: dict[str, str] = {}
    for node in nodes:
        if type(node).__name__ != "TableAtomContext":
            continue
        try:
            name = table_name(node.qualifiedName(), database)
        except SQLFrontendError:
            continue
        if not name.database:
            continue
        full = f"{name.database}.{name.table}"
        tables[name.table.lower()] = full
        if node.alias is not None:
            tables[identifier(node.alias).lower()] = full
    pairs: list[JoinPair] = []
    for node in nodes:
        if type(node).__name__ != "ComparisonContext":
            continue
        if node.comparisonOperator().getText() != "=":
            continue
        left, right = _column(node.left), _column(node.right)
        if left is None or right is None:
            continue
        left_table, right_table = tables.get(left[0].lower()), tables.get(right[0].lower())
        if not left_table or not right_table or left_table == right_table:
            continue
        pairs.append(JoinPair(left_table, left[1], right_table, right[1]))
    return pairs
