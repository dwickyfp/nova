"""Table-to-table lineage of one statement, read from the central parse tree.

Only statements that write one named relation from a query have lineage:
``INSERT [OVERWRITE] INTO t SELECT ...``, ``CREATE TABLE t AS SELECT ...``,
``CREATE [OR REPLACE] VIEW t AS ...`` and ``CREATE MATERIALIZED VIEW t AS ...``.
The sources are the named relations the query reads, minus common table
expressions the statement defines. ``EXPLAIN`` forms, ``INSERT ... VALUES`` and
writes to ``FILES()`` have none.

This is analysis only: it never binds names against the engine, executes, or
reads metadata. A statement that does not parse has no lineage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.sql_frontend.antlr_utils import walk_nodes
from app.sql_frontend.binding.relations import identifier, table_name
from app.sql_frontend.errors import SQLFrontendError
from app.sql_frontend.parser import parse_statement

_WRITES = {
    "InsertStatementContext": "insert",
    "CreateTableAsSelectStatementContext": "create_table_as_select",
    "CreateViewStatementContext": "create_view",
    "CreateMaterializedViewStatementContext": "create_materialized_view",
}


@dataclass(frozen=True, slots=True)
class RelationRef:
    catalog: str
    database: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.catalog}.{self.database}.{self.name}"


@dataclass(frozen=True, slots=True)
class StatementLineage:
    kind: str
    target: RelationRef
    sources: tuple[RelationRef, ...]


def _ref(node: Any, database: str | None) -> RelationRef | None:
    try:
        name = table_name(node, database)
    except SQLFrontendError:
        return None
    if not name.database:
        return None
    return RelationRef(name.catalog, name.database, name.table)


def statement_lineage(sql: str, *, database: str | None = None) -> StatementLineage | None:
    """Lineage of one statement, or ``None`` when it writes nothing from a query.

    ``database`` is the session database used to qualify one-part names; a
    one-part name without it is dropped rather than guessed.
    """
    try:
        parsed = parse_statement(sql)
    except SQLFrontendError:
        return None
    node = parsed.statement_context
    kind = _WRITES.get(type(node).__name__)
    if kind is None:
        return None
    explain = getattr(node, "explainDesc", None)
    if explain is not None and explain() is not None:
        return None
    target_node = node.qualifiedName()
    if isinstance(target_node, list):
        target_node = target_node[0] if target_node else None
    if target_node is None or node.queryStatement() is None:
        return None
    target = _ref(target_node, database)
    if target is None:
        return None
    nodes = list(walk_nodes(node.queryStatement()))
    ctes = {
        identifier(child.identifier()).casefold()
        for child in nodes
        if type(child).__name__ == "CommonTableExpressionContext"
    }
    sources: dict[RelationRef, None] = {}
    for child in nodes:
        if type(child).__name__ != "TableAtomContext":
            continue
        qualified = child.qualifiedName()
        if qualified is None:
            continue
        parts = qualified.identifier()
        if len(parts) == 1 and identifier(parts[0]).casefold() in ctes:
            continue
        ref = _ref(qualified, database)
        if ref is not None and ref != target:
            sources[ref] = None
    if not sources:
        return None
    return StatementLineage(kind=kind, target=target, sources=tuple(sources))
