"""Parse semantic expressions with Nova's pinned StarRocks grammar."""

from __future__ import annotations

from typing import Any

from antlr4 import CommonTokenStream, Token

from app.modules.agents.semantic.planning import SemanticPlanError
from app.modules.query.dialect.parser import CaseInsensitiveInputStream, _RecordingErrorListener
from app.sql_dialect.grammar import StarRocksLexer, StarRocksParser


def quote_identifier(value: str) -> str:
    if value.startswith("`") and value.endswith("`"):
        value = value[1:-1]
    if not value or not all(char.isalnum() or char in "_$ " for char in value):
        raise SemanticPlanError("Invalid semantic identifier.")
    if value[0].isdigit():
        raise SemanticPlanError("Invalid semantic identifier.")
    return "`" + value + "`"


def quote_source(value: str) -> str:
    parts = value.split(".")
    if not 1 <= len(parts) <= 3:
        raise SemanticPlanError("Invalid semantic source.")
    return ".".join(quote_identifier(part) for part in parts)


def parse_expression(value: str) -> Any:
    if not value.strip() or len(value) > 16000:
        raise SemanticPlanError("Semantic expression is empty or too large.")
    listener = _RecordingErrorListener()
    lexer = StarRocksLexer(CaseInsensitiveInputStream(value))
    lexer.removeErrorListeners()
    lexer.addErrorListener(listener)
    stream = CommonTokenStream(lexer)
    parser = StarRocksParser(stream)
    parser.removeErrorListeners()
    parser.addErrorListener(listener)
    tree = parser.expression()
    stream.fill()
    if any(
        token.type
        in {
            StarRocksLexer.SIMPLE_COMMENT,
            StarRocksLexer.BRACKETED_COMMENT,
            StarRocksLexer.OPTIMIZER_HINT,
        }
        for token in stream.tokens
    ):
        raise SemanticPlanError("Comments are not allowed in semantic expressions.")
    if listener.errors or stream.LA(1) != Token.EOF:
        raise SemanticPlanError("Invalid or unbounded semantic expression.")
    for node in walk(tree):
        if type(node).__name__ in {
            "SubqueryContext",
            "QueryRelationContext",
            "UserVariableContext",
            "SystemVariableContext",
            "OverContext",
        }:
            raise SemanticPlanError(
                "Subqueries, variables, and windows are not semantic expressions."
            )
    return tree


def walk(node: Any) -> Any:
    yield node
    for child in getattr(node, "children", ()) or ():
        yield from walk(child)


def qualify_expression(value: str, dataset: str, model: Any = None) -> str:
    tree = parse_expression(value)
    replacements: list[tuple[int, int, str]] = []
    for node in walk(tree):
        if not isinstance(node, StarRocksParser.ColumnReferenceContext):
            continue
        reference = node.parentCtx
        while isinstance(reference.parentCtx, StarRocksParser.DereferenceContext):
            reference = reference.parentCtx
        if isinstance(reference, StarRocksParser.DereferenceContext):
            parts = reference.getText().split(".")
            if len(parts) != 2:
                raise SemanticPlanError("Semantic columns require a dataset and field.")
            replacement = ".".join(quote_identifier(part) for part in parts)
            reference_dataset, reference_field = (part.strip("`") for part in parts)
        else:
            reference = node
            replacement = quote_identifier(dataset) + "." + quote_identifier(node.getText())
            reference_dataset, reference_field = dataset, node.getText().strip("`")
        if model is not None:
            source = model.dataset(reference_dataset)
            if source is None:
                raise SemanticPlanError("Expression references an unavailable dataset.")
            field = source.field(reference_field)
            if field is not None and field.expression.strip("`") != reference_field:
                replacement = "(" + qualify_expression(field.expression, reference_dataset) + ")"
        replacements.append((reference.start.start, reference.stop.stop + 1, replacement))
    for start, end, replacement in sorted(set(replacements), reverse=True):
        value = value[:start] + replacement + value[end:]
    return value


def referenced_datasets(value: str, default: str) -> set[str]:
    tree = parse_expression(value)
    datasets: set[str] = set()
    for node in walk(tree):
        if isinstance(node, StarRocksParser.ColumnReferenceContext):
            parent = node.parentCtx.parentCtx
            datasets.add(
                node.getText().strip("`")
                if isinstance(parent, StarRocksParser.DereferenceContext)
                else default
            )
    return datasets
