"""Pipe statements: the engine's grammar, run as Nova pipes.

The pinned StarRocks grammar already parses ``CREATE``/``ALTER``/``DROP PIPE``,
``SHOW PIPES`` and ``DESC PIPE``. They are decoded here into a typed,
credential-free :class:`PipePayload` and handed to the pipes service, which
stores the pipe in PostgreSQL and runs it on the task worker as its owner role.
The engine is never asked to create a pipe: an engine pipe would run without the
caller's sign-in and role and would have to keep storage credentials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.sql_frontend.analysis.effects import PlanEffects
from app.sql_frontend.ast.statements import PipeStatement, Statement
from app.sql_frontend.planning.execution import NovaActionPlan, SourcePayload

ACTION = "pipe"

_OPERATIONS = {
    "CreatePipeStatementContext": "create",
    "DropPipeStatementContext": "drop",
    "AlterPipeStatementContext": "alter",
    "ShowPipeStatementContext": "show",
    "DescPipeStatementContext": "describe",
}


@dataclass(frozen=True, slots=True)
class PipePayload(SourcePayload):
    """A validated pipe statement. ``definition`` holds no credential (refused earlier)."""

    operation: str
    database: str | None = None
    schema: str | None = None
    name: tuple[str, ...] = ()
    if_exists: bool = False
    #: ALTER: suspend, resume, retry_all, retry_file or set.
    action: str | None = None
    properties: tuple[tuple[str, str], ...] = ()
    file_name: str | None = None
    definition: Any = field(default=None, repr=False)
    #: SHOW PIPES: FROM database, LIKE pattern, and SQL sliced from the statement.
    show_database: str | None = None
    show_like: str | None = None
    show_where: str | None = None
    show_order: str | None = None
    show_limit: str | None = None


def build_pipe_statement(parsed) -> PipeStatement:
    return PipeStatement(parsed, _OPERATIONS[type(parsed.statement_context).__name__])


def pipe_effects(statement: Statement) -> PlanEffects:
    operation = getattr(statement, "operation", "")
    if operation == "create":
        # The pipe is stored now and loads files as its owner from now on.
        return PlanEffects(writes_metadata=True, writes_data=True, external_io=True)
    if operation == "drop":
        return PlanEffects(writes_metadata=True, drops_objects=True)
    if operation == "alter":
        return PlanEffects(writes_metadata=True)
    return PlanEffects(reads_data=True)


def _slice(parsed, node) -> str:
    return parsed.normalized_sql[node.start.start: node.stop.stop + 1]


def validate_pipe(statement: Statement, context) -> PipePayload:
    """Decode the statement without metadata or I/O (preflight-safe)."""
    from app.modules.pipes.definition import (
        PipeDefinitionError,
        decode_create,
        name_parts,
        parse_properties,
        property_pairs,
        split_name,
        string_value,
    )

    if not isinstance(statement, PipeStatement):
        raise TypeError("Pipe validation requires a pipe statement")
    parsed = statement.parsed
    root = parsed.statement_context
    database = getattr(context, "database", None)
    schema = getattr(context, "schema", None)
    operation = statement.operation
    if operation == "create":
        definition = decode_create(parsed, database)
        return PipePayload(0, operation, database, schema, definition.name,
                           definition=definition)
    if operation == "show":
        clauses = root.showPredicateClauses()
        show_database = ".".join(name_parts(root.qualifiedName())) if root.FROM() else None
        if root.FROM() and len(name_parts(root.qualifiedName())) != 1:
            raise PipeDefinitionError("SHOW PIPES FROM takes a database name")
        if not (show_database or database):
            raise PipeDefinitionError("Select a database, or use SHOW PIPES FROM <database>")
        return PipePayload(
            0, operation, database, schema,
            show_database=show_database,
            show_like=string_value(_slice(parsed, root.pattern)) if root.LIKE() else None,
            show_where=_slice(parsed, clauses.expression()) if clauses.WHERE() else None,
            show_order=", ".join(_slice(parsed, item) for item in clauses.sortItem()) or None,
            show_limit=_slice(parsed, clauses.limitElement()) if clauses.limitElement() else None,
        )
    name = name_parts(root.qualifiedName())
    split_name(name, database, "pipe")
    if operation == "drop":
        return PipePayload(0, operation, database, schema, name,
                           if_exists=root.EXISTS() is not None)
    if operation == "describe":
        return PipePayload(0, operation, database, schema, name)
    clause = root.alterPipeClause()
    if clause.SUSPEND() is not None:
        return PipePayload(0, operation, database, schema, name, action="suspend")
    if clause.RESUME() is not None:
        return PipePayload(0, operation, database, schema, name, action="resume")
    if clause.RETRY() is not None and clause.ALL() is not None:
        return PipePayload(0, operation, database, schema, name, action="retry_all")
    if clause.RETRY() is not None:
        return PipePayload(0, operation, database, schema, name, action="retry_file",
                           file_name=string_value(_slice(parsed, clause.fileName)))
    pairs = tuple(property_pairs(parsed, clause.propertyList()))
    parse_properties(pairs)  # refuse an unknown or malformed property before running
    return PipePayload(0, operation, database, schema, name, action="set", properties=pairs)


class PipePlanner:
    """Every pipe statement is a Nova action executed by the pipes service."""

    async def plan(self, logical, context) -> NovaActionPlan:
        from dataclasses import replace

        payload = context.validated[logical.source_key]
        return NovaActionPlan(
            ACTION,
            replace(payload, source_key=logical.source_key),
            logical.analysis.effects,
            logical.analysis.requires_confirmation,
        )
