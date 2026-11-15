"""Table-to-table lineage of one statement, read from the central parse tree.

Only statements that write one named relation from a query have lineage:
``INSERT [OVERWRITE] INTO t SELECT ...``, ``CREATE TABLE t AS SELECT ...``,
``CREATE [OR REPLACE] VIEW t AS ...``, ``CREATE MATERIALIZED VIEW t AS ...`` and
``MERGE INTO t USING ...``.
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
from app.sql_frontend.binding.models import TableName
from app.sql_frontend.binding.relations import identifier, table_name
from app.sql_frontend.errors import SemanticError, SQLFrontendError
from app.sql_frontend.parser import ParsedStatement, parse_statement


def read_query_sources(parsed: ParsedStatement, *, database: str) -> tuple[TableName, ...]:
    """Resolve lexical table references without treating scoped CTEs as physical sources.

    These names still need caller-bound catalog identity and engine authorization.
    A view remains a view dependency; the engine governs its underlying reads.
    """
    from app.sql_frontend.read_queries import validate_read_query

    validate_read_query(parsed)
    found: dict[tuple[str, str, str], TableName] = {}
    stack: list[tuple[Any, frozenset[str], int]] = [
        (parsed.statement_context.queryRelation(), frozenset(), 0)
    ]
    visited = 0
    while stack:
        node, ctes, depth = stack.pop()
        visited += 1
        if visited > 100_000 or depth > 32:
            raise SemanticError("Query dependency analysis exceeds its complexity limit")
        kind = type(node).__name__
        if kind == "QueryRelationContext":
            local = set(ctes)
            definitions = []
            clause = node.withClause()
            for cte in clause.commonTableExpression() if clause else ():
                definitions.append((cte.queryRelation(), frozenset(local), depth + 1))
                local.add(identifier(cte.identifier()).casefold())
            stack.append((node.queryNoWith(), frozenset(local), depth + 1))
            stack.extend(reversed(definitions))
            continue
        if kind == "TableAtomContext":
            qualified = node.qualifiedName()
            if qualified is None:
                raise SemanticError("Unsupported dataset source relation")
            parts = qualified.identifier()
            if len(parts) == 1 and identifier(parts[0]).casefold() in ctes:
                continue
            name = table_name(qualified, database)
            if name.catalog != "default_catalog" or not name.database:
                raise SemanticError("Dataset source has no supported physical identity")
            key = (name.catalog.casefold(), name.database.casefold(), name.table.casefold())
            found.setdefault(key, name)
            if len(found) > 64:
                raise SemanticError("Dataset exceeds the source dependency limit")
        stack.extend(
            (child, ctes, depth)
            for child in reversed(node.children or ())
            if hasattr(child, "children")
        )
    return tuple(found.values())


_WRITES = {
    "InsertStatementContext": "insert",
    "CreateTableAsSelectStatementContext": "create_table_as_select",
    "CreateViewStatementContext": "create_view",
    "CreateMaterializedViewStatementContext": "create_materialized_view",
    "NovaMergeStatementContext": "merge",
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
    if kind == "merge":
        target_node, query = node.target, node.novaMergeSource()
    else:
        target_node = node.qualifiedName()
        if isinstance(target_node, list):
            target_node = target_node[0] if target_node else None
        query = node.queryStatement()
    if target_node is None or query is None:
        return None
    target = _ref(target_node, database)
    if target is None:
        return None
    nodes = list(walk_nodes(query))
    if kind == "merge" and type(query).__name__ == "NovaMergeSourceTableContext":
        source = _ref(query.qualifiedName(), database)
        return (
            StatementLineage(kind=kind, target=target, sources=(source,))
            if source is not None and source != target
            else None
        )
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
