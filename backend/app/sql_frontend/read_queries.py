"""Composable read queries used by governed dataset and parameter execution."""

from __future__ import annotations

from app.sql_frontend.antlr_utils import walk_nodes
from app.sql_frontend.binding.models import BoundColumn
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parameters import ParameterSpec, parameter_slots, parameter_sql_type
from app.sql_frontend.parser import ParsedStatement, parse_statement

REPLAY_SAFE_FUNCTIONS = frozenset(
    {
        "abs",
        "ceil",
        "floor",
        "round",
        "coalesce",
        "ifnull",
        "nullif",
        "date_trunc",
        "date_format",
        "year",
        "month",
        "day",
        "hour",
        "lower",
        "upper",
        "length",
        "substring",
        "concat",
        "cast",
        "sum",
        "count",
        "avg",
        "min",
        "max",
    }
)
_READ_FUNCTIONS = REPLAY_SAFE_FUNCTIONS | {
    "date_add",
    "date_sub",
    "datediff",
    "convert_tz",
    "last_day",
    "dayofweek",
    "dayofyear",
    "week",
    "weekofyear",
    "monthname",
    "str_to_date",
    "trim",
    "ltrim",
    "rtrim",
    "concat_ws",
    "substr",
    "char_length",
    "replace",
    "split_part",
    "power",
    "sqrt",
    "ln",
    "log",
    "log10",
    "exp",
    "sign",
    "truncate",
    "greatest",
    "least",
}
_FORBIDDEN = {
    "ExplainDescContext",
    "OptimizerTraceContext",
    "OutfileContext",
    "StageReferenceContext",
    "FileTableFunctionContext",
    "TableFunctionContext",
    "NormalizedTableFunctionContext",
    "UserVariableContext",
    "SystemVariableContext",
    "InformationFunctionContext",
    "QueryPeriodContext",
    "SampleClauseContext",
    "ParameterContext",
    "LimitConstExprContext",
}


def validate_read_query(parsed: ParsedStatement) -> None:
    if len(parsed.normalized_sql.encode("utf-8")) > 200_000:
        raise SemanticError("Read query exceeds the byte limit")
    if type(parsed.statement_context).__name__ != "QueryStatementContext":
        raise SemanticError("Expected one composable SELECT or WITH SELECT query")
    if any(token.text and token.text.startswith("/*+") for token in parsed.tokens.tokens):
        raise SemanticError("Query hints are not supported in dataset queries")
    for node in walk_nodes(parsed.statement_context):
        kind = type(node).__name__
        if kind == "LimitConstExprContext":
            if node.INTEGER_VALUE() is None:
                raise SemanticError("Dataset limits must be fixed integers")
        elif kind in _FORBIDDEN:
            raise SemanticError("Query contains an unsupported read construct")
        if kind == "WithClauseContext" and node.RECURSIVE() is not None:
            raise SemanticError("Recursive dataset queries are not supported")
        if kind == "SimpleFunctionCallContext":
            name = node.qualifiedName()
            source = parsed.normalized_sql[name.start.start : name.stop.stop + 1]
            if source.lower() not in _READ_FUNCTIONS:
                raise SemanticError("Query function is not an approved read-only built-in")
        if kind == "SpecialFunctionExpressionContext" and node.PASSWORD() is not None:
            raise SemanticError("Credential functions are not supported in dataset queries")


def validate_parameter_query(statement, context):
    from app.sql_frontend.parameters import parameter_slots

    validate_read_query(statement.parsed)
    return parameter_slots(statement.parsed, context.parameter_specs)


def validate_query_schema(statement, context):
    parsed = statement.parsed
    query = parsed.statement_context.queryRelation()
    if context.parameter_specs or any(
        type(node).__name__ == "NamedParameterContext" for node in walk_nodes(query)
    ):
        raise SemanticError("Schema inspection requires explicit typed SQL expressions")
    inner = parse_statement(parsed.normalized_sql[query.start.start : query.stop.stop + 1])
    validate_read_query(inner)
    return inner


def schema_query(parsed: ParsedStatement) -> str:
    """Wrap a validated query through the engine's metadata statement."""
    validate_read_query(parsed)
    if any(
        type(node).__name__ == "NamedParameterContext"
        for node in walk_nodes(parsed.statement_context)
    ):
        raise SemanticError("Schema inspection requires explicit typed SQL expressions")
    query = parsed.statement_context.queryRelation()
    return "EXPLAIN SCHEMA " + parsed.normalized_sql[query.start.start : query.stop.stop + 1]


def parameter_schema_query(parsed: ParsedStatement, specs: tuple[ParameterSpec, ...]) -> str:
    """Describe declared SQL types without accepting or sampling any input values.

    Typed NULLs retain conservative nullability for parameters. The FE analyzer
    remains authoritative for expression types and the other output fields.
    """
    validate_read_query(parsed)
    slots = parameter_slots(parsed, specs)
    declared = {spec.name: spec for spec in specs}
    parts: list[str] = []
    end = 0
    for slot in slots:
        parts.append(parsed.normalized_sql[end:slot.start])
        parts.append(f"CAST(NULL AS {parameter_sql_type(declared[slot.name])})")
        end = slot.end
    parts.append(parsed.normalized_sql[end:])
    return schema_query(parse_statement("".join(parts)))


def preview_query(
    parsed: ParsedStatement, columns: tuple[BoundColumn, ...], *, limit: int, offset: int,
    parameter_specs: tuple[ParameterSpec, ...] = (),
) -> str:
    """Bound a composable read without changing its inner grouping or LIMIT."""
    if (
        type(limit) is not int
        or not 1 <= limit <= 201
        or type(offset) is not int
        or not 0 <= offset <= 10_000
    ):
        raise SemanticError("Invalid preview bounds")
    validate_read_query(parsed)
    if parameter_specs:
        parameter_slots(parsed, parameter_specs)
    elif any(
        type(node).__name__ == "NamedParameterContext"
        for node in walk_nodes(parsed.statement_context)
    ):
        raise SemanticError("Preview parameters require a typed execution plan")
    query = parsed.statement_context.queryRelation()
    inner = parsed.normalized_sql[query.start.start : query.stop.stop + 1]
    from app.sql_frontend.binding.starrocks import column_projection

    # Fetch one character past the contract bound so the sink can reject a
    # long cell explicitly, while bounding each row before it crosses the wire.
    projection = column_projection(columns, text_limit=4097)
    order = column_projection(columns)
    return (
        f"SELECT {projection} FROM ({inner}\n) AS `nova_preview` "
        f"ORDER BY {order} LIMIT {limit} OFFSET {offset}"
    )
