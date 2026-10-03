"""Preserve proxy LAST_QUERY_ID across Nova's internal correlation statement."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from app.sql_frontend.antlr_utils import walk_nodes
from app.sql_frontend.parser import parse_statement


@dataclass
class QueryCorrelationSession:
    last_query_id: str | None = None
    instrumented: bool = False


CORRELATION_SESSION: ContextVar[QueryCorrelationSession | None] = ContextVar(
    "query_correlation_session", default=None
)


def preserve_last_query_id(
    sql: str, session: QueryCorrelationSession | None
) -> tuple[str, dict[int, str]]:
    if session is None or not session.instrumented:
        return sql, {}
    parsed = parse_statement(sql)
    spans = []
    labels = {}
    nodes = list(walk_nodes(parsed.statement_context))
    for node in nodes:
        if type(node).__name__ != "SimpleFunctionCallContext":
            continue
        name = node.qualifiedName()
        if (
            sql[name.start.start : name.stop.stop + 1].lower() != "last_query_id"
            or node.expression()
        ):
            continue
        value = (
            "NULL"
            if session.last_query_id is None
            else "'" + session.last_query_id.replace("'", "''") + "'"
        )
        spans.append((node.start.start, node.stop.stop + 1, value))
    if not spans:
        return sql, {}
    # Preserve unaliased projection labels as well as their values.
    specifications = [n for n in nodes if type(n).__name__ == "QuerySpecificationContext"]
    if specifications:
        items = specifications[0].selectItem()
        if not any(type(n).__name__ == "SelectAllContext" for n in items):
            for index, item in enumerate(items):
                if item.identifier() is None and item.string() is None:
                    expression = item.expression()
                    if any(
                        expression.start.start <= start <= expression.stop.stop
                        for start, _, _ in spans
                    ):
                        labels[index] = sql[expression.start.start : expression.stop.stop + 1]
    for start, end, value in sorted(spans, reverse=True):
        sql = sql[:start] + value + sql[end:]
    return sql, labels
