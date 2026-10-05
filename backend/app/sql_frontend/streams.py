"""Streams syntax uses the central grammar, including names carried in strings."""

from antlr4 import Token

from app.modules.streams.namespace import resolve_stream_name
from app.sql_dialect.grammar import StarRocksParser
from app.sql_frontend.analysis.effects import PlanEffects
from app.sql_frontend.ast.statements import StreamStatement
from app.sql_frontend.errors import SQLSyntaxError, SyntaxDiagnostic
from app.sql_frontend.parser import _Diagnostics, tokenize_sql


def name_parts(node) -> tuple[str, ...]:
    parts = []
    for child in node.children:
        text = child.getText()
        if text == ".":
            continue
        if hasattr(child, "symbol") and child.symbol.type == StarRocksParser.DOT_IDENTIFIER:
            text = text[1:]
        elif text.startswith("`"):
            text = text[1:-1].replace("``", "`")
        parts.append(text)
    return tuple(parts)


def parse_stream_name(value: str, database: str | None = None):
    tokens, errors = tokenize_sql(value)
    listener = _Diagnostics()
    parser = StarRocksParser(tokens)
    parser.removeErrorListeners()
    parser.addErrorListener(listener)
    node = parser.novaStreamName()
    errors += listener.errors
    if tokens.LA(1) != Token.EOF:
        errors.append(SyntaxDiagnostic(1, 0, "Expected one qualified identifier"))
    if errors:
        raise SQLSyntaxError(tuple(errors))
    return resolve_stream_name(name_parts(node), database)


def build_stream_statement(parsed) -> StreamStatement:
    node = parsed.statement_context
    names = node.novaStreamName()
    operation = (
        "create" if node.CREATE() else "drop" if node.DROP() else
        "status" if node.STATUS() else "backlog" if node.BACKLOG() else
        "list" if node.STREAMS() else "describe"
    )
    return StreamStatement(
        parsed, operation,
        name_parts(names[0]) if names else None,
        name_parts(names[1]) if len(names) > 1 else None,
        bool(node.IF()) and operation == "drop",
        bool(node.IF()) and operation == "create",
    )


def stream_effects(statement: StreamStatement) -> PlanEffects:
    return PlanEffects(
        reads_data=statement.operation not in {"create", "drop"},
        writes_metadata=statement.operation in {"create", "drop"},
        drops_objects=statement.operation == "drop",
    )


def validate_stream(statement: StreamStatement, context):
    from app.sql_frontend.planning.execution import StreamPayload

    name = resolve_stream_name(statement.name, context.database) if statement.name else None
    source = (
        resolve_stream_name(statement.source, context.database) if statement.source else None
    )
    database = name.database if name else context.database
    if not database:
        from app.sql_frontend.errors import SemanticError

        raise SemanticError("An active database is required for SHOW STREAMS")
    return StreamPayload(
        0, statement.operation, database, name, source,
        statement.if_exists, statement.if_not_exists,
    )


def stream_functions(parsed, database):
    from app.sql_frontend.antlr_utils import walk_nodes
    from app.sql_frontend.errors import SemanticError
    from app.sql_frontend.planning.execution import StreamFunctionBinding

    bindings = []
    for node in walk_nodes(parsed.statement_context):
        if type(node).__name__ != "SimpleFunctionCallContext":
            continue
        parts = name_parts(node.qualifiedName())
        if parts[-1].upper() != "NOVA_STREAM_HAS_DATA":
            continue
        expressions = node.expression()
        if len(parts) != 1 or len(expressions) != 1 or node.over():
            raise SemanticError("NOVA_STREAM_HAS_DATA requires one literal Stream name")
        tokens = [
            token for token in parsed.visible_tokens
            if expressions[0].start.start <= token.start <= expressions[0].stop.stop
        ]
        if len(tokens) != 1 or tokens[0].text[0] not in {"'", '"'}:
            raise SemanticError("NOVA_STREAM_HAS_DATA requires one literal Stream name")
        literal = tokens[0].text
        if "\\" in literal:
            raise SemanticError("Backslash escapes in Stream name literals are unsupported")
        value = literal[1:-1].replace(literal[0] * 2, literal[0])
        bindings.append(StreamFunctionBinding(
            parse_stream_name(value, database), node.start.start, node.stop.stop + 1,
        ))
    return tuple(bindings)
